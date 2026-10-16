#!/usr/bin/env python3
"""The two renders of one board must agree, and the editor must still pass its own self-test.

    $PY tests/test_editor.py            # ~/.hermes/installs/*/environments/*/venv/bin/python

`vendor/boardkit.py` and `vendor/board_editor.js` are two hand-written renderers of ONE document
schema (a board has to look the same in a chat artifact, in a PNG and in the desktop pane). The
schema is frozen in `docs/SPEC-v1.2-primitives.md`; this suite is the only check that the two sides
actually produce the same picture after a change to either of them.

How it works: the fixture `docs/fixture-v1.2.json` is rendered by the Python renderer, and rendered
again by the real editor in headless Chrome (the page's `#ck-board-host` after the editor booted),
then the two element trees are compared element by element, attribute by attribute, with every
numeric token rounded to 3 decimals (the two languages format floats differently — that is not a
mismatch). `<defs>` children are compared as a set keyed by `id` (marker order is not part of the
picture); everything else is compared in order.

The self-test half then loads the same page with `#selftest` and asserts the editor's own report is
`ok` and that it exercised the v1.2 features (shapes, parts, heads, grid, align).
"""

from __future__ import annotations

# (html is used by the self-test report decoding above)
import json
import os
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="hermes-board-editor-")
os.environ["HERMES_BOARD_DIR"] = os.path.join(TMP, "boards")
os.environ["HERMES_BOARD_RENDER_DIR"] = os.path.join(TMP, "render")

sys.path.insert(0, str(ROOT / "vendor"))
import boardkit  # noqa: E402  (the bundled copy, resolved exactly like store.py does)

FAILURES: list[str] = []
FIXTURE = ROOT / "docs" / "fixture-v1.2.json"
V13 = ROOT / "docs" / "fixture-v1.3.json"
V14 = ROOT / "docs" / "fixture-v1.4.json"
V15 = ROOT / "docs" / "fixture-v1.5.json"
V16 = ROOT / "docs" / "fixture-v1.6.json"
V17 = ROOT / "docs" / "fixture-v1.7.json"


