from __future__ import annotations

import json
import logging
import shutil
import tempfile
import textwrap
import zipfile
from pathlib import Path
from typing import Any, Optional

import requests
from mcp.server.fastmcp import Context, Image

from mcp_server import runtime

logger = logging.getLogger("BlenderMCPServer")

SKETCHFAB_API_BASE_URL = "https://api.sketchfab.com/v3"
_SKETCHFAB_IMPORT_MARKER = "MCP_SKETCHFAB_IMPORT_RESULT::"
_SKETCHFAB_MIN_SEARCH_COUNT = 1
_SKETCHFAB_MAX_SEARCH_COUNT = 50


def _sketchfab_disabled_message() -> str:
    return (
        "Sketchfab tools are disabled. They require ENABLE_SKETCHFAB=true "
        "and SKETCHFAB_API_KEY configured."
    )


def _get_sketchfab_api_key_or_error() -> tuple[Optional[str], Optional[str]]:
    if not runtime.is_sketchfab_tool_enabled():
        return None, _sketchfab_disabled_message()
    api_key = runtime.get_sketchfab_api_key()
    if not api_key:
        return None, (
            "Sketchfab tools are enabled, but SKETCHFAB_API_KEY is not configured."
        )
    return api_key, None


def _sketchfab_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Token {api_key}"}


def _safe_extract_zip(zip_path: Path, target_dir: Path) -> None:
    resolved_target = target_dir.resolve()
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        for member in zip_ref.infolist():
            destination = (target_dir / member.filename).resolve()
            if resolved_target not in destination.parents and destination != resolved_target:
                raise RuntimeError("Zip contains an unsafe path traversal entry")
        zip_ref.extractall(target_dir)


def _find_importable_gltf_file(root_dir: Path) -> Optional[Path]:
    candidates: list[Path] = []
    for pattern in ("*.glb", "*.gltf"):
        candidates.extend(root_dir.rglob(pattern))
    if not candidates:
        return None
    candidates.sort(key=lambda path: (path.suffix != ".glb", len(path.parts), str(path)))
    return candidates[0]


def _extract_marked_json(raw_output: str, marker: str) -> Optional[dict[str, Any]]:
    for line in reversed(raw_output.splitlines()):
        if line.startswith(marker):
            payload = line[len(marker) :].strip()
            if not payload:
                return None
            return json.loads(payload)
    return None


