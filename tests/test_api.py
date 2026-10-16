#!/usr/bin/env python3
"""Drive the plugin's HTTP namespace through the real ASGI app — no server, no httpx.

    python3 tests/test_api.py

This is the backend the desktop pane talks to, so it gets tested like a client would: route
table, status codes, the stale-write guard, the ops endpoint and the editor page the pane loads
into its frame (bridge mode on).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="hermes-board-api-")
os.environ["HERMES_BOARD_DIR"] = os.path.join(TMP, "boards")
os.environ["HERMES_BOARD_RENDER_DIR"] = os.path.join(TMP, "render")
os.environ["HERMES_BOARD_WATCH"] = os.path.join(TMP, "watch.json")

FAILURES: list[str] = []


def check(name, condition, detail=""):
    print(f"  {'ok  ' if condition else 'FAIL'} {name} {detail if not condition else ''}".rstrip())
    if not condition:
        FAILURES.append(name)


def load(module_name, path):
    spec = importlib.util.spec_from_file_location(f"hermes_board_{module_name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def mounted_routes(app) -> set:
    """Every (path, method) this app answers on, whatever shape the installed FastAPI mounts.

    FastAPI used to flatten ``include_router()`` into ``app.routes`` with the prefix already applied;
    since 0.14x it mounts one ``_IncludedRouter`` whose routes and prefix live in ``include_context``.
    A route-table check written for the first shape reports "nothing mounted" on the second — and
    would pass vacuously if the walker simply found no routes at all, so this walks both shapes.
    """
    found: set = set()

    def walk(routes, prefix: str) -> None:
        for route in routes:
            context = getattr(route, "include_context", None)
            if context is not None:                       # FastAPI >= 0.14x include_router()
                walk(getattr(getattr(context, "included_router", None), "routes", []),
                     prefix + (getattr(context, "prefix", "") or ""))
                continue
            path = getattr(route, "path", None) or ""
            children = getattr(route, "routes", None)
            if children:                                  # Starlette Mount / plain router
                walk(children, prefix + path)
                continue
            for method in (getattr(route, "methods", None) or ()):
                found.add((prefix + path, str(method).upper()))

    walk(getattr(app, "routes", []), "")
    return found


async def request(app, method, path, query="", body=None):
    """Minimal ASGI client: enough to exercise routing, status and JSON bodies."""
    payload = json.dumps(body).encode() if body is not None else b""
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "path": path, "raw_path": path.encode(), "query_string": query.encode(),
        "root_path": "", "scheme": "http", "client": ("127.0.0.1", 12345), "server": ("test", 80),
        "headers": [(b"host", b"test"), (b"content-type", b"application/json")],
    }
    messages = []
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        parsed = raw.decode("utf-8", "replace")
    return status, parsed


async def main_async() -> int:
    from fastapi import FastAPI

    api = load("api", ROOT / "dashboard" / "plugin_api.py")
    store = api.store
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/hermes-board")

    print("route table")
    mounted = mounted_routes(app)
    wanted = {("/api/plugins/hermes-board/boards", "GET"), ("/api/plugins/hermes-board/boards", "POST"),
              ("/api/plugins/hermes-board/board", "GET"), ("/api/plugins/hermes-board/board", "POST"),
              ("/api/plugins/hermes-board/board", "DELETE"),
              ("/api/plugins/hermes-board/page", "GET"),
              ("/api/plugins/hermes-board/ops", "POST"),
              ("/api/plugins/hermes-board/layout", "POST")}
    check("every route/method is mounted under the plugin namespace", wanted <= mounted,
          f"missing={sorted(wanted - mounted)} mounted={sorted(mounted)[:6]}")

    print("board lifecycle over http")
    status, body = await request(app, "POST", "/api/plugins/hermes-board/boards",
                                 body={"id": "fresh", "title": "Fresh"})
    check("POST /boards creates one", status == 200 and body["ok"] and body["id"] == "fresh",
          str(body))
    status, body = await request(app, "POST", "/api/plugins/hermes-board/boards",
                                 body={"id": "fresh"})
    check("POST /boards refuses a name clash", status == 200 and body.get("error") == "exists",
          str(body))
    status, body = await request(app, "DELETE", "/api/plugins/hermes-board/board", "id=fresh")
    check("DELETE /board removes it", status == 200 and body["ok"], str(body))
    status, body = await request(app, "DELETE", "/api/plugins/hermes-board/board", "id=fresh")
    check("deleting twice is a 404", status == 404, str(status))

    print("http behaviour")
    store.save_board("pane", store.new_board("pane", "Pane board"))
    status, body = await request(app, "GET", "/api/plugins/hermes-board/boards")
    check("GET /boards lists it", status == 200 and body["boards"][0]["id"] == "pane", str(body))

    status, body = await request(app, "GET", "/api/plugins/hermes-board/board", "id=pane")
    version = body["version"]
    check("GET /board returns the document and version", status == 200 and version, str(body)[:80])

    status, body = await request(app, "GET", "/api/plugins/hermes-board/page", "id=pane")
    html_page = body["html"]
    check("GET /page serves the editor", status == 200 and "ck-board-host" in html_page and
          "ck-board" in html_page)
    check("pane page runs in bridge mode", "CK_BOARD_BRIDGE = true" in html_page)
    check("pane page has no external reference",
          not any(x in html_page.lower() for x in ("<script src", "<link ", "@import", "url(http)")))

    ops = [{"op": "add_node", "id": "a", "text": "A"},
           {"op": "add_node", "id": "b", "text": "B"},
           {"op": "layout", "rows": [["a"], ["b"]]},
           {"op": "bus", "sources": ["a"], "targets": ["b"]}]
    status, body = await request(app, "POST", "/api/plugins/hermes-board/ops",
                                 body={"id": "pane", "ops": ops})
    check("POST /ops applies", status == 200 and body["ok"] and len(body["did"]) == 4, str(body)[:90])
    status, body = await request(app, "POST", "/api/plugins/hermes-board/ops",
                                 body={"id": "pane", "ops": [{"op": "nonsense"}]})
    check("POST /ops rejects a bad op", status == 400 and "unknown op" in str(body), str(body)[:90])

    status, body = await request(app, "GET", "/api/plugins/hermes-board/board", "id=pane")
    current = body["board"]
    current_version = body["version"]
    moved = json.loads(json.dumps(current))
    moved["title"] = "edited in the pane"
    status, body = await request(app, "POST", "/api/plugins/hermes-board/board",
                                 body={"id": "pane", "board": moved, "based_on": current_version,
                                       "origin": "pane"})
    check("POST /board saves a pane edit", status == 200 and body["ok"] and body["version"], str(body))
    check("a pane save reports what the user changed",
          "retitled" in (body.get("edited") or ""), str(body.get("edited")))
    check("a pane save reports the wake attempt",
          "wake" in body and body["wake"] is not None and body["wake"]["ok"] is False,
          str(body.get("wake")))
    stale = json.loads(json.dumps(moved))
    status, body = await request(app, "POST", "/api/plugins/hermes-board/board",
                                 body={"id": "pane", "board": stale, "based_on": current_version})
    check("POST /board refuses a stale write", status == 200 and body.get("error") == "stale"
          and body.get("board"), str(body)[:90])
    status, body = await request(app, "POST", "/api/plugins/hermes-board/board",
                                 body={"id": "pane", "board": {"nope": True}})
    check("POST /board validates the body", status == 400, str(body)[:90])
    status, body = await request(app, "GET", "/api/plugins/hermes-board/board", "id=ghost")
    check("missing board is a 404", status == 404, str(status))

    print("layout over http")
    await request(app, "POST", "/api/plugins/hermes-board/boards",
                  body={"id": "graph", "title": "Graph"})
    await request(app, "POST", "/api/plugins/hermes-board/ops", body={"id": "graph", "ops": [
        {"op": "add_node", "id": "a", "text": "A"}, {"op": "add_node", "id": "b", "text": "B"},
        {"op": "add_node", "id": "c", "text": "C"},
        {"op": "add_edge", "from": "a", "to": "b"}, {"op": "add_edge", "from": "a", "to": "c"}]})
    stacked = {n["id"]: (n["x"], n["y"]) for n in store.load_board("graph")["nodes"]}
    status, body = await request(app, "POST", "/api/plugins/hermes-board/layout",
                                 body={"id": "graph", "direction": "down", "bands": True})
    placed = {n["id"]: (n["x"], n["y"]) for n in body["board"]["nodes"]}
    check("POST /layout returns the re-laid board and its stats",
          status == 200 and body["ok"] and body["info"]["ranks"] == 2, str(body.get("info")))
    check("layout stacked the graph instead of the manual stack",
          placed["a"][1] < placed["b"][1] and placed["b"][1] == placed["c"][1]
          and placed["b"][0] != placed["c"][0], f"{stacked} -> {placed}")
    check("layout moved the boxes", placed != stacked, f"{stacked} -> {placed}")
    status, body = await request(app, "POST", "/api/plugins/hermes-board/layout",
                                 body={"id": "graph", "direction": "right"})
    side = {n["id"]: (n["x"], n["y"]) for n in body["board"]["nodes"]}
    check("the direction button lays the graph out sideways",
          side["a"][0] < side["b"][0] and side["b"][0] == side["c"][0], str(side))
    check("the laid-out board is what the pane will render",
          {n["id"]: (n["x"], n["y"]) for n in store.load_board("graph")["nodes"]} == side)
    status, body = await request(app, "POST", "/api/plugins/hermes-board/layout",
                                 body={"id": "ghost"})
    check("layout on a missing board is a 404", status == 404, str(status))
    last = store.read_events("graph")["events"][-1]
    check("the layout run is logged but does not wake anyone",
          last["origin"] == "pane" and "wake" not in last and "moved" in last["summary"],
          str(last.get("summary")))

    print("storage")
    check("pane edit landed on disk",
          store.load_board("pane")["title"] == "edited in the pane")
    logged = store.read_events("pane")["events"]
    check("the user's edit is in the watch log",
          any(e["origin"] == "pane" and "retitled the board to 'edited in the pane'" in e["summary"]
              for e in logged), str([(e["origin"], e["summary"]) for e in logged]))
    check("the agent's own writes are logged too, so the pane can show them",
          any(e["origin"] == "api" and "connected" in e["summary"] for e in logged),
          str([(e["origin"], e["summary"]) for e in logged]))
    check("board files stay in one directory",
          os.path.dirname(store._path("pane")) == store.BOARD_DIR)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main_async()))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
