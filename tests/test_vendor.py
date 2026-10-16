#!/usr/bin/env python3
"""The bundled boardkit must be enough on its own, and must not drift from the skill.

    python3 tests/test_vendor.py

Two things can go wrong with a vendored library and neither shows up as an error: the plugin stops
working on a host that never had the skill (missing file, missing companion), or the two copies
quietly disagree and a bug fixed in one is still live in the other. Both are checked here — the
drift check is skipped, loudly, when no skill copy exists on this machine.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="hermes-board-vendor-")
os.environ["HERMES_BOARD_DIR"] = os.path.join(TMP, "boards")
os.environ["HERMES_BOARD_RENDER_DIR"] = os.path.join(TMP, "render")
os.environ.pop("HERMES_BOARDKIT", None)

FAILURES: list[str] = []


def check(name: str, condition, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


def load_package():
    spec = importlib.util.spec_from_file_location(
        "hermes_board", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["hermes_board"] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    print("bundled files")
    for name in ("boardkit.py", "board_editor.js"):
        check(f"vendor/{name} ships", (ROOT / "vendor" / name).is_file())

    print("self-contained resolution")
    spec = importlib.util.spec_from_file_location("hb_store", ROOT / "store.py")
    store = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(store)
    check("the candidate list offers the bundled copy",
          str(ROOT / "vendor") in store._boardkit_candidates(),
          str(store._boardkit_candidates()[:2]))
    store._BK = None
    loaded = store.boardkit()
    check("boardkit resolves without a skill checkout",
          pathlib.Path(loaded.__file__).resolve() == (ROOT / "vendor" / "boardkit.py").resolve(),
          str(getattr(loaded, "__file__", "")))
    check("the editor companion is found next to it",
          pathlib.Path(loaded.EDITOR).is_file(), str(loaded.EDITOR))

    print("bundled copy works")
    pkg = load_package()
    ctx = type("Ctx", (), {"tools": {}, "register_tool": lambda self, **kw: self.tools.update(
        {kw["name"]: kw}), "on_dispose": lambda self, fn: None})()
    pkg.register_tools(ctx)
    made = json.loads(ctx.tools["board_new"]["handler"]({"id": "vendor", "title": "Vendor"}))
    applied = json.loads(ctx.tools["board_apply"]["handler"]({"id": "vendor", "ops": [
        {"op": "add_node", "id": "a", "text": "A"}, {"op": "add_node", "id": "b", "text": "B"},
        {"op": "add_edge", "from": "a", "to": "b"}]}))
    check("board_new and board_apply run on the bundled library",
          made["ok"] and applied["ok"] and len(applied["did"]) == 3, json.dumps(applied)[:120])
    rendered = json.loads(ctx.tools["board_render"]["handler"]({"id": "vendor", "format": "html"}))
    html = open(rendered["path"], encoding="utf-8").read()
    check("the bundled copy renders the editor page",
          rendered["ok"] and "ck-board-host" in html and "ck-selftest" in html)
    png = json.loads(ctx.tools["board_render"]["handler"]({"id": "vendor", "format": "png"}))
    if store.boardkit().find_chrome():
        check("the bundled copy renders a PNG", png["ok"] and os.path.getsize(png["path"]) > 3000,
              str(png.get("path")))
    else:
        print("  skip bundled PNG — no Chrome/Chromium on PATH")

    print("no drift from the skill copy")
    home = pathlib.Path(os.path.expanduser(os.environ.get("HERMES_HOME") or "~/.hermes"))
    skill = home / "skills/data-science/chat-charts/scripts"
    if not (skill / "boardkit.py").is_file():
        print("  skip no chat-charts skill on this machine — drift unchecked")
    else:
        for name in ("boardkit.py", "board_editor.js"):
            mine = hashlib.sha256((ROOT / "vendor" / name).read_bytes()).hexdigest()
            theirs = hashlib.sha256((skill / name).read_bytes()).hexdigest()
            check(f"vendor/{name} matches the skill copy", mine == theirs,
                  "run scripts/sync_boardkit.py and commit the result")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    import shutil
    try:
        raise SystemExit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
