#!/usr/bin/env python3
"""Real-input reproduction: does a REAL double-click commit the line, as Enter does?

MANUAL REPRODUCTION — not a CI gate. This script needs a Chrome binary and the `websockets`
package and is run by hand (`PYTHONPATH= python3 scripts/repro_dblclick.py`); nothing in the
test suite invokes it. The editor's own self-test (the `line` step, driven with the mousedown
sequence this script measured) is the gate.

The self-test once dispatched `new MouseEvent("dblclick")` straight onto the SVG, which is
exactly the synthetic shortcut that cannot see this bug: with real input the browser decides
whether a dblclick happened, and the line tool rebuilds the SVG on every pointerdown/move, so
the compatibility events (click/dblclick) are never delivered — the click count rides on
`mousedown.detail`.

Drives headless Chrome over CDP with Input.dispatchMouseEvent, so the events come from the
browser's input pipeline rather than from script. Honours BOARD_ROOT (default: this repo).
Exits 0 only when BOTH gestures commit.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

import websockets

PORT = 9333
ROOT = os.environ.get("BOARD_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "vendor"))
import boardkit  # noqa: E402


def page_file() -> str:
    board = boardkit.load(os.path.join(ROOT, "docs", "fixture-v1.6.json"))
    path = os.path.join(tempfile.gettempdir(), "v16-dbl.html")
    boardkit.report(path, board, bridge=True)
    return "file://" + path


def launch() -> subprocess.Popen:
    profile = tempfile.mkdtemp()
    proc = subprocess.Popen(
        [boardkit.find_chrome(), "--headless=new", "--disable-gpu", "--no-sandbox",
         "--hide-scrollbars", f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
         "--window-size=1000,900", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1).read()
            break
        except Exception:                                    # noqa: BLE001
            time.sleep(0.5)
    return proc


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.n = 0

    async def call(self, method: str, **params):
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == mid:
                return msg.get("result", {})

    async def js(self, expr: str):
        res = await self.call("Runtime.evaluate", expression=expr, returnByValue=True)
        return res.get("result", {}).get("value")


async def click(cdp: CDP, x: float, y: float, count: int = 1) -> None:
    for kind in ("mousePressed", "mouseReleased"):
        await cdp.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left",
                       buttons=1 if kind == "mousePressed" else 0, clickCount=count)


async def run_case(cdp: CDP, commit_by: str) -> dict:
    await cdp.js("document.querySelector('[data-tool=\"line\"]').click()")
    geo = json.loads(await cdp.js(
        "JSON.stringify((()=>{const s=document.querySelector('svg.ck-board');"
        "const r=s.getBoundingClientRect();return {x:r.left,y:r.top,w:r.width,h:r.height};})())"))
    x0, y0 = geo["x"] + geo["w"] * 0.25, geo["y"] + geo["h"] * 0.30
    # three real clicks -> three draft points
    await click(cdp, x0, y0)
    await click(cdp, x0 + 60, y0)
    await click(cdp, x0 + 120, y0)
    if commit_by == "dblclick":
        # a REAL double-click: two press/release pairs inside the double-click interval
        await click(cdp, x0 + 120, y0, 1)
        await click(cdp, x0 + 120, y0, 2)
    else:
        await cdp.call("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter",
                       windowsVirtualKeyCode=13, nativeVirtualKeyCode=13)
        await cdp.call("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter",
                       windowsVirtualKeyCode=13, nativeVirtualKeyCode=13)
    await asyncio.sleep(0.4)
    return {
        "lines_in_dom": await cdp.js("document.querySelectorAll('.ck-line').length"),
        "status": await cdp.js("(document.getElementById('ck-status')||{}).textContent || ''"),
        "panel_on": await cdp.js("!!document.querySelector('.ck-panel.on')"),
    }


async def main() -> int:
    proc = launch()
    results = {}
    try:
        targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list").read())
        page = next(t for t in targets if t["type"] == "page")
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=8 << 20) as ws:
            cdp = CDP(ws)
            await cdp.call("Page.enable")
            await cdp.call("Runtime.enable")
            await cdp.call("Emulation.setDeviceMetricsOverride", width=1000, height=900,
                           deviceScaleFactor=1, mobile=False)
            await cdp.call("Page.navigate", url=page_file())
            await asyncio.sleep(1.5)
            base = await cdp.js("document.querySelectorAll('.ck-line').length")
            for case in ("dblclick", "enter"):
                got = await run_case(cdp, case)
                results[case] = got
                print(f"commit by {case:8}: {json.dumps(got)}")
                await cdp.call("Page.navigate", url=page_file())
                await asyncio.sleep(1.2)
    finally:
        proc.terminate()
    ok = all(got["lines_in_dom"] == base + 1 for got in results.values())
    print("PASS: both gestures commit" if ok else
          "FAIL: a gesture did not commit (see the counts above)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