def _build_blender_import_code(import_path: str, target_size: float) -> str:
    marker_literal = json.dumps(_SKETCHFAB_IMPORT_MARKER)
    path_literal = json.dumps(import_path)
    return textwrap.dedent(
        f"""
        import bpy
        import json
        import mathutils

        marker = {marker_literal}
        import_path = {path_literal}
        target_size = {target_size}

        objects_before = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=import_path)
        objects_after = set(bpy.data.objects)

        imported_objects = list(objects_after - objects_before)
        if not imported_objects:
            raise RuntimeError("No objects were imported from Sketchfab file")

        imported_names = [obj.name for obj in imported_objects]
        root_objects = [obj for obj in imported_objects if obj.parent is None]

        def _collect_mesh_children(obj):
            meshes = []
            if obj.type == "MESH":
                meshes.append(obj)
            for child in obj.children:
                meshes.extend(_collect_mesh_children(child))
            return meshes

        all_meshes = []
        for root in root_objects:
            all_meshes.extend(_collect_mesh_children(root))

        world_bounding_box = None
        dimensions = None
        normalized = False
        scale_applied = 1.0

        if all_meshes:
            all_min = mathutils.Vector((float("inf"), float("inf"), float("inf")))
            all_max = mathutils.Vector((float("-inf"), float("-inf"), float("-inf")))

            for mesh_obj in all_meshes:
                for corner in mesh_obj.bound_box:
                    world_corner = mesh_obj.matrix_world @ mathutils.Vector(corner)
                    all_min.x = min(all_min.x, world_corner.x)
                    all_min.y = min(all_min.y, world_corner.y)
                    all_min.z = min(all_min.z, world_corner.z)
                    all_max.x = max(all_max.x, world_corner.x)
                    all_max.y = max(all_max.y, world_corner.y)
                    all_max.z = max(all_max.z, world_corner.z)

            dimensions = [
                all_max.x - all_min.x,
                all_max.y - all_min.y,
                all_max.z - all_min.z,
            ]
            max_dimension = max(dimensions)

            if target_size > 0 and max_dimension > 0:
                scale_applied = target_size / max_dimension
                for root in root_objects:
                    root.scale = (
                        root.scale.x * scale_applied,
                        root.scale.y * scale_applied,
                        root.scale.z * scale_applied,
                    )
                bpy.context.view_layer.update()
                normalized = True

                all_min = mathutils.Vector((float("inf"), float("inf"), float("inf")))
                all_max = mathutils.Vector((float("-inf"), float("-inf"), float("-inf")))

                for mesh_obj in all_meshes:
                    for corner in mesh_obj.bound_box:
                        world_corner = mesh_obj.matrix_world @ mathutils.Vector(corner)
                        all_min.x = min(all_min.x, world_corner.x)
                        all_min.y = min(all_min.y, world_corner.y)
                        all_min.z = min(all_min.z, world_corner.z)
                        all_max.x = max(all_max.x, world_corner.x)
                        all_max.y = max(all_max.y, world_corner.y)
                        all_max.z = max(all_max.z, world_corner.z)

                dimensions = [
                    all_max.x - all_min.x,
                    all_max.y - all_min.y,
                    all_max.z - all_min.z,
                ]

            world_bounding_box = [
                [all_min.x, all_min.y, all_min.z],
                [all_max.x, all_max.y, all_max.z],
            ]

        result = {
            "success": True,
            "imported_objects": imported_names,
            "num_objects": len(imported_names),
            "world_bounding_box": world_bounding_box,
            "dimensions": [round(d, 4) for d in dimensions] if dimensions else None,
            "normalized": normalized,
            "scale_applied": round(scale_applied, 6),
            "target_size": target_size,
            "import_path": import_path,
        }
        print(marker + json.dumps(result, ensure_ascii=False))
        """
    ).strip()


def _import_sketchfab_asset_into_blender(import_path: Path, target_size: float) -> dict[str, Any]:
    blender = runtime.get_blender_connection(logger)
    code = _build_blender_import_code(str(import_path), target_size)
    result = blender.send_command("execute_code", {"code": code})
    raw_output = result.get("result", "") if isinstance(result, dict) else ""

    parsed = _extract_marked_json(raw_output, _SKETCHFAB_IMPORT_MARKER)
    if parsed is None:
        raise RuntimeError(
            "Unexpected Blender response when importing Sketchfab asset. "
            f"Raw output: {raw_output.strip() or '<empty>'}"
        )
    return parsed