def check(name: str, condition, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


# --------------------------------------------------------------------------- normalising

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def norm_token(value: str) -> str:
    """Round every number inside a string to 3 decimals: 8.640000000000001 == 8.64."""
    return _NUM.sub(lambda m: f"{round(float(m.group(0)), 3):g}", value)


def shape(element: ET.Element):
    """(tag, sorted attrs, children) — attribute order ignored, element order kept."""
    attrs = sorted((key, norm_token(value)) for key, value in element.attrib.items()
                   if key != "style")            # `style` is a JS-only cursor hint
    kids = [shape(kid) for kid in element]
    text = (element.text or "").strip()
    return (element.tag, tuple(attrs), tuple(kids), text)


def walk(element: ET.Element, out: list) -> None:      # kept for ad-hoc debugging
    """Flatten one level of children, keeping document order, skipping empty text nodes."""
    for kid in element:
        out.append(shape(kid))


def compare(py_svg: str, js_svg: str) -> list[str]:
    problems: list[str] = []
    try:
        py_root = ET.fromstring(py_svg)
        js_root = ET.fromstring(js_svg)
    except ET.ParseError as exc:
        return [f"one of the two renders is not parseable XML: {exc}"]
    if norm_token(py_root.get("viewBox", "")) != norm_token(js_root.get("viewBox", "")):
        problems.append(f"viewBox differs: {py_root.get('viewBox')} vs {js_root.get('viewBox')}")
    for attr in ("class", "role", "aria-label", "preserveAspectRatio"):
        if py_root.get(attr) != js_root.get(attr):
            problems.append(f"<svg {attr}> differs: {py_root.get(attr)!r} vs {js_root.get(attr)!r}")

    py_kids, js_kids = list(py_root), list(js_root)
    for index, kid in enumerate(py_kids):
        if kid.tag.endswith("defs"):
            py_defs = {item.get("id") or "": shape(item) for item in kid}
            js_defs = {}
            for other in js_kids:
                if other.tag.endswith("defs"):
                    js_defs = {item.get("id") or "": shape(item) for item in other}
            missing = sorted(set(py_defs) - set(js_defs))
            extra = sorted(set(js_defs) - set(py_defs))
            if missing:
                problems.append(f"<defs>: Python defines {missing}, the editor does not")
            if extra:
                problems.append(f"<defs>: the editor defines {extra}, Python does not")
            for key in sorted(set(py_defs) & set(js_defs)):
                if py_defs[key] != js_defs[key]:
                    problems.append(f"<defs> #{key} differs:\n    py {py_defs[key]}\n    js {js_defs[key]}")
    # top-level drawing order (grid rect, groups, areas, edges, nodes, notes)
    order = lambda kids: [k.tag + (k.get("class") or "") for k in kids if not k.tag.endswith("defs")]
    if order(py_kids) != order(js_kids):
        problems.append("draw order differs:\n    py " + ", ".join(order(py_kids)) +
                        "\n    js " + ", ".join(order(js_kids)))

    py_items, js_items = [], []
    for kid in py_root:
        if not kid.tag.endswith("defs"):
            py_items.append(shape(kid))
    for kid in js_root:
        if not kid.tag.endswith("defs"):
            js_items.append(shape(kid))
    if len(py_items) != len(js_items):
        problems.append(f"element count differs: {len(py_items)} vs {len(js_items)}")
    for index, (left, right) in enumerate(zip(py_items, js_items)):
        if left != right:
            name = left[0] + " " + str(dict(left[1]).get("data-id", "")) + \
                " " + str(dict(left[1]).get("class", ""))
            problems.append(f"element #{index} ({name}) differs:\n    py {left}\n    js {right}")
    return problems


# --------------------------------------------------------------------------- chrome helpers

def dump_dom(page: str) -> str:
    """The whole serialised DOM after the editor booted (Chrome `--dump-dom`)."""
    binary = boardkit.find_chrome()
    if not binary:
        raise RuntimeError("no Chrome/Chromium found")
    profile = tempfile.mkdtemp(prefix="chrome-")
    try:
        return subprocess.run([binary, "--headless=new", "--disable-gpu", "--no-sandbox",
                               f"--user-data-dir={profile}", "--virtual-time-budget=8000",
                               "--dump-dom", "file://" + os.path.abspath(page)],
                              check=True, capture_output=True, text=True, timeout=300).stdout
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def dump_host(page: str, extra: str = "") -> str:
    """Load `page` in headless Chrome and return the serialised `#ck-board-host` content.

    Fails loudly when the editor did not take the host over: a JS exception leaves the
    server-side SVG in place, and comparing that with the Python render compares Python with
    itself — a check that cannot fail. `style="cursor:grab"` is emitted by the editor only.
    """
    binary = boardkit.find_chrome()
    if not binary:
        raise RuntimeError("no Chrome/Chromium found")
    profile = tempfile.mkdtemp(prefix="chrome-")
    try:
        dom = subprocess.run([binary, "--headless=new", "--disable-gpu", "--no-sandbox",
                              f"--user-data-dir={profile}", "--virtual-time-budget=8000",
                              "--dump-dom", "file://" + os.path.abspath(page) + extra],
                             check=True, capture_output=True, text=True, timeout=300).stdout
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    marker = 'id="ck-board-host">'
    if marker not in dom:
        raise AssertionError("no #ck-board-host in the rendered page")
    tail = dom.split(marker, 1)[1]
    end = tail.find("</svg>")
    if end < 0:
        raise AssertionError("#ck-board-host has no </svg>")
    # NOT unescaped on purpose: the dumped DOM is serialised HTML, so entities are already
    # escaped the way XML expects them (unescaping turns `R&amp;D` into a parse error).
    host = tail[:end + len("</svg>")]
    if 'style="cursor:grab"' not in host:
        raise AssertionError("the editor did not render into #ck-board-host (a JS error left the "
                             "server-side SVG in place) — the comparison would be vacuous")
    return host


def probe_board() -> dict:
    """The round-1 disposition probes: every way a reader can meet a head or a long caption."""
    part = lambda nid, x, y, kind, text, **kw: ({"id": nid, "x": x, "y": y, "w": 120, "h": 110,
                                               "text": text, "kind": kind, "tone": "default"}
                                              | kw)
    return {
        "title": "dispositions", "width": 700, "height": 500, "grid": 20, "grid_on": True,
        "grid_style": "lines",
        # Every list the editor's render() walks must be here: a missing `groups` makes the JS
        # render throw BEFORE it replaces the server-side SVG, and the comparison below would then
        # compare Python with Python and pass no matter what the editor does.
        "groups": [],
        "nodes": [
            {"id": "pill0", "x": 20, "y": 20, "w": 140, "h": 60, "text": "parts cleared",
             "kind": "pill", "parts": [], "tone": "default"},
            part("srv", 200, 20, "server", "a very long server label that will not fit"),
            part("pc", 340, 20, "computer", "operator"),
            part("act", 480, 20, "actor", "a long actor label"),
            part("zone", 20, 160, "area", "a very long area title that will not fit either"),
            part("b", 400, 160, "box", "B"), part("c", 400, 260, "box", "C"),
            part("d", 400, 360, "box", "D"),
            {"id": "blank", "x": 20, "y": 300, "w": 160, "h": 90, "text": "", "kind": "box",
             "tone": "default", "parts": ["a", "   ", "b"], "split": "rows"},
        ],
        "edges": [
            {"from": "b", "to": "c"},                                    # no heads at all
            {"from": "c", "to": "d", "start": None, "end": None},         # explicit nulls
            {"from": "d", "to": "b", "start": "circle", "end": "bogus"},  # unknown end
            {"from": "b", "to": "d", "start": "none", "end": "none"},      # both ends bare
            {"from": "c", "to": "b", "bidir": True},                      # legacy flag
            {"from": "d", "to": "c", "start": "squiggle"},                 # unknown start
            {"from": "b", "to": "c", "start": "squiggle", "bidir": True},   # unknown start + bidir
        ],
        "notes": [],
    }


def disposition_checks() -> None:
    """R4/R6.1: the rules the round-1 gap pass forced into the spec, asserted on the Python side."""
    board = probe_board()
    svg = boardkit.render_svg(board)
    markers = re.findall(r'<g class="ck-edge"[^>]*>(.*?)</g>', svg, re.S)
    heads = [[h for h in re.findall(r'marker-(start|end)="url\(#(ck-head-[^)]+)\)"', seg)] for seg in markers]
    check("a legacy bidir edge still draws an arrow at both ends",
          len(heads[4]) == 2 and all("arrow" in h[1] for h in heads[4]), str(heads[4]))
    check("null heads fall back to the defaults, never to a `None` marker",
          all("None" not in h[1] for seg in heads for h in seg) and
          [h[0] for h in heads[1]] == ["end"], str(heads[1]))
    check("an unknown end falls back to arrow while a good start survives",
          sorted(h[0] for h in heads[2]) == ["end", "start"] and
          "arrow" in dict((h[0], h[1]) for h in heads[2])["end"] and
          "circle" in dict((h[0], h[1]) for h in heads[2])["start"], str(heads[2]))
    check("both ends explicitly `none` draws no head at all", heads[3] == [], str(heads[3]))

    caption = re.findall(r'<text x="60" y="104"[^>]*>([^<]*)</text>', svg)
    check("an icon caption is ONE wrapped line (R2)",
          caption and caption[0] == "a very long", str(caption))
    title = re.findall(r'<text x="12" y="17"[^>]*>([^<]*)</text>', svg)
    check("an area title is the first wrapped line at w - 24, font 11",
          title and title[0] == "a very long", str(title))   # 120 wide: limit = floor(96/6.6) = 14
    check("a pill with no parts left is still a pill (R4)",
          f'<rect x="0" y="0" width="140" height="60" rx="30"' in svg)
    check("an unknown start is no head: the bidir rule decides instead (R6.1)",
          heads[5] == [("end", "ck-head-default-arrow")] and
          sorted(h[0] for h in heads[6]) == ["end", "start"] and
          all("arrow" in h[1] for h in heads[6]), f"{heads[5]} {heads[6]}")
    blank = re.search(r'<g class="ck-node" data-id="blank".*?</g>', svg, re.S)
    check("a part that is only blanks is dropped, on both sides (R4)",
          blank is not None and len(re.findall(r"<path ", blank.group(0))) == 1,
          blank.group(0) if blank else "no blank group")


def v13_checks() -> None:
    """W3: attach sides and head sizes — the parts the two-renderer comparison cannot see."""
    board = boardkit.load(str(V13))
    svg = boardkit.render_svg(board)

    def points_for(from_id, to_id):
        seg = re.search(r'<g class="ck-edge"[^>]*data-from="%s"[^>]*data-to="%s".*?</g>'
                        % (from_id, to_id), svg, re.S)
        return re.findall(r"(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", seg.group(0)) if seg else []

    # node a: 60,60 150x80 -> top mid (135,60); node b: 380,60 150x80 -> bottom mid (455,140).
    # `auto` would have picked right/left for this pair, so this one discriminates.
    tb = points_for("a", "b")
    check("from_side top / to_side bottom anchor at those midpoints (W3.2)",
          tb and tb[0] == ("135.0", "60.0") and tb[-1] == ("455.0", "140.0"), str(tb[:3]))
    check("that pair is not what `auto` would have chosen (the check discriminates)",
          tb and tb[0] != ("210.0", "100.0"), str(tb[:2]))
    # node b: 380,60 150x80 -> bottom mid (455,140); node c: 700,60 140x110 -> top mid (770,60)
    bt = points_for("b", "c")
    check("bottom/top sides anchor at those midpoints (W3.2)",
          bt and bt[0] == ("455.0", "140.0") and bt[-1] == ("770.0", "60.0"), str(bt[:3]))
    auto = points_for("d", "e")
    check("an auto end still uses the position-based rule (W3.2)",
          auto and auto[0] == ("380.0", "355.0"), str(auto[:2]))
    check("a waypoint path still starts at the explicit side (W3.2)",
          (wp := points_for("e", "f")) and wp[0] == ("210.0", "340.0"), str(wp[:2]))

    ids = re.findall(r"marker-end=\"url\(#([^)]+)\)\"", svg)
    check("head_size large/small suffix the marker id (W3.3)",
          "ck-head-default-open-large" in ids and "ck-head-default-diamond-small" in ids, str(sorted(set(ids))))
    check("an explicit medium keeps the v1.2 marker id (W3.3)",
          "ck-head-default-triangle" in ids and "ck-head-default-triangle-medium" not in ids,
          str(sorted(set(ids))))
    check("an unknown size falls back to medium's id (W3.1)",
          "ck-head-default-arrow" in ids and not [i for i in ids if i.endswith("-huge")], str(sorted(set(ids))))
    unknown = dict(board)
    unknown["edges"] = [dict(e) for e in board["edges"]]
    for e in unknown["edges"]:
        if e.get("head_size") == "huge" or e.get("from_side") == "middle":
            e.pop("head_size", None)
            e.pop("from_side", None)
    check("an unknown side/size renders exactly like an absent one (W3.1)",
          boardkit.render_svg(unknown) == svg)

    sized = re.search(r'<marker id="ck-head-default-open-large"[^>]*>', svg)
    check("the head size scales markerWidth only; the anchor stays (10,5) (W3.3, v1.2 identity)",
          sized is not None and 'refX="10"' in sized.group(0) and 'refY="5"' in sized.group(0)
          and 'markerWidth="16"' in sized.group(0), sized.group(0) if sized else "no large marker")
    plain_markers = re.findall(r"<marker [^>]*>", boardkit.render_svg(
        boardkit.load(str(FIXTURE))))
    check("every v1.2 marker still carries refX=10/refY=5/11px (a v1.2 document is unchanged)",
          plain_markers and all('refX="10"' in m and 'refY="5"' in m and 'markerWidth="11"' in m
                                for m in plain_markers), f"{len(plain_markers)} markers")

    legacy = {"id": "x", "x": 0, "y": 0, "w": 100, "h": 50, "text": "x", "kind": "box", "tone": "default"}
    other = {"id": "y", "x": 300, "y": 0, "w": 100, "h": 50, "text": "y", "kind": "box", "tone": "default"}
    plain = boardkit.render_svg({"nodes": [legacy, other], "edges": [{"from": "x", "to": "y"}]})
    spelled = boardkit.render_svg({"nodes": [legacy, other], "edges": [
        {"from": "x", "to": "y", "from_side": "auto", "to_side": "auto", "head_size": "medium"}]})
    check("spelling out the defaults changes nothing (W3.1)", plain == spelled)


#: Every capability that must be reachable from the pane's own controls, not only from the API or
#: an agent op: (control, where). A feature that ships without one of these is invisible to the user.
CONTROLS = {
    # v1.6: `connect` is gone (Z1.6/Z4.5) — the line tool replaces it; `data-act="fit"` is the
    # `#ck-zoom` readout, the single fit control (Z1.1).
    "toolbar": ("data-act=\"add\"", "data-act=\"addnote\"",
                "data-act=\"grid\"", "data-act=\"zoom-in\"", "data-act=\"zoom-out\"",
                "data-act=\"fit\""),
    "panel": ("ck-text", "ck-sub", "ck-tone", "ck-kind", "ck-align", "ck-parts", "ck-split",
              "ck-start", "ck-end", "ck-from-side", "ck-to-side", "ck-head-size",
              # v1.4: the split count control, the table's rows/cols/cells/header,
              # text size/bold, and the width/height readouts the resize handles need
              "ck-parts-count", "ck-rows", "ck-cols", "ck-cells", "ck-header",
              "ck-text-size", "ck-bold", "ck-w", "ck-h"),
    "readout": ("ck-zoom",),
    # v1.4: buttons that live in the PANEL (arrange) rather than the toolbar
    "panelact": ("data-act=\"align-left\"", "data-act=\"align-right\"",
                 "data-act=\"align-top\"", "data-act=\"align-bottom\"",
                 "data-act=\"spread-h\"", "data-act=\"spread-v\"",
                 "data-act=\"part-add\"", "data-act=\"part-del\""),
}


def control_surface_checks() -> None:
    """Every new capability needs a control in the pane (and a live handler for it).

    Writes the page it inspects first, so the toolbar half can be read from the DOM the browser
    actually built instead of from the inlined script text.

    The user-facing half of a feature is the toolbar/panel: a shape the picker does not offer, or a
    select the editor never binds, is a feature that only exists in the API. This walks the generated
    page and the inlined editor and pairs them, so a future change cannot ship one without the other.
    """
    page_path = os.path.join(TMP, "controls.html")
    with open(page_path, "w", encoding="utf-8") as handle:
        handle.write(boardkit.page(boardkit.load(str(FIXTURE)), bridge=True))
    with open(page_path, encoding="utf-8") as handle:
        page = handle.read()
    editor = (ROOT / "vendor" / "board_editor.js").read_text(encoding="utf-8")
    # The toolbar half is read from the RENDERED dom, not from the page source: the page inlines the
    # editor script, so a string that only lives inside that script would satisfy a naive substring
    # check while the user sees no button at all.
    if boardkit.find_chrome():
        dom = dump_dom(page_path)
        bar = re.search(r'class="ck-bar"[^>]*>(.*?)</div>', dom, re.S)
        toolbar_html = bar.group(1) if bar else ""
        check("the rendered toolbar exists", bool(toolbar_html), dom[:160])
        for needle in CONTROLS["toolbar"]:
            check(f"the rendered toolbar offers {needle} exactly once",
                  toolbar_html.count(needle) == 1, f"occurrences: {toolbar_html.count(needle)}")
        actions = set(re.findall(r'data-act="([a-z-]+)"', toolbar_html))
        panel = re.search(r'id="ck-panel"[^>]*>(.*?)</div>\s*</div>', dom, re.S)
        panel_html = ""
        for cid in CONTROLS["panel"]:
            hit = re.search(r'id="%s"[^>]*' % cid, dom)
            if hit:
                panel_html += hit.group(0) + "\n"
            else:
                check(f"the panel offers #{cid}", False, "not in the rendered DOM")
        for needle in CONTROLS["panel"]:
            check(f"the rendered panel offers {needle}", ('id="%s"' % needle) in dom)
        for needle in CONTROLS["panelact"]:
            check(f"the rendered panel offers {needle}",
                  needle in dom, f"occurrences: {dom.count(needle)}")
            actions.add(needle.split('"')[1])
    else:
        for needle in CONTROLS["toolbar"]:
            check(f"the toolbar offers {needle} (no browser for the rendered DOM)",
                  needle in page, "install Chrome to prove it is rendered")
        actions = set(re.findall(r'data-act="([a-z-]+)"', page))
    unwired = sorted(a for a in actions if f'act === "{a}"' not in editor)
    check("every toolbar button has a handler", not unwired, str(unwired))
    for cid in CONTROLS["panel"]:
        check(f"the panel offers #{cid}", f'id="{cid}"' in page)
        check(f"#{cid} is wired", f'bind("{cid}"' in editor or f'getElementById("{cid}")' in editor)
    for cid in CONTROLS["readout"]:
        check(f"the toolbar shows #{cid}", f'id="{cid}"' in page)
        check(f"#{cid} is updated", f'getElementById("{cid}")' in editor)
    # Y2/Y8 (v1.5 gap finding 12): Y2 REPLACES these selects with button groups, so parsing <option>
    # lists would fail a correct implementation. Count the buttons and their pressed state instead.
    check("the shape picker is a button group, not a dropdown (Y2)",
          'id="ck-kind"' in page and not re.search(r'<select id="ck-kind"', page))
    missing_kinds = [k for k in boardkit.KINDS if ('data-kind="%s"' % k) not in page]
    check("the shape picker offers every kind the renderer knows (shapes are reachable in the pane)",
          not missing_kinds, str(missing_kinds))
    check("the head picker is a button group, not a dropdown (Y2)",
          'id="ck-end"' in page and not re.search(r'<select id="ck-end"', page))
    missing_heads = [h for h in boardkit.HEADS if ('data-end="%s"' % h) not in page]
    check("the head picker offers every head", not missing_heads, str(missing_heads))


def v14_checks() -> None:
    """X3/X4: the table and text-formatting rules the two-renderer comparison cannot see."""
    svg = boardkit.render_svg(boardkit.load(str(V14)))

    def group_of(node_id):
        hit = re.search(r'<g class="ck-node" data-id="%s".*?</g>' % node_id, svg, re.S)
        return hit.group(0) if hit else ""

    # the table's own text is not drawn; cells are (row-major, empty cell = no text element)
    tbl = group_of("tbl")
    check("a table draws its cells, not its own text (X3.2)",
          tbl and "name" in tbl and "value" in tbl and "38%" in tbl)
    paths = len(re.findall(r"<path ", tbl))
    check("a 3x2 header table draws two interior boundaries and one vertical (Y4.6)",
          paths == 3, f"{paths} divider paths")
    # 3 rows x 2 cols with a header (Y4.6): row 0 IS the header band (28), the two body rows are 24
    # each, so h = 28 + 48 = 76 and the interior boundaries are at 28 and 52.
    horiz = re.findall(r'<path d="M (\d+),(\d+) H (\d+)"', tbl)
    vert = re.findall(r'<path d="M (\d+),(\d+) V (\d+)"', tbl)
    check("the interior boundaries sit where the rows end, header row included (Y4.6)",
          sorted(int(y) for _, y, _ in horiz) == [28, 52] and
          [int(x) for x, _, _ in vert] == [90], f"h={horiz} v={vert}")
    check("the header's own cells are drawn inside the top band, not under an empty one (Y4.6)",
          re.search(r'<text[^>]*y="19(\.\d+)?"[^>]*font-weight="700"', tbl) is not None and
          'rx="9"' in tbl,
          "no bold cell text at y = 19 (row 0's own band)")
    check("the cell text uses the renderer's own constants, not numbers retyped in the spec (Y4.6)",
          'font-size="12.5"' in tbl)

    # X3.3/F1: the table's stored w/h are 0, so EVERY reader must resolve the effective size — an
    # edge anchored to its bottom side has to start at the effective bottom midpoint, not at 60,60.
    mini = {"nodes": [{"id": "t", "x": 60, "y": 60, "w": 0, "h": 0, "text": "", "kind": "table",
                       "tone": "default", "rows": 3, "cols": 2, "header": True,
                       "cells": ["a", "b", "c", "d", "e", "f"]},
                      {"id": "b", "x": 460, "y": 260, "w": 150, "h": 80, "text": "b",
                       "kind": "box", "tone": "default"}],
            "edges": [{"from": "t", "to": "b", "from_side": "bottom", "to_side": "top"}]}
    mini_svg = boardkit.render_svg(mini)
    seg = re.search(r'<g class="ck-edge".*?</g>', mini_svg, re.S)
    pts = re.findall(r"(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", seg.group(0)) if seg else []
    check("an edge anchored to a zero-sized table uses its effective box (X3.3)",
          pts and pts[0] == ("150.0", "136.0"), f"start {pts[:1]} (expected 150,136)")

    # F5: bold must be an override — icons draw their caption at weight 400 today
    icon = {"nodes": [{"id": "s", "x": 0, "y": 0, "w": 120, "h": 110, "text": "cap",
                       "kind": "server", "tone": "default"}]}
    icon_bold = {"nodes": [dict(icon["nodes"][0])]} 
    icon_bold["nodes"][0]["bold"] = True
    check("bold overrides an icon caption's 400 (X4.3)",
          'font-weight="700"' in boardkit.render_svg(icon_bold) and
          'font-weight="400"' in boardkit.render_svg(icon),
          "icon caption weights")
    cap = {"nodes": [{"id": "c", "x": 0, "y": 0, "w": 160, "h": 40, "text": "caption text",
                      "kind": "caption", "tone": "default", "bold": True}]}
    check("bold adds the attribute where a path emits none (X4.3)",
          'font-weight="700"' in boardkit.render_svg(cap))
    check("an empty cell emits no text (X3.2)",
          tbl.count("<text") == 5, f"{tbl.count('<text')} text elements")
    check("the header row is bold (X3.1)", 'font-weight="700"' in tbl and 'font-weight="600"' in tbl)
    tbl2 = group_of("tbl2")
    check("a table without rows/cols is 2x2 (X3.1)", tbl2 and len(re.findall(r"<path ", tbl2)) == 2,
          f"{len(re.findall(r'<path ', tbl2))} dividers")

    # text size: small 10, large 17, default == absent (byte-identical)
    check("text_size small/large set the font the wrap uses (X4.2)",
          'font-size="10"' in group_of("small") and 'font-size="17"' in group_of("big"),
          group_of("small")[:120])
    check("bold emits font-weight 700 (X4.3)", 'font-weight="700"' in group_of("big"))
    check("a large title on an area and a small icon caption both follow text_size (X4.1)",
          'font-size="17"' in group_of("zone") and 'font-size="10"' in group_of("srv"))

    node = {"id": "x", "x": 0, "y": 0, "w": 300, "h": 60, "text": "same words here",
            "kind": "box", "tone": "default"}
    plain = boardkit.render_svg({"nodes": [dict(node)]})
    spelled = boardkit.render_svg({"nodes": [dict(node, text_size="default", bold=False)]})
    check("spelling out the text defaults changes nothing (X4.2/X4.3)", plain == spelled)
    unknown = boardkit.render_svg({"nodes": [dict(node, text_size="huge")]})
    check("an unknown text_size falls back to default (X4.4)", unknown == plain)

    table = {"id": "t", "x": 0, "y": 0, "w": 0, "h": 0, "text": "", "kind": "table",
             "tone": "default", "rows": 2, "cols": 2, "cells": ["a", "b", "c", "d", "extra"]}
    auto = boardkit.render_svg({"nodes": [table]})
    check("cells beyond rows*cols are dropped (X3.1)",
          auto.count("<text") == 4 and "<g" in auto, f"{auto.count('<text')} cells rendered")


def v15_checks() -> None:
    """Y4/Y2/Y3: the table v2 rules and the panel control surface, checked in the page and the SVG."""
    board = boardkit.load(str(V15))
    svg = boardkit.render_svg(board)

    def group_of(node_id):
        hit = re.search(r'<g class="ck-node" data-id="%s".*?</g>' % node_id, svg, re.S)
        return hit.group(0) if hit else ""

    t2 = group_of("t2")
    # 3x2, header, col_widths [2, 1] on w = 180 -> one vertical divider at 120, not at 90 (Y4.2);
    # h = 28 + 24*2 = 76 (Y4.6) so the interior boundaries are at 28 and 52
    vert = re.findall(r'<path d="M (\d+(?:\.\d+)?),0 V (\d+(?:\.\d+)?)"', t2)
    horiz = re.findall(r'<path d="M 0,(\d+(?:\.\d+)?) H (\d+(?:\.\d+)?)"', t2)
    check("col widths move the vertical divider to the column's share (Y4.2)",
          [float(x) for x, _ in vert] == [120.0], f"verticals at {vert}")
    check("the interior boundaries are where the rows end (Y4.6)",
          [float(y) for y, _ in horiz] == [28.0, 52.0], f"horizontals at {horiz}")
    check("a table with a per-cell colour drops its rounded corners (Y4.4)",
          'rx="0"' in t2 and 'rx="9"' in group_of("t3"))
    # Y4.4/Y8 (v1.5 gap finding 10): a multiset of tone names cannot falsify a swapped precedence —
    # locate every painted overlay on its own cell box. The base rect is NOT a tone fill (it keeps the
    # table's tone stroke), so exactly the four non-default cells appear here.
    painted = []
    for tag in re.findall(r"<rect\b[^>]*>", t2):
        tone = re.search(r'fill="var\(--ck-tone-([a-z]+)-fill\)"', tag)
        attrs = {a: re.search(r'\b%s="([\d.]+)"' % a, tag) for a in ("x", "y", "width", "height")}
        if tone and all(attrs.values()):
            painted.append(tuple(float(attrs[a].group(1)) for a in ("x", "y", "width", "height"))
                           + (tone.group(1),))
    want = [(0.0, 28.0, 120.0, 24.0, "muted"), (120.0, 0.0, 60.0, 28.0, "accent"),
            (120.0, 28.0, 60.0, 24.0, "muted"), (120.0, 52.0, 60.0, 24.0, "warn")]
    # Y4.4 (v1.5 gap finding 10): the base rect IS a tone fill — of the table's OWN tone — so the
    # overlays are the painted rects that do not cover the whole box, and each is located by geometry.
    base = [p for p in painted if p[:4] == (0.0, 0.0, 180.0, 76.0)]
    overlays = [p for p in painted if p not in base]
    check("the base rect is painted in the table's own tone, over the whole box (Y4.4)",
          base == [(0.0, 0.0, 180.0, 76.0, "default")], f"base {base}")
    check("each painted cell sits on its own box with the tone the precedence picks (Y4.3/Y4.4)",
          sorted(overlays) == sorted(want), f"painted {sorted(overlays)}")
    right = re.search(r'<text x="([\d.]+)"[^>]*text-anchor="end"[^>]*>38%</text>', t2)
    check("a right-aligned cell anchors 6 units from its own right edge (Y4.5)",
          right is not None and abs(float(right.group(1)) - 174.0) < 0.01,
          f"x={right.group(1) if right else 'missing'}")
    check("an object cell with an empty text draws nothing (Y4.1)",
          not re.search(r'<text[^>]*>\s*</text>', t2))
    # Y8 (v1.5 gap finding 13): the fixture's empty cell is a plain STRING, so that check alone is
    # vacuous. Probe a real {text:""} object that is also painted.
    empty_probe = boardkit.render_svg(boardkit.board(nodes=[{
        "id": "eo", "x": 0, "y": 0, "w": 0, "h": 0, "text": "", "kind": "table", "tone": "default",
        "rows": 1, "cols": 2, "cells": ["a", {"text": "", "tone": "warn"}]}]))
    check("a {text:\"\"} object cell draws no text at all, and is still painted (Y4.1)",
          not re.search(r'<text[^>]*>\s*</text>', empty_probe)
          and re.search(r'<text[^>]*>a</text>', empty_probe) is not None
          and "var(--ck-tone-warn-fill)" in empty_probe, empty_probe[:0])

    check("a v1.4-shaped table keeps its rounded corners and its cell (Y4, back-compat)",
          'rx="9"' in group_of("t1") and "solo" in group_of("t1"))

    base = {"id": "b", "x": 0, "y": 0, "w": 0, "h": 0, "text": "", "kind": "table", "tone": "default",
            "rows": 2, "cols": 2, "cells": ["a", "b", "c", "d"]}
    spelled = dict(base, col_widths=[1, 1], row_tones=[None, None], col_tones=[None, None])
    check("spelling out the table defaults changes nothing (Y4.2/Y4.3)",
          boardkit.render_svg({"nodes": [dict(base)]}) == boardkit.render_svg({"nodes": [spelled]}))

    page = boardkit.page(boardkit.load(str(V15)))
    for kind in boardkit.KINDS:
        check(f"the shape picker has a glyph button for {kind}", 'data-kind="%s"' % kind in page)
    for tone in boardkit.TONES:
        check(f"the tone row has a swatch for {tone}", 'data-tone="%s"' % tone in page)
    for section in ("content", "shape", "layout", "parts", "table", "edge", "arrange", "cell"):
        check(f"the panel has a collapsible {section} section", 'data-section="%s"' % section in page)
    check("the tone/kind/align/split/size controls are button groups, not dropdowns (Y2)",
          not re.search(r'<select id="ck-(tone|kind|align|split|text-size|head-size)"', page))
    check("the panel's groups carry the ids the old selects had (Y2)",
          all(('id="%s"' % i) in page for i in ("ck-tone", "ck-kind", "ck-align", "ck-split",
                                                "ck-text-size", "ck-head-size")))
    for control in ("ck-w", "ck-h", "ck-rows", "ck-cols", "ck-cells", "ck-parts", "ck-cell-text"):
        check(f"#{control} has a hint line (Y3)", 'data-hint-for="%s"' % control in page)
    for control in ("ck-cell-row", "ck-cell-col", "ck-cell-text", "ck-colw-0"):
        check(f"the table's cell controls are present: {control}", 'id="%s"' % control in page)
    # Y5/Y8 (v1.5 gap finding 14): an anchored step="0.5" makes a number input reject its own default
    # of 1, so the pane would refuse a value it wrote itself.
    check("no number field pins a step that rejects its own default (Y5)",
          'step="0.5"' not in page)


def editor_self_consistency() -> None:
    """A merge that adds a second definition of the same function is silent in JS: declarations
    hoist and the LAST one wins, so the earlier (often the renderer's) body is discarded with no
    error. Two lanes independently needing a `tableGrid` did exactly this."""
    js = (ROOT / "vendor" / "board_editor.js").read_text()
    names = re.findall(r"^\s{2}function\s+([A-Za-z_$][\w$]*)\s*\(", js, re.M)
    dupes = sorted({n for n in names if names.count(n) > 1})
    check("the editor defines every function once (a duplicate silently overrides by hoisting)",
          not dupes, f"duplicated: {dupes}")
    py = (ROOT / "vendor" / "boardkit.py").read_text()
    pynames = re.findall(r"^def\s+([A-Za-z_]\w*)\s*\(", py, re.M)
    pydupes = sorted({n for n in pynames if pynames.count(n) > 1})
    check("the renderer defines every function once", not pydupes, f"duplicated: {pydupes}")
    check("the renderer and the editor agree on how many top-level functions they define",
          # v1.6: the bound is proportional, not absolute. The editor legitimately carries a whole
          # interaction half with no Python twin (the tool machine, hit testing, vertex and waypoint
          # dragging, the draft preview), so the gap grows by design: 116 vs 65 at v1.5, 134 vs 69 at
          # v1.6 — a bare `< 60` failed a correct merge. The check still catches a half that has been
          # emptied (or a lane's helpers vanishing from one side).
          len(pynames) >= 60 and len(names) <= len(pynames) * 2.5,
          f"js={len(names)} py={len(pynames)}")


def probe_chrome(page: str, script: str) -> dict:
    """Run `script` inside the loaded `page` and decode the JSON it leaves in `<pre id="ck-probe">`.

    `dump_host` answers from the SERIALISED DOM, which cannot report a computed style: the malformed
    JS clip (v1.5 verifier round 2) still serialised an attribute, it just was not a clip-path. A
    missing `#ck-probe` is a FAILURE, never an empty result — a JS error would otherwise read as
    "the assertion quietly did not run".
    """
    binary = boardkit.find_chrome()
    if not binary:
        raise RuntimeError("no Chrome/Chromium found")
    profile = tempfile.mkdtemp(prefix="chrome-")
    path = page + ".probe.html"
    with open(page, encoding="utf-8") as handle:
        html = handle.read()
    injected = ('<script>window.addEventListener("load", function () { setTimeout(function () {'
                "var q = (function () {" + script + "})();"
                'var pre = document.createElement("pre"); pre.id = "ck-probe";'
                "pre.textContent = JSON.stringify(q); document.body.appendChild(pre); }, 400); });"
                "</script></body>")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(html.replace("</body>", injected))
    try:
        dom = subprocess.run([binary, "--headless=new", "--disable-gpu", "--no-sandbox",
                              f"--user-data-dir={profile}", "--virtual-time-budget=8000", "--dump-dom",
                              "file://" + os.path.abspath(path)],
                             check=True, capture_output=True, text=True, timeout=300).stdout
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    match = re.search(r'<pre id="ck-probe">(.*?)</pre>', dom, re.S)
    if not match:
        raise AssertionError("the Chrome probe did not report (a JS error leaves #ck-probe out)")
    return json.loads(match.group(1).replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">"))


