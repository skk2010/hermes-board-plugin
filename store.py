"""hermes-board — storage and op application for boards the agent draws.

A board is a JSON document (the same shape `boardkit` renders). Boards live as one file each
under `$HERMES_HOME/board/boards/<id>.json`, so a session, the desktop pane and a cron job all
see the same object with no database in the way. Every mutation goes through `apply_ops`, which
validates the op name and reports what it did — an unknown op fails loudly instead of silently
doing nothing.

`boardkit` (the renderer and the layout/bus geometry) is loaded from the `chat-charts` skill,
because a board is only useful if it can be drawn: duplicate the renderer and the two would
drift. Set `HERMES_BOARDKIT` to point the plugin at a different copy.
"""

from __future__ import annotations

import glob
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import tempfile

BOARD_DIR = os.path.expanduser(os.environ.get("HERMES_BOARD_DIR", "~/.hermes/board/boards"))
RENDER_DIR = os.path.expanduser(os.environ.get("HERMES_BOARD_RENDER_DIR", "~/.hermes/board/render"))
WATCH_FILE = os.path.expanduser(os.environ.get("HERMES_BOARD_WATCH", "~/.hermes/board/watch.json"))
EVENT_CAP = 300          # keep the log bounded; the oldest events fall off the front
DEFAULT_ID = "default"

# v1.2 primitives (R1/R5/R6). The vocabulary lives here, not in boardkit, because an op has to be
# able to reject a bad value even when the installed renderer is older than the spec — and while a
# lane is in flight the two copies of the library are allowed to be out of step.
HEADS = ("none", "arrow", "open", "triangle", "circle", "diamond", "bar")
# v1.3 edges v2 (W3.1/W4). Same reason: the op vocabulary is the plugin's, so a bad side or head
# size fails the op list even against a renderer that does not know the fields yet.
SIDES = ("auto", "left", "right", "top", "bottom")
HEAD_SIZES = ("small", "medium", "large")
# v1.6 lines (Z3.2/Z7.1). A line reuses the EDGE vocabulary exactly — the same stroke styles,
# the same `HEADS`, the same `SIDES` for its anchors — so no second enum list is invented here;
# only the style tuple gains a name, for the same reason as every tuple above: an op has to be
# able to refuse a bad value against a renderer that predates `lines`.
EDGE_STYLES = ("solid", "dashed", "dotted")
LINE_MIN_POINTS = 2      # Z3.1: fewer than two points is not a polyline
GRID_STYLES = ("lines", "dots")
SPLITS = ("rows", "columns")
DEFAULT_GRID = 10        # R5.1: the spacing a board with no `grid` is drawn with
SPLIT_HEAD_H = 28        # R4/R8: the header row a split box reserves when it has `text`
SPLIT_ROW_H = 24         # R8: one compartment of a split box
SPLIT_PAD = 16

# v1.4 content (X3/X4/X8). Same rule as HEADS/SIDES above: the op vocabulary is the PLUGIN's, so a
# bad value fails the op list even against a renderer that predates the fields. `KINDS` mirrors
# `boardkit.KINDS` and adds the table (X3.1: the pane's picker iterates the renderer's tuple, so a
# shape joins that one too — this tuple is what an op is checked against).
TEXT_SIZES = ("small", "default", "large")
# v1.5 table v2 (Y4/Y6). The same rule once more: the op vocabulary is the PLUGIN's, so a bad tone
# or a cell alignment fails the op list even against a renderer that predates the fields. The
# READS stay forgiving (`col_widths_of`, `_norm_cell`) — a document can come from outside the
# plugin, and a reader that raises on a stray value breaks a tool over a valid board.
TONES = ("default", "accent", "ok", "warn", "danger", "muted")     # Y4.3: matches boardkit.TONES
CELL_ALIGNS = ("left", "center", "right")                          # Y4.1: the cell tri-state
KINDS = ("box", "pill", "diamond", "note", "caption", "cloud", "cylinder", "document",
         "hexagon", "parallelogram", "server", "computer", "actor", "area", "table")
TABLE_ROWS = 2           # X3.1: rows/cols default
TABLE_COLS = 2
TABLE_COL_W = 90         # X3.3: one column of an auto-sized table
TABLE_ROW_H = 24         # X3.3: one row's band (equals SPLIT_ROW_H — pinned by the spec, not shared)
TABLE_HEAD_H = 28        # X3.3: the band a table reserves when `header` is true (equals SPLIT_HEAD_H)

OPS = (
    "add_node", "add_table", "update_node", "move_node", "delete_node",
    "add_edge", "update_edge", "delete_edge",
    "add_line", "update_line", "delete_line",
    "add_group", "update_group", "delete_group",
    "add_caption", "add_note", "update_note", "delete_note",
    "set_title", "set_grid", "layout", "bus", "graph",
)


# --------------------------------------------------------------------------- boardkit


_BK = None


VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")


def _boardkit_candidates() -> list[str]:
    """Where to look for the renderer, most specific first.

    `HERMES_BOARDKIT` wins, then the copy this plugin ships (`vendor/`), then any installed
    'chat-charts' skill. Shipping the library keeps the plugin installable on a host that never
    had the skill; `scripts/sync_boardkit.py` and `tests/test_vendor.py` keep the two copies from
    drifting, so a local skill checkout is not silently ignored.
    """
    home = os.environ.get("HERMES_HOME") or "~/.hermes"
    roots = [home, "~/.hermes"]
    return [os.environ.get("HERMES_BOARDKIT") or "", VENDOR_DIR] + [
        candidate
        for root in dict.fromkeys(roots)
        for candidate in [os.path.expanduser(os.path.join(root, "skills/data-science/chat-charts/scripts")),
                          *glob.glob(os.path.expanduser(
                              os.path.join(root, "profiles/*/skills/data-science/chat-charts/scripts"))),
                          *glob.glob(os.path.expanduser(os.path.join(root, "skills/*/*/scripts"))),
                          *glob.glob(os.path.expanduser(os.path.join(root, "profiles/*/skills/*/*/scripts")))]
    ]


def boardkit():
    """Import the board renderer+editor (cached). Raises a named error when missing."""
    global _BK
    if _BK is not None:
        return _BK
    for directory in _boardkit_candidates():
        path = os.path.join(directory, "boardkit.py") if directory else ""
        if path and os.path.isfile(path):
            spec = importlib.util.spec_from_file_location("hermes_board_boardkit", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _BK = module
            return module
    raise RuntimeError(
        "boardkit.py not found — this plugin ships one in vendor/, so the install is incomplete; "
        "run scripts/sync_boardkit.py or point HERMES_BOARDKIT at a chat-charts scripts directory")


# --------------------------------------------------------------------------- files


def _path(board_id: str) -> str:
    safe = re.sub(r"[^a-z0-9_-]+", "-", str(board_id).strip().lower()) or DEFAULT_ID
    return os.path.join(BOARD_DIR, f"{safe}.json")


def new_board(board_id: str = DEFAULT_ID, title: str | None = None) -> dict:
    bk = boardkit()
    return bk.board(title=title or str(board_id))


def _runtime():
    """The in-process handle shared with the plugin's tools (see runtime.py).

    Resolved by file path so the copy loaded here and the one the plugin package imported are
    the same object whenever both live in one process.
    """
    global _RT
    if _RT is None:
        path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "runtime.py"))
        found = None
        for module in list(sys.modules.values()):
            candidate = getattr(module, "__file__", None)
            if candidate and os.path.abspath(candidate) == path and hasattr(module, "STATE"):
                found = module
                break
        if found is None:
            spec = importlib.util.spec_from_file_location("hermes_board_runtime", path)
            found = importlib.util.module_from_spec(spec)
            sys.modules["hermes_board_runtime"] = found
            spec.loader.exec_module(found)
        _RT = found
    return _RT


_RT = None


def board_exists(board_id: str = DEFAULT_ID) -> bool:
    return os.path.isfile(_path(board_id))


