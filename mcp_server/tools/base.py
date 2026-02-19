from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import Context

from mcp_server import runtime

logger = logging.getLogger("BlenderMCPServer")


def _normalize_object_names(object_names: list[str] | str) -> list[str]:
    if isinstance(object_names, list):
        normalized: list[str] = []
        for raw_name in object_names:
            if not isinstance(raw_name, str):
                continue
            name = raw_name.strip()
            if name:
                normalized.append(name)
        return normalized

    text = object_names.strip()
    if not text:
        return []

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, list):
        return _normalize_object_names(parsed)

    if any(separator in text for separator in (",", "\n", ";")):
        raw_parts = text.replace("\n", ",").replace(";", ",").split(",")
    else:
        raw_parts = [text]

    normalized: list[str] = []
    for part in raw_parts:
        name = part.strip().strip("\"'`")
        if name:
            normalized.append(name)
    return normalized


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


def delete_objects(
    ctx: Context,
    object_names: list[str] | str,
    mode: str = "cascade",
    strict: bool = True,
    dry_run: bool = False,
    ignore_missing: bool = False,
    name_match_mode: str = "exact",
) -> str:
    """Delete Blender objects with hierarchy-aware semantics.

    Modes:
    - cascade: delete requested objects and all descendants
    - detach_keep_world: keep descendants, unparent to world while preserving world transform
    - reparent_to_parent_keep_world: keep descendants, reparent to deleted object's parent
    Name matching:
    - exact: exact object name lookup
    - contains: case-insensitive contains lookup (exact-normalized match preferred)
    """
    try:
        normalized_names = _normalize_object_names(object_names)
        blender = runtime.get_blender_connection(logger)
        result = blender.send_command(
            "delete_objects",
            {
                "object_names": normalized_names,
                "mode": mode,
                "strict": strict,
                "dry_run": dry_run,
                "ignore_missing": ignore_missing,
                "name_match_mode": name_match_mode,
            },
        )
        return json.dumps(result, indent=2)
    except Exception as exc:
        logger.error("Error deleting objects from Blender: %s", str(exc))
        return f"Error deleting objects: {str(exc)}"


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
