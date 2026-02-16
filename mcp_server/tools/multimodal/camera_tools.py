from __future__ import annotations

import logging
import os
import tempfile
import time
from typing import Optional

from mcp.server.fastmcp import Context
from mcp.types import CallToolResult

from mcp_server import runtime
from scene_agent.utils.rendering import process_and_save_render

logger = logging.getLogger("BlenderMCPServer")


def _extract_thread_id(ctx: Context) -> str:
    thread_id = "unknown"
    if hasattr(ctx, "request_context") and ctx.request_context:
        if hasattr(ctx.request_context, "get"):
            thread_id = ctx.request_context.get("thread_id", "unknown")
        elif isinstance(ctx.request_context, dict):
            thread_id = ctx.request_context.get("thread_id", "unknown")
    return thread_id


def _render_result_to_markdown(
    *,
    filepath: str,
    thread_id: str,
    camera_name: str,
    alt_text: str,
) -> CallToolResult:
    if not os.path.exists(filepath):
        raise Exception(f"Rendered file not found: {filepath}")
    image_url = process_and_save_render(filepath, thread_id, camera_name, logger=logger)
    try:
        os.remove(filepath)
    except Exception:
        pass
    logger.info("Render available at: %s", image_url)
    markdown_image = f"![{alt_text}]({image_url})"
    return CallToolResult(content=[{"type": "text", "text": markdown_image}], isError=False)


def _is_no_valid_mesh_error(error_text: str) -> bool:
    normalized = (error_text or "").lower()
    return "no valid mesh objects found" in normalized


def _render_all_meshes_fallback(
    *,
    ctx: Context,
    blender,
    mode: str,
    focal_length: str,
    azimuth: float,
    elevation: float,
) -> CallToolResult | None:
    temp_path = os.path.join(
        tempfile.gettempdir(), f"blender_render_fallback_{os.getpid()}_{int(time.time())}.png"
    )
    result = blender.send_command(
        "camera_observe",
        {
            "object_names": [],
            "mode": "single_view",
            "focal_length": focal_length,
            "azimuth": azimuth,
            "elevation": elevation,
            "reuse_cameras": True,
            "filepath": temp_path,
        },
    )
    if not result.get("success"):
        return None
    filepath = result["filepath"]
    thread_id = _extract_thread_id(ctx)
    camera_name = result.get("camera", "auto")
    return _render_result_to_markdown(
        filepath=filepath,
        thread_id=thread_id,
        camera_name=camera_name,
        alt_text=f"Fallback render ({mode})",
    )


def render_from_objects(
    ctx: Context,
    object_names: list[str],
    mode: str = "rgb",
    focal_length: str = "normal",
    azimuth: float = 45,
    elevation: float = 30,
) -> CallToolResult:
    """Render image by auto-creating camera and return markdown image link."""
    blender = None
    try:
        blender = runtime.get_blender_connection(logger)
        thread_id = _extract_thread_id(ctx)
        temp_path = os.path.join(
            tempfile.gettempdir(), f"blender_render_{os.getpid()}_{int(time.time())}.png"
        )
        result = blender.send_command(
            "render_from_objects",
            {
                "object_names": object_names,
                "mode": mode,
                "focal_length": focal_length,
                "azimuth": azimuth,
                "elevation": elevation,
                "filepath": temp_path,
            },
        )
        if not result.get("success"):
            raise Exception("Render failed")
        filepath = result["filepath"]
        camera_name = result.get("camera", "auto")
        objects_str = ", ".join(object_names)
        return _render_result_to_markdown(
            filepath=filepath,
            thread_id=thread_id,
            camera_name=camera_name,
            alt_text=f"Render of {objects_str}",
        )
    except Exception as exc:
        error_text = str(exc)
        if _is_no_valid_mesh_error(error_text):
            logger.warning(
                "render_from_objects fallback triggered for target objects %s: %s",
                object_names,
                error_text,
            )
            if blender is not None:
                try:
                    fallback_result = _render_all_meshes_fallback(
                        ctx=ctx,
                        blender=blender,
                        mode=mode,
                        focal_length=focal_length,
                        azimuth=azimuth,
                        elevation=elevation,
                    )
                    if fallback_result is not None:
                        return fallback_result
                except Exception as fallback_exc:
                    logger.warning("render_from_objects fallback failed: %s", str(fallback_exc))
            fallback_note = (
                "Render skipped: no valid mesh objects were found for this request. "
                "Try `get_scene_info` to inspect current object names."
            )
            return CallToolResult(
                content=[{"type": "text", "text": fallback_note}],
                isError=False,
            )
        logger.error("Error rendering from objects: %s", error_text)
        raise Exception(f"Render failed: {str(exc)}")


