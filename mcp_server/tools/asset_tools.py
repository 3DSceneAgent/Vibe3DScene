from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import tempfile
import textwrap
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from mcp.server.fastmcp import Context, Image

from mcp_server import runtime
from mcp_server.tools.core_blender_tools import import_glb_model

logger = logging.getLogger("BlenderMCPServer")

SKETCHFAB_API_BASE_URL = "https://api.sketchfab.com/v3"
_SKETCHFAB_IMPORT_MARKER = "MCP_SKETCHFAB_IMPORT_RESULT::"
_SKETCHFAB_MIN_SEARCH_COUNT = 1
_SKETCHFAB_MAX_SEARCH_COUNT = 50
RODIN_MAIN_SITE_API_BASE_URL = "https://hyperhuman.deemos.com/api/v2"
RODIN_FAL_API_BASE_URL = "https://queue.fal.run/fal-ai/hyper3d"


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


def _rodin_disabled_message() -> str:
    return (
        "Rodin tools are disabled. They require BLENDER_MODE=local-client, "
        "ENABLE_RODIN=true, and RODIN_API_KEY configured."
    )


def _get_rodin_mode_and_api_key_or_error() -> tuple[Optional[str], Optional[str], Optional[str]]:
    if not runtime.is_rodin_tool_enabled():
        return None, None, _rodin_disabled_message()

    api_key = runtime.get_rodin_api_key()
    if not api_key:
        return runtime.get_rodin_mode(), None, (
            "Rodin tools are enabled, but RODIN_API_KEY is not configured."
        )

    return runtime.get_rodin_mode(), api_key, None


def _rodin_headers(api_key: str, mode: str, use_json: bool = False) -> dict[str, str]:
    if mode == "FAL_AI":
        headers = {"Authorization": f"Key {api_key}"}
        if use_json:
            headers["Content-Type"] = "application/json"
        return headers
    return {"Authorization": f"Bearer {api_key}"}


def _format_rodin_submit_response(result: dict[str, Any]) -> str:
    if result.get("submit_time"):
        output: dict[str, Any] = {"task_uuid": result.get("uuid")}
        jobs = result.get("jobs") or {}
        if isinstance(jobs, dict) and jobs.get("subscription_key"):
            output["subscription_key"] = jobs["subscription_key"]
        if result.get("request_id"):
            output["request_id"] = result["request_id"]
        return json.dumps(output, indent=2)
    return json.dumps(result, indent=2)


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


