from __future__ import annotations

import json
import logging
import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import requests
from mcp.server.fastmcp import Context

from mcp_server import runtime

logger = logging.getLogger("BlenderMCPServer")

TRIPO_API_BASE_URL = "https://api.tripo3d.ai/v2/openapi"
DEFAULT_TRIPO_MODEL_VERSION = "P1-20260311"

_MODEL_ASSET_PRIORITY: dict[str, int] = {
    "PBR_MODEL": 0,
    "MODEL": 1,
    "BASE_MODEL": 2,
}
_TYPE_BY_URL_EXTENSION: dict[str, str] = {
    "glb": "GLB",
    "gltf": "GLTF",
    "fbx": "FBX",
    "obj": "OBJ",
    "png": "PNG",
    "jpg": "JPG",
    "jpeg": "JPEG",
    "webp": "WEBP",
}
_UPLOAD_IMAGE_TYPES: set[str] = {"jpg", "jpeg", "png", "webp"}
_DIRECT_IMAGE_URL_TYPES: set[str] = {"jpg", "jpeg", "png"}
_TEXTURE_QUALITY_VALUES: set[str] = {"standard", "detailed"}
_TEXTURE_ALIGNMENT_VALUES: set[str] = {"original_image", "geometry"}
_ORIENTATION_VALUES: set[str] = {"default", "align_image"}


def _tripo_disabled_message() -> str:
    return (
        "Tripo tools are disabled. They require BLENDER_MODE in {local-client, headless}, "
        "ENABLE_TRIPO=true, and TRIPO_API_KEY configured."
    )


def _get_tripo_api_key_or_error() -> tuple[Optional[str], Optional[str]]:
    if not runtime.is_tripo_tool_enabled():
        return None, _tripo_disabled_message()

    api_key = runtime.get_tripo_api_key()
    if not api_key:
        return None, "Tripo tools are enabled, but TRIPO_API_KEY is not configured."

    return api_key, None


def _tripo_headers(api_key: str, *, json_request: bool = False) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {api_key}"}
    if json_request:
        headers["Content-Type"] = "application/json"
    return headers


def _trace_id_from_response(response: requests.Response) -> str | None:
    trace_id = response.headers.get("X-Tripo-Trace-ID")
    if not isinstance(trace_id, str):
        return None
    cleaned = trace_id.strip()
    return cleaned or None


