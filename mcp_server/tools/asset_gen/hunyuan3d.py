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


def _build_success_payload(job_id: str, status: str, result_files: list[Any], query_resp: dict[str, Any]) -> str:
    normalized_assets = _normalize_hunyuan_assets(result_files)
    preferred_model_asset = _select_preferred_model_asset(normalized_assets)
    return json.dumps(
        {
            "job_id": f"job_{job_id}",
            "status": status,
            "result_file_3ds": result_files,
            "normalized_assets": normalized_assets,
            "preferred_model_asset": preferred_model_asset,
            "response": query_resp,
        },
        indent=2,
    )


def generate_hunyuan3d_model(
    ctx: Context,
    text_prompt: Optional[str] = None,
    input_image_url: Optional[str] = None,
    input_image_name: Optional[str] = None,
    input_image_id: Optional[str] = None,
    timeout_seconds: int = 300,
    poll_interval_seconds: float = 5.0,
) -> str:
    """Generate Hunyuan3D asset via Tencent official API from MCP server side."""
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
                return _build_success_payload(job_id, normalized_status, result_files, query_resp)
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
                return _build_success_payload(job_id, "DONE", result_files, query_resp)
            time.sleep(poll_interval_seconds)
    except Exception as exc:
        logger.error("Error generating Hunyuan3D model: %s", exc)
        return f"Error generating Hunyuan3D model: {str(exc)}"
