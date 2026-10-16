"""hermes-board — a board the agent draws and the user edits, as a desktop pane.

Two halves in one folder:

* the agent half (`plugin.yaml`, `tools.py`, `store.py`) registers `board_*` tools so any session
  can build a diagram with ops, including layered auto-layout and shared-trunk buses;
* the desktop half (`dashboard/plugin_api.py`, `desktop/plugin.js`) serves the interactive
  editor over `/api/plugins/hermes-board/*` and shows it in a pane beside the conversation.

`register()` wires only the public plugin API (`ctx.register_tool`).
"""

from __future__ import annotations

import logging

from . import runtime
from .tools import register_tools

logger = logging.getLogger(__name__)

PLUGIN_NAME = "hermes-board"


def register(ctx) -> None:
    # The handle the pane's backend needs to wake this process's session (runtime.py).
    runtime.bind(ctx)
    register_tools(ctx)
    logger.info("%s: board tools registered (wake %s)",
                PLUGIN_NAME, "available" if runtime.bound() else "unavailable")
