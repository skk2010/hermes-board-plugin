"""Tool schemas and handlers for the hermes-board plugin.

Six tools, all thin wrappers over `store`: list, get, new, delete, apply (ops), render. The
agent draws by sending a list of ops, so one call can build a whole diagram, and every call
returns a summary the model can check itself against.
"""

from __future__ import annotations

import json

from . import store
from .runtime import target_from_kwargs

OPS_HELP = (
    "ops are objects with an 'op' key. Available: "
    "add_node {id?,text,sub?,x?,y?,w?,h?,tone?,"
    "kind?(box|pill|diamond|note|caption|cloud|cylinder|document|hexagon|parallelogram|"
    "server|computer|actor|area|table),parts?,split?,align?,rows?,cols?,cells?,header?,"
    "text_size?,bold?,col_widths?,row_tones?,col_tones?}; "
    "`parts` splits a box/pill into compartments — one entry per part, each a string or "
    "{text,sub?} — laid out `split` (rows|columns, default rows); a split box may leave `text` "
    "out (no header row), and `h` then defaults to the height that fits them "
    "(28 when there is header text + 24 per part + 16); "
    "`kind: \"table\"` draws `rows` x `cols` cells (default 2x2) from `cells` — one entry per "
    "cell, row-major, empty cells allowed — and `header: true` bolds the first row and separates "
    "it with a divider; a table needs no `text`, sizes itself (90 per column, 28 for the first row "
    "when it is a header and 24 per row after it) and ignores text_size/bold; a cell is a string, "
    "or an object {text, tone?, align?} to give that one cell its own colour (tone) and alignment "
    "(left|center|right); `col_widths` (one positive number per column, share of the width), "
    "`row_tones`/`col_tones` (one tone name or null per row/column; a cell's own tone wins, then "
    "its row's, then its column's, then the table's) are all optional; "
    "`add_table` is the same table without the kind: "
    "add_table {id?,x?,y?,rows?,cols?,cells?,header?,col_widths?,row_tones?,col_tones?,tone?}; "
    "`text_size?(small|default|large)` and `bold?` set the size and weight of the node's own "
    "`text` (never of its `sub`, its parts or its cells); "
    "`align?(center|left)` left-aligns the text; "
    "update_node {id,text?,sub?,tone?,kind?,x?,y?,w?,h?,parts?,split?,align?,rows?,cols?,cells?,"
    "header?,text_size?,bold?,col_widths?,row_tones?,col_tones?} (parts: [] removes the split); "
    "move_node {id,x,y}; delete_node {id}; "
    "add_edge {from,to,label?,style?(solid|dashed|dotted),route?(auto|h|v|straight),tone?,bidir?,"
    "label_at?(first|middle|last),start?,end?,from_side?,to_side?,from_at?,to_at?,head_size?,"
    "waypoints?:[[x,y],...]}; "
    "update_edge {from,to,new_from?,new_to?,label?,style?,tone?,label_at?,bidir?,start?,end?,"
    "from_side?,to_side?,from_at?,to_at?,head_size?,waypoints?}; "
    "delete_edge {from,to}; "
    "`waypoints` is the edge's own hand-routed path — the [x, y] points the router runs through "
    "instead of the automatic route (the same field `bus` writes); `waypoints: []` puts the edge "
    "back on the auto-router; "
    "start/end choose the head drawn at each end: none|arrow|open|triangle|circle|diamond|bar "
    "(defaults: end arrow, start none; bidir: true is the same as start: arrow); "
    "from_side/to_side say where an edge meets each node: auto|left|right|top|bottom — auto (the "
    "default) is the side nearest the other end, an explicit side anchors at that side's midpoint; "
    "from_at/to_at say WHERE along that side: a number 0..1 (0 the start corner, 0.5 the midpoint, "
    "1 the end corner; left/right walk y from the top, top/bottom walk x from the left) — null "
    "resets one to the default 0.5, and new_from/new_to re-point an end onto another node; "
    "head_size scales the head's box: small|medium|large (medium, the default, is what every "
    "earlier board draws); from_side: \"auto\" puts a side back to the default; "
    "add_line {points:[[x,y],...],tone?,style?(solid|dashed|dotted),start?,end?,from?,to?} draws a "
    "free path of at least 2 points in board units (0 or more) — an annotation arrow, or a stroke "
    "the user can re-shape by hand; a line connects no nodes, so `points` is the whole path and "
    "`from`/`to` are OPTIONAL anchors {ref: <node or note id>, side?(auto|left|right|top|bottom), "
    "at?(0..1, the same fraction along the side as from_at; null resets it)} "
    "that pin that end to an item (`null` takes one away, a ref that names nothing is refused); "
    "update_line {id,points?,tone?,style?,start?,end?,from?,to?} (same validation as add_line); "
    "delete_line {id}; a line's tone/style/start/end are the edge's own vocabulary, and its "
    "interior points are allowed to cross a box — that is the annotation; "
    "add_group {id?,text,x,y,w,h,tone?}; update_group/delete_group {id}; "
    "add_caption {text,x,y,w?} (free text, no box); "
    "add_note {text,x,y,w?}; update_note/delete_note {id}; set_title {title}; "
    "set_grid {size?,on?,style?(lines|dots),snap?} sets the background grid: its spacing in px, "
    "whether it is drawn, its style and whether the pane snaps drags to it "
    "(none of them are drawn on a board that never asked for a grid); "
    "layout {rows:[[id,...],...],labels?,direction?(down|right),gap_node?,gap_layer?,w?,h?} "
    "places nodes in bands when you want to choose the layers; "
    "graph {direction?(down|right),bands?,labels?,gap_node?,gap_layer?} derives the ranks and the "
    "order from the edges themselves (use this instead of rows for a real graph); "
    "bus {sources:[id,...],targets:[id,...],direction?(v|h),label?,tone?,style?} connects two "
    "bands through one shared trunk instead of a web of diagonals."
)


