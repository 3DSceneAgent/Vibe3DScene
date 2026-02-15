from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import Context

from mcp_server import runtime

logger = logging.getLogger("BlenderMCPServer")


def undo_last_snapshot(ctx: Context) -> str:
    """Undo last mutating step by loading previous snapshot."""
    try:
        blender = runtime.get_blender_connection(logger)
        result = blender.send_command("undo_last_snapshot", {})
        return json.dumps(result, indent=2)
    except Exception as exc:
        logger.error("Error undoing last snapshot: %s", str(exc))
        return f"Error undoing snapshot: {str(exc)}"


def get_session_persistence_status(ctx: Context) -> str:
    """Get current session persistence metadata from addon."""
    try:
        blender = runtime.get_blender_connection(logger)
        result = blender.send_command("get_session_persistence_status", {})
        return json.dumps(result, indent=2)
    except Exception as exc:
        logger.error("Error getting session persistence status: %s", str(exc))
        return f"Error getting session persistence status: {str(exc)}"
