from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional
from urllib.parse import urlparse

from mcp.server.fastmcp import Context

from mcp_server import runtime

logger = logging.getLogger("BlenderMCPServer")

_HUNYUAN3D_API_SERVICE = "ai3d"
_HUNYUAN3D_API_VERSION = "2025-05-13"
_HUNYUAN3D_DEFAULT_ENABLE_PBR = True
_HUNYUAN3D_DEFAULT_RESULT_FORMAT = "GLB"
_HUNYUAN3D_SUCCESS_STATUSES = {"DONE", "SUCCEEDED", "SUCCESS", "FINISHED", "COMPLETED"}
_HUNYUAN3D_FAILED_STATUSES = {"FAIL", "FAILED", "ERROR", "CANCELED", "CANCELLED", "ABORTED"}
_HUNYUAN3D_MODE_ALIASES = {
    "pro": "pro",
    "professional": "pro",
    "professional版": "pro",
    "专业": "pro",
    "专业版": "pro",
    "rapid": "rapid",
    "fast": "rapid",
    "极速": "rapid",
    "极速版": "rapid",
}
_HUNYUAN3D_MODE_CONFIGS: dict[str, dict[str, str | int]] = {
    "pro": {
        "submit_action": "SubmitHunyuanTo3DProJob",
        "query_action": "QueryHunyuanTo3DProJob",
        "max_text_prompt_chars": 1024,
    },
    "rapid": {
        "submit_action": "SubmitHunyuanTo3DRapidJob",
        "query_action": "QueryHunyuanTo3DRapidJob",
        "max_text_prompt_chars": 200,
    },
}

_MODEL_TYPE_PRIORITY: dict[str, int] = {
    "OBJ": 0,
    "GLB": 1,
    "GLTF": 2,
    "FBX": 3,
    "BLEND": 4,
    "STL": 5,
}
_PREVIEW_ASSET_TYPES: set[str] = {"GIF", "PNG", "JPG", "JPEG", "WEBP"}
_TYPE_BY_URL_EXTENSION: dict[str, str] = {
    "obj": "OBJ",
    "glb": "GLB",
    "gltf": "GLTF",
    "fbx": "FBX",
    "blend": "BLEND",
    "stl": "STL",
    "gif": "GIF",
    "png": "PNG",
    "jpg": "JPG",
    "jpeg": "JPEG",
    "webp": "WEBP",
}


def _infer_url_extension(url: str) -> str | None:
    path = urlparse(url).path
    if "." not in path:
        return None
    suffix = path.rsplit(".", 1)[-1].strip().lower()
    return suffix or None


def _normalize_hunyuan_asset_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    raw_url = entry.get("Url") or entry.get("url")
    if not isinstance(raw_url, str) or not raw_url.strip():
        return None

    url = raw_url.strip()
    raw_type = entry.get("Type") or entry.get("type")
    asset_type = str(raw_type).strip().upper() if raw_type else ""
    url_extension = _infer_url_extension(url)
    if not asset_type and url_extension:
        asset_type = _TYPE_BY_URL_EXTENSION.get(url_extension, "")

    normalized: dict[str, Any] = {"url": url}
    if asset_type:
        normalized["type"] = asset_type
    if url_extension:
        normalized["url_extension"] = url_extension
        normalized["is_archive"] = url_extension == "zip"
    return normalized


def _normalize_hunyuan_assets(result_files: list[Any]) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []

    def _walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                _walk(item)
            return
        if not isinstance(node, dict):
            return

        nested = node.get("File3D") or node.get("file3d")
        if isinstance(nested, list):
            for item in nested:
                _walk(item)

        normalized = _normalize_hunyuan_asset_entry(node)
        if normalized is not None:
            assets.append(normalized)

    _walk(result_files)
    return assets