def load_board(board_id: str = DEFAULT_ID) -> dict:
    path = _path(board_id)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no board '{board_id}' ({path}) — call board_new first")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def save_board(board_id: str, board: dict) -> str:
    """Atomic write; returns the new version hash."""
    os.makedirs(BOARD_DIR, exist_ok=True)
    path = _path(board_id)
    handle, tmp = tempfile.mkstemp(dir=BOARD_DIR, suffix=".tmp")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(board, stream, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return version_of(board)


def version_of(board: dict) -> str:
    blob = json.dumps(board, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:12]


def list_boards() -> list[dict]:
    if not os.path.isdir(BOARD_DIR):
        return []
    out = []
    for name in sorted(os.listdir(BOARD_DIR)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(BOARD_DIR, name)
        try:
            with open(path, encoding="utf-8") as handle:
                board = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            out.append({"id": name[:-5], "error": str(exc)})
            continue
        out.append({"id": name[:-5], "title": board.get("title", ""),
                    "nodes": len(board.get("nodes", [])), "edges": len(board.get("edges", [])),
                    "notes": len(board.get("notes", [])),
                    "updated": int(os.path.getmtime(path)), "version": version_of(board)})
    return out


def delete_board(board_id: str) -> bool:
    path = _path(board_id)
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


# --------------------------------------------------------------------------- ops


def _next_id(board: dict, prefix: str) -> str:
    pattern = re.compile(rf"^{prefix}(\d+)$")
    used = {int(m.group(1)) for item in board.get("nodes", []) + board.get("notes", [])
            if (m := pattern.match(str(item.get("id", ""))))}
    used |= {int(m.group(1)) for item in board.get("groups", [])
             if (m := pattern.match(str(item.get("id", "")))) and prefix == "g"}
    return f"{prefix}{max(used, default=0) + 1}"


def _auto_position(board: dict, w: float = 190, h: float = 72) -> tuple[float, float]:
    items = list(board.get("nodes", [])) + list(board.get("notes", []))
    if not items:
        return 40.0, 40.0
    # A board file may come from outside the plugin (hand-written JSON, an import), so a missing
    # w/h must not break placement - fall back to the standard box size.
    bottom = max(float(i.get("y", 0)) + float(i.get("h", 72)) for i in items)
    items_ = items  # noqa: F841  (kept for readability of the fallback above)
    return 40.0, round(bottom + 40, 1)


def _find(items, **match):
    for item in items:
        if all(item.get(key) == value for key, value in match.items()):
            return item
    return None


def _require(params: dict, *keys):
    missing = [k for k in keys if params.get(k) in (None, "")]
    if missing:
        raise ValueError(f"{params.get('op')}: missing {', '.join(missing)}")


# --------------------------------------------------------------- primitives (v1.2: R1/R4/R6/R8)


def split_height(text, parts) -> float:
    """R8: the `h` a split box gets when the caller did not give one — header row + one per part."""
    return (SPLIT_HEAD_H if text else 0) + SPLIT_ROW_H * len(parts) + SPLIT_PAD


def _norm_align(value, allow_right: bool = False) -> str:
    """R1/R3: `left` is the only value that moves a node's text; everything else is the default.

    Y4.5a: a TABLE (and a note inside one) uses the full tri-state — `right` is legal there, because
    Y4.5 makes a table's own align the fallback for every cell that has none (v1.5 verifier, MAJOR:
    `right` was reduced to `center` on the way in, so the writer silently lost it)."""
    word = str(value or "").strip().lower()
    if word == "left":
        return "left"
    if allow_right and word == "right":
        return "right"
    return "center"


def _norm_split(value) -> str:
    """R1/R4: `columns`, or the default `rows` (an unknown value reads as the default)."""
    return "columns" if str(value or "").strip().lower() == "columns" else "rows"


def _norm_head(op: str, field: str, value, *, strict: bool = True):
    """R6.1/R8: one end head. None means 'not given' (the renderer's default then applies)."""
    if value is None or str(value).strip() == "":
        return None
    head = str(value).strip().lower()
    if head not in HEADS:
        if not strict:
            return None
        raise ValueError(f"{op}: {field} '{value}' is not a head; valid heads: {', '.join(HEADS)}")
    return head


def _norm_side(op: str, field: str, value, *, strict: bool = True):
    """W4/W3.1: one attach side. None means 'not given' (the renderer's `auto` then applies)."""
    if value is None or str(value).strip() == "":
        return None
    side = str(value).strip().lower()
    if side not in SIDES:
        if not strict:
            return None
        raise ValueError(f"{op}: {field} '{value}' is not a side; valid sides: {', '.join(SIDES)}")
    return side


def _norm_head_size(op: str, field: str, value, *, strict: bool = True):
    """W4/W3.3: the head size. None means 'not given' (the renderer's `medium` then applies)."""
    if value is None or str(value).strip() == "":
        return None
    size = str(value).strip().lower()
    if size not in HEAD_SIZES:
        if not strict:
            return None
        raise ValueError(f"{op}: {field} '{value}' is not a head size; valid sizes: "
                         f"{', '.join(HEAD_SIZES)}")
    return size


def _norm_at(op: str, field: str, value):
    """v1.7 (A3, the ops layer): one anchor position — a REAL number in [0, 1], None to reset.

    The readers CLAMP (`-3` -> 0, `1e400` -> 1) or IGNORE (a string, a boolean, a NaN) the same
    inputs instead, per the three-layer policy; an op RAISES, naming the field, for anything
    that is not a finite number in range (`True` is a boolean, not a number; a NaN is not a
    position a control can offer). `None` is how a position resets to the default 0.5 — an
    absent `at` IS 0.5, so the key is dropped, never stored as null.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not 0 <= value <= 1:
        raise ValueError(f"{op}: {field} must be a number between 0 and 1 "
                         f"(a fraction along the side), got {value!r}")
    return value


def _norm_bool(op: str, field: str, value) -> bool:
    """A flag from an op: a REAL boolean, nothing else (Y6/Y4.1a).

    `"yes"`, `"true"` and `1` are refused rather than guessed at — a coerced flag is a silent typo,
    and the pane always writes a real boolean (a checkbox), so nothing legitimate is turned away.
    The READS (`header_of`, `bold_of`) stay forgiving, because they also read documents this plugin
    never wrote: three layers, three policies.
    """
    if isinstance(value, bool):
        return value
    raise ValueError(f"{op}: {field} must be true or false, got {value!r}")


def _norm_count(op: str, field: str, value, default: int) -> int:
    """X8/X3.1: `rows`/`cols` are integers of at least 1; an absent one is the table's default.

    The limit is named in the message, like every other rejection here. A number written as a
    string or as `3.0` is read (an op list is often built by hand), but `0`, `-1`, `2.5` and a word
    are not a count.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return int(default)
    number = None
    if isinstance(value, bool):
        number = None
    elif isinstance(value, int):
        number = value
    elif isinstance(value, float) and float(value).is_integer():
        number = int(value)
    elif isinstance(value, str):
        try:
            number = int(value.strip())
        except ValueError:
            number = None
    if number is None or number < 1:
        raise ValueError(f"{op}: {field} must be an integer of at least 1, got {value!r}")
    return number


def _norm_text_size(op: str, field: str, value):
    """X4.1/X8: the node's own text size. None means 'not given' (the default then applies)."""
    if value is None or str(value).strip() == "":
        return None
    size = str(value).strip().lower()
    if size not in TEXT_SIZES:
        raise ValueError(f"{op}: {field} '{value}' is not a text size; valid sizes: "
                         f"{', '.join(TEXT_SIZES)}")
    return size


def _norm_kind(op: str, value) -> str:
    """X8/X3.1: the node kind. The vocabulary is this plugin's (`KINDS`), and a bad one names it."""
    if value is None or str(value).strip() == "":
        return "box"
    kind = str(value).strip().lower()
    if kind not in KINDS:
        raise ValueError(f"{op}: kind '{value}' is not a node kind; valid kinds: {', '.join(KINDS)}")
    return kind


def _positive(value) -> bool:
    """X3.3: a size is real only when it is a positive number — absent or non-positive means auto."""
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def table_size(rows: int, cols: int, header: bool) -> tuple[float, float]:
    """X3.3/X8 (Y4.6): the size an auto-sized table is drawn at — the same numbers the renderer
    resolves: `90` per column, and the height is the FIRST row's band (`28` with a header, `24`
    without) plus `24` for every row after it. A 3x2 header table is 180x76, a one-row header table
    180x28, a 3-row headerless one 180x72.

    Defined here for the same reason SPLIT_HEAD_H/SPLIT_ROW_H are: an op has to be able to write a
    resolved size against a renderer that does not know the table yet.
    """
    # Y4.6: `rows` counts every row, and row 0 is the header band when `header` is true
    first = TABLE_HEAD_H if header else TABLE_ROW_H
    return float(TABLE_COL_W * cols), float(first + TABLE_ROW_H * (int(rows) - 1))


def _cell_text(cell) -> str:
    """Y4.1: the text of one cell, whichever of the two shapes it was written in.

    A string cell is exactly what it is today; an object cell carries its text under `text`.
    """
    if isinstance(cell, dict):
        return "" if cell.get("text") is None else str(cell.get("text"))
    return "" if cell is None else str(cell)


def cell_tone_of(cell):
    """Y4.1/Y4.3: a cell's own tone, or None — a value outside `TONES` is not a tone at all."""
    if isinstance(cell, dict):
        tone = cell.get("tone")
        if isinstance(tone, str) and tone.strip().lower() in TONES:
            return tone.strip().lower()
    return None


def cell_align_of(cell):
    """Y4.1: a cell's own alignment, or None — outside the tri-state it is not an alignment."""
    if isinstance(cell, dict):
        align = cell.get("align")
        if isinstance(align, str) and align.strip().lower() in CELL_ALIGNS:
            return align.strip().lower()
    return None


def _norm_cell(cell):
    """Y4.1: one cell as it is STORED — a plain string, or `{text[, tone][, align]}`.

    An object keeps only the keys it needs: `text` always, `tone`/`align` when they name a real
    tone/alignment, nothing else (surplus keys dropped, `text` stringified). An object that ends
    up with neither a tone nor an alignment is written back as the plain string it stands for, so
    a v1.4-shaped document stays v1.4-shaped (Y5's cell editor follows the same rule, which is what
    makes an agent and the pane write the SAME document).
    """
    if isinstance(cell, dict):
        item = {"text": _cell_text(cell)}
        tone, align = cell_tone_of(cell), cell_align_of(cell)
        if tone:
            item["tone"] = tone
        if align:
            item["align"] = align
        return item if len(item) > 1 else item["text"]
    return "" if cell is None else str(cell)


def _norm_cells(value, rows: int, cols: int) -> list:
    """X3.1/X8/Y4.1: exactly `rows * cols` cells, row-major — missing ones become `""`, extras
    are dropped. This is the rule both halves write by, so the pane and an op cannot disagree.

    Forgiving on purpose (like `parts_of`): a document can come from outside the plugin, and empty
    cells are LEGAL here (X3.2) — unlike split-box parts, they are never filtered out.
    """
    if value is None:
        value = []
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, (list, tuple)):
        value = []
    flat = [_norm_cell(cell) for cell in value]
    want = int(rows) * int(cols)
    return (flat + [""] * want)[:want]


def _norm_col_widths(value, cols: int) -> list:
    """Y4.2: exactly `cols` positive numbers. Missing/short -> `1`, extras dropped, and anything
    non-positive, non-numeric or **non-finite** is `1` — a bad width never widens a column or breaks
    a read. The finiteness test is not pedantry: `float('inf')` passes `> 0`, and an infinite share
    makes the column sum infinite, so every column resolves to NaN and the renderer dies with
    "cannot convert float NaN to integer" on a document that was accepted (v1.5 verifier, CRITICAL)."""
    if isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        raw = []
    out = []
    for index in range(int(cols)):
        number = 1.0
        if index < len(raw):
            try:
                number = float(raw[index])
            except (TypeError, ValueError):
                number = 1.0
            if not (number > 0 and math.isfinite(number)):
                number = 1.0
        out.append(int(number) if float(number).is_integer() else number)
    return out


def _norm_tones(value, count: int) -> list:
    """Y4.3: exactly `count` tone names — `None` where there is no tone, an unknown name dropped."""
    if isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        raw = []
    out = []
    for index in range(int(count)):
        tone = raw[index] if index < len(raw) else None
        out.append(tone.strip().lower() if isinstance(tone, str) and tone.strip().lower() in TONES
                   else None)
    return out


def col_widths_of(node: dict) -> list:
    """Y4.2: the column widths a table draws with — the effective list, `1` per column when absent."""
    return _norm_col_widths(node.get("col_widths"), cols_of(node))


def row_tones_of(node: dict) -> list:
    """Y4.3: the row tones a table draws with — the effective list, `None` when absent."""
    return _norm_tones(node.get("row_tones"), rows_of(node))


def col_tones_of(node: dict) -> list:
    """Y4.3: the column tones a table draws with — the effective list, `None` when absent."""
    return _norm_tones(node.get("col_tones"), cols_of(node))


def _number(value):
    """A width/step printed the way a person wrote it: `2`, not `2.0`."""
    number = float(value)
    return int(number) if number.is_integer() else number


def table_line(node: dict) -> str:
    """Y6: one line for the board description the tools hand the model — `table n1 3x2
    header=yes filled=4/6`. The compact face of a table: its shape, whether the first row is a
    header band, and how many of its cells carry text."""
    rows, cols = rows_of(node), cols_of(node)
    filled = sum(1 for cell in cells_of(node) if _cell_text(cell).strip())
    header = "yes" if header_of(node) else "no"
    return f"table {node.get('id')} {rows}x{cols} header={header} filled={filled}/{rows * cols}"


def table_sentence(node: dict) -> str:
    """Y6: the table's current shape in one sentence, for the edit log — `table n1: 3x2 header,
    4/6 cells filled, widths 2:1, 1 coloured row`. The parts that are at their default (no header
    band, `1`-wide columns, no row tones) are left out rather than spelled out."""
    rows, cols = rows_of(node), cols_of(node)
    filled = sum(1 for cell in cells_of(node) if _cell_text(cell).strip())
    parts = [f"{rows}x{cols}" + (" header" if header_of(node) else "")]
    parts.append(f"{filled}/{rows * cols} cells filled")
    widths = col_widths_of(node)
    if any(abs(width - 1) > 1e-9 for width in widths):
        parts.append("widths " + ":".join(str(_number(width)) for width in widths))
    toned = sum(1 for tone in row_tones_of(node) if tone)
    if toned:
        parts.append(f"{toned} coloured row" + ("s" if toned != 1 else ""))
    return f"table {node.get('id')}: " + ", ".join(parts)


def _validate_table_fields(op: str, p: dict) -> None:
    """Y6: the TYPES an op may not get wrong. Y4's normalisation is forgiving by design (it also
    reads documents this plugin never wrote); an op is checked, so a typo fails loudly here instead
    of silently doing nothing."""
    if p.get("cells") is not None and not isinstance(p["cells"], (list, tuple)):
        raise ValueError(f"{op}: cells must be a list of cells, got {p['cells']!r}")
    if isinstance(p.get("cells"), (list, tuple)):
        for cell in p["cells"]:
            if not isinstance(cell, dict):
                continue
            tone = cell.get("tone")
            if tone is not None and not (isinstance(tone, str) and tone.strip().lower() in TONES):
                raise ValueError(f"{op}: cell tone {tone!r} is not a tone; valid tones: "
                                 f"{', '.join(TONES)}")
            align = cell.get("align")
            if align is not None and not (isinstance(align, str)
                                          and align.strip().lower() in CELL_ALIGNS):
                raise ValueError(f"{op}: cell align {align!r} is not an alignment; valid "
                                 f"alignments: {', '.join(CELL_ALIGNS)}")
    if p.get("col_widths") is not None and not isinstance(p["col_widths"], (list, tuple)):
        raise ValueError(f"{op}: col_widths must be a list of numbers, got {p['col_widths']!r}")
    if isinstance(p.get("col_widths"), (list, tuple)):
        for index, share in enumerate(p["col_widths"]):
            bad = (isinstance(share, bool) or not isinstance(share, (int, float))
                   or not (float(share) > 0 and math.isfinite(float(share))))
            if bad:
                raise ValueError(f"{op}: col_widths[{index}] must be a positive finite number, "
                                 f"got {share!r}")
    if p.get("align") is not None and not (isinstance(p["align"], str)
                                           and p["align"].strip().lower() in CELL_ALIGNS):
        raise ValueError(f"{op}: align {p['align']!r} is not an alignment; valid alignments: "
                         f"{', '.join(CELL_ALIGNS)}")
    for field in ("row_tones", "col_tones"):
        value = p.get(field)
        if value is None:
            continue
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"{op}: {field} must be a list of tone names, got {value!r}")
        for tone in value:
            if tone is not None and not (isinstance(tone, str) and tone.strip().lower() in TONES):
                raise ValueError(f"{op}: {field} has {tone!r}, which is not a tone; valid tones: "
                                 f"{', '.join(TONES)}")


