# ruff: noqa: F401
"""
FastAPI REST server for the 3D scene agent.
Provides HTTP endpoints and streaming support.
"""
import asyncio
import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, Any
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from scene_agent.blender.connection import BlenderConnection

from scene_agent.agent.graph import create_agent_graph
from scene_agent.agent.redis_checkpointer import get_graph_checkpointer
from scene_agent.agent.state import AgentState
from scene_agent.blender.session_manager import (
    SessionResourceError,
    SessionResourceReason,
    allocate_headless_port_strict,
    build_headless_command_args,
    get_session_manager,
    start_headless_process,
)
from scene_agent.config import get_settings
from scene_agent.env import load_project_dotenv
from scene_agent.memory.reference_image_memory import (
    ImageAsset,
    get_image_asset_memory,
)
from scene_agent.session import get_session_coordinator
from scene_agent.session.owner_proxy import OwnerProxyError, forward_request_to_owner
from scene_agent.utils.diagnostics import (
    build_diagnostic_record,
    elapsed_ms,
    new_request_id,
    start_timer,
    within_target,
)
from scene_agent.utils.logging import log_event
from scene_agent.utils.rendering import RENDERS_DIR, process_and_save_render

# Create FastAPI app
app = FastAPI(
    title="3D Scene Agent API",
    description="LangGraph-based 3D scene manipulation agent with Blender integration",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Image storage configuration
# Mount static files for renders
app.mount("/renders", StaticFiles(directory=str(RENDERS_DIR)), name="renders")
_MODULE_PATH = Path(__file__).resolve()
_ROOT_EXAMPLE_PROMPTS_PATH = _MODULE_PATH.parents[3] / "assets" / "example_prompts.md"
_PACKAGE_EXAMPLE_PROMPTS_PATH = _MODULE_PATH.parents[2] / "assets" / "example_prompts.md"
EXAMPLE_PROMPTS_PATH = (
    _ROOT_EXAMPLE_PROMPTS_PATH
    if _ROOT_EXAMPLE_PROMPTS_PATH.exists()
    else _PACKAGE_EXAMPLE_PROMPTS_PATH
)

# Global agent instance
_agent_graph = None
_agent_graphs_by_thread: Dict[str, Any] = {}
_agent_graph_refresh_tasks: Dict[str, asyncio.Task] = {}
_agent_graph_refresh_lock = threading.Lock()
_idle_sweeper_task: asyncio.Task | None = None

# Blender addon connection (direct socket)
_blender_connection = None
_blender_lock = threading.Lock()
_thread_vlm_lock = threading.Lock()
_thread_vlm_configs: Dict[str, Dict[str, Any]] = {}
_thread_client_lock = threading.Lock()
_thread_frontend_clients: Dict[str, str] = {}
_FRONTEND_CLIENT_HEADER = "x-frontend-client-id"
_DEFAULT_FRONTEND_CLIENT_ID = "default"
_SUPPORTED_VLM_PROVIDERS = ("gemini", "openai", "anthropic", "qwen")
_VLM_PROVIDER_DISPLAY_NAMES = {
    "gemini": "Gemini",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "qwen": "Qwen",
}
_SCENE_LEVEL_RENDER_CAMERA_CONFIGS: tuple[tuple[str, float, float], ...] = (
    ("SceneCamera_NE", 45.0, 30.0),
    ("SceneCamera_SW", -135.0, 30.0),
    ("SceneCamera_TopDown", 0.0, 89.0),
)
_SCENE_LEVEL_RENDER_FOCAL_MM = 42.0
_DEFAULT_API_LOG_PATH = Path(__file__).resolve().parents[3] / "logs" / "api_server.log"
_API_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def _normalize_optional(value: str | None, *, lower: bool = False) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized.lower() if lower else normalized


def _normalize_frontend_client_id(raw: str | None) -> str:
    if raw is None:
        return _DEFAULT_FRONTEND_CLIENT_ID
    normalized = raw.strip()
    if not normalized:
        return _DEFAULT_FRONTEND_CLIENT_ID
    # Keep IDs deterministic and log-safe.
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", normalized)[:64].strip("-")
    return cleaned or _DEFAULT_FRONTEND_CLIENT_ID


def resolve_frontend_client_id(request: Request | None) -> str:
    if request is None:
        return _DEFAULT_FRONTEND_CLIENT_ID
    raw = request.headers.get(_FRONTEND_CLIENT_HEADER)
    return _normalize_frontend_client_id(raw)


def _bind_frontend_client_to_thread(thread_id: str, client_id: str) -> None:
    normalized = _normalize_frontend_client_id(client_id)
    with _thread_client_lock:
        _thread_frontend_clients[thread_id] = normalized
    coordinator = get_session_coordinator()
    coordinator.update_session_runtime_fields(
        thread_id,
        {"frontend_client_id": normalized},
    )


def _clear_frontend_client_binding(thread_id: str) -> None:
    with _thread_client_lock:
        _thread_frontend_clients.pop(thread_id, None)


def _bind_frontend_client_to_thread_if_unclaimed(thread_id: str, client_id: str) -> None:
    coordinator = get_session_coordinator()
    meta = coordinator.get_session_meta(thread_id) or {}
    resolved = _resolve_thread_frontend_client(thread_id, meta)
    normalized_request_client = _normalize_frontend_client_id(client_id)
    if resolved == normalized_request_client:
        return
    if (
        resolved == _DEFAULT_FRONTEND_CLIENT_ID
        and normalized_request_client != _DEFAULT_FRONTEND_CLIENT_ID
    ):
        _bind_frontend_client_to_thread(thread_id, normalized_request_client)


def _resolve_api_log_path() -> Path:
    configured = _normalize_optional(os.getenv("SCENE_AGENT_API_LOG_PATH"))
    if configured:
        return Path(configured).expanduser()
    return _DEFAULT_API_LOG_PATH


def _logger_has_file_handler(logger: logging.Logger, log_path: Path) -> bool:
    resolved = str(log_path.resolve())
    for handler in logger.handlers:
        if not isinstance(handler, logging.FileHandler):
            continue
        base_filename = getattr(handler, "baseFilename", None)
        if isinstance(base_filename, str) and os.path.abspath(base_filename) == resolved:
            return True
    return False


def _add_file_handler_if_missing(
    logger: logging.Logger,
    log_path: Path,
    *,
    formatter: logging.Formatter,
) -> None:
    if _logger_has_file_handler(logger, log_path):
        return
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


def _configure_api_file_logging() -> Path:
    log_path = _resolve_api_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(_API_LOG_FORMAT)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    _add_file_handler_if_missing(root_logger, log_path, formatter=formatter)

    # Uvicorn access/error loggers often run with custom handlers/propagation.
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        _add_file_handler_if_missing(logging.getLogger(logger_name), log_path, formatter=formatter)
    return log_path


def extract_graph_step_events(mode: str | None, payload: Any) -> list[dict[str, Any]]:
    if mode != "updates" or not isinstance(payload, dict):
        return []
    steps: list[dict[str, Any]] = []
    for raw_name, update in payload.items():
        if not isinstance(raw_name, str) or not raw_name:
            continue
        update_keys = sorted(update.keys()) if isinstance(update, dict) else []
        steps.append({"step": raw_name, "update_keys": update_keys})
    return steps


def _sanitize_graph_state_patch(update: dict[str, Any]) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    for key, value in update.items():
        if key == "messages":
            continue
        patch[key] = _sanitize_stream_value(value)
    return patch


def build_graph_node_event_payload(
    *,
    request_id: str,
    thread_id: str,
    node_name: str,
    step_index: int,
    update: dict[str, Any],
) -> dict[str, Any]:
    event_payload: dict[str, Any] = {
        "event": "graph_node",
        "graph_node": {
            "request_id": request_id,
            "thread_id": thread_id,
            "node": node_name,
            "step_index": step_index,
            "update_keys": sorted(update.keys()),
            "state_patch": _sanitize_graph_state_patch(update),
        },
    }
    messages = update.get("messages")
    if isinstance(messages, list):
        event_payload["graph_node"]["message_count"] = len(messages)
    return event_payload


def headless_timeout_seconds_for_session(settings: Any, session: Any | None = None) -> float:
    # Scene/render export operations are frequently heavier than tool RPCs.
    base_timeout = max(1.0, float(getattr(settings, "headless_request_timeout_seconds", 15)))
    if session is None:
        return base_timeout
    session_status = getattr(session, "status", None)
    session_process = getattr(session, "process", None)
    # Cold-start requests need extra budget for launching Blender/MCP.
    if session_status != "ready" or session_process is None:
        if base_timeout < 10:
            return base_timeout
        startup_timeout = max(1.0, float(getattr(settings, "blender_headless_startup_timeout", 10)))
        return base_timeout + startup_timeout
    return base_timeout


def _worker_capacity() -> int:
    coordinator = get_session_coordinator()
    raw_count = coordinator.worker_count()
    if isinstance(raw_count, int) and raw_count > 0:
        return raw_count
    return 1


def _active_headless_session_count(host: str) -> int:
    coordinator = get_session_coordinator()
    reserved = coordinator.reserved_port_count(host=host, kind="headless")
    if isinstance(reserved, int) and reserved >= 0:
        return reserved
    manager = get_session_manager()
    return sum(
        1
        for session in manager.list_sessions()
        if session.port is not None and (session.host == host or session.host is None)
    )


def _build_capacity_error(
    *,
    reason: SessionResourceReason,
    message: str,
    headless_range: int,
    host: str | None = None,
    active_sessions: int | None = None,
) -> SessionResourceError:
    worker_capacity = _worker_capacity()
    limits: dict[str, Any] = {
        "worker_capacity": worker_capacity,
        "headless_port_capacity": max(1, int(headless_range)),
    }
    in_use: dict[str, Any] = {}
    if active_sessions is None and host:
        active_sessions = _active_headless_session_count(host)
    if active_sessions is not None:
        in_use["active_headless_sessions"] = int(active_sessions)
    return SessionResourceError(
        error=message,
        reason=reason,
        limits=limits,
        in_use=in_use,
    )


def _should_reset_redis_runtime_on_start() -> bool:
    raw = os.getenv("SCENE_AGENT_RESET_REDIS_RUNTIME_ON_START", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def restart_headless_session_after_timeout(thread_id: str) -> None:
    settings = get_settings()
    manager = get_session_manager()
    session = manager.get(thread_id)
    headless_host = (session.host if session is not None else None) or settings.blender_host
    headless_port = session.port if session is not None else None
    mcp_host = (session.mcp_host if session is not None else None) or os.getenv("BLENDER_MCP_HOST", "localhost")
    mcp_port = session.mcp_port if session is not None else None
    coordinator = get_session_coordinator()
    try:
        manager.restart_session_processes(thread_id, timeout=3.0)
        coordinator.release_port(host=headless_host, kind="headless", port=headless_port)
        coordinator.release_port(host=mcp_host, kind="mcp", port=mcp_port)
        coordinator.update_session_runtime_fields(
            thread_id,
            {
                "status": "closed",
                "host": "",
                "mcp_host": "",
                "blender_port": "",
                "mcp_port": "",
            },
        )
        log_event(
            "warning",
            "headless_session_restarted_after_timeout",
            {
                "thread_id": thread_id,
                "released_headless_port": headless_port,
                "released_mcp_port": mcp_port,
            },
        )
    except Exception as exc:
        log_event(
            "warning",
            "headless_session_restart_failed",
            {"thread_id": thread_id, "error": str(exc)},
        )


def build_vlm_provider_catalog() -> list[Dict[str, Any]]:
    settings = get_settings()
    providers: list[Dict[str, Any]] = []
    for provider in _SUPPORTED_VLM_PROVIDERS:
        models = settings.get_vlm_provider_models(provider)
        default_model = settings.get_vlm_default_model(provider)
        if default_model not in models:
            models = [default_model, *models]
        providers.append(
            {
                "provider": provider,
                "display_name": _VLM_PROVIDER_DISPLAY_NAMES.get(provider, provider.title()),
                "default_model": default_model,
                "models": models,
                "configured": bool(settings.get_vlm_api_key(provider)),
            }
        )
    return providers


def _resolve_vlm_selection(
    *,
    provider: str | None = None,
    model: str | None = None,
) -> Dict[str, str]:
    settings = get_settings()
    selected_provider = _normalize_optional(provider, lower=True) or settings.vlm_provider
    if selected_provider not in _SUPPORTED_VLM_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported VLM provider '{selected_provider}'. "
                f"Supported providers: {', '.join(_SUPPORTED_VLM_PROVIDERS)}."
            ),
        )

    available_models = settings.get_vlm_provider_models(selected_provider)
    default_model = settings.get_vlm_default_model(selected_provider)
    if default_model not in available_models:
        available_models = [default_model, *available_models]

    selected_model = _normalize_optional(model) or default_model
    if selected_model not in available_models:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported model '{selected_model}' for provider '{selected_provider}'. "
                f"Available models: {', '.join(available_models)}."
            ),
        )

    selected_api_key = settings.get_vlm_api_key(selected_provider)
    if not selected_api_key:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No API key configured for provider '{selected_provider}'. "
                f"Set {selected_provider.upper()}_API_KEY. "
                "VLM_API_KEY is no longer used."
            ),
        )

    return {
        "provider": selected_provider,
        "model": selected_model,
        "api_key": selected_api_key,
    }