def width_probe() -> dict:
    """The round-2/round-3 reader/clip probe: imported bad widths and one legal extreme share.

    ONE board, because the Chrome half renders it once: `inf` carries the string a JSON import can
    hold, `big` a finite pair whose SUM overflows, `digit` a 310-digit JSON integer (a real double
    cannot hold it at all — Python's `float()` raises `OverflowError` and the editor's `JSON.parse`
    reads the same literal as `Infinity`), `narrow` a legal extreme share that needs the clip, and
    `plain` a v1.4-shaped table that must stay free of clip bytes.
    """
    cells = ["long label", "other", "", ""]
    return {
        "title": "readers", "width": 700, "height": 760, "grid": 20, "grid_on": False,
        "groups": [], "edges": [], "notes": [],
        "nodes": [
            {"id": "inf", "x": 40, "y": 40, "w": 180, "h": 48, "text": "", "kind": "table",
             "tone": "default", "rows": 2, "cols": 2, "col_widths": ["Infinity", 1],
             "cells": list(cells)},
            {"id": "big", "x": 260, "y": 40, "w": 180, "h": 48, "text": "", "kind": "table",
             "tone": "default", "rows": 2, "cols": 2, "col_widths": [1e308, 1e308],
             "cells": list(cells)},
            {"id": "digit", "x": 40, "y": 160, "w": 180, "h": 48, "text": "", "kind": "table",
             "tone": "default", "rows": 2, "cols": 2, "col_widths": [10 ** 309, 1],
             "cells": list(cells)},
            {"id": "narrow", "x": 40, "y": 280, "w": 180, "h": 48, "text": "", "kind": "table",
             "tone": "default", "rows": 2, "cols": 2, "col_widths": [0.1, 100],
             "cells": list(cells)},
            {"id": "plain", "x": 260, "y": 280, "w": 180, "h": 48, "text": "", "kind": "table",
             "tone": "default", "rows": 2, "cols": 2, "cells": ["a", "b", "c", "d"]},
        ],
    }


