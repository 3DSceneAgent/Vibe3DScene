from __future__ import annotations

import os

SHARED_TOOL_SERVICE_HOST_ENV = "TOOL_SERVICE_HOST"
DEFAULT_TOOL_SERVICE_HOST = "localhost"
DEFAULT_SAM_HTTP_BASE_URL = "http://127.0.0.1:8004"


def _read_env(name: str) -> str | None:
    if not name:
        return None
    raw = os.getenv(name)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _read_int_env(name: str, default: int) -> int:
    raw = _read_env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def get_shared_tool_service_host() -> str | None:
    return _read_env(SHARED_TOOL_SERVICE_HOST_ENV)


def get_tool_service_host(
    host_env_key: str,
    *,
    default_host: str = DEFAULT_TOOL_SERVICE_HOST,
) -> str:
    return _read_env(host_env_key) or get_shared_tool_service_host() or default_host


def get_tool_service_port(
    port_env_key: str,
    *,
    default_port: int,
) -> int:
    return _read_int_env(port_env_key, default_port)


def get_tool_service_base_url(
    *,
    host_env_key: str,
    port_env_key: str,
    default_port: int,
    default_host: str = DEFAULT_TOOL_SERVICE_HOST,
    scheme: str = "http",
) -> str:
    host = get_tool_service_host(host_env_key, default_host=default_host)
    port = get_tool_service_port(port_env_key, default_port=default_port)
    return f"{scheme}://{host}:{port}"


def get_trellis2_base_url() -> str:
    return get_tool_service_base_url(
        host_env_key="TRELLIS2_HOST",
        port_env_key="TRELLIS2_PORT",
        default_port=8001,
    )


def get_retrieval_base_url() -> str:
    return get_tool_service_base_url(
        host_env_key="RETRIEVAL_API_HOST",
        port_env_key="RETRIEVAL_API_PORT",
        default_port=8002,
    )


def get_infinigen_base_url() -> str:
    return get_tool_service_base_url(
        host_env_key="INFINIGEN_HOST",
        port_env_key="INFINIGEN_PORT",
        default_port=8003,
    )


def get_sam_http_base_url() -> str:
    configured_base_url = _read_env("SAM_HTTP_BASE_URL")
    if configured_base_url:
        return configured_base_url.rstrip("/")

    shared_host = get_shared_tool_service_host()
    if shared_host:
        return f"http://{shared_host}:8004"

    return DEFAULT_SAM_HTTP_BASE_URL
