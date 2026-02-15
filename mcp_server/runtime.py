from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.request import urlopen

import requests

from scene_agent.blender.connection import BlenderConnection
from scene_agent.utils.health_check_services import ServiceHealthChecker

DEFAULT_CLIENT_HOST = "localhost"
DEFAULT_CLIENT_PORT = 9876
REQ_HEADERS = {"User-Agent": "blender-mcp-vision"}
POLYHAVEN_META_URL = "https://fishwowater.oss-cn-shenzhen.aliyuncs.com/polyhaven_meta.json"

_blender_connection: BlenderConnection | None = None


def load_polyhaven_meta_info(logger) -> dict[str, Any]:
    try:
        with urlopen(POLYHAVEN_META_URL, timeout=30) as response:
            return json.load(response)
    except Exception as exc:
        logger.warning(
            "Failed to load PolyHaven meta from URL, falling back to local file: %s",
            str(exc),
        )
        local_path = Path("assets/polyhaven_meta.json")
        if local_path.exists():
            with local_path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        return {}


def get_blender_connection(logger):
    global _blender_connection
    if _blender_connection is not None:
        try:
            _blender_connection.send_command("get_scene_info")
            return _blender_connection
        except Exception as exc:
            logger.warning("Existing connection is no longer valid: %s", str(exc))
            try:
                _blender_connection.disconnect()
            except Exception:
                pass
            _blender_connection = None

    host = os.getenv("BLENDER_HOST", DEFAULT_CLIENT_HOST)
    port = int(os.getenv("BLENDER_PORT", str(DEFAULT_CLIENT_PORT)))
    _blender_connection = BlenderConnection(host=host, port=port, logger=logger)
    if not _blender_connection.connect():
        logger.error("Failed to connect to Blender")
        _blender_connection = None
        raise Exception("Could not connect to Blender. Make sure the Blender addon is running.")
    logger.info("Created new persistent connection to Blender")
    return _blender_connection


def disconnect_blender_connection(logger) -> None:
    global _blender_connection
    if _blender_connection:
        logger.info("Disconnecting from Blender on shutdown")
        _blender_connection.disconnect()
        _blender_connection = None


def parse_env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def get_blender_mode() -> str:
    return os.getenv("BLENDER_MODE", "").strip().lower()


def is_hunyuan_tool_enabled() -> bool:
    return get_blender_mode() == "headless" and parse_env_bool("ENABLE_HUNYUAN", False)


def is_rodin_tool_enabled() -> bool:
    return get_blender_mode() in {"local-client", "headless"} and parse_env_bool("ENABLE_RODIN", False)


def get_rodin_api_key() -> str:
    return os.getenv("RODIN_API_KEY", "").strip()


def get_rodin_mode() -> str:
    raw = os.getenv("RODIN_MODE", "MAIN_SITE").strip().upper()
    if raw in {"MAIN_SITE", "FAL_AI"}:
        return raw
    return "MAIN_SITE"


def is_trellis2_tool_enabled() -> bool:
    return get_blender_mode() == "headless" and parse_env_bool("ENABLE_TRELLIS2", False)


def is_retrieval_tool_enabled() -> bool:
    return parse_env_bool("ENABLE_RETRIEVAL", False)


def is_infinigen_tool_enabled() -> bool:
    return parse_env_bool("ENABLE_INFINIGEN", False)


def is_sketchfab_tool_enabled() -> bool:
    return parse_env_bool("ENABLE_SKETCHFAB", False)


def get_sketchfab_api_key() -> str:
    return os.getenv("SKETCHFAB_API_KEY", "").strip()


def process_bbox(original_bbox: Optional[list[float] | list[int]]) -> Optional[list[int]]:
    if original_bbox is None:
        return None
    if len(original_bbox) != 3:
        raise ValueError("bbox_condition must contain exactly 3 values")
    if all(isinstance(i, int) for i in original_bbox):
        return list(original_bbox)
    if any(float(i) <= 0 for i in original_bbox):
        raise ValueError("bbox_condition values must be greater than zero")
    max_value = max(float(i) for i in original_bbox)
    return [int(float(i) / max_value * 100) for i in original_bbox]