def search_sketchfab_models(
    ctx: Context,
    query: str,
    categories: Optional[str] = None,
    count: int = 20,
    downloadable: bool = True,
) -> str:
    """Search Sketchfab models with optional category and downloadable filters."""
    api_key, error_message = _get_sketchfab_api_key_or_error()
    if error_message:
        return error_message

    if count < _SKETCHFAB_MIN_SEARCH_COUNT or count > _SKETCHFAB_MAX_SEARCH_COUNT:
        return (
            f"Error: count must be between {_SKETCHFAB_MIN_SEARCH_COUNT} and "
            f"{_SKETCHFAB_MAX_SEARCH_COUNT}."
        )

    params: dict[str, Any] = {
        "type": "models",
        "q": query,
        "count": count,
        "downloadable": downloadable,
        "archives_flavours": False,
    }
    if categories:
        params["categories"] = categories

    try:
        response = requests.get(
            f"{SKETCHFAB_API_BASE_URL}/search",
            headers=_sketchfab_headers(api_key),
            params=params,
            timeout=30,
        )
    except requests.exceptions.Timeout:
        return "Error: Sketchfab search timed out."
    except requests.exceptions.ConnectionError:
        return "Error: Cannot connect to Sketchfab search API."
    except Exception as exc:
        logger.error("Error searching Sketchfab models: %s", exc)
        return f"Error searching Sketchfab models: {str(exc)}"

    if response.status_code == 401:
        return "Error: Sketchfab API key is invalid (401 Unauthorized)."
    if response.status_code != 200:
        return (
            "Error: Sketchfab search API failed with status "
            f"{response.status_code}: {response.text[:300]}"
        )

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        return f"Error: Invalid JSON from Sketchfab search API: {str(exc)}"

    models = payload.get("results", [])
    if not models:
        return f"No models found matching '{query}'."

    output = [f"Found {len(models)} models matching '{query}':", ""]
    for model in models:
        if not isinstance(model, dict):
            continue
        model_name = model.get("name", "Unnamed model")
        uid = model.get("uid", "Unknown ID")
        user_data = model.get("user") if isinstance(model.get("user"), dict) else {}
        username = user_data.get("username", "Unknown author")
        license_data = (
            model.get("license") if isinstance(model.get("license"), dict) else {}
        )
        license_label = license_data.get("label", "Unknown")
        face_count = model.get("faceCount", "Unknown")
        is_downloadable = "Yes" if model.get("isDownloadable") else "No"

        output.append(f"- {model_name} (UID: {uid})")
        output.append(f"  Author: {username}")
        output.append(f"  License: {license_label}")
        output.append(f"  Face count: {face_count}")
        output.append(f"  Downloadable: {is_downloadable}")
        output.append("")

    output.append(
        "Use get_sketchfab_model_preview(uid) before download_sketchfab_model(uid, target_size)."
    )
    return "\n".join(output)


def get_sketchfab_model_preview(
    ctx: Context,
    uid: str,
) -> Image:
    """Fetch a preview thumbnail for a Sketchfab model and return it as an MCP image."""
    api_key, error_message = _get_sketchfab_api_key_or_error()
    if error_message:
        raise Exception(error_message)

    try:
        response = requests.get(
            f"{SKETCHFAB_API_BASE_URL}/models/{uid}",
            headers=_sketchfab_headers(api_key),
            timeout=30,
        )
    except requests.exceptions.Timeout as exc:
        raise Exception("Sketchfab preview request timed out") from exc
    except requests.exceptions.ConnectionError as exc:
        raise Exception("Cannot connect to Sketchfab preview API") from exc

    if response.status_code == 401:
        raise Exception("Sketchfab API key is invalid (401 Unauthorized)")
    if response.status_code == 404:
        raise Exception(f"Sketchfab model not found: {uid}")
    if response.status_code != 200:
        raise Exception(
            "Sketchfab preview API failed: "
            f"HTTP {response.status_code} - {response.text[:300]}"
        )

    data = response.json()
    thumbnails = data.get("thumbnails", {}).get("images", [])
    if not thumbnails:
        raise Exception("No thumbnail available for this Sketchfab model")

    thumbnail_candidates = [
        image
        for image in thumbnails
        if isinstance(image, dict) and image.get("url")
    ]
    if not thumbnail_candidates:
        raise Exception("No valid thumbnail URL found in Sketchfab response")

    selected = min(
        thumbnail_candidates,
        key=lambda image: abs((image.get("width") or 0) - 640),
    )
    thumbnail_url = selected["url"]

    image_response = requests.get(thumbnail_url, timeout=30)
    if image_response.status_code != 200:
        raise Exception(
            f"Failed to download thumbnail image: HTTP {image_response.status_code}"
        )

    content_type = image_response.headers.get("Content-Type", "").lower()
    image_format = "png" if "png" in content_type or thumbnail_url.endswith(".png") else "jpeg"
    return Image(data=image_response.content, format=image_format)