def ensure_thread_vlm_config(thread_id: str) -> Dict[str, Any]:
    coordinator = get_session_coordinator()
    stored = coordinator.get_thread_vlm(thread_id)
    if stored is not None:
        locked_raw = stored.get("locked", "0")
        locked = locked_raw in {"1", "true", "True"}
        with _thread_vlm_lock:
            _thread_vlm_configs[thread_id] = {
                "provider": stored["provider"],
                "model": stored["model"],
                "locked": locked,
            }
        return {
            "provider": str(stored["provider"]),
            "model": str(stored["model"]),
            "locked": locked,
        }

    with _thread_vlm_lock:
        existing = _thread_vlm_configs.get(thread_id)
    if existing is None:
        resolved = _resolve_vlm_selection()
        with _thread_vlm_lock:
            current = _thread_vlm_configs.get(thread_id)
            if current is None:
                current = {
                    "provider": resolved["provider"],
                    "model": resolved["model"],
                }
                _thread_vlm_configs[thread_id] = current
                coordinator.set_thread_vlm(
                    thread_id=thread_id,
                    provider=resolved["provider"],
                    model=resolved["model"],
                    locked=bool(current.get("locked", False)),
                )
            existing = current
    return {
        "provider": str(existing["provider"]),
        "model": str(existing["model"]),
        "locked": bool(existing.get("locked", False)),
    }


def resolve_thread_vlm_for_chat(
    thread_id: str,
    requested_provider: str | None,
    requested_model: str | None,
) -> Dict[str, str]:
    coordinator = get_session_coordinator()
    normalized_provider = _normalize_optional(requested_provider, lower=True)
    normalized_model = _normalize_optional(requested_model)
    state = ensure_thread_vlm_config(thread_id)
    current_provider = state["provider"]
    current_model = state["model"]

    target_provider = normalized_provider or current_provider
    if normalized_model:
        target_model = normalized_model
    elif normalized_provider and normalized_provider != current_provider:
        target_model = None
    else:
        target_model = current_model

    resolved = _resolve_vlm_selection(provider=target_provider, model=target_model)
    with _thread_vlm_lock:
        current = _thread_vlm_configs.get(thread_id)
        if current is None:
            current = {
                "provider": resolved["provider"],
                "model": resolved["model"],
            }
            _thread_vlm_configs[thread_id] = current
        else:
            current["provider"] = resolved["provider"]
            current["model"] = resolved["model"]
    coordinator.set_thread_vlm(
        thread_id=thread_id,
        provider=resolved["provider"],
        model=resolved["model"],
        locked=bool(current.get("locked", False)),
    )
    return resolved


def _resolve_thread_vlm_for_agent(thread_id: str) -> Dict[str, str]:
    state = ensure_thread_vlm_config(thread_id)
    return _resolve_vlm_selection(provider=state["provider"], model=state["model"])


_MIGRATABLE_AGENT_STATE_KEYS = frozenset(getattr(AgentState, "__annotations__", {}).keys())