def _select_preferred_model_asset(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates: list[tuple[int, int, dict[str, Any]]] = []

    for index, asset in enumerate(assets):
        asset_type = str(asset.get("type") or "").upper()
        if asset_type in _PREVIEW_ASSET_TYPES:
            continue
        priority = _MODEL_TYPE_PRIORITY.get(asset_type, 100)
        candidates.append((priority, index, asset))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def _normalize_generation_mode(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    return _HUNYUAN3D_MODE_ALIASES.get(normalized)


def _resolve_generation_mode(requested_mode: str | None) -> tuple[str | None, str | None]:
    raw_mode = requested_mode
    if raw_mode is None:
        raw_mode = os.getenv("HUNYUAN3D_GENERATION_MODE") or os.getenv("HUNYUAN3D_API_VARIANT")
    normalized_mode = _normalize_generation_mode(raw_mode)
    if normalized_mode is not None:
        return normalized_mode, None
    if raw_mode is None or not raw_mode.strip():
        return "rapid", None
    return None, f"Error: Unsupported generation_mode '{raw_mode}'. Use 'pro' or 'rapid'."


def _get_generation_mode_config(generation_mode: str) -> dict[str, str | int]:
    return _HUNYUAN3D_MODE_CONFIGS[generation_mode]


def _build_success_payload(
    job_id: str,
    status: str,
    result_files: list[Any],
    query_resp: dict[str, Any],
    *,
    generation_mode: str,
) -> str:
    response_block = query_resp.get("Response", {})
    normalized_assets = _normalize_hunyuan_assets(result_files)
    preferred_model_asset = _select_preferred_model_asset(normalized_assets)
    payload: dict[str, Any] = {
        "job_id": f"job_{job_id}",
        "status": status,
        "generation_mode": generation_mode,
        "result_file_3ds": result_files,
        "normalized_assets": normalized_assets,
        "preferred_model_asset": preferred_model_asset,
        "response": query_resp,
    }

    result_credit_details = response_block.get("ResultCreditDetails")
    if result_credit_details not in (None, ""):
        payload["result_credit_details"] = result_credit_details

    result_credit_consumed = response_block.get("ResultCreditConsumed")
    if result_credit_consumed is not None:
        payload["result_credit_consumed"] = result_credit_consumed

    return json.dumps(payload, indent=2, ensure_ascii=False)


def _extract_query_job_error_detail(response_block: dict[str, Any]) -> dict[str, str] | None:
    error_code = str(response_block.get("ErrorCode") or "").strip()
    error_message = str(response_block.get("ErrorMessage") or "").strip()
    if not error_code and not error_message:
        return None

    detail: dict[str, str] = {}
    if error_code:
        detail["Code"] = error_code
    if error_message:
        detail["Message"] = error_message
    return detail


def generate_hunyuan3d_model(
    ctx: Context,
    text_prompt: Optional[str] = None,
    input_image_url: Optional[str] = None,
    input_image_name: Optional[str] = None,
    input_image_id: Optional[str] = None,
    generation_mode: Optional[str] = None,
    timeout_seconds: int = 300,
    poll_interval_seconds: float = 5.0,
) -> str:
    """Generate Hunyuan3D via Tencent Cloud using pro or rapid mode."""
    del input_image_name, input_image_id
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

    resolved_generation_mode, generation_mode_error = _resolve_generation_mode(generation_mode)
    if generation_mode_error is not None:
        return generation_mode_error
    assert resolved_generation_mode is not None
    mode_config = _get_generation_mode_config(resolved_generation_mode)
    submit_action = str(mode_config["submit_action"])
    query_action = str(mode_config["query_action"])
    max_text_prompt_chars = int(mode_config["max_text_prompt_chars"])

    if timeout_seconds == 300:
        timeout_seconds = int(os.getenv("HUNYUAN3D_TIMEOUT_SECONDS", "300"))
    if poll_interval_seconds == 5.0:
        poll_interval_seconds = float(os.getenv("HUNYUAN3D_POLL_INTERVAL_SECONDS", "5"))
    if timeout_seconds <= 0:
        return "Error: timeout_seconds must be > 0."
    if poll_interval_seconds <= 0:
        return "Error: poll_interval_seconds must be > 0."

    submit_payload: dict[str, Any] = {}
    if text_prompt:
        if len(text_prompt) > max_text_prompt_chars:
            return f"Error: text_prompt exceeds {max_text_prompt_chars} characters."
        submit_payload["Prompt"] = text_prompt

    if input_image_url:
        try:
            key, encoded = runtime.encode_local_or_url_image(input_image_url)
            submit_payload[key] = encoded
        except Exception as exc:
            return f"Error: Failed to encode local image: {str(exc)}"

    submit_payload["EnablePBR"] = _HUNYUAN3D_DEFAULT_ENABLE_PBR
    if resolved_generation_mode == "rapid":
        submit_payload["ResultFormat"] = _HUNYUAN3D_DEFAULT_RESULT_FORMAT

    try:
        submit_resp = runtime.call_tencent_cloud_api(
            action=submit_action,
            payload=submit_payload,
            secret_id=secret_id,
            secret_key=secret_key,
            service=_HUNYUAN3D_API_SERVICE,
            version=_HUNYUAN3D_API_VERSION,
            region=region,
            timeout=min(60, timeout_seconds),
        )
        response_block = submit_resp.get("Response", {})
        if response_block.get("Error"):
            return json.dumps(
                {
                    "error": f"{submit_action} failed",
                    "generation_mode": resolved_generation_mode,
                    "detail": response_block.get("Error"),
                },
                indent=2,
                ensure_ascii=False,
            )

        job_id = response_block.get("JobId")
        if not job_id:
            return json.dumps(
                {
                    "error": "Submit response missing JobId",
                    "generation_mode": resolved_generation_mode,
                    "response": submit_resp,
                },
                indent=2,
                ensure_ascii=False,
            )

        started_at = time.time()
        while True:
            elapsed = time.time() - started_at
            if elapsed > timeout_seconds:
                return json.dumps(
                    {
                        "error": "Hunyuan job polling timed out",
                        "job_id": f"job_{job_id}",
                        "generation_mode": resolved_generation_mode,
                        "timeout_seconds": timeout_seconds,
                    },
                    indent=2,
                    ensure_ascii=False,
                )

            query_resp = runtime.call_tencent_cloud_api(
                action=query_action,
                payload={"JobId": job_id},
                secret_id=secret_id,
                secret_key=secret_key,
                service=_HUNYUAN3D_API_SERVICE,
                version=_HUNYUAN3D_API_VERSION,
                region=region,
                timeout=30,
            )
            query_block = query_resp.get("Response", {})
            if query_block.get("Error"):
                return json.dumps(
                    {
                        "error": f"{query_action} failed",
                        "job_id": f"job_{job_id}",
                        "generation_mode": resolved_generation_mode,
                        "detail": query_block.get("Error"),
                    },
                    indent=2,
                    ensure_ascii=False,
                )

            status = runtime.extract_hunyuan_status(query_resp)
            normalized_status = (status or "").upper()
            result_files = query_block.get("ResultFile3Ds") or []
            query_error_detail = _extract_query_job_error_detail(query_block)

            if normalized_status in _HUNYUAN3D_SUCCESS_STATUSES:
                return _build_success_payload(
                    job_id,
                    normalized_status,
                    result_files,
                    query_resp,
                    generation_mode=resolved_generation_mode,
                )
            if normalized_status in _HUNYUAN3D_FAILED_STATUSES:
                payload = {
                    "error": "Hunyuan job finished with failed status",
                    "job_id": f"job_{job_id}",
                    "status": normalized_status,
                    "generation_mode": resolved_generation_mode,
                    "response": query_resp,
                }
                if query_error_detail is not None:
                    payload["detail"] = query_error_detail
                return json.dumps(payload, indent=2, ensure_ascii=False)
            if not normalized_status and result_files:
                return _build_success_payload(
                    job_id,
                    "DONE",
                    result_files,
                    query_resp,
                    generation_mode=resolved_generation_mode,
                )
            time.sleep(poll_interval_seconds)
    except Exception as exc:
        logger.error("Error generating Hunyuan3D model: %s", exc)
        return f"Error generating Hunyuan3D model: {str(exc)}"