def group_in(svg: str, node_id: str) -> str:
    hit = re.search(r'<g class="ck-node" data-id="%s".*?</g>' % node_id, svg, re.S)
    return hit.group(0) if hit else ""


def width_checks() -> None:
    """v1.5 verifier round 2, findings 1 and 6: the renderers' OWN readers, and the JS clip.

    `store.py`'s writer is a different layer and is gated in `tests/test_tools.py`; what failed here
    was the reader both renderers use, plus a clip-path the JS concatenated inside an open quote
    (Chrome computed "none" and the text drew into the next column).
    """
    board = width_probe()
    svg = boardkit.render_svg(board)
    for node_id, what in (("inf", 'an imported "Infinity" column share'),
                          ("big", "a finite [1e308, 1e308] pair"),
                          ("digit", "an imported 310-digit JSON integer (float() raises OverflowError)")):
        body = group_in(svg, node_id)
        check(f"Y4.2/Y4.1a: {what} reads as a 1 share — divider at 90, texts at 45/135",
              'd="M 90,0 V 48"' in body and '<text x="45"' in body and '<text x="135"' in body
              and 'd="M 0,0 V 48"' not in body and "nan" not in body.lower(),
              body[:220] or f"no node {node_id} in the render")
    narrow = group_in(svg, "narrow")
    plain = group_in(svg, "plain")
    check("Y4.2/Y4.5: the extreme share still clips its cell text on the Python side",
          '<clipPath id="tclip-narrow-0">' in narrow
          and 'clip-path="url(#tclip-narrow-0)"' in narrow, narrow[:220])
    check("Y4.2: the v1.4-shaped table beside it carries no clip bytes at all",
          plain and "clipPath" not in plain and "clip-path" not in plain, plain[:220])

    if not boardkit.find_chrome():
        print("  skip the Chrome half of the width probe — no Chrome/Chromium on PATH")
        return
    page = os.path.join(TMP, "widths.html")
    boardkit.report(page, board, bridge=True)
    host = dump_host(page)
    problems = compare(svg, host)
    check("python and the editor agree on the bad-width board, clip included", not problems,
          "\n      ".join(problems[:8]))
    for node_id, what in (("inf", 'an imported "Infinity" column share'),
                          ("big", "a finite [1e308, 1e308] pair"),
                          ("digit", "an imported 310-digit JSON integer")):
        body = group_in(host, node_id)
        check(f"Y4.2/Y4.1a (JS): {what} reads as a 1 share in the editor too",
              'd="M 90,0 V 48"' in body and 'x="45"' in body and 'x="135"' in body
              and "NaN" not in body and 'd="M 0,0 V 48"' not in body, body[:220])
    check("Y4.2/Y4.5 (JS): the editor's DOM carries the clip on the narrow cell's text",
          'clip-path="url(#tclip-narrow-0)"' in group_in(host, "narrow"),
          group_in(host, "narrow")[:260])
    probe = probe_chrome(page, """
        var g = document.querySelector('.ck-node[data-id="narrow"]');
        var t = g ? g.querySelector("text") : null;
        var r = g ? g.querySelector("clipPath rect") : null;
        var b = t ? t.getBBox() : null;
        var div = g ? g.querySelector('path[d*=" V "]') : null;
        return { clip: t ? getComputedStyle(t).clipPath : null,
                 attr: t ? t.getAttribute("clip-path") : null,
                 weight: t ? t.getAttribute("font-weight") : null,
                 rect: r ? [parseFloat(r.getAttribute("x")), parseFloat(r.getAttribute("width"))] : null,
                 bbox: b ? [b.x, b.x + b.width] : null,
                 divider: div ? div.getAttribute("d") : null };
    """)
    rect = probe.get("rect") or [None, None]
    bbox = probe.get("bbox") or [None, None]
    check("Y4.2/Y4.5 (JS): Chrome COMPUTES the clip on the narrow cell's text (a malformed "
          "attribute computed to `none` and the text spilled)",
          bool(probe.get("clip")) and "tclip-narrow-0" in str(probe.get("clip"))
          and probe.get("attr") == "url(#tclip-narrow-0)" and probe.get("weight") == "600",
          json.dumps(probe))
    check("Y4.2/Y4.5 (JS): ...and that clip is the CELL's own box, not the table's",
          rect[0] == 0 and rect[1] is not None and 0 < rect[1] < 1, json.dumps(probe))
    check("Y4.2/Y4.5 (JS): the text's own geometry really does stick out of that box, so the clip "
          "is what keeps it in (the check is not vacuous)",
          bbox[0] is not None and bbox[0] < 0 and bbox[1] > rect[1], json.dumps(probe))
    check("Y4.2/Y4.5 (JS): the narrow cell's divider sits at its 0.18 share",
          str(probe.get("divider") or "").startswith("M 0.17982"), json.dumps(probe))