def _sanitize_migrated_agent_state(values: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(values, dict):
        return {}
    if _MIGRATABLE_AGENT_STATE_KEYS:
        sanitized = {
            key: value
            for key, value in values.items()
            if key in _MIGRATABLE_AGENT_STATE_KEYS
        }
    else:
        sanitized = dict(values)
    if "attached_image_ids" in sanitized and not isinstance(sanitized["attached_image_ids"], list):
        sanitized.pop("attached_image_ids", None)
    if "request_reference_image_keys" in sanitized and not isinstance(
        sanitized["request_reference_image_keys"],
        list,
    ):
        sanitized["request_reference_image_keys"] = []
    if "reference_image_catalog" in sanitized and not isinstance(
        sanitized["reference_image_catalog"],
        dict,
    ):
        sanitized["reference_image_catalog"] = {}
    return sanitized


async def _migrate_agent_state_if_possible(
    *,
    thread_id: str,
    from_graph: Any,
    to_graph: Any,
) -> None:
    if from_graph is to_graph:
        return
    config = {"configurable": {"thread_id": thread_id}}
    try:
        snapshot = await from_graph.aget_state(config)
    except Exception as exc:
        log_event(
            "warning",
            "vlm_switch_state_snapshot_failed",
            {"thread_id": thread_id, "error": str(exc)},
        )
        return
    values = getattr(snapshot, "values", None)
    if not isinstance(values, dict) or len(values) == 0:
        return
    sanitized_values = _sanitize_migrated_agent_state(values)
    if not sanitized_values:
        return
    try:
        await to_graph.aupdate_state(config, sanitized_values)
    except Exception as exc:
        log_event(
            "warning",
            "vlm_switch_state_migration_failed",
            {"thread_id": thread_id, "error": str(exc)},
        )


async def _create_agent_graph_for_runtime(
    *,
    session_id: str | None,
    provider: str,
    model: str,
    api_key: str,
):
    try:
        return await create_agent_graph(
            session_id=session_id,
            provider_name=provider,
            model=model,
            api_key=api_key,
        )
    except TypeError:
        # Backward compatibility for monkeypatched test doubles that accept only session_id.
        return await create_agent_graph(session_id=session_id)


def get_blender_connection() -> BlenderConnection:
    global _blender_connection
    if _blender_connection is not None:
        return _blender_connection
    host = os.getenv("BLENDER_HOST", "localhost")
    port = int(os.getenv("BLENDER_PORT", "9876"))
    _blender_connection = BlenderConnection(host=host, port=port)
    if not _blender_connection.connect():
        _blender_connection = None
        raise Exception("Could not connect to Blender addon.")
    return _blender_connection


def get_blender_connection_for_thread(thread_id: str) -> BlenderConnection:
    settings = get_settings()
    if settings.blender_mode == "local-client":
        return get_blender_connection()

    coordinator = get_session_coordinator()
    manager = get_session_manager()
    session = manager.ensure(thread_id, "headless")
    manager.ensure_session_storage(thread_id)
    host = os.getenv("BLENDER_HEADLESS_HOST", settings.blender_host)
    base_port = int(os.getenv("BLENDER_HEADLESS_BASE_PORT", "9876"))
    port_range = max(1, int(os.getenv("BLENDER_HEADLESS_PORT_RANGE", "16")))
    newly_allocated_port = False
    if session.port is None:
        worker_capacity = _worker_capacity()
        active_sessions = _active_headless_session_count(host)
        if active_sessions >= worker_capacity:
            raise _build_capacity_error(
                reason="process_capacity_exhausted",
                message=(
                    "Cannot create new headless session: process capacity is exhausted. "
                    f"Active sessions={active_sessions}, worker capacity={worker_capacity}."
                ),
                headless_range=port_range,
                host=host,
                active_sessions=active_sessions,
            )

        reservation = coordinator.reserve_port_detailed(
            host=host,
            kind="headless",
            base_port=base_port,
            range_size=port_range,
            seed=thread_id,
        )
        if reservation.status == "reserved" and reservation.port is not None:
            port = reservation.port
        elif reservation.status == "exhausted":
            raise _build_capacity_error(
                reason="blender_port_exhausted",
                message="Cannot create new headless session: no available Blender port in configured range.",
                headless_range=port_range,
                host=host,
                active_sessions=active_sessions,
            )
        else:
            used_ports = {
                int(item.port)
                for item in manager.list_sessions()
                if item.port is not None and (item.host == host or item.host is None)
            }
            blocked_ports = {
                int(item.mcp_port)
                for item in manager.list_sessions()
                if item.mcp_port is not None and (item.mcp_host == host or item.mcp_host is None)
            }
            try:
                port = allocate_headless_port_strict(
                    thread_id,
                    base_port,
                    port_range,
                    used_ports=used_ports,
                    blocked_ports=blocked_ports,
                )
            except RuntimeError as exc:
                raise _build_capacity_error(
                    reason="blender_port_exhausted",
                    message="Cannot create new headless session: no available Blender port in configured range.",
                    headless_range=port_range,
                    host=host,
                    active_sessions=active_sessions,
                ) from exc
        manager.set_endpoint(thread_id, host, port)
        newly_allocated_port = True
    else:
        port = session.port

    try:
        command, args = build_headless_command_args(
            thread_id,
            host,
            port,
            blend_path=session.blend_path,
        )
        headless_env = os.environ.copy()
        headless_env.update(
            {
                "SESSION_ID": thread_id,
                "SESSION_STORAGE_DIR": session.storage_dir or "",
                "SESSION_BLEND_PATH": session.blend_path or "",
                "SESSION_SNAPSHOT_DIR": session.snapshot_dir or "",
                "SESSION_MAX_SNAPSHOTS": str(session.max_snapshots),
                "SESSION_IDLE_TIMEOUT_SECONDS": str(session.idle_timeout_seconds),
                "SESSION_BLEND_ROOT": settings.session_blend_root,
                "SESSION_PERSISTENCE_ENABLED": "1",
            }
        )
        log_event(
            "debug",
            "headless_command_prepared",
            {"thread_id": thread_id, "command": command, "args": args},
        )

        with session.lock:
            log_event("debug", "headless_session_lock_acquired", {"thread_id": thread_id})
            connection = session.connection
            if not isinstance(connection, BlenderConnection):
                log_event("info", "headless_connection_creating", {"thread_id": thread_id})
                connection = BlenderConnection(host=host, port=port)
                session.connection = connection
            if not connection.connect():
                log_event(
                    "info",
                    "headless_process_starting",
                    {"thread_id": thread_id, "host": host, "port": port},
                )
                start_headless_process(session, command, args, env=headless_env)

                deadline = time.time() + settings.blender_headless_startup_timeout
                last_check = time.time()
                connected = False

                while time.time() < deadline:
                    if time.time() - last_check > 2.0:
                        if session.process:
                            if session.process.poll() is not None:
                                error_msg = f"Blender process exited with code {session.process.returncode}"
                                if session.log_path and os.path.exists(session.log_path):
                                    with open(session.log_path, "r") as f:
                                        log_content = f.read()
                                    error_msg += f"\n\nProcess Log:\n{log_content[-2000:]}"
                                manager.set_error(thread_id, error_msg)
                                raise Exception(error_msg)
                            log_event(
                                "debug",
                                "headless_process_waiting_for_connection",
                                {"thread_id": thread_id, "pid": session.process.pid},
                            )
                        last_check = time.time()

                    if connection.connect():
                        log_event(
                            "info",
                            "headless_connection_established",
                            {"thread_id": thread_id, "host": host, "port": port},
                        )
                        connected = True
                        break
                    time.sleep(0.5)

                if not connected:
                    error_msg = f"Connection timeout after {settings.blender_headless_startup_timeout}s"
                    if session.log_path and os.path.exists(session.log_path):
                        with open(session.log_path, "r") as f:
                            log_content = f.read()
                        error_msg += f"\n\nProcess Log:\n{log_content[-2000:]}"
                    manager.set_error(thread_id, error_msg)
                    raise Exception(error_msg)
            else:
                log_event("debug", "headless_connection_reused", {"thread_id": thread_id})

            if not connection.sock:
                error_message = "Could not connect to headless Blender session."
                manager.set_error(thread_id, error_message)
                raise Exception(error_message)

            manager.set_ready(thread_id, connection)
            coordinator.touch_activity(thread_id)
            coordinator.update_session_runtime_fields(
                thread_id,
                {
                    "host": host,
                    "blender_port": port,
                    "storage_dir": session.storage_dir,
                    "blend_path": session.blend_path,
                    "snapshot_dir": session.snapshot_dir,
                    "status": "ready",
                },
            )
            log_event("info", "headless_session_ready", {"thread_id": thread_id})

        return connection
    except SessionResourceError:
        if newly_allocated_port:
            manager.terminate_session_processes(thread_id, timeout=3.0)
            coordinator.release_port(host=host, kind="headless", port=port)
        raise
    except Exception:
        if newly_allocated_port:
            manager.terminate_session_processes(thread_id, timeout=3.0)
            coordinator.release_port(host=host, kind="headless", port=port)
        raise


def send_blender_command_sync(
    command_type: str,
    params: Dict[str, Any] | None = None,
    thread_id: str | None = None
) -> Dict[str, Any]:
    global _blender_connection
    settings = get_settings()
    if settings.blender_mode == "headless" and thread_id:
        coordinator = get_session_coordinator()
        manager = get_session_manager()
        session = manager.ensure(thread_id, "headless")
        last_error: Exception | None = None
        for _attempt in range(2):
            blender = get_blender_connection_for_thread(thread_id)
            with session.lock:
                try:
                    result = blender.send_command(command_type, params)
                    coordinator.touch_activity(thread_id)
                    return result
                except Exception as exc:
                    last_error = exc
                    try:
                        blender.disconnect()
                    except Exception:
                        pass
                    session.connection = None
                    manager.set_error(thread_id, f"{command_type} failed: {exc}")
                    coordinator.update_session_runtime_fields(
                        thread_id,
                        {"status": "error"},
                    )
        if last_error is not None:
            raise last_error
        raise Exception(f"{command_type} failed unexpectedly")

    with _blender_lock:
        try:
            blender = get_blender_connection()
            return blender.send_command(command_type, params)
        except Exception:
            if _blender_connection:
                _blender_connection.disconnect()
            _blender_connection = None
            blender = get_blender_connection()
            return blender.send_command(command_type, params)


def serialize_message(message: Any) -> Dict[str, Any]:
    if isinstance(message, tuple) and len(message) == 2:
        message = message[0]
    if isinstance(message, dict):
        return message
    if hasattr(message, "type") or hasattr(message, "content"):
        return {
            "type": getattr(message, "type", None),
            "content": getattr(message, "content", None),
            "content_blocks": getattr(message, "content_blocks", None),
            "text": getattr(message, "text", None),
            "additional_kwargs": getattr(message, "additional_kwargs", None),
            "response_metadata": getattr(message, "response_metadata", None),
            "tool_calls": getattr(message, "tool_calls", None),
            "tool_call_chunks": getattr(message, "tool_call_chunks", None),
            "invalid_tool_calls": getattr(message, "invalid_tool_calls", None),
            "chunk_position": getattr(message, "chunk_position", None),
            "usage_metadata": getattr(message, "usage_metadata", None),
            "name": getattr(message, "name", None),
            "id": getattr(message, "id", None),
        }
    return {"type": "unknown", "content": str(message)}


def serialize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(event, dict):
        return {"event": event}
    payload: Dict[str, Any] = {}
    for key, value in event.items():
        if key == "messages" and isinstance(value, list):
            payload[key] = [serialize_message(msg) for msg in value]
        else:
            payload[key] = value
    return payload


def _looks_like_base64(value: str) -> bool:
    if len(value) < 256:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9+/=\s]+", value))