def _normalised_field(field: str, value, rows: int, cols: int) -> list:
    """The normalised list for one optional table field (Y4.2/Y4.3)."""
    return _norm_col_widths(value, cols) if field == "col_widths" else _norm_tones(
        value, rows if field == "row_tones" else cols)



def _norm_table(op: str, p: dict) -> tuple[int, int, list, bool]:
    """X3.1/X8: the table fields an op asked for, validated and normalised in one place."""
    rows = _norm_count(op, "rows", p.get("rows"), TABLE_ROWS)
    cols = _norm_count(op, "cols", p.get("cols"), TABLE_COLS)
    header = _norm_bool(op, "header", p["header"]) if p.get("header") is not None else False
    return rows, cols, _norm_cells(p.get("cells"), rows, cols), header


def _write_table(node: dict, rows: int, cols: int, cells, header: bool, *,
                 col_widths=None, row_tones=None, col_tones=None) -> None:
    """Write the v1.4/v1.5 table fields onto the dict `boardkit.box` returned (X3.1/X8, Y4/Y6).

    `rows`/`cols`/`cells` are always written: their normalised shape IS the contract the two halves
    share. `header: false` stays absent, like every other default in this format (`align: center`,
    `split: rows`), so a table that never asked for one looks like a board written before v1.4 —
    and so do the v1.5 fields: each is written only when the caller supplies its normalised list
    (`None` clears it), which is what keeps a v1.4-shaped document v1.4-shaped.
    """
    node.pop("parts", None)                      # a table draws its cells, never compartments
    node.pop("split", None)
    node["rows"] = int(rows)
    node["cols"] = int(cols)
    node["cells"] = list(cells)
    if header:
        node["header"] = True
    else:
        node.pop("header", None)
    for field, value in (("col_widths", col_widths), ("row_tones", row_tones),
                         ("col_tones", col_tones)):
        if value is None:
            node.pop(field, None)
        else:
            node[field] = list(value)


def _write_text_format(node: dict, p: dict, op: str) -> None:
    """Write the v1.4 text formatting onto the dict `boardkit.box` returned (X4/X8).

    Both keys are cleared first, so `update_node {text_size: "default"}` puts a large title back on
    the default — and the defaults stay absent, so a node that never asked for either is byte
    identical to v1.3.
    """
    node.pop("text_size", None)
    node.pop("bold", None)
    size = _norm_text_size(op, "text_size", p.get("text_size"))
    if size is not None and size != "default":
        node["text_size"] = size
    if p.get("bold") is not None and _norm_bool(op, "bold", p["bold"]):
        node["bold"] = True


def _part_text(part) -> str:
    """The text of one compartment, whichever of the two shapes it was written in (R1)."""
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        return str(part.get("text") or "")
    return ""


def _norm_parts(value) -> list:
    """R1/R4/R8: the compartment list to store.

    A part is stored as `{text}` — plus the `sub` it was submitted with — whatever shape it
    arrived in: R8 pins the WRITTEN schema, so a reader (the pane, the summary, a diff) never has
    to guess whether it is looking at a string or an object. A part with no text is dropped (R4
    draws nothing for it anyway), and an empty list is how `parts: []` clears the split. One
    string with newlines is read as one part per line, which is how the pane's `parts` field is
    typed.
    """
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"parts must be a list of parts, got {value!r}")
    kept = []
    for part in value:
        if part is None:
            continue
        if not isinstance(part, (str, dict)):
            raise ValueError(f"a part must be a string or an object with 'text', got {part!r}")
        text = _part_text(part)
        if not text.strip():
            continue
        item = {"text": text}
        if isinstance(part, dict) and part.get("sub"):
            item["sub"] = str(part["sub"])
        kept.append(item)
    return kept


def parts_of(node: dict) -> list:
    """R4: the compartments a node draws — a read, nothing is rewritten.

    Forgiving on purpose: a board can come from outside the plugin (hand-written JSON, an import),
    and a reader that raises on a stray entry would break a tool over a valid document.
    """
    parts = node.get("parts")
    if isinstance(parts, str):
        parts = parts.splitlines()
    if not isinstance(parts, (list, tuple)):
        return []
    return [p for p in parts if isinstance(p, (str, dict)) and _part_text(p).strip()]


def heads_of(edge: dict) -> tuple:
    """R6.1: the heads an edge actually draws, `(start, end)`, defaults filled in."""
    end = _norm_head("edge", "end", edge.get("end"), strict=False) or "arrow"
    start = _norm_head("edge", "start", edge.get("start"), strict=False)
    if start is None:                       # absent: the legacy flag decides the source end
        start = "arrow" if edge.get("bidir") else "none"
    return start, end


def side_of(edge: dict, field: str = "from_side") -> str:
    """W3.1/W4: where one end attaches — absent or unknown means `auto` (the renderer's default)."""
    return _norm_side("edge", field, edge.get(field), strict=False) or "auto"


def at_of(item: dict, field: str = "at") -> float:
    """v1.7 (A3, the reader layer): the position one end's `at`/`from_at`/`to_at` reads as.

    The same lenient policy as the renderer's `_at_of`/`atOf`: absent, `null`, a string, a
    boolean or a NaN all read as 0.5 — the midpoint, i.e. exactly the point every pre-v1.7
    document means — and a finite number clamps into [0, 1] (`-3` -> 0, `1e400`'s +inf -> 1).
    The ops layer RAISES on the same inputs instead (`_norm_at`). Because a spelled-out 0.5 and
    an absent key both read as 0.5 here, the diff never reports a default spelling as an edit.
    """
    value = item.get(field) if isinstance(item, dict) else None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return 0.5
    if isinstance(value, float) and math.isnan(value):
        return 0.5
    if value <= 0:
        return 0.0
    if value >= 1:
        return 1.0
    return float(value)


def head_size_of(edge: dict) -> str:
    """W3.1/W3.3/W4: the head size an edge draws with — absent or unknown means `medium`."""
    return _norm_head_size("edge", "head_size", edge.get("head_size"), strict=False) or "medium"


# ------------------------------------------------- v1.6 lines: the READ half (Z2/Z3, lane L's
# rendering reads the same way). Both halves of this plugin read a document it did not write
# (hand-written JSON, an import), so a stray value becomes the default here and never raises; the
# OPS below raise instead. That difference is deliberate (Z2's three layers).


def _number_ok(value) -> bool:
    """A coordinate: a real number, not a bool, and finite — what Z2 pins for a line's points."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def points_of(line: dict) -> list:
    """Z3.1/Z2: the points a line is drawn from, or `[]` when the ITEM cannot be drawn at all.

    A non-list, an entry that is not an [x, y] pair of finite numbers, or fewer than two entries
    drops the whole line (half a polyline is worse than no line); a negative coordinate is
    CLAMPED to 0, because the canvas origin is the board's corner and `bounds()` is max-only
    (Z2/Z3.5). Nothing is re-rounded or normalised: a point is stored as it was written.
    """
    raw = line.get("points")
    if not isinstance(raw, (list, tuple)) or len(raw) < LINE_MIN_POINTS:
        return []
    out = []
    for entry in raw:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            return []
        if not _number_ok(entry[0]) or not _number_ok(entry[1]):
            return []
        x, y = entry[0], entry[1]
        out.append([0 if x < 0 else x, 0 if y < 0 else y])
    return out


def line_tone_of(line: dict) -> str:
    """Z3.2: the tone a line draws with — absent or unknown means `default`, as for an edge."""
    tone = str(line.get("tone") or "").strip().lower()
    return tone if tone in TONES else "default"


def line_style_of(line: dict) -> str:
    """Z3.2: the stroke style a line draws with — absent or unknown means `solid`, as for an edge."""
    style = str(line.get("style") or "").strip().lower()
    return style if style in EDGE_STYLES else "solid"


def line_heads_of(line: dict) -> tuple:
    """Z3.2: the heads a line draws, `(start, end)`, with the edge's own defaults filled in.

    A line has no `bidir` flag (that is the legacy edge spelling), so an absent `start` is simply
    `none`; the heads are the same boxes the `medium` edge head size uses.
    """
    end = _norm_head("line", "end", line.get("end"), strict=False) or "arrow"
    return (_norm_head("line", "start", line.get("start"), strict=False) or "none"), end


def anchor_of(line: dict, field: str = "from"):
    """Z3.3/Z2 + v1.7 (A3): one end's anchor as the renderer reads it — `(ref, side, at)`.

    Not an object, no string `ref`, or an unknown `side` all read as the default (a free end, and
    `auto`); `at` reads through `at_of`, so a position spelled as the default 0.5 and one left
    absent compare equal. Whether that `ref` names anything on the board is `line_anchors`'
    business, because only a board knows its own ids.
    """
    value = line.get(field)
    if not isinstance(value, dict):
        return None
    ref = value.get("ref")
    if not isinstance(ref, str) or not ref:
        return None
    return ref, (_norm_side("line", field, value.get("side"), strict=False) or "auto"), \
        at_of(value)


def _anchor_ids(board: dict) -> set:
    """Z3.4: what a `ref` may name — a node or a note (an edge resolves nodes only)."""
    return {str(item.get("id")) for item in
            list(board.get("nodes", [])) + list(board.get("notes", []))}


def line_anchors(line: dict, board: dict) -> tuple:
    """Z3.4: the two end anchors a line actually uses, `(from, to)`, each `(ref, side, at)` or None.

    **A dangling anchor is not a vanished item**: a `ref` that names nothing on THIS board falls
    back to the stored point, so that end reads FREE and the line is still drawn (the reader stays
    lenient, as Z3.4 requires). `add_line`/`update_line` refuse the same input instead.
    """
    ids = _anchor_ids(board)
    return tuple(anchor if anchor and anchor[0] in ids else None
                 for anchor in (anchor_of(line, "from"), anchor_of(line, "to")))


def _anchor_phrase(anchors) -> str:
    """One end-anchor pair in words for the edit log: `none`, `from`, `to` or `both` (Z7.3)."""
    ends = [name for name, anchor in zip(("from", "to"), anchors) if anchor]
    return "both" if len(ends) == 2 else (ends[0] if ends else "none")


def align_of(node: dict) -> str:
    return _norm_align(node.get("align"))


def split_of(node: dict) -> str:
    return _norm_split(node.get("split"))


def rows_of(node: dict) -> int:
    """X3.1: how many rows a table draws — absent or junk means the default 2, never a crash."""
    try:
        return _norm_count("table", "rows", node.get("rows"), TABLE_ROWS)
    except ValueError:
        return int(TABLE_ROWS)


def cols_of(node: dict) -> int:
    """X3.1: how many columns a table draws — absent or junk means the default 2."""
    try:
        return _norm_count("table", "cols", node.get("cols"), TABLE_COLS)
    except ValueError:
        return int(TABLE_COLS)


def header_of(node: dict) -> bool:
    """X3.1: whether the first row is a header band — absent (or junk) means no."""
    value = node.get("header")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "on", "1")
    return bool(value) if isinstance(value, (int, float)) else False


def cells_of(node: dict) -> list:
    """X3.1/X3.2: the cells a table draws, read the way the renderer reads them.

    One flat row-major list, always normalised to `rows * cols` (missing cells are empty ones,
    extras are dropped) — and empty cells are kept, because an empty compartment is a real cell.
    """
    return _norm_cells(node.get("cells"), rows_of(node), cols_of(node))


def table_view(node: dict) -> tuple:
    """X3.1/X8: the table a node draws as one comparable value (rows, cols, texts, header).

    Compared through the effective reads, so padding a cell list or writing `header: false` out in
    full is not an edit — the same rule the heads and sides follow. The third entry is the cell
    TEXTS (Y4.1): a cell's own tone/alignment is a separate field with its own diff line, so it must
    not read as "the cells changed".
    """
    return (rows_of(node), cols_of(node),
            tuple(_cell_text(cell) for cell in cells_of(node)), header_of(node))


def text_size_of(node: dict) -> str:
    """X4.1/X4.4: the size a node's own text is drawn at — absent or unknown means `default`."""
    size = str(node.get("text_size") or "").strip().lower()
    return size if size in TEXT_SIZES else "default"