def _json(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=1)


WATCH_HELP = (
    "board_watch keeps the agent in the loop when the USER edits a board in the desktop pane: "
    "action='read' (default) returns pending edits with a plain-language diff, "
    "action='ack' marks them read, action='arm' points future edits at THIS session (they arrive "
    "as an injected message; a messaging chat also needs "
    "plugins.entries.hermes-board.allow_gateway_injection: true in the profile config), "
    "action='disarm' stops that, action='status' reports the armed target and the last wake."
)


def _arm_hint(target: dict, resolved: str | None) -> str:
    """Say plainly where an armed edit will go, and what is still missing when it cannot."""
    if target.get("session_key"):
        via = f" (resolved from session_id {target['session_id']})" if resolved else ""
        return (f"edits now wake this session through session_key {target['session_key']}{via}; "
                "a messaging chat also needs "
                "plugins.entries.hermes-board.allow_gateway_injection: true, and the host must "
                "still be running when the edit happens")
    return ("armed, but no routable session key was found for this session — only a local "
            "CLI/REPL session can be woken without one; the edits stay in the log for "
            "action='read'")


def register_tools(ctx) -> None:
    def board_list(params, **kwargs):
        return _json({"boards": store.list_boards(), "dir": store.BOARD_DIR})

    def board_get(params, **kwargs):
        board_id = params.get("id") or store.DEFAULT_ID
        board = store.load_board(board_id)
        return _json({"id": board_id, "version": store.version_of(board), "board": board,
                      "summary": _summary(board)})

    def board_new(params, **kwargs):
        board_id = params.get("id") or store.DEFAULT_ID
        board = store.new_board(board_id, params.get("title"))
        version = store.save_board(board_id, board)
        return _json({"ok": True, "id": board_id, "version": version,
                      "path": store._path(board_id)})

    def board_delete(params, **kwargs):
        board_id = params.get("id")
        if not board_id:
            raise ValueError("board_delete needs an id")
        return _json({"ok": store.delete_board(board_id), "id": board_id})

    def board_apply(params, **kwargs):
        board_id = params.get("id") or store.DEFAULT_ID
        board = store.load_board(board_id)
        notes = store.apply_ops(board, params.get("ops"))
        # Agent edits are logged (so the user can see what the agent did) but never wake anyone.
        version, event = store.write_board(board_id, board, origin="agent", notify=False)
        return _json({"ok": True, "id": board_id, "version": version, "did": notes,
                      "logged": bool(event), "summary": _summary(board)})

    def board_render(params, **kwargs):
        board_id = params.get("id") or store.DEFAULT_ID
        kind = params.get("format") or "png"
        path = store.render_to_file(board_id, kind=kind, out_path=params.get("out_path"))
        return _json({"ok": True, "id": board_id, "format": kind, "path": path,
                      "hint": "attach this file to the reply (MEDIA:<path>) so the user sees it"})

    def board_watch(params, **kwargs):
        action = str(params.get("action") or "read").lower()
        board_id = params.get("id") or None
        if action == "arm":
            target = target_from_kwargs(kwargs)
            if params.get("session_key"):
                target["session_key"] = str(params["session_key"])
            # The dispatcher hands us the agent's session_id; the host routes on the durable
            # session key, so translate one into the other before storing the target.
            resolved = None
            if not target.get("session_key") and target.get("session_id"):
                resolved = store.resolve_session_key(target["session_id"])
                if resolved:
                    target["session_key"] = resolved
            armed = store.arm_watch(target, note=params.get("note"))
            status = store.watch_status()
            return _json({"ok": True, "action": "arm", **armed, "status": status,
                          "captured_from": sorted(target), "resolved_session_key": resolved,
                          "hint": _arm_hint(target, resolved) if target else
                          ("this host passed no session_key/origin, so the wake can only reach a "
                           "classic CLI/REPL session; edits always land in the log, read them "
                           "with action='read'")})
        if action == "disarm":
            return _json({"ok": True, "action": "disarm", **store.disarm_watch()})
        if action == "ack":
            return _json({"ok": True, "action": "ack", "acked": store.acknowledge(board_id)})
        if action == "status":
            return _json({"ok": True, "action": "status", "status": store.watch_status()})
        data = store.read_events(board_id, limit=int(params.get("limit") or 20),
                                ack=bool(params.get("ack")))
        return _json({"ok": True, "action": "read", **data, "status": store.watch_status(),
                      "hint": "these are the user's edits since the last ack; act on them, then "
                              "ack so they do not come back" if data["events"] else "nothing pending"})

    ctx.register_tool(
        name="board_watch", toolset="hermes_board",
        schema={"name": "board_watch", "description": WATCH_HELP,
                "parameters": {"type": "object", "properties": {
                    "action": {"type": "string",
                               "enum": ["read", "ack", "arm", "disarm", "status"]},
                    "id": {"type": "string", "description": "limit to one board"},
                    "limit": {"type": "integer", "description": "max events to return (20)"},
                    "ack": {"type": "boolean", "description": "mark returned events read"},
                    "note": {"type": "string", "description": "why this session armed it"},
                    "session_key": {"type": "string",
                                    "description": "explicit wake target when the host passes none"}},
                    "required": []}},
        handler=board_watch)

    ctx.register_tool(
        name="board_list", toolset="hermes_board",
        schema={"name": "board_list", "description":
                "List the boards available to draw on (id, title, node/edge counts, version).",
                "parameters": {"type": "object", "properties": {}}},
        handler=board_list)

    ctx.register_tool(
        name="board_get", toolset="hermes_board",
        schema={"name": "board_get", "description":
                "Read one board: the full JSON document plus a compact summary of nodes, edges, "
                "lines, groups and notes. Read before editing so node ids are correct.",
                "parameters": {"type": "object", "properties": {
                    "id": {"type": "string", "description": "board id (default: 'default')"}},
                    "required": []}},
        handler=board_get)

    ctx.register_tool(
        name="board_new", toolset="hermes_board",
        schema={"name": "board_new", "description":
                "Create an empty board (fails if it exists — use board_delete first).",
                "parameters": {"type": "object", "properties": {
                    "id": {"type": "string"}, "title": {"type": "string"}}, "required": []}},
        handler=board_new)

    ctx.register_tool(
        name="board_delete", toolset="hermes_board",
        schema={"name": "board_delete", "description": "Delete a board file.",
                "parameters": {"type": "object", "properties": {
                    "id": {"type": "string"}}, "required": ["id"]}},
        handler=board_delete)

    ctx.register_tool(
        name="board_apply", toolset="hermes_board",
        schema={"name": "board_apply", "description":
                "Draw on a board by applying an ordered list of ops, then save it. " + OPS_HELP,
                "parameters": {"type": "object", "properties": {
                    "id": {"type": "string", "description": "board id (default: 'default')"},
                    "ops": {"type": "array", "items": {"type": "object"},
                            "description": "ordered op objects, each with an 'op' key"}},
                    "required": ["ops"]}},
        handler=board_apply)

    ctx.register_tool(
        name="board_render", toolset="hermes_board",
        schema={"name": "board_render", "description":
                "Render a board to a file: format='png' for a picture to send in chat, "
                "format='html' for the interactive editor (drag, edit, export JSON).",
                "parameters": {"type": "object", "properties": {
                    "id": {"type": "string"},
                    "format": {"type": "string", "enum": ["png", "html"]},
                    "out_path": {"type": "string", "description": "optional absolute output path"}},
                    "required": []}},
        handler=board_render)


