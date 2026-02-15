from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional

from mcp.server.fastmcp import Context

from mcp_server import runtime

logger = logging.getLogger("BlenderMCPServer")


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