def _sanitize_stream_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "[truncated]"
    if isinstance(value, str):
        if value.startswith("data:image/"):
            return "[image data omitted]"
        if len(value) > 12_000 and ("base64" in value.lower() or _looks_like_base64(value)):
            return "[large payload omitted]"
        if len(value) > 24_000:
            return f"{value[:24_000]}...[truncated]"
        return value
    if isinstance(value, list):
        return [_sanitize_stream_value(item, depth=depth + 1) for item in value[:64]]
    if isinstance(value, dict):
        sanitized: dict[Any, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= 128:
                sanitized["__truncated__"] = True
                break
            key_str = str(key).lower()
            if key_str in {"base64", "image_base64"}:
                sanitized[key] = "[image data omitted]"
                continue
            sanitized[key] = _sanitize_stream_value(item, depth=depth + 1)
        return sanitized
    return value


def sanitize_message_for_stream(serialized: Dict[str, Any]) -> Dict[str, Any]:
    sanitized = dict(serialized)
    sanitized["content"] = _sanitize_stream_value(serialized.get("content"))
    if "content_blocks" in serialized:
        sanitized["content_blocks"] = _sanitize_stream_value(serialized.get("content_blocks"))
    if "text" in serialized:
        sanitized["text"] = _sanitize_stream_value(serialized.get("text"))
    return sanitized


_TOOL_LIKE_CONTENT_TYPES = frozenset(
    {
        "tool_use",
        "tool_call",
        "tool_call_chunk",
        "server_tool_call",
        "server_tool_result",
        "tool_result",
        "function_call",
        "function",
    }
)
_REASONING_LIKE_CONTENT_TYPES = frozenset(
    {
        "reasoning",
        "thinking",
        "reasoning_content",
        "summary_text",
    }
)
_IMAGE_LIKE_CONTENT_TYPES = frozenset(
    {
        "image",
        "image_url",
        "input_image",
        "output_image",
        "media",
        "file",
        "document",
    }
)


def _content_block_type(item: Dict[str, Any]) -> str:
    raw_type = item.get("type")
    if isinstance(raw_type, str):
        return raw_type.strip().lower()
    return ""


def _looks_like_image_block(item: Dict[str, Any]) -> bool:
    if "image_url" in item and isinstance(item["image_url"], dict):
        return True
    return _content_block_type(item) in _IMAGE_LIKE_CONTENT_TYPES


def _content_block_has_thought_signature(item: Dict[str, Any]) -> bool:
    if item.get("thought") is True:
        return True
    for key in ("thought_signature", "signature"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return True
    extras = item.get("extras")
    if isinstance(extras, dict):
        signature = extras.get("signature")
        if isinstance(signature, str) and signature:
            return True
    return False


def _extract_text_from_content_item(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return str(item)

    if _looks_like_image_block(item):
        image_url = item.get("image_url")
        if isinstance(image_url, dict):
            url = image_url.get("url", "")
            if isinstance(url, str) and url and not url.startswith("data:"):
                return f"![image]({url})"
        direct_url = item.get("url")
        if isinstance(direct_url, str) and direct_url and not direct_url.startswith("data:"):
            return f"![image]({direct_url})"
        return ""

    item_type = _content_block_type(item)
    if item_type in _TOOL_LIKE_CONTENT_TYPES:
        return ""
    if item_type in _REASONING_LIKE_CONTENT_TYPES:
        return ""
    if _content_block_has_thought_signature(item):
        return ""

    if item_type == "non_standard":
        nested_value = item.get("value")
        if isinstance(nested_value, dict):
            nested_type = _content_block_type(nested_value)
            if nested_type in _TOOL_LIKE_CONTENT_TYPES:
                return ""
            if nested_type in _REASONING_LIKE_CONTENT_TYPES:
                return ""
            nested_text = _extract_text_from_content_item(nested_value)
            if nested_text is not None:
                return nested_text

    text = item.get("text")
    if isinstance(text, str):
        return text

    content = item.get("content")
    if isinstance(content, str):
        return content

    nested_value = item.get("value")
    if isinstance(nested_value, (str, list, dict)):
        nested_text = message_content_to_text(nested_value)
        if nested_text:
            return nested_text

    return None


def message_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            extracted = _extract_text_from_content_item(item)
            if extracted is None:
                if isinstance(item, dict):
                    if "base64" not in str(item):
                        parts.append(json.dumps(item, ensure_ascii=False, default=str))
                    continue
                parts.append(str(item))
                continue
            if extracted:
                parts.append(extracted)
        return "".join(parts)
    if isinstance(content, dict):
        extracted = _extract_text_from_content_item(content)
        if extracted is not None:
            return extracted
        return json.dumps(_sanitize_stream_value(content), ensure_ascii=False, default=str)
    return str(content)


def _extract_tool_call_name(tool_call: Any) -> str | None:
    if isinstance(tool_call, dict):
        raw_name = tool_call.get("name")
        if isinstance(raw_name, str) and raw_name.strip():
            return raw_name.strip()
        function_value = tool_call.get("function")
        if isinstance(function_value, dict):
            function_name = function_value.get("name")
            if isinstance(function_name, str) and function_name.strip():
                return function_name.strip()
        return None
    raw_name = getattr(tool_call, "name", None)
    if isinstance(raw_name, str) and raw_name.strip():
        return raw_name.strip()
    return None


def _extract_reasoning_from_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            reasoning_text = _extract_reasoning_from_value(item)
            if reasoning_text:
                parts.append(reasoning_text)
        return "".join(parts)
    if not isinstance(value, dict):
        return ""

    item_type = _content_block_type(value)
    if item_type in _REASONING_LIKE_CONTENT_TYPES or _content_block_has_thought_signature(value):
        if item_type == "reasoning":
            reasoning = value.get("reasoning")
            if isinstance(reasoning, str):
                return reasoning
            summary = value.get("summary")
            summary_text = _extract_reasoning_from_value(summary)
            if summary_text:
                return summary_text
        thinking = value.get("thinking")
        if isinstance(thinking, str):
            return thinking
        text = value.get("text")
        if isinstance(text, str):
            return text
        content = value.get("content")
        if isinstance(content, str):
            return content
        value_field = value.get("value")
        return _extract_reasoning_from_value(value_field)

    nested_value = value.get("value")
    if item_type == "non_standard" and nested_value is not None:
        return _extract_reasoning_from_value(nested_value)

    if isinstance(nested_value, (dict, list, str)):
        nested_text = _extract_reasoning_from_value(nested_value)
        if nested_text:
            return nested_text

    summary = value.get("summary")
    if isinstance(summary, list):
        summary_text = _extract_reasoning_from_value(summary)
        if summary_text:
            return summary_text

    return ""


def extract_message_reasoning_text(serialized: Dict[str, Any]) -> str:
    additional_kwargs = serialized.get("additional_kwargs")
    if isinstance(additional_kwargs, dict):
        reasoning_content = additional_kwargs.get("reasoning_content")
        if isinstance(reasoning_content, str) and reasoning_content != "":
            return reasoning_content
        reasoning = additional_kwargs.get("reasoning")
        reasoning_text = _extract_reasoning_from_value(reasoning)
        if reasoning_text:
            return reasoning_text

    top_level_reasoning = serialized.get("reasoning_content")
    if isinstance(top_level_reasoning, str) and top_level_reasoning != "":
        return top_level_reasoning

    for key in ("content", "content_blocks"):
        reasoning_text = _extract_reasoning_from_value(serialized.get(key))
        if reasoning_text:
            return reasoning_text
    return ""


def extract_message_tool_call_names(serialized: Dict[str, Any]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    def add_name(raw_name: str | None) -> None:
        if not isinstance(raw_name, str):
            return
        name = raw_name.strip()
        if not name or name in seen:
            return
        seen.add(name)
        names.append(name)

    def add_tool_calls(raw_calls: Any) -> None:
        if not isinstance(raw_calls, list):
            return
        for tool_call in raw_calls:
            add_name(_extract_tool_call_name(tool_call))

    add_tool_calls(serialized.get("tool_calls"))
    add_tool_calls(serialized.get("tool_call_chunks"))

    additional_kwargs = serialized.get("additional_kwargs")
    if isinstance(additional_kwargs, dict):
        add_tool_calls(additional_kwargs.get("tool_calls"))

    def scan_content_blocks(raw_content: Any) -> None:
        if isinstance(raw_content, list):
            for item in raw_content:
                scan_content_blocks(item)
            return
        if not isinstance(raw_content, dict):
            return

        item_type = _content_block_type(raw_content)
        if item_type in _TOOL_LIKE_CONTENT_TYPES:
            add_name(_extract_tool_call_name(raw_content))
            return

        nested_value = raw_content.get("value")
        if isinstance(nested_value, dict):
            nested_type = _content_block_type(nested_value)
            if nested_type in _TOOL_LIKE_CONTENT_TYPES:
                add_name(_extract_tool_call_name(nested_value))
                return

    scan_content_blocks(serialized.get("content"))
    scan_content_blocks(serialized.get("content_blocks"))
    return names


def assistant_message_display_text(
    serialized: Dict[str, Any],
) -> str:
    raw_text = serialized.get("text")
    if isinstance(raw_text, str) and raw_text != "":
        return raw_text

    for key in ("content", "content_blocks"):
        text = message_content_to_text(serialized.get(key))
        if text != "":
            return text
    return ""


def normalize_stream_event(event: Any) -> tuple[str | None, Any]:
    if isinstance(event, tuple) and len(event) == 2:
        return event[0], event[1]
    return None, event


def message_has_tool_calls(serialized: Dict[str, Any]) -> bool:
    additional_kwargs = serialized.get("additional_kwargs")
    if isinstance(additional_kwargs, dict):
        tool_calls = additional_kwargs.get("tool_calls")
        if isinstance(tool_calls, list) and len(tool_calls) > 0:
            return True
    tool_calls = serialized.get("tool_calls")
    if isinstance(tool_calls, list) and len(tool_calls) > 0:
        return True
    tool_call_chunks = serialized.get("tool_call_chunks")
    if isinstance(tool_call_chunks, list) and len(tool_call_chunks) > 0:
        return True
    return len(extract_message_tool_call_names(serialized)) > 0


def message_is_tool(serialized: Dict[str, Any]) -> bool:
    return serialized.get("type") == "tool"


def _thread_graph_matches_runtime(graph: Any, vlm_runtime: dict[str, Any]) -> bool:
    if graph is None:
        return False
    graph_provider = getattr(graph, "_vlm_provider", None)
    graph_model = getattr(graph, "_vlm_model", None)
    has_vlm_metadata = graph_provider is not None and graph_model is not None
    if not has_vlm_metadata:
        return True
    return (
        graph_provider == vlm_runtime["provider"]
        and graph_model == vlm_runtime["model"]
    )


async def _refresh_thread_agent_graph(
    *,
    thread_id: str,
    vlm_runtime: dict[str, Any],
) -> Any:
    previous_graph = _agent_graphs_by_thread.get(thread_id)
    next_graph = await _create_agent_graph_for_runtime(
        session_id=thread_id,
        provider=vlm_runtime["provider"],
        model=vlm_runtime["model"],
        api_key=vlm_runtime["api_key"],
    )
    if previous_graph is not None:
        await _migrate_agent_state_if_possible(
            thread_id=thread_id,
            from_graph=previous_graph,
            to_graph=next_graph,
        )
    _agent_graphs_by_thread[thread_id] = next_graph
    return next_graph


async def _ensure_thread_agent_graph(
    *,
    thread_id: str,
    vlm_runtime: dict[str, Any],
) -> Any:
    created_task = False
    with _agent_graph_refresh_lock:
        task = _agent_graph_refresh_tasks.get(thread_id)
        if task is None or task.done():
            task = asyncio.create_task(
                _refresh_thread_agent_graph(
                    thread_id=thread_id,
                    vlm_runtime=vlm_runtime,
                )
            )
            _agent_graph_refresh_tasks[thread_id] = task
            created_task = True
    try:
        return await task
    finally:
        if created_task:
            with _agent_graph_refresh_lock:
                if _agent_graph_refresh_tasks.get(thread_id) is task:
                    _agent_graph_refresh_tasks.pop(thread_id, None)


async def get_agent(thread_id: str | None = None):
    """Get or create the agent graph (singleton or per-thread in headless mode)."""
    global _agent_graph, _agent_graphs_by_thread
    settings = get_settings()
    if thread_id:
        vlm_runtime = _resolve_thread_vlm_for_agent(thread_id)
        restart_needed = False
        if settings.blender_mode == "headless":
            manager = get_session_manager()
            session = manager.ensure(thread_id, "headless")
            manager.ensure_session_storage(thread_id)
            if session.process is None or session.mcp_process is None:
                restart_needed = True
            else:
                if session.process.poll() is not None or session.mcp_process.poll() is not None:
                    restart_needed = True

        graph = _agent_graphs_by_thread.get(thread_id)
        if not _thread_graph_matches_runtime(graph, vlm_runtime):
            graph = await _ensure_thread_agent_graph(
                thread_id=thread_id,
                vlm_runtime=vlm_runtime,
            )
            if not _thread_graph_matches_runtime(graph, vlm_runtime):
                graph = await _ensure_thread_agent_graph(
                    thread_id=thread_id,
                    vlm_runtime=vlm_runtime,
                )
        elif restart_needed:
            # Keep existing graph memory, only bring headless runtime back.
            from scene_agent.tools.blender_tools import get_blender_tools as _ensure_tools

            await _ensure_tools(session_id=thread_id)
        return _agent_graphs_by_thread[thread_id]

    if _agent_graph is None:
        runtime = _resolve_vlm_selection()
        _agent_graph = await _create_agent_graph_for_runtime(
            session_id=None,
            provider=runtime["provider"],
            model=runtime["model"],
            api_key=runtime["api_key"],
        )
    return _agent_graph


# Request/Response models
class ChatRequest(BaseModel):
    message: str
    thread_id: str = "default"
    vlm_provider: str | None = None
    vlm_model: str | None = None
    enabled_mcp_tools: list[str] | None = None
    attached_image_ids: list[str] | None = None
    task_id: str | None = None
    workflow_topology: str | None = None
    memory_profile: str | None = None
    fast_mode: bool | None = None


class ChatResponse(BaseModel):
    response: str
    thread_id: str
    todos: list[Dict[str, Any]] = []


class ImageAssetResponse(BaseModel):
    id: str
    thread_id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    uploaded_at: str
    source: str


class ImageAssetListResponse(BaseModel):
    thread_id: str
    images: list[ImageAssetResponse]


class ExamplePromptsResponse(BaseModel):
    prompts: list[str]


class MCPToolsResponse(BaseModel):
    thread_id: str
    loaded: bool
    tool_count: int
    tools: list[str]
    tool_hints: dict[str, str] = Field(default_factory=dict)
    blender_mode: str


class HeadlessRuntimeThreadEntry(BaseModel):
    thread_id: str
    frontend_client_id: str
    status: str
    last_active_ms: int
    occupying_resources: bool
    blender_port: int | None = None
    mcp_port: int | None = None


class HeadlessSessionCapacityResponse(BaseModel):
    blender_mode: str
    frontend_client_id: str
    quota: int
    in_use: int
    occupying_threads: list[HeadlessRuntimeThreadEntry]


class HeadlessSessionDebugEntry(BaseModel):
    thread_id: str
    frontend_client_id: str
    status: str
    local_status: str | None = None
    meta_status: str | None = None
    owner_worker_id: str | None = None
    lease_ttl_ms: int | None = None
    last_active_ms: int
    local_last_active_ms: int | None = None
    meta_last_active_ms: int | None = None
    idle_timeout_seconds: int | None = None
    idle_elapsed_seconds: float | None = None
    seconds_until_idle_cleanup: float | None = None
    local_headless_port: int | None = None
    local_mcp_port: int | None = None
    meta_headless_port: int | None = None
    meta_mcp_port: int | None = None
    effective_headless_port: int | None = None
    effective_mcp_port: int | None = None
    occupying_resources: bool
    has_blender_process: bool
    has_mcp_process: bool


class HeadlessSessionDebugResponse(BaseModel):
    blender_mode: str
    frontend_client_id: str
    include_all_clients: bool
    now_ms: int
    session_idle_timeout_seconds: int
    session_sweep_interval_seconds: int
    total_sessions: int
    occupying_sessions: int
    sessions: list[HeadlessSessionDebugEntry]


class ReleaseRuntimeResponse(BaseModel):
    thread_id: str
    released: bool
    cleaned: list[str] = Field(default_factory=list)


class VLMProviderOption(BaseModel):
    provider: str
    display_name: str
    default_model: str
    models: list[str]
    configured: bool


class ThreadVLMSelectionResponse(BaseModel):
    thread_id: str
    provider: str
    model: str
    locked: bool


class VLMModelsResponse(BaseModel):
    providers: list[VLMProviderOption]
    default_provider: str
    default_model: str
    thread_selection: ThreadVLMSelectionResponse | None = None


class BlendFileEntry(BaseModel):
    relative_path: str
    filename: str
    size_bytes: int
    modified_at: str
    category: str


class BlendFileListResponse(BaseModel):
    thread_id: str
    files: list[BlendFileEntry]


def serialize_image_asset(image: ImageAsset) -> ImageAssetResponse:
    return ImageAssetResponse(
        id=image.id,
        thread_id=image.thread_id,
        filename=image.filename,
        content_type=image.content_type,
        size_bytes=image.size_bytes,
        sha256=image.sha256,
        uploaded_at=image.uploaded_at,
        source=image.source,
    )


def _safe_storage_session_id(thread_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", thread_id).strip("._")
    return safe or "session"


def resolve_thread_storage_dir(thread_id: str) -> Path:
    settings = get_settings()
    storage_root = (
        os.getenv("SESSION_SHARED_STORAGE_ROOT")
        or os.getenv("SESSION_BLEND_ROOT")
        or settings.session_shared_storage_root
    )
    return Path(storage_root) / _safe_storage_session_id(thread_id)


def parse_example_prompts(markdown_text: str) -> list[str]:
    prompts: list[str] = []
    numbered_line_pattern = re.compile(r"^\s*(?:[-*]\s*)?(\d+)[\.\)]\s+(.+?)\s*$")
    for raw_line in markdown_text.splitlines():
        match = numbered_line_pattern.match(raw_line)
        if not match:
            continue
        prompt = match.group(2).strip()
        if prompt:
            prompts.append(prompt)
    return prompts


def load_example_prompts() -> list[str]:
    with EXAMPLE_PROMPTS_PATH.open("r", encoding="utf-8") as handle:
        content = handle.read()
    prompts = parse_example_prompts(content)
    if not prompts:
        raise ValueError("No prompts found in assets/example_prompts.md")
    return prompts


def extract_available_tool_names(agent: Any) -> list[str]:
    raw_names = getattr(agent, "_public_tool_names", getattr(agent, "_available_tool_names", []))
    if not isinstance(raw_names, list):
        return []
    valid_names = [
        name
        for name in raw_names
        if isinstance(name, str) and name
    ]
    return sorted(set(valid_names))


def extract_available_tool_hints(agent: Any) -> dict[str, str]:
    raw_hints = getattr(agent, "_public_tool_hints", getattr(agent, "_available_tool_hints", {}))
    if not isinstance(raw_hints, dict):
        return {}
    cleaned: dict[str, str] = {}
    for raw_name, raw_hint in raw_hints.items():
        if not isinstance(raw_name, str):
            continue
        name = raw_name.strip()
        if not name or not isinstance(raw_hint, str):
            continue
        hint = re.sub(r"\s+", " ", raw_hint).strip()
        if not hint:
            continue
        cleaned[name] = hint
    return cleaned


def build_default_tool_hint(tool_name: str) -> str:
    normalized = re.sub(r"[_\s]+", " ", tool_name).strip()
    return f"MCP tool: {normalized}." if normalized else "MCP tool."


def normalize_requested_tool_names(raw_names: list[str] | None) -> list[str] | None:
    if raw_names is None:
        return None
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_name in raw_names:
        if not isinstance(raw_name, str):
            continue
        name = raw_name.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        normalized.append(name)
    return normalized


def extract_runtime_tool_names(agent: Any) -> list[str]:
    raw_names = getattr(agent, "_available_tool_names", [])
    if not isinstance(raw_names, list):
        return []
    valid_names = [
        name
        for name in raw_names
        if isinstance(name, str) and name
    ]
    return sorted(set(valid_names))


def resolve_enabled_tool_names(
    agent: Any,
    requested_tool_names: list[str] | None,
) -> list[str]:
    available_tool_names = extract_runtime_tool_names(agent)
    if requested_tool_names is None:
        return available_tool_names
    requested_set = set(requested_tool_names)
    return [name for name in available_tool_names if name in requested_set]


def build_headless_diagnostics(
    *,
    session,
    request_id: str,
    elapsed_ms_value: int,
    status: str,
    target_ms: int,
) -> dict[str, Any]:
    record = build_diagnostic_record(
        request_id=request_id,
        thread_id=session.session_id,
        session_id=session.session_id,
        process_id=session.process.pid if session.process else None,
        log_path=session.log_path,
        elapsed_ms_value=elapsed_ms_value,
        status=status,
    )
    payload = record.__dict__.copy()
    payload["within_target"] = within_target(elapsed_ms_value, target_ms)
    return payload


async def execute_headless_export_code(
    *,
    thread_id: str,
    export_code: str,
    timeout_seconds: float,
    timeout_error_message: str,
    timeout_event_name: str,
    failed_event_name: str,
    ok_event_name: str,
    restart_on_timeout: bool = False,
) -> None:
    manager = get_session_manager()
    session = manager.ensure(thread_id, "headless")
    request_id = new_request_id(thread_id)
    start_time = start_timer()
    try:
        await asyncio.wait_for(
            asyncio.to_thread(
                send_blender_command_sync,
                "execute_code",
                {"code": export_code},
                thread_id,
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        elapsed_value = elapsed_ms(start_time)
        diagnostics = build_headless_diagnostics(
            session=session,
            request_id=request_id,
            elapsed_ms_value=elapsed_value,
            status="timeout",
            target_ms=int(timeout_seconds * 1000),
        )
        log_event("error", timeout_event_name, diagnostics)
        if restart_on_timeout:
            restart_headless_session_after_timeout(thread_id)
        raise HTTPException(
            status_code=504,
            detail={"error": timeout_error_message, **diagnostics},
        ) from exc
    except SessionResourceError as exc:
        raise HTTPException(status_code=503, detail=exc.detail) from exc
    except Exception as exc:
        elapsed_value = elapsed_ms(start_time)
        diagnostics = build_headless_diagnostics(
            session=session,
            request_id=request_id,
            elapsed_ms_value=elapsed_value,
            status="error",
            target_ms=int(timeout_seconds * 1000),
        )
        log_event("error", failed_event_name, {**diagnostics, "error": str(exc)})
        raise HTTPException(
            status_code=500,
            detail={"error": str(exc), **diagnostics},
        ) from exc
    else:
        elapsed_value = elapsed_ms(start_time)
        diagnostics = build_headless_diagnostics(
            session=session,
            request_id=request_id,
            elapsed_ms_value=elapsed_value,
            status="ok",
            target_ms=int(timeout_seconds * 1000),
        )
        log_event("info", ok_event_name, diagnostics)


async def render_scene_level_views(
    *,
    thread_id: str,
    is_headless: bool,
    request_timeout_seconds: float | None,
) -> list[dict[str, str]]:
    """
    Render canonical 3 scene-level viewpoints (NE/SW + top-down bird view).

    This path does not depend on pre-existing camera objects in scene info.
    It uses camera_observe in single_view mode and labels outputs with
    deterministic scene-level camera names.
    """
    renders: list[dict[str, str]] = []
    for camera_name, azimuth, elevation in _SCENE_LEVEL_RENDER_CAMERA_CONFIGS:
        temp_path = os.path.join(
            tempfile.gettempdir(),
            f"scene_level_render_{camera_name}_{int(time.time() * 1000)}.png",
        )
        render_call = asyncio.to_thread(
            send_blender_command_sync,
            "camera_observe",
            {
                "object_names": [],
                "mode": "single_view",
                "focal_length": _SCENE_LEVEL_RENDER_FOCAL_MM,
                "azimuth": azimuth,
                "elevation": elevation,
                "reuse_cameras": True,
                "camera_name": camera_name,
                "camera_kind": "scene_level",
                "filepath": temp_path,
            },
            thread_id,
        )
        try:
            if is_headless and request_timeout_seconds is not None:
                result = await asyncio.wait_for(render_call, timeout=request_timeout_seconds)
            else:
                result = await render_call
        except Exception as exc:
            log_event(
                "warning",
                "scene_level_render_failed",
                {"thread_id": thread_id, "camera_name": camera_name, "error": str(exc)},
            )
            continue

        filepath = result.get("filepath") or temp_path
        if not os.path.exists(filepath):
            continue

        image_url = process_and_save_render(
            filepath,
            thread_id,
            camera_name,
            log_event=log_event,
        )
        try:
            os.remove(filepath)
        except OSError:
            pass
        renders.append({"camera_name": camera_name, "image_url": image_url})
    return renders


def set_owner_headers(response: Response, resolution: Any | None) -> None:
    if resolution is None:
        return
    owner = getattr(resolution, "owner_worker_id", "") or ""
    epoch = getattr(resolution, "lease_epoch", None)
    if owner:
        response.headers["X-Session-Owner"] = owner
    if epoch is not None:
        response.headers["X-Session-Lease-Epoch"] = str(epoch)


async def claim_or_proxy_request(
    *,
    request: Request,
    thread_id: str,
) -> tuple[Any, Response | None]:
    coordinator = get_session_coordinator()
    settings = get_settings()
    request_client_id = resolve_frontend_client_id(request)
    resolution = coordinator.claim_or_get_owner(thread_id)
    if resolution.is_owner:
        _bind_frontend_client_to_thread_if_unclaimed(thread_id, request_client_id)
        return resolution, None

    if _is_multipart_request(request):
        takeover = coordinator.force_takeover(thread_id)
        if not takeover.is_owner:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Cannot proxy multipart upload for thread '{thread_id}'. "
                    f"Current owner: {takeover.owner_worker_id}"
                ),
            )
        _bind_frontend_client_to_thread_if_unclaimed(thread_id, request_client_id)
        return takeover, None

    if not resolution.owner_url:
        raise HTTPException(
            status_code=503,
            detail=f"Session owner URL is missing for thread '{thread_id}'.",
        )

    try:
        proxied = await forward_request_to_owner(
            request=request,
            owner_url=resolution.owner_url,
            timeout_seconds=max(
                settings.api_stream_timeout_seconds + 30,
                settings.headless_request_timeout_seconds + 30,
            ),
        )
        set_owner_headers(proxied, resolution)
        return resolution, proxied
    except OwnerProxyError as exc:
        retry_error = exc
        # Retry once to absorb transient owner socket failures.
        try:
            proxied = await forward_request_to_owner(
                request=request,
                owner_url=resolution.owner_url,
                timeout_seconds=max(
                    settings.api_stream_timeout_seconds + 30,
                    settings.headless_request_timeout_seconds + 30,
                ),
            )
            set_owner_headers(proxied, resolution)
            return resolution, proxied
        except OwnerProxyError as retry_exc:
            retry_error = retry_exc

        latest_resolution = coordinator.get_owner(thread_id) or resolution
        if not coordinator.should_attempt_takeover(latest_resolution):
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Owner worker '{latest_resolution.owner_worker_id}' is unavailable; "
                    f"lease still active, skipping takeover. Error: {retry_error}"
                ),
            ) from retry_error
        takeover = coordinator.force_takeover(thread_id)
        if not takeover.is_owner:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Failed to proxy and failed to take over thread '{thread_id}'. "
                    f"Current owner: {takeover.owner_worker_id}"
                ),
            ) from retry_error
        return takeover, None


def _is_multipart_request(request: Request) -> bool:
    content_type = request.headers.get("content-type", "")
    return content_type.lower().startswith("multipart/form-data")


async def claim_or_takeover_upload_request(
    *,
    request: Request,
    thread_id: str,
) -> Any:
    """
    Ensure current worker owns upload requests.

    Multipart form bodies are consumed by FastAPI before endpoint logic runs,
    so proxy forwarding is unreliable for UploadFile endpoints.
    """
    coordinator = get_session_coordinator()
    request_client_id = resolve_frontend_client_id(request)
    resolution = coordinator.claim_or_get_owner(thread_id)
    if not resolution.is_owner:
        takeover = coordinator.force_takeover(thread_id)
        if not takeover.is_owner:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Cannot process upload for thread '{thread_id}' on this worker; "
                    f"current owner is '{takeover.owner_worker_id}'."
                ),
            )
        resolution = takeover
    _bind_frontend_client_to_thread_if_unclaimed(thread_id, request_client_id)
    return resolution


