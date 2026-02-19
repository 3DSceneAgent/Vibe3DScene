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