def _line_face(line: dict, board: dict) -> dict:
    """Z7.2/Z3.4: a line's `from`/`to` for the summary — the anchor each end actually uses.

    An anchor is `{"ref": <id>, "side": <side>}` once its `ref` resolves to a node or a note, and
    `None` where the end is free — which is also what a `ref` that names nothing reads as, because
    the renderer falls back to the stored point (Z3.4) and the summary describes what is drawn.
    """
    return {name: ({"ref": anchor[0], "side": anchor[1]} if anchor else None)
            for name, anchor in zip(("from", "to"), store.line_anchors(line, board))}


def _summary(board: dict) -> dict:
    """What the board holds now, in the shape the model reads back after every write (R8)."""
    grid = store.grid_state(board)
    size = grid["size"]
    return {
        "title": board.get("title", ""),
        "grid": {"on": grid["on"],
                 "size": int(size) if float(size).is_integer() else size,
                 "style": grid["style"], "snap": grid["snap"]},
        "nodes": [{"id": n.get("id"), "text": n.get("text"), "kind": n.get("kind", "box"),
                   "x": n.get("x"), "y": n.get("y"),
                   "align": store.align_of(n), "parts": len(store.parts_of(n)),
                   # X8/v1.4: the effective text formatting every node has, and the table's own
                   # fields (the same defaults the renderers read: 2x2, no header, no bold).
                   "text_size": store.text_size_of(n), "bold": store.bold_of(n),
                   "rows": store.rows_of(n), "cols": store.cols_of(n),
                   "header": store.header_of(n)}
                  for n in board.get("nodes", [])],
        "edges": [{"from": e.get("from"), "to": e.get("to"), "label": e.get("label"),
                   "start": store.heads_of(e)[0], "end": store.heads_of(e)[1],
                   "from_side": store.side_of(e), "to_side": store.side_of(e, "to_side"),
                   "head_size": store.head_size_of(e)}
                  for e in board.get("edges", [])],
        # Z7.2 (v1.6): the compact face of a line — how long its path is, how it draws, and each
        # end's anchor as the renderer resolves it (`null` means that end is free: Z3.4 falls back
        # to the stored point when a `ref` names nothing).
        "lines": [{"id": line.get("id"), "points": len(store.points_of(line)),
                   "tone": store.line_tone_of(line), "style": store.line_style_of(line),
                   "start": store.line_heads_of(line)[0], "end": store.line_heads_of(line)[1],
                   **_line_face(line, board)}
                  for line in board.get("lines", [])],
        "groups": [{"id": g.get("id"), "text": g.get("text"), "x": g.get("x"), "y": g.get("y"),
                    "w": g.get("w"), "h": g.get("h")} for g in board.get("groups", [])],
        # Y6: one line per table, the compact face of a table an agent reads back before editing
        # (`table n1 3x2 header=yes filled=4/6`) — the same helper the renderer's numbers come from.
        "tables": [store.table_line(n) for n in board.get("nodes", [])
                   if n.get("kind") == "table"],
        "notes": [{"id": n.get("id"), "text": str(n.get("text", ""))[:60]}
                  for n in board.get("notes", [])],
        "canvas": [board.get("width"), board.get("height")],
    }