def render_from_camera(
    ctx: Context,
    camera_name: str,
    object_names: Optional[list[str]] = None,
    mode: str = "rgb",
) -> CallToolResult:
    """Render from an existing camera and return markdown image link."""
    try:
        blender = runtime.get_blender_connection(logger)
        temp_path = os.path.join(
            tempfile.gettempdir(), f"blender_render_{os.getpid()}_{int(time.time())}.png"
        )
        result = blender.send_command(
            "render_from_camera",
            {
                "camera_name": camera_name,
                "object_names": object_names,
                "mode": mode,
                "filepath": temp_path,
            },
        )
        if not result.get("success"):
            raise Exception("Render failed")
        filepath = result["filepath"]
        thread_id = _extract_thread_id(ctx)
        return _render_result_to_markdown(
            filepath=filepath,
            thread_id=thread_id,
            camera_name=camera_name,
            alt_text=f"Render from {camera_name}",
        )
    except Exception as exc:
        logger.error("Error rendering from camera: %s", str(exc))
        raise Exception(f"Render failed: {str(exc)}")


def camera_observe(
    ctx: Context,
    object_names: list[str],
    mode: str = "multi_view",
    focal_length: str = "normal",
    azimuth: float = 45,
    elevation: float = 30,
    reuse_cameras: bool = True,
) -> CallToolResult:
    """Observe objects with single or multi-view camera strategy."""
    try:
        blender = runtime.get_blender_connection(logger)
        temp_path = os.path.join(
            tempfile.gettempdir(), f"blender_observe_{os.getpid()}_{int(time.time())}.png"
        )
        result = blender.send_command(
            "camera_observe",
            {
                "object_names": object_names,
                "mode": mode,
                "focal_length": focal_length,
                "azimuth": azimuth,
                "elevation": elevation,
                "reuse_cameras": reuse_cameras,
                "filepath": temp_path,
            },
        )
        if not result.get("success"):
            raise Exception("Camera observe failed")
        filepath = result["filepath"]
        thread_id = _extract_thread_id(ctx)
        camera_name = result.get("camera", "observe")
        return _render_result_to_markdown(
            filepath=filepath,
            thread_id=thread_id,
            camera_name=camera_name,
            alt_text=f"Observation view ({mode})",
        )
    except Exception as exc:
        logger.error("Error observing scene: %s", str(exc))
        raise Exception(f"Camera observe failed: {str(exc)}")


def camera_act(
    ctx: Context,
    action: str = "focus",
    object_names: Optional[list[str]] = None,
    direction: Optional[str] = None,
    step_scale: float = 1.0,
    keep_distance: bool = True,
    mode: str = "rgb",
) -> CallToolResult:
    """Actively control camera with focus/move/zoom and render."""
    try:
        blender = runtime.get_blender_connection(logger)
        temp_path = os.path.join(
            tempfile.gettempdir(), f"blender_camera_act_{os.getpid()}_{int(time.time())}.png"
        )
        result = blender.send_command(
            "camera_act",
            {
                "action": action,
                "object_names": object_names or [],
                "direction": direction,
                "step_scale": step_scale,
                "keep_distance": keep_distance,
                "mode": mode,
                "filepath": temp_path,
            },
        )
        if not result.get("success"):
            raise Exception("Camera act failed")
        filepath = result["filepath"]
        thread_id = _extract_thread_id(ctx)
        camera_name = result.get("camera", "camera_act")
        return _render_result_to_markdown(
            filepath=filepath,
            thread_id=thread_id,
            camera_name=camera_name,
            alt_text=f"Camera action ({action})",
        )
    except Exception as exc:
        logger.error("Error running camera action: %s", str(exc))
        raise Exception(f"Camera action failed: {str(exc)}")


def camera_set_pose(
    ctx: Context,
    location: list[float],
    rotation_euler: list[float],
    focal_mm: float = 50.0,
    mode: str = "rgb",
    object_names: Optional[list[str]] = None,
) -> CallToolResult:
    """Set absolute camera pose and render from that viewpoint."""
    try:
        blender = runtime.get_blender_connection(logger)
        temp_path = os.path.join(
            tempfile.gettempdir(), f"blender_camera_pose_{os.getpid()}_{int(time.time())}.png"
        )
        result = blender.send_command(
            "camera_set_pose",
            {
                "location": location,
                "rotation_euler": rotation_euler,
                "focal_mm": focal_mm,
                "mode": mode,
                "object_names": object_names or [],
                "filepath": temp_path,
            },
        )
        if not result.get("success"):
            raise Exception("Camera set pose failed")
        filepath = result["filepath"]
        thread_id = _extract_thread_id(ctx)
        camera_name = result.get("camera", "camera_set_pose")
        return _render_result_to_markdown(
            filepath=filepath,
            thread_id=thread_id,
            camera_name=camera_name,
            alt_text="Camera pose render",
        )
    except Exception as exc:
        logger.error("Error setting camera pose: %s", str(exc))
        raise Exception(f"Camera set pose failed: {str(exc)}")
