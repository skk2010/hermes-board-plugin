"""HTTP namespace for the desktop board pane — mounted at /api/plugins/hermes-board/.

The pane needs three things from the backend: what boards exist, the board itself, and the
interactive editor page. Everything the user does in the pane comes back as one POST that
replaces the board (with an optimistic `based_on` version check, so a stale pane can never
clobber a change the agent just made).

Writers broadcast `plugin.hermes-board.board.changed` on the gateway event stream; the pane
subscribes with `host.onEvent` and pushes the new board into the editor instead of reloading it.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

from fastapi import APIRouter, HTTPException

PLUGIN_ID = "hermes-board"
EVENT = "board.changed"
_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load(module_name: str, path: pathlib.Path):
    """Load a sibling module by path — `dashboard/` is not an importable package."""
    spec = importlib.util.spec_from_file_location(f"hermes_board_{module_name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


store = _load("store", _ROOT / "store.py")

router = APIRouter()


def _broadcast(board_id: str, version: str, origin: str) -> None:
    """Tell every connected desktop window that this board changed (no-op when nobody listens)."""
    try:
        from hermes_cli.plugin_events import broadcast_plugin_event
    except Exception:                      # older core, or running outside the gateway
        return
    try:
        broadcast_plugin_event(PLUGIN_ID, EVENT,
                               {"id": board_id, "version": version, "origin": origin,
                                "deleted": not version})
    except Exception:
        pass


@router.get("/boards")
async def boards():
    return {"boards": store.list_boards(), "dir": store.BOARD_DIR}


@router.post("/boards")
async def create_board(payload: dict):
    """Create an empty board from the pane. Fails loudly on a name clash instead of clearing it."""
    board_id = str(payload.get("id") or "").strip() or "board-1"
    if store.board_exists(board_id):
        return {"ok": False, "error": "exists", "id": board_id}
    board = store.new_board(board_id, payload.get("title"))
    version = store.save_board(board_id, board)
    _broadcast(board_id, version, "pane")
    return {"ok": True, "id": board_id, "version": version}


@router.delete("/board")
async def remove_board(id: str = store.DEFAULT_ID):
    if not store.delete_board(id):
        raise HTTPException(status_code=404, detail=f"no board '{id}'")
    _broadcast(id, "", "pane")
    return {"ok": True, "id": id}


@router.get("/board")
async def board(id: str = store.DEFAULT_ID):
    try:
        data = store.load_board(id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"id": id, "board": data, "version": store.version_of(data)}


@router.get("/page")
async def page(id: str = store.DEFAULT_ID):
    """The interactive editor as HTML; the pane loads it into a sandboxed frame."""
    try:
        data = store.load_board(id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"id": id, "version": store.version_of(data),
            "html": store.render_html(data, interactive=True, bridge=True,
                                      subtitle=f"board {id}")}


@router.post("/board")
async def put_board(payload: dict):
    """Replace a board with the pane's state. `based_on` guards against stale writes."""
    board_id = payload.get("id") or store.DEFAULT_ID
    data = payload.get("board")
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise HTTPException(status_code=400, detail="body needs {id, board:{nodes:[...]}}")
    try:
        current = store.load_board(board_id)
        current_version = store.version_of(current)
    except FileNotFoundError:
        current_version = None
    based_on = payload.get("based_on")
    if based_on and current_version and based_on != current_version:
        return {"ok": False, "error": "stale", "version": current_version, "board": current}
    origin = payload.get("origin") or "pane"
    # The user's own edit: log the diff and (if a session armed the watch) wake the agent.
    version, event = store.write_board(board_id, data, origin=origin, notify=True)
    _broadcast(board_id, version, origin)
    return {"ok": True, "id": board_id, "version": version,
            "edited": (event or {}).get("summary", ""), "wake": (event or {}).get("wake")}


@router.post("/layout")
async def post_layout(payload: dict):
    """Auto-layout (Sugiyama-lite) for the board the pane is showing.

    Returns the new document so the pane can push it straight into its frame; the run is logged
    as a user edit but does not wake the agent (re-arranging boxes is not a content change).
    """
    board_id = payload.get("id") or store.DEFAULT_ID
    try:
        data = store.load_board(board_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        info = store.boardkit().apply_auto_layout(
            data, direction=payload.get("direction") or "down", bands=bool(payload.get("bands")),
            gap_node=payload.get("gap_node", 40), gap_layer=payload.get("gap_layer", 90))
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    version, event = store.write_board(board_id, data, origin="pane", notify=False)
    _broadcast(board_id, version, "pane")
    return {"ok": True, "id": board_id, "version": version, "board": data,
            "info": {"ranks": len(info["layers"]), "crossings": info["crossings"],
                     "placed": len(info["positions"])},
            "edited": (event or {}).get("summary", "")}


@router.post("/ops")
async def post_ops(payload: dict):
    """Apply ops over HTTP — the same path the agent tools take, for scripts and the CLI."""
    board_id = payload.get("id") or store.DEFAULT_ID
    try:
        data = store.load_board(board_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        did = store.apply_ops(data, payload.get("ops"))
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    origin = payload.get("origin") or "api"
    version, event = store.write_board(board_id, data, origin=origin, notify=True)
    _broadcast(board_id, version, origin)
    return {"ok": True, "id": board_id, "version": version, "did": did,
            "edited": (event or {}).get("summary", ""), "wake": (event or {}).get("wake")}
