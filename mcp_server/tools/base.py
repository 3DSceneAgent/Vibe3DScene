from __future__ import annotations

import json
import logging
import os
import tempfile

from mcp.server.fastmcp import Context, Image

from mcp_server import runtime

logger = logging.getLogger("BlenderMCPServer")


def get_scene_info(ctx: Context) -> str:
    """Get detailed information about the current Blender scene."""
    try:
        blender = runtime.get_blender_connection(logger)
        result = blender.send_command("get_scene_info")
        return json.dumps(result, indent=2)
    except Exception as exc:
        logger.error("Error getting scene info from Blender: %s", str(exc))
        return f"Error getting scene info: {str(exc)}"


def get_object_info(ctx: Context, object_name: str) -> str:
    """Get detailed information about a specific Blender object."""
    try:
        blender = runtime.get_blender_connection(logger)
        result = blender.send_command("get_object_info", {"name": object_name})
        return json.dumps(result, indent=2)
    except Exception as exc:
        logger.error("Error getting object info from Blender: %s", str(exc))
        return f"Error getting object info: {str(exc)}"


def get_viewport_screenshot(ctx: Context, max_size: int = 800) -> Image:
    """Capture a screenshot of the current Blender viewport."""
    try:
        blender = runtime.get_blender_connection(logger)
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"blender_screenshot_{os.getpid()}.png")
        result = blender.send_command(
            "get_viewport_screenshot",
            {"max_size": max_size, "filepath": temp_path, "format": "png"},
        )
        if "error" in result:
            raise Exception(result["error"])
        if not os.path.exists(temp_path):
            raise Exception("Screenshot file was not created")
        with open(temp_path, "rb") as handle:
            image_bytes = handle.read()
        os.remove(temp_path)
        return Image(data=image_bytes, format="png")
    except Exception as exc:
        logger.error("Error capturing screenshot: %s", str(exc))
        raise Exception(f"Screenshot failed: {str(exc)}")


def execute_blender_code(ctx: Context, code: str) -> str:
    """Execute arbitrary Python code in Blender."""
    try:
        blender = runtime.get_blender_connection(logger)
        result = blender.send_command("execute_code", {"code": code})
        return (
            "Status: success\n"
            "Script:\n"
            "```python\n"
            f"{code}\n"
            "```\n"
            f"Result: {result.get('result', '')}"
        )
    except Exception as exc:
        logger.error("Error executing code: %s", str(exc))
        return (
            "Status: error\n"
            "Script:\n"
            "```python\n"
            f"{code}\n"
            "```\n"
            f"Result: {str(exc)}"
        )


def import_glb_model(ctx: Context, model_url: str, object_name: str = None) -> str:
    """Import a GLB model from URL into Blender."""
    try:
        blender = runtime.get_blender_connection(logger)
        object_name = object_name or "ImportedModel"
        result = blender.send_command(
            "import_glb_model",
            {"model_url": model_url, "object_name": object_name},
        )
        if "error" in result:
            return f"Error importing model: {result['error']}"
        if result.get("success"):
            imported_objects = result.get("imported_objects", [])
            message = f"Successfully imported model from '{model_url}'\n"
            message += f"Imported {len(imported_objects)} object(s): {', '.join(imported_objects)}\n"
            if result.get("bounding_box"):
                bbox = result["bounding_box"]
                message += f"Bounding box: min={bbox.get('min')}, max={bbox.get('max')}\n"
            return message
        return f"Failed to import model: {result.get('message', 'Unknown error')}"
    except Exception as exc:
        logger.error("Error importing GLB model: %s", str(exc))
        return f"Error importing GLB model: {str(exc)}"
