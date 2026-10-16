#!/usr/bin/env python3
"""Pre-wave render baseline for v1.6: sha256 of both halves' SVG for the four shipped fixtures.

The v1.6 wave promises that a document without a `lines` key renders byte-identically to v1.5.
Parity between the two halves cannot prove that (both halves can move together), so the baseline is
recorded here at the base commit and re-checked on the merged head.

    python3 scripts/baseline_svg.py            # print
    python3 scripts/baseline_svg.py --write    # write docs/baseline-v1.6.json
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "vendor"))
sys.path.insert(0, os.path.join(HERE, "tests"))
import boardkit  # noqa: E402

FIXTURES = tuple(p.name for p in sorted(pathlib.Path(HERE, "docs").glob("fixture-v*.json")))
# every document the repo ships is pinned, not just the newest wave's: a wave that moves an
# older fixture's rendering is exactly what this file exists to catch.


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    import test_editor  # the repo's own Chrome harness (dump_host)

    tmp = tempfile.mkdtemp()
    out: dict = {"base_commit": subprocess.run(
                     ["git", "-C", HERE, "rev-parse", "HEAD"],
                     capture_output=True, text=True, check=False).stdout.strip(),
                 "fixtures": {}}
    for name in FIXTURES:
        path = os.path.join(HERE, "docs", name)
        board = boardkit.load(path)
        py_svg = boardkit.render_svg(board)
        page = os.path.join(tmp, name + ".html")
        boardkit.report(page, board, bridge=True)
        js_svg = test_editor.dump_host(page)
        out["fixtures"][name] = {"python_sha256": sha(py_svg), "editor_sha256": sha(js_svg),
                                 "python_bytes": len(py_svg.encode()), "editor_bytes": len(js_svg.encode())}
        print(f"{name}: py {out['fixtures'][name]['python_sha256'][:16]} "
              f"({out['fixtures'][name]['python_bytes']}B)  "
              f"js {out['fixtures'][name]['editor_sha256'][:16]} "
              f"({out['fixtures'][name]['editor_bytes']}B)")
    if "--write" in sys.argv:
        target = os.path.join(HERE, "docs", os.environ.get("BASELINE_OUT", "baseline-v1.7.json"))
        with open(target, "w") as fh:
            json.dump(out, fh, indent=1, sort_keys=True)
            fh.write("\n")
        print("wrote", target)
    print("base commit:", out["base_commit"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