async def _idle_session_sweeper() -> None:
    while True:
        try:
            settings = get_settings()
            interval = max(1, settings.session_sweep_interval_seconds)
            await asyncio.sleep(interval)
            manager = get_session_manager()
            coordinator = get_session_coordinator()
            idle_sessions = manager.get_idle_sessions()
            for session in idle_sessions:
                try:
                    headless_host_snapshot = session.host or settings.blender_host
                    headless_port_snapshot = session.port
                    mcp_host_snapshot = session.mcp_host or os.getenv("BLENDER_MCP_HOST", "localhost")
                    mcp_port_snapshot = session.mcp_port
                    stopped, persisted = await asyncio.to_thread(
                        manager.shutdown_if_idle,
                        session.session_id,
                    )
                    if not stopped:
                        continue
                    coordinator.update_session_runtime_fields(
                        session.session_id,
                        {
                            "status": "closed",
                            "last_active_ms": int(time.time() * 1000),
                            "host": "",
                            "mcp_host": "",
                            "blender_port": "",
                            "mcp_port": "",
                        },
                    )
                    coordinator.release_port(
                        host=headless_host_snapshot,
                        kind="headless",
                        port=headless_port_snapshot,
                    )
                    coordinator.release_port(
                        host=mcp_host_snapshot,
                        kind="mcp",
                        port=mcp_port_snapshot,
                    )
                    # Also clean up cached agent graph & VLM config so MCP client
                    # references are released and don't keep reconnecting.
                    if session.session_id in _agent_graphs_by_thread:
                        del _agent_graphs_by_thread[session.session_id]
                    with _thread_vlm_lock:
                        _thread_vlm_configs.pop(session.session_id, None)
                    log_event(
                        "info",
                        "headless_session_idle_stopped",
                        {
                            "thread_id": session.session_id,
                            "session_id": session.session_id,
                            "blend_path": session.blend_path,
                            "persisted": persisted,
                            "idle_timeout_seconds": session.idle_timeout_seconds,
                        },
                    )
                except Exception as exc:
                    log_event(
                        "warning",
                        "headless_idle_sweeper_session_failed",
                        {
                            "thread_id": getattr(session, "session_id", None),
                            "error": str(exc),
                        },
                    )
        except Exception as exc:
            log_event(
                "warning",
                "headless_idle_sweeper_cycle_failed",
                {"error": str(exc)},
            )