def bold_of(node: dict) -> bool:
    """X4.1: whether a node's own text is drawn bold — absent (or junk) means no."""
    value = node.get("bold")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "on", "1")
    return bool(value) if isinstance(value, (int, float)) else False


def _label_of(node: dict) -> tuple:
    """X3.2/X8: what a node draws on its own face — a table draws neither its `text` nor its `sub`
    (the header row carries that role), so an edit to either is not an edit to the board."""
    if node.get("kind") == "table":
        return None, None, node.get("tone"), node.get("kind")
    return node.get("text"), node.get("sub"), node.get("tone"), node.get("kind")


def grid_state(board: dict) -> dict:
    """R5.1: the grid a board is drawn with — absence means off, 10px, lines, snapping on."""
    size = board.get("grid")
    try:
        size = float(DEFAULT_GRID if size is None else size)
    except (TypeError, ValueError):
        size = float(DEFAULT_GRID)
    style = board.get("grid_style")
    return {"on": bool(board.get("grid_on")), "size": size,
            "style": style if style in GRID_STYLES else "lines",
            "snap": board.get("snap") is not False}


def grid_phrase(board: dict) -> str:
    """The grid in words — `on, 20px, dots` (R8); both callers below prefix it."""
    state = grid_state(board)
    size = state["size"]
    size = int(size) if float(size).is_integer() else size
    return f"{'on' if state['on'] else 'off'}, {size:g}px, {state['style']}"


def grid_line(board: dict) -> str:
    """One sentence for the edit log — `grid on, 20px, dots` (R8)."""
    return f"grid {grid_phrase(board)}"


def _write_split(node: dict, p: dict, parts, allow_right: bool = False) -> None:
    """Write the v1.2 split fields onto the dict `boardkit.box` returned (R1/R4/R8).

    They are set here rather than passed in, so these ops keep working against a renderer that
    does not know the keywords yet; the defaults (`split: rows`, `align: center`) stay absent,
    because an absent key is what every board drawn before v1.2 looks like.
    """
    node.pop("parts", None)
    node.pop("split", None)
    node.pop("align", None)
    if parts:
        node["parts"] = parts
        if _norm_split(p.get("split")) == "columns":
            node["split"] = "columns"
    align = _norm_align(p.get("align"), allow_right)
    if align != "center":               # the default is absent, as it always was
        node["align"] = align


def _drop_null_heads(items) -> None:
    """A renderer that always writes the head keys can leave them null on an edge that asked for
    none; a null head means "not given" (R6.1), so it never belongs in a stored document."""
    for item in items:
        for field in ("start", "end"):
            if field in item and item[field] is None:
                item.pop(field)


def _norm_waypoints(op: str, value) -> list:
    """Z7.6/W3.2: an edge's hand-routed path — a list of `[x, y]` points the router runs through.

    The document, both renderers and `bus` have carried `waypoints` all along; the ops were the one
    layer that dropped the field silently, so an agent could not author a routed path at all. The
    points are checked like a line's (finite numbers, board units, 0 or more): both SVGs start their
    viewBox at `0 0` and `bounds()` is max-only, so a negative waypoint would be drawn clipped.
    There is no minimum, because one waypoint is a legal path (`bus` writes exactly two) and an
    empty list is how the routing is taken away again.
    """
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{op}: waypoints must be a list of [x, y] pairs, got {value!r}")
    out = []
    for index, entry in enumerate(value):
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise ValueError(f"{op}: waypoints[{index}] is not an [x, y] pair, got {entry!r}")
        for number, axis in zip(entry, ("x", "y")):
            if not _number_ok(number):
                raise ValueError(f"{op}: waypoints[{index}].{axis} is not a finite number, "
                                 f"got {number!r}")
            if number < 0:
                raise ValueError(f"{op}: waypoints[{index}].{axis} is negative ({number!r}); "
                                 f"coordinates are board units, 0 or more")
        out.append([entry[0], entry[1]])
    return out


def _write_waypoints(item: dict, p: dict, op: str, *, clear: bool = False) -> None:
    """Write `waypoints` onto the dict `boardkit.edge` returned (Z7.6), the `_write_heads` way.

    It is set here rather than passed into the builder so the op keeps working against a renderer
    that predates `waypoints`; `waypoints: []` is how an edge is put back on the auto-router, and an
    op that never mentioned the field leaves whatever the edge already carries alone.
    """
    if clear:
        item.pop("waypoints", None)
    if p.get("waypoints") is None:
        return
    points = _norm_waypoints(op, p["waypoints"])
    if points:
        item["waypoints"] = points
    else:
        item.pop("waypoints", None)


def _write_heads(item: dict, p: dict, op: str) -> None:
    """Write the per-end heads onto the dict `boardkit.edge` returned (R6/R8).

    A head is stored only when the op asked for one — absent means today's behaviour (`end` arrow,
    `start` none) — plus the legacy translation: `bidir: true` with no `start` stores
    `start: "arrow"`. The keys are removed first so the result does not depend on what the
    renderer's own `edge()` writes into the dict.
    """
    item.pop("start", None)
    item.pop("end", None)
    start = _norm_head(op, "start", p.get("start"))
    if start is None and p.get("bidir") and not p.get("start"):
        start = "arrow"
    end = _norm_head(op, "end", p.get("end"))
    if start is not None:
        item["start"] = start
    if end is not None:
        item["end"] = end


def _write_sides(item: dict, p: dict, op: str, *, clear: bool = False) -> None:
    """Write the v1.3 attach sides and head size onto the dict `boardkit.edge` returned (W4),
    plus v1.7's `from_at`/`to_at` positions (A3).

    `"auto"`/`"medium"` are stored as given — they mean the default, and a diff compares the
    EFFECTIVE value, so spelling one out is not an edit. With `clear`, the keys are dropped
    first so the stored document does not depend on what the renderer's own `edge()` puts there;
    an op that never mentioned a field leaves the one already on the edge alone (that is how
    `update_edge` keeps the other fields). A position is different from a side in one way: a
    NULL is a real instruction (reset to the default 0.5 — the key is dropped), so it is
    matched by key presence, not by value.
    """
    if clear:
        item.pop("from_side", None)
        item.pop("to_side", None)
        item.pop("head_size", None)
        item.pop("from_at", None)
        item.pop("to_at", None)
    for field in ("from_side", "to_side"):
        side = _norm_side(op, field, p.get(field))
        if side is not None:
            item[field] = side
    size = _norm_head_size(op, "head_size", p.get("head_size"))
    if size is not None:
        item["head_size"] = size
    for field in ("from_at", "to_at"):
        if field not in p:
            continue
        at = _norm_at(op, field, p[field])
        if at is None:
            item.pop(field, None)   # null resets the position: absent IS the default 0.5
        else:
            item[field] = at


def apply_ops(board: dict, ops) -> list[str]:
    """Apply ops in order; return one human line per op. Raises ValueError on a bad op."""
    if isinstance(ops, str):
        ops = json.loads(ops)
    if not isinstance(ops, list):
        raise ValueError("ops must be a list of objects")
    notes = []
    for params in ops:
        if not isinstance(params, dict) or "op" not in params:
            raise ValueError(f"each op must be an object with an 'op' key, got {params!r}")
        name = params["op"]
        handler = globals().get(f"_op_{name}")
        if name not in OPS or handler is None:
            raise ValueError(f"unknown op '{name}'; valid ops: {', '.join(OPS)}")
        notes.append(handler(board, params) or name)
    # Keep the canvas in step with the content: the desktop editor renders straight from
    # width/height, so a caption or note placed outside them would be invisible in the pane.
    bk = boardkit()
    need_w, need_h = bk.bounds(board)
    # Z3.5: a line is free to run outside every box, so its own points have to grow the canvas too
    # (a line drawn past the last box is otherwise clipped by both SVGs, whose viewBox starts at
    # 0 0). `bounds()` is lane L's half and also counts a line's RESOLVED anchors; this is the
    # max-only subset the ops can see on their own, so the two can never disagree — the canvas only
    # ever grows.
    for line in board.get("lines", []):
        for x, y in points_of(line):
            need_w, need_h = max(need_w, x + 40), max(need_h, y + 40)
    board["width"] = max(float(board.get("width") or 0), need_w)
    board["height"] = max(float(board.get("height") or 0), need_h)
    return notes


def _op_add_node(b, p):
    bk = boardkit()
    nid = p.get("id") or _next_id(b, "n")
    kind = _norm_kind("add_node", p.get("kind"))
    # X3/X8: a table is its own shape — it needs no text (X3.2), and its rows/cols/cells/header and
    # its EFFECTIVE size (X3.3, not the box's 190x72) are established before the text requirement
    # and before any normalisation.
    if kind == "table":
        return _add_table(b, p, nid, "add_node")
    parts = _norm_parts(p["parts"]) if p.get("parts") is not None else []
    # R4/R8: a split box may have no header row at all (the parity fixture has one), so a
    # non-empty `parts` stands in for `text`; a plain box still needs its label.
    if not parts:
        _require(p, "text")
    text = p["text"] if p.get("text") is not None else ""
    h = p.get("h")
    if h is None and parts:                      # R8: a split box sizes itself to fit its parts
        h = split_height(text, parts)
    x, y = p.get("x"), p.get("y")
    if x is None or y is None:
        ax, ay = _auto_position(b, p.get("w", 190), h if h is not None else 72)
        x, y = (ax if x is None else x), (ay if y is None else y)
    node = bk.box(nid, text, x, y, w=p.get("w", 190), h=h if h is not None else 72,
                  sub=p.get("sub"), tone=p.get("tone", "default"), kind=kind)
    _write_split(node, p, parts)
    _write_text_format(node, p, "add_node")
    b.setdefault("nodes", []).append(node)
    return f"added node {nid} '{text}' at ({x:g}, {y:g})"


def _add_table(b, p, nid: str, op: str = "add_node") -> str:
    """X3/X8/Y6: add a table — textless and headerless ones are legal, and the size is resolved.

    The box builder answers 190x72 for its own default shape; a table's own numbers (X3.3/Y4.6) are
    written instead, so what an op stores is the same size every reader resolves and no later
    consumer ever sees a 0x0 table written by this plugin. `add_table` and `add_node {kind: "table"}`
    come through this ONE path, with the one normalisation Y4 pins, so the pane and either op cannot
    write two different documents.
    """
    bk = boardkit()
    _validate_table_fields(op, p)
    rows, cols, cells, header = _norm_table(op, p)
    # X8: the text fields are validated on every node kind — a table simply does not store them
    # (X3.3), but a bad value still fails the op list instead of being swallowed.
    _norm_text_size(op, "text_size", p.get("text_size"))
    if p.get("bold") is not None:
        _norm_bool(op, "bold", p["bold"])
    # Y4.2/Y4.3: a field the op did not give stays absent; one it did is stored normalised.
    extra = {name: (_normalised_field(name, p[name], rows, cols)
                    if p.get(name) is not None else None)
             for name in ("col_widths", "row_tones", "col_tones")}
    auto_w, auto_h = table_size(rows, cols, header)
    w = p.get("w") if _positive(p.get("w")) else auto_w
    h = p.get("h") if _positive(p.get("h")) else auto_h
    x, y = p.get("x"), p.get("y")
    if x is None or y is None:
        ax, ay = _auto_position(b, w, h)
        x, y = (ax if x is None else x), (ay if y is None else y)
    text = p["text"] if p.get("text") is not None else ""
    node = bk.box(nid, text, x, y, w=w, h=h, tone=p.get("tone", "default"), kind="table")
    node["kind"] = "table"                       # the installed library's KINDS may predate X3
    node.pop("sub", None)                        # X3.2: a table draws neither its text nor its sub
    _write_table(node, rows, cols, cells, header, **extra)
    _write_split(node, p, [], allow_right=True)   # align only; a table may be right-aligned
    b.setdefault("nodes", []).append(node)
    return f"added {rows}x{cols} table {nid} at ({x:g}, {y:g})"


def _op_add_table(b, p):
    """Y6: `add_table` — the table op, for a caller that has nothing but a table to draw.

    Same fields, same defaults and the same normalisation as `add_node {kind: "table"}`, without
    having to say the kind (and without the box builder's 190x72 ever reaching the document).
    """
    nid = p.get("id") or _next_id(b, "n")
    return _add_table(b, p, nid, "add_table")


