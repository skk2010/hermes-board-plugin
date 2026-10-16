"""Process-wide handle shared by the plugin's tools (the agent turn) and its dashboard backend.

The board pane and the agent have to reach the same objects: a write from the pane must be able
to wake the session the agent armed, and both halves must see one `ctx` (the sanctioned
`PluginContext.inject_message` lives only there). Hermes loads the tools through the plugin
package and the backend by path, so `find_or_load` resolves by FILE — the first copy loaded in
this process wins and every later caller gets that same instance. If the backend really does run
in a separate process (hosted isolation), nothing breaks: `inject_message` reports "no live host"
and the edit still lands in the event log, where `board_watch` reads it.

`PluginContext.inject_message` rules this code respects:
  * CLI / desktop-REPL session: queues into the pending input — no config needed;
  * messaging gateway: needs `session_key` or an `origin` mapping AND
    `plugins.entries.hermes-board.allow_gateway_injection: true` in this profile's config.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys

logger = logging.getLogger(__name__)

HERE = os.path.dirname(os.path.abspath(__file__))
STATE: dict = {
    "ctx": None,          # the PluginContext from register()
    "target": None,       # {"session_key": str} and/or {"origin": {...}} armed by a session
    "last_wake": None,    # {"at", "ok", "content", "detail"} of the most recent attempt
}


def find_or_load(path: str | None = None):
    """Return THIS module (the instance already loaded from `path`, or a fresh path load)."""
    wanted = os.path.abspath(path or os.path.join(HERE, "runtime.py"))
    for module in list(sys.modules.values()):
        candidate = getattr(module, "__file__", None)
        if candidate and os.path.abspath(candidate) == wanted and hasattr(module, "STATE"):
            return module
    name = "hermes_board_runtime"
    spec = importlib.util.spec_from_file_location(name, wanted)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def bind(ctx) -> None:
    """Remember the PluginContext so a later pane write can wake this process's session."""
    STATE["ctx"] = ctx


def bound() -> bool:
    return STATE["ctx"] is not None


def target_from_kwargs(kwargs: dict) -> dict:
    """Best-effort wake target from whatever the tool dispatcher passed to a handler.

    Hermes signature-inspects handlers, so a tool that declares `**kwargs` sees fields like
    `session_key` / `session_id` when the host provides them. An `origin` mapping in the
    `SessionSource.to_dict()` shape is the gateway path; a bare session key is the CLI/TUI path.
    """
    target: dict = {}
    for key in ("session_key", "session_id", "chat_id", "thread_id", "user_id", "platform"):
        value = kwargs.get(key)
        if isinstance(value, (str, int)) and str(value):
            target[key] = str(value)
    origin = kwargs.get("origin") or kwargs.get("source")
    if isinstance(origin, dict) and origin:
        target["origin"] = {k: v for k, v in origin.items()
                            if isinstance(v, (str, int, type(None)))}
    return target


def arm(target: dict) -> dict:
    """Persist the wake target in this process (the file copy lives in the store)."""
    STATE["target"] = dict(target) if target else None
    return STATE["target"] or {}


def injected_target() -> dict:
    return dict(STATE["target"] or {})


def inject_message(content: str, target: dict | None = None) -> dict:
    """Wake the armed session through `PluginContext.inject_message`. Never raises.

    Target shape follows the host's own routing: `origin` (a `SessionSource.to_dict()` mapping)
    names a MESSAGING chat to continue, `session_key` names a durable local (Ink TUI / desktop)
    session — and passing both is refused, so exactly one is sent. A classic CLI REPL host needs
    neither. The returned dict always says what was sent and what the host said.
    """
    target = dict(target or STATE["target"] or {})
    result = {"at": _now(), "content": content, "ok": False, "detail": "", "target": target}
    ctx = STATE["ctx"]
    if ctx is None:
        result["detail"] = ("no live plugin host in this process — the edit is in the event log, "
                            "read it with board_watch")
    else:
        kwargs: dict = {}
        if target.get("origin"):
            kwargs["origin"] = target["origin"]
        elif target.get("session_key") or target.get("session_id"):
            kwargs["session_key"] = str(target.get("session_key") or target.get("session_id"))
        try:
            ok = ctx.inject_message(content, **kwargs)
            result["ok"] = bool(ok)
            result["sent"] = {k: v for k, v in kwargs.items() if k != "origin"} or \
                ({"origin": target["origin"].get("platform", "?")} if "origin" in kwargs else {})
            if not ok:
                result["detail"] = (
                    "the host refused the injection — a messaging chat needs "
                    "plugins.entries.hermes-board.allow_gateway_injection: true in this profile's "
                    "config, and a gateway wake needs an armed origin/session_key")
        except Exception as exc:                       # never let a wake kill a save
            result["detail"] = f"inject_message failed: {exc}"
            logger.debug("hermes-board: wake failed", exc_info=True)
    STATE["last_wake"] = result
    return result


def _now() -> str:
    import datetime as dt
    return dt.datetime.now().isoformat(timespec="seconds")


def status() -> dict:
    return {"ctx_bound": bound(), "target": injected_target(), "last_wake": STATE["last_wake"]}
