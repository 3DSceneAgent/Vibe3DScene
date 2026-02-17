from __future__ import annotations

import logging

import requests
from mcp.server.fastmcp import Context

from mcp_server import runtime

logger = logging.getLogger("BlenderMCPServer")
_POLYHAVEN_MAX_SEARCH_RESULTS = 5


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
        if not isinstance(assets, dict):
            return "Error: PolyHaven API returned an unexpected payload."

        sorted_assets = sorted(
            assets.items(),
            key=lambda x: x[1].get("download_count", 0),
            reverse=True,
        )
        limited_assets = sorted_assets[:_POLYHAVEN_MAX_SEARCH_RESULTS]
        total_count = len(assets)
        returned_count = len(limited_assets)

        formatted_output = f"Found {total_count} assets"
        if categories:
            formatted_output += f" in categories: {categories}"
        formatted_output += f"\nShowing {returned_count} assets:\n\n"

        for asset_id, asset_data in limited_assets:
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