@app.on_event("startup")
async def startup_event():
    """Initialize agent on startup"""
    global _idle_sweeper_task
    try:
        log_path = _configure_api_file_logging()
        log_event("info", "api_file_logging_enabled", {"log_path": str(log_path)})
        settings = get_settings()
        coordinator = get_session_coordinator()
        if _should_reset_redis_runtime_on_start():
            reset_result = coordinator.clear_runtime_state()
            log_event(
                "warning",
                "redis_runtime_state_reset_on_startup",
                {
                    "enabled": True,
                    "result": reset_result or {"deleted_keys": 0},
                },
            )
        coordinator.register_worker()
        if settings.blender_mode == "headless":
            if _idle_sweeper_task is None or _idle_sweeper_task.done():
                _idle_sweeper_task = asyncio.create_task(_idle_session_sweeper())
                log_event(
                    "info",
                    "headless_idle_sweeper_started",
                    {
                        "interval_seconds": max(1, settings.session_sweep_interval_seconds),
                        "idle_timeout_seconds": settings.session_idle_timeout_seconds,
                    },
                )
            log_event("info", "startup_skip_agent_init", {"mode": settings.blender_mode})
            return
        await get_agent()
        log_event("info", "agent_initialized", {"mode": settings.blender_mode})
    except Exception as e:
        log_event("error", "agent_init_failed", {"error": str(e)})