def _op_update_node(b, p):
    _require(p, "id")
    node = _find(b.get("nodes", []), id=p["id"])
    if not node:
        raise ValueError(f"update_node: no node '{p['id']}'")
    for key in ("text", "sub", "tone", "x", "y", "w", "h"):
        if p.get(key) is not None:
            node[key] = p[key]
    if p.get("kind") is not None:
        node["kind"] = _norm_kind("update_node", p["kind"])
    if node.get("kind") == "table":
        _update_table(node, p)
        return f"updated table {p['id']} ({rows_of(node)}x{cols_of(node)})"
    # A node that is not (or is no longer) a table drops the table's fields.
    for gone in ("rows", "cols", "cells", "header"):
        node.pop(gone, None)
    # R8: `parts: []` says "no split any more", so it cannot go through the `is not None`
    # whitelist above — the key has to be looked at on its own (`null` is still "not given").
    # `h` is not recomputed: an existing box keeps the size the caller gave it.
    if p.get("parts") is not None:
        parts = _norm_parts(p["parts"])
        if parts:
            node["parts"] = parts
        else:
            node.pop("parts", None)
    if p.get("split") is not None:
        if _norm_split(p["split"]) == "columns" and parts_of(node):
            node["split"] = "columns"
        else:
            node.pop("split", None)
    _write_text_format(node, p, "update_node")
    if p.get("align") is not None:
        if _norm_align(p["align"]) == "left":
            node["align"] = "left"
        else:
            node.pop("align", None)
    return f"updated node {p['id']}"


def _update_table(node: dict, p: dict, op: str = "update_node") -> None:
    """X3/X8/Y6: update a table in place — the same normalisation an `add_node` of one goes through.

    An absent `rows`/`cols` keeps the table's own count, `cells` is re-normalised to the resulting
    shape (pad `""` / truncate the tail, exactly as the pane does, X3.4) and `text_size`/`bold` are
    dropped: they do not apply to a table (X3.3). `col_widths`/`row_tones`/`col_tones` are the v1.5
    fields: an absent one keeps the list the table already had (re-normalised to the new shape), a
    supplied one is validated (Y6) and normalised (Y4.2/Y4.3). A size the caller gave — or the one
    the table already had — is left alone; an absent or non-positive one is resolved to the table's
    own numbers, so an op never leaves a table at 0x0.
    """
    _validate_table_fields(op, p)
    rows = _norm_count(op, "rows", p.get("rows"), rows_of(node))
    cols = _norm_count(op, "cols", p.get("cols"), cols_of(node))
    header = _norm_bool(op, "header", p["header"]) if p.get("header") is not None \
        else header_of(node)
    cells = _norm_cells(p["cells"], rows, cols) if p.get("cells") is not None \
        else _norm_cells(cells_of(node), rows, cols)
    extra = {}
    for name in ("col_widths", "row_tones", "col_tones"):
        if p.get(name) is not None:
            extra[name] = _normalised_field(name, p[name], rows, cols)
        elif name in node:
            extra[name] = _normalised_field(name, node.get(name), rows, cols)
        else:
            extra[name] = None
    _write_table(node, rows, cols, cells, header, **extra)
    for gone in ("parts", "split", "text_size", "bold"):
        node.pop(gone, None)
    # Y4.5a: a table's own align is a writer field (`right` included). An absent key keeps whatever
    # the table had — a write that does not mention align must not reset it (v1.5 verifier, MAJOR).
    if p.get("align") is not None:
        node.pop("align", None)
        align = _norm_align(p["align"], allow_right=True)
        if align != "center":
            node["align"] = align
    # X8/X3.3: validated like any other node, but a table never stores the text fields.
    _norm_text_size(op, "text_size", p.get("text_size"))
    if p.get("bold") is not None:
        _norm_bool(op, "bold", p["bold"])
    auto_w, auto_h = table_size(rows, cols, header)
    if not _positive(node.get("w")):
        node["w"] = auto_w
    if not _positive(node.get("h")):
        node["h"] = auto_h


def _op_move_node(b, p):
    _require(p, "id", "x", "y")
    node = _find(b.get("nodes", []), id=p["id"])
    if not node:
        raise ValueError(f"move_node: no node '{p['id']}'")
    node["x"], node["y"] = p["x"], p["y"]
    return f"moved node {p['id']} to ({p['x']:g}, {p['y']:g})"


def _op_delete_node(b, p):
    _require(p, "id")
    before = len(b.get("nodes", []))
    b["nodes"] = [n for n in b.get("nodes", []) if n.get("id") != p["id"]]
    dropped = len(b.get("edges", []))
    b["edges"] = [e for e in b.get("edges", []) if p["id"] not in (e.get("from"), e.get("to"))]
    if len(b["nodes"]) == before:
        raise ValueError(f"delete_node: no node '{p['id']}'")
    return f"deleted node {p['id']} (and {dropped - len(b['edges'])} edge(s))"


def _op_add_edge(b, p):
    bk = boardkit()
    _require(p, "from", "to")
    ids = {n.get("id") for n in b.get("nodes", [])}
    for key in ("from", "to"):
        if p[key] not in ids:
            raise ValueError(f"add_edge: no node '{p[key]}'")
    item = bk.edge(p["from"], p["to"], p.get("label"), style=p.get("style", "solid"),
                   route=p.get("route", "auto"), bidir=bool(p.get("bidir")),
                   tone=p.get("tone", "default"), label_at=p.get("label_at"))
    _write_heads(item, p, "add_edge")
    _write_sides(item, p, "add_edge", clear=True)
    _write_waypoints(item, p, "add_edge", clear=True)
    b.setdefault("edges", []).append(item)
    return f"added edge {p['from']} -> {p['to']}" + (f" '{p['label']}'" if p.get("label") else "")


def _op_update_edge(b, p):
    _require(p, "from", "to")
    item = _find(b.get("edges", []), **{"from": p["from"], "to": p["to"]})
    if not item:
        raise ValueError(f"update_edge: no edge {p['from']} -> {p['to']}")
    # v1.7 (A5/A3): an end can be RE-POINTED onto another node — the edge's `from`/`to` stay the
    # lookup key, `new_from`/`new_to` carry the move. The new id must name a node (an edge's ends
    # always resolve), and a re-point onto an edge pair that already exists is refused, because
    # two edges with the same key could never be told apart by the next update_edge.
    ids = {n.get("id") for n in b.get("nodes", [])}
    for field, key in (("new_from", "from"), ("new_to", "to")):
        if p.get(field) is None:
            continue
        if p[field] not in ids:
            raise ValueError(f"update_edge: {field} '{p[field]}' names no node on this board; "
                             f"an edge's end re-points onto a node id")
        if p[field] == item[key]:
            continue
        candidate = dict(item, **{key: p[field]})
        if _find(b.get("edges", []), **{"from": candidate["from"], "to": candidate["to"]}):
            raise ValueError(f"update_edge: {field} would make "
                             f"{candidate['from']} -> {candidate['to']} a duplicate edge")
        item[key] = p[field]
    was_bidir = bool(item.get("bidir"))
    for key in ("label", "style", "route", "tone", "label_at", "bidir"):
        if p.get(key) is not None:
            item[key] = p[key]
    # Heads are per-end fields (R6), so each is updated on its own: `start: "none"` is how a head
    # is taken away again, and turning `bidir` off drops the arrow the flag stood for.
    for field in ("start", "end"):
        head = _norm_head("update_edge", field, p.get(field))
        if head is not None:
            item[field] = head
    if p.get("bidir") and not p.get("start") and "start" not in item:
        item["start"] = "arrow"
    elif p.get("bidir") is False and was_bidir and p.get("start") is None:
        item.pop("start", None)
    # W4: the attach sides and the head size are per-edge fields too. `from_side: "auto"` (or a
    # head size back to `"medium"`) is how either is reset to the default it stands for.
    _write_sides(item, p, "update_edge")
    _write_waypoints(item, p, "update_edge")
    if (item["from"], item["to"]) != (p["from"], p["to"]):
        return (f"updated edge {p['from']} -> {p['to']} "
                f"(now {item['from']} -> {item['to']})")
    return f"updated edge {p['from']} -> {p['to']}"


def _op_delete_edge(b, p):
    _require(p, "from", "to")
    before = len(b.get("edges", []))
    b["edges"] = [e for e in b.get("edges", [])
                  if not (e.get("from") == p["from"] and e.get("to") == p["to"])]
    if len(b["edges"]) == before:
        raise ValueError(f"delete_edge: no edge {p['from']} -> {p['to']}")
    return f"deleted edge {p['from']} -> {p['to']}"


# ------------------------------------------------------------ v1.6 lines (Z7.1/Z7.2): the ops


def _norm_line_points(op: str, value) -> list:
    """Z7.1/Z2 (the ops column): `points` is a list of at least two finite `[x, y]` pairs of
    non-negative numbers, or the op RAISES, naming the field.

    A point list with an unusable entry has no sane default, and half a polyline is worse than no
    line — so this is stricter than every other field here on purpose. The READER (`points_of`)
    drops such a line and clamps a negative coordinate instead of raising: three layers, three
    policies (Z2), and the two are deliberately different.
    """
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{op}: points must be a list of [x, y] pairs, got {value!r}")
    if len(value) < LINE_MIN_POINTS:
        raise ValueError(f"{op}: points needs at least {LINE_MIN_POINTS} [x, y] pairs, "
                         f"got {len(value)}")
    out = []
    for index, entry in enumerate(value):
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise ValueError(f"{op}: points[{index}] is not an [x, y] pair, got {entry!r}")
        for number, axis in zip(entry, ("x", "y")):
            if not _number_ok(number):
                raise ValueError(f"{op}: points[{index}].{axis} is not a finite number, "
                                 f"got {number!r}")
            if number < 0:
                raise ValueError(f"{op}: points[{index}].{axis} is negative ({number!r}); "
                                 f"coordinates are board units, 0 or more")
        out.append([entry[0], entry[1]])
    return out


def _norm_line_anchor(op: str, field: str, value, ids: set):
    """Z7.1/Z3.3 + v1.7 (A2/A3): one end anchor from an op — `{"ref": <id>, "side": <side>,
    "at": <0..1>}`, or None for FREE.

    `null` (and an absent field, which the caller filters out) leaves the end free. An object whose
    `ref` is not a string, or whose `ref` names nothing on this board, RAISES: the READER keeps the
    same document working by falling back to the stored point (Z3.4), but an op that quietly wrote
    a dangling anchor would hide the typo from the agent that made it. A `side` outside `SIDES`
    raises like every other enum here; an absent one is written as `auto`. A3's `at` follows the
    ops column of the three-layer table: a number outside [0, 1] or a non-number RAISES naming
    `<field>.at` (the readers clamp/ignore the same value instead), and `at: null` RESETS the
    position — the key is dropped, because an absent `at` IS the default 0.5.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{op}: {field} must be an object like {{ref: <id>, side: auto}}, "
                         f"or null to leave that end free, got {value!r}")
    ref = value.get("ref")
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError(f"{op}: {field}.ref must be the id of a node or a note, "
                         f"got {value.get('ref')!r}")
    if ref not in ids:
        raise ValueError(f"{op}: {field}.ref '{ref}' names nothing on this board; an anchor "
                         f"attaches to a node or a note")
    out: dict = {"ref": ref, "side": _norm_side(op, f"{field}.side", value.get("side")) or "auto"}
    if "at" in value:
        at = _norm_at(op, f"{field}.at", value["at"])
        if at is not None:
            out["at"] = at      # a null already means the reset: the key simply stays absent
    return out


def _norm_line_tone(op: str, field: str, value) -> str:
    """Z3.2/Z7.1: a line's tone — the same vocabulary as an edge, refused when unknown."""
    tone = str(value).strip().lower()
    if tone not in TONES:
        raise ValueError(f"{op}: {field} '{value}' is not a tone; valid tones: {', '.join(TONES)}")
    return tone


def _norm_line_style(op: str, field: str, value) -> str:
    """Z3.2/Z7.1: a line's stroke style — the same three an edge draws with."""
    style = str(value).strip().lower()
    if style not in EDGE_STYLES:
        raise ValueError(f"{op}: {field} '{value}' is not a style; valid styles: "
                         f"{', '.join(EDGE_STYLES)}")
    return style


def _norm_line_head(op: str, field: str, value):
    """Z3.2/Z7.1: `start`/`end` on a line are the edge's heads; `none` is how one is taken away."""
    return _norm_head(op, field, value)