def download_sketchfab_model(
    ctx: Context,
    uid: str,
    target_size: float,
) -> str:
    """Download a Sketchfab model on backend and import it through generic Blender execute_code."""
    api_key, error_message = _get_sketchfab_api_key_or_error()
    if error_message:
        return error_message

    if target_size <= 0:
        return "Error: target_size must be greater than zero."

    temp_root = Path(tempfile.mkdtemp(prefix=f"mcp_sketchfab_{uid}_"))
    extract_dir = temp_root / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)
    archive_path = temp_root / f"{uid}.zip"

    try:
        metadata_response = requests.get(
            f"{SKETCHFAB_API_BASE_URL}/models/{uid}/download",
            headers=_sketchfab_headers(api_key),
            timeout=30,
        )
        if metadata_response.status_code == 401:
            return "Error: Sketchfab API key is invalid (401 Unauthorized)."
        if metadata_response.status_code == 404:
            return f"Error: Sketchfab model not found: {uid}."
        if metadata_response.status_code != 200:
            return (
                "Error: Sketchfab download metadata request failed with status "
                f"{metadata_response.status_code}: {metadata_response.text[:300]}"
            )

        metadata = metadata_response.json()
        download_url = None
        download_kind = "gltf"

        glb_info = metadata.get("glb")
        if isinstance(glb_info, dict) and glb_info.get("url"):
            download_url = glb_info["url"]
            download_kind = "glb"

        if not download_url:
            gltf_info = metadata.get("gltf")
            if isinstance(gltf_info, dict) and gltf_info.get("url"):
                download_url = gltf_info["url"]
                download_kind = "gltf"

        if not download_url:
            return (
                "Error: No downloadable glTF/GLB URL was returned by Sketchfab. "
                "Make sure the model is downloadable and your account has access."
            )

        logger.info("Downloading Sketchfab model uid=%s kind=%s", uid, download_kind)
        model_response = requests.get(download_url, timeout=120, stream=True)
        if model_response.status_code != 200:
            return (
                f"Error: Failed to download model archive (HTTP {model_response.status_code})."
            )

        content_type = model_response.headers.get("Content-Type", "").lower()
        looks_like_zip = (
            download_kind == "gltf"
            or "zip" in content_type
            or download_url.lower().endswith(".zip")
        )

        if looks_like_zip:
            with archive_path.open("wb") as handle:
                for chunk in model_response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
            _safe_extract_zip(archive_path, extract_dir)
        else:
            glb_path = extract_dir / f"{uid}.glb"
            with glb_path.open("wb") as handle:
                for chunk in model_response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)

        import_path = _find_importable_gltf_file(extract_dir)
        if import_path is None:
            return (
                "Error: Download succeeded but no .glb or .gltf file was found "
                "in the extracted Sketchfab package."
            )

        import_result = _import_sketchfab_asset_into_blender(import_path, target_size)
        if not import_result.get("success"):
            return f"Error: Blender import failed: {json.dumps(import_result, indent=2)}"

        imported_objects = import_result.get("imported_objects") or []
        object_names = ", ".join(imported_objects) if imported_objects else "none"

        output = "Successfully imported Sketchfab model.\n"
        output += f"Created objects: {object_names}\n"
        dimensions = import_result.get("dimensions")
        if dimensions:
            output += (
                "Dimensions (X, Y, Z): "
                f"{dimensions[0]:.3f} x {dimensions[1]:.3f} x {dimensions[2]:.3f} meters\n"
            )
        world_bbox = import_result.get("world_bounding_box")
        if world_bbox:
            output += f"Bounding box: min={world_bbox[0]}, max={world_bbox[1]}\n"
        if import_result.get("normalized"):
            scale_applied = import_result.get("scale_applied", 1.0)
            output += (
                "Size normalized: "
                f"scale factor {scale_applied:.6f} applied (target size: {target_size}m)\n"
            )
        output += f"Imported source file: {import_path.name}"
        return output

    except requests.exceptions.Timeout:
        return "Error: Sketchfab model download timed out."
    except requests.exceptions.ConnectionError:
        return "Error: Cannot connect to Sketchfab download endpoint."
    except Exception as exc:
        logger.error("Error downloading Sketchfab model: %s", exc)
        return f"Error downloading Sketchfab model: {str(exc)}"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
