from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import Context

from mcp_server import runtime

logger = logging.getLogger("BlenderMCPServer")


def undo_last_snapshot(ctx: Context) -> str:
    """Rollback scene to the previous snapshot after a bad mutation (blank render, missing objects, bad scale)."""
    try:
        blender = runtime.get_blender_connection(logger)
        result = blender.send_command("undo_last_snapshot", {})
        return json.dumps(result, indent=2)
    except Exception as exc:
        logger.error("Error undoing last snapshot: %s", str(exc))
        return f"Error undoing snapshot: {str(exc)}"


def clear_scene(ctx: Context) -> str:
    """Clear all scene objects using addon-side hierarchy-safe deletion."""
    try:
        blender = runtime.get_blender_connection(logger)
        result = blender.send_command("clear_scene", {})
        return json.dumps(result, indent=2)
    except Exception as exc:
        logger.error("Error clearing scene: %s", str(exc))
        return f"Error clearing scene: {str(exc)}"
