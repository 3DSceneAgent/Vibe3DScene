from __future__ import annotations

import os

SHARED_TOOL_SERVICE_HOST_ENV = "TOOL_SERVICE_HOST"
DEFAULT_TOOL_SERVICE_HOST = "localhost"
DEFAULT_OBJAVERSE_PORT = 8002
DEFAULT_SCENESMITH_COMPAT_PORT = 8005
DEFAULT_SAM_PORT = 8004


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


def _read_first_env(*names: str) -> str | None:
    for name in names:
        value = _read_env(name)
        if value is not None:
            return value
    return None


def get_tool_service_host(
    host_env_key: str,
    *,
    default_host: str = DEFAULT_TOOL_SERVICE_HOST,
) -> str:
    return _read_env(host_env_key) or get_shared_tool_service_host() or default_host


def get_tool_service_host_from_candidates(
    *host_env_keys: str,
    default_host: str = DEFAULT_TOOL_SERVICE_HOST,
) -> str:
    return _read_first_env(*host_env_keys) or get_shared_tool_service_host() or default_host


def get_tool_service_port(
    port_env_key: str,
    *,
    default_port: int,
) -> int:
    return _read_int_env(port_env_key, default_port)


def get_tool_service_port_from_candidates(
    *port_env_keys: str,
    default_port: int,
) -> int:
    for name in port_env_keys:
        raw = _read_env(name)
        if raw is None:
            continue
        try:
            return int(raw)
        except ValueError:
            continue
    return default_port


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


def get_retrieval_provider() -> str:
    return (_read_env("ASSET_RETRIEVAL_BACKEND") or "disabled").strip().lower()


def is_scenesmith_retrieval_provider() -> bool:
    return get_retrieval_provider() == "scenesmith"


def get_trellis2_base_url() -> str:
    return get_tool_service_base_url(
        host_env_key="TRELLIS2_HOST",
        port_env_key="TRELLIS2_PORT",
        default_port=8001,
    )


def get_assetretrieval_base_url() -> str:
    return get_tool_service_base_url(
        host_env_key="OBJAVERSE_HOST",
        port_env_key="OBJAVERSE_PORT",
        default_port=DEFAULT_OBJAVERSE_PORT,
    )


def get_scenesmith_compat_base_url() -> str:
    return get_tool_service_base_url(
        host_env_key="SCENESMITH_COMPAT_HOST",
        port_env_key="SCENESMITH_COMPAT_PORT",
        default_port=DEFAULT_SCENESMITH_COMPAT_PORT,
    )


def get_retrieval_base_url() -> str:
    if is_scenesmith_retrieval_provider():
        return get_scenesmith_compat_base_url()
    return get_assetretrieval_base_url()


def get_infinigen_base_url() -> str:
    return get_tool_service_base_url(
        host_env_key="INFINIGEN_HOST",
        port_env_key="INFINIGEN_PORT",
        default_port=8003,
    )


def get_sam_http_base_url() -> str:
    return get_tool_service_base_url(
        host_env_key="SAM_HOST",
        port_env_key="SAM_PORT",
        default_port=DEFAULT_SAM_PORT,
    )
