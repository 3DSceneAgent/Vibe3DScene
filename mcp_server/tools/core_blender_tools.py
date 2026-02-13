from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from typing import Optional

import requests
from mcp.server.fastmcp import Context, Image
from mcp.types import CallToolResult

from mcp_server import runtime
from scene_agent.utils.rendering import process_and_save_render

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
        return f"Code executed successfully: {result.get('result', '')}"
    except Exception as exc:
        logger.error("Error executing code: %s", str(exc))
        return f"Error executing code: {str(exc)}"


def search_polyhaven_assets(
    ctx: Context,
    asset_type: str = "all",
    categories: str = None,
) -> str:
    """Search PolyHaven assets with optional filters."""
    try:
        params = {}
        if asset_type and asset_type != "all":
            if asset_type not in ["hdris", "textures", "models"]:
                return (
                    f"Error: Invalid asset type: {asset_type}. "
                    "Must be one of: hdris, textures, models, all"
                )
            params["type"] = asset_type
        if categories:
            params["categories"] = categories

        response = requests.get(
            "https://api.polyhaven.com/assets",
            params=params,
            headers=runtime.REQ_HEADERS,
            timeout=30,
        )
        if response.status_code != 200:
            return f"Error: PolyHaven API failed with status code {response.status_code}"

        assets = response.json()
        limited_assets = {}
        for i, (key, value) in enumerate(assets.items()):
            if i >= 20:
                break
            limited_assets[key] = value
        total_count = len(assets)
        returned_count = len(limited_assets)

        formatted_output = f"Found {total_count} assets"
        if categories:
            formatted_output += f" in categories: {categories}"
        formatted_output += f"\nShowing {returned_count} assets:\n\n"

        sorted_assets = sorted(
            limited_assets.items(),
            key=lambda x: x[1].get("download_count", 0),
            reverse=True,
        )
        for asset_id, asset_data in sorted_assets:
            formatted_output += f"- {asset_data.get('name', asset_id)} (ID: {asset_id})\n"
            formatted_output += (
                f"  Type: {['HDRI', 'Texture', 'Model'][asset_data.get('type', 0)]}\n"
            )
            formatted_output += (
                f"  Categories: {', '.join(asset_data.get('categories', []))}\n"
            )
            formatted_output += (
                f"  Downloads: {asset_data.get('download_count', 'Unknown')}\n\n"
            )
        return formatted_output
    except Exception as exc:
        logger.error("Error searching Polyhaven assets: %s", str(exc))
        return f"Error searching Polyhaven assets: {str(exc)}"


def download_polyhaven_asset(
    ctx: Context,
    asset_id: str,
    asset_type: str,
    resolution: str = "1k",
    file_format: str = None,
) -> str:
    """Download and import a PolyHaven asset via Blender addon."""
    try:
        blender = runtime.get_blender_connection(logger)
        result = blender.send_command(
            "download_polyhaven_asset",
            {
                "asset_id": asset_id,
                "asset_type": asset_type,
                "resolution": resolution,
                "file_format": file_format,
            },
        )
        if "error" in result:
            return f"Error: {result['error']}"

        if result.get("success"):
            message = result.get("message", "Asset downloaded and imported successfully")
            if asset_type == "hdris":
                return f"{message}. The HDRI has been set as the world environment."
            if asset_type == "textures":
                material_name = result.get("material", "")
                maps = ", ".join(result.get("maps", []))
                return f"{message}. Created material '{material_name}' with maps: {maps}."
            if asset_type == "models":
                return f"{message}. The model has been imported into the current scene."
            return message
        return f"Failed to download asset: {result.get('message', 'Unknown error')}"
    except Exception as exc:
        logger.error("Error downloading Polyhaven asset: %s", str(exc))
        return f"Error downloading Polyhaven asset: {str(exc)}"


