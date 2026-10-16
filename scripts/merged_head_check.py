#!/usr/bin/env python3
"""Merged-head checks the orchestrator owes after every lane merge or fix.

Gates, all of them hard:

1. ONE definition per name in both halves. A second `function foo()` in the merged editor does not
   shadow — the last declaration wins silently — so a duplicate is a real defect, not a style choice;
2. byte identity for the four shipped fixtures against `docs/baseline-v1.6.json`, in BOTH halves:
   the Python `render_svg` and the editor's rendered DOM. Cross-half parity cannot prove it (both
   halves can move together);
3. the tree under inspection is derived from THIS script's checkout — never a hardcoded home. A gate
   that resolves another tree prints a false green for the one you are actually standing in
   (round-2 verifier finding, MAJOR).

Exits NONZERO when any check fails, and also when a check cannot run: a missing browser is a
FAILURE, not a skip — a gate that quietly skips itself is a green light.

    python3 scripts/merged_head_check.py
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
JS = ROOT / "vendor" / "board_editor.js"
PY = ROOT / "vendor" / "boardkit.py"
BASELINE = ROOT / "docs" / "baseline-v1.6.json"

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def dupes(names):
    return {n: c for n, c in collections.Counter(names).items() if c > 1}


def main() -> int:
    print(f"merged-head checks in {ROOT}")
    js = JS.read_text(encoding="utf-8")
    py = PY.read_text(encoding="utf-8")

    js_names = re.findall(r"^\s{2}function\s+([A-Za-z_$][\w$]*)\s*\(", js, re.M)
    py_names = re.findall(r"^def\s+(\w+)", py, re.M)
    js_dupes, py_dupes = dupes(js_names), dupes(py_names)
    check(f"one definition per name in the editor ({len(js_names)} names)",
          not js_dupes, f"duplicated: {sorted(js_dupes)}")
    check(f"one definition per name in the renderer ({len(py_names)} names)",
          not py_dupes, f"duplicated: {sorted(py_dupes)}")

    sys.path.insert(0, str(ROOT / "vendor"))
    sys.path.insert(0, str(ROOT / "tests"))
    import boardkit  # noqa: E402

    pinned: dict = {}
    for baseline in sorted((ROOT / "docs").glob("baseline-v*.json")):
        pinned.update(json.loads(baseline.read_text(encoding="utf-8"))["fixtures"])
    check("every shipped document is pinned (a fixture nobody pinned cannot be compared)",
          len(pinned) >= 6, f"{len(pinned)} pinned: {sorted(pinned)}")
    browser = boardkit.find_chrome()
    check("a browser is on PATH (the editor half must not be skipped silently)", bool(browser),
          f"find_chrome() -> {browser}")
    if not browser:
        print(f"\nVERDICT: DEFECTS FOUND ({len(failures)})")
        return 1

    import test_editor  # the repo's own Chrome harness (dump_host)

    tmp = tempfile.mkdtemp()
    for name in sorted(pinned):
        want = pinned[name]
        board = boardkit.load(str(ROOT / "docs" / name))
        got_py = hashlib.sha256(boardkit.render_svg(board).encode()).hexdigest()
        check(f"{name} — python render matches the pre-wave baseline",
              got_py == want["python_sha256"], f"{got_py[:16]} vs {want['python_sha256'][:16]}")
        page = os.path.join(tmp, name + ".html")
        boardkit.report(page, board, bridge=True)
        got_js = hashlib.sha256(test_editor.dump_host(page).encode()).hexdigest()
        check(f"{name} — editor DOM matches the pre-wave baseline",
              got_js == want["editor_sha256"], f"{got_js[:16]} vs {want['editor_sha256'][:16]}")

    print(f"\nVERDICT: {'clean' if not failures else f'DEFECTS FOUND ({len(failures)})'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
