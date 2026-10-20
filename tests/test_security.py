#!/usr/bin/env python3
"""The security findings from the plugin-catalog review, graded at 7c8e9f3.

    python3 tests/test_security.py

Each check corresponds to a defect a reviewer demonstrated, and each one is written to FAIL while
its fix is absent:

1. **No caller-chosen output path.** `board_render` offered a model-supplied `out_path`
   (tools.py:145, :249) that `store.render_to_file` wrote verbatim (store.py:1771/1776), so a
   prompt-injected call could overwrite any file the user can write.
2. **No unsanitised board id in a path.** `_path()` sanitised the id for the JSON store while
   `render_to_file` interpolated the RAW id (store.py:1771/1774/1776): the board was found *because*
   `load_board` sanitised, and the render landed outside `RENDER_DIR`.
3. **`</script>` cannot close the embedded payload.** boardkit.py:2150 embeds the document with
   `json.dumps`, which does not escape `</`, so node text `</script><script>…` executed in the headless
   render and in any HTML export.
4. **No `--no-sandbox`, always a private profile.** Four headless invocations (2235, 2265, 2417, 2486)
   passed `--no-sandbox`, and three of them shared the user's Chrome profile. The review named two.
5. **No other profile's state.db.** `_state_db_candidates()` fell back to `~/.hermes/state.db`, so a
   plugin running under any profile could read the default profile's session store.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="hermes-board-security-")
os.environ["HERMES_BOARD_DIR"] = os.path.join(TMP, "boards")
os.environ["HERMES_BOARD_RENDER_DIR"] = os.path.join(TMP, "render")
os.environ["HERMES_BOARD_WATCH"] = os.path.join(TMP, "watch.json")
os.environ.pop("HERMES_STATE_DB", None)
os.environ["HERMES_HOME"] = os.path.join(TMP, "home")

sys.path.insert(0, str(ROOT / "vendor"))


def load_package():
    """Load this folder as a package (its name has a dash, so plain import cannot do it)."""
    spec = importlib.util.spec_from_file_location(
        "hermes_board", ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["hermes_board"] = module
    spec.loader.exec_module(module)
    return module


load_package()
from hermes_board import store, tools  # noqa: E402  (the env above must be set first)

FAILURES: list[str] = []


def check(name, condition, detail=""):
    print(f"  {'ok  ' if condition else 'FAIL'} {name} {detail if not condition else ''}".rstrip())
    if not condition:
        FAILURES.append(name)


def boardkit():
    """The bundled renderer, imported the way the plugin imports it."""
    import boardkit as kit
    return kit


class _Ctx:
    """Just enough PluginContext to capture what `register_tools` registers."""

    def __init__(self):
        self.tools: dict = {}

    def register_tool(self, name=None, toolset=None, schema=None, handler=None, **kw):
        self.tools[name] = {"schema": schema, "handler": handler, "toolset": toolset}

    def __getattr__(self, _item):
        def _noop(*_a, **_kw):
            return None
        return _noop


def contains(obj, needle: str) -> bool:
    if isinstance(obj, dict):
        return any(contains(v, needle) for v in obj.values())
    if isinstance(obj, list):
        return any(contains(v, needle) for v in obj)
    return isinstance(obj, str) and needle in obj


def main() -> int:
    inside_render = os.path.realpath(os.environ["HERMES_BOARD_RENDER_DIR"]) + os.sep
    os.makedirs(os.environ["HERMES_BOARD_DIR"], exist_ok=True)

    # ── 1. no caller-chosen output path ───────────────────────────────────────────────────────
    print("1. board_render accepts no caller-chosen output path")
    check("render_to_file has no `out_path` parameter",
          "out_path" not in inspect.signature(store.render_to_file).parameters)

    ctx = _Ctx()
    tools.register_tools(ctx)
    render_tool = ctx.tools.get("board_render") or {}
    props = ((render_tool.get("schema") or {}).get("parameters") or {}).get("properties") or {}
    check("the board_render schema declares no `out_path`", props and "out_path" not in props,
          f"props={sorted(props)}")

    store.write_board("victim", store.new_board("victim"), origin="agent", notify=False)
    victim_path = os.path.join(TMP, "OUTSIDE-the-render-dir.html")
    if render_tool.get("handler"):
        render_tool["handler"]({"id": "victim", "format": "html", "out_path": victim_path})
    check("a supplied out_path writes nothing outside the render dir", not os.path.isfile(victim_path))

    # ── 2. a board id never escapes the render dir ───────────────────────────────────────────
    print("2. a traversal board id cannot render outside RENDER_DIR")
    for hostile in ("../../escaped", "/tmp/absolute-escape", "..", "", "a/../../b"):
        try:
            store.write_board(hostile, store.new_board(hostile), origin="agent", notify=False)
            rendered = store.render_to_file(hostile, kind="html")
        except Exception as err:                       # refusing outright is also correct
            print(f"  ok   {hostile!r} refused: {type(err).__name__}: {err}")
            continue
        real = os.path.realpath(rendered)
        check(f"{hostile!r} renders inside RENDER_DIR", real.startswith(inside_render),
              f"escaped to {real}")

    # ── 3. `</script>` cannot close the payload ──────────────────────────────────────────────
    print("3. `</script>` in board text cannot close the embedded JSON payload")
    evil = "</script><script>document.title='pwned'</script>"
    page_board = store.new_board("payload")
    store.apply_ops(page_board, [{"op": "add_node", "id": "n1", "text": evil}])
    page = store.render_html(page_board)
    hit = re.search(r'<script type="application/json" id="ck-board">(.*?)</script>', page, re.S)
    check("the payload block is present", hit is not None)
    payload = hit.group(1) if hit else ""
    check("the payload holds no raw `</script>`", "</script>" not in payload,
          f"found at {payload.find('</script>')}")
    check("the payload holds no raw `<!--`", "<!--" not in payload)
    try:
        loaded = json.loads(payload)
        check("the payload still parses as JSON", True)
        check("the hostile text survives the round-trip intact", contains(loaded, evil))
    except json.JSONDecodeError as err:
        check("the payload still parses as JSON", False, f"{err}")
        check("the hostile text survives the round-trip intact", False, "no payload to read")

    # ── 4. headless flags ────────────────────────────────────────────────────────────────────
    print("4. headless Chrome runs without --no-sandbox and with a private profile")
    kit = boardkit()
    html_path = os.path.join(TMP, "probe.html")
    with open(html_path, "w", encoding="utf-8") as handle:
        handle.write("<!doctype html><html><body>probe</body></html>")

    recorded: list[list[str]] = []

    class _Done:
        returncode = 0
        stderr = ""
        stdout = "<html><head><title>100x200</title></head><body></body></html>"

    real_run = subprocess.run

    def fake_run(args, **_kw):
        recorded.append([str(x) for x in args])
        return _Done()

    subprocess.run = fake_run
    try:
        kit.render_png(html_path, os.path.join(TMP, "out.png"), size="100,100")
        kit.page_size(html_path)
    finally:
        subprocess.run = real_run

    check("both headless invocations were observed", len(recorded) >= 2, f"got {len(recorded)}")
    for argv in recorded:
        joined = " ".join(argv)
        check(f"no --no-sandbox as non-root ({pathlib.Path(argv[0]).name})",
              "--no-sandbox" not in argv or os.geteuid() == 0, joined[:90])
        check(f"private --user-data-dir ({pathlib.Path(argv[0]).name})",
              any(a.startswith("--user-data-dir=") for a in argv), joined[:90])

    # The two CDP-driven paths cannot be exercised without a browser, so the source is the gate:
    # every headless argv must come from ONE helper, which is the only place the flag may live.
    source = (ROOT / "vendor" / "boardkit.py").read_text(encoding="utf-8")
    literal = [i for i, line in enumerate(source.splitlines(), 1) if '"--no-sandbox"' in line]
    check("exactly one site can emit --no-sandbox (the shared helper)", len(literal) == 1,
          f"lines {literal}")
    if len(literal) == 1:
        owner = ""
        for line in reversed(source.splitlines()[:literal[0]]):
            if line.startswith("def "):
                owner = line.split("(")[0][4:]
                break
        check("and that site is the shared helper", owner == "_headless_args", f"owner={owner!r}")
    check("all four headless invocations are built by the helper",
          source.count("_headless_args(") >= 5, f"seen {source.count('_headless_args(')}")

    # ── 5. never another profile's state.db ─────────────────────────────────────────────────
    print("5. the session-key lookup never falls back to the default profile's state.db")
    real_isfile = os.path.isfile
    os.path.isfile = lambda _p: True                 # make every candidate "present"
    try:
        candidates = store._state_db_candidates()
    finally:
        os.path.isfile = real_isfile
    default_db = os.path.expanduser("~/.hermes/state.db")
    check("the default profile's state.db is not a candidate", default_db not in candidates,
          f"{candidates}")
    check("this profile's state.db still is",
          os.path.join(os.environ["HERMES_HOME"], "state.db") in candidates, f"{candidates}")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