CHURN_SCRIPT = r"""
  /* v1.8 (R1-R5, S1-S4): what does a gesture do to the board's DOM?
   *
   * Real mouse input over CDP is the better proof, but this probe asks four questions that are
   * DOM facts and cannot be faked by the editor's own claims: does the <svg> element survive a
   * gesture, do the scene's elements survive it BY IDENTITY (a scene rebuild replaces every one of
   * them), does a patched gesture render exactly as a full render does, and what does a real
   * straight drag commit. The observer is read with takeRecords() because MutationObserver
   * callbacks are microtasks: a synchronous gesture would otherwise report nothing at all.
   */
  var host = document.getElementById('ck-board-host');
  var CHROME = ['ck-guides', 'ck-handles', 'ck-vtxs', 'ck-anchors', 'ck-draft', 'ck-marquee'];
  var out = { svgCreations: 0, strokeMutations: null, dragMutations: null, svgSame: null,
 strokeHostMutations: null, dragHostMutations: null, straightHostMutations: null,
 straightSvgSame: null, straightSceneKept: null, straightDraft: null,
 straightCancelKept: null, straightCancelDraft: null,
              strokeSceneKept: null, strokeDraft: null, dragSvgSame: null, dragSceneKept: null,
              dragMoved: null, driftEqual: null, straightButton: null, tools: null,
              step45: null, line45: null, vertical: null, lineVertical: null, shiftFree: null,
              lineShift: null, barRows: null, transformBefore: null, transformAfter: null };
  var obs = new MutationObserver(function () {});
  obs.observe(host, { childList: true, subtree: true });
  /* Z9's budget is about the HOST's own children — the board that must not be thrown away — so it is
   * counted separately: a chrome refresh inside the <svg> is deliberate and cheap, while a replaced
   * host child is the defect. subtree counts the former, so it is reported, not asserted. */
  var hostObs = new MutationObserver(function () {});
  hostObs.observe(host, { childList: true });
  function take() { return obs.takeRecords().length; }
  function takeHost() { return hostObs.takeRecords().length; }
  /* Numbers rounded to 3 decimals: the two halves format floats differently and that is not a
   * mismatch — the same tolerance the renderer parity comparison uses. */
  function norm(s) {
    return s.replace(/-?\d+\.?\d*/g, function (m) { return String(Math.round(parseFloat(m) * 1000) / 1000); });
  }
  function scene() {
    var svg = host.querySelector('svg.ck-board');
    if (!svg) return [];
    return Array.prototype.slice.call(svg.children).filter(function (el) {
      for (var i = 0; i < CHROME.length; i++) {
        if (el.classList && el.classList.contains(CHROME[i])) return false;
      }
      return true;
    });
  }
  /* The scene's own signature: every top-level scene child's markup, normalised. */
  function sceneSig() {
    return scene().map(function (el) { return norm(el.outerHTML); }).join('\u0001');
  }
  function pev(el, type, x, y, opts) {
    var o = { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0,
              buttons: type === 'pointerup' ? 0 : 1, pointerId: 1, pointerType: 'mouse',
              isPrimary: true };
    if (opts) { for (var k in opts) { o[k] = opts[k]; } }
    el.dispatchEvent(new PointerEvent(type, o));
  }
  /* A press on the canvas must not land on an item: the straight tool's own branch has to answer
   * first, and an empty spot keeps that question out of this probe. */
  function emptySpot(svg) {
    var r = svg.getBoundingClientRect();
    var boxes = [];
    Array.prototype.forEach.call(document.querySelectorAll('.ck-node, .ck-note, .ck-group'),
      function (el) { boxes.push(el.getBoundingClientRect()); });
    for (var ty = 0.12; ty < 0.85; ty += 0.07) {
      for (var tx = 0.06; tx < 0.9; tx += 0.07) {
        var x = r.left + r.width * tx, y = r.top + r.height * ty;
        var clear = boxes.every(function (b) { return x < b.left || x > b.right || y < b.top || y > b.bottom; });
        if (clear) return [x, y];
      }
    }
    return [r.left + 8, r.top + 8];
  }
  function polyOf(g) {
    var p = g && g.querySelector('polyline');
    if (!p) return null;
    var parts = (p.getAttribute('points') || '').trim().split(/\s+/);
    if (parts.length < 2) return null;
    var a = parts[0].split(',').map(Number), b = parts[parts.length - 1].split(',').map(Number);
    return { x0: a[0], y0: a[1], x1: b[0], y1: b[1] };
  }
  function lines() { return document.querySelectorAll('.ck-line'); }

  try {
    out.svgCreations = document.querySelectorAll('svg.ck-board').length;

    /* ---- 1. a 20-move stroke in line mode ---- */
    document.querySelector('[data-tool="line"]').click();
    var svgRef = host.querySelector('svg.ck-board');
    var before = scene();
    var r0 = svgRef.getBoundingClientRect();
    var sx = r0.left + r0.width * 0.12, sy = r0.top + r0.height * 0.2;
    pev(host, 'pointerdown', sx, sy);
    take();
    for (var i = 1; i <= 20; i++) { pev(host, 'pointermove', sx + i * 6, sy + i * 3); }
    out.strokeMutations = take();
    out.strokeHostMutations = takeHost();
    out.svgSame = host.querySelector('svg.ck-board') === svgRef;
    out.strokeSceneKept = before.length > 0 && before.every(function (el) { return el.isConnected; });
    out.strokeDraft = !!host.querySelector('.ck-draft');
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

    /* ---- 2. a 20-move node drag: identity DURING the gesture, then the drift check ---- */
    document.querySelector('[data-tool="select"]').click();
    var svgRef2 = host.querySelector('svg.ck-board');
    var before2 = scene();
    var node = document.querySelector('.ck-node[data-id]');
    var nb = node.getBoundingClientRect();
    var nx = nb.left + nb.width / 2, ny = nb.top + nb.height / 2;
    out.transformBefore = node.getAttribute('transform');
    pev(node, 'pointerdown', nx, ny);
    take();
    for (var j = 1; j <= 20; j++) { pev(host, 'pointermove', nx + j * 5, ny + j * 4); }
    out.dragMutations = take();
    out.dragHostMutations = takeHost();
    out.dragSvgSame = host.querySelector('svg.ck-board') === svgRef2;   // measured while it is LIVE
    out.dragSceneKept = before2.length > 0 && before2.every(function (el) { return el.isConnected; });
    /* R4 while the gesture is still live, so a rebuild at the drop cannot hide a patch that drifted.
     * The comparison is on the SCENE, and deliberately not on the chrome: the chrome is rebuilt from
     * the model on every draw by construction, so it cannot drift — it can only be one frame behind,
     * and a synchronous burst of pointer events leaves a requestAnimationFrame legitimately pending.
     * The scene is what the patch wrote by hand, so the scene is what must equal a full rebuild's.
     * Two grid toggles force two full scene rebuilds of the SAME model (grid_on returns to where it
     * was); numbers are rounded to 3 decimals, the tolerance the two renderers are compared with. */
    var during = sceneSig();
    document.querySelector('[data-act="grid"]').click();
    document.querySelector('[data-act="grid"]').click();
    out.driftEqual = sceneSig() === during;
    pev(host, 'pointerup', nx + 100, ny + 80);        // the drop is allowed to rebuild
    var live = document.querySelector('.ck-node[data-id]');
    out.transformAfter = live ? live.getAttribute('transform') : null;
    out.dragMoved = !!live && out.transformAfter !== out.transformBefore;

    /* ---- 3. the straight tool: control, then the 45-degree step through a real drag ---- */
    out.straightButton = document.querySelectorAll('.ck-bar [data-tool="straight"]').length === 1;
    out.tools = Array.prototype.map.call(document.querySelectorAll('[data-tool]'),
      function (b) { return b.getAttribute('data-tool') + ':' + b.getAttribute('aria-pressed'); }).join(' ');
    var bar = document.querySelector('.ck-bar');
    if (bar) {
      var tops = {};
      Array.prototype.forEach.call(bar.children, function (k) {
        tops[Math.round(k.getBoundingClientRect().top)] = 1;
      });
      out.barRows = Object.keys(tops).length;
    }
    var straightBtn = document.querySelector('[data-tool="straight"]');
    if (straightBtn) {
      /* F1 (verifier round 2, CRITICAL): the straight drag is a LIVE gesture too. Identity is measured
       * WHILE the draft is live — a drag that rebuilds the scene on every move passes every check that
       * only reads the model back after the release — and again after Esc cancels it: a stroke that is
       * thrown away must cost the scene nothing. */
      straightBtn.click();
      var svgS = host.querySelector('svg.ck-board');
      var sceneS = scene();
      var spotS = emptySpot(svgS);
      take(); takeHost();
      pev(svgS, 'pointerdown', spotS[0], spotS[1]);
      var straightLines0 = lines().length;
      for (var m = 1; m <= 20; m++) { pev(svgS, 'pointermove', spotS[0] + m * 6, spotS[1] + m * 4); }
      out.straightSvgSame = host.querySelector('svg.ck-board') === svgS;
      out.straightSceneKept = sceneS.length > 0 && sceneS.every(function (el) { return el.isConnected; });
      out.straightDraft = !!host.querySelector('.ck-draft');
      out.straightHostMutations = takeHost();
      out.straightDraftLive = lines().length === straightLines0;   // still a draft, nothing written
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      out.straightCancelKept = sceneS.every(function (el) { return el.isConnected; });
      out.straightCancelDraft = !host.querySelector('.ck-draft');
      out.straightCancelWrote = lines().length !== straightLines0;
      /* the mode stays armed after Esc (S2/S5): the three committed drags below ride on it */
      var svg3 = host.querySelector('svg.ck-board');
      var spot = emptySpot(svg3);
      function drawTo(dx, dy, opts) {
        var n0 = lines().length;
        pev(svg3, 'pointerdown', spot[0], spot[1]);
        pev(svg3, 'pointermove', spot[0] + dx, spot[1] + dy, opts);
        pev(svg3, 'pointerup', spot[0] + dx, spot[1] + dy, opts);
        var got = lines();
        if (got.length !== n0 + 1) return null;
        return polyOf(got[got.length - 1]);
      }
      out.line45 = drawTo(80, 70, null);            // ~41 degrees -> the 45-degree step
      out.step45 = !!out.line45 &&
        Math.abs(Math.abs(out.line45.y1 - out.line45.y0) - Math.abs(out.line45.x1 - out.line45.x0)) < 0.2;
      out.lineVertical = drawTo(10, 90, null);      // mostly vertical -> the vertical step
      out.vertical = !!out.lineVertical && Math.abs(out.lineVertical.x1 - out.lineVertical.x0) < 0.05;
      out.lineShift = drawTo(80, 20, { shiftKey: true });   // Shift -> the pointer's own angle
      out.shiftFree = !!out.lineShift &&
        Math.abs((out.lineShift.y1 - out.lineShift.y0) - 0.25 * (out.lineShift.x1 - out.lineShift.x0)) < 0.4 &&
        Math.abs(out.lineShift.x1 - out.lineShift.x0) > 20;
    }
  } catch (err) {
    out.error = String(err && err.message ? err.message : err);
  }
  return out;              /* the harness stringifies it once */
"""