def set_texture(ctx: Context, object_name: str, texture_id: str) -> str:
    """Apply a previously downloaded PolyHaven texture to an object."""
    try:
        blender = runtime.get_blender_connection(logger)
        result = blender.send_command(
            "set_texture", {"object_name": object_name, "texture_id": texture_id}
        )
        if "error" in result:
            return f"Error: {result['error']}"

        if result.get("success"):
            material_name = result.get("material", "")
            maps = ", ".join(result.get("maps", []))
            material_info = result.get("material_info", {})
            node_count = material_info.get("node_count", 0)
            has_nodes = material_info.get("has_nodes", False)
            texture_nodes = material_info.get("texture_nodes", [])

            output = f"Successfully applied texture '{texture_id}' to {object_name}.\n"
            output += f"Using material '{material_name}' with maps: {maps}.\n\n"
            output += f"Material has nodes: {has_nodes}\n"
            output += f"Total node count: {node_count}\n\n"

            if texture_nodes:
                output += "Texture nodes:\n"
                for node in texture_nodes:
                    output += f"- {node['name']} using image: {node['image']}\n"
                    if node["connections"]:
                        output += "  Connections:\n"
                        for conn in node["connections"]:
                            output += f"    {conn}\n"
            else:
                output += "No texture nodes found in the material.\n"
            return output
        return f"Failed to apply texture: {result.get('message', 'Unknown error')}"
    except Exception as exc:
        logger.error("Error applying texture: %s", str(exc))
        return f"Error applying texture: {str(exc)}"


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


def create_camera_from_objects(
    ctx: Context,
    object_names: list[str],
    focal_length: str = "normal",
    azimuth: float = 45,
    elevation: float = 30,
) -> str:
    """Create and position camera to frame specified objects."""
    try:
        blender = runtime.get_blender_connection(logger)
        result = blender.send_command(
            "create_camera_from_objects",
            {
                "object_names": object_names,
                "focal_length": focal_length,
                "azimuth": azimuth,
                "elevation": elevation,
            },
        )
        return json.dumps(result, indent=2)
    except Exception as exc:
        logger.error("Error creating camera from objects: %s", str(exc))
        return f"Error creating camera from objects: {str(exc)}"


def create_camera_from_params(
    ctx: Context,
    x: float,
    y: float,
    z: float,
    rot_x: float,
    rot_y: float,
    rot_z: float,
    focal: float,
) -> str:
    """Create camera from explicit parameters."""
    try:
        blender = runtime.get_blender_connection(logger)
        result = blender.send_command(
            "create_camera_from_params",
            {"x": x, "y": y, "z": z, "rot_x": rot_x, "rot_y": rot_y, "rot_z": rot_z, "focal": focal},
        )
        return json.dumps(result, indent=2)
    except Exception as exc:
        logger.error("Error creating camera from params: %s", str(exc))
        return f"Error creating camera from params: {str(exc)}"


def render_from_objects(
    ctx: Context,
    object_names: list[str],
    mode: str = "rgb",
    focal_length: str = "normal",
    azimuth: float = 45,
    elevation: float = 30,
) -> CallToolResult:
    """Render image by auto-creating camera and return markdown image link."""
    try:
        blender = runtime.get_blender_connection(logger)
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
        if not os.path.exists(filepath):
            raise Exception(f"Rendered file not found: {filepath}")

        thread_id = "unknown"
        if hasattr(ctx, "request_context") and ctx.request_context:
            if hasattr(ctx.request_context, "get"):
                thread_id = ctx.request_context.get("thread_id", "unknown")
            elif isinstance(ctx.request_context, dict):
                thread_id = ctx.request_context.get("thread_id", "unknown")
        camera_name = result.get("camera", "auto")
        image_url = process_and_save_render(filepath, thread_id, camera_name, logger=logger)
        try:
            os.remove(filepath)
        except Exception:
            pass
        logger.info("Render available at: %s", image_url)
        objects_str = ", ".join(object_names)
        markdown_image = f"![Render of {objects_str}]({image_url})"
        return CallToolResult(content=[{"type": "text", "text": markdown_image}], isError=False)
    except Exception as exc:
        logger.error("Error rendering from objects: %s", str(exc))
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
        if not os.path.exists(filepath):
            raise Exception(f"Rendered file not found: {filepath}")

        thread_id = "unknown"
        if hasattr(ctx, "request_context") and ctx.request_context:
            if hasattr(ctx.request_context, "get"):
                thread_id = ctx.request_context.get("thread_id", "unknown")
            elif isinstance(ctx.request_context, dict):
                thread_id = ctx.request_context.get("thread_id", "unknown")
        image_url = process_and_save_render(filepath, thread_id, camera_name, logger=logger)
        try:
            os.remove(filepath)
        except Exception:
            pass
        logger.info("Render available at: %s", image_url)
        markdown_image = f"![Render from {camera_name}]({image_url})"
        return CallToolResult(content=[{"type": "text", "text": markdown_image}], isError=False)
    except Exception as exc:
        logger.error("Error rendering from camera: %s", str(exc))
        raise Exception(f"Render failed: {str(exc)}")