def _line_fields(op: str, p: dict, ids: set, *, require_points: bool) -> dict:
    """Z7.1: the validation `add_line` and `update_line` share — IDENTICAL for both, by design.

    The spec pins one table (Z2's ops column) for the two, so there is one code path: two would
    drift. Only the fields the op actually sent come back, already normalised; `from`/`to` come back
    as None when the op asked for a free end (`null`), which the caller drops on add and clears on
    update.
    """
    out = {}
    # Round-1 finding 4: MEMBERSHIP first, then the value — an explicit null must raise, naming
    # the field (Z2/Z7.1); only `from`/`to` accept null (clearing the anchor). `p.get(field) is
    # not None` used to swallow the null, so `update_line {tone: null}` silently "succeeded".
    if require_points or "points" in p:
        if require_points:
            _require(p, "points")
        out["points"] = _norm_line_points(op, p.get("points"))
    for field, normalise in (("tone", _norm_line_tone), ("style", _norm_line_style),
                             ("start", _norm_line_head), ("end", _norm_line_head)):
        # "" means "not given" here exactly as it does on an edge; `null` is only meaningful for an
        # anchor (Z7.1), so a null head, tone or style is refused — it never reaches the document.
        if field in p:
            if p[field] is None:
                raise ValueError(f"{op}: {field} may not be null — omit the field to keep the "
                                 f"line's current {field}; only from/to accept null (clearing "
                                 f"the anchor)")
            value = normalise(op, field, p[field])
            if value is not None:
                out[field] = value
    for field in ("from", "to"):
        if field in p:
            out[field] = _norm_line_anchor(op, field, p[field], ids)
    return out


def _line_id(board: dict, points) -> str:
    """Z3: a new line's id — `line-` plus 8 characters, never one already on the board.

    Derived from the path (and a nonce) instead of a counter, so replaying the same op list gives
    the same document twice, and a hand-written board cannot make the id collide.
    """
    used = {str(item.get("id")) for item in board.get("lines", [])}
    seed = json.dumps([[float(x), float(y)] for x, y in points])
    nonce = 0
    while True:
        digest = hashlib.sha1(f"{seed}|{nonce}".encode("utf-8")).hexdigest()[:8]
        if f"line-{digest}" not in used:
            return f"line-{digest}"
        nonce += 1


def _op_add_line(b, p):
    """Z7.1: `add_line` — one `lines` entry: a path of at least two points, optionally anchored
    at either end. A line is not an edge: it connects nothing, it keeps its own path, and there is
    exactly one of it per id."""
    fields = _line_fields("add_line", p, _anchor_ids(b), require_points=True)
    points = fields.pop("points")
    lid = _line_id(b, points)
    # The defaults an edge stores are stored here too, so what an op writes and what the pane
    # writes are the same document (the renderer's own defaults then change nothing).
    line = {"id": lid, "points": points, "tone": fields.pop("tone", "default"),
            "style": fields.pop("style", "solid")}
    for field in ("start", "end", "from", "to"):
        if fields.get(field) is not None:
            line[field] = fields[field]
    b.setdefault("lines", []).append(line)
    return f"added line {lid} ({len(points)} points)"


def _op_update_line(b, p):
    """Z7.1: `update_line` — re-shape one line. Same validation as `add_line`; an explicit `null`
    in `from`/`to` takes that anchor away (the end falls back to its stored point) and a field the
    op did not send is left alone."""
    _require(p, "id")
    line = _find(b.get("lines", []), id=p["id"])
    if not line:
        raise ValueError(f"update_line: no line '{p['id']}'")
    fields = _line_fields("update_line", p, _anchor_ids(b), require_points=False)
    if "points" in fields:
        line["points"] = fields.pop("points")
    for field in ("tone", "style", "start", "end"):
        if field in fields:
            line[field] = fields.pop(field)
    for field in ("from", "to"):
        if field in fields:
            if fields[field] is None:
                line.pop(field, None)
            else:
                line[field] = fields[field]
    return f"updated line {p['id']}"


def _op_delete_line(b, p):
    _require(p, "id")
    before = len(b.get("lines", []))
    b["lines"] = [line for line in b.get("lines", []) if line.get("id") != p["id"]]
    if len(b["lines"]) == before:
        raise ValueError(f"delete_line: no line '{p['id']}'")
    return f"deleted line {p['id']}"


def _op_add_group(b, p):
    bk = boardkit()
    _require(p, "text", "x", "y", "w", "h")
    gid = p.get("id") or _next_id(b, "g")
    b.setdefault("groups", []).append(
        bk.group(gid, p["text"], p["x"], p["y"], p["w"], p["h"], tone=p.get("tone", "muted")))
    return f"added group {gid} '{p['text']}'"


def _op_update_group(b, p):
    _require(p, "id")
    item = _find(b.get("groups", []), id=p["id"])
    if not item:
        raise ValueError(f"update_group: no group '{p['id']}'")
    for key in ("text", "tone", "x", "y", "w", "h"):
        if p.get(key) is not None:
            item[key] = p[key]
    return f"updated group {p['id']}"


def _op_delete_group(b, p):
    _require(p, "id")
    before = len(b.get("groups", []))
    b["groups"] = [g for g in b.get("groups", []) if g.get("id") != p["id"]]
    if len(b["groups"]) == before:
        raise ValueError(f"delete_group: no group '{p['id']}'")
    return f"deleted group {p['id']}"


def _op_add_caption(b, p):
    bk = boardkit()
    _require(p, "text", "x", "y")
    item = bk.caption(p["text"], p["x"], p["y"], w=p.get("w", 220), tone=p.get("tone", "muted"))
    if p.get("id"):
        item["id"] = p["id"]
    b.setdefault("nodes", []).append(item)
    return f"added caption {item['id']} at ({p['x']:g}, {p['y']:g})"


def _op_add_note(b, p):
    bk = boardkit()
    _require(p, "text", "x", "y")
    item = bk.note(p["text"], p["x"], p["y"], w=p.get("w", 280), tone=p.get("tone", "warn"))
    if p.get("id"):
        item["id"] = p["id"]
    b.setdefault("notes", []).append(item)
    return f"added note {item['id']}"


def _op_update_note(b, p):
    _require(p, "id")
    item = _find(b.get("notes", []), id=p["id"])
    if not item:
        raise ValueError(f"update_note: no note '{p['id']}'")
    for key in ("text", "tone", "x", "y", "w"):
        if p.get(key) is not None:
            item[key] = p[key]
    return f"updated note {p['id']}"


def _op_delete_note(b, p):
    _require(p, "id")
    before = len(b.get("notes", []))
    b["notes"] = [n for n in b.get("notes", []) if n.get("id") != p["id"]]
    if len(b["notes"]) == before:
        raise ValueError(f"delete_note: no note '{p['id']}'")
    return f"deleted note {p['id']}"


def _op_set_title(b, p):
    _require(p, "title")
    b["title"] = p["title"]
    return f"title -> '{p['title']}'"


def _op_set_grid(b, p):
    """R5/R8: the canvas grid — spacing, on/off, style, and whether drags snap to it.

    Only the keys the caller sent are written; a call with none of them just reports the grid the
    board is drawn with. Every value that arrives is validated here, so a bad one fails the whole
    op list loudly instead of leaving the document half-edited.
    """
    if p.get("size") is not None:
        try:
            size = float(p["size"])
        except (TypeError, ValueError):
            size = 0.0
        if not size > 0:                        # rejects 0, negatives, NaN and junk alike
            raise ValueError(f"set_grid: size must be a positive number of px, got {p['size']!r}")
        b["grid"] = int(size) if size.is_integer() else size
    if p.get("on") is not None:
        b["grid_on"] = _norm_bool("set_grid", "on", p["on"])
    if p.get("style") is not None:
        style = str(p["style"]).strip().lower()
        if style not in GRID_STYLES:
            raise ValueError(f"set_grid: style '{p['style']}' is unknown; valid styles: "
                             f"{', '.join(GRID_STYLES)}")
        b["grid_style"] = style
    if p.get("snap") is not None:
        b["snap"] = _norm_bool("set_grid", "snap", p["snap"])
    return f"grid: {grid_phrase(b)}"


def _op_layout(b, p):
    """Auto-place nodes by layers: {'op':'layout','rows':[['a'],['b','c']],'labels':[...]}."""
    bk = boardkit()
    rows = p.get("rows")
    if not rows or not isinstance(rows, list):
        raise ValueError("layout: 'rows' must be a list of node-id lists")
    rows = [[r] if isinstance(r, str) else list(r) for r in rows]
    ids = {n.get("id") for n in b.get("nodes", [])}
    for row in rows:
        for nid in row:
            if nid not in ids:
                raise ValueError(f"layout: no node '{nid}'")
    positions, groups, width, height = bk.layer_layout(
        rows, direction=p.get("direction", "down"), gap_node=p.get("gap_node", 40),
        gap_layer=p.get("gap_layer", 90), w=p.get("w", 190), h=p.get("h", 72),
        labels=p.get("labels"))
    for node in b.get("nodes", []):
        if node["id"] in positions:
            node["x"], node["y"] = positions[node["id"]]
    # Layer bands are regenerated on every layout; hand-made groups are left alone.
    b["groups"] = [g for g in b.get("groups", [])
                   if not str(g.get("id", "")).startswith("layer-")]
    b["groups"].extend(groups)
    b["width"] = max(float(b.get("width") or 0), width)
    b["height"] = max(float(b.get("height") or 0), height)
    return (f"laid out {sum(len(r) for r in rows)} nodes in {len(rows)} layer(s), "
            f"canvas {width:g}x{height:g}")


def _op_graph(b, p):
    """Place the whole graph automatically: {'op':'graph','direction':'right','bands':true}.

    Ranks come from the edges (`boardkit.auto_layout`: longest-path ranking, cycles broken, then
    barycentre ordering to cut crossings) — no hand-written `rows`, and each edge gets a route
    that suits its rank relation.
    """
    bk = boardkit()
    label = "labels" if isinstance(p.get("labels"), list) else None
    info = bk.apply_auto_layout(b, direction=p.get("direction", "down"),
                                gap_node=p.get("gap_node", 40), gap_layer=p.get("gap_layer", 90),
                                bands=bool(p.get("bands")), labels=p.get(label) if label else None)
    return (f"auto layout: {len(info['layers'])} rank(s), {info['crossings']} crossing(s), "
            f"{len(info['positions'])} node(s) placed")


def _op_bus(b, p):
    """Connect two bands through one shared trunk: {'sources':[ids],'targets':[ids]}."""
    bk = boardkit()
    sources, targets = p.get("sources"), p.get("targets")
    if not sources or not targets:
        raise ValueError("bus: 'sources' and 'targets' must be non-empty node-id lists")
    rects = {n["id"]: n for n in b.get("nodes", [])}
    for nid in list(sources) + list(targets):
        if nid not in rects:
            raise ValueError(f"bus: no node '{nid}'")
    new = bk.bus_edges(rects, sources, targets, direction=p.get("direction", "v"),
                       trunk=p.get("trunk"), label=p.get("label"), tone=p.get("tone", "default"),
                       style=p.get("style", "solid"), label_at=p.get("label_at"),
                       bidir=bool(p.get("bidir")))
    have = {(e.get("from"), e.get("to")) for e in b.get("edges", [])}
    added = [e for e in new if (e["from"], e["to"]) not in have]
    _drop_null_heads(added)
    b.setdefault("edges", []).extend(added)
    return (f"bus {len(sources)}x{len(targets)}: added {len(added)} edge(s), "
            f"{len(new) - len(added)} already present")


# --------------------------------------------------------------------------- rendering


def render_html(board: dict, *, interactive: bool = True, bridge: bool = False,
                subtitle: str | None = None) -> str:
    return boardkit().page(board, interactive=interactive, bridge=bridge, subtitle=subtitle)