@app.on_event("shutdown")
async def shutdown_event():
    """Ensure headless processes are cleaned up on shutdown."""
    global _idle_sweeper_task
    try:
        coordinator = get_session_coordinator()
        if _idle_sweeper_task is not None:
            _idle_sweeper_task.cancel()
            try:
                await _idle_sweeper_task
            except asyncio.CancelledError:
                pass
            finally:
                _idle_sweeper_task = None

        manager = get_session_manager()
        for session in manager.list_sessions():
            if session.mode == "headless":
                try:
                    manager.persist_session_blend(session.session_id)
                except Exception:
                    pass
                coordinator.update_session_runtime_fields(
                    session.session_id,
                    {"status": "closed"},
                )
                coordinator.release_port(
                    host=session.host or get_settings().blender_host,
                    kind="headless",
                    port=session.port,
                )
                coordinator.release_port(
                    host=session.mcp_host or os.getenv("BLENDER_MCP_HOST", "localhost"),
                    kind="mcp",
                    port=session.mcp_port,
                )
        manager.shutdown_all()
        _agent_graphs_by_thread.clear()
        with _thread_vlm_lock:
            _thread_vlm_configs.clear()
        coordinator.unregister_worker()
    except Exception as e:
        log_event("error", "shutdown_cleanup_failed", {"error": str(e)})
























def blend_file_category(relative_path: str) -> str:
    if relative_path == "scene.blend":
        return "session"
    if relative_path.startswith("snapshots/"):
        return "snapshot"
    return "other"






def normalize_image_limit(limit: int | None) -> int | None:
    if isinstance(limit, int) and limit > 0:
        return limit
    return None








def _safe_int_optional(raw: object) -> int | None:
    try:
        if raw is None or raw == "":
            return None
        return int(raw)
    except (TypeError, ValueError):
        return None


def _resolve_thread_frontend_client(thread_id: str, meta: dict[str, str] | None = None) -> str:
    if meta is not None:
        from_meta = _normalize_frontend_client_id(meta.get("frontend_client_id"))
        if from_meta != _DEFAULT_FRONTEND_CLIENT_ID:
            return from_meta
    with _thread_client_lock:
        from_memory = _thread_frontend_clients.get(thread_id)
    return _normalize_frontend_client_id(from_memory)


def collect_headless_runtime_entries(*, frontend_client_id: str) -> list[dict[str, Any]]:
    settings = get_settings()
    if settings.blender_mode != "headless":
        return []

    coordinator = get_session_coordinator()
    manager = get_session_manager()
    entries_by_thread: dict[str, dict[str, Any]] = {}

    thread_ids = coordinator.list_threads(limit=2000)
    for thread_id in thread_ids:
        meta = coordinator.get_session_meta(thread_id) or {}
        if not meta:
            continue
        blender_port = _safe_int_optional(meta.get("blender_port"))
        mcp_port = _safe_int_optional(meta.get("mcp_port"))
        occupying = blender_port is not None or mcp_port is not None
        client_id = _resolve_thread_frontend_client(thread_id, meta)
        if (
            occupying
            and client_id == _DEFAULT_FRONTEND_CLIENT_ID
            and frontend_client_id != _DEFAULT_FRONTEND_CLIENT_ID
        ):
            # Migrate legacy runtime entries (without client ownership) on first
            # access so a frontend can see and release its stale occupied slots.
            _bind_frontend_client_to_thread(thread_id, frontend_client_id)
            client_id = frontend_client_id
        if client_id != frontend_client_id:
            continue
        last_active_ms = (
            _safe_int_optional(meta.get("last_active_ms"))
            or _safe_int_optional(meta.get("updated_at_ms"))
            or 0
        )
        entries_by_thread[thread_id] = {
            "thread_id": thread_id,
            "frontend_client_id": client_id,
            "status": str(meta.get("status") or ("active" if occupying else "closed")),
            "last_active_ms": int(last_active_ms),
            "occupying_resources": bool(occupying),
            "blender_port": blender_port,
            "mcp_port": mcp_port,
        }

    for session in manager.list_sessions():
        if session.mode != "headless":
            continue
        thread_id = session.session_id
        existing = entries_by_thread.get(thread_id)
        client_id = str((existing or {}).get("frontend_client_id") or _resolve_thread_frontend_client(thread_id))
        # Trust local runtime state for local sessions. Do not fallback to stale
        # metadata ports that may have already been released.
        blender_port = session.port
        mcp_port = session.mcp_port
        occupying = blender_port is not None or mcp_port is not None
        if (
            occupying
            and client_id == _DEFAULT_FRONTEND_CLIENT_ID
            and frontend_client_id != _DEFAULT_FRONTEND_CLIENT_ID
        ):
            _bind_frontend_client_to_thread(thread_id, frontend_client_id)
            client_id = frontend_client_id
        if client_id != frontend_client_id:
            continue
        last_active_ms = int(getattr(session, "last_active_at", 0.0) * 1000) or (existing or {}).get("last_active_ms") or 0
        status = str(getattr(session, "status", None) or (existing or {}).get("status") or ("active" if occupying else "closed"))
        entries_by_thread[thread_id] = {
            "thread_id": thread_id,
            "frontend_client_id": client_id,
            "status": status,
            "last_active_ms": int(last_active_ms),
            "occupying_resources": bool(occupying),
            "blender_port": blender_port,
            "mcp_port": mcp_port,
        }

    entries = list(entries_by_thread.values())
    entries.sort(key=lambda item: (int(item.get("last_active_ms") or 0), str(item.get("thread_id") or "")))
    return entries