def _error_payload(
    message: str,
    *,
    action: str | None = None,
    status_code: int | None = None,
    task_id: str | None = None,
    status: str | None = None,
    trace_id: str | None = None,
    detail: Any = None,
    response: Any = None,
) -> str:
    payload: dict[str, Any] = {"error": message}
    if action:
        payload["action"] = action
    if status_code is not None:
        payload["status_code"] = status_code
    if task_id:
        payload["task_id"] = task_id
    if status:
        payload["status"] = status
    if trace_id:
        payload["trace_id"] = trace_id
    if detail is not None:
        payload["detail"] = detail
    if response is not None:
        payload["response"] = response
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _parse_json_response(
    response: requests.Response,
    *,
    action: str,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    trace_id = _trace_id_from_response(response)
    if response.status_code >= 400:
        return (
            None,
            _error_payload(
                f"Tripo {action} failed",
                action=action,
                status_code=response.status_code,
                trace_id=trace_id,
                detail=response.text[:400],
            ),
            trace_id,
        )

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        return (
            None,
            _error_payload(
                f"Tripo {action} returned invalid JSON",
                action=action,
                status_code=response.status_code,
                trace_id=trace_id,
                detail=str(exc),
                response=response.text[:400],
            ),
            trace_id,
        )

    if not isinstance(payload, dict):
        return (
            None,
            _error_payload(
                f"Tripo {action} returned an unexpected payload type",
                action=action,
                status_code=response.status_code,
                trace_id=trace_id,
                detail=type(payload).__name__,
                response=payload,
            ),
            trace_id,
        )

    if payload.get("code") != 0:
        return (
            None,
            _error_payload(
                f"Tripo {action} returned API error",
                action=action,
                status_code=response.status_code,
                trace_id=trace_id,
                detail={
                    "code": payload.get("code"),
                    "message": payload.get("message"),
                    "suggestion": payload.get("suggestion"),
                },
                response=payload,
            ),
            trace_id,
        )

    data = payload.get("data")
    if not isinstance(data, dict):
        return (
            None,
            _error_payload(
                f"Tripo {action} response is missing a data object",
                action=action,
                status_code=response.status_code,
                trace_id=trace_id,
                response=payload,
            ),
            trace_id,
        )

    return data, None, trace_id


def _infer_url_extension(url: str) -> str | None:
    path = urlparse(url).path
    if "." not in path:
        return None
    suffix = path.rsplit(".", 1)[-1].strip().lower()
    return suffix or None


def _normalize_asset(output_field: str, raw_url: Any) -> dict[str, Any] | None:
    if not isinstance(raw_url, str) or not raw_url.strip():
        return None

    url = raw_url.strip()
    url_extension = _infer_url_extension(url)
    asset_type = output_field.strip().upper()

    normalized: dict[str, Any] = {
        "type": asset_type,
        "output_field": output_field,
        "url": url,
    }
    if url_extension:
        normalized["url_extension"] = url_extension
        normalized["resolved_type"] = _TYPE_BY_URL_EXTENSION.get(url_extension, asset_type)
        normalized["is_archive"] = url_extension == "zip"
    return normalized


def _normalize_output_assets(output: dict[str, Any]) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for output_field in (
        "pbr_model",
        "model",
        "base_model",
        "generated_image",
        "rendered_image",
    ):
        normalized = _normalize_asset(output_field, output.get(output_field))
        if normalized is not None:
            assets.append(normalized)
    return assets


def _select_preferred_model_asset(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for index, asset in enumerate(assets):
        asset_type = str(asset.get("type") or "").upper()
        if asset_type not in _MODEL_ASSET_PRIORITY:
            continue
        candidates.append((_MODEL_ASSET_PRIORITY[asset_type], index, asset))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def _infer_image_type_from_content_type(content_type: str | None) -> str | None:
    if not isinstance(content_type, str):
        return None
    normalized = content_type.split(";", 1)[0].strip().lower()
    mapping = {
        "image/jpeg": "jpeg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }
    return mapping.get(normalized)


def _validate_image_type(image_type: str | None) -> str:
    if image_type in _UPLOAD_IMAGE_TYPES:
        return image_type
    raise ValueError(
        "Tripo image input must be one of: jpg, jpeg, png, webp "
        "(remote direct URL supports jpg/jpeg/png)."
    )


def _build_upload_file_tuple(
    *,
    filename: str,
    content: bytes,
    image_type: str,
) -> tuple[str, bytes, str]:
    mime_type = mimetypes.types_map.get(f".{image_type}") or f"image/{image_type}"
    return filename, content, mime_type


def _upload_image_bytes(
    *,
    api_key: str,
    filename: str,
    content: bytes,
    image_type: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    response = requests.post(
        f"{TRIPO_API_BASE_URL}/upload/sts",
        headers=_tripo_headers(api_key),
        files={"file": _build_upload_file_tuple(filename=filename, content=content, image_type=image_type)},
        timeout=min(120, timeout_seconds),
    )
    data, error_message, _trace_id = _parse_json_response(response, action="upload image")
    if error_message:
        raise RuntimeError(error_message)

    image_token = data.get("image_token") if isinstance(data, dict) else None
    if not isinstance(image_token, str) or not image_token.strip():
        raise RuntimeError(
            _error_payload(
                "Tripo upload image response is missing image_token",
                action="upload image",
                response=data,
            )
        )

    return {"type": image_type, "file_token": image_token.strip()}


def _build_image_input(
    *,
    api_key: str,
    input_image_url: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    normalized_input = input_image_url.strip()
    parsed = urlparse(normalized_input)

    if parsed.scheme.lower() in {"http", "https"}:
        image_type = _infer_url_extension(normalized_input)
        if image_type in _DIRECT_IMAGE_URL_TYPES:
            return {"type": image_type, "url": normalized_input}

        download_response = requests.get(
            normalized_input,
            headers=runtime.REQ_HEADERS,
            timeout=min(60, timeout_seconds),
        )
        if download_response.status_code >= 400:
            raise RuntimeError(
                _error_payload(
                    "Failed to fetch remote image for Tripo upload",
                    action="download remote image",
                    status_code=download_response.status_code,
                    detail=download_response.text[:400],
                )
            )

        resolved_type = image_type or _infer_image_type_from_content_type(
            download_response.headers.get("Content-Type")
        )
        resolved_type = _validate_image_type(resolved_type)
        filename = Path(parsed.path).name or f"tripo_input.{resolved_type}"
        return _upload_image_bytes(
            api_key=api_key,
            filename=filename,
            content=download_response.content,
            image_type=resolved_type,
            timeout_seconds=timeout_seconds,
        )

    image_path = Path(normalized_input).expanduser()
    if not image_path.is_file():
        raise FileNotFoundError(f"Input image does not exist: {normalized_input}")

    image_type = _validate_image_type(image_path.suffix.lstrip(".").lower() or None)
    return _upload_image_bytes(
        api_key=api_key,
        filename=image_path.name,
        content=image_path.read_bytes(),
        image_type=image_type,
        timeout_seconds=timeout_seconds,
    )


def _build_success_payload(
    *,
    task_id: str,
    task_data: dict[str, Any],
    model_version: str,
    submit_trace_id: str | None,
    poll_trace_id: str | None,
) -> str:
    output = task_data.get("output") if isinstance(task_data.get("output"), dict) else {}
    normalized_assets = _normalize_output_assets(output)
    preferred_model_asset = _select_preferred_model_asset(normalized_assets)
    trace_ids = {
        key: value
        for key, value in {
            "submit": submit_trace_id,
            "last_poll": poll_trace_id,
        }.items()
        if value
    }
    return json.dumps(
        {
            "task_id": task_id,
            "status": task_data.get("status"),
            "progress": task_data.get("progress"),
            "model_version": model_version,
            "normalized_assets": normalized_assets,
            "preferred_model_asset": preferred_model_asset,
            "preview_image_url": output.get("rendered_image") or output.get("generated_image"),
            "recommended_next_tool": "import_glb_model" if preferred_model_asset else None,
            "recommended_next_action": (
                "Call import_glb_model(model_url=preferred_model_asset.url, object_name=...) "
                "promptly because Tripo download URLs are temporary."
                if preferred_model_asset
                else None
            ),
            "note": "Tripo output URLs typically expire after about 5 minutes.",
            "trace_ids": trace_ids or None,
            "response": task_data,
        },
        indent=2,
        ensure_ascii=False,
    )


def generate_tripo3d_model(
    ctx: Context,
    text_prompt: Optional[str] = None,
    input_image_url: Optional[str] = None,
    input_image_name: Optional[str] = None,
    input_image_id: Optional[str] = None,
    model_version: str = DEFAULT_TRIPO_MODEL_VERSION,
    timeout_seconds: int = 300,
    poll_interval_seconds: float = 2.0,
    face_limit: Optional[int] = None,
    texture: bool = True,
    pbr: bool = True,
    texture_quality: str = "standard",
    export_uv: bool = False,
    auto_size: bool = False,
    model_seed: Optional[int] = None,
    texture_seed: Optional[int] = None,
    image_seed: Optional[int] = None,
    enable_image_autofix: bool = False,
    texture_alignment: Optional[str] = None,
    orientation: Optional[str] = None,
) -> str:
    """Generate a Tripo 3D model via the official API with built-in polling."""
    del ctx, input_image_name, input_image_id

    api_key, error_message = _get_tripo_api_key_or_error()
    if error_message:
        return error_message
    assert api_key is not None

    normalized_text_prompt = (text_prompt or "").strip()
    normalized_input_image_url = (input_image_url or "").strip()
    if bool(normalized_text_prompt) == bool(normalized_input_image_url):
        return "Error: Provide exactly one of text_prompt or input_image_url."

    if timeout_seconds == 300:
        timeout_seconds = int(os.getenv("TRIPO_TIMEOUT_SECONDS", "300"))
    if poll_interval_seconds == 2.0:
        poll_interval_seconds = float(os.getenv("TRIPO_POLL_INTERVAL_SECONDS", "2"))
    if model_version == DEFAULT_TRIPO_MODEL_VERSION:
        model_version = os.getenv("TRIPO_MODEL_VERSION", DEFAULT_TRIPO_MODEL_VERSION).strip()
        model_version = model_version or DEFAULT_TRIPO_MODEL_VERSION
    if timeout_seconds <= 0:
        return "Error: timeout_seconds must be > 0."
    if poll_interval_seconds <= 0:
        return "Error: poll_interval_seconds must be > 0."
    if not model_version.strip():
        return "Error: model_version must not be empty."
    if texture_quality not in _TEXTURE_QUALITY_VALUES:
        return "Error: texture_quality must be one of {'standard', 'detailed'}."
    if texture_alignment and texture_alignment not in _TEXTURE_ALIGNMENT_VALUES:
        return "Error: texture_alignment must be one of {'original_image', 'geometry'}."
    if orientation and orientation not in _ORIENTATION_VALUES:
        return "Error: orientation must be one of {'default', 'align_image'}."
    if normalized_text_prompt and len(normalized_text_prompt) > 1024:
        return "Error: text_prompt exceeds 1024 characters."
    if face_limit is not None:
        if face_limit <= 0:
            return "Error: face_limit must be > 0."
        if model_version.upper().startswith("P1-") and not 48 <= face_limit <= 20000:
            return "Error: face_limit must be between 48 and 20000 for P1 model versions."

    payload: dict[str, Any] = {
        "model_version": model_version,
        "texture": texture,
        "pbr": pbr,
        "texture_quality": texture_quality,
        "export_uv": export_uv,
        "auto_size": auto_size,
    }
    if face_limit is not None:
        payload["face_limit"] = face_limit
    if model_seed is not None:
        payload["model_seed"] = model_seed
    if texture_seed is not None:
        payload["texture_seed"] = texture_seed

    try:
        if normalized_text_prompt:
            payload["type"] = "text_to_model"
            payload["prompt"] = normalized_text_prompt
            if image_seed is not None:
                payload["image_seed"] = image_seed
        else:
            payload["type"] = "image_to_model"
            payload["file"] = _build_image_input(
                api_key=api_key,
                input_image_url=normalized_input_image_url,
                timeout_seconds=timeout_seconds,
            )
            payload["enable_image_autofix"] = enable_image_autofix
            if texture_alignment:
                payload["texture_alignment"] = texture_alignment
            if orientation:
                payload["orientation"] = orientation

        submit_response = requests.post(
            f"{TRIPO_API_BASE_URL}/task",
            headers=_tripo_headers(api_key, json_request=True),
            json=payload,
            timeout=min(60, timeout_seconds),
        )
        submit_data, submit_error, submit_trace_id = _parse_json_response(
            submit_response,
            action="submit task",
        )
        if submit_error:
            return submit_error

        task_id = submit_data.get("task_id") if isinstance(submit_data, dict) else None
        if not isinstance(task_id, str) or not task_id.strip():
            return _error_payload(
                "Tripo submit task response is missing task_id",
                action="submit task",
                trace_id=submit_trace_id,
                response=submit_data,
            )
        task_id = task_id.strip()

        started_at = time.time()
        last_status: str | None = None
        last_trace_id: str | None = None
        while True:
            elapsed = time.time() - started_at
            if elapsed > timeout_seconds:
                return _error_payload(
                    "Tripo task polling timed out",
                    action="poll task",
                    task_id=task_id,
                    status=last_status,
                    trace_id=last_trace_id,
                    detail={"timeout_seconds": timeout_seconds},
                )

            poll_response = requests.get(
                f"{TRIPO_API_BASE_URL}/task/{task_id}",
                headers=_tripo_headers(api_key),
                timeout=30,
            )
            task_data, poll_error, poll_trace_id = _parse_json_response(
                poll_response,
                action="poll task",
            )
            last_trace_id = poll_trace_id
            if poll_error:
                return poll_error

            last_status = str(task_data.get("status") or "").strip().lower()
            if last_status == "success":
                return _build_success_payload(
                    task_id=task_id,
                    task_data=task_data,
                    model_version=model_version,
                    submit_trace_id=submit_trace_id,
                    poll_trace_id=poll_trace_id,
                )
            if last_status in {"failed", "banned", "expired", "cancelled", "unknown"}:
                return _error_payload(
                    "Tripo task finished with failed status",
                    action="poll task",
                    task_id=task_id,
                    status=last_status,
                    trace_id=poll_trace_id,
                    response=task_data,
                )
            time.sleep(poll_interval_seconds)
    except requests.exceptions.Timeout:
        return "Error: Tripo request timed out."
    except requests.exceptions.ConnectionError:
        return "Error: Cannot connect to Tripo API endpoint."
    except Exception as exc:
        logger.error("Error generating Tripo 3D model: %s", exc)
        message = str(exc)
        if message.startswith("{") and message.endswith("}"):
            return message
        return f"Error generating Tripo 3D model: {message}"
