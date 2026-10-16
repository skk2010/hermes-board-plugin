#!/usr/bin/env python3
"""Refresh this plugin's bundled boardkit from a chat-charts install.

    python3 scripts/sync_boardkit.py [path/to/chat-charts/scripts]

The plugin ships `vendor/boardkit.py` + `vendor/board_editor.js` so it installs on a host that
never had the skill; this keeps that copy honest. Copies, then prints the hashes of both sides so
a sync is visible in the diff, and touches nothing else. Run it after editing the skill's copy,
then run the tests — `tests/test_vendor.py` fails while the two copies differ.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parents[1]
VENDOR = HERE / "vendor"
FILES = ("boardkit.py", "board_editor.js")


def sources() -> list[pathlib.Path]:
    """Candidate source directories, most specific first."""
    from_arg = [pathlib.Path(sys.argv[1])] if len(sys.argv) > 1 else []
    home = pathlib.Path(os.path.expanduser(os.environ.get("HERMES_HOME") or "~/.hermes"))
    return from_arg + [
        pathlib.Path(os.environ["HERMES_BOARDKIT"]) if os.environ.get("HERMES_BOARDKIT") else None,
        home / "skills/data-science/chat-charts/scripts",
        *(home.glob("profiles/*/skills/data-science/chat-charts/scripts")),
        *(home.glob("skills/*/*/scripts")),
    ]


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def main() -> int:
    source = next((candidate for candidate in sources()
                   if candidate and (candidate / "boardkit.py").is_file()
                   and candidate.resolve() != VENDOR.resolve()), None)
    if source is None:
        print("no chat-charts scripts directory found; pass one as an argument", file=sys.stderr)
        return 2
    print(f"source: {source}")
    VENDOR.mkdir(exist_ok=True)
    changed = []
    for name in FILES:
        origin, target = source / name, VENDOR / name
        if not origin.is_file():
            print(f"  {name}: missing in the source — skipped")
            continue
        before = digest(target) if target.is_file() else "absent"
        if before != digest(origin):
            shutil.copyfile(origin, target)
            changed.append(name)
        print(f"  {name}: {before} -> {digest(target)}" + ("  (updated)" if name in changed else ""))
    print("bundled copy is up to date" if not changed else f"updated: {', '.join(changed)} — "
          "re-run tests/test_tools.py and commit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
