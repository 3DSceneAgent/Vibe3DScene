from __future__ import annotations

import json
import logging
import os
import tempfile

from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from mcp.server.fastmcp import Context

from mcp_server import runtime
from mcp_server.tools.base import import_glb_model
from scene_agent.utils.tool_service_endpoints import get_retrieval_base_url

logger = logging.getLogger("BlenderMCPServer")
_ALLOWED_HSSD_OBJECT_TYPES = {
    "FURNITURE",
    "MANIPULAND",
    "WALL_MOUNTED",
    "CEILING_MOUNTED",
}


def _retrieval_base_url() -> str:
    return get_retrieval_base_url()


def _format_request_error(exc: Exception) -> str:
    if isinstance(exc, requests.exceptions.ConnectionError):
        return f"Cannot connect to retrieval service at {_retrieval_base_url()}."
    if isinstance(exc, requests.exceptions.Timeout):
        return "Request to retrieval service timed out."
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        return (
            "HTTP error from retrieval service: "
            f"{exc.response.status_code} - {exc.response.text}"
        )
    return str(exc)


def _normalize_dimensions(
    desired_dimensions_m: Optional[list[float] | tuple[float, float, float] | str],
) -> Optional[list[float]]:
    if desired_dimensions_m is None:
        return None
    if isinstance(desired_dimensions_m, str):
        raw_parts = desired_dimensions_m.replace("x", ",").split(",")
        values = [part.strip() for part in raw_parts if part.strip()]
    else:
        values = list(desired_dimensions_m)

    if len(values) != 3:
        raise ValueError("desired_dimensions_m must contain exactly 3 values")

    normalized: list[float] = []
    for value in values:
        numeric = float(value)
        if numeric <= 0:
            raise ValueError("desired_dimensions_m values must be positive")
        normalized.append(numeric)
    return normalized


