#!/usr/bin/env python3
"""In-process tests for the hermes-board plugin (no gateway, no desktop app needed).

    python3 tests/test_tools.py

Covers the layer that the CLI validators cannot: op application, layout geometry, bus trunks,
rendering, id sanitisation and the tool contracts. `hermes plugins validate/doctor` covers the
loading path; `tests/pane_smoke.mjs` covers the desktop half.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import shutil
import sqlite3
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="hermes-board-test-")
os.environ["HERMES_BOARD_DIR"] = os.path.join(TMP, "boards")
os.environ["HERMES_BOARD_RENDER_DIR"] = os.path.join(TMP, "render")
os.environ["HERMES_BOARD_WATCH"] = os.path.join(TMP, "watch.json")

FAILURES: list[str] = []


def check(name: str, condition, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


def load_package():
    """Load this folder as a package (its name has a dash, so plain import cannot do it)."""
    spec = importlib.util.spec_from_file_location(
        "hermes_board", ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["hermes_board"] = module
    spec.loader.exec_module(module)
    return module


class FakeCtx:
    """Stands in for PluginContext: records tools, and answers inject_message like the host."""

    def __init__(self, allow_injection: bool = True):
        self.tools = {}
        self.disposed = []
        self.injections = []
        self.allow_injection = allow_injection

    def inject_message(self, content, **kwargs):
        self.injections.append({"content": content, **kwargs})
        return self.allow_injection

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}

    def on_dispose(self, fn):
        self.disposed.append(fn)


def main() -> int:
    print("loading package")
    pkg = load_package()
    ctx = FakeCtx()
    pkg.register(ctx)
    store = sys.modules["hermes_board.store"]

    print("tool contracts")
    expected = {"board_list", "board_get", "board_new", "board_delete", "board_apply",
                "board_render", "board_watch"}
    check("seven tools registered", set(ctx.tools) == expected, str(sorted(ctx.tools)))
    check("one toolset", {t["toolset"] for t in ctx.tools.values()} == {"hermes_board"})
    apply_schema = ctx.tools["board_apply"]["schema"]
    check("apply schema requires ops", apply_schema["parameters"]["required"] == ["ops"])
    desc = apply_schema["description"]
    check("ops documented for the model",
          all(op in desc for op in ("add_node", "layout", "bus", "add_caption")), desc[:80])
    check("render schema offers png+html",
          ctx.tools["board_render"]["schema"]["parameters"]["properties"]["format"]["enum"]
          == ["png", "html"])

    print("board lifecycle")
    created = json.loads(ctx.tools["board_new"]["handler"]({"id": "test", "title": "Test"}))
    check("board_new wrote a file", created["ok"] and os.path.isfile(created["path"]),
          created.get("path", ""))

    ops = [
        {"op": "set_title", "title": "Agent drawn"},
        {"op": "add_node", "id": "ui", "text": "UI", "sub": "chat"},
        {"op": "add_node", "id": "api", "text": "API"},
        {"op": "add_node", "id": "db", "text": "DB"},
        {"op": "layout", "rows": [["ui"], ["api"], ["db"]], "labels": ["front", "core", "store"]},
        {"op": "bus", "sources": ["ui"], "targets": ["api", "db"], "direction": "v"},
        {"op": "add_caption", "text": "shared trunk", "x": 600, "y": 200},
        {"op": "add_note", "text": "drawn by the agent", "x": 600, "y": 400},
        {"op": "add_edge", "from": "api", "to": "db", "label": "sql", "style": "dashed"},
    ]
    result = json.loads(ctx.tools["board_apply"]["handler"]({"id": "test", "ops": ops}))
    check("nine ops applied", result["ok"] and len(result["did"]) == len(ops), str(result.get("did")))
    board = store.load_board("test")
    check("title set", board["title"] == "Agent drawn")
    check("canvas has a real size", float(board["width"]) > 0 and float(board["height"]) > 0,
          f'{board["width"]}x{board["height"]}')

    print("canvas covers the content")
    items = board["nodes"] + board["notes"] + board["groups"]
    outside = [(i.get("id"), float(i["x"]) + float(i["w"]), float(i["y"]) + float(i["h"]))
               for i in items
               if float(i["x"]) + float(i["w"]) > float(board["width"])
               or float(i["y"]) + float(i["h"]) > float(board["height"])]
    check("every item fits inside the canvas", not outside, str(outside))

    print("layout geometry")
    layers = [g for g in board["groups"] if str(g["id"]).startswith("layer-")]
    check("one band per layer", len(layers) == 3, str([g["id"] for g in board["groups"]]))
    boxed = [n for n in board["nodes"] if n.get("kind", "box") == "box"]
    inside = []
    for node in boxed:
        band = next((g for g in layers if g["x"] <= node["x"] <= g["x"] + g["w"]
                     and g["y"] <= node["y"] <= g["y"] + g["h"] - 20), None)
        inside.append(bool(band))
    check("every laid-out node sits inside a band", len(boxed) == 3 and all(inside),
          str([(n["id"], n["x"], n["y"]) for n in boxed]))
    gaps = [float(layers[i + 1]["y"]) - (float(layers[i]["y"]) + float(layers[i]["h"]))
            for i in range(len(layers) - 1)]
    check("bands do not overlap", all(gap > 0 for gap in gaps), str(gaps))

    print("bus geometry")
    buses = [e for e in board["edges"] if e.get("waypoints")]
    check("bus produced edges with waypoints", len(buses) == 2, str(len(buses)))
    trunks = {round(float(e["waypoints"][0][1]), 3) for e in buses}
    check("all bus edges share one trunk", len(trunks) == 1, str(trunks))
    check("labels ride the stub, not the trunk",
          all(e.get("label_at") in (None, "first", "last") for e in buses))
    ctx.tools["board_apply"]["handler"]({"id": "test", "ops": [
        {"op": "add_node", "id": "extra2", "text": "extra2"},
        {"op": "add_edge", "from": "api", "to": "extra2"},
        {"op": "graph", "direction": "right", "bands": True}]})
    graphed = store.load_board("test")
    boxes = [n for n in graphed["nodes"] if n.get("kind", "box") == "box"]
    keys = {n["id"]: n for n in boxes}
    check("graph op places every box", all("x" in n and "y" in n for n in boxes))
    check("graph op routes edges from the ranks",
          any(e.get("route") in ("h", "v") for e in graphed["edges"] if e.get("waypoints") is None))
    check("graph op keeps captions out of the ranks",
          all(n["id"] != "cap-0" for n in boxes))
    bk_ranks = store.boardkit().rank_layers([n["id"] for n in boxes], graphed["edges"])
    rank_of = {nid: i for i, layer in enumerate(bk_ranks) for nid in layer}
    ordered_ok = all(rank_of[e["from"]] < rank_of[e["to"]]
                     for e in graphed["edges"] if e["from"] in rank_of and e["to"] in rank_of
                     and e["from"] != e["to"] and not e.get("waypoints"))
    check("graph op ranks flow along the edges", ordered_ok, str(rank_of))
    check("plain edge kept its label",
          any(e.get("label") == "sql" for e in board["edges"]))
    ctx.tools["board_apply"]["handler"]({"id": "test", "ops": [
        {"op": "delete_edge", "from": "api", "to": "db"},
        {"op": "bus", "sources": ["api"], "targets": ["db"], "direction": "v", "bidir": True}]})
    twin = [e for e in store.load_board("test")["edges"]
            if e.get("from") == "api" and e.get("to") == "db" and e.get("bidir")]
    check("a bidirectional bus marks both ends", len(twin) == 1, str(len(twin)))

    print("render")
    png = json.loads(ctx.tools["board_render"]["handler"]({"id": "test", "format": "png"}))
    if store.boardkit().find_chrome():
        check("png rendered", png["ok"] and os.path.isfile(png["path"]), str(png.get("path")))
        check("png is not empty", os.path.getsize(png["path"]) > 3000
              if os.path.isfile(png["path"]) else False)
    else:
        # No browser on this machine: say so instead of failing the suite for an unrelated reason.
        # CI asserts `command -v chromium` before running, so the check cannot silently vanish there.
        print("  skip png render — no Chrome/Chromium on PATH (install one, or run this in CI)")
    html = json.loads(ctx.tools["board_render"]["handler"]({"id": "test", "format": "html"}))
    document = open(html["path"], encoding="utf-8").read()
    bk = store.boardkit()
    check("html has no external reference", bool(bk.verify(document)))
    check("html carries the editor", "ck-selftest" in document and "ck-board-host" in document)

    print("versions and guards")
    first = store.version_of(board)
    ctx.tools["board_apply"]["handler"]({"id": "test", "ops": [
        {"op": "add_node", "id": "extra", "text": "extra"}]})
    check("version changes after an edit", store.version_of(store.load_board("test")) != first)
    try:
        ctx.tools["board_apply"]["handler"]({"id": "test", "ops": [{"op": "explode"}]})
        check("unknown op is refused", False, "no error raised")
    except ValueError as exc:
        check("unknown op is refused", "unknown op" in str(exc), str(exc))
    try:
        ctx.tools["board_apply"]["handler"]({"id": "test", "ops": [
            {"op": "add_edge", "from": "nope", "to": "db"}]})
        check("edge to a missing node is refused", False, "no error raised")
    except ValueError as exc:
        check("edge to a missing node is refused", "no node" in str(exc), str(exc))

    print("edit watch")
    # The dispatcher passes session_id; the host routes on the durable session key kept in
    # state.db — stand in for that database so the translation is covered, not assumed.
    state_db = os.path.join(TMP, "state.db")
    with sqlite3.connect(state_db) as con:
        con.execute("create table sessions (id text primary key, session_key text, "
                    "started_at text, last_activity_at text)")
        con.execute("insert into sessions values (?,?,?,?)",
                    ("20261011_002818_ce2f5d82", "agent:main:telegram:dm:379020925:203268",
                     "2026-10-11T00:28:18", "2026-10-11T12:43:00"))
    os.environ["HERMES_STATE_DB"] = state_db
    check("a session id resolves to the durable session key the host routes on",
          store.resolve_session_key("20261011_002818_ce2f5d82")
          == "agent:main:telegram:dm:379020925:203268",
          str(store.resolve_session_key("20261011_002818_ce2f5d82")))
    check("an unknown session id resolves to nothing",
          store.resolve_session_key("20260101_000000_deadbeef") is None
          and store.resolve_session_key(None) is None)

    watch = ctx.tools["board_watch"]["handler"]
    from_id = json.loads(watch({"action": "arm"}, session_id="20261011_002818_ce2f5d82"))
    check("arm turns a session id into the routable session key",
          from_id["target"].get("session_key") == "agent:main:telegram:dm:379020925:203268"
          and from_id["resolved_session_key"] == "agent:main:telegram:dm:379020925:203268",
          json.dumps(from_id["target"]))
    check("the arm reply says where the wake will go",
          "session_key" in from_id["hint"] and "allow_gateway_injection" in from_id["hint"],
          from_id["hint"])
    unresolved = json.loads(watch({"action": "arm"}, session_id="20260101_000000_deadbeef"))
    check("an unroutable session id is called out instead of silently armed",
          "no routable session key" in unresolved["hint"]
          and not unresolved["target"].get("session_key"), unresolved["hint"])

    armed = json.loads(watch({"action": "arm", "note": "test"}, session_key="sess-1"))
    check("arm captures the session key", armed["target"].get("session_key") == "sess-1",
          str(armed["target"]))
    check("armed target is visible in status",
          json.loads(watch({"action": "status"}))["status"]["armed"].get("session_key") == "sess-1")

    live = store.load_board("test")
    live["nodes"][0]["x"] = float(live["nodes"][0].get("x", 0)) + 60
    live["nodes"].append({"id": "cache", "text": "cache", "x": 10, "y": 10})
    version, event = store.write_board("test", live, origin="pane", notify=True)
    check("a pane edit is logged as a user edit",
          bool(event) and event["origin"] == "pane" and "moved" in event["summary"],
          json.dumps(event and event.get("summary")))
    check("the edit names what changed",
          "added 1 box(es) (cache)" in event["summary"], event["summary"])
    check("the armed session was woken",
          len(ctx.injections) == 1 and ctx.injections[0].get("session_key") == "sess-1",
          json.dumps(ctx.injections))
    check("the wake says which board and what happened",
          event["summary"] in ctx.injections[0]["content"]
          and "test" in ctx.injections[0]["content"], ctx.injections[0]["content"])
    check("the wake is recorded on the event", event.get("wake", {}).get("ok") is True,
          json.dumps(event.get("wake")))

    before = json.loads(watch({"action": "read"}))["pending"]
    store.write_board("test", store.load_board("test"), origin="pane", notify=True)
    replay = json.loads(watch({"action": "read"}))
    check("re-saving an unchanged board logs nothing and wakes nobody",
          len(ctx.injections) == 1 and replay["pending"] == before,
          f"wakes={len(ctx.injections)} pending {before}->{replay['pending']}")

    agent_edit = json.loads(ctx.tools["board_apply"]["handler"]({"id": "test", "ops": [
        {"op": "add_node", "id": "agent-note", "text": "agent"}]}))
    check("an agent edit is logged but does not wake the session",
          agent_edit["logged"] and len(ctx.injections) == 1, str(len(ctx.injections)))
    check("the log distinguishes agent edits from user edits",
          json.loads(watch({"action": "read"}))["events"][-1]["origin"] == "agent")

    pending = json.loads(watch({"action": "read"}))
    check("read returns the pending edits oldest first",
          [e["origin"] for e in pending["events"]][-2:] == ["pane", "agent"],
          json.dumps([e["origin"] for e in pending["events"]]))
    total = len(store.read_events("test", include_acked=True)["events"])
    acked = json.loads(watch({"action": "read", "ack": True}))
    check("ack drains the queue",
          acked["acked"] and json.loads(watch({"action": "read"}))["pending"] == 0)
    check("acked edits are still readable on request",
          len(store.read_events("test", include_acked=True)["events"]) == total,
          str(total))

    refused = FakeCtx(allow_injection=False)
    sys.modules["hermes_board.runtime"].bind(refused)
    moved = store.load_board("test")
    moved["nodes"][1]["y"] = float(moved["nodes"][1].get("y", 0)) + 50
    _, blocked = store.write_board("test", moved, origin="pane", notify=True)
    check("a refused injection is reported, not raised",
          blocked["wake"]["ok"] is False
          and "allow_gateway_injection" in blocked["wake"]["detail"], json.dumps(blocked["wake"]))
    check("the refused edit still reaches the log",
          json.loads(watch({"action": "read"}))["pending"] == 1)

    # The host refuses session_key and origin together ("pass one, not both"), so a target that
    # carries both must still send exactly one - the chat, which is the narrower route.
    sys.modules["hermes_board.runtime"].bind(ctx)
    ctx.injections.clear()
    watch({"action": "arm"}, session_key="sess-1", origin={"platform": "telegram", "chat_id": "42"})
    chatty = store.load_board("test")
    chatty["nodes"][1]["x"] = float(chatty["nodes"][1].get("x", 0)) + 30
    _, chat_event = store.write_board("test", chatty, origin="pane", notify=True)
    check("a chat wake sends the origin alone, never both routes",
          len(ctx.injections) == 1 and "origin" in ctx.injections[0]
          and "session_key" not in ctx.injections[0], json.dumps(ctx.injections[:1]))
    check("the origin sent is the one the host passed",
          ctx.injections[0]["origin"].get("chat_id") == "42", json.dumps(ctx.injections[:1]))
    check("the wake result says where it went",
          chat_event["wake"]["sent"] == {"origin": "telegram"}, json.dumps(chat_event["wake"]))

    check("disarm clears the target",
          json.loads(watch({"action": "disarm"}))["disarmed"]
          and json.loads(watch({"action": "status"}))["status"]["armed"] is None)
    ctx.injections.clear()
    _, loud = store.write_board("test", live | {"title": "Renamed"}, origin="pane", notify=True)
    check("nothing armed still tries the CLI/REPL route with no target",
          len(ctx.injections) == 1 and ctx.injections[0] == {"content": ctx.injections[0]["content"]}
          and loud["wake"]["ok"] is True and loud["wake"]["sent"] == {},
          f"wakes={len(ctx.injections)} sent={loud['wake'].get('sent')}")
    check("the edit is logged either way",
          loud["changes"]["title"][1] == "Renamed" and "retitled" in loud["summary"],
          str(loud.get("changes", {}).get("title")))

    print("storage safety")
    path = store._path("../../etc/passwd")
    check("board id cannot escape the board dir",
          os.path.abspath(path).startswith(os.path.abspath(store.BOARD_DIR)), path)
    listed = json.loads(ctx.tools["board_list"]["handler"]({}))
    check("board_list sees the board", [b["id"] for b in listed["boards"]] == ["test"],
          str(listed["boards"]))
    summary = json.loads(ctx.tools["board_get"]["handler"]({"id": "test"}))
    check("board_get returns a summary with ids",
          {"ui", "api", "db"} <= {n["id"] for n in summary["summary"]["nodes"]})
    deleted = json.loads(ctx.tools["board_delete"]["handler"]({"id": "test"}))
    check("board_delete removes it", deleted["ok"] and not os.path.isfile(path))

    # ------------------------------------------------------------------ v1.2 primitives (lane C)
    print("v1.2 — the new vocabulary")
    bk = store.boardkit()
    if hasattr(bk, "HEADS"):
        check("store.HEADS matches the renderer's HEADS (R6.1)",
              tuple(bk.HEADS) == store.HEADS, f"{bk.HEADS} != {store.HEADS}")
    else:
        print("  skip store.HEADS vs boardkit.HEADS — this renderer predates v1.2 "
              "(lane A defines HEADS; the check is live once the lanes are merged)")
    three = ["a", "b", "c"]
    check("the auto height of a 3-part split box counts the header row (R8)",
          store.split_height("Steps", three) == 116, str(store.split_height("Steps", three)))
    check("the auto height without header text drops that row (R8)",
          store.split_height("", three) == 88, str(store.split_height("", three)))

    print("v1.2 — ops write the new fields")
    apply12 = ctx.tools["board_apply"]["handler"]
    json.loads(ctx.tools["board_new"]["handler"]({"id": "v12", "title": "v1.2"}))
    applied = json.loads(apply12({"id": "v12", "ops": [
        {"op": "add_node", "id": "steps", "text": "Steps", "parts": ["parse", "rank", "render"]},
        {"op": "add_node", "id": "cols", "text": "Boxes", "parts": ["west", "east"],
         "split": "columns"},
        {"op": "add_node", "id": "lefty", "text": "Left", "align": "left"},
        {"op": "add_node", "id": "mid", "text": "Centre", "align": "center"},
        {"op": "add_node", "id": "sized", "text": "Sized", "parts": ["one"], "h": 140},
        {"op": "add_node", "id": "bare", "parts": ["a", "b", "c"]},
        {"op": "add_edge", "from": "steps", "to": "cols", "start": "open", "end": "triangle"},
        {"op": "add_edge", "from": "cols", "to": "lefty", "bidir": True},
        {"op": "add_edge", "from": "lefty", "to": "mid"},
        {"op": "set_grid", "size": 20, "on": True, "style": "dots", "snap": False},
    ]}))
    board12 = store.load_board("v12")
    nodes = {n["id"]: n for n in board12["nodes"]}
    check("add_node with parts sizes the box to fit them (R8)",
          nodes["steps"]["h"] == 116 and nodes["steps"]["w"] == 190
          and nodes["cols"]["h"] == 92, f'{nodes["steps"]["h"]} {nodes["cols"]["h"]}')
    check("an explicit h is left alone (R8)", nodes["sized"]["h"] == 140,
          str(nodes["sized"]["h"]))
    check("a headerless split box can be created through board_apply (R4/R8)",
          nodes["bare"]["text"] == "" and
          nodes["bare"]["parts"] == [{"text": "a"}, {"text": "b"}, {"text": "c"}]
          and nodes["bare"]["h"] == 88, json.dumps(nodes["bare"]))
    try:
        apply12({"id": "v12", "ops": [{"op": "add_node", "id": "nameless"}]})
        check("a plain box still needs its text (R8)", False, "no error raised")
    except ValueError as exc:
        check("a plain box still needs its text (R8)", "missing text" in str(exc), str(exc))
    check("a bare string part is stored as a {text} object, sub kept (R1/R8)",
          nodes["steps"]["parts"] == [{"text": "parse"}, {"text": "rank"}, {"text": "render"}],
          json.dumps(nodes["steps"].get("parts")))
    check("split rows is the default and stays absent (R1/R8)",
          "split" not in nodes["steps"] and nodes["cols"]["split"] == "columns",
          json.dumps(nodes["cols"].get("split")))
    check("align left is written, align center stays absent (R1/R8)",
          nodes["lefty"]["align"] == "left" and "align" not in nodes["mid"],
          json.dumps(nodes["mid"].get("align")))
    edges12 = {(e["from"], e["to"]): e for e in board12["edges"]}
    check("an edge stores the heads it was given (R6.3/R8)",
          edges12[("steps", "cols")].get("start") == "open"
          and edges12[("steps", "cols")].get("end") == "triangle",
          json.dumps(edges12[("steps", "cols")]))
    check("bidir stores the start it stands for, and no end (R8)",
          edges12[("cols", "lefty")].get("start") == "arrow"
          and "end" not in edges12[("cols", "lefty")], json.dumps(edges12[("cols", "lefty")]))
    check("an edge that asked for no heads keeps today's shape (R6.4)",
          "start" not in edges12[("lefty", "mid")] and "end" not in edges12[("lefty", "mid")],
          json.dumps(edges12[("lefty", "mid")]))
    check("set_grid writes the grid fields (R5.1/R8)",
          board12["grid"] == 20 and board12["grid_on"] is True
          and board12["grid_style"] == "dots" and board12["snap"] is False,
          json.dumps({k: board12.get(k) for k in ("grid", "grid_on", "grid_style", "snap")}))
    check("set_grid answers with a human line (R8)",
          applied["did"][-1] == "grid: on, 20px, dots", str(applied["did"][-1]))

    print("v1.2 — validation")
    bad_ops = [{"op": "set_grid", "size": 0}, {"op": "set_grid", "size": -4},
               {"op": "set_grid", "size": "wide"}, {"op": "set_grid", "style": "zigzag"},
               {"op": "set_grid", "on": "maybe"}, {"op": "update_node", "id": "steps", "parts": 7},
               {"op": "add_edge", "from": "steps", "to": "cols", "end": "chevron"},
               {"op": "add_edge", "from": "steps", "to": "cols", "start": "both"},
               {"op": "update_edge", "from": "steps", "to": "cols", "end": "chevron"}]
    for bad in bad_ops:
        try:
            apply12({"id": "v12", "ops": [bad]})
            check(f"{bad['op']} refuses {bad}", False, "no error raised")
        except ValueError as exc:
            check(f"{bad['op']} refuses {bad}",
                  any(word in str(exc) for word in ("valid heads", "valid styles", "positive number",
                                                    "true or false", "list of parts")), str(exc))
    try:
        apply12({"id": "v12", "ops": [{"op": "add_edge", "from": "steps", "to": "cols",
                                       "end": "chevron"}]})
        check("an unknown head names the valid list (R8)", False, "no error raised")
    except ValueError as exc:
        check("an unknown head names the valid list (R8)",
              "chevron" in str(exc) and all(head in str(exc) for head in store.HEADS), str(exc))

    print("v1.2 — update_node / update_edge")
    apply12({"id": "v12", "ops": [
        {"op": "update_node", "id": "steps", "parts": []},
        {"op": "update_node", "id": "cols", "split": "rows", "align": "left"},
        {"op": "update_node", "id": "mid", "parts": ["only"], "split": "columns"},
        {"op": "update_node", "id": "mid", "parts": None},
        {"op": "update_node", "id": "sized", "align": "center"},
        {"op": "add_node", "id": "lines", "text": "Lines", "parts": "a\n\nb"},
        {"op": "update_edge", "from": "cols", "to": "lefty", "bidir": False},
        {"op": "update_edge", "from": "steps", "to": "cols", "start": "none"},
    ]})
    after12 = store.load_board("v12")
    nodes = {n["id"]: n for n in after12["nodes"]}
    edges12 = {(e["from"], e["to"]): e for e in after12["edges"]}
    check("update_node with parts: [] clears the split (R8)",
          "parts" not in nodes["steps"], json.dumps(nodes["steps"]))
    check("update_node writes parts and a split direction back (R8)",
          nodes["mid"]["parts"] == [{"text": "only"}] and nodes["mid"]["split"] == "columns",
          json.dumps(nodes["mid"]))
    check("update_node with parts: null leaves the split where it was (R8)",
          nodes["mid"]["parts"] == [{"text": "only"}], json.dumps(nodes["mid"]))
    check("one string with newlines is read as one part per line, blanks dropped (R1/R8)",
          nodes["lines"]["parts"] == [{"text": "a"}, {"text": "b"}] and nodes["lines"]["h"] == 92,
          json.dumps(nodes["lines"]))
    check("update_node normalises split back to the default and keeps align (R1/R8)",
          "split" not in nodes["cols"] and nodes["cols"]["align"] == "left",
          json.dumps(nodes["cols"]))
    check("update_node takes align back to center (R1/R8)",
          "align" not in nodes["sized"], json.dumps(nodes["sized"]))
    check("turning bidir off drops the arrow it stood for (R8)",
          "start" not in edges12[("cols", "lefty")] and edges12[("cols", "lefty")]["bidir"] is False,
          json.dumps(edges12[("cols", "lefty")]))
    check("a head is taken away by setting it to none (R6.1)",
          edges12[("steps", "cols")].get("start") == "none"
          and edges12[("steps", "cols")].get("end") == "triangle", json.dumps(edges12[("steps", "cols")]))

    print("v1.2 — the summary the model reads back")
    summary12 = json.loads(ctx.tools["board_get"]["handler"]({"id": "v12"}))["summary"]
    by_id = {n["id"]: n for n in summary12["nodes"]}
    check("the summary reports the grid block (R8)",
          summary12["grid"] == {"on": True, "size": 20, "style": "dots", "snap": False},
          json.dumps(summary12.get("grid")))
    check("the summary reports align and the parts count per node (R8)",
          by_id["cols"]["align"] == "left" and by_id["mid"]["parts"] == 1
          and by_id["steps"]["parts"] == 0, json.dumps([by_id["cols"], by_id["mid"], by_id["steps"]]))
    check("the summary reports the effective heads per edge (R8)",
          any(e["start"] == "none" and e["end"] == "triangle" for e in summary12["edges"]),
          json.dumps(summary12["edges"]))

    print("v1.2 — the diff says what changed (and stays quiet when nothing did)")
    before = {"title": "d",
              "nodes": [{"id": "n1", "x": 0, "y": 0, "w": 190, "h": 116, "text": "Steps",
                         "parts": ["parse", "rank"], "split": "rows"},
                        {"id": "n2", "x": 0, "y": 200, "w": 190, "h": 72, "text": "B"}],
              "edges": [{"from": "n1", "to": "n2", "end": "open"}]}
    after = json.loads(json.dumps(before))
    after["nodes"][0]["parts"] = ["parse", "rank", "render"]
    after["nodes"][0]["align"] = "left"
    after["nodes"][1]["parts"] = ["x"]
    after["edges"][0]["end"] = "diamond"
    after["grid"], after["grid_on"], after["grid_style"] = 20, True, "dots"
    changes = store.diff_boards(before, after)
    check("a box that gained a compartment reads as 'split into 3 parts' (R8)",
          "split into 3 parts (n1)" in changes["summary"], changes["summary"])
    check("a new split is described too (R8)",
          "split into 1 part (n2)" in changes["summary"], changes["summary"])
    check("an align edit reads as 'align left' (R8)",
          "align left (n1)" in changes["summary"], changes["summary"])
    check("a head edit reads as 'head open -> diamond' (R8)",
          "head open -> diamond (n1→n2)" in changes["summary"], changes["summary"])
    check("a grid edit reads as 'grid on, 20px, dots' (R8)",
          "grid on, 20px, dots" in changes["summary"], changes["summary"])
    check("the diff carries the new edits as data, not only as a sentence (R8)",
          changes["nodes"]["split"] == ["n1", "n2"]
          and changes["nodes"]["realigned"] == {"left": ["n1"]}
          and changes["edges"]["heads"][0]["end"] == ["open", "diamond"]
          and changes["grid"]["changed"] and changes["grid"]["snap"] is False,
          json.dumps({k: changes[k] for k in ("nodes", "grid")}))
    check("a split that went away reads as 'removed the split' (R8)",
          "removed the split (n2)" in store.diff_boards(after, before)["summary"],
          store.diff_boards(after, before)["summary"])
    twin = json.loads(json.dumps(before))
    twin["nodes"][0]["parts"] = ["one", "two"]          # same count, different words
    check("rewriting the parts is not reported as a resize (R8)",
          "rewrote the parts of 1 box(es) (n1)" in store.diff_boards(before, twin)["summary"],
          store.diff_boards(before, twin)["summary"])
    spelled = json.loads(json.dumps(before))
    spelled["grid_on"], spelled["grid_style"], spelled["snap"] = False, "lines", True
    check("writing the grid defaults out in full is not an edit (R5.1)",
          store.diff_boards(before, spelled)["changed"] is False,
          store.diff_boards(before, spelled)["summary"])
    spelled["edges"][0]["start"] = "none"                # the effective head, written out
    check("spelling out the effective heads is not an edit (R6.1)",
          store.diff_boards(before, spelled)["changed"] is False,
          store.diff_boards(before, spelled)["summary"])
    check("an identical re-save has nothing to report (R8)",
          store.diff_boards(before, json.loads(json.dumps(before)))["changed"] is False)
    live12 = store.load_board("v12")
    live12["grid_on"] = False
    _, grid_event = store.write_board("v12", live12, origin="pane")
    check("a grid-only edit is logged, with the grid line (R8)",
          bool(grid_event) and "grid off, 20px, dots" in grid_event["summary"],
          json.dumps(grid_event and grid_event.get("summary")))
    check("re-saving the same board again logs nothing at all (R8)",
          store.write_board("v12", store.load_board("v12"), origin="pane")[1] is None)
    ctx.tools["board_delete"]["handler"]({"id": "v12"})

    # ------------------------------------------------------------------ v1.3 edges v2 (lane O)
    print("v1.3 — the new vocabulary")
    check("the side vocabulary is the spec's, in order (W4)",
          store.SIDES == ("auto", "left", "right", "top", "bottom"), str(store.SIDES))
    check("the head size vocabulary is the spec's, in order (W4)",
          store.HEAD_SIZES == ("small", "medium", "large"), str(store.HEAD_SIZES))
    if hasattr(bk, "SIDES"):
        check("store.SIDES matches the renderer's SIDES (W3.1)",
              set(bk.SIDES) == set(store.SIDES), f"{bk.SIDES} != {store.SIDES}")
    else:
        print("  skip store.SIDES vs boardkit.SIDES — this renderer predates v1.3 (lane E defines "
              "it; the check is live once the lanes are merged)")
    if hasattr(bk, "HEAD_SIZES"):
        check("store.HEAD_SIZES matches the renderer's HEAD_SIZES (W3.3)",
              set(bk.HEAD_SIZES) == set(store.HEAD_SIZES), f"{bk.HEAD_SIZES} != {store.HEAD_SIZES}")
    else:
        print("  skip store.HEAD_SIZES vs boardkit.HEAD_SIZES — this renderer predates v1.3 "
              "(lane E defines it; the check is live once the lanes are merged)")
    desc13 = ctx.tools["board_apply"]["schema"]["description"]
    check("OPS_HELP documents the three new fields (W4)",
          all(word in desc13 for word in ("from_side", "to_side", "head_size",
                                          "auto|left|right|top|bottom", "small|medium|large")),
          desc13[-160:])

    print("v1.3 — ops write the new fields")
    apply13 = ctx.tools["board_apply"]["handler"]
    json.loads(ctx.tools["board_new"]["handler"]({"id": "v13", "title": "v1.3"}))
    json.loads(apply13({"id": "v13", "ops": [
        {"op": "add_node", "id": "a", "text": "A"},
        {"op": "add_node", "id": "b", "text": "B"},
        {"op": "add_node", "id": "c", "text": "C"},
        {"op": "add_edge", "from": "a", "to": "b", "from_side": "right", "to_side": "top",
         "head_size": "large"},
        {"op": "add_edge", "from": "b", "to": "c"},
        {"op": "add_edge", "from": "a", "to": "c", "from_side": "auto", "head_size": "medium"},
        {"op": "add_edge", "from": "c", "to": "a", "from_side": "left"},
        {"op": "update_edge", "from": "c", "to": "a", "to_side": "bottom"},
    ]}))
    board13 = store.load_board("v13")
    edges13 = {(e["from"], e["to"]): e for e in board13["edges"]}
    check("add_edge writes from_side/to_side/head_size onto the edge (W3.1/W4)",
          edges13[("a", "b")].get("from_side") == "right"
          and edges13[("a", "b")].get("to_side") == "top"
          and edges13[("a", "b")].get("head_size") == "large", json.dumps(edges13[("a", "b")]))
    check("an edge that asked for none of them keeps today's shape (W3.1)",
          all(k not in edges13[("b", "c")] for k in ("from_side", "to_side", "head_size")),
          json.dumps(edges13[("b", "c")]))
    check('an explicit "auto"/"medium" is stored as given, not dropped (W4)',
          edges13[("a", "c")].get("from_side") == "auto"
          and edges13[("a", "c")].get("head_size") == "medium", json.dumps(edges13[("a", "c")]))
    check("update_edge writes the side it was given and leaves the other end alone (W4)",
          edges13[("c", "a")].get("from_side") == "left"
          and edges13[("c", "a")].get("to_side") == "bottom", json.dumps(edges13[("c", "a")]))

    print("v1.3 — validation")
    bad13 = [{"op": "add_edge", "from": "a", "to": "b", "from_side": "middle"},
             {"op": "add_edge", "from": "a", "to": "b", "to_side": "centre"},
             {"op": "add_edge", "from": "a", "to": "b", "head_size": "huge"},
             {"op": "update_edge", "from": "a", "to": "b", "to_side": "middle"},
             {"op": "update_edge", "from": "a", "to": "b", "head_size": "jumbo"}]
    for bad in bad13:
        try:
            apply13({"id": "v13", "ops": [bad]})
            check(f"{bad['op']} refuses an unknown side/size {bad}", False, "no error raised")
        except ValueError as exc:
            check(f"{bad['op']} refuses an unknown side/size {bad}",
                  "valid sides" in str(exc) or "valid sizes" in str(exc), str(exc))
    try:
        apply13({"id": "v13", "ops": [{"op": "add_edge", "from": "a", "to": "b",
                                       "from_side": "middle"}]})
        check("an unknown side names the valid list (W4)", False, "no error raised")
    except ValueError as exc:
        check("an unknown side names the valid list (W4)",
              "middle" in str(exc) and all(side in str(exc) for side in store.SIDES), str(exc))
    try:
        apply13({"id": "v13", "ops": [{"op": "update_edge", "from": "a", "to": "b",
                                       "head_size": "huge"}]})
        check("an unknown head size names the valid list (W4)", False, "no error raised")
    except ValueError as exc:
        check("an unknown head size names the valid list (W4)",
              "huge" in str(exc) and all(size in str(exc) for size in store.HEAD_SIZES), str(exc))

    print("v1.3 — a side goes back to auto")
    json.loads(apply13({"id": "v13", "ops": [
        {"op": "update_edge", "from": "a", "to": "c", "from_side": "left", "head_size": "large"},
        {"op": "update_edge", "from": "a", "to": "b", "from_side": "auto", "to_side": "auto"},
        {"op": "update_edge", "from": "a", "to": "c", "from_side": "auto", "head_size": "medium"},
    ]}))
    reset13 = {(e["from"], e["to"]): e for e in store.load_board("v13")["edges"]}
    check('update_edge stores "auto" as given and the edge is back on the default (W4)',
          reset13[("a", "b")].get("from_side") == "auto" and reset13[("a", "b")].get("to_side") == "auto"
          and (store.side_of(reset13[("a", "b")]), store.side_of(reset13[("a", "b")], "to_side"))
          == ("auto", "auto"), json.dumps(reset13[("a", "b")]))
    check("resetting a side leaves the fields that op did not mention alone (W4)",
          reset13[("a", "b")].get("head_size") == "large", json.dumps(reset13[("a", "b")]))
    check('the head size goes back to the default the same way (W4)',
          reset13[("a", "c")].get("head_size") == "medium"
          and store.head_size_of(reset13[("a", "c")]) == "medium"
          and reset13[("a", "c")].get("from_side") == "auto", json.dumps(reset13[("a", "c")]))

    print("v1.3 — the shared fixture reads with the defaults filled in")
    fixture13 = store.boardkit().load(str(ROOT / "docs" / "fixture-v1.3.json"))
    by_pair = {(e["from"], e["to"]): e for e in fixture13["edges"]}
    check("an explicit side/size reads as written (W3.1)",
          store.side_of(by_pair[("e", "a")]) == "top" and store.side_of(by_pair[("e", "a")], "to_side") == "left"
          and store.head_size_of(by_pair[("e", "a")]) == "large", json.dumps(by_pair[("e", "a")]))
    check("an unknown side/size reads as the default, never raw (W3.1)",
          store.side_of(by_pair[("f", "d")]) == "auto"
          and store.side_of(by_pair[("f", "d")], "to_side") == "auto"
          and store.head_size_of(by_pair[("f", "d")]) == "medium", json.dumps(by_pair[("f", "d")]))
    check("an edge with neither field reads as the default pair (W3.1)",
          store.side_of(by_pair[("c", "f")]) == "auto" and store.head_size_of(by_pair[("c", "f")]) == "medium",
          json.dumps(by_pair[("c", "f")]))

    print("v1.3 — the summary the model reads back")
    summary13 = json.loads(ctx.tools["board_get"]["handler"]({"id": "v13"}))["summary"]
    by_edge = {(e["from"], e["to"]): e for e in summary13["edges"]}
    check("the summary reports the effective sides and head size per edge (W4)",
          by_edge[("a", "b")]["from_side"] == "auto" and by_edge[("a", "b")]["to_side"] == "auto"
          and by_edge[("a", "b")]["head_size"] == "large", json.dumps(by_edge[("a", "b")]))
    check("an edge an op reset is reported back on the defaults (W4)",
          by_edge[("a", "c")]["from_side"] == "auto" and by_edge[("a", "c")]["to_side"] == "auto"
          and by_edge[("a", "c")]["head_size"] == "medium", json.dumps(by_edge[("a", "c")]))
    check("an edge that never mentioned them is reported on the defaults too (W4)",
          by_edge[("b", "c")]["from_side"] == "auto" and by_edge[("b", "c")]["to_side"] == "auto"
          and by_edge[("b", "c")]["head_size"] == "medium", json.dumps(by_edge[("b", "c")]))
    check("a side an op set is reported as such (W4)",
          by_edge[("c", "a")]["from_side"] == "left" and by_edge[("c", "a")]["to_side"] == "bottom",
          json.dumps(by_edge[("c", "a")]))

    print("v1.3 — the diff says what changed (and stays quiet when nothing did)")
    base13 = {"title": "d", "nodes": [{"id": "a", "x": 0, "y": 0, "w": 190, "h": 72, "text": "A"},
                                      {"id": "b", "x": 300, "y": 0, "w": 190, "h": 72, "text": "B"}],
              "edges": [{"from": "a", "to": "b"}]}
    sided = json.loads(json.dumps(base13))
    sided["edges"][0].update({"from_side": "right", "to_side": "top"})
    side_changes = store.diff_boards(base13, sided)
    check("a side edit reads as 'edge a→b attaches right→top' (W4)",
          "edge a→b attaches right→top" in side_changes["summary"], side_changes["summary"])
    check("the side edit is data too, not only a sentence (W4)",
          side_changes["edges"]["sides"][0]["from_side"] == ["auto", "right"]
          and side_changes["edges"]["sides"][0]["to_side"] == ["auto", "top"],
          json.dumps(side_changes["edges"]))
    one = json.loads(json.dumps(base13))
    one["edges"][0]["to_side"] = "bottom"
    check("a single-end edit names the pair it ends up on (W4)",
          "edge a→b attaches auto→bottom" in store.diff_boards(base13, one)["summary"],
          store.diff_boards(base13, one)["summary"])
    bigger = json.loads(json.dumps(sided))
    bigger["edges"][0]["head_size"] = "large"
    size_changes = store.diff_boards(sided, bigger)
    check("a head size edit reads as 'edge a→b head size large' (W4)",
          "edge a→b head size large" in size_changes["summary"], size_changes["summary"])
    check("the head size edit is data too (W4)",
          size_changes["edges"]["head_size"][0]["head_size"] == ["medium", "large"],
          json.dumps(size_changes["edges"]))
    spelled = json.loads(json.dumps(base13))
    spelled["edges"][0].update({"from_side": "auto", "to_side": "auto", "head_size": "medium"})
    check("spelling the effective sides/size out in full is not an edit (W3.1/W4)",
          store.diff_boards(base13, spelled)["changed"] is False,
          store.diff_boards(base13, spelled)["summary"])
    junk = json.loads(json.dumps(base13))
    junk["edges"][0].update({"from_side": "middle", "head_size": "huge"})
    check("an unknown side/size is the default, so it is not an edit either (W3.1)",
          store.diff_boards(base13, junk)["changed"] is False,
          store.diff_boards(base13, junk)["summary"])

    live13 = store.load_board("v13")
    for edge in live13["edges"]:
        if (edge["from"], edge["to"]) == ("b", "c"):
            edge.update({"from_side": "left", "to_side": "right", "head_size": "small"})
    _, side_event = store.write_board("v13", live13, origin="pane")
    check("a pane edit to the sides logs both new lines (W4)",
          bool(side_event) and "edge b→c attaches left→right" in side_event["summary"]
          and "edge b→c head size small" in side_event["summary"],
          json.dumps(side_event and side_event.get("summary")))
    check("re-saving the same board logs nothing at all (W4)",
          store.write_board("v13", store.load_board("v13"), origin="pane")[1] is None)
    spelled13 = store.load_board("v13")
    for edge in spelled13["edges"]:
        edge.setdefault("from_side", "auto")
        edge.setdefault("to_side", "auto")
        edge.setdefault("head_size", "medium")
    check("a re-save that only writes the defaults out in full logs nothing either (W4)",
          store.write_board("v13", spelled13, origin="pane")[1] is None
          and store.diff_boards(store.load_board("v13"), spelled13)["changed"] is False)
    ctx.tools["board_delete"]["handler"]({"id": "v13"})

    # ------------------------------------------------------------------ v1.4 content (lane O)
    print("v1.4 — the new vocabulary")
    check("the text size vocabulary is the spec's, in order (X8)",
          store.TEXT_SIZES == ("small", "default", "large"), str(store.TEXT_SIZES))
    check("the kind vocabulary carries the table and every kind the renderer knows (X8/X3.1)",
          "table" in store.KINDS and all(k in store.KINDS for k in bk.KINDS),
          str(store.KINDS))
    # Y4.6 (v1.4 gate round 1): `rows` counts EVERY row and row 0 is the header band, so a 3-row
    # table with a header is 28 + 24*2 = 76 tall — not 28 + 24*3 (which left y = 0..28 empty).
    check("the table's own geometry is the spec's, not the box builder's (Y4.6/X8)",
          store.table_size(3, 2, True) == (180, 76) and store.table_size(3, 2, False) == (180, 72)
          and store.table_size(2, 2, False) == (180, 48) and store.table_size(1, 3, True) == (270, 28),
          str((store.table_size(3, 2, True), store.table_size(3, 2, False))))
    desc14 = ctx.tools["board_apply"]["schema"]["description"]
    check("OPS_HELP documents the table and the text fields (X8)",
          all(word in desc14 for word in ("rows?,cols?,cells?,header?,text_size?,bold?",
                                          "small|default|large", "table")), desc14[-220:])

    print("v1.4 — a table is a shape an op can write")
    apply14 = ctx.tools["board_apply"]["handler"]
    json.loads(ctx.tools["board_new"]["handler"]({"id": "v14", "title": "v1.4"}))
    json.loads(apply14({"id": "v14", "ops": [
        # The round-1 disposition: a textless, headerless table must be writable (X8/F2).
        {"op": "add_node", "id": "t1", "kind": "table", "rows": 3, "cols": 2},
        {"op": "add_node", "id": "t2", "kind": "table", "text": "", "rows": 1, "cols": 3,
         "header": True, "cells": ["a", "b", "c", "extra"]},
        {"op": "add_node", "id": "auto", "kind": "table", "rows": 4, "cols": 1, "w": 0, "h": 0},
        {"op": "add_node", "id": "big", "text": "Large", "text_size": "large", "bold": True},
        {"op": "add_node", "id": "small", "text": "Small", "text_size": "small"},
        {"op": "add_node", "id": "plain", "text": "Plain"},
    ]}))
    board14 = store.load_board("v14")
    n14 = {n["id"]: n for n in board14["nodes"]}
    check("a textless, headerless table is writable through board_apply (X8/F2)",
          n14["t1"]["kind"] == "table" and n14["t1"]["text"] == "" and "sub" not in n14["t1"],
          json.dumps(n14["t1"]))
    check("a table is NOT given the box builder's 190x72 (X3.3/X8)",
          (n14["t1"]["w"], n14["t1"]["h"]) == (180, 72)
          and not (n14["t1"]["w"] == 190 and n14["t1"]["h"] == 72), json.dumps(n14["t1"]))
    check("the table's cells are normalised to exactly rows*cols (X3.1/X8)",
          n14["t1"]["cells"] == [""] * 6 and n14["t2"]["cells"] == ["a", "b", "c"],
          json.dumps([n14["t1"]["cells"], n14["t2"]["cells"]]))
    check("header: true is written and a headerless table keeps the default absent (X3.1/X8)",
          n14["t2"]["header"] is True and "header" not in n14["t1"], json.dumps(n14["t2"]))
    check("a one-row header table is exactly one header band tall (Y4.6)",
          (n14["t2"]["w"], n14["t2"]["h"]) == (270, 28), json.dumps(n14["t2"]))
    check("an op never writes a 0-sized table (X3.3)",
          (n14["auto"]["w"], n14["auto"]["h"]) == (90, 96), json.dumps(n14["auto"]))
    check("a table carries none of the split/text-format fields (X3.2/X3.3)",
          all(k not in n14["t1"] for k in ("parts", "split", "text_size", "bold", "align")),
          json.dumps(n14["t1"]))
    check("text_size/bold are written and their defaults stay absent (X4.1/X8)",
          n14["big"]["text_size"] == "large" and n14["big"]["bold"] is True
          and n14["small"]["text_size"] == "small" and "bold" not in n14["small"]
          and "text_size" not in n14["plain"], json.dumps([n14["big"], n14["small"], n14["plain"]]))

    print("v1.4 — validation")
    bad14 = [{"op": "add_node", "id": "bad", "kind": "table", "rows": 0},
             {"op": "add_node", "id": "bad", "kind": "table", "rows": -1},
             {"op": "add_node", "id": "bad", "kind": "table", "cols": "two"},
             {"op": "add_node", "id": "bad", "text": "x", "text_size": "huge"},
             {"op": "add_node", "id": "bad", "kind": "table", "header": "maybe"},
             {"op": "add_node", "id": "bad", "text": "x", "bold": "maybe"},
             {"op": "add_node", "id": "bad", "text": "x", "kind": "bogus"},
             {"op": "update_node", "id": "t1", "rows": 0},
             {"op": "update_node", "id": "t1", "text_size": "huge"},
             {"op": "update_node", "id": "t1", "header": "maybe"},
             {"op": "update_node", "id": "t1", "kind": "bogus"}]
    for bad in bad14:
        try:
            apply14({"id": "v14", "ops": [bad]})
            check(f"{bad['op']} refuses {bad}", False, "no error raised")
        except ValueError as exc:
            check(f"{bad['op']} refuses {bad}",
                  any(word in str(exc) for word in ("at least 1", "valid sizes", "true or false",
                                                    "valid kinds")), str(exc))
    try:
        apply14({"id": "v14", "ops": [{"op": "add_node", "id": "bad", "kind": "table", "rows": 0}]})
        check("a bad row count names the limit (X8)", False, "no error raised")
    except ValueError as exc:
        check("a bad row count names the limit (X8)", "at least 1" in str(exc), str(exc))
    try:
        apply14({"id": "v14", "ops": [{"op": "add_node", "id": "bad", "text": "x",
                                       "text_size": "huge"}]})
        check("an unknown text size names the valid list (X8)", False, "no error raised")
    except ValueError as exc:
        check("an unknown text size names the valid list (X8)",
              "huge" in str(exc) and all(size in str(exc) for size in store.TEXT_SIZES), str(exc))
    try:
        apply14({"id": "v14", "ops": [{"op": "add_node", "id": "bad", "text": "x",
                                       "kind": "bogus"}]})
        check("an unknown kind names the valid list (X8)", False, "no error raised")
    except ValueError as exc:
        check("an unknown kind names the valid list (X8)",
              "bogus" in str(exc) and "table" in str(exc), str(exc))
    check("a refused op list writes nothing",
          len(store.load_board("v14")["nodes"]) == 6, str(len(store.load_board("v14")["nodes"])))

    print("v1.4 — update_node on a table and on text formatting")
    json.loads(apply14({"id": "v14", "ops": [
        {"op": "update_node", "id": "t1", "cells": ["h1", "h2", "a", "b", "c", "d", "extra"],
         "header": True},
        {"op": "update_node", "id": "t2", "rows": 2},
        {"op": "update_node", "id": "big", "text_size": "small", "bold": False},
        {"op": "update_node", "id": "plain", "text_size": "large"},
        {"op": "update_node", "id": "plain", "text_size": "default"},
        {"op": "update_node", "id": "big", "kind": "table", "rows": 2, "cols": 2},
    ]}))
    after14 = store.load_board("v14")
    n14 = {n["id"]: n for n in after14["nodes"]}
    check("update_node writes the cells and re-normalises them (X8/X3.1)",
          n14["t1"]["cells"] == ["h1", "h2", "a", "b", "c", "d"] and n14["t1"]["header"] is True,
          json.dumps(n14["t1"]))
    check("update_node pads the cells when rows grows, losing no text (X3.4/X8)",
          n14["t2"]["rows"] == 2 and n14["t2"]["cells"] == ["a", "b", "c", "", "", ""]
          and n14["t2"]["header"] is True, json.dumps(n14["t2"]))
    check("update_node clears bold and writes a smaller size (X4.1/X8)",
          "bold" not in n14["plain"] and "bold" not in n14["small"], json.dumps(n14["small"]))
    check("text_size: default puts a node back on the default (X4.4/X8)",
          "text_size" not in n14["plain"], json.dumps(n14["plain"]))
    check("a node turned into a table drops its text formatting and gains the table's fields (X8)",
          n14["big"]["kind"] == "table" and n14["big"]["rows"] == 2 and n14["big"]["cols"] == 2
          and n14["big"]["cells"] == [""] * 4
          and all(k not in n14["big"] for k in ("text_size", "bold", "parts", "split")),
          json.dumps(n14["big"]))

    print("v1.4 — the summary the model reads back")
    sum14 = json.loads(ctx.tools["board_get"]["handler"]({"id": "v14"}))["summary"]
    by14 = {n["id"]: n for n in sum14["nodes"]}
    check("the summary reports rows/cols/header/text_size/bold per node (X8)",
          by14["t2"]["rows"] == 2 and by14["t2"]["cols"] == 3 and by14["t2"]["header"] is True
          and by14["t2"]["text_size"] == "default" and by14["t2"]["bold"] is False,
          json.dumps(by14["t2"]))
    check("the summary reads a node's own formatting as effective (X4.4/X8)",
          by14["small"]["text_size"] == "small" and by14["small"]["bold"] is False
          and by14["small"]["rows"] == 2 and by14["small"]["header"] is False,
          json.dumps(by14["small"]))

    print("v1.4 — the diff says what changed (and stays quiet when nothing did)")
    base14 = {"title": "d", "nodes": [
        {"id": "t", "x": 0, "y": 0, "w": 180, "h": 72, "text": "", "kind": "table",
         "rows": 3, "cols": 2, "cells": ["a", "b", "c", "d"]},
        {"id": "n", "x": 300, "y": 0, "w": 190, "h": 72, "text": "N"}]}
    cells14 = json.loads(json.dumps(base14))
    cells14["nodes"][0]["cells"] = ["a", "b", "c", "d", "e", "f"]
    changes14 = store.diff_boards(base14, cells14)
    check("a cell edit reads as 'table 3x2 cells changed (t)' (X8)",
          "table 3x2 cells changed (t)" in changes14["summary"], changes14["summary"])
    check("the table edit is data too, not only a sentence (X8)",
          changes14["nodes"]["table"][0]["id"] == "t"
          and changes14["nodes"]["table"][0]["cells"] is True
          and changes14["nodes"]["table"][0]["rows"] == [3, 3], json.dumps(changes14["nodes"]["table"]))
    head14 = json.loads(json.dumps(base14))
    head14["nodes"][0]["header"] = True
    check("a header edit reads as 'table header on (t)' (X8)",
          "table header on (t)" in store.diff_boards(base14, head14)["summary"],
          store.diff_boards(base14, head14)["summary"])
    size14 = json.loads(json.dumps(base14))
    size14["nodes"][1]["text_size"] = "large"
    size_changes14 = store.diff_boards(base14, size14)
    check("a size edit reads as 'text size large (n)' (X8)",
          "text size large (n)" in size_changes14["summary"], size_changes14["summary"])
    check("the size edit is data too (X8)",
          size_changes14["nodes"]["text_size"][0]["text_size"] == ["default", "large"],
          json.dumps(size_changes14["nodes"]["text_size"]))
    bold14 = json.loads(json.dumps(base14))
    bold14["nodes"][1]["bold"] = True
    bold_changes14 = store.diff_boards(base14, bold14)
    check("a weight edit reads as 'bold on (n)' (X8)",
          "bold on (n)" in bold_changes14["summary"], bold_changes14["summary"])
    check("the weight edit is data too (X8)",
          bold_changes14["nodes"]["bold"] == {"n": [False, True]},
          json.dumps(bold_changes14["nodes"]["bold"]))
    spelled14 = json.loads(json.dumps(base14))
    spelled14["nodes"][0].update({"rows": 3, "cols": 2, "header": False,
                                  "cells": ["a", "b", "c", "d", "", ""]})
    spelled14["nodes"][1].update({"text_size": "default", "bold": False})
    check("spelling the table/text defaults out in full is not an edit (X3.1/X4.4/X8)",
          store.diff_boards(base14, spelled14)["changed"] is False,
          store.diff_boards(base14, spelled14)["summary"])
    textonly14 = json.loads(json.dumps(base14))
    textonly14["nodes"][0]["text"] = "a header row this table never draws"
    check("a table's own text is not drawn, so editing it is not an edit (X3.2/X8)",
          store.diff_boards(base14, textonly14)["changed"] is False,
          store.diff_boards(base14, textonly14)["summary"])
    check("an identical re-save of a table has nothing to report either (X8)",
          store.diff_boards(base14, json.loads(json.dumps(base14)))["changed"] is False)

    live14 = store.load_board("v14")
    for node in live14["nodes"]:
        if node["id"] == "t1":
            node["cells"] = ["1", "2", "3", "4", "5", "6"]
    _, cell_event = store.write_board("v14", live14, origin="pane")
    check("a pane edit to the cells logs the table line (X8)",
          bool(cell_event) and "table 3x2 cells changed (t1)" in cell_event["summary"],
          json.dumps(cell_event and cell_event.get("summary")))
    check("re-saving the same board again logs nothing at all (X8)",
          store.write_board("v14", store.load_board("v14"), origin="pane")[1] is None)
    ctx.tools["board_delete"]["handler"]({"id": "v14"})

    # ------------------------------------------------------------------ v1.5 table v2 (lane O)
    print("v1.5 — the new vocabulary")
    check("the tone vocabulary is the spec's, in order (Y4.3/Y6)",
          store.TONES == ("default", "accent", "ok", "warn", "danger", "muted"), str(store.TONES))
    if hasattr(bk, "TONES"):
        check("store.TONES matches the renderer's TONES (Y4.3)",
              tuple(bk.TONES) == store.TONES, f"{bk.TONES} != {store.TONES}")
    else:
        print("  skip store.TONES vs boardkit.TONES — this renderer predates v1.5 "
              "(lane T defines it; the check is live once the lanes are merged)")
    check("the cell alignment tri-state is the spec's (Y4.1)",
          store.CELL_ALIGNS == ("left", "center", "right"), str(store.CELL_ALIGNS))
    check("add_table is an op the plugin knows (Y6)", "add_table" in store.OPS, str(store.OPS))
    desc15 = ctx.tools["board_apply"]["schema"]["description"]
    check("OPS_HELP documents add_table and the table v2 fields (Y6)",
          all(word in desc15 for word in ("add_table", "col_widths?", "row_tones?", "col_tones?",
                                          "{text, tone?, align?}")), desc15[-320:])

    print("v1.5 — add_table writes the document the pane would")
    apply15 = ctx.tools["board_apply"]["handler"]
    json.loads(ctx.tools["board_new"]["handler"]({"id": "v15", "title": "v1.5"}))
    table_op = {"id": "n1", "x": 40, "y": 40, "rows": 3, "cols": 2, "header": True,
                "col_widths": [2, 1], "row_tones": ["muted", None, None],
                "cells": ["name", "value", "cpu", "", "", {"text": "38%", "tone": "warn",
                                                           "align": "right"}]}
    applied15 = json.loads(apply15({"id": "v15", "ops": [
        dict(table_op, op="add_table"),
        {"op": "add_table", "id": "t2"},                    # every field at its default
        {"op": "add_table", "id": "t3", "rows": 1, "cols": 3, "header": True, "w": 0, "h": 0,
         "col_widths": [2]},
    ]}))
    board15 = store.load_board("v15")
    n15 = {n["id"]: n for n in board15["nodes"]}
    check("add_table answers 'added 3x2 table n1 at (40, 40)' (Y6)",
          applied15["did"][0] == "added 3x2 table n1 at (40, 40)", str(applied15["did"][0]))
    check("add_table resolves the table's own size, never the box's 190x72 (Y4.6/Y6)",
          (n15["n1"]["w"], n15["n1"]["h"]) == (180, 76),
          json.dumps({k: n15["n1"][k] for k in ("w", "h")}))
    check("a fully defaulted table is 2x2, headerless and auto-sized (Y6)",
          (n15["t2"]["rows"], n15["t2"]["cols"], "header" in n15["t2"],
           n15["t2"]["w"], n15["t2"]["h"]) == (2, 2, False, 180, 48), json.dumps(n15["t2"]))
    check("a 0-sized header table resolves to 28 for its one row (Y4.6/Y6)",
          (n15["t3"]["w"], n15["t3"]["h"]) == (270, 28),
          json.dumps({k: n15["t3"][k] for k in ("w", "h")}))
    check("col_widths is normalised to exactly cols positive numbers (Y4.2/Y6)",
          n15["n1"]["col_widths"] == [2, 1] and n15["t3"]["col_widths"] == [2, 1, 1],
          json.dumps([n15["n1"].get("col_widths"), n15["t3"].get("col_widths")]))
    check("row_tones is normalised to exactly rows entries (Y4.3/Y6)",
          n15["n1"]["row_tones"] == ["muted", None, None],
          json.dumps(n15["n1"].get("row_tones")))
    check("a field the op never gave stays absent, so a v1.4-shaped table stays v1.4-shaped (Y6)",
          all(key not in n15["t2"] for key in ("col_widths", "row_tones", "col_tones")),
          json.dumps(n15["t2"]))
    check("a cell object keeps only the keys it needs and its text is stringified (Y4.1/Y6)",
          n15["n1"]["cells"][5] == {"text": "38%", "tone": "warn", "align": "right"}
          and all(isinstance(cell, str) for cell in n15["n1"]["cells"][:5]),
          json.dumps(n15["n1"]["cells"]))
    check("a table's cells are still exactly rows*cols, empty ones kept (X3.1/Y6)",
          len(n15["n1"]["cells"]) == 6 and n15["n1"]["cells"][3] == "",
          json.dumps(n15["n1"]["cells"]))

    json.loads(ctx.tools["board_new"]["handler"]({"id": "v15b", "title": "twin"}))
    apply15({"id": "v15b", "ops": [dict(table_op, op="add_node", kind="table")]})
    twin15 = {n["id"]: n for n in store.load_board("v15b")["nodes"]}
    check("add_node {kind: table} writes the SAME node as add_table (Y6)",
          twin15["n1"] == n15["n1"], json.dumps(twin15.get("n1")))

    # Y6/Y4.5a (v1.5 verifier round 1): the builder the spec promises, and a table's own tri-state
    # alignment, which used to be reduced to "center" on the way in.
    _bk = store.boardkit()
    built = _bk.table("b1", 40, 40, 3, 2,
                      ["name", "value", "cpu", "12%", "", {"text": "38%", "tone": "warn",
                                                           "align": "right"}],
                      header=True, col_widths=[2, 1], row_tones=[None, "muted"], align="right")
    svg_built = _bk.render_svg(_bk.board(nodes=[built]))
    check("boardkit.table(...) builds a node the renderer draws as a table (Y6)",
          built["rows"] == 3 and built["cols"] == 2 and built["header"] is True
          and built["col_widths"] == [2.0, 1.0] and built["row_tones"] == [None, "muted", None]
          and 'width="180"' in svg_built and 'height="76"' in svg_built
          and 'text-anchor="end"' in svg_built and "var(--ck-tone-warn-fill)" in svg_built,
          json.dumps({k: v for k, v in built.items() if k in ("rows", "cols", "col_widths")}))
    check("boardkit.table(...) leaves a plain table plain (Y4.1)",
          _bk.table("b2", 0, 0, 2, 2, ["a", "b", "c", "d"])["cells"] == ["a", "b", "c", "d"]
          and "align" not in _bk.table("b3", 0, 0, 2, 2, []))
    for value, expected in (("right", "right"), ("left", "left"), ("center", None)):
        slug = "align" + value
        json.loads(ctx.tools["board_new"]["handler"]({"id": slug, "title": slug}))
        apply15({"id": slug, "ops": [
            {"op": "add_table", "id": "t", "rows": 2, "cols": 2, "cells": ["a", "b", "c", "d"],
             "align": value}]})
        stored = store.load_board(slug)["nodes"][0].get("align")
        check(f"a table's own align {value} is stored as {expected} (Y4.5a)", stored == expected, str(stored))
    # Y4.5a (v1.5 verifier ROUND 2, MAJOR): that loop only proved the ADD path — deleting the update
    # path's alignment write entirely kept this suite green, so the claimed update gate proved
    # nothing. An initially UNALIGNED table (no `align` key at all) is updated to each value: the
    # stored key AND the rendered anchor must both follow, every cell text moving with it, and
    # `center` must store nothing — the default stays absent, exactly as `add_table` writes it.
    json.loads(ctx.tools["board_new"]["handler"]({"id": "alignup", "title": "alignup"}))
    apply15({"id": "alignup", "ops": [{"op": "add_table", "id": "t", "rows": 2, "cols": 2,
                                      "cells": ["a", "b", "c", "d"]}]})
    unaligned = store.load_board("alignup")["nodes"][0]
    check("a fresh table is unaligned: the key is absent, not a stored 'center' (Y4.5a)",
          "align" not in unaligned, json.dumps(unaligned.get("align")))
    for value, expected, anchor in (("left", "left", "start"), ("center", None, "middle"),
                                    ("right", "right", "end")):
        apply15({"id": "alignup", "ops": [{"op": "update_node", "id": "t", "align": value}]})
        updated = store.load_board("alignup")["nodes"][0]
        svg_up = store.boardkit().render_svg(store.load_board("alignup"))
        others = [name for name, one in (("start", "left"), ("middle", "center"), ("end", "right"))
                  if one != value and f'text-anchor="{name}"' in svg_up]
        check(f"update_node moves an unaligned table to {value}: stored {expected}, anchored {anchor}"
              f" (Y4.5a)",
              updated.get("align") == expected and svg_up.count(f'text-anchor="{anchor}"') == 4
              and not others,
              json.dumps([updated.get("align"), svg_up.count(f'text-anchor="{anchor}"'), others]))
    before_bad = store.load_board("alignup")["nodes"][0].get("align")
    try:
        apply15({"id": "alignup", "ops": [{"op": "update_node", "id": "t", "align": "bogus"}]})
        check("update_node refuses a bad align", False, "it was accepted")
    except ValueError as err:
        check("update_node refuses a bad align, naming the field", "align" in str(err), str(err))
    check("the refused update left the table's own align alone (Y4.5a)",
          store.load_board("alignup")["nodes"][0].get("align") == before_bad, json.dumps(before_bad))
    json.loads(ctx.tools["board_new"]["handler"]({"id": "alignbad", "title": "alignbad"}))
    try:
        apply15({"id": "alignbad", "ops": [{"op": "add_table", "id": "t", "align": "bogus"}]}) 
        check("add_table refuses a bad align", False, "it was accepted")
    except ValueError as err:
        check("add_table refuses a bad align, naming the field", "align" in str(err), str(err))

    print("v1.5 — the normalisation that keeps an op and the pane in step")
    # Y4.1a (v1.5 verifier round 1, CRITICAL/MAJOR): the writers REFUSE what they cannot store — a bad
    # width is rejected with the field named, not silently turned into 1. `float("inf")` is the one
    # that mattered: it passes `> 0`, makes the column sum infinite, and every column then resolves to
    # NaN, so the table was accepted and the renderer died on it. The lenient 1-on-read fallback is a
    # separate layer and is asserted on a hand-written document below.
    json.loads(ctx.tools["board_new"]["handler"]({"id": "badwidths", "title": "bad widths"}))
    for bad in ([2, "wide", -1, 5], [float("inf")], [0], [-1]):
        try:
            apply15({"id": "badwidths", "ops": [{"op": "add_table", "id": "w", "col_widths": bad}]})
            check(f"add_table refuses col_widths {bad}", False, "it was accepted")
        except ValueError as err:
            check(f"add_table refuses col_widths {bad}, naming the field",
                  "col_widths" in str(err), str(err))
    json.loads(apply15({"id": "v15", "ops": [
        {"op": "add_table", "id": "widths", "cols": 3, "col_widths": [2, 5],
         "row_tones": ["warn", None], "col_tones": [None, "ok"]},
        {"op": "add_table", "id": "obj", "cells": [{"text": 12, "tone": "warn", "align": "right",
                                                    "extra": 1}]},
    ]}))
    extra15 = {n["id"]: n for n in store.load_board("v15")["nodes"]}
    check("a short width list is padded with 1, and a document's non-finite width reads as 1 (Y4.2/Y6)",
          extra15["widths"]["col_widths"] == [2, 5, 1]
          and store._norm_col_widths([float("inf"), 3], 2) == [1, 3],
          json.dumps(extra15["widths"].get("col_widths")))
    check("col_tones/row_tones are padded with nulls to the table's shape (Y4.3/Y6)",
          extra15["widths"]["col_tones"] == [None, "ok", None]
          and extra15["widths"]["row_tones"] == ["warn", None],
          json.dumps([extra15["widths"].get("col_tones"), extra15["widths"].get("row_tones")]))
    check("a cell object's surplus key is dropped and a bare 12 becomes \"12\" (Y4.1/Y6)",
          extra15["obj"]["cells"] == [{"text": "12", "tone": "warn", "align": "right"},
                                      "", "", ""], json.dumps(extra15["obj"]["cells"]))
    hand = {"kind": "table", "rows": 1, "cols": 1,
            "cells": [{"text": 12, "tone": "nope", "align": "middle", "extra": 1}]}
    check("a hand-written cell object is READ forgivingly: text str()'d, junk dropped (Y4.1)",
          store.cells_of(hand) == ["12"], json.dumps(store.cells_of(hand)))
    check("reading a stray document never rewrites it (Y4.1)",
          hand["cells"] == [{"text": 12, "tone": "nope", "align": "middle", "extra": 1}],
          json.dumps(hand["cells"]))
    check("a table's tone lists read effective even when the fields are absent (Y4.2/Y4.3)",
          store.col_widths_of({"cols": 3}) == [1, 1, 1] and
          store.row_tones_of({"rows": 2}) == [None, None],
          json.dumps([store.col_widths_of({"cols": 3}), store.row_tones_of({"rows": 2})]))

    print("v1.5 — validation: a wrong TYPE is refused, not coerced")
    before_ops15 = len(store.load_board("v15")["nodes"])
    bad15 = [
        {"op": "add_table", "id": "bad", "col_widths": 2},
        {"op": "add_table", "id": "bad", "col_widths": {"0": 2}},
        {"op": "add_table", "id": "bad", "row_tones": "warn"},
        {"op": "add_table", "id": "bad", "col_tones": ["nope"]},
        {"op": "add_table", "id": "bad", "cells": "a\nb"},
        {"op": "add_table", "id": "bad", "cells": [{"text": "a", "tone": "nope"}]},
        {"op": "add_table", "id": "bad", "cells": [{"text": "a", "align": "middle"}]},
        {"op": "add_table", "id": "bad", "header": "yes"},
        {"op": "add_table", "id": "bad", "header": 1},
        {"op": "add_table", "id": "bad", "bold": "true"},
        {"op": "add_node", "id": "bad", "kind": "table", "header": "yes"},
        {"op": "add_node", "id": "bad", "kind": "table", "bold": 1},
        {"op": "add_node", "id": "bad", "kind": "table", "col_widths": 2},
        {"op": "update_node", "id": "n1", "col_widths": 2},
        {"op": "update_node", "id": "n1", "col_tones": ["nope"]},
        {"op": "update_node", "id": "n1", "cells": "a\nb"},
        {"op": "update_node", "id": "n1", "cells": [{"text": "a", "align": "middle"}]},
        {"op": "update_node", "id": "n1", "header": "yes"},
        {"op": "update_node", "id": "n1", "bold": 1},
    ]
    for bad in bad15:
        try:
            apply15({"id": "v15", "ops": [bad]})
            check(f"{bad['op']} refuses {bad}", False, "no error raised")
        except ValueError as exc:
            check(f"{bad['op']} refuses {bad}",
                  any(word in str(exc) for word in ("must be a list", "is not a tone",
                                                    "is not an alignment", "true or false")), str(exc))
    check("a refused op list writes nothing (Y6)",
          len(store.load_board("v15")["nodes"]) == before_ops15,
          str(len(store.load_board("v15")["nodes"])))

    print("v1.5 — the board description the tools hand the model")
    sum15 = json.loads(ctx.tools["board_get"]["handler"]({"id": "v15"}))["summary"]
    check("the description gains one line per table, 'table n1 3x2 header=yes filled=4/6' (Y6)",
          "table n1 3x2 header=yes filled=4/6" in sum15["tables"], json.dumps(sum15.get("tables")))
    check("a headerless, empty table reads header=no filled=0/4 (Y6)",
          "table t2 2x2 header=no filled=0/4" in sum15["tables"], json.dumps(sum15.get("tables")))
    check("one line per table, in board order (Y6)",
          len(sum15["tables"]) == len([n for n in sum15["nodes"] if n["kind"] == "table"])
          == len([n for n in store.load_board("v15")["nodes"] if n.get("kind") == "table"]),
          json.dumps(sum15.get("tables")))
    check("the lines are a helper's output, not hand-rolled per call (Y6)",
          store.table_line(n15["n1"]) == "table n1 3x2 header=yes filled=4/6",
          store.table_line(n15["n1"]))

    print("v1.5 — update_node on the table v2 fields")
    json.loads(apply15({"id": "v15", "ops": [
        {"op": "update_node", "id": "n1", "col_widths": [1, 3]},
        {"op": "update_node", "id": "n1", "row_tones": [None, None, "danger"], "col_tones": ["ok", None]},
        {"op": "update_node", "id": "n1", "cells": ["a", "b", "c", "d"]},
    ]}))
    updated15 = {n["id"]: n for n in store.load_board("v15")["nodes"]}
    check("update_node writes the widths (Y6)",
          updated15["n1"]["col_widths"] == [1, 3], json.dumps(updated15["n1"].get("col_widths")))
    check("update_node writes the row/column tones (Y6)",
          updated15["n1"]["row_tones"] == [None, None, "danger"]
          and updated15["n1"]["col_tones"] == ["ok", None],
          json.dumps([updated15["n1"].get("row_tones"), updated15["n1"].get("col_tones")]))
    check("update_node re-normalises the cells to exactly rows*cols (Y6)",
          updated15["n1"]["cells"] == ["a", "b", "c", "d", "", ""],
          json.dumps(updated15["n1"]["cells"]))
    apply15({"id": "v15", "ops": [{"op": "update_node", "id": "n1", "rows": 1, "cols": 3}]})
    shrunk15 = {n["id"]: n for n in store.load_board("v15")["nodes"]}["n1"]
    check("changing rows/cols re-normalises each tone/width list (Y4.2/Y4.3/Y6)",
          shrunk15["col_widths"] == [1, 3, 1] and shrunk15["row_tones"] == [None]
          and shrunk15["col_tones"] == ["ok", None, None],
          json.dumps({k: shrunk15.get(k) for k in ("col_widths", "row_tones", "col_tones")}))
    apply15({"id": "v15", "ops": [{"op": "update_node", "id": "obj", "cells": [
        {"text": "x", "tone": "warn"}]}]})
    obj15 = {n["id"]: n for n in store.load_board("v15")["nodes"]}["obj"]
    check("a cell object is written by update_node too (Y6)",
          obj15["cells"][0] == {"text": "x", "tone": "warn"}, json.dumps(obj15["cells"]))

    print("v1.5 — the diff says what changed (and stays quiet when nothing did)")
    base15 = {"title": "d", "nodes": [
        {"id": "n1", "x": 0, "y": 0, "w": 180, "h": 76, "text": "", "kind": "table",
         "rows": 3, "cols": 2, "header": True,
         "cells": ["name", "value", "cpu", "", "", "38%"]}]}
    styled15 = json.loads(json.dumps(base15))
    styled15["nodes"][0]["col_widths"] = [2, 1]
    styled15["nodes"][0]["row_tones"] = [None, "warn", None]
    styled15["nodes"][0]["cells"][2] = {"text": "cpu", "tone": "warn", "align": "right"}
    diff15 = store.diff_boards(base15, styled15)
    check("a widths edit reads as 'column widths 2:1' (Y6)",
          "column widths 2:1" in diff15["summary"], diff15["summary"])
    check("a row tone edit reads as 'row 2 tone warn' (Y6)",
          "row 2 tone warn" in diff15["summary"], diff15["summary"])
    check("a cell alignment edit reads as 'cell 2/1 align right' (Y6)",
          "cell 2/1 align right" in diff15["summary"], diff15["summary"])
    check("a cell tone edit reads as 'cell 2/1 tone warn' (Y6)",
          "cell 2/1 tone warn" in diff15["summary"], diff15["summary"])
    check("the cell's text did not change, so there is no 'cells changed' line either (Y6)",
          "cells changed" not in diff15["summary"], diff15["summary"])
    edit15 = diff15["nodes"]["table"][0]
    check("the table v2 edits are data too, not only a sentence (Y6)",
          edit15["col_widths"] == [[1, 1], [2, 1]] and edit15["row_tones"] == [[1, None, "warn"]]
          and edit15["cell_aligns"] == [[1, 0, None, "right"]]
          and edit15["cell_tones"] == [[1, 0, None, "warn"]], json.dumps(edit15))
    added15 = store.diff_boards({"title": "d", "nodes": []}, {"title": "d", "nodes": [
        {"id": "n1", "x": 0, "y": 0, "w": 180, "h": 76, "text": "", "kind": "table",
         "rows": 3, "cols": 2, "header": True, "col_widths": [2, 1],
         "row_tones": ["muted", None, None], "cells": ["name", "value", "cpu", "", "", "38%"]}]})
    check("a new table is described by its own sentence (Y6)",
          "table n1: 3x2 header, 4/6 cells filled, widths 2:1, 1 coloured row" in added15["summary"],
          added15["summary"])
    check("...and that sentence is data too (Y6)",
          added15["tables_added"] == ["table n1: 3x2 header, 4/6 cells filled, widths 2:1, "
                                      "1 coloured row"], json.dumps(added15.get("tables_added")))
    spelled15 = json.loads(json.dumps(base15))
    spelled15["nodes"][0].update({"col_widths": [1, 1], "row_tones": [None, None, None],
                                  "col_tones": [None, None],
                                  "cells": ["name", "value", "cpu", {"text": ""}, "", "38%"]})
    check("spelling the table v2 defaults out in full is not an edit (Y4.2/Y4.3/Y6)",
          store.diff_boards(base15, spelled15)["changed"] is False,
          store.diff_boards(base15, spelled15)["summary"])
    check("an identical re-save of a table v2 board has nothing to report (Y6)",
          store.diff_boards(base15, json.loads(json.dumps(base15)))["changed"] is False)

    check("a re-save that changed nothing logs nothing at all (Y6)",
          store.write_board("v15", store.load_board("v15"), origin="pane")[1] is None)
    live15 = store.load_board("v15")
    for node in live15["nodes"]:
        if node["id"] == "n1":
            node["cells"][1] = {"text": "b", "tone": "warn"}
    _, tone_event15 = store.write_board("v15", live15, origin="pane")
    check("a pane edit to a cell's own tone logs 'cell 1/2 tone warn' (Y6)",
          bool(tone_event15) and "cell 1/2 tone warn" in tone_event15["summary"],
          json.dumps(tone_event15 and tone_event15.get("summary")))
    ctx.tools["board_delete"]["handler"]({"id": "v15"})
    ctx.tools["board_delete"]["handler"]({"id": "v15b"})

    # ------------------------------------------------------------------ v1.6 lines (lane O)
    print("v1.6 — the new vocabulary")
    check("the line style vocabulary is the spec's, in order (Z3.2/Z7.1)",
          store.EDGE_STYLES == ("solid", "dashed", "dotted"), str(store.EDGE_STYLES))
    check("the ops tuple lists the three line ops, in the spec's order (Z7.2)",
          [op for op in store.OPS if op.endswith("_line")]
          == ["add_line", "update_line", "delete_line"], str(store.OPS))
    desc16 = ctx.tools["board_apply"]["schema"]["description"]
    check("OPS_HELP documents the three ops and their fields (Z7.2)",
          all(word in desc16 for word in ("add_line {points:[[x,y],...]", "update_line {id,points?",
                                          "delete_line {id}", "from?,to?", "side?(auto|left|right|"
                                          "top|bottom)")), desc16[-520:])
    check("OPS_HELP documents waypoints on add_edge and update_edge (Z7.6)",
          "waypoints?:[[x,y],...]}" in desc16 and "head_size?,waypoints?}" in desc16,
          desc16[-1000:])
    check("board_get talks about lines too (Z7.2)",
          "lines" in ctx.tools["board_get"]["schema"]["description"],
          ctx.tools["board_get"]["schema"]["description"])

    print("v1.6 — add_line writes the document the pane would")
    apply16 = ctx.tools["board_apply"]["handler"]
    json.loads(ctx.tools["board_new"]["handler"]({"id": "v16", "title": "v1.6"}))
    json.loads(apply16({"id": "v16", "ops": [
        {"op": "add_node", "id": "a", "text": "A", "x": 40, "y": 40},
        {"op": "add_node", "id": "b", "text": "B", "x": 400, "y": 40},
        {"op": "add_note", "text": "the note a line anchors to", "x": 400, "y": 300},
    ]}))
    note16 = store.load_board("v16")["notes"][0]["id"]
    applied16 = json.loads(apply16({"id": "v16", "ops": [
        {"op": "add_line", "points": [[80, 240], [173, 281], [300, 240]]},
        {"op": "add_line", "points": [[40, 40], [300, 90]], "tone": "accent", "style": "dashed",
         "start": "circle", "end": "triangle", "from": {"ref": "a", "side": "right"},
         "to": {"ref": note16}},
        {"op": "add_line", "points": [[900, 1200], [1200, 1300]]},
    ]}))
    board16 = store.load_board("v16")
    lines16 = board16["lines"]
    check("add_line appends to `lines` and answers 'added line <id> (3 points)' (Z7, Z7.1)",
          len(lines16) == 3 and applied16["did"][0] == f"added line {lines16[0]['id']} (3 points)",
          str(applied16["did"][:1]))
    check("a line's id is `line-` plus 8 characters, unique on the board (Z3)",
          all(re.fullmatch(r"line-[0-9a-f]{8}", str(line["id"])) for line in lines16)
          and len({line["id"] for line in lines16}) == 3, str([line["id"] for line in lines16]))
    check("the path is stored exactly as written — off-grid vertex, integers and all (Z3.1)",
          lines16[0]["points"] == [[80, 240], [173, 281], [300, 240]], json.dumps(lines16[0]))
    check("a line with no anchors stores no anchor keys, and reads as two free ends (Z3.4)",
          "from" not in lines16[0] and "to" not in lines16[0]
          and store.line_anchors(lines16[0], board16) == (None, None), json.dumps(lines16[0]))
    check("add_line stores the same defaults an edge does (tone/style), heads only when given (Z3.2)",
          (lines16[0]["tone"], lines16[0]["style"]) == ("default", "solid")
          and "start" not in lines16[0] and "end" not in lines16[0],
          json.dumps({k: lines16[0].get(k) for k in ("tone", "style", "start", "end")}))
    check("an anchor is stored as {ref, side}, an absent side written as auto (Z3.3)",
          lines16[1]["from"] == {"ref": "a", "side": "right"}
          and lines16[1]["to"] == {"ref": note16, "side": "auto"},
          json.dumps(lines16[1].get("to")))
    check("a line anchored to a NOTES entry stores and resolves that ref (Z3.4)",
          lines16[1]["to"] == {"ref": note16, "side": "auto"}
          and store.line_anchors(lines16[1], board16)
          == (("a", "right", 0.5), (note16, "auto", 0.5)),   # v1.7 (A3): (ref, side, at)
          json.dumps(lines16[1].get("to")))
    check("the heads a line stores are the edge vocabulary (Z3.2)",
          store.line_heads_of(lines16[1]) == ("circle", "triangle")
          and (lines16[1]["start"], lines16[1]["end"]) == ("circle", "triangle"),
          str(store.line_heads_of(lines16[1])))
    check("the canvas grows to cover a line drawn outside every box (Z3.5)",
          float(board16["width"]) >= 1240 and float(board16["height"]) >= 1340,
          f'{board16["width"]}x{board16["height"]}')

    print("v1.6 — the reader is lenient where the op raises (Z2/Z3.4)")
    fx16 = json.loads((ROOT / "docs" / "fixture-v1.6.json").read_text(encoding="utf-8"))
    fixture_lines = {line["id"]: line for line in fx16["lines"]}
    check("a negative coordinate in an imported document is CLAMPED to 0, not refused (Z2)",
          store.points_of(fixture_lines["line-negative"]) == [[0, 600], [120, 600]],
          json.dumps(store.points_of(fixture_lines["line-negative"])))
    check("a point list with an unusable entry drops the whole ITEM (Z2)",
          store.points_of({"points": [[-5, 10], [20, "x"]]}) == []
          and store.points_of({"points": [[1, 2]]}) == []
          and store.points_of({"points": "1,2 3,4"}) == [])
    check("a ref that names nothing leaves that end FREE, and the line is still drawn (Z3.4)",
          store.line_anchors(fixture_lines["line-dangling"], fx16)
          == (None, ("box-a", "bottom", 0.5))   # v1.7 (A3): the resolved anchor carries its at
          and len(store.points_of(fixture_lines["line-dangling"])) == 2,
          json.dumps(store.line_anchors(fixture_lines["line-dangling"], fx16)))
    check("a malformed from/to leaves both ends free — it is not a vanished line (Z3.4)",
          store.line_anchors(fixture_lines["line-malformed"], fx16) == (None, None)
          and store.line_anchors(fixture_lines["line-note"], fx16)
          == (("box-d", "right", 0.5), ("note-1", "left", 0.5)),
          json.dumps(store.line_anchors(fixture_lines["line-malformed"], fx16)))
    check("an unknown tone/style/head/side reads as the default, never raw (Z2/Z3.3)",
          store.line_tone_of({"tone": "nope"}) == "default"
          and store.line_style_of({"style": "zigzag"}) == "solid"
          and store.line_heads_of({"start": "chevron", "end": "chevron"}) == ("none", "arrow")
          and store.anchor_of({"from": {"ref": "a", "side": "middle"}}) == ("a", "auto", 0.5)
          and store.anchor_of({"from": "just a string"}) is None)
    check("the read half never rewrites the document it read (Z2)",
          fx16["lines"][5]["points"][0][0] == -40 and fx16["lines"][4]["from"] == "box-b",
          json.dumps(fx16["lines"][5]["points"][0]))

    print("v1.6 — add_line/update_line refuse the same input, field by field (Z7.1)")
    target16 = lines16[0]["id"]

    def refusal(payload) -> str:
        """Run one op list and hand back the refusal's message (empty when it was accepted)."""
        try:
            apply16({"id": "v16", "ops": [payload]})
        except ValueError as exc:
            return str(exc)
        return ""

    before_version16 = store.version_of(store.load_board("v16"))
    rejects = [
        ("points", {"points": "nope"}),
        ("points", {"points": {"0": [1, 2]}}),
        ("points", {"points": []}),
        ("points", {"points": [[1, 2]]}),
        ("points", {"points": [[1, 2], [3]]}),
        ("points", {"points": [[1, 2], [3, 4, 5]]}),
        ("points", {"points": [[1, 2], [3, -4]]}),
        ("points", {"points": [[-1, 2], [3, 4]]}),
        ("points", {"points": [[1, 2], [3, "x"]]}),
        ("points", {"points": [[1, 2], [True, 4]]}),
        ("points", {"points": [[1, 2], [3, float("inf")]]}),
        ("tone", {"tone": "nope"}),
        ("style", {"style": "zigzag"}),
        ("start", {"start": "chevron"}),
        ("end", {"end": "chevron"}),
        ("from", {"from": "a"}),
        ("from", {"from": {}}),
        ("from", {"from": {"ref": "ghost-id"}}),
        ("to", {"to": {"ref": "ghost-id"}}),
        ("to", {"to": {"ref": note16, "side": "middle"}}),
        # Round-1 finding 4: an explicit null is NOT "not given" — the op raises, naming the
        # field. Only `from`/`to` accept null, and there it clears the anchor (gated below).
        ("points", {"points": None}),
        ("tone", {"tone": None}),
        ("style", {"style": None}),
        ("start", {"start": None}),
        ("end", {"end": None}),
    ]
    for op16 in ("add_line", "update_line"):
        for field16, extra16 in rejects:
            payload16 = {"op": op16, **extra16}
            if op16 == "update_line":
                payload16["id"] = target16          # the op's own precondition, satisfied first
            else:
                payload16.setdefault("points", [[10, 10], [20, 20]])
            try:
                apply16({"id": "v16", "ops": [payload16]})
                check(f"{op16} refuses a bad {field16}", False, "it was accepted")
            except ValueError as exc:
                # "naming the field": the anchor fields report as `from`/`to`, their side as `.side`
                check(f"{op16} refuses a bad {field16}: {str(exc)[:60]}",
                      field16.split(".")[0] in str(exc), str(exc))
    check("a refused op list wrote nothing at all (Z7.1)",
          store.version_of(store.load_board("v16")) == before_version16, "the document moved")
    check("a missing points is refused on add, and is not needed on update (Z7.1)",
          "missing points" in refusal({"op": "add_line"})
          and json.loads(apply16({"id": "v16", "ops": [{"op": "update_line", "id": target16,
                                                       "tone": "ok"}]}))["ok"])

    print("v1.6 — update_line re-shapes a path and clears an anchor (Z7.1)")
    apply16({"id": "v16", "ops": [
        {"op": "update_line", "id": target16,
         "points": [[80, 320], [173, 361], [300, 320], [360, 380]]},
        {"op": "update_line", "id": target16, "tone": "warn", "style": "dotted", "start": "bar",
         "end": "none"},
    ]})
    reshaped16 = {line["id"]: line for line in store.load_board("v16")["lines"]}[target16]
    check("update_line replaces the whole path (Z7.1)",
          reshaped16["points"] == [[80, 320], [173, 361], [300, 320], [360, 380]],
          json.dumps(reshaped16["points"]))
    check("update_line writes tone/style/start/end through the same validation (Z7.1)",
          (reshaped16["tone"], reshaped16["style"], reshaped16["start"], reshaped16["end"])
          == ("warn", "dotted", "bar", "none"), json.dumps(reshaped16))
    check("update_line leaves a field it never mentioned alone (Z7.1)",
          "from" not in reshaped16 and "to" not in reshaped16
          and store.line_tone_of(reshaped16) == "warn", json.dumps(reshaped16))
    apply16({"id": "v16", "ops": [{"op": "update_line", "id": target16,
                                  "from": {"ref": "b", "side": "top"}}]})
    anchored16 = {line["id"]: line for line in store.load_board("v16")["lines"]}[target16]
    check("update_line sets an anchor (Z7.1)", anchored16["from"] == {"ref": "b", "side": "top"},
          json.dumps(anchored16.get("from")))
    apply16({"id": "v16", "ops": [{"op": "update_line", "id": target16, "from": None}]})
    cleared16 = store.load_board("v16")
    cleared_line = {line["id"]: line for line in cleared16["lines"]}[target16]
    check("an explicit null clears that anchor and the end reads free again (Z7.1)",
          "from" not in cleared_line
          and store.line_anchors(cleared_line, cleared16) == (None, None),
          json.dumps(cleared_line))
    apply16({"id": "v16", "ops": [{"op": "update_line", "id": lines16[1]["id"], "to": None}]})
    check("clearing the `to` anchor leaves the `from` one where it was (Z7.1)",
          store.line_anchors({l["id"]: l for l in store.load_board("v16")["lines"]}[lines16[1]["id"]],
                             store.load_board("v16")) == (("a", "right", 0.5), None))

    print("v1.6 — delete_line removes one line, and only a real one (Z7.1)")
    deleted16 = json.loads(apply16({"id": "v16", "ops": [
        {"op": "delete_line", "id": lines16[2]["id"]}]}))
    check("delete_line answers 'deleted line <id>' (Z7.1)",
          deleted16["did"] == [f"deleted line {lines16[2]['id']}"], str(deleted16["did"]))
    check("the line is gone from the document and the others are untouched (Z7.1)",
          lines16[2]["id"] not in [line["id"] for line in store.load_board("v16")["lines"]]
          and {l["id"] for l in store.load_board("v16")["lines"]} == {lines16[0]["id"],
                                                                     lines16[1]["id"]})
    try:
        apply16({"id": "v16", "ops": [{"op": "delete_line", "id": lines16[2]["id"]}]})
        check("deleting a line that is not there is refused (Z7.1)", False, "no error raised")
    except ValueError as exc:
        check("deleting a line that is not there is refused (Z7.1)", "no line" in str(exc), str(exc))

    print("v1.6 — the board description the tools hand the model (Z7.2)")
    sum16 = json.loads(ctx.tools["board_get"]["handler"]({"id": "v16"}))["summary"]
    check("board_get's summary carries `lines`, one entry per line, in board order (Z7.2)",
          [entry["id"] for entry in sum16["lines"]]
          == [line["id"] for line in store.load_board("v16")["lines"]], json.dumps(sum16["lines"]))
    face16 = {entry["id"]: entry for entry in sum16["lines"]}
    check("a line reads back its length, tone, style, heads and free ends (Z7.2)",
          (face16[target16]["points"], face16[target16]["tone"], face16[target16]["style"],
           face16[target16]["start"], face16[target16]["end"], face16[target16]["from"],
           face16[target16]["to"])
          == (4, "warn", "dotted", "bar", "none", None, None), json.dumps(face16[target16]))
    check("an anchor reads back as {ref, side}, resolved against the board (Z7.2/Z3.4)",
          face16[lines16[1]["id"]]["from"] == {"ref": "a", "side": "right"}
          and face16[lines16[1]["id"]]["to"] is None,
          json.dumps(face16[lines16[1]["id"]]))
    # The shipped fixture through the same tool: a `ref` that names nothing (and a malformed
    # anchor) has to read back FREE, exactly as it draws (Z3.4).
    store.save_board("fx16", fx16)
    fx16_face = {entry["id"]: entry for entry in json.loads(
        ctx.tools["board_get"]["handler"]({"id": "fx16"}))["summary"]["lines"]}
    check("the summary lists every line the document carries, once each (Z7.2)",
          len(fx16_face) == len(fx16["lines"]) == 6, str(sorted(fx16_face)))
    check("a dangling or malformed anchor reads back free, a resolved one reads back live (Z7.2)",
          fx16_face["line-dangling"]["from"] is None
          and fx16_face["line-dangling"]["to"] == {"ref": "box-a", "side": "bottom"}
          and fx16_face["line-malformed"]["from"] is None
          and fx16_face["line-malformed"]["to"] is None
          and fx16_face["line-note"]["to"] == {"ref": "note-1", "side": "left"}
          and fx16_face["line-negative"]["points"] == 2,
          json.dumps([fx16_face["line-dangling"], fx16_face["line-malformed"]]))

    print("v1.6 — a v1.5 document behaves exactly as before (Z7)")
    fx15 = json.loads((ROOT / "docs" / "fixture-v1.5.json").read_text(encoding="utf-8"))
    store.save_board("fx15", fx15)
    fx15_sum = json.loads(ctx.tools["board_get"]["handler"]({"id": "fx15"}))["summary"]
    check("a document with no `lines` key reports an empty list, not a missing key (Z7.2)",
          fx15_sum["lines"] == [] and "lines" not in fx15, str(fx15_sum.get("lines")))
    check("adding an empty `lines` list to a v1.5 document is not an edit (Z7.3)",
          store.diff_boards(fx15, dict(fx15, lines=[]))["changed"] is False)
    check("a v1.5 document re-saved unchanged logs nothing at all (Z7.3)",
          store.write_board("fx15", store.load_board("fx15"), origin="pane")[1] is None)
    check("the v1.5 fixture's own diff is still quiet (Z7.3)",
          store.diff_boards(fx15, json.loads(json.dumps(fx15)))["changed"] is False)

    print("v1.6 — the diff says what changed (and stays quiet when nothing did) (Z7.3)")
    one16 = {"title": "d", "lines": [{"id": "line-a1b2c3d4", "points": [[100, 100], [400, 140],
                                                                       [420, 300]],
                                      "tone": "default", "style": "solid"}]}
    drew16 = store.diff_boards({"title": "d", "lines": []}, one16)
    check("a new line reads 'drew 1 line (3 points)' (Z7.3)",
          drew16["summary"] == "drew 1 line (3 points)", drew16["summary"])
    check("...and which line it was is data too (Z7.3)",
          drew16["lines"]["added"] == ["line-a1b2c3d4"], json.dumps(drew16["lines"]))
    two16 = json.loads(json.dumps(one16))
    two16["lines"].append({"id": "line-b2c3d4e5", "points": [[10, 20], [30, 40]], "tone": "ok",
                           "style": "solid"})
    check("several new lines read 'drew 2 lines (3 points, 2 points)' (Z7.3)",
          store.diff_boards({"title": "d", "lines": []}, two16)["summary"]
          == "drew 2 lines (3 points, 2 points)",
          store.diff_boards({"title": "d", "lines": []}, two16)["summary"])
    check("a removed line reads 'deleted 1 line' (Z7.3)",
          store.diff_boards(one16, {"title": "d", "lines": []})["summary"] == "deleted 1 line",
          store.diff_boards(one16, {"title": "d", "lines": []})["summary"])
    dragged16 = json.loads(json.dumps(one16))
    dragged16["lines"][0]["points"] = [[160, 140], [460, 180], [480, 340]]
    dragged_diff = store.diff_boards(one16, dragged16)
    check("a path dragged as a whole reads as `moved`, never as a re-shape (Z7.3)",
          dragged_diff["lines"]["moved"] == ["line-a1b2c3d4"]
          and dragged_diff["lines"]["edited"] == []
          and dragged_diff["summary"] == "moved 1 line (line-a1b2c3d4)", dragged_diff["summary"])
    reshaped16 = json.loads(json.dumps(one16))
    reshaped16["lines"][0].update({"points": [[100, 100], [400, 140], [420, 300], [500, 360]],
                                  "tone": "accent", "style": "dashed", "start": "circle",
                                  "end": "diamond"})
    edit16 = store.diff_boards(one16, reshaped16)
    check("a re-shaped line reports points, tone, style and heads as data (Z7.3)",
          edit16["lines"]["edited"] == [{"id": "line-a1b2c3d4", "points": [3, 4],
                                         "tone": ["default", "accent"], "style": ["solid", "dashed"],
                                         "heads": [["none", "arrow"], ["circle", "diamond"]]}],
          json.dumps(edit16["lines"]["edited"]))
    check("...and as one sentence per field (Z7.3)",
          all(part in edit16["summary"] for part in
              ("line points 3 -> 4 (line-a1b2c3d4)", "line tone accent (line-a1b2c3d4)",
               "line style dashed (line-a1b2c3d4)",
               "line heads none -> circle, arrow -> diamond (line-a1b2c3d4)")), edit16["summary"])
    anchored16 = json.loads(json.dumps(one16))
    anchored16["nodes"] = [{"id": "n1", "x": 0, "y": 0, "w": 10, "h": 10}]
    anchored16["lines"][0]["from"] = {"ref": "n1", "side": "auto"}
    anchor_diff = store.diff_boards(one16, anchored16)
    check("an anchor arriving is its own edit, resolved against the board (Z7.3)",
          anchor_diff["lines"]["edited"][0]["anchors"]
          == [[None, None], [["n1", "auto", 0.5], None]]   # v1.7 (A3): the triple carries at
          and "line anchors none -> from (line-a1b2c3d4)" in anchor_diff["summary"],
          json.dumps(anchor_diff["lines"]["edited"]))
    spelled16 = json.loads(json.dumps(one16))
    spelled16["lines"][0].update({"tone": "default", "style": "solid", "start": "none",
                                  "end": "arrow", "from": None, "to": None})
    check("spelling a line's defaults out in full is not an edit (Z7.3)",
          store.diff_boards(one16, spelled16)["changed"] is False,
          store.diff_boards(one16, spelled16)["summary"])
    ghost16 = json.loads(json.dumps(one16))
    ghost16["lines"][0]["from"] = {"ref": "ghost-id"}
    check("...and neither is an anchor that names nothing: the reader reads that end free (Z3.4)",
          store.diff_boards(one16, ghost16)["changed"] is False,
          store.diff_boards(one16, ghost16)["summary"])
    check("a re-save of a line board that changed nothing logs nothing at all (Z7.3)",
          store.write_board("v16", store.load_board("v16"), origin="pane")[1] is None)
    live16 = store.load_board("v16")
    live16["lines"][0]["tone"] = "danger"
    _, line_event16 = store.write_board("v16", live16, origin="pane", notify=True)
    check("a pane edit to a line logs the line's sentence (Z7.3)",
          bool(line_event16) and f"line tone danger ({target16})" in line_event16["summary"],
          json.dumps(line_event16 and line_event16.get("summary")))

    print("v1.6 — waypoints: the ops stop dropping the field (Z7.6)")
    json.loads(apply16({"id": "v16", "ops": [
        {"op": "add_edge", "from": "a", "to": "b", "waypoints": [[150, 130], [350, 130]]}]}))
    edge16 = {e["from"]: e for e in store.load_board("v16")["edges"]}
    check("add_edge writes waypoints like the document does (Z7.6)",
          edge16["a"].get("waypoints") == [[150, 130], [350, 130]], json.dumps(edge16["a"]))
    check("...and the renderer draws a different path with them than without (Z7.6)",
          bk.render_svg(store.load_board("v16")) != bk.render_svg(
              {**store.load_board("v16"),
               "edges": [{k: v for k, v in e.items() if k != "waypoints"}
                         for e in store.load_board("v16")["edges"]]}),
          "the waypoints changed nothing in the SVG")
    apply16({"id": "v16", "ops": [{"op": "update_edge", "from": "a", "to": "b",
                                  "waypoints": [[150, 200], [350, 200]]}]})
    check("update_edge moves them (Z7.6)",
          {e["from"]: e for e in store.load_board("v16")["edges"]}["a"]["waypoints"]
          == [[150, 200], [350, 200]])
    apply16({"id": "v16", "ops": [{"op": "update_edge", "from": "a", "to": "b",
                                  "waypoints": []}]})
    check("waypoints: [] puts the edge back on the auto-router (Z7.6)",
          "waypoints" not in {e["from"]: e for e in store.load_board("v16")["edges"]}["a"])
    check("an op that never mentioned waypoints leaves the field alone (Z7.6)",
          "waypoints" not in {e["from"]: e for e in store.load_board("v16")["edges"]}["a"])
    for bad16 in ("x", 5, [[1]], [[1, "y"]], [[1, -2]], [[float("inf"), 2]]):
        try:
            apply16({"id": "v16", "ops": [{"op": "add_edge", "from": "a", "to": "b",
                                          "waypoints": bad16}]})
            check(f"add_edge refuses waypoints {bad16}", False, "it was accepted")
        except ValueError as exc:
            check(f"add_edge refuses waypoints {bad16}", "waypoints" in str(exc), str(exc))
    check("the refused edges were never added (Z7.6)",
          len(store.load_board("v16")["edges"]) == 1, str(len(store.load_board("v16")["edges"])))

    print("v1.7 — anchor positions: the ops layer raises, the readers stay lenient (A2/A3)")
    desc17 = ctx.tools["board_apply"]["schema"]["description"]
    check("OPS_HELP documents the position fields and the re-point (A3)",
          all(word in desc17 for word in ("from_at?", "to_at?", "at?", "new_from?", "new_to?"))
          and "null" in desc17, desc17[-400:])
    json.loads(ctx.tools["board_new"]["handler"]({"id": "v17", "title": "v1.7"}))
    json.loads(apply16({"id": "v17", "ops": [
        {"op": "add_node", "id": "a", "text": "A", "x": 40, "y": 40},
        {"op": "add_node", "id": "b", "text": "B", "x": 400, "y": 40},
        {"op": "add_node", "id": "c", "text": "C", "x": 400, "y": 300},
        {"op": "add_note", "text": "n", "x": 40, "y": 300},
    ]}))
    note17 = store.load_board("v17")["notes"][0]["id"]

    # A3's table, the reader layer, every row (store.py's at_of is the board-facing read).
    check("A3 reader: a finite number in [0, 1] reads as-is",
          store.at_of({"from_at": 0.4}, "from_at") == 0.4 and store.at_of({"at": 0}, "at") == 0.0
          and store.at_of({"at": 1}, "at") == 1.0)
    check("A3 reader: -3 clamps to 0 and 1e400 (+inf) clamps to 1",
          store.at_of({"at": -3}, "at") == 0.0
          and store.at_of({"at": json.loads("1e400")}, "at") == 1.0)
    check("A3 reader: a string, a boolean and a literal NaN are IGNORED (0.5)",
          store.at_of({"at": "0.25"}, "at") == 0.5 and store.at_of({"at": True}, "at") == 0.5
          and store.at_of({"at": float("nan")}, "at") == 0.5)
    check("A3 reader: null resets — an absent key and a null both read 0.5",
          store.at_of({"at": None}, "at") == 0.5 and store.at_of({}, "at") == 0.5)
    check("A3 reader: anchor_of resolves the at triple through the same table",
          store.anchor_of({"from": {"ref": "a", "side": "left", "at": 0.75}})
          == ("a", "left", 0.75)
          and store.anchor_of({"from": {"ref": "a", "at": "half"}}) == ("a", "auto", 0.5))

    # The ops column: accepted values are stored; everything outside the table RAISES, naming
    # the field; null is the documented reset (the key is dropped, never stored as null).
    json.loads(apply16({"id": "v17", "ops": [
        {"op": "add_edge", "from": "a", "to": "b", "from_side": "bottom", "from_at": 0.25,
         "to_at": 0.75},
        {"op": "add_edge", "from": "b", "to": "c", "to_at": 0},
        {"op": "add_line", "points": [[10, 10], [90, 90]],
         "from": {"ref": "a", "side": "right", "at": 0.4},
         "to": {"ref": note17, "at": 1}},
    ]}))
    board17 = store.load_board("v17")
    edges17 = {(e["from"], e["to"]): e for e in board17["edges"]}
    check("add_edge stores from_at/to_at, the boundary values included (A3)",
          edges17[("a", "b")].get("from_at") == 0.25 and edges17[("a", "b")].get("to_at") == 0.75
          and edges17[("b", "c")].get("to_at") == 0 and "from_at" not in edges17[("b", "c")],
          str(edges17))
    line17 = board17["lines"][0]
    check("add_line stores the anchors' at (A2)",
          line17["from"] == {"ref": "a", "side": "right", "at": 0.4}
          and line17["to"] == {"ref": note17, "side": "auto", "at": 1}, json.dumps(line17))

    before17 = store.version_of(board17)
    rejects17 = [
        # (field the message must name, op payload) — every refusal row of A3's table.
        ("from_at", {"op": "add_edge", "from": "a", "to": "c", "from_at": -3}),
        ("to_at", {"op": "add_edge", "from": "a", "to": "c", "to_at": 1.4}),
        ("from_at", {"op": "add_edge", "from": "a", "to": "c", "from_at": json.loads("1e400")}),
        ("to_at", {"op": "add_edge", "from": "a", "to": "c", "to_at": "0.25"}),
        ("from_at", {"op": "add_edge", "from": "a", "to": "c", "from_at": True}),
        ("to_at", {"op": "add_edge", "from": "a", "to": "c", "to_at": float("nan")}),
        ("from_at", {"op": "update_edge", "from": "a", "to": "b", "from_at": -3}),
        ("to_at", {"op": "update_edge", "from": "a", "to": "b", "to_at": "high"}),
        ("from.at", {"op": "add_line", "points": [[1, 2], [3, 4]],
                     "from": {"ref": "a", "at": -3}}),
        ("to.at", {"op": "add_line", "points": [[1, 2], [3, 4]],
                   "to": {"ref": "a", "at": "half"}}),
        ("from.at", {"op": "update_line", "id": line17["id"],
                     "from": {"ref": "a", "at": json.loads("1e400")}}),
        ("to.at", {"op": "update_line", "id": line17["id"],
                   "to": {"ref": note17, "at": False}}),
    ]
    for field17, payload17 in rejects17:
        try:
            apply16({"id": "v17", "ops": [payload17]})
            check(f"the op refuses {field17}={payload17!r:.60}", False, "it was accepted")
        except ValueError as exc:
            check(f"the op refuses a bad {field17}, naming it: {str(exc)[:60]}",
                  field17 in str(exc), str(exc))
    check("a refused op list wrote nothing at all (A3)",
          store.version_of(store.load_board("v17")) == before17, "the document moved")

    # null resets: the key drops out, the read returns to 0.5 — for the edge fields and the
    # line anchor alike (A3: an absent at IS 0.5, so null is never STORED).
    json.loads(apply16({"id": "v17", "ops": [
        {"op": "update_edge", "from": "a", "to": "b", "from_at": None},
        # update_line replaces the anchor OBJECT wholesale (Z7.1), so the reset carries the
        # side it keeps; only the at drops out.
        {"op": "update_line", "id": line17["id"], "from": {"ref": "a", "side": "right",
                                                           "at": None}},
    ]}))
    reset17 = store.load_board("v17")
    check("null resets an edge's position to the default — the key is dropped (A3)",
          "from_at" not in {(e["from"], e["to"]): e for e in reset17["edges"]}[("a", "b")]
          and reset17["edges"][0].get("to_at") == 0.75, json.dumps(reset17["edges"][0]))
    check("null resets a line anchor's at — the ref/side stay (A3)",
          reset17["lines"][0]["from"] == {"ref": "a", "side": "right"},
          json.dumps(reset17["lines"][0]["from"]))

    print("v1.7 — update_edge re-points an end onto another node (A5/A3)")
    repointed = json.loads(apply16({"id": "v17", "ops": [
        {"op": "update_edge", "from": "a", "to": "b", "new_to": "c", "to_side": "top",
         "to_at": 0.5}]}))
    moved17 = {(e["from"], e["to"]): e for e in store.load_board("v17")["edges"]}
    check("new_to re-points the edge, keeping its other fields (A5)",
          ("a", "c") in moved17 and ("a", "b") not in moved17
          and moved17[("a", "c")].get("to_side") == "top"
          and moved17[("a", "c")].get("to_at") == 0.5, json.dumps(repointed["did"]))
    check("the report names both the old and the new pair (A5)",
          any("a -> b" in note and "now a -> c" in note for note in repointed["did"]),
          str(repointed["did"]))
    json.loads(apply16({"id": "v17", "ops": [
        {"op": "update_edge", "from": "b", "to": "c", "new_from": "c", "new_to": "a"}]}))
    swung17 = {(e["from"], e["to"]): e for e in store.load_board("v17")["edges"]}
    check("new_from and new_to can move in one op (A5)",
          ("c", "a") in swung17 and ("b", "c") not in swung17, json.dumps(list(swung17)))
    for field17, payload17 in [
            ("new_from", {"op": "update_edge", "from": "a", "to": "c", "new_from": "ghost"}),
            ("new_to", {"op": "update_edge", "from": "a", "to": "c", "new_to": note17}),
            # The edges (a,c) and (c,a) are both live: swinging (c,a)'s to end onto c lands on
            # the existing (a,c) pair — refused, or the two could never be told apart again.
            ("new_to", {"op": "update_edge", "from": "c", "to": "a", "new_from": "a",
                        "new_to": "c"})]:
        try:
            apply16({"id": "v17", "ops": [payload17]})
            check(f"update_edge refuses {field17}={payload17.get(field17)!r}", False,
                  "it was accepted")
        except ValueError as exc:
            check(f"update_edge refuses a bad {field17}, naming it: {str(exc)[:60]}",
                  field17 in str(exc), str(exc))
    check("a re-point to the SAME node is a no-op, not an error (A5)",
          json.loads(apply16({"id": "v17", "ops": [
              {"op": "update_edge", "from": "a", "to": "c", "new_to": "c"}]}))["ok"])

    print("v1.7 — diff_boards reports a moved position as an edit of that end (A3)")
    snap17 = store.load_board("v17")
    moved_pos = json.loads(json.dumps(snap17))
    moved_pos["edges"][0]["from_at"] = 0.5      # ("a","c") edge: no from_at -> 0.5 stays silent…
    check("spelling out the default 0.5 is not an edit (A3)",
          store.diff_boards(snap17, moved_pos)["changed"] is False,
          store.diff_boards(snap17, moved_pos)["summary"])
    moved_pos["edges"][0]["from_at"] = 0.25
    pos_diff = store.diff_boards(snap17, moved_pos)
    check("a changed from_at is an edit of the from end, in data and in words (A3)",
          pos_diff["edges"]["positions"] == [{"edge": "a→c", "from_at": [0.5, 0.25],
                                              "to_at": [0.5, 0.5]}]
          and "edge a→c from position 0.5 -> 0.25" in pos_diff["summary"],
          json.dumps(pos_diff["edges"]["positions"]))
    moved_line = json.loads(json.dumps(snap17))
    moved_line["lines"][0]["to"]["at"] = 0.25   # was 1: the line's anchors triple moves too
    line_pos_diff = store.diff_boards(snap17, moved_line)
    check("a changed at on a line end reads as an anchors edit of that end (A3)",
          line_pos_diff["lines"]["edited"][0]["anchors"]
          == [[["a", "right", 0.5], [note17, "auto", 1]],
              [["a", "right", 0.5], [note17, "auto", 0.25]]],
          json.dumps(line_pos_diff["lines"]["edited"]))

    ctx.tools["board_delete"]["handler"]({"id": "v17"})
    ctx.tools["board_delete"]["handler"]({"id": "v16"})
    ctx.tools["board_delete"]["handler"]({"id": "fx15"})
    ctx.tools["board_delete"]["handler"]({"id": "fx16"})

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
