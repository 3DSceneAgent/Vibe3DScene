from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from typing import Any

from mcp.server.fastmcp import Context, Image

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


def get_viewport_screenshot(ctx: Context, max_size: int = 800) -> Image:
    """Capture current Blender viewport as an MCP Image (local-client only)."""
    del ctx
    mode = runtime.get_blender_mode()
    if mode != "local-client":
        raise Exception(
            "get_viewport_screenshot is only available in BLENDER_MODE=local-client. "
            f"Current mode: {mode or '<unset>'}."
        )

    temp_path = os.path.join(
        tempfile.gettempdir(),
        f"blender_viewport_{os.getpid()}_{int(time.time() * 1000)}.png",
    )
    image_path = temp_path
    try:
        blender = runtime.get_blender_connection(logger)
        result = blender.send_command(
            "get_viewport_screenshot",
            {"max_size": max_size, "filepath": temp_path, "format": "png"},
        )
        if isinstance(result, dict):
            error = result.get("error")
            if isinstance(error, str) and error.strip():
                raise Exception(error)
            reported_path = result.get("filepath")
            if isinstance(reported_path, str) and reported_path.strip():
                image_path = reported_path

        if not os.path.exists(image_path):
            raise Exception("Screenshot file was not created by Blender.")
        with open(image_path, "rb") as handle:
            return Image(data=handle.read(), format="png")
    finally:
        for path in {temp_path, image_path}:
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass


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


def execute_blender_code(
    ctx: Context,
    code: str,
    safe_mode: bool = True,
    rollback_on_guard_fail: bool = True,
    validate_scene: bool = True,
) -> str:
    """Execute Python code in Blender with transactional safeguards."""
    try:
        blender = runtime.get_blender_connection(logger)
        result = blender.send_command(
            "execute_code",
            {
                "code": code,
                "safe_mode": safe_mode,
                "rollback_on_guard_fail": rollback_on_guard_fail,
                "validate_scene": validate_scene,
            },
        )
        transaction_json = json.dumps(result, indent=2, ensure_ascii=False)
        return (
            "Status: success\n"
            "Mode: execute_code(transactional)\n"
            f"Options: safe_mode={safe_mode}, validate_scene={validate_scene}, rollback_on_guard_fail={rollback_on_guard_fail}\n"
            "Script:\n"
            "```python\n"
            f"{code}\n"
            "```\n"
            "Transaction:\n"
            "```json\n"
            f"{transaction_json}\n"
            "```"
        )
    except Exception as exc:
        logger.error("Error executing code: %s", str(exc))
        return (
            "Status: error\n"
            "Mode: execute_code(transactional)\n"
            f"Options: safe_mode={safe_mode}, validate_scene={validate_scene}, rollback_on_guard_fail={rollback_on_guard_fail}\n"
            "Script:\n"
            "```python\n"
            f"{code}\n"
            "```\n"
            f"Result: {str(exc)}"
        )