def gesture_churn() -> dict:
    """Drive a stroke, a node drag and three straight drags on the real page and report the DOM facts.

    This is the v1.8 gate: `R1-R5` are about what a gesture does to the document the user is drawing
    on, so they are asserted by IDENTITY (a scene rebuild replaces every element) and by the drift
    check (a patched gesture must render exactly as a full render) — never by reading the code.
    `S4`'s 45° step is asserted from the committed line's own polyline, so a helper that exists but
    is not wired to the gesture cannot pass.
    """
    page = os.path.join(TMP, "churn.html")
    boardkit.report(page, boardkit.load(str(V17)), bridge=True)
    return probe_chrome(page, CHURN_SCRIPT)


def baseline_coverage() -> None:
    """F2 (verifier round 2, MAJOR): every SHIPPED fixture is pinned, in BOTH halves, by some baseline.

    The v1.7 wave's `baseline-v1.7.json` pins the six documents that existed before it and deliberately
    leaves out `fixture-v1.7.json` — the fixture that wave was born with. Fair, but it left that shipped
    document pinned by NOTHING, so a later shared change to its picture would pass every recorded-hash
    check. The coverage is therefore derived from the glob, never from a list someone has to remember to
    edit, and it is the UNION of every `docs/baseline-*.json` that has to cover it. The values themselves
    come from a baseline generated at the wave's base commit, and both halves must still reproduce them.
    """
    import hashlib

    pinned: dict = {}
    for path in sorted((ROOT / "docs").glob("baseline-*.json")):
        for name, ref in json.loads(path.read_text()).get("fixtures", {}).items():
            pinned.setdefault(name, {}).update(ref)

    fixtures = sorted(p.name for p in (ROOT / "docs").glob("fixture-*.json"))
    unpinned = [name for name in fixtures if not pinned.get(name, {}).get("python_sha256")]
    check("F2: every shipped fixture is pinned by SOME baseline (the union, derived from the glob)",
          not unpinned, f"unpinned: {unpinned}")

    if not boardkit.find_chrome():
        print("  skip the editor half — no Chrome/Chromium on PATH")
        return
    for name in fixtures:
        ref = pinned.get(name) or {}
        board = boardkit.load(str(ROOT / "docs" / name))
        py_digest = hashlib.sha256(boardkit.render_svg(board).encode("utf-8")).hexdigest()
        check(f"F2 byte-identity (python): {name}", py_digest == ref.get("python_sha256"),
              f"{py_digest[:16]} != {str(ref.get('python_sha256'))[:16]}")
        page_path = os.path.join(TMP, "pinned-" + name + ".html")
        boardkit.report(page_path, board, bridge=True)
        js_digest = hashlib.sha256(dump_host(page_path).encode("utf-8")).hexdigest()
        check(f"F2 byte-identity (editor): {name}", js_digest == ref.get("editor_sha256"),
              f"{js_digest[:16]} != {str(ref.get('editor_sha256'))[:16]}")


