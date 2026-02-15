# blender_mcpv_server.py
from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict

from mcp.server.fastmcp import FastMCP

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scene_agent.env import load_project_dotenv

load_project_dotenv()

from mcp_server import runtime
from mcp_server.tool_registry import register_mcp_tools
from mcp_server.tools.strategy import asset_creation_strategy_text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("BlenderMCPServer")

print(
    "[mcp_server] pid=%s host=%s port=%s blender=%s:%s python=%s"
    % (
        os.getpid(),
        os.getenv("MCP_SERVER_HOST", "localhost"),
        os.getenv("MCP_SERVER_PORT", "9877"),
        os.getenv("BLENDER_HOST", "localhost"),
        os.getenv("BLENDER_PORT", "9876"),
        sys.executable,
    ),
    flush=True,
)

DEFAULT_SERVER_HOST = os.getenv("MCP_SERVER_HOST", "localhost")
DEFAULT_SERVER_PORT = int(os.getenv("MCP_SERVER_PORT", "9877"))
_polyhaven_meta_info = runtime.load_polyhaven_meta_info(logger)


def record_startup() -> None:
    """Placeholder for telemetry - can be implemented if needed."""


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """Manage server startup and shutdown lifecycle."""
    try:
        logger.info("BlenderMCP server starting up")
        try:
            record_startup()
        except Exception as exc:
            logger.debug("Failed to record startup telemetry: %s", exc)

        try:
            runtime.get_blender_connection(logger)
            logger.info("Successfully connected to Blender on startup")
        except Exception as exc:
            logger.warning("Could not connect to Blender on startup: %s", str(exc))
            logger.warning(
                "Make sure the Blender addon is running before using Blender resources or tools"
            )

        yield {}
    finally:
        runtime.disconnect_blender_connection(logger)
        logger.info("BlenderMCP server shut down")


mcp = FastMCP(
    "BlenderMCP",
    lifespan=server_lifespan,
    host=DEFAULT_SERVER_HOST,
    port=DEFAULT_SERVER_PORT,
)


@mcp.resource("resource://polyhaven_types")
def get_polyhaven_types() -> str:
    return json.dumps(["hdris", "textures", "models"], indent=2)


@mcp.resource("resource://polyhaven_categories/{category}")
def get_polyhaven_categories(category: str) -> str:
    if category not in ["hdris", "textures", "models", "all"]:
        return (
            f"Error: Invalid asset type: {category}. Must be one of: "
            "hdris, textures, models, all"
        )

    categories = _polyhaven_meta_info.get(category)
    if not categories:
        return f"Error: No cached categories for asset type: {category}"

    formatted_output = f"Categories for {category}:\n\n"
    sorted_categories = sorted(categories.items(), key=lambda x: x[1], reverse=True)
    for category_name, count in sorted_categories:
        formatted_output += f"- {category_name}: {count} assets\n"
    return formatted_output


@mcp.prompt()
def asset_creation_strategy() -> str:
    """Defines the preferred strategy for creating assets in Blender."""
    return asset_creation_strategy_text()


def main() -> None:
    """Run the MCP server."""
    register_mcp_tools(mcp, logger)
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