def render_to_file(board_id: str, kind: str = "html", out_path: str | None = None,
                   board: dict | None = None) -> str:
    """Render a board to disk (html always; png when a headless Chrome is available)."""
    bk = boardkit()
    board = board or load_board(board_id)
    os.makedirs(RENDER_DIR, exist_ok=True)
    kind = (kind or "html").lower()
    if kind == "html":
        target = out_path or os.path.join(RENDER_DIR, f"{board_id}.html")
        return bk.report(target, board, subtitle=f"board {board_id}")
    if kind == "png":
        html_path = os.path.join(RENDER_DIR, f"{board_id}.html")
        bk.report(html_path, board, subtitle=f"board {board_id}")
        target = out_path or os.path.join(RENDER_DIR, f"{board_id}.png")
        # The width is the board's canvas (as before); the HEIGHT is measured in the browser
        # instead of taken from the canvas — a canvas-sized window cut off the bottom of a board
        # whose content reached low (the page adds the title, the toolbar and its own padding).
        width = int(min(max(float(board.get("width") or 0) or bk.bounds(board)[0], 640), 2400))
        return bk.render_png(html_path, target, width=width)
    raise ValueError(f"unsupported format '{kind}' (use html or png)")


# --------------------------------------------------------------------------- edit watch


def _dragged(before: list, after: list) -> bool:
    """Z7.3: was a whole path picked up and moved, rather than re-shaped?

    A line that was dragged in the pane has the same point count and every point shifted by the
    same delta (within the same 0.5 the box `moved` check uses) — that is the `moved` line of the
    diff, next to the `edited` one (a re-shaped path, or any of the other fields).
    """
    if not before or len(before) != len(after):
        return False
    dx, dy = after[0][0] - before[0][0], after[0][1] - before[0][1]
    if abs(dx) <= 0.5 and abs(dy) <= 0.5:
        return False
    return all(abs((now[0] - was[0]) - dx) <= 0.5 and abs((now[1] - was[1]) - dy) <= 0.5
               for was, now in zip(before, after))


def _name_list(ids, limit: int = 3) -> str:
    ids = [str(i) for i in ids]
    shown = ", ".join(ids[:limit])
    return shown + (f" +{len(ids) - limit}" if len(ids) > limit else "")


def diff_boards(old: dict, new: dict) -> dict:
    """What changed between two board versions — as data, plus one plain sentence.

    The pane saves on every drag; a diff is what makes those writes worth telling the agent
    about, and what keeps an identical re-save from waking anyone.
    """
    old_nodes = {str(n.get("id")): n for n in old.get("nodes", [])}
    new_nodes = {str(n.get("id")): n for n in new.get("nodes", [])}
    added = [i for i in new_nodes if i not in old_nodes]
    removed = [i for i in old_nodes if i not in new_nodes]
    moved, edited = [], []
    for key, node in new_nodes.items():
        before = old_nodes.get(key)
        if not before:
            continue
        if abs(float(before.get("x", 0)) - float(node.get("x", 0))) > 0.5 or \
                abs(float(before.get("y", 0)) - float(node.get("y", 0))) > 0.5:
            moved.append(key)
        if _label_of(before) != _label_of(node):
            edited.append(key)
    # v1.2 fields (R1/R4/R5/R6): compared as the renderers will read them, so writing a default
    # out in full (`align: "center"`, `start: "none"`) is not mistaken for an edit.
    resplit, unsplit, parts_edited, split_dir, realigned = {}, [], [], {}, {}
    for key, node in new_nodes.items():
        before = old_nodes.get(key)
        if not before:
            continue
        was_parts, now_parts = parts_of(before), parts_of(node)
        if was_parts != now_parts:
            if not now_parts:
                unsplit.append(key)
            elif len(was_parts) != len(now_parts):
                resplit.setdefault(len(now_parts), []).append(key)
            else:
                parts_edited.append(key)
        elif now_parts and split_of(before) != split_of(node):
            split_dir.setdefault(split_of(node), []).append(key)
        was_align, now_align = align_of(before), align_of(node)
        if was_align != now_align:
            realigned.setdefault(now_align, []).append(key)
    # v1.4 content (X3/X4/X8): a table's own shape, and the node's own text size/weight — compared
    # through the effective reads (`table_view`, `text_size_of`, `bold_of`), so padding a cell list
    # or writing `header: false`/`text_size: "default"` out in full is not an edit.
    table_edits, new_table_lines, text_size_edits, bold_edits = [], [], [], {}
    for key, node in new_nodes.items():
        before = old_nodes.get(key)
        if not before:
            # Y6: a table arriving on the board is described by its own sentence, not only by the
            # generic "added N box(es)" line.
            if node.get("kind") == "table":
                new_table_lines.append(table_sentence(node))
            continue
        if node.get("kind") == "table" or before.get("kind") == "table":
            was_view, now_view = table_view(before), table_view(node)
            edit = {"id": key, "rows": [was_view[0], now_view[0]],
                    "cols": [was_view[1], now_view[1]],
                    "header": [was_view[3], now_view[3]],
                    "cells": was_view[2] != now_view[2]}
            if node.get("kind") == "table" and before.get("kind") == "table":
                # Y4.2/Y4.3/Y6: the widths, the row/column tones and each cell's own tone and
                # alignment, compared through the effective reads so writing a default out in full
                # is not an edit.
                was_widths, now_widths = col_widths_of(before), col_widths_of(node)
                if was_widths != now_widths:
                    edit["col_widths"] = [was_widths, now_widths]
                was_rows, now_rows = row_tones_of(before), row_tones_of(node)
                deltas = [[index, was_rows[index], now_rows[index]]
                          for index in range(min(len(was_rows), len(now_rows)))
                          if was_rows[index] != now_rows[index]]
                if deltas:
                    edit["row_tones"] = deltas
                was_cols, now_cols = col_tones_of(before), col_tones_of(node)
                deltas = [[index, was_cols[index], now_cols[index]]
                          for index in range(min(len(was_cols), len(now_cols)))
                          if was_cols[index] != now_cols[index]]
                if deltas:
                    edit["col_tones"] = deltas
                was_cells, now_cells = cells_of(before), cells_of(node)
                aligns, tones = [], []
                for index in range(min(len(was_cells), len(now_cells))):
                    row, col = divmod(index, cols_of(node))
                    if cell_align_of(was_cells[index]) != cell_align_of(now_cells[index]):
                        aligns.append([row, col, cell_align_of(was_cells[index]),
                                       cell_align_of(now_cells[index])])
                    if cell_tone_of(was_cells[index]) != cell_tone_of(now_cells[index]):
                        tones.append([row, col, cell_tone_of(was_cells[index]),
                                      cell_tone_of(now_cells[index])])
                if aligns:
                    edit["cell_aligns"] = aligns
                if tones:
                    edit["cell_tones"] = tones
            if was_view != now_view or len(edit) > 5:
                table_edits.append(edit)
        if node.get("kind") == "table":
            continue                    # X3.3: a table draws no text of its own, so these never apply
        was_size, now_size = text_size_of(before), text_size_of(node)
        if was_size != now_size:
            text_size_edits.append({"id": key, "text_size": [was_size, now_size]})
        was_bold, now_bold = bold_of(before), bold_of(node)
        if was_bold != now_bold:
            bold_edits[key] = [was_bold, now_bold]
    old_edges = {(e.get("from"), e.get("to")): e for e in old.get("edges", [])}
    new_edges = {(e.get("from"), e.get("to")): e for e in new.get("edges", [])}
    edges_added = [f"{a}→{b}" for (a, b) in new_edges if (a, b) not in old_edges]
    edges_removed = [f"{a}→{b}" for (a, b) in old_edges if (a, b) not in new_edges]
    head_edits, side_edits, size_edits, pos_edits = [], [], [], []
    for key, edge in new_edges.items():
        before = old_edges.get(key)
        if not before:
            continue
        was_start, was_end = heads_of(before)
        now_start, now_end = heads_of(edge)
        if (was_start, was_end) != (now_start, now_end):
            head_edits.append({
                "edge": f"{key[0]}→{key[1]}",
                "start": [was_start, now_start] if was_start != now_start else None,
                "end": [was_end, now_end] if was_end != now_end else None,
            })
        # v1.3 (W3.1/W4): the attach sides and the head size, compared as the renderers read them
        # — `from_side: "auto"` and `head_size: "medium"` mean the default, so writing them out in
        # full is not an edit (same rule as the heads above).
        was_sides = (side_of(before), side_of(before, "to_side"))
        now_sides = (side_of(edge), side_of(edge, "to_side"))
        if was_sides != now_sides:
            side_edits.append({"edge": f"{key[0]}→{key[1]}",
                               "from_side": [was_sides[0], now_sides[0]],
                               "to_side": [was_sides[1], now_sides[1]],
                               "attaches": list(now_sides)})
        if head_size_of(before) != head_size_of(edge):
            size_edits.append({"edge": f"{key[0]}→{key[1]}",
                               "head_size": [head_size_of(before), head_size_of(edge)]})
        # v1.7 (A3): the position along the side, compared through the same lenient read — a
        # spelled-out 0.5 and an absent key both read as the midpoint, so neither is an edit,
        # while a moved position IS reported as an edit of that end.
        was_at = (at_of(before, "from_at"), at_of(before, "to_at"))
        now_at = (at_of(edge, "from_at"), at_of(edge, "to_at"))
        if was_at != now_at:
            pos_edits.append({"edge": f"{key[0]}→{key[1]}",
                              "from_at": [was_at[0], now_at[0]],
                              "to_at": [was_at[1], now_at[1]]})
    # v1.6 lines (Z7.3): the same treatment for the free paths — added / removed / moved (a line
    # picked up and dragged: every point shifted by ONE delta) / edited (points, tone, style,
    # heads, anchors). The anchors are compared as the READER resolves them against each board, so
    # an anchor that names nothing reads free, and spelling `{"ref": r, "side": "auto"}` out in full
    # is not an edit.
    old_lines = {str(line.get("id")): line for line in old.get("lines", [])}
    new_lines = {str(line.get("id")): line for line in new.get("lines", [])}
    lines_added = [key for key in new_lines if key not in old_lines]
    lines_removed = [key for key in old_lines if key not in new_lines]
    lines_moved, line_edits = [], []
    for key, line in new_lines.items():
        before = old_lines.get(key)
        if not before:
            continue
        was_points, now_points = points_of(before), points_of(line)
        if _dragged(was_points, now_points):
            lines_moved.append(key)
        edit = {}
        if was_points != now_points and key not in lines_moved:
            edit["points"] = [len(was_points), len(now_points)]
        was_tone, now_tone = line_tone_of(before), line_tone_of(line)
        if was_tone != now_tone:
            edit["tone"] = [was_tone, now_tone]
        was_style, now_style = line_style_of(before), line_style_of(line)
        if was_style != now_style:
            edit["style"] = [was_style, now_style]
        was_heads, now_heads = line_heads_of(before), line_heads_of(line)
        if was_heads != now_heads:
            edit["heads"] = [list(was_heads), list(now_heads)]
        was_anchors, now_anchors = line_anchors(before, old), line_anchors(line, new)
        if was_anchors != now_anchors:
            edit["anchors"] = [[list(a) if a else None for a in was_anchors],
                               [list(a) if a else None for a in now_anchors]]
        if edit:
            line_edits.append({"id": key, **edit})
    old_grid, new_grid = grid_state(old), grid_state(new)
    grid_changed = (old_grid["on"], old_grid["size"], old_grid["style"]) != \
        (new_grid["on"], new_grid["size"], new_grid["style"])
    snap_changed = old_grid["snap"] != new_grid["snap"]
    old_notes, new_notes = {str(n.get("id")): n for n in old.get("notes", [])}, \
        {str(n.get("id")): n for n in new.get("notes", [])}
    notes_added = [i for i in new_notes if i not in old_notes]
    notes_removed = [i for i in old_notes if i not in new_notes]
    notes_edited = [i for i in new_notes if i in old_notes
                    and old_notes[i].get("text") != new_notes[i].get("text")]
    groups_added = len([g for g in new.get("groups", [])
                        if g.get("id") not in {x.get("id") for x in old.get("groups", [])}])
    groups_removed = len([g for g in old.get("groups", [])
                          if g.get("id") not in {x.get("id") for x in new.get("groups", [])}])
    title = (old.get("title"), new.get("title")) if old.get("title") != new.get("title") else None

    parts = []
    if added:
        parts.append(f"added {len(added)} box(es) ({_name_list(added)})")
    if removed:
        parts.append(f"deleted {len(removed)} box(es) ({_name_list(removed)})")
    if moved:
        parts.append(f"moved {len(moved)} box(es) ({_name_list(moved)})")
    if edited:
        parts.append(f"edited the label of {len(edited)} box(es) ({_name_list(edited)})")
    for line in new_table_lines:
        parts.append(line)
    for count, ids in resplit.items():
        parts.append(f"split into {count} part{'s' if count != 1 else ''} ({_name_list(ids)})")
    if unsplit:
        parts.append(f"removed the split ({_name_list(unsplit)})")
    if parts_edited:
        parts.append(f"rewrote the parts of {len(parts_edited)} box(es) "
                     f"({_name_list(parts_edited)})")
    for direction, ids in split_dir.items():
        parts.append(f"split {direction} ({_name_list(ids)})")
    for align, ids in realigned.items():
        parts.append(f"align {align} ({_name_list(ids)})")
    for edit in table_edits:
        if edit["cells"]:
            parts.append(f"table {edit['rows'][1]}x{edit['cols'][1]} cells changed ({edit['id']})")
        if edit["header"][0] != edit["header"][1]:
            parts.append(f"table header {'on' if edit['header'][1] else 'off'} ({edit['id']})")
        # Y6/v1.5: the table v2 fields, one line each.
        if "col_widths" in edit:
            parts.append("column widths " +
                         ":".join(str(_number(width)) for width in edit["col_widths"][1]))
        for index, _was, now in edit.get("row_tones", []):
            parts.append(f"row {index + 1} tone {now or 'none'}")
        for index, _was, now in edit.get("col_tones", []):
            parts.append(f"column {index + 1} tone {now or 'none'}")
        for row, col, _was, now in edit.get("cell_aligns", []):
            parts.append(f"cell {row + 1}/{col + 1} align {now or 'none'}")
        for row, col, _was, now in edit.get("cell_tones", []):
            parts.append(f"cell {row + 1}/{col + 1} tone {now or 'none'}")
    for edit in text_size_edits:
        parts.append(f"text size {edit['text_size'][1]} ({edit['id']})")
    for key, value in bold_edits.items():
        parts.append(f"bold {'on' if value[1] else 'off'} ({key})")
    if edges_added:
        parts.append(f"connected {_name_list(edges_added)}")
    if edges_removed:
        parts.append(f"removed the link {_name_list(edges_removed)}")
    for edit in head_edits:
        if edit["start"] and edit["end"]:
            parts.append(f"heads {edit['start'][0]} -> {edit['start'][1]}, "
                         f"{edit['end'][0]} -> {edit['end'][1]} ({edit['edge']})")
        else:
            was, now = edit["start"] or edit["end"]
            parts.append(f"head {was} -> {now} ({edit['edge']})")
    for edit in side_edits:
        parts.append(f"edge {edit['edge']} attaches {edit['attaches'][0]}→{edit['attaches'][1]}")
    for edit in size_edits:
        parts.append(f"edge {edit['edge']} head size {edit['head_size'][1]}")
    for edit in pos_edits:
        # v1.7 (A3): one phrase per end that moved along its side.
        for end, field in (("from", "from_at"), ("to", "to_at")):
            was, now = edit[field]
            if was != now:
                parts.append(f"edge {edit['edge']} {end} position "
                             f"{_number(was)} -> {_number(now)}")
    # v1.6 lines (Z7.3): `drew 1 line (3 points)` / `deleted 1 line`, then the re-shaping edits.
    if lines_added:
        counts = ", ".join(f"{len(points_of(new_lines[key]))} points" for key in lines_added)
        parts.append(f"drew {len(lines_added)} line{'s' if len(lines_added) != 1 else ''} "
                     f"({counts})")
    if lines_removed:
        parts.append(f"deleted {len(lines_removed)} "
                     f"line{'s' if len(lines_removed) != 1 else ''}")
    if lines_moved:
        parts.append(f"moved {len(lines_moved)} line{'s' if len(lines_moved) != 1 else ''} "
                     f"({_name_list(lines_moved)})")
    for edit in line_edits:
        line_name = edit["id"]
        if "points" in edit:
            was_count, now_count = edit["points"]
            if was_count != now_count:
                parts.append(f"line points {was_count} -> {now_count} ({line_name})")
            else:
                parts.append(f"line points changed ({line_name})")
        if "tone" in edit:
            parts.append(f"line tone {edit['tone'][1]} ({line_name})")
        if "style" in edit:
            parts.append(f"line style {edit['style'][1]} ({line_name})")
        if "heads" in edit:
            (was_start, was_end), (now_start, now_end) = edit["heads"]
            if was_start != now_start and was_end != now_end:
                parts.append(f"line heads {was_start} -> {now_start}, "
                             f"{was_end} -> {now_end} ({line_name})")
            else:
                was, now = (was_start, now_start) if was_start != now_start \
                    else (was_end, now_end)
                parts.append(f"line head {was} -> {now} ({line_name})")
        if "anchors" in edit:
            parts.append(f"line anchors {_anchor_phrase(edit['anchors'][0])} -> "
                         f"{_anchor_phrase(edit['anchors'][1])} ({line_name})")
    if notes_added:
        parts.append(f"added {len(notes_added)} note(s)")
    if notes_removed:
        parts.append(f"deleted {len(notes_removed)} note(s)")
    if notes_edited:
        parts.append(f"rewrote {len(notes_edited)} note(s)")
    if groups_added or groups_removed:
        parts.append(f"changed bands (+{groups_added}/-{groups_removed})")
    if title:
        parts.append(f"retitled the board to '{new.get('title')}'")
    if grid_changed or snap_changed:
        line = grid_line(new)
        if snap_changed:
            line += f", snap {'on' if new_grid['snap'] else 'off'}"
        parts.append(line)
    return {
        "changed": bool(parts),
        "nodes": {"added": added, "removed": removed, "moved": moved, "edited": edited,
                  "split": [i for ids in resplit.values() for i in ids], "unsplit": unsplit,
                  "parts_edited": parts_edited, "split_dir": split_dir, "realigned": realigned,
                  "table": table_edits, "text_size": text_size_edits, "bold": bold_edits},
        "edges": {"added": edges_added, "removed": edges_removed, "heads": head_edits,
                  "sides": side_edits, "head_size": size_edits, "positions": pos_edits},
        "lines": {"added": lines_added, "removed": lines_removed, "moved": lines_moved,
                  "edited": line_edits},
        "notes": {"added": notes_added, "removed": notes_removed, "edited": notes_edited},
        "groups": {"added": groups_added, "removed": groups_removed},
        "tables_added": new_table_lines,
        "title": title,
        "grid": {"changed": grid_changed, "snap": snap_changed, "state": new_grid},
        "summary": ", ".join(parts),
    }