def import_glb_model(ctx: Context, model_url: str, object_name: str = None) -> str:
    """Import a remote model URL into Blender.

    Legacy tool name retained for compatibility. The Blender-side importer also
    supports Hunyuan3D OBJ ZIP bundles in addition to direct GLB/GLTF/FBX/OBJ files.
    """
    def _extract_object_names(raw: Any) -> list[str]:
        if not isinstance(raw, (list, tuple, set)):
            return []
        names: list[str] = []
        for item in raw:
            name = ""
            if isinstance(item, str):
                name = item.strip()
            elif isinstance(item, dict):
                for key in ("name", "object_name", "id"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        name = value.strip()
                        break
            elif item is not None:
                name = str(item).strip()
            if name:
                names.append(name)
        return names

    def _normalize_import_result(raw_result: Any) -> dict[str, Any]:
        if isinstance(raw_result, dict):
            return raw_result

        if isinstance(raw_result, str):
            text = raw_result.strip()
            if text:
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    return {"success": False, "message": text}
                return _normalize_import_result(parsed)
            return {"success": False, "message": "Empty response from Blender import command."}

        names = _extract_object_names(raw_result)
        if names:
            return {
                "success": True,
                "imported_objects": names,
                "num_objects": len(names),
                "bounding_box": None,
            }

        return {
            "success": False,
            "message": (
                "Unexpected import response type from Blender: "
                f"{type(raw_result).__name__}"
            ),
        }

    def _format_bounding_box(raw_bbox: Any) -> str | None:
        if isinstance(raw_bbox, dict):
            return f"Bounding box: min={raw_bbox.get('min')}, max={raw_bbox.get('max')}"
        if (
            isinstance(raw_bbox, (list, tuple))
            and len(raw_bbox) == 2
            and all(isinstance(corner, (list, tuple)) for corner in raw_bbox)
        ):
            return f"Bounding box: min={raw_bbox[0]}, max={raw_bbox[1]}"
        return None

    try:
        blender = runtime.get_blender_connection(logger)
        object_name = object_name or "ImportedModel"
        raw_result = blender.send_command(
            "import_glb_model",
            {"model_url": model_url, "object_name": object_name},
        )
        result = _normalize_import_result(raw_result)
        if not isinstance(raw_result, dict):
            logger.warning(
                "import_glb_model received non-dict result from Blender (%s); normalized response applied.",
                type(raw_result).__name__,
            )
        if "error" in result:
            return f"Error importing model: {result['error']}"
        if result.get("success"):
            imported_objects = result.get("imported_objects", [])
            message = f"Successfully imported model from '{model_url}'\n"
            message += f"Imported {len(imported_objects)} object(s): {', '.join(imported_objects)}\n"
            packed_images = result.get("packed_images", [])
            if isinstance(packed_images, list) and packed_images:
                message += f"Packed {len(packed_images)} texture image(s) into the Blender scene.\n"
            if result.get("bounding_box"):
                bbox_line = _format_bounding_box(result["bounding_box"])
                if bbox_line:
                    message += f"{bbox_line}\n"
            return message
        return f"Failed to import model: {result.get('message', 'Unknown error')}"
    except Exception as exc:
        logger.error("Error importing model via import_glb_model: %s", str(exc))
        return f"Error importing model: {str(exc)}"


def import_blend_contents(
    ctx: Context,
    blend_file_path: str,
    import_mode: str = "auto",
    collection_names: list[str] | str | None = None,
    object_names: list[str] | str | None = None,
    link: bool = False,
) -> str:
    """Import collections/objects from a local .blend file into the current scene."""
    try:
        def _dedupe_keep_order(values: list[str]) -> list[str]:
            deduped: list[str] = []
            seen: set[str] = set()
            for value in values:
                if value in seen:
                    continue
                seen.add(value)
                deduped.append(value)
            return deduped

        normalized_collections = (
            _dedupe_keep_order(_normalize_object_names(collection_names))
            if collection_names
            else []
        )
        normalized_objects = (
            _dedupe_keep_order(_normalize_object_names(object_names))
            if object_names
            else []
        )

        blender = runtime.get_blender_connection(logger)
        result = blender.send_command(
            "import_blend_contents",
            {
                "blend_file_path": blend_file_path,
                "import_mode": import_mode,
                "collection_names": normalized_collections,
                "object_names": normalized_objects,
                "link": bool(link),
            },
        )

        if not isinstance(result, dict):
            return f"Failed to import blend contents: unexpected response type {type(result).__name__}"
        if "error" in result:
            return f"Failed to import blend contents: {result['error']}"
        if not result.get("success"):
            return f"Failed to import blend contents: {result.get('message', 'Unknown error')}"

        imported_collections = result.get("imported_collections", [])
        imported_objects = result.get("imported_objects", [])
        linked_scene_collections = result.get("linked_scene_collections", [])
        linked_scene_objects = result.get("linked_scene_objects", [])
        missing_collections = result.get("requested_collections_missing", [])
        missing_objects = result.get("requested_objects_missing", [])

        message_lines = [
            f"Successfully imported blend file: {result.get('blend_file_path', blend_file_path)}",
            (
                "Import mode: "
                f"{result.get('import_mode', import_mode)} (link={bool(result.get('link', link))})"
            ),
            (
                "Imported collections: "
                f"{len(imported_collections)} ({', '.join(imported_collections) if imported_collections else 'none'})"
            ),
            (
                "Imported objects: "
                f"{len(imported_objects)} ({', '.join(imported_objects) if imported_objects else 'none'})"
            ),
        ]

        if linked_scene_collections:
            message_lines.append(
                "Linked scene collections: " + ", ".join(linked_scene_collections)
            )
        if linked_scene_objects:
            message_lines.append("Linked scene objects: " + ", ".join(linked_scene_objects))
        if missing_collections:
            message_lines.append("Requested collections not found: " + ", ".join(missing_collections))
        if missing_objects:
            message_lines.append("Requested objects not found: " + ", ".join(missing_objects))

        return "\n".join(message_lines)
    except Exception as exc:
        logger.error("Error importing blend contents: %s", str(exc))
        return f"Error importing blend contents: {str(exc)}"