def build_tencent_cloud_headers(
    *,
    action: str,
    version: str,
    region: str,
    service: str,
    payload: dict[str, Any],
    secret_id: str,
    secret_key: str,
) -> tuple[dict[str, str], str, str]:
    endpoint = f"https://{service}.tencentcloudapi.com"
    host = f"{service}.tencentcloudapi.com"
    timestamp = int(time.time())
    date = datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")

    canonical_uri = "/"
    canonical_querystring = ""
    payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    canonical_headers = f"content-type:application/json; charset=utf-8\nhost:{host}\n"
    signed_headers = "content-type;host"
    hashed_request_payload = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    canonical_request = (
        "POST\n"
        f"{canonical_uri}\n"
        f"{canonical_querystring}\n"
        f"{canonical_headers}\n"
        f"{signed_headers}\n"
        f"{hashed_request_payload}"
    )

    algorithm = "TC3-HMAC-SHA256"
    credential_scope = f"{date}/{service}/tc3_request"
    string_to_sign = (
        f"{algorithm}\n"
        f"{timestamp}\n"
        f"{credential_scope}\n"
        f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
    )

    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    secret_date = _sign(f"TC3{secret_key}".encode("utf-8"), date)
    secret_service = _sign(secret_date, service)
    secret_signing = _sign(secret_service, "tc3_request")
    signature = hmac.new(
        secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    authorization = (
        f"{algorithm} Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    headers = {
        "Authorization": authorization,
        "Content-Type": "application/json; charset=utf-8",
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": version,
        "X-TC-Region": region,
    }
    return headers, endpoint, payload_json


def call_tencent_cloud_api(
    *,
    action: str,
    payload: dict[str, Any],
    secret_id: str,
    secret_key: str,
    service: str = "hunyuan",
    version: str = "2023-09-01",
    region: str = "ap-guangzhou",
    timeout: int = 30,
) -> dict[str, Any]:
    headers, endpoint, payload_json = build_tencent_cloud_headers(
        action=action,
        version=version,
        region=region,
        service=service,
        payload=payload,
        secret_id=secret_id,
        secret_key=secret_key,
    )
    response = requests.post(endpoint, headers=headers, data=payload_json, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(
            f"Tencent API {action} failed: {response.status_code} {response.text}"
        )
    return response.json()


def extract_hunyuan_status(resp_json: dict[str, Any]) -> Optional[str]:
    response = resp_json.get("Response", {})
    status_fields = ("Status", "JobStatus", "StatusCode", "JobStatusCode", "State")

    for field in status_fields:
        value = response.get(field)
        if isinstance(value, str) and value:
            return value.upper()

    jobs = response.get("Jobs")
    if isinstance(jobs, list) and jobs:
        first = jobs[0]
        if isinstance(first, dict):
            for field in status_fields:
                value = first.get(field)
                if isinstance(value, str) and value:
                    return value.upper()

    job_info = response.get("JobStatusInfo")
    if isinstance(job_info, dict):
        for field in status_fields:
            value = job_info.get(field)
            if isinstance(value, str) and value:
                return value.upper()

    return None


def encode_local_or_url_image(image_path_or_url: str) -> tuple[str, str]:
    if re.match(r"^https?://", image_path_or_url, re.IGNORECASE):
        return "ImageUrl", image_path_or_url
    with open(image_path_or_url, "rb") as handle:
        return "ImageBase64", base64.b64encode(handle.read()).decode("ascii")


def probe_conditional_services(logger) -> dict[str, bool]:
    timeout_raw = os.getenv("MCP_TOOL_HEALTH_TIMEOUT_SECONDS", "2.0")
    try:
        timeout = float(timeout_raw)
    except ValueError:
        timeout = 2.0

    checker = ServiceHealthChecker(
        host_override=os.getenv("MCP_TOOL_HEALTH_HOST"),
        timeout=timeout,
    )
    service_names = ["trellis2", "retrieval", "pcg_integrator"]

    try:
        check_results = checker.check_services(service_names)
    except Exception as exc:
        logger.warning("Failed to probe conditional services: %s", exc)
        check_results = {}

    service_status: dict[str, bool] = {}
    service_details: dict[str, Any] = {}
    for name in service_names:
        result = check_results.get(name)
        if result is None:
            service_status[name] = False
            service_details[name] = {"ok": False, "error": "missing check result"}
            continue
        service_status[name] = result.ok
        service_details[name] = result.to_dict()

    logger.info("Conditional tool service health: %s", json.dumps(service_details))
    print(
        f"[mcp_server] conditional_service_health={json.dumps(service_details)}",
        flush=True,
    )
    return service_status