def get_infinigen_available_assets(
    ctx: Context,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Fetch available Infinigen asset types from the API."""
    infinigen_host = os.getenv("INFINIGEN_HOST", "localhost")
    infinigen_port = os.getenv("INFINIGEN_PORT", "8003")
    base_url = f"http://{infinigen_host}:{infinigen_port}/api/v1"
    endpoint = f"{base_url}/infinigen/assets/available"
    try:
        logger.info("Fetching available Infinigen assets from %s", endpoint)
        response = requests.get(endpoint, timeout=timeout)
        if response.status_code != 200:
            return {
                "error": (
                    "Infinigen API request failed with status "
                    f"{response.status_code}: {response.text}"
                )
            }
        result = response.json()
        return {
            "success": True,
            "total_count": result.get("total_count", 0),
            "asset_types": result.get("asset_types", {}),
        }
    except requests.exceptions.Timeout:
        return {"error": f"Infinigen API request timed out after {timeout} seconds"}
    except requests.exceptions.ConnectionError:
        return {
            "error": (
                f"Could not connect to Infinigen API at {base_url}. "
                "Make sure Infinigen service is running."
            )
        }
    except Exception as exc:
        logger.exception("Failed to fetch available Infinigen assets")
        return {"error": f"Failed to fetch available assets: {str(exc)}"}


def generate_infinigen_assets(
    ctx: Context,
    asset_type: str,
    seed: Optional[int] = 42,
    timeout: int = 300,
    output_dir: Optional[str] = None,
    cleanup: bool = False,
) -> Dict[str, Any]:
    """Generate an Infinigen asset and download the .blend output."""
    if not asset_type:
        return {"error": "asset_type is required"}

    infinigen_host = os.getenv("INFINIGEN_HOST", "localhost")
    infinigen_port = os.getenv("INFINIGEN_PORT", "8003")
    base_url = f"http://{infinigen_host}:{infinigen_port}/api/v1"
    endpoint = f"{base_url}/infinigen/assets/generate"

    temp_dir_created = False
    target_dir = Path(output_dir) if output_dir else Path(
        os.path.join("/tmp", f"infinigen_{int(time.time() * 1000)}")
    )
    if not output_dir:
        temp_dir_created = True

    target_dir.mkdir(parents=True, exist_ok=True)
    blend_file_path = target_dir / f"{asset_type}.blend"

    data = {"asset_type": asset_type, "num_assets": 1, "output_folder": str(target_dir)}
    if seed is not None:
        data["seed"] = seed

    try:
        logger.info("Generating Infinigen asset: %s", asset_type)
        response = requests.post(endpoint, json=data, timeout=timeout, stream=True)
        if response.status_code != 200:
            return {
                "error": (
                    "Infinigen API request failed with status "
                    f"{response.status_code}: {response.text}"
                )
            }
        with open(blend_file_path, "wb") as handle:
            handle.write(response.content)
        if not blend_file_path.exists():
            return {"error": f"Blend file not found at: {blend_file_path}"}

        result = {
            "success": True,
            "asset_type": asset_type,
            "blend_file_path": str(blend_file_path),
            "output_dir": str(target_dir),
            "downloaded_bytes": blend_file_path.stat().st_size,
            "cleanup_required": temp_dir_created and not cleanup,
        }
        if cleanup and temp_dir_created:
            try:
                shutil.rmtree(target_dir)
                result["cleanup_performed"] = True
            except Exception as exc:
                logger.warning("Failed to clean up temp dir %s: %s", target_dir, exc)
                result["cleanup_performed"] = False
        return result
    except requests.exceptions.Timeout:
        return {"error": f"Infinigen request timed out after {timeout} seconds"}
    except requests.exceptions.ConnectionError:
        return {
            "error": (
                f"Could not connect to Infinigen API at {base_url}. "
                "Make sure Infinigen service is running."
            )
        }
    except Exception as exc:
        logger.exception("Failed to generate Infinigen assets")
        return {"error": f"Failed to generate Infinigen assets: {str(exc)}"}


def generate_trellis2_model(
    ctx: Context,
    text_prompt: str,
    object_name: str,
    pipeline_type: str = "512",
    texture_size: int = 1024,
    timeout: int = 300,
) -> str:
    """Generate a 3D model using TRELLIS2 and auto-import in Blender."""
    try:
        trellis_host = os.getenv("TRELLIS2_HOST", "localhost")
        trellis_port = os.getenv("TRELLIS2_PORT", "8001")
        base_url = f"http://{trellis_host}:{trellis_port}"
        endpoint = f"{base_url}/api/v1/text-to-3d"
        payload = {
            "prompt": text_prompt,
            "generate_model": True,
            "generate_video": False,
            "pipeline_type": pipeline_type.replace('"', "").replace("'", ""),
            "texture_size": texture_size,
            "timeout": timeout,
        }
        response = requests.post(endpoint, data=payload, timeout=timeout)
        if response.status_code != 200:
            return (
                "Error: TRELLIS2 API request failed with status "
                f"{response.status_code}: {response.text}"
            )
        result = response.json()
        model_url = result.get("model_url")
        if not model_url:
            return "Error: No model_url in TRELLIS2 response"
        if not model_url.startswith("http"):
            model_url = f"{base_url}{model_url}"
        import_result = import_glb_model(ctx, model_url, object_name)
        return f"TRELLIS2 generation completed.\n{import_result}"
    except Exception as exc:
        logger.error("Error generating TRELLIS2 model: %s", str(exc))
        return f"Error generating TRELLIS2 model: {str(exc)}"


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


def generate_hyper3d_model_via_text(
    ctx: Context,
    text_prompt: str,
    bbox_condition: Optional[list[float]] = None,
) -> str:
    """Create a Hyper3D Rodin generation task using text prompt."""
    mode, api_key, error_message = _get_rodin_mode_and_api_key_or_error()
    if error_message:
        return error_message
    assert mode is not None and api_key is not None

    processed_bbox = runtime.process_bbox(bbox_condition)

    try:
        if mode == "FAL_AI":
            payload: dict[str, Any] = {
                "tier": "Sketch",
                "prompt": text_prompt,
            }
            if processed_bbox:
                payload["bbox_condition"] = processed_bbox

            response = requests.post(
                f"{RODIN_FAL_API_BASE_URL}/rodin",
                headers=_rodin_headers(api_key, mode, use_json=True),
                json=payload,
                timeout=120,
            )
        else:
            files: list[tuple[str, tuple[Optional[str], str]]] = [
                ("tier", (None, "Sketch")),
                ("mesh_mode", (None, "Raw")),
                ("prompt", (None, text_prompt)),
            ]
            if processed_bbox:
                files.append(("bbox_condition", (None, json.dumps(processed_bbox))))

            response = requests.post(
                f"{RODIN_MAIN_SITE_API_BASE_URL}/rodin",
                headers=_rodin_headers(api_key, mode),
                files=files,
                timeout=120,
            )

        if response.status_code >= 400:
            return (
                "Error: Rodin task creation failed with status "
                f"{response.status_code}: {response.text[:400]}"
            )

        result = response.json()
        return _format_rodin_submit_response(result)
    except requests.exceptions.Timeout:
        return "Error: Rodin task creation timed out."
    except requests.exceptions.ConnectionError:
        return "Error: Cannot connect to Rodin API endpoint."
    except json.JSONDecodeError as exc:
        return f"Error: Invalid JSON from Rodin API: {str(exc)}"
    except Exception as exc:
        logger.error("Error creating Hyper3D task via text: %s", exc)
        return f"Error creating Hyper3D task via text: {str(exc)}"


def generate_hyper3d_model_via_images(
    ctx: Context,
    input_image_paths: Optional[list[str]] = None,
    input_image_urls: Optional[list[str]] = None,
    bbox_condition: Optional[list[float]] = None,
) -> str:
    """Create a Hyper3D Rodin generation task using image inputs."""
    mode, api_key, error_message = _get_rodin_mode_and_api_key_or_error()
    if error_message:
        return error_message
    assert mode is not None and api_key is not None

    if input_image_paths and input_image_urls:
        return "Error: Provide only one of input_image_paths or input_image_urls."
    if not input_image_paths and not input_image_urls:
        return "Error: At least one image input is required."

    processed_bbox = runtime.process_bbox(bbox_condition)

    try:
        if mode == "MAIN_SITE":
            if not input_image_paths:
                return (
                    "Error: RODIN_MODE=MAIN_SITE requires input_image_paths, "
                    "not input_image_urls."
                )

            if not all(os.path.exists(path) for path in input_image_paths):
                return "Error: One or more input_image_paths do not exist."

            files: list[tuple[str, tuple[Optional[str], str]]] = [
                ("tier", (None, "Sketch")),
                ("mesh_mode", (None, "Raw")),
            ]
            if processed_bbox:
                files.append(("bbox_condition", (None, json.dumps(processed_bbox))))

            for i, path in enumerate(input_image_paths):
                with open(path, "rb") as handle:
                    suffix = Path(path).suffix or ".png"
                    files.append(
                        (
                            "images",
                            (
                                f"{i:04d}{suffix}",
                                base64.b64encode(handle.read()).decode("ascii"),
                            ),
                        )
                    )

            response = requests.post(
                f"{RODIN_MAIN_SITE_API_BASE_URL}/rodin",
                headers=_rodin_headers(api_key, mode),
                files=files,
                timeout=120,
            )
        else:
            if not input_image_urls:
                return (
                    "Error: RODIN_MODE=FAL_AI requires input_image_urls, "
                    "not input_image_paths."
                )

            from urllib.parse import urlparse

            parsed_urls = [urlparse(url) for url in input_image_urls]
            if not all(
                parsed.scheme in {"http", "https"} and parsed.netloc for parsed in parsed_urls
            ):
                return "Error: One or more input_image_urls are invalid."

            payload: dict[str, Any] = {
                "tier": "Sketch",
                "input_image_urls": list(input_image_urls),
            }
            if processed_bbox:
                payload["bbox_condition"] = processed_bbox

            response = requests.post(
                f"{RODIN_FAL_API_BASE_URL}/rodin",
                headers=_rodin_headers(api_key, mode, use_json=True),
                json=payload,
                timeout=120,
            )

        if response.status_code >= 400:
            return (
                "Error: Rodin image task creation failed with status "
                f"{response.status_code}: {response.text[:400]}"
            )

        result = response.json()
        return _format_rodin_submit_response(result)
    except requests.exceptions.Timeout:
        return "Error: Rodin image task creation timed out."
    except requests.exceptions.ConnectionError:
        return "Error: Cannot connect to Rodin API endpoint."
    except json.JSONDecodeError as exc:
        return f"Error: Invalid JSON from Rodin API: {str(exc)}"
    except Exception as exc:
        logger.error("Error creating Hyper3D task via images: %s", exc)
        return f"Error creating Hyper3D task via images: {str(exc)}"


def poll_rodin_job_status(
    ctx: Context,
    subscription_key: Optional[str] = None,
    request_id: Optional[str] = None,
) -> str:
    """Poll Hyper3D Rodin task status."""
    mode, api_key, error_message = _get_rodin_mode_and_api_key_or_error()
    if error_message:
        return error_message
    assert mode is not None and api_key is not None

    try:
        if mode == "MAIN_SITE":
            if not subscription_key:
                return "Error: RODIN_MODE=MAIN_SITE requires subscription_key."

            response = requests.post(
                f"{RODIN_MAIN_SITE_API_BASE_URL}/status",
                headers=_rodin_headers(api_key, mode),
                json={"subscription_key": subscription_key},
                timeout=60,
            )
            if response.status_code >= 400:
                return (
                    "Error: Rodin status polling failed with status "
                    f"{response.status_code}: {response.text[:400]}"
                )
            result = response.json()
            jobs = result.get("jobs") if isinstance(result, dict) else None
            if isinstance(jobs, list):
                status_list = [job.get("status") for job in jobs if isinstance(job, dict)]
                return json.dumps({"status_list": status_list}, indent=2)
        else:
            if not request_id:
                return "Error: RODIN_MODE=FAL_AI requires request_id."

            response = requests.get(
                f"{RODIN_FAL_API_BASE_URL}/requests/{request_id}/status",
                headers=_rodin_headers(api_key, mode),
                timeout=60,
            )
            if response.status_code >= 400:
                return (
                    "Error: Rodin status polling failed with status "
                    f"{response.status_code}: {response.text[:400]}"
                )
            result = response.json()

        return json.dumps(result, indent=2)
    except requests.exceptions.Timeout:
        return "Error: Rodin status polling timed out."
    except requests.exceptions.ConnectionError:
        return "Error: Cannot connect to Rodin status endpoint."
    except json.JSONDecodeError as exc:
        return f"Error: Invalid JSON from Rodin status API: {str(exc)}"
    except Exception as exc:
        logger.error("Error polling Hyper3D task: %s", exc)
        return f"Error polling Hyper3D task: {str(exc)}"


def import_generated_asset(
    ctx: Context,
    name: str,
    task_uuid: Optional[str] = None,
    request_id: Optional[str] = None,
) -> str:
    """Import a Hyper3D generated asset into Blender."""
    mode, api_key, error_message = _get_rodin_mode_and_api_key_or_error()
    if error_message:
        return error_message
    assert mode is not None and api_key is not None

    try:
        glb_url: Optional[str] = None

        if mode == "MAIN_SITE":
            if not task_uuid:
                return "Error: RODIN_MODE=MAIN_SITE requires task_uuid."

            response = requests.post(
                f"{RODIN_MAIN_SITE_API_BASE_URL}/download",
                headers=_rodin_headers(api_key, mode),
                json={"task_uuid": task_uuid},
                timeout=120,
            )
            if response.status_code >= 400:
                return (
                    "Error: Rodin download metadata request failed with status "
                    f"{response.status_code}: {response.text[:400]}"
                )
            payload = response.json()
            files = payload.get("list") if isinstance(payload, dict) else None
            if isinstance(files, list):
                for item in files:
                    if not isinstance(item, dict):
                        continue
                    name_value = item.get("name")
                    url_value = item.get("url")
                    if (
                        isinstance(name_value, str)
                        and name_value.lower().endswith(".glb")
                        and isinstance(url_value, str)
                        and url_value
                    ):
                        glb_url = url_value
                        break
        else:
            if not request_id:
                return "Error: RODIN_MODE=FAL_AI requires request_id."

            response = requests.get(
                f"{RODIN_FAL_API_BASE_URL}/requests/{request_id}",
                headers=_rodin_headers(api_key, mode),
                timeout=120,
            )
            if response.status_code >= 400:
                return (
                    "Error: Rodin request lookup failed with status "
                    f"{response.status_code}: {response.text[:400]}"
                )
            payload = response.json()
            model_mesh = payload.get("model_mesh") if isinstance(payload, dict) else None
            if isinstance(model_mesh, dict):
                url_value = model_mesh.get("url")
                if isinstance(url_value, str) and url_value:
                    glb_url = url_value

        if not glb_url:
            return (
                "Error: Failed to resolve generated GLB download URL. "
                "Please confirm the generation task is fully completed."
            )

        blender = runtime.get_blender_connection(logger)
        result = blender.send_command(
            "import_glb_model",
            {
                "model_url": glb_url,
                "object_name": name,
            },
        )
        if isinstance(result, dict):
            output = dict(result)
            output["source_url"] = glb_url
            return json.dumps(output, indent=2)
        return json.dumps({"result": result, "source_url": glb_url}, indent=2)
    except requests.exceptions.Timeout:
        return "Error: Rodin import request timed out."
    except requests.exceptions.ConnectionError:
        return "Error: Cannot connect to Rodin import endpoint."
    except json.JSONDecodeError as exc:
        return f"Error: Invalid JSON from Rodin import API: {str(exc)}"
    except Exception as exc:
        logger.error("Error importing Hyper3D asset: %s", exc)
        return f"Error importing Hyper3D asset: {str(exc)}"


def generate_hunyuan3d_model(
    ctx: Context,
    text_prompt: Optional[str] = None,
    input_image_url: Optional[str] = None,
    timeout_seconds: int = 300,
    poll_interval_seconds: float = 5.0,
) -> str:
    """Generate Hunyuan3D asset via Tencent official API from MCP server side."""
    if not runtime.is_hunyuan_tool_enabled():
        return (
            "Hunyuan tool is disabled. "
            "It requires BLENDER_MODE=headless and ENABLE_HUNYUAN=true."
        )
    if bool(text_prompt) == bool(input_image_url):
        return "Error: Provide exactly one of text_prompt or input_image_url."

    secret_id = os.getenv("HUNYUAN3D_SECRET_ID", "").strip()
    secret_key = os.getenv("HUNYUAN3D_SECRET_KEY", "").strip()
    region = os.getenv("HUNYUAN3D_REGION", "ap-guangzhou").strip() or "ap-guangzhou"
    if not secret_id or not secret_key:
        return "Error: HUNYUAN3D_SECRET_ID or HUNYUAN3D_SECRET_KEY is not configured."

    if timeout_seconds == 300:
        timeout_seconds = int(os.getenv("HUNYUAN3D_TIMEOUT_SECONDS", "300"))
    if poll_interval_seconds == 5.0:
        poll_interval_seconds = float(os.getenv("HUNYUAN3D_POLL_INTERVAL_SECONDS", "5"))
    if timeout_seconds <= 0:
        return "Error: timeout_seconds must be > 0."
    if poll_interval_seconds <= 0:
        return "Error: poll_interval_seconds must be > 0."

    submit_payload: dict[str, Any] = {"Num": 1}
    if text_prompt:
        if len(text_prompt) > 200:
            return "Error: text_prompt exceeds 200 characters."
        submit_payload["Prompt"] = text_prompt

    if input_image_url:
        try:
            key, encoded = runtime.encode_local_or_url_image(input_image_url)
            submit_payload[key] = encoded
        except Exception as exc:
            return f"Error: Failed to encode local image: {str(exc)}"

    try:
        submit_resp = runtime.call_tencent_cloud_api(
            action="SubmitHunyuanTo3DJob",
            payload=submit_payload,
            secret_id=secret_id,
            secret_key=secret_key,
            region=region,
            timeout=min(60, timeout_seconds),
        )
        response_block = submit_resp.get("Response", {})
        if response_block.get("Error"):
            return json.dumps(
                {"error": "SubmitHunyuanTo3DJob failed", "detail": response_block.get("Error")},
                indent=2,
            )

        job_id = response_block.get("JobId")
        if not job_id:
            return json.dumps(
                {"error": "Submit response missing JobId", "response": submit_resp},
                indent=2,
            )

        started_at = time.time()
        while True:
            elapsed = time.time() - started_at
            if elapsed > timeout_seconds:
                return json.dumps(
                    {
                        "error": "Hunyuan job polling timed out",
                        "job_id": f"job_{job_id}",
                        "timeout_seconds": timeout_seconds,
                    },
                    indent=2,
                )

            query_resp = runtime.call_tencent_cloud_api(
                action="QueryHunyuanTo3DJob",
                payload={"JobId": job_id},
                secret_id=secret_id,
                secret_key=secret_key,
                region=region,
                timeout=30,
            )
            query_block = query_resp.get("Response", {})
            if query_block.get("Error"):
                return json.dumps(
                    {
                        "error": "QueryHunyuanTo3DJob failed",
                        "job_id": f"job_{job_id}",
                        "detail": query_block.get("Error"),
                    },
                    indent=2,
                )

            status = runtime.extract_hunyuan_status(query_resp)
            normalized_status = (status or "").upper()
            result_files = query_block.get("ResultFile3Ds") or []

            if normalized_status in {"DONE", "SUCCEEDED", "SUCCESS", "FINISHED", "COMPLETED"}:
                return json.dumps(
                    {
                        "job_id": f"job_{job_id}",
                        "status": normalized_status,
                        "result_file_3ds": result_files,
                        "response": query_resp,
                    },
                    indent=2,
                )
            if normalized_status in {"FAIL", "FAILED", "ERROR", "CANCELED", "CANCELLED", "ABORTED"}:
                return json.dumps(
                    {
                        "error": "Hunyuan job finished with failed status",
                        "job_id": f"job_{job_id}",
                        "status": normalized_status,
                        "response": query_resp,
                    },
                    indent=2,
                )
            if not normalized_status and result_files:
                return json.dumps(
                    {
                        "job_id": f"job_{job_id}",
                        "status": "DONE",
                        "result_file_3ds": result_files,
                        "response": query_resp,
                    },
                    indent=2,
                )
            time.sleep(poll_interval_seconds)
    except Exception as exc:
        logger.error("Error generating Hunyuan3D model: %s", exc)
        return f"Error generating Hunyuan3D model: {str(exc)}"


def search_3d_assets_by_text(
    ctx: Context,
    query: str,
    top_k: int = 3,
) -> str:
    """Search for 3D assets in retrieval database using text queries."""
    try:
        retrieval_host = os.getenv("RETRIEVAL_API_HOST", "localhost")
        retrieval_port = os.getenv("RETRIEVAL_API_PORT", "8002")
        base_url = f"http://{retrieval_host}:{retrieval_port}"
        if top_k < 1 or top_k > 100:
            return f"Error: top_k must be between 1 and 100, got {top_k}"
        payload = {"query": query, "top_k": top_k}
        response = requests.post(f"{base_url}/search/text", json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        results_list = result.get("results", [])
        if not results_list:
            return f"No results found for query: '{query}'"

        output = f"Found {len(results_list)} assets for query: '{query}'\n"
        for i, asset in enumerate(results_list, 1):
            output += f"{i}. Asset ID: {asset.get('asset_id', 'N/A')}\n"
            output += f"   Similarity: {asset.get('similarity', 0):.3f}\n"
            output += f"   Description (EN): {asset.get('caption_en', 'N/A')}\n"
            if asset.get("caption_cn"):
                output += f"   Description (CN): {asset.get('caption_cn', '')}\n"
            output += f"   Model URL: {asset.get('model_url', 'N/A')}\n"
            if asset.get("objaverse_id"):
                output += f"   Objaverse ID: {asset.get('objaverse_id', '')}\n"
            output += "\n"
        output += "\nTo import an asset, use import_retrieved_asset() with the asset_id and model_url."
        return output
    except requests.exceptions.ConnectionError:
        return f"Cannot connect to retrieval service at {retrieval_host}:{retrieval_port}."
    except requests.exceptions.Timeout:
        return "Request to retrieval service timed out."
    except requests.exceptions.HTTPError as exc:
        return f"HTTP error from retrieval service: {exc.response.status_code} - {exc.response.text}"
    except Exception as exc:
        logger.error("Error searching 3D assets: %s", str(exc))
        return f"Error searching 3D assets: {str(exc)}"


def import_retrieved_asset(
    ctx: Context,
    model_url: str,
    object_name: str = None,
) -> str:
    """Import a 3D asset from retrieval database into Blender."""
    object_name = object_name or "RetrievedAsset"
    return import_glb_model(ctx, model_url, object_name)


def asset_creation_strategy_text() -> str:
    service_status = runtime.probe_conditional_services(logger)

    sketchfab_ready = runtime.is_sketchfab_tool_enabled() and bool(runtime.get_sketchfab_api_key())
    infinigen_ready = runtime.is_infinigen_tool_enabled() and service_status.get("pcg_integrator", False)
    trellis2_ready = runtime.is_trellis2_tool_enabled() and service_status.get("trellis2", False)
    rodin_ready = runtime.is_rodin_tool_enabled() and bool(runtime.get_rodin_api_key())
    hunyuan_ready = runtime.is_hunyuan_tool_enabled()
    retrieval_ready = runtime.is_retrieval_tool_enabled() and service_status.get("retrieval", False)

    lines: list[str] = [
        "When creating 3D content in Blender:",
        "",
        "0. Before anything, call get_scene_info().",
        "1. Use only these currently available asset workflows (no status-check tools needed):",
        "   - PolyHaven",
        "     - Objects/models: download_polyhaven_asset(asset_type=\"models\")",
        "     - Materials/textures: download_polyhaven_asset(asset_type=\"textures\")",
        "     - Environment lighting: download_polyhaven_asset(asset_type=\"hdris\")",
        "     - Best for physically plausible materials and HDRI lighting setup",
    ]

    if sketchfab_ready:
        lines.extend(
            [
                "   - Sketchfab (server-side)",
                "     - Search: search_sketchfab_models(query=...)",
                "     - Compare previews: get_sketchfab_model_preview(uid)",
                "     - Import: download_sketchfab_model(uid=..., target_size=...)",
                "     - Best for authored realistic assets",
            ]
        )

    if infinigen_ready:
        lines.extend(
            [
                "   - Infinigen (Procedural Content Generation)",
                "     - Inspect supported asset types: get_infinigen_available_assets()",
                "     - Generate: generate_infinigen_assets(asset_type=\"...\")",
                "     - Best for natural assets and procedural indoor/architectural variations",
            ]
        )

    if trellis2_ready:
        lines.extend(
            [
                "   - TRELLIS2 (headless)",
                "     - Generate custom single object: generate_trellis2_model(text_prompt=..., object_name=...)",
                "     - Use when retrieval/libraries cannot satisfy a unique object request",
            ]
        )

    if rodin_ready:
        lines.extend(
            [
                "   - Hyper3D Rodin (local-client)",
                "     - Create task: generate_hyper3d_model_via_text(...) or generate_hyper3d_model_via_images(...)",
                "     - Poll task: poll_rodin_job_status(...)",
                "     - Import generated model: import_generated_asset(...)",
                "     - Best for single-item custom generation, especially from reference images",
            ]
        )

    if hunyuan_ready:
        lines.extend(
            [
                "   - Hunyuan3D (headless)",
                "     - Generate with built-in polling: generate_hunyuan3d_model(text_prompt=... or input_image_url=...)",
                "     - Best for single custom object generation in headless mode",
            ]
        )

    if retrieval_ready:
        lines.extend(
            [
                "   - 3D Asset Retrieval Database",
                "     - Search: search_3d_assets_by_text(query=..., top_k=...)",
                "     - Import: import_retrieved_asset(model_url=..., object_name=...)",
                "     - Best for common real-world objects and fast scene assembly",
            ]
        )

    if not any(
        [sketchfab_ready, infinigen_ready, trellis2_ready, rodin_ready, hunyuan_ready, retrieval_ready]
    ):
        lines.extend(
            [
                "   - Note: No extra generator/retrieval workflow is currently available beyond PolyHaven.",
            ]
        )

    priority_rules: list[str] = []
    if sketchfab_ready and retrieval_ready:
        priority_rules.append("For realistic authored objects: Sketchfab -> Retrieval")
    elif sketchfab_ready:
        priority_rules.append("For realistic authored objects: Sketchfab")
    elif retrieval_ready:
        priority_rules.append("For realistic authored objects: Retrieval")

    if infinigen_ready:
        if sketchfab_ready and retrieval_ready:
            priority_rules.append(
                "For natural or indoor procedural assets: Infinigen first, then Sketchfab, then Retrieval"
            )
        elif sketchfab_ready:
            priority_rules.append("For natural or indoor procedural assets: Infinigen first, then Sketchfab")
        elif retrieval_ready:
            priority_rules.append("For natural or indoor procedural assets: Infinigen first, then Retrieval")
        else:
            priority_rules.append("For natural or indoor procedural assets: Infinigen")

    if rodin_ready and retrieval_ready:
        priority_rules.append("For unique custom objects: Retrieval first, then Rodin")
    elif rodin_ready:
        priority_rules.append("For unique custom objects: Rodin")
    elif retrieval_ready:
        priority_rules.append("For unique custom objects: Retrieval")

    if trellis2_ready and retrieval_ready:
        priority_rules.append("For headless custom generation fallback: Retrieval first, then TRELLIS2")
    elif trellis2_ready:
        priority_rules.append("For headless custom generation fallback: TRELLIS2")

    if hunyuan_ready and retrieval_ready:
        priority_rules.append("For headless unique-object fallback: Retrieval first, then Hunyuan3D")
    elif hunyuan_ready:
        priority_rules.append("For headless unique-object fallback: Hunyuan3D")

    lines.extend(
        [
            "",
            "2. Always verify placement and scale after each import/generation:",
            "   - Inspect world/object bounding boxes to avoid clipping and floating assets",
            "   - Ensure spatial relationships and target size are consistent across objects",
            "",
            "3. Recommended source priority among available workflows:",
        ]
    )

    if priority_rules:
        for rule in priority_rules:
            lines.append(f"   - {rule}")
    else:
        lines.append("   - Use PolyHaven for materials/textures/HDRIs; rely on scripting for custom geometry.")

    generator_names = [
        name
        for enabled, name in [
            (trellis2_ready, "TRELLIS2"),
            (rodin_ready, "Rodin"),
            (hunyuan_ready, "Hunyuan3D"),
        ]
        if enabled
    ]

    lines.extend(
        [
            "",
            "4. Only fall back to scripting when:",
            "   - A simple primitive is explicitly requested",
            "   - No suitable asset exists after searching/generating with available workflows",
        ]
    )
    if generator_names:
        lines.append(
            "   - "
            + ", ".join(generator_names)
            + " generation failed, timed out, or returned unusable geometry"
        )
    lines.append("   - The task specifically requires basic procedural geometry/material edits")

    return "\n".join(lines)