def main() -> int:
    missing = [str(f) for f in (FIXTURE, V13, V14, V15) if not f.is_file()]
    if missing:
        print("missing fixture(s): " + ", ".join(missing))
        return 2
    board = boardkit.load(str(FIXTURE))
    py_svg = boardkit.render_svg(board)

    print("python renderer")
    check("the fixture renders", py_svg.startswith("<svg") and "</svg>" in py_svg)
    check("every new kind emits its own element",
          all(needle in py_svg for needle in ("<ellipse", "<polygon")) and
          py_svg.count('class="ck-node"') == len(board["nodes"]), str(py_svg.count('class="ck-node"')))
    check("the grid is drawn when grid_on",
          'class="ck-grid"' in py_svg and 'id="ck-grid"' in py_svg)
    heads = set(re.findall(r'id="(ck-head-[a-z]+-[a-z]+)"', py_svg))
    check("one marker per tone and head type", len(heads) == 6 * 6, f"{len(heads)} markers")

    print("round-1 dispositions (the rules the gap pass forced into the spec)")
    disposition_checks()

    print("v1.3 edges: attach sides and head sizes")
    v13_checks()

    print("v1.4 content: tables and text formatting")
    v14_checks()

    print("v1.5: table v2 and the panel control surface")
    v15_checks()

    print("the two halves are self-consistent")
    editor_self_consistency()

    print("the pane's own controls (a feature with no control is only in the API)")
    control_surface_checks()

    if not boardkit.find_chrome():
        print("  skip the editor half — no Chrome/Chromium on PATH (the pane needs it anyway)")
        return 1 if FAILURES else 0

    print("the editor renders the same picture")
    page = os.path.join(TMP, "parity.html")
    boardkit.report(page, board, bridge=True)          # bridge => the editor never restores state
    host = dump_host(page)
    check("the editor booted and re-rendered the host (JS-only cursor style present)",
          "style=" in host and 'class="ck-node"' in host)
    problems = compare(py_svg, host)
    check("python and the editor agree on the whole picture", not problems,
          "\n      " + "\n      ".join(problems[:8]))

    for fixture, what in ((V13, "v1.3 (attach sides, head sizes)"),
                          (V14, "v1.4 (tables, text size/bold, split counts)"),
                          (V15, "v1.5 (table v2: widths, alignment, row/column/cell colour)"),
                          (V16, "v1.6 (the line primitive)"),
                          (V17, "v1.7 (anchor positions along a side)")):
        print(f"the editor renders the {fixture.name} identically too")
        page_path = os.path.join(TMP, fixture.stem + ".html")
        board = boardkit.load(str(fixture))
        boardkit.report(page_path, board, bridge=True)
        problems = compare(boardkit.render_svg(board), dump_host(page_path))
        check(f"python and the editor agree on {what}", not problems,
              "\n      " + "\n      ".join(problems[:8]))

    probe = probe_board()
    probe_page = os.path.join(TMP, "probe.html")
    boardkit.report(probe_page, probe, bridge=True)
    probe_problems = compare(boardkit.render_svg(probe), dump_host(probe_page))
    check("python and the editor agree on the disposition probe as well", not probe_problems,
          "\n      ".join(probe_problems[:8]))

    print("every shipped fixture is pinned, in both halves, by some baseline (F2)")
    baseline_coverage()

    print("the readers survive an imported width, and the JS clip is real (verifier round 2)")
    width_checks()

    print("the PNG covers the whole page (a canvas-sized window cut the bottom off)")
    png_html = os.path.join(TMP, "png.html")
    boardkit.report(png_html, boardkit.load(str(V13)), subtitle="board v13fixture")
    # measured at the SAME width the shot uses: the board scales with the window width, so a
    # measurement taken at another width is not the height this picture needs.
    measured = boardkit.page_size(png_html, window_width=900)
    png = os.path.join(TMP, "v13.png")
    boardkit.render_png(png_html, png, width=900)
    with open(png, "rb") as handle:
        header = handle.read(33)
    width, height = struct.unpack(">II", header[16:24])
    check("the page measures itself", measured is not None, str(measured))
    check("the PNG window covers the measured page (W6: nothing is cut off)",
          measured is not None and height >= measured[1] and width >= measured[0] - 1,
          f"png {width}x{height} vs page {measured}")
    # A measurement that reports the WINDOW instead of the content (done once: every PNG came out
    # 900x3913 of whitespace) still satisfies "covers the page", so bound it from above too.
    check("the PNG is not wildly taller than the page it measured (the measurement is the content)",
          measured is not None and width == 900 and height <= measured[1] + 60,
          f"png {width}x{height} vs page {measured}")

    print("the editor's own self-test")
    report = boardkit.selftest(page)
    check("self-test ok", report.get("ok") is True, str(report)[:200])
    names = " ".join(str(step[0]).lower() for step in report.get("steps", []) if step)
    for word in ("shape", "part", "head", "grid", "align", "snap",
                 "zoom", "pan", "marquee", "mselect", "groupmove", "groupdrag", "groupdup",
                 "duplicate", "attach", "origin", "clickdrag",
                 "panelhide", "splitstep", "arrange", "resize", "guides",
                 "sections", "validate", "cellcontrols", "replace", "panelfields", "celledit",
                 "panelinputs", "badwidth", "clipjs"):
        check(f"self-test exercised {word}", word in names, names[:200])
    # v1.6 (Z9): the six new names must be present EXACTLY and TRUE. A substring check would let
    # "line" be satisfied by "linepanel", and a step that reports False must not read as exercised.
    by_name = {}
    for step in report.get("steps", []):
        if step:
            by_name.setdefault(str(step[0]), []).append(bool(step[1]))
    for word in ("line", "linesnap", "lineedit", "edgepath", "barfit", "linepanel",
                 "anchorpoints", "anchorsnap", "anchordrag",
                 "livepatch", "straightdraw"):
        got = by_name.get(word, [])
        check(f"self-test exercised {word} exactly and truly", bool(got) and all(got), f"{word}: {got}")

    print("a gesture does not rebuild the board (v1.8 R1-R5), and a straight drag is a 45-deg step")
    churn = gesture_churn()
    check("the stroke keeps the SAME svg element (R1)", churn.get("svgSame") is True,
          f"svgSame={churn.get('svgSame')}")
    check("the stroke leaves every scene element in place (R2/R5)",
          churn.get("strokeSceneKept") is True,
          f"kept={churn.get('strokeSceneKept')} mutations={churn.get('strokeMutations')}")
    stroke_host = churn.get("strokeHostMutations")
    check("the stroke never replaces the host's children (Z9 churn budget)",
          isinstance(stroke_host, int) and stroke_host <= 2,
          f"host childList mutations={stroke_host}")
    check("the stroke's preview is drawn while it is live (R2/S3)", churn.get("strokeDraft") is True)
    check("the drag keeps the SAME svg element (R1)", churn.get("dragSvgSame") is True,
          f"dragSvgSame={churn.get('dragSvgSame')}")
    check("the drag leaves every scene element in place (R3)",
          churn.get("dragSceneKept") is True,
          f"kept={churn.get('dragSceneKept')} mutations={churn.get('dragMutations')}")
    drag_host = churn.get("dragHostMutations")
    check("the drag never replaces the host's children (Z9 churn budget)",
          isinstance(drag_host, int) and drag_host <= 2,
          f"host childList mutations={drag_host}")
    # F1 (verifier round 2, CRITICAL): the STRAIGHT drag is a live gesture too — measured while the
    # draft is live, because a drag that rebuilds on every move passes every post-release check.
    check("a live straight drag keeps the SAME svg element (F1/R1)", churn.get("straightSvgSame") is True,
          f"straightSvgSame={churn.get('straightSvgSame')}")
    check("a live straight drag leaves every scene element in place (F1/R2/S3)",
          churn.get("straightSceneKept") is True,
          f"kept={churn.get('straightSceneKept')} host mutations={churn.get('straightHostMutations')}")
    straight_host = churn.get("straightHostMutations")
    check("a live straight drag never replaces the host's children (F1/Z9 churn budget)",
          isinstance(straight_host, int) and straight_host <= 2,
          f"host childList mutations={straight_host}")
    check("a live straight drag draws its preview and writes nothing yet (S2/S3)",
          churn.get("straightDraft") is True and churn.get("straightDraftLive") is True,
          f"draft={churn.get('straightDraft')} unwritten={churn.get('straightDraftLive')}")
    check("Esc cancels a straight stroke without touching the scene (S5)",
          churn.get("straightCancelKept") is True and churn.get("straightCancelDraft") is True and
          churn.get("straightCancelWrote") is False,
          f"kept={churn.get('straightCancelKept')} dropped={churn.get('straightCancelDraft')} "
          f"wrote={churn.get('straightCancelWrote')}")
    check("the drag really moved the item (so the identity checks are not vacuous)",
          churn.get("dragMoved") is True, str(churn.get("transformBefore")) + " -> " +
          str(churn.get("transformAfter")))
    check("a patched gesture renders exactly as a full render does (R4)", churn.get("driftEqual") is True,
          "the patched DOM differs from a full render of the same model")
    check("the straight tool has a control in the rendered pane (S1)",
          churn.get("straightButton") is True, str(churn.get("tools")))
    check("a plain straight drag commits a 45-degree step (S2/S4)",
          churn.get("step45") is True, str(churn.get("line45")))
    check("a mostly-vertical straight drag commits a vertical line (S4)",
          churn.get("vertical") is True, str(churn.get("lineVertical")))
    check("a Shift straight drag keeps the pointer's own angle (S4)",
          churn.get("shiftFree") is True, str(churn.get("lineShift")))
    print(f"  measured: stroke mutations={churn.get('strokeMutations')} "
          f"drag mutations={churn.get('dragMutations')} svg elements={churn.get('svgCreations')} "
          f"bar rows at 800px={churn.get('barRows')}")

    print("the anchor fraction is the point the spec pins, not the midpoint (A2/A8)")
    anchor_coordinate_checks()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