def search_hssd_assets(
    ctx: Context,
    query: str,
    object_type: str = "FURNITURE",
    top_k: int = 3,
    desired_dimensions_m: Optional[list[float] | tuple[float, float, float] | str] = None,
) -> str:
    """Search SceneSmith HSSD assets by text and optional target size."""
    del ctx
    normalized_object_type = object_type.strip().upper()
    if normalized_object_type not in _ALLOWED_HSSD_OBJECT_TYPES:
        allowed = ", ".join(sorted(_ALLOWED_HSSD_OBJECT_TYPES))
        return f"Error: object_type must be one of {allowed}, got '{object_type}'"
    if top_k < 1 or top_k > 20:
        return f"Error: top_k must be between 1 and 20, got {top_k}"

    try:
        payload = {
            "query": query,
            "object_type": normalized_object_type,
            "top_k": top_k,
        }
        normalized_dimensions = _normalize_dimensions(desired_dimensions_m)
        if normalized_dimensions is not None:
            payload["desired_dimensions_m"] = normalized_dimensions

        response = requests.post(
            f"{_retrieval_base_url()}/hssd/v1/search",
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        result = response.json()
        candidates = result.get("candidates", [])
        if not candidates:
            return f"No HSSD results found for query: '{query}'"

        output = (
            f"Found {len(candidates)} HSSD assets for query: '{query}' "
            f"(object_type={normalized_object_type})\n"
        )
        for index, candidate in enumerate(candidates, 1):
            output += f"{index}. HSSD ID: {candidate.get('hssd_id', 'N/A')}\n"
            output += f"   Name: {candidate.get('name', 'N/A')}\n"
            output += f"   Category: {candidate.get('category', 'N/A')}\n"
            output += f"   Similarity: {candidate.get('similarity_score', 0):.3f}\n"
            output += f"   BBox score: {candidate.get('bbox_score', 0):.3f}\n"
            size_m = candidate.get("size_m")
            if isinstance(size_m, list):
                output += f"   Size (m): {size_m}\n"
            output += f"   Download URL: {candidate.get('download_url', 'N/A')}\n\n"
        output += (
            "To import one result, use import_hssd_asset(download_url=..., object_name=...)."
        )
        return output
    except Exception as exc:
        logger.error("Error searching HSSD assets: %s", exc)
        return f"Error searching HSSD assets: {_format_request_error(exc)}"


def import_hssd_asset(
    ctx: Context,
    download_url: str,
    object_name: Optional[str] = None,
) -> str:
    """Import a SceneSmith HSSD GLB asset into Blender."""
    return import_glb_model(ctx, download_url, object_name or "HSSDRetrievedAsset")


def search_ambientcg_materials(
    ctx: Context,
    query: str,
    top_k: int = 3,
) -> str:
    """Search SceneSmith AmbientCG materials."""
    del ctx
    if top_k < 1 or top_k > 20:
        return f"Error: top_k must be between 1 and 20, got {top_k}"

    try:
        response = requests.post(
            f"{_retrieval_base_url()}/ambientcg/v1/search",
            json={"query": query, "top_k": top_k},
            timeout=60,
        )
        response.raise_for_status()
        result = response.json()
        candidates = result.get("candidates", [])
        if not candidates:
            return f"No AmbientCG materials found for query: '{query}'"

        output = f"Found {len(candidates)} AmbientCG materials for query: '{query}'\n"
        for index, candidate in enumerate(candidates, 1):
            output += f"{index}. Material ID: {candidate.get('material_id', 'N/A')}\n"
            output += f"   Category: {candidate.get('category', 'N/A')}\n"
            output += f"   Tags: {', '.join(candidate.get('tags', []))}\n"
            output += f"   Similarity: {candidate.get('similarity_score', 0):.3f}\n"
            output += (
                f"   Package URL: {candidate.get('package_download_url', 'N/A')}\n"
            )
            textures = candidate.get("textures", {})
            output += f"   Color URL: {textures.get('color_url', 'N/A')}\n"
            output += f"   Normal URL: {textures.get('normal_url', 'N/A')}\n"
            output += f"   Roughness URL: {textures.get('roughness_url', 'N/A')}\n\n"
        output += (
            "To apply one result in Blender, use apply_ambientcg_material("
            "object_name=..., color_url=..., normal_url=..., roughness_url=..., material_name=...)."
        )
        return output
    except Exception as exc:
        logger.error("Error searching AmbientCG materials: %s", exc)
        return f"Error searching AmbientCG materials: {_format_request_error(exc)}"


def _download_remote_file(url: str, destination_dir: Path, stem: str) -> Path:
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix or ".bin"
    destination = destination_dir / f"{stem}{suffix}"
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    destination.write_bytes(response.content)
    return destination


def _ambientcg_texture_cache_dir() -> Path:
    configured = os.getenv(
        "SCENESMITH_AMBIENTCG_TEXTURE_DIR",
        os.path.join(tempfile.gettempdir(), "scene_agent_ambientcg_materials"),
    )
    target = Path(configured).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    return target


def apply_ambientcg_material(
    ctx: Context,
    object_name: str,
    color_url: str,
    normal_url: Optional[str] = None,
    roughness_url: Optional[str] = None,
    material_name: Optional[str] = None,
) -> str:
    """Download AmbientCG textures and create a Blender PBR material on a mesh object."""
    del ctx
    if not object_name.strip():
        return "Error: object_name is required"
    if not color_url.strip():
        return "Error: color_url is required"

    cache_root = _ambientcg_texture_cache_dir() / object_name.replace(os.sep, "_")
    cache_root.mkdir(parents=True, exist_ok=True)

    try:
        color_path = _download_remote_file(color_url, cache_root, "color")
        normal_path = (
            _download_remote_file(normal_url, cache_root, "normal")
            if normal_url and normal_url.strip()
            else None
        )
        roughness_path = (
            _download_remote_file(roughness_url, cache_root, "roughness")
            if roughness_url and roughness_url.strip()
            else None
        )
    except Exception as exc:
        logger.error("Error downloading AmbientCG textures: %s", exc)
        return f"Error downloading AmbientCG textures: {_format_request_error(exc)}"

    resolved_material_name = material_name or f"{object_name}_AmbientCG"
    blender = runtime.get_blender_connection(logger)
    script = f"""
import bpy

object_name = {json.dumps(object_name)}
material_name = {json.dumps(resolved_material_name)}
color_path = {json.dumps(str(color_path))}
normal_path = {json.dumps(str(normal_path) if normal_path else "")}
roughness_path = {json.dumps(str(roughness_path) if roughness_path else "")}

obj = bpy.data.objects.get(object_name)
if obj is None:
    raise ValueError(f"Object '{{object_name}}' not found")
if not hasattr(obj.data, "materials"):
    raise ValueError(f"Object '{{object_name}}' does not support materials")

mat = bpy.data.materials.get(material_name)
if mat is None:
    mat = bpy.data.materials.new(name=material_name)
mat.use_nodes = True

nodes = mat.node_tree.nodes
links = mat.node_tree.links
nodes.clear()

output_node = nodes.new("ShaderNodeOutputMaterial")
output_node.location = (500, 0)
bsdf_node = nodes.new("ShaderNodeBsdfPrincipled")
bsdf_node.location = (200, 0)
links.new(bsdf_node.outputs["BSDF"], output_node.inputs["Surface"])

def add_texture_node(image_path: str, label: str, location_x: int, location_y: int):
    if not image_path:
        return None
    image = bpy.data.images.load(image_path, check_existing=True)
    tex_node = nodes.new("ShaderNodeTexImage")
    tex_node.label = label
    tex_node.image = image
    tex_node.location = (location_x, location_y)
    return tex_node

color_node = add_texture_node(color_path, "Color", -300, 120)
if color_node is not None:
    links.new(color_node.outputs["Color"], bsdf_node.inputs["Base Color"])

roughness_node = add_texture_node(roughness_path, "Roughness", -300, -20)
if roughness_node is not None:
    roughness_node.image.colorspace_settings.name = "Non-Color"
    links.new(roughness_node.outputs["Color"], bsdf_node.inputs["Roughness"])

normal_node = add_texture_node(normal_path, "Normal", -300, -180)
if normal_node is not None:
    normal_node.image.colorspace_settings.name = "Non-Color"
    normal_map_node = nodes.new("ShaderNodeNormalMap")
    normal_map_node.location = (-40, -180)
    links.new(normal_node.outputs["Color"], normal_map_node.inputs["Color"])
    links.new(normal_map_node.outputs["Normal"], bsdf_node.inputs["Normal"])

if obj.data.materials:
    obj.data.materials[0] = mat
else:
    obj.data.materials.append(mat)

result = {{
    "success": True,
    "object_name": obj.name,
    "material_name": mat.name,
}}
"""
    try:
        result = blender.send_command(
            "execute_code",
            {
                "code": script,
                "safe_mode": True,
                "rollback_on_guard_fail": True,
                "validate_scene": True,
            },
        )
    except Exception as exc:
        logger.error("Error applying AmbientCG material: %s", exc)
        return f"Error applying AmbientCG material: {exc}"

    if not isinstance(result, dict):
        return f"Applied AmbientCG material '{resolved_material_name}' to '{object_name}'."

    return (
        f"Applied AmbientCG material '{resolved_material_name}' to '{object_name}'. "
        f"Cached textures under '{cache_root}'."
    )