def _watch_load() -> dict:
    if os.path.isfile(WATCH_FILE):
        try:
            with open(WATCH_FILE, encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                data.setdefault("events", [])
                data.setdefault("acked", 0)
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"events": [], "acked": 0, "target": None}


def _watch_save(state: dict) -> None:
    os.makedirs(os.path.dirname(WATCH_FILE), exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=os.path.dirname(WATCH_FILE), suffix=".tmp")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(state, stream, ensure_ascii=False, indent=1)
    os.replace(tmp, WATCH_FILE)


def write_board(board_id: str, board: dict, *, origin: str = "api", notify: bool = False,
                wake_message: bool = True):
    """The one write path: save, diff against the previous version, log the edit, maybe wake.

    Returns `(version, event)`. `event` is None for a write that changed nothing (the pane saves
    on every drag frame, and an identical board must not spam the log or the agent).
    """
    previous = None
    try:
        previous = load_board(board_id)
    except FileNotFoundError:
        pass
    version = save_board(board_id, board)
    if previous is not None and version == version_of(previous):
        return version, None

    if previous is None:
        changes = {"changed": True, "summary": "created the board",
                   "nodes": {"added": list({str(n.get("id")) for n in board.get("nodes", [])})},
                   "edges": {"added": []}, "notes": {}, "groups": {}, "title": None}
    else:
        changes = diff_boards(previous, board)
        if not changes["changed"]:
            return version, None

    event = {"at": _now_iso(), "board": board_id, "origin": origin, "version": version,
             "summary": changes["summary"], "changes": changes, "acked": False}
    state = _watch_load()
    events = state["events"] + [event]
    if len(events) > EVENT_CAP:                      # drop the oldest, keep the ack cursor sane
        dropped = len(events) - EVENT_CAP
        events = events[dropped:]
        state["acked"] = max(0, int(state.get("acked", 0)) - dropped)
    state["events"] = events
    _watch_save(state)

    if notify and wake_message and origin in ("pane", "api", "watch"):
        wake = _runtime().inject_message(
            f"[board {board_id}] edited by the user: {changes['summary']}. "
            f"Call board_get to see it (board_render for a picture).",
            target=state.get("target"))
        event["wake"] = {"ok": wake.get("ok", False), "detail": wake.get("detail", ""),
                         "sent": wake.get("sent", {})}
        state["events"][-1] = event
        _watch_save(state)
    return version, event


def read_events(board_id: str | None = None, *, limit: int = 20, ack: bool = False,
                include_acked: bool = False) -> dict:
    """Pending board edits, newest last. `ack=True` marks everything returned as read."""
    state = _watch_load()
    events = state["events"]
    if not include_acked:
        events = [event for event in events if not event.get("acked")]
    if board_id:
        events = [event for event in events if event.get("board") == board_id]
    selected = events[-limit:] if limit else events
    if ack and selected:
        stamp = _now_iso()
        for event in state["events"]:
            if event in selected:
                event["acked"] = True
                event["acked_at"] = stamp
        _watch_save(state)
    return {"events": selected, "pending": len(events), "total": len(state["events"]),
            "board": board_id, "acked": bool(ack)}


def acknowledge(board_id: str | None = None) -> int:
    """Mark every pending event (optionally for one board) as read; returns how many."""
    state = _watch_load()
    count = 0
    for event in state["events"]:
        if event.get("acked"):
            continue
        if board_id and event.get("board") != board_id:
            continue
        event["acked"] = True
        event["acked_at"] = _now_iso()
        count += 1
    if count:
        _watch_save(state)
    return count


def _state_db_candidates() -> list[str]:
    from_env = os.environ.get("HERMES_STATE_DB")
    home = os.environ.get("HERMES_HOME") or "~/.hermes"
    candidates = [from_env] if from_env else []
    candidates += [os.path.join(os.path.expanduser(home), "state.db"),
                   os.path.expanduser("~/.hermes/state.db")]
    return [c for c in dict.fromkeys(candidates) if c and os.path.isfile(c)]


def resolve_session_key(session_id: str | None) -> str | None:
    """Translate an agent session id into the durable session key the host routes on.

    A tool handler is handed the agent's ``session_id`` (``20261011_002818_ce2f5d82``), but
    ``PluginContext.inject_message`` routes on the durable *session key*
    (``agent:main:telegram:dm:379020925:203268``) — the two are not interchangeable, and the
    gateway looks the key up in this profile's session store. That mapping is in ``state.db``, so
    read it (read-only) instead of guessing or sending a key that can never resolve.
    """
    if not session_id:
        return None
    for path in _state_db_candidates():
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
                row = con.execute(
                    "select session_key from sessions where id = ? and session_key is not null "
                    "order by coalesce(last_activity_at, started_at) desc limit 1",
                    (str(session_id),)).fetchone()
        except sqlite3.Error:
            continue
        if row and row[0]:
            return str(row[0])
    return None


def arm_watch(target: dict, *, note: str | None = None) -> dict:
    """Record where a board edit should wake: the session that armed it."""
    state = _watch_load()
    state["target"] = dict(target or {})
    state["target_at"] = _now_iso()
    state["target_note"] = note or ""
    _watch_save(state)
    _runtime().arm(target)
    return {"target": state["target"], "note": state["target_note"], "at": state["target_at"]}


def disarm_watch() -> dict:
    state = _watch_load()
    previous = state.get("target")
    state["target"] = None
    _watch_save(state)
    _runtime().arm({})
    return {"target": previous, "disarmed": True}


def watch_status() -> dict:
    state = _watch_load()
    pending = [e for e in state["events"] if not e.get("acked")]
    runtime = _runtime().status()
    return {"armed": state.get("target"), "armed_at": state.get("target_at"),
            "note": state.get("target_note", ""), "pending": len(pending),
            "last_event": (state["events"][-1] if state["events"] else None),
            "ctx_bound": runtime.get("ctx_bound"), "last_wake": runtime.get("last_wake")}


def _now_iso() -> str:
    import datetime as dt
    return dt.datetime.now().isoformat(timespec="seconds")