def anchor_coordinate_checks() -> None:
    """`at` is measured along the side's INCREASING coordinate (SPEC-v1.7 A2) — asserted against the
    formula, in both halves, and against the picture the Python renderer actually draws.

    The pinned baseline cannot catch this class of defect (both halves returning 0.5, or both
    swapping an axis, pass parity and every old hash), so the numbers are derived HERE, from the
    rect and the fraction, independently of `boardkit`'s own helper.
    """
    import inspect
    import json

    board = boardkit.load(str(V17))
    rects = {n["id"]: (float(n["x"]), float(n["y"]), float(n["w"]), float(n["h"]))
             for n in board["nodes"]}

    def expected(r, side, at):
        x, y, w, h = r
        if side == "left":
            return (x, y + at * h)
        if side == "right":
            return (x + w, y + at * h)
        if side == "top":
            return (x + at * w, y)
        if side == "bottom":
            return (x + at * w, y + h)
        raise AssertionError(side)

    params = inspect.signature(boardkit._side_mid).parameters
    check("the resolver takes a position along the side (A3)", len(params) >= 3,
          f"_side_mid signature: {list(params)}")

    rect = (100.0, 200.0, 80.0, 40.0)
    for side in ("left", "right", "top", "bottom"):
        for at in (0.25, 0.5, 0.75):
            want = expected(rect, side, at)
            try:
                got = boardkit._side_mid(rect, side, at)
            except TypeError as exc:
                check(f"_side_mid({side}, {at}) takes the fraction", False, str(exc))
                continue
            check(f"_side_mid {side} at {at} is the pinned point",
                  abs(got[0] - want[0]) < 1e-6 and abs(got[1] - want[1]) < 1e-6,
                  f"got {got} want {want}")
    # a non-midpoint fraction must not collapse to the midpoint, and 0.25/0.75 must not be swapped
    check("0.25 and 0.75 are not the same point",
          boardkit._side_mid(rect, "top", 0.25) != boardkit._side_mid(rect, "top", 0.75),
          f"{boardkit._side_mid(rect, 'top', 0.25)} vs {boardkit._side_mid(rect, 'top', 0.75)}")

    # an `auto` side with a non-midpoint fraction: the side keeps its v1.2 choice, the position does not
    a, b = (100.0, 200.0, 80.0, 40.0), (400.0, 200.0, 80.0, 40.0)
    try:
        got = boardkit.anchors(a, b, "auto", "auto", 0.75, 0.25)
        auto_ok = abs(got[0][1] - (200.0 + 0.75 * 40.0)) < 1e-6 and \
            abs(got[1][1] - (200.0 + 0.25 * 40.0)) < 1e-6
    except TypeError as exc:
        auto_ok, got = False, str(exc)
    check("an auto side keeps its choice and still honours the fraction", auto_ok, str(got))

    # the picture agrees: the fixture's first line draws its anchor at the pinned point
    drawn = re.search(r'data-id="line-q25"[^>]*>\s*<polyline points="([^"]+)"',
                      boardkit.render_svg(board), re.S)
    if not drawn:
        check("the fixture's first line is drawn", False, "no line-q25 polyline in the SVG")
        return
    first = drawn.group(1).split()[0].split(",")
    want = expected(rects["box-a"], "left", 0.25)
    check("the drawn line starts at the anchor the formula pins",
          abs(float(first[0]) - want[0]) < 0.5 and abs(float(first[1]) - want[1]) < 0.5,
          f"drawn {first} want {want} (line-q25, box-a left at 0.25)")


if __name__ == "__main__":
    import shutil
    try:
        raise SystemExit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
