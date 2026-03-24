from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

import requests
from mcp.server.fastmcp import Context

from mcp_server import runtime

logger = logging.getLogger("BlenderMCPServer")

RODIN_MAIN_SITE_API_BASE_URL = "https://hyperhuman.deemos.com/api/v2"


def _rodin_disabled_message() -> str:
    return (
        "Rodin tools are disabled. They require BLENDER_MODE in {local-client, headless}, "
        "ENABLE_RODIN=true, and RODIN_API_KEY configured."
    )


def _get_rodin_api_key_or_error() -> tuple[Optional[str], Optional[str]]:
    if not runtime.is_rodin_tool_enabled():
        return None, _rodin_disabled_message()

    api_key = runtime.get_rodin_api_key()
    if not api_key:
        return None, "Rodin tools are enabled, but RODIN_API_KEY is not configured."

    return api_key, None


def _rodin_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def _format_rodin_submit_response(result: dict[str, Any]) -> str:
    if not result.get("submit_time"):
        return json.dumps(result, indent=2)

    output: dict[str, Any] = {}
    task_uuid = result.get("uuid")
    if isinstance(task_uuid, str) and task_uuid:
        output["task_uuid"] = task_uuid

    jobs = result.get("jobs") or {}
    if isinstance(jobs, dict):
        subscription_key_value = jobs.get("subscription_key") or jobs.get("subscription_id")
        if isinstance(subscription_key_value, str) and subscription_key_value:
            # Keep legacy alias for compatibility with older callers.
            output["subscription_key"] = subscription_key_value
            output["subscription_id"] = subscription_key_value

    if not output:
        return json.dumps(result, indent=2)
    return json.dumps(output, indent=2)


def generate_hyper3d_model_via_text(
    ctx: Context,
    text_prompt: str,
    bbox_condition: Optional[list[float]] = None,
) -> str:
    """Create a Hyper3D Rodin generation task using text prompt."""
    api_key, error_message = _get_rodin_api_key_or_error()
    if error_message:
        return error_message
    assert api_key is not None

    processed_bbox = runtime.process_bbox(bbox_condition)

    try:
        files: list[tuple[str, tuple[Optional[str], str]]] = [
            ("tier", (None, "Sketch")),
            ("mesh_mode", (None, "Raw")),
            ("prompt", (None, text_prompt)),
        ]
        if processed_bbox:
            files.append(("bbox_condition", (None, json.dumps(processed_bbox))))

        response = requests.post(
            f"{RODIN_MAIN_SITE_API_BASE_URL}/rodin",
            headers=_rodin_headers(api_key),
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
    input_image_name: Optional[str] = None,
    input_image_id: Optional[str] = None,
    bbox_condition: Optional[list[float]] = None,
) -> str:
    """Create a Hyper3D Rodin generation task using image inputs."""
    del input_image_name, input_image_id
    api_key, error_message = _get_rodin_api_key_or_error()
    if error_message:
        return error_message
    assert api_key is not None

    if input_image_paths and input_image_urls:
        return "Error: Provide only one of input_image_paths or input_image_urls."
    if input_image_urls:
        return (
            "Error: MAIN_SITE Rodin only supports input_image_paths. "
            "Please upload local images and pass input_image_paths."
        )
    if not input_image_paths:
        return "Error: At least one image path is required in input_image_paths."

    processed_bbox = runtime.process_bbox(bbox_condition)

    try:
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
            headers=_rodin_headers(api_key),
            files=files,
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
    subscription_id: Optional[str] = None,
) -> str:
    """Poll Hyper3D Rodin task status using MAIN SITE subscription key."""
    api_key, error_message = _get_rodin_api_key_or_error()
    if error_message:
        return error_message
    assert api_key is not None

    try:
        resolved_subscription_key = (subscription_key or subscription_id or "").strip()
        if not resolved_subscription_key:
            return (
                "Error: MAIN_SITE Rodin status polling requires subscription_key "
                "(or legacy subscription_id)."
            )

        response = requests.post(
            f"{RODIN_MAIN_SITE_API_BASE_URL}/status",
            headers=_rodin_headers(api_key),
            json={"subscription_key": resolved_subscription_key},
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
) -> str:
    """Import a Hyper3D generated asset into Blender."""
    api_key, error_message = _get_rodin_api_key_or_error()
    if error_message:
        return error_message
    assert api_key is not None

    try:
        glb_url: Optional[str] = None

        if not task_uuid:
            return "Error: MAIN_SITE Rodin import requires task_uuid."

        response = requests.post(
            f"{RODIN_MAIN_SITE_API_BASE_URL}/download",
            headers=_rodin_headers(api_key),
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