def collect_headless_runtime_debug_entries(
    *,
    frontend_client_id: str,
    include_all_clients: bool = False,
) -> tuple[int, list[dict[str, Any]]]:
    settings = get_settings()
    if settings.blender_mode != "headless":
        return int(time.time() * 1000), []

    coordinator = get_session_coordinator()
    manager = get_session_manager()
    now_ms = int(time.time() * 1000)

    meta_by_thread: dict[str, dict[str, str]] = {}
    for thread_id in coordinator.list_threads(limit=5000):
        meta = coordinator.get_session_meta(thread_id) or {}
        if meta:
            meta_by_thread[thread_id] = meta

    local_by_thread: dict[str, Any] = {}
    for session in manager.list_sessions():
        if getattr(session, "mode", None) != "headless":
            continue
        local_by_thread[str(session.session_id)] = session

    all_thread_ids = set(meta_by_thread.keys()) | set(local_by_thread.keys())
    entries: list[dict[str, Any]] = []

    def _process_alive(process: Any) -> bool:
        if process is None:
            return False
        try:
            return process.poll() is None
        except Exception:
            return True

    for thread_id in sorted(all_thread_ids):
        meta = meta_by_thread.get(thread_id, {})
        local = local_by_thread.get(thread_id)
        resolved_client_id = _resolve_thread_frontend_client(thread_id, meta if meta else None)
        if not include_all_clients and resolved_client_id != frontend_client_id:
            continue

        owner = coordinator.get_owner(thread_id)
        local_headless_port = _safe_int_optional(getattr(local, "port", None))
        local_mcp_port = _safe_int_optional(getattr(local, "mcp_port", None))
        meta_headless_port = _safe_int_optional(meta.get("blender_port")) if meta else None
        meta_mcp_port = _safe_int_optional(meta.get("mcp_port")) if meta else None
        effective_headless_port = local_headless_port if local is not None else meta_headless_port
        effective_mcp_port = local_mcp_port if local is not None else meta_mcp_port
        occupying = effective_headless_port is not None or effective_mcp_port is not None

        local_last_active_ms = None
        if local is not None:
            try:
                local_last_active_ms = int(float(getattr(local, "last_active_at", 0.0)) * 1000)
            except Exception:
                local_last_active_ms = None
        meta_last_active_ms = (
            _safe_int_optional(meta.get("last_active_ms"))
            or _safe_int_optional(meta.get("updated_at_ms"))
            or None
        )
        last_active_ms = local_last_active_ms or meta_last_active_ms or 0

        idle_timeout_seconds = None
        idle_elapsed_seconds = None
        seconds_until_idle_cleanup = None
        if local is not None:
            try:
                idle_timeout_seconds = int(getattr(local, "idle_timeout_seconds", 0))
            except Exception:
                idle_timeout_seconds = None
            if local_last_active_ms is not None:
                idle_elapsed_seconds = max(0.0, (now_ms - local_last_active_ms) / 1000.0)
            if (
                idle_timeout_seconds is not None
                and idle_timeout_seconds > 0
                and idle_elapsed_seconds is not None
                and (_process_alive(getattr(local, "process", None)) or _process_alive(getattr(local, "mcp_process", None)))
            ):
                seconds_until_idle_cleanup = max(0.0, float(idle_timeout_seconds) - idle_elapsed_seconds)
            elif (
                idle_timeout_seconds is not None
                and idle_timeout_seconds > 0
                and idle_elapsed_seconds is not None
                and not occupying
            ):
                seconds_until_idle_cleanup = 0.0

        local_status = str(getattr(local, "status", "") or "") if local is not None else None
        meta_status = str(meta.get("status") or "") if meta else None
        status = local_status or meta_status or ("active" if occupying else "closed")

        entries.append(
            {
                "thread_id": thread_id,
                "frontend_client_id": resolved_client_id,
                "status": status,
                "local_status": local_status,
                "meta_status": meta_status,
                "owner_worker_id": owner.owner_worker_id if owner is not None else None,
                "lease_ttl_ms": int(owner.lease_ttl_ms) if owner is not None else None,
                "last_active_ms": int(last_active_ms),
                "local_last_active_ms": local_last_active_ms,
                "meta_last_active_ms": meta_last_active_ms,
                "idle_timeout_seconds": idle_timeout_seconds,
                "idle_elapsed_seconds": idle_elapsed_seconds,
                "seconds_until_idle_cleanup": seconds_until_idle_cleanup,
                "local_headless_port": local_headless_port,
                "local_mcp_port": local_mcp_port,
                "meta_headless_port": meta_headless_port,
                "meta_mcp_port": meta_mcp_port,
                "effective_headless_port": effective_headless_port,
                "effective_mcp_port": effective_mcp_port,
                "occupying_resources": occupying,
                "has_blender_process": _process_alive(getattr(local, "process", None)) if local is not None else False,
                "has_mcp_process": _process_alive(getattr(local, "mcp_process", None)) if local is not None else False,
            }
        )

    entries.sort(key=lambda item: (int(item.get("last_active_ms") or 0), str(item.get("thread_id") or "")))
    return now_ms, entries


def ensure_frontend_client_can_manage_thread(thread_id: str, request_client_id: str) -> None:
    coordinator = get_session_coordinator()
    meta = coordinator.get_session_meta(thread_id) or {}
    resolved_client_id = _resolve_thread_frontend_client(thread_id, meta)
    if resolved_client_id == request_client_id:
        return
    if (
        resolved_client_id == _DEFAULT_FRONTEND_CLIENT_ID
        and request_client_id != _DEFAULT_FRONTEND_CLIENT_ID
    ):
        _bind_frontend_client_to_thread(thread_id, request_client_id)
        return
    raise HTTPException(
        status_code=403,
        detail=(
            f"Thread '{thread_id}' is associated with a different frontend client "
            "and cannot be released by this browser."
        ),
    )


def _release_headless_runtime_resources(
    *,
    thread_id: str,
    settings: Any,
    coordinator: Any,
    manager: Any,
    result: dict[str, Any],
    release_meta_when_non_headless_session: bool,
) -> bool:
    session = manager.get(thread_id)
    released = False

    if session is not None and session.mode == "headless":
        headless_host_snapshot = session.host or settings.blender_host
        headless_port_snapshot = session.port
        mcp_host_snapshot = session.mcp_host or os.getenv("BLENDER_MCP_HOST", "localhost")
        mcp_port_snapshot = session.mcp_port

        try:
            manager.persist_session_blend(thread_id)
            result["cleaned"].append("blend_persisted")
        except Exception:
            pass

        manager.terminate_session_processes(thread_id, timeout=5.0)
        result["cleaned"].append("processes_terminated")

        coordinator.release_port(
            host=headless_host_snapshot,
            kind="headless",
            port=headless_port_snapshot,
        )
        if headless_port_snapshot is not None:
            result["cleaned"].append(f"headless_port:{headless_port_snapshot}")

        coordinator.release_port(
            host=mcp_host_snapshot,
            kind="mcp",
            port=mcp_port_snapshot,
        )
        if mcp_port_snapshot is not None:
            result["cleaned"].append(f"mcp_port:{mcp_port_snapshot}")

        manager.remove(thread_id)
        result["cleaned"].append("session_removed")
        released = True
        return released

    if session is None or release_meta_when_non_headless_session:
        session_meta = coordinator.get_session_meta(thread_id) or {}
        meta_headless_host = str(session_meta.get("host") or settings.blender_host)
        meta_headless_port = _safe_int_optional(session_meta.get("blender_port"))
        meta_mcp_host = str(session_meta.get("mcp_host") or os.getenv("BLENDER_MCP_HOST", "localhost"))
        meta_mcp_port = _safe_int_optional(session_meta.get("mcp_port"))
        if meta_headless_port is not None:
            coordinator.release_port(
                host=meta_headless_host,
                kind="headless",
                port=meta_headless_port,
            )
            result["cleaned"].append(f"headless_port:{meta_headless_port}")
            released = True
        if meta_mcp_port is not None:
            coordinator.release_port(
                host=meta_mcp_host,
                kind="mcp",
                port=meta_mcp_port,
            )
            result["cleaned"].append(f"mcp_port:{meta_mcp_port}")
            released = True
    return released


def release_thread_runtime(thread_id: str) -> dict[str, Any]:
    """Release headless runtime resources while keeping thread identity/history intact."""
    settings = get_settings()
    coordinator = get_session_coordinator()
    manager = get_session_manager()
    result: dict[str, Any] = {"thread_id": thread_id, "released": False, "cleaned": []}
    result["released"] = _release_headless_runtime_resources(
        thread_id=thread_id,
        settings=settings,
        coordinator=coordinator,
        manager=manager,
        result=result,
        release_meta_when_non_headless_session=True,
    )

    if thread_id in _agent_graphs_by_thread:
        del _agent_graphs_by_thread[thread_id]
        result["cleaned"].append("agent_graph")
    coordinator.update_session_runtime_fields(
        thread_id,
        {
            "status": "closed",
            "host": "",
            "mcp_host": "",
            "blender_port": "",
            "mcp_port": "",
        },
    )
    return result








def teardown_thread_session(thread_id: str) -> dict[str, Any]:
    """Fully tear down a headless session: kill processes, release ports, clean caches."""
    settings = get_settings()
    coordinator = get_session_coordinator()
    manager = get_session_manager()
    result: dict[str, Any] = {"thread_id": thread_id, "cleaned": []}
    _release_headless_runtime_resources(
        thread_id=thread_id,
        settings=settings,
        coordinator=coordinator,
        manager=manager,
        result=result,
        release_meta_when_non_headless_session=False,
    )

    # Clean up cached agent graph (frees MCP client references).
    if thread_id in _agent_graphs_by_thread:
        del _agent_graphs_by_thread[thread_id]
        result["cleaned"].append("agent_graph")

    # Clean up cached VLM config.
    with _thread_vlm_lock:
        if thread_id in _thread_vlm_configs:
            del _thread_vlm_configs[thread_id]
            result["cleaned"].append("vlm_config")

    try:
        get_image_asset_memory().clear_thread(thread_id)
        result["cleaned"].append("image_assets")
    except Exception:
        pass

    try:
        checkpointer = get_graph_checkpointer()
        delete_fn = getattr(checkpointer, "delete_thread", None)
        if callable(delete_fn):
            delete_fn(thread_id)
            result["cleaned"].append("graph_checkpoints")
    except Exception:
        pass

    # Clean up Redis session metadata.
    coordinator.update_session_runtime_fields(thread_id, {"status": "closed"})
    coordinator.delete_session_metadata(thread_id)
    result["cleaned"].append("redis_metadata")
    _clear_frontend_client_binding(thread_id)
    result["cleaned"].append("frontend_client_binding")

    return result






def run_api(host: str = "0.0.0.0", port: int = 8000, workers: int | None = None):
    """
    Run the FastAPI server.
    
    Args:
        host: Host to bind to
        port: Port to listen on
        workers: Number of worker processes
    """
    import uvicorn
    load_project_dotenv()
    log_path = _configure_api_file_logging()
    log_event("info", "api_run_configured", {"log_path": str(log_path), "host": host, "port": port})
    settings = get_settings()
    worker_count = workers if workers is not None else settings.api_workers
    worker_count = max(1, worker_count)
    if worker_count > 1:
        log_event(
            "warning",
            "api_worker_count_adjusted",
            {
                "requested_workers": worker_count,
                "forced_workers": 1,
                "reason": (
                    "single API process must run with workers=1; "
                    "use multiple processes on different ports with "
                    "unique API_WORKER_ADVERTISE_URL"
                ),
            },
        )
        worker_count = 1
    uvicorn.run("scene_agent.interfaces.api:app", host=host, port=port, workers=worker_count)


# Export all non-dunder names for package-level compatibility.


__all__ = [name for name in globals() if not (name.startswith("__") and name.endswith("__"))]
