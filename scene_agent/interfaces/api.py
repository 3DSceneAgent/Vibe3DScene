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
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any
from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.responses import StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage
from scene_agent.blender.connection import BlenderConnection

from scene_agent.agent.graph import create_agent_graph
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
from scene_agent.memory.scene_memory import SceneMemory
from scene_agent.memory.reference_image_memory import (
    ReferenceImage,
    get_reference_image_memory,
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
EXAMPLE_PROMPTS_PATH = Path(__file__).resolve().parents[2] / "assets" / "example_prompts.md"

# Global agent instance
_agent_graph = None
_agent_graphs_by_thread: Dict[str, Any] = {}
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
_SUPPORTED_VLM_PROVIDERS = ("openai", "anthropic", "gemini")
_VLM_PROVIDER_DISPLAY_NAMES = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Gemini",
}
_SCENE_LEVEL_RENDER_CAMERA_CONFIGS: tuple[tuple[str, float, float], ...] = (
    ("SceneCamera_NE", 45.0, 30.0),
    ("SceneCamera_NW", 135.0, 30.0),
    ("SceneCamera_SE", -45.0, 30.0),
    ("SceneCamera_SW", -135.0, 30.0),
    ("SceneCamera_TopDown", 0.0, 89.0),
)
_SCENE_LEVEL_RENDER_FOCAL_MM = 50.0
_DEFAULT_API_LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "api_server.log"
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


def _resolve_frontend_client_id(request: Request | None) -> str:
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


def _extract_graph_step_events(mode: str | None, payload: Any) -> list[dict[str, Any]]:
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


def _build_graph_node_event_payload(
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


def _headless_timeout_seconds_for_session(settings: Any, session: Any | None = None) -> float:
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


def _restart_headless_session_after_timeout(thread_id: str) -> None:
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
            {"status": "closed"},
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


def _build_vlm_provider_catalog() -> list[Dict[str, Any]]:
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
                f"Set {selected_provider.upper()}_API_KEY or VLM_API_KEY."
            ),
        )

    return {
        "provider": selected_provider,
        "model": selected_model,
        "api_key": selected_api_key,
    }


def _ensure_thread_vlm_config(thread_id: str) -> Dict[str, Any]:
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


def _resolve_thread_vlm_for_chat(
    thread_id: str,
    requested_provider: str | None,
    requested_model: str | None,
) -> Dict[str, str]:
    coordinator = get_session_coordinator()
    normalized_provider = _normalize_optional(requested_provider, lower=True)
    normalized_model = _normalize_optional(requested_model)
    state = _ensure_thread_vlm_config(thread_id)
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
    state = _ensure_thread_vlm_config(thread_id)
    return _resolve_vlm_selection(provider=state["provider"], model=state["model"])


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
    await to_graph.aupdate_state(config, values)


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
            "additional_kwargs": getattr(message, "additional_kwargs", None),
            "response_metadata": getattr(message, "response_metadata", None),
            "tool_calls": getattr(message, "tool_calls", None),
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
    return sanitized


def message_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                # Check if it's an image_url object
                if "image_url" in item and isinstance(item["image_url"], dict):
                    url = item["image_url"].get("url", "")
                    # Convert image_url to markdown if it's not a data URL
                    if url and not url.startswith("data:"):
                        parts.append(f"![image]({url})")
                    # Skip data URLs to avoid including base64 in text
                    continue
                elif isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
                else:
                    # Don't serialize large base64 data
                    if "base64" not in str(item):
                        parts.append(json.dumps(item, ensure_ascii=False, default=str))
            else:
                parts.append(str(item))
        return "".join(parts)
    if isinstance(content, dict):
        return json.dumps(_sanitize_stream_value(content), ensure_ascii=False, default=str)
    return str(content)


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
    return False


def message_is_tool(serialized: Dict[str, Any]) -> bool:
    return serialized.get("type") == "tool"


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
        graph_provider = getattr(graph, "_vlm_provider", None) if graph is not None else None
        graph_model = getattr(graph, "_vlm_model", None) if graph is not None else None
        has_vlm_metadata = graph_provider is not None and graph_model is not None
        graph_mismatch = (
            graph is None
            or (
                has_vlm_metadata
                and (
                    graph_provider != vlm_runtime["provider"]
                    or graph_model != vlm_runtime["model"]
                )
            )
        )
        if graph_mismatch:
            previous_graph = graph
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


class ChatResponse(BaseModel):
    response: str
    thread_id: str
    todos: list[Dict[str, Any]] = []


class ReferenceImageResponse(BaseModel):
    id: str
    thread_id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    uploaded_at: str


class ReferenceImageListResponse(BaseModel):
    thread_id: str
    images: list[ReferenceImageResponse]


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


def serialize_reference_image(image: ReferenceImage) -> ReferenceImageResponse:
    return ReferenceImageResponse(
        id=image.id,
        thread_id=image.thread_id,
        filename=image.filename,
        content_type=image.content_type,
        size_bytes=image.size_bytes,
        sha256=image.sha256,
        uploaded_at=image.uploaded_at,
    )


def _safe_storage_session_id(thread_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", thread_id).strip("._")
    return safe or "session"


def _resolve_thread_storage_dir(thread_id: str) -> Path:
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
    raw_names = getattr(agent, "_available_tool_names", [])
    if not isinstance(raw_names, list):
        return []
    valid_names = [
        name
        for name in raw_names
        if isinstance(name, str) and name
    ]
    return sorted(set(valid_names))


def extract_available_tool_hints(agent: Any) -> dict[str, str]:
    raw_hints = getattr(agent, "_available_tool_hints", {})
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


def resolve_enabled_tool_names(
    agent: Any,
    requested_tool_names: list[str] | None,
) -> list[str]:
    available_tool_names = extract_available_tool_names(agent)
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


async def _render_scene_level_views(
    *,
    thread_id: str,
    is_headless: bool,
    request_timeout_seconds: float | None,
) -> list[dict[str, str]]:
    """
    Render canonical 5 scene-level viewpoints (NE/NW/SE/SW + top-down bird view).

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


def _set_owner_headers(response: Response, resolution: Any | None) -> None:
    if resolution is None:
        return
    owner = getattr(resolution, "owner_worker_id", "") or ""
    epoch = getattr(resolution, "lease_epoch", None)
    if owner:
        response.headers["X-Session-Owner"] = owner
    if epoch is not None:
        response.headers["X-Session-Lease-Epoch"] = str(epoch)


async def _claim_or_proxy_request(
    *,
    request: Request,
    thread_id: str,
) -> tuple[Any, Response | None]:
    coordinator = get_session_coordinator()
    settings = get_settings()
    request_client_id = _resolve_frontend_client_id(request)
    resolution = coordinator.claim_or_get_owner(thread_id)
    if resolution.is_owner:
        _bind_frontend_client_to_thread_if_unclaimed(thread_id, request_client_id)
        return resolution, None

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
        _set_owner_headers(proxied, resolution)
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
            _set_owner_headers(proxied, resolution)
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


async def _idle_session_sweeper() -> None:
    while True:
        settings = get_settings()
        interval = max(1, settings.session_sweep_interval_seconds)
        await asyncio.sleep(interval)
        coordinator = get_session_coordinator()
        manager = get_session_manager()
        idle_sessions = manager.get_idle_sessions()
        for session in idle_sessions:
            if not coordinator.is_owned_by_current_worker(session.session_id):
                continue
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


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "3D Scene Agent API",
        "status": "running",
        "endpoints": {
            "health": "GET /health",
            "chat": "POST /chat",
            "chat_stream": "POST /chat/stream",
            "scene": "GET /scene/{thread_id}",
            "scene_renders": "GET /scene/{thread_id}/renders",
            "scene_gltf": "GET /scene/{thread_id}/gltf",
            "reference_images": "GET/POST /threads/{thread_id}/reference-images",
            "example_prompts": "GET /example-prompts",
            "vlm_models": "GET /vlm/models",
            "mcp_tools": "GET /threads/{thread_id}/mcp-tools",
            "session_capacity": "GET /headless/session-capacity",
            "release_runtime": "POST /threads/{thread_id}/release-runtime",
            "todos": "GET /todos/{thread_id}",
            "threads": "GET /threads",
            "delete_thread": "DELETE /threads/{thread_id}"
        }
    }


@app.get("/health")
async def healthcheck():
    """Healthcheck endpoint."""
    settings = get_settings()
    coordinator = get_session_coordinator()
    redis_ok, redis_latency_ms = coordinator.redis_health()
    return {
        "status": "ok",
        "timestamp": time.time(),
        "blender_mode": settings.blender_mode,
        "worker_id": settings.api_worker_id,
        "redis_ok": redis_ok,
        "redis_latency_ms": redis_latency_ms,
    }


@app.get("/example-prompts", response_model=ExamplePromptsResponse)
async def get_example_prompts():
    try:
        prompts = load_example_prompts()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="assets/example_prompts.md not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ExamplePromptsResponse(prompts=prompts)


@app.get("/vlm/models", response_model=VLMModelsResponse)
async def get_vlm_models(thread_id: str | None = None):
    settings = get_settings()
    providers = _build_vlm_provider_catalog()
    thread_selection: ThreadVLMSelectionResponse | None = None
    if thread_id:
        state = _ensure_thread_vlm_config(thread_id)
        thread_selection = ThreadVLMSelectionResponse(
            thread_id=thread_id,
            provider=state["provider"],
            model=state["model"],
            locked=False,
        )
    return VLMModelsResponse(
        providers=[VLMProviderOption(**provider) for provider in providers],
        default_provider=settings.vlm_provider,
        default_model=settings.get_vlm_default_model(settings.vlm_provider),
        thread_selection=thread_selection,
    )


@app.get("/threads/{thread_id}/mcp-tools", response_model=MCPToolsResponse)
async def get_mcp_tools(thread_id: str, request: Request, response: Response):
    settings = get_settings()
    resolution, proxied = await _claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied
    try:
        agent = await get_agent(thread_id)
    except SessionResourceError as exc:
        raise HTTPException(status_code=503, detail=exc.detail) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to load MCP tools for thread '{thread_id}': {exc}",
        ) from exc

    tools = extract_available_tool_names(agent)
    tool_hints_raw = extract_available_tool_hints(agent)
    tool_hints = {
        name: tool_hints_raw.get(name, build_default_tool_hint(name))
        for name in tools
    }
    payload = MCPToolsResponse(
        thread_id=thread_id,
        loaded=len(tools) > 0,
        tool_count=len(tools),
        tools=tools,
        tool_hints=tool_hints,
        blender_mode=settings.blender_mode,
    )
    _set_owner_headers(response, resolution)
    return payload


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, request_http: Request, response: Response):
    """
    Chat with the agent (non-streaming).
    
    Args:
        request: ChatRequest with message and thread_id
        
    Returns:
        ChatResponse with agent's response and todos
    """
    resolution, proxied = await _claim_or_proxy_request(
        request=request_http,
        thread_id=request.thread_id,
    )
    if proxied is not None:
        return proxied
    try:
        _resolve_thread_vlm_for_chat(
            request.thread_id,
            request.vlm_provider,
            request.vlm_model,
        )
        agent = await get_agent(request.thread_id)
        enabled_tool_names = resolve_enabled_tool_names(
            agent,
            normalize_requested_tool_names(request.enabled_mcp_tools),
        )
        config = {"configurable": {"thread_id": request.thread_id}}
        
        # Run agent
        result = await agent.ainvoke(
            {
                "messages": [HumanMessage(content=request.message)],
                "thread_id": request.thread_id,
                "enabled_tool_names": enabled_tool_names,
            },
            config=config
        )
        get_session_coordinator().touch_activity(request.thread_id)
        
        # Extract response as plain text (LangChain message content can be list/dict blocks).
        last_message = result["messages"][-1]
        serialized_last = serialize_message(last_message)
        response_text = message_content_to_text(serialized_last.get("content")).strip()
        if not response_text:
            response_text = str(last_message)
        
        payload = ChatResponse(
            response=response_text,
            thread_id=request.thread_id,
            todos=result.get("todos", [])
        )
        _set_owner_headers(response, resolution)
        return payload

    except HTTPException:
        raise
    except SessionResourceError as e:
        raise HTTPException(status_code=503, detail=e.detail) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest, request_http: Request):
    """
    Chat with the agent (streaming via Server-Sent Events).
    
    Args:
        request: ChatRequest with message and thread_id
        
    Returns:
        StreamingResponse with SSE events
    """
    resolution, proxied = await _claim_or_proxy_request(
        request=request_http,
        thread_id=request.thread_id,
    )
    if proxied is not None:
        return proxied

    async def event_generator():
        import time 
        settings = get_settings()
        timeout_seconds = max(1, settings.api_stream_timeout_seconds)
        keepalive_interval = min(15.0, max(5.0, timeout_seconds / 4))
        idle_deadline = time.time() + timeout_seconds
        request_id = f"{request.thread_id}:{int(time.time() * 1000)}"
        saw_message_stream = False
        saw_new_message = False
        graph_step_index = 0
        existing_message_ids: set[str] = set()
        last_assistant_text: str | None = None
        scene_has_change = False
        done_payload: dict[str, Any] | None = None
        try:
            _resolve_thread_vlm_for_chat(
                request.thread_id,
                request.vlm_provider,
                request.vlm_model,
            )
            agent = await get_agent(request.thread_id)
            get_session_coordinator().touch_activity(request.thread_id)
            enabled_tool_names = resolve_enabled_tool_names(
                agent,
                normalize_requested_tool_names(request.enabled_mcp_tools),
            )
            config = {"configurable": {"thread_id": request.thread_id}}
            try:
                state = await agent.aget_state(config)
                state_messages = []
                if hasattr(state, "values") and isinstance(state.values, dict):
                    state_messages = state.values.get("messages", []) or []
                for message in state_messages:
                    serialized = serialize_message(message)
                    message_type = serialized.get("type")
                    if message_type in {"ai", "assistant"}:
                        message_id = serialized.get("id")
                        if isinstance(message_id, str) and message_id:
                            existing_message_ids.add(message_id)
                        content_text = message_content_to_text(serialized.get("content"))
                        if content_text:
                            last_assistant_text = content_text
            except Exception:
                pass

            stream = agent.astream(
                {
                    "messages": [HumanMessage(content=request.message)],
                    "thread_id": request.thread_id,
                    "enabled_tool_names": enabled_tool_names,
                },
                config=config,
                stream_mode=["messages", "values", "updates"]
            )
            next_event_task: asyncio.Task | None = None
            while True:
                if time.time() >= idle_deadline:
                    if next_event_task is not None:
                        next_event_task.cancel()
                    raise TimeoutError("Stream timed out")
                if next_event_task is None:
                    next_event_task = asyncio.create_task(stream.__anext__())
                done, _pending = await asyncio.wait({next_event_task}, timeout=keepalive_interval)
                if not done:
                    yield ": keepalive\n\n"
                    continue
                try:
                    event = next_event_task.result()
                except StopAsyncIteration:
                    break
                finally:
                    next_event_task = None
                idle_deadline = time.time() + timeout_seconds

                mode, payload = normalize_stream_event(event)
                step_events = _extract_graph_step_events(mode, payload)
                for step_event in step_events:
                    log_event(
                        "info",
                        "agent_graph_step",
                        {
                            "request_id": request_id,
                            "thread_id": request.thread_id,
                            "step": step_event["step"],
                            "update_keys": step_event["update_keys"],
                        },
                    )
                is_message_stream = mode == "messages" or hasattr(mode, "content") or hasattr(mode, "type")
                if is_message_stream:
                    saw_message_stream = True
                if isinstance(payload, dict) and "todos" in payload and payload["todos"]:
                    yield f"data: {json.dumps({'todos': payload['todos']}, default=str)}\n\n"

                stream_payload: Any = payload
                stream_source_node: str | None = None
                if is_message_stream:
                    if mode == "messages" and isinstance(payload, tuple) and len(payload) == 2:
                        stream_payload = payload[0]
                        raw_meta = payload[1]
                        if isinstance(raw_meta, dict):
                            raw_node_name = raw_meta.get("langgraph_node")
                            if isinstance(raw_node_name, str) and raw_node_name:
                                stream_source_node = raw_node_name
                    elif isinstance(payload, dict):
                        raw_node_name = payload.get("langgraph_node")
                        if isinstance(raw_node_name, str) and raw_node_name:
                            stream_source_node = raw_node_name

                update_mode_tool_messages: list[Any] = []
                update_mode_non_tool_messages: list[Any] = []
                if mode == "updates" and isinstance(payload, dict):
                    for node_name, node_update in payload.items():
                        if not isinstance(node_name, str) or not node_name:
                            continue
                        if not isinstance(node_update, dict):
                            continue
                        graph_step_index += 1
                        graph_event_payload = _build_graph_node_event_payload(
                            request_id=request_id,
                            thread_id=request.thread_id,
                            node_name=node_name,
                            step_index=graph_step_index,
                            update=node_update,
                        )
                        yield f"data: {json.dumps(graph_event_payload, default=str)}\n\n"

                        todos_payload = node_update.get("todos")
                        if isinstance(todos_payload, list) and len(todos_payload) > 0:
                            yield f"data: {json.dumps({'todos': todos_payload}, default=str)}\n\n"

                        node_messages = node_update.get("messages")
                        node_messages_list: list[Any] = []
                        if isinstance(node_messages, list):
                            node_messages_list = node_messages
                        elif node_messages is not None:
                            node_messages_list = [node_messages]
                        for node_message in node_messages_list:
                            serialized_node_message = serialize_message(node_message)
                            if message_is_tool(serialized_node_message):
                                update_mode_tool_messages.append(node_message)
                            else:
                                update_mode_non_tool_messages.append(node_message)

                messages = None
                if is_message_stream:
                    messages = stream_payload if mode == "messages" else [mode]
                elif update_mode_tool_messages:
                    messages = update_mode_tool_messages
                elif update_mode_non_tool_messages and not saw_message_stream:
                    # Fallback only when token streaming is unavailable.
                    messages = update_mode_non_tool_messages
                elif isinstance(payload, dict) and "messages" in payload:
                    if not saw_message_stream:
                        messages = payload["messages"]

                if messages:
                    if not isinstance(messages, list):
                        messages = [messages]
                    for message in messages:
                        serialized = serialize_message(message)
                        is_tool_message = message_is_tool(serialized)
                        if (
                            is_message_stream
                            and stream_source_node == "verify"
                            and not is_tool_message
                        ):
                            # verify node is internal; user-facing output comes from
                            # ToolMessage(name="verification") only.
                            continue
                        serialized_stream = sanitize_message_for_stream(serialized)
                        message_type = serialized_stream.get("type")
                        if message_has_tool_calls(serialized) or is_tool_message:
                            scene_has_change = True
                        if message_type in {"human", "system"}:
                            continue
                        if message_type == "tool":
                            tool_event_payload = {
                                "messages": [serialized_stream],
                                "scene_has_change": scene_has_change,
                            }
                            yield f"data: {json.dumps(tool_event_payload, default=str)}\n\n"
                            continue
                        message_id = serialized_stream.get("id")
                        if isinstance(message_id, str) and message_id in existing_message_ids:
                            continue
                        delta = message_content_to_text(serialized_stream.get("content"))
                        if not delta:
                            continue
                        if (
                            not message_id
                            and not saw_new_message
                            and last_assistant_text
                            and delta == last_assistant_text
                        ):
                            continue
                        saw_new_message = True
                        if is_message_stream:
                            event_payload = {"delta": delta, "message_id": message_id}
                            yield f"data: {json.dumps(event_payload, default=str)}\n\n"
                        else:
                            message_event_payload = {"messages": [serialized_stream]}
                            yield f"data: {json.dumps(message_event_payload, default=str)}\n\n"

            done_payload = {"event": "done", "scene_has_change": scene_has_change}
            get_session_coordinator().touch_activity(request.thread_id)
        except HTTPException as e:
            detail = e.detail if isinstance(e.detail, str) else json.dumps(e.detail, ensure_ascii=False)
            error_event = {"error": detail, "status_code": e.status_code}
            yield f"data: {json.dumps(error_event)}\n\n"
            done_payload = {"event": "done", "scene_has_change": scene_has_change}
        except SessionResourceError as e:
            error_event = {
                "error": e.error,
                "reason": e.reason,
                "limits": e.limits,
                "in_use": e.in_use,
                "status_code": 503,
            }
            yield f"data: {json.dumps(error_event)}\n\n"
            done_payload = {"event": "done", "scene_has_change": scene_has_change}
        except Exception as e:
            log_event(
                "error",
                "stream_failed",
                {
                    "request_id": request_id,
                    "thread_id": request.thread_id,
                    "error": str(e),
                },
            )
            error_event = {"error": str(e)}
            yield f"data: {json.dumps(error_event)}\n\n"
            done_payload = {"event": "done", "scene_has_change": scene_has_change}
        finally:
            if done_payload is not None:
                yield f"data: {json.dumps(done_payload)}\n\n"
    
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    owner = getattr(resolution, "owner_worker_id", "") or ""
    epoch = getattr(resolution, "lease_epoch", None)
    if owner:
        headers["X-Session-Owner"] = owner
    if epoch is not None:
        headers["X-Session-Lease-Epoch"] = str(epoch)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=headers,
    )


@app.get("/scene/{thread_id}")
async def get_scene(thread_id: str, request: Request, response: Response):
    """
    Get current scene state for a thread.
    
    Args:
        thread_id: Thread identifier
        
    Returns:
        Scene objects and metadata
    """
    resolution, proxied = await _claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied
    try:
        settings = get_settings()
        if settings.blender_mode == "headless":
            manager = get_session_manager()
            session = manager.ensure(thread_id, "headless")
            request_timeout_seconds = _headless_timeout_seconds_for_session(settings, session)
            request_id = new_request_id(thread_id)
            start_time = start_timer()
            try:
                scene_info = await asyncio.wait_for(
                    asyncio.to_thread(send_blender_command_sync, "get_scene_info", None, thread_id),
                    timeout=request_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                elapsed_value = elapsed_ms(start_time)
                diagnostics = build_headless_diagnostics(
                    session=session,
                    request_id=request_id,
                    elapsed_ms_value=elapsed_value,
                    status="timeout",
                    target_ms=int(request_timeout_seconds * 1000),
                )
                log_event("error", "headless_scene_timeout", diagnostics)
                _restart_headless_session_after_timeout(thread_id)
                raise HTTPException(
                    status_code=504,
                    detail={"error": "Headless scene request timed out.", **diagnostics},
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
                    target_ms=int(request_timeout_seconds * 1000),
                )
                log_event("error", "headless_scene_failed", {**diagnostics, "error": str(exc)})
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
                    target_ms=int(request_timeout_seconds * 1000),
                )
                log_event("info", "headless_scene_ok", diagnostics)
        else:
            scene_info = await asyncio.to_thread(send_blender_command_sync, "get_scene_info", None, thread_id)
        scene_objects = SceneMemory.parse_scene_info(scene_info)
        objects = scene_info.get("objects", []) if isinstance(scene_info, dict) else []
        cameras = [obj.get("name") for obj in objects if obj.get("type") == "CAMERA" and obj.get("name")]
        payload = {
            "thread_id": thread_id,
            "scene_objects": scene_objects,
            "persistent_cameras": cameras,
            "iteration_count": 0
        }
        _set_owner_headers(response, resolution)
        return payload
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        if isinstance(e, SessionResourceError):
            raise HTTPException(status_code=503, detail=e.detail) from e
        settings = get_settings()
        if settings.blender_mode == "local-client":
            raise HTTPException(
                status_code=503,
                detail="Blender client not connected. Start the Blender addon or enable headless mode."
            ) from e
        raise


@app.get("/scene/{thread_id}/renders")
async def get_scene_renders(
    thread_id: str,
    request: Request,
    response: Response,
    mode: str = "rgb",
    include_local_work: bool = False,
):
    """
    Render all cameras in the current Blender scene and return processed image URLs.
    """
    resolution, proxied = await _claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied
    try:
        settings = get_settings()
        diagnostics = None
        request_timeout_seconds: float | None = None
        if settings.blender_mode == "headless":
            manager = get_session_manager()
            session = manager.ensure(thread_id, "headless")
            request_timeout_seconds = _headless_timeout_seconds_for_session(settings, session)
            request_id = new_request_id(thread_id)
            start_time = start_timer()
            try:
                scene_info = await asyncio.wait_for(
                    asyncio.to_thread(send_blender_command_sync, "get_scene_info", None, thread_id),
                    timeout=request_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                elapsed_value = elapsed_ms(start_time)
                diagnostics = build_headless_diagnostics(
                    session=session,
                    request_id=request_id,
                    elapsed_ms_value=elapsed_value,
                    status="timeout",
                    target_ms=int(request_timeout_seconds * 1000),
                )
                log_event("error", "headless_renders_scene_timeout", diagnostics)
                _restart_headless_session_after_timeout(thread_id)
                raise HTTPException(
                    status_code=504,
                    detail={"error": "Headless render request timed out.", **diagnostics},
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
                    target_ms=int(request_timeout_seconds * 1000),
                )
                log_event("error", "headless_renders_scene_failed", {**diagnostics, "error": str(exc)})
                raise HTTPException(
                    status_code=500,
                    detail={"error": str(exc), **diagnostics},
                ) from exc
        else:
            scene_info = await asyncio.to_thread(send_blender_command_sync, "get_scene_info", None, thread_id)

        start_time = start_timer()
        renders: list[dict[str, str]] = []
        if mode == "rgb":
            renders = await _render_scene_level_views(
                thread_id=thread_id,
                is_headless=settings.blender_mode == "headless",
                request_timeout_seconds=request_timeout_seconds,
            )

        # Backward-compatible fallback: if scene-level strategy returns no output,
        # render from any existing camera objects in scene info.
        if not renders:
            objects = scene_info.get("objects", []) if isinstance(scene_info, dict) else []
            cameras = [obj.get("name") for obj in objects if obj.get("type") == "CAMERA"]
            for camera_name in cameras:
                if not camera_name:
                    continue
                temp_path = os.path.join(
                    tempfile.gettempdir(),
                    f"blender_render_{camera_name}_{int(time.time() * 1000)}.png"
                )
                render_call = asyncio.to_thread(
                    send_blender_command_sync,
                    "render_from_camera",
                    {
                        "camera_name": camera_name,
                        "object_names": None,
                        "mode": mode,
                        "filepath": temp_path
                    },
                    thread_id
                )
                if settings.blender_mode == "headless":
                    try:
                        result = await asyncio.wait_for(render_call, timeout=request_timeout_seconds)
                    except asyncio.TimeoutError as exc:
                        elapsed_value = elapsed_ms(start_time)
                        diagnostics = build_headless_diagnostics(
                            session=session,
                            request_id=request_id,
                            elapsed_ms_value=elapsed_value,
                            status="timeout",
                            target_ms=int(request_timeout_seconds * 1000),
                        )
                        log_event(
                            "error",
                            "headless_renders_timeout",
                            {**diagnostics, "camera_name": camera_name},
                        )
                        _restart_headless_session_after_timeout(thread_id)
                        raise HTTPException(
                            status_code=504,
                            detail={"error": "Headless render request timed out.", **diagnostics},
                        ) from exc
                else:
                    result = await render_call
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
                renders.append({
                    "camera_name": camera_name,
                    "image_url": image_url
                })

        if include_local_work:
            existing_camera_names = {
                entry.get("camera_name")
                for entry in renders
                if isinstance(entry, dict) and isinstance(entry.get("camera_name"), str)
            }
            local_work_camera_names: list[str] = []
            list_call = asyncio.to_thread(
                send_blender_command_sync,
                "get_camera_manager_cameras",
                {
                    "only_local": True,
                    "include_invalid": False,
                },
                thread_id,
            )
            try:
                if settings.blender_mode == "headless":
                    camera_listing = await asyncio.wait_for(list_call, timeout=request_timeout_seconds)
                else:
                    camera_listing = await list_call
            except asyncio.TimeoutError as exc:
                elapsed_value = elapsed_ms(start_time)
                diagnostics = build_headless_diagnostics(
                    session=session,
                    request_id=request_id,
                    elapsed_ms_value=elapsed_value,
                    status="timeout",
                    target_ms=int(request_timeout_seconds * 1000),
                )
                log_event("error", "headless_local_work_camera_list_timeout", diagnostics)
                _restart_headless_session_after_timeout(thread_id)
                raise HTTPException(
                    status_code=504,
                    detail={"error": "Headless local-work camera listing timed out.", **diagnostics},
                ) from exc
            except Exception as exc:
                log_event(
                    "warning",
                    "local_work_camera_list_failed",
                    {"thread_id": thread_id, "error": str(exc)},
                )
                camera_listing = {}

            if isinstance(camera_listing, dict):
                raw_camera_entries = camera_listing.get("cameras")
                if isinstance(raw_camera_entries, list):
                    for entry in raw_camera_entries:
                        if not isinstance(entry, dict):
                            continue
                        camera_name = entry.get("camera_name")
                        camera_kind = entry.get("camera_kind")
                        if (
                            isinstance(camera_name, str)
                            and camera_name
                            and camera_name not in existing_camera_names
                            and camera_kind == "local_work"
                        ):
                            local_work_camera_names.append(camera_name)

            for camera_name in local_work_camera_names:
                temp_path = os.path.join(
                    tempfile.gettempdir(),
                    f"blender_render_local_work_{camera_name}_{int(time.time() * 1000)}.png"
                )
                render_call = asyncio.to_thread(
                    send_blender_command_sync,
                    "render_from_camera",
                    {
                        "camera_name": camera_name,
                        "object_names": None,
                        "mode": mode,
                        "filepath": temp_path
                    },
                    thread_id
                )
                if settings.blender_mode == "headless":
                    try:
                        result = await asyncio.wait_for(render_call, timeout=request_timeout_seconds)
                    except asyncio.TimeoutError as exc:
                        elapsed_value = elapsed_ms(start_time)
                        diagnostics = build_headless_diagnostics(
                            session=session,
                            request_id=request_id,
                            elapsed_ms_value=elapsed_value,
                            status="timeout",
                            target_ms=int(request_timeout_seconds * 1000),
                        )
                        log_event(
                            "error",
                            "headless_local_work_render_timeout",
                            {**diagnostics, "camera_name": camera_name},
                        )
                        _restart_headless_session_after_timeout(thread_id)
                        raise HTTPException(
                            status_code=504,
                            detail={"error": "Headless local-work camera render timed out.", **diagnostics},
                        ) from exc
                    except Exception as exc:
                        log_event(
                            "warning",
                            "local_work_render_failed",
                            {"thread_id": thread_id, "camera_name": camera_name, "error": str(exc)},
                        )
                        continue
                else:
                    try:
                        result = await render_call
                    except Exception as exc:
                        log_event(
                            "warning",
                            "local_work_render_failed",
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
                renders.append({
                    "camera_name": camera_name,
                    "image_url": image_url,
                })
                existing_camera_names.add(camera_name)

        if settings.blender_mode == "headless":
            elapsed_value = elapsed_ms(start_time)
            diagnostics = build_headless_diagnostics(
                session=session,
                request_id=request_id,
                elapsed_ms_value=elapsed_value,
                status="ok",
                target_ms=int(request_timeout_seconds * 1000),
            )
            log_event("info", "headless_renders_ok", diagnostics)
        payload = {
            "thread_id": thread_id,
            "renders": renders,
            "diagnostics": diagnostics,
            "include_local_work": bool(include_local_work),
        }
        _set_owner_headers(response, resolution)
        return payload
    except Exception as e:
        import traceback
        traceback.print_exc()
        if isinstance(e, HTTPException):
            raise
        if isinstance(e, SessionResourceError):
            raise HTTPException(status_code=503, detail=e.detail) from e
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/scene/{thread_id}/gltf")
async def get_scene_gltf(thread_id: str, request: Request):
    """
    Export current Blender scene to GLB and return the binary.
    """
    resolution, proxied = await _claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied
    try:
        settings = get_settings()
        temp_path = os.path.join(
            tempfile.gettempdir(),
            f"scene_{thread_id}_{int(time.time() * 1000)}.glb"
        )
        export_code = (
            "import bpy\n"
            f"bpy.ops.export_scene.gltf(filepath=r\"{temp_path}\", "
            "export_format='GLB', export_apply=True)\n"
        )
        if settings.blender_mode == "headless":
            manager = get_session_manager()
            session = manager.ensure(thread_id, "headless")
            request_timeout_seconds = _headless_timeout_seconds_for_session(settings, session)
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
                    timeout=request_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                elapsed_value = elapsed_ms(start_time)
                diagnostics = build_headless_diagnostics(
                    session=session,
                    request_id=request_id,
                    elapsed_ms_value=elapsed_value,
                    status="timeout",
                    target_ms=int(request_timeout_seconds * 1000),
                )
                log_event("error", "headless_gltf_timeout", diagnostics)
                _restart_headless_session_after_timeout(thread_id)
                raise HTTPException(
                    status_code=504,
                    detail={"error": "Headless GLTF export timed out.", **diagnostics},
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
                    target_ms=int(request_timeout_seconds * 1000),
                )
                log_event("error", "headless_gltf_failed", {**diagnostics, "error": str(exc)})
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
                    target_ms=int(request_timeout_seconds * 1000),
                )
                log_event("info", "headless_gltf_ok", diagnostics)
        else:
            await asyncio.to_thread(send_blender_command_sync, "execute_code", {"code": export_code}, thread_id)

        # Wait for file to be written with retries
        max_retries = 10
        retry_delay = 0.2
        temp_dir = tempfile.gettempdir()
        log_event("info", "gltf_export_waiting", {
            "thread_id": thread_id,
            "temp_path": temp_path,
            "temp_dir": temp_dir,
            "max_retries": max_retries
        })
        
        for attempt in range(max_retries):
            if os.path.exists(temp_path):
                file_size = os.path.getsize(temp_path)
                if file_size > 0:
                    log_event("info", "gltf_export_file_ready", {
                        "thread_id": thread_id,
                        "attempt": attempt + 1,
                        "file_size": file_size
                    })
                    break
                else:
                    log_event("warning", "gltf_export_file_empty", {
                        "thread_id": thread_id,
                        "attempt": attempt + 1
                    })
            else:
                log_event("debug", "gltf_export_file_not_found", {
                    "thread_id": thread_id,
                    "attempt": attempt + 1
                })
            
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
        
        if not os.path.exists(temp_path):
            log_event("error", "gltf_export_failed_not_found", {
                "thread_id": thread_id,
                "temp_path": temp_path,
                "temp_dir": temp_dir,
                "temp_dir_exists": os.path.exists(temp_dir),
                "temp_dir_writable": os.access(temp_dir, os.W_OK) if os.path.exists(temp_dir) else False
            })
            raise Exception("GLB export failed: file not found")
        
        if os.path.getsize(temp_path) == 0:
            log_event("error", "gltf_export_failed_empty", {
                "thread_id": thread_id,
                "temp_path": temp_path
            })
            raise Exception("GLB export failed: file is empty")

        with open(temp_path, "rb") as f:
            glb_data = f.read()
        try:
            os.remove(temp_path)
        except OSError:
            pass

        filename = f"scene-{thread_id}.glb"
        result = Response(
            content=glb_data,
            media_type="model/gltf-binary",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
        _set_owner_headers(result, resolution)
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        if isinstance(e, HTTPException):
            raise
        if isinstance(e, SessionResourceError):
            raise HTTPException(status_code=503, detail=e.detail) from e
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/scene/{thread_id}/blend")
async def get_scene_blend(thread_id: str, request: Request):
    """
    Export current Blender scene to .blend file and return the binary.
    """
    resolution, proxied = await _claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied
    try:
        settings = get_settings()
        temp_path = os.path.join(
            tempfile.gettempdir(),
            f"scene_{thread_id}_{int(time.time() * 1000)}.blend"
        )
        export_code = (
            "import bpy\n"
            f"bpy.ops.wm.save_as_mainfile(filepath=r\"{temp_path}\", copy=True)\n"
        )
        if settings.blender_mode == "headless":
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
                    timeout=settings.headless_request_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                elapsed_value = elapsed_ms(start_time)
                diagnostics = build_headless_diagnostics(
                    session=session,
                    request_id=request_id,
                    elapsed_ms_value=elapsed_value,
                    status="timeout",
                    target_ms=settings.headless_request_timeout_seconds * 1000,
                )
                log_event("error", "headless_blend_timeout", diagnostics)
                raise HTTPException(
                    status_code=504,
                    detail={"error": "Headless BLEND export timed out.", **diagnostics},
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
                    target_ms=settings.headless_request_timeout_seconds * 1000,
                )
                log_event("error", "headless_blend_failed", {**diagnostics, "error": str(exc)})
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
                    target_ms=settings.headless_request_timeout_seconds * 1000,
                )
                log_event("info", "headless_blend_ok", diagnostics)
        else:
            await asyncio.to_thread(send_blender_command_sync, "execute_code", {"code": export_code}, thread_id)

        # Wait for file to be written with retries
        max_retries = 10
        retry_delay = 0.2
        for attempt in range(max_retries):
            if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                break
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
        
        if not os.path.exists(temp_path):
            raise Exception("BLEND export failed: file not found")
        
        if os.path.getsize(temp_path) == 0:
            raise Exception("BLEND export failed: file is empty")

        with open(temp_path, "rb") as f:
            blend_data = f.read()
        try:
            os.remove(temp_path)
        except OSError:
            pass

        filename = f"scene-{thread_id}.blend"
        result = Response(
            content=blend_data,
            media_type="application/x-blender",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
        _set_owner_headers(result, resolution)
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        if isinstance(e, HTTPException):
            raise
        if isinstance(e, SessionResourceError):
            raise HTTPException(status_code=503, detail=e.detail) from e
        raise HTTPException(status_code=500, detail=str(e))


def _blend_file_category(relative_path: str) -> str:
    if relative_path == "scene.blend":
        return "session"
    if relative_path.startswith("snapshots/"):
        return "snapshot"
    return "other"


@app.get("/scene/{thread_id}/blends", response_model=BlendFileListResponse)
async def list_scene_blends(thread_id: str, request: Request, response: Response):
    """
    List persisted .blend files available for this thread.
    """
    resolution, proxied = await _claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied

    settings = get_settings()
    if settings.blender_mode == "headless":
        try:
            get_session_manager().persist_session_blend(
                thread_id,
                min_interval_seconds=8.0,
            )
        except Exception:
            pass

    storage_dir = _resolve_thread_storage_dir(thread_id)
    files_with_mtime: list[tuple[float, BlendFileEntry]] = []
    if storage_dir.exists():
        root = storage_dir.resolve()
        for candidate in storage_dir.rglob("*.blend"):
            if not candidate.is_file():
                continue
            try:
                resolved = candidate.resolve()
                relative_path = resolved.relative_to(root).as_posix()
                stat = resolved.stat()
            except Exception:
                continue
            files_with_mtime.append(
                (
                    float(stat.st_mtime),
                    BlendFileEntry(
                        relative_path=relative_path,
                        filename=resolved.name,
                        size_bytes=int(stat.st_size),
                        modified_at=datetime.fromtimestamp(
                            stat.st_mtime,
                            tz=timezone.utc,
                        ).isoformat(),
                        category=_blend_file_category(relative_path),
                    ),
                )
            )

    files_with_mtime.sort(key=lambda item: (-item[0], item[1].relative_path))
    payload = BlendFileListResponse(
        thread_id=thread_id,
        files=[entry for _, entry in files_with_mtime],
    )
    _set_owner_headers(response, resolution)
    return payload


@app.get("/scene/{thread_id}/blends/download")
async def download_scene_blend_file(thread_id: str, path: str, request: Request):
    """
    Download one persisted .blend file for this thread.
    """
    resolution, proxied = await _claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied

    normalized = path.strip().replace("\\", "/").lstrip("/")
    if not normalized or normalized.endswith("/") or "\x00" in normalized:
        raise HTTPException(status_code=400, detail="Invalid blend path.")

    path_parts = [part for part in normalized.split("/") if part]
    if not path_parts or any(part in {".", ".."} for part in path_parts):
        raise HTTPException(status_code=400, detail="Invalid blend path.")

    storage_dir = _resolve_thread_storage_dir(thread_id)
    root = storage_dir.resolve()
    target = (root / "/".join(path_parts)).resolve()
    if target != root and root not in target.parents:
        raise HTTPException(status_code=400, detail="Invalid blend path.")
    if target.suffix.lower() != ".blend":
        raise HTTPException(status_code=400, detail="Only .blend files are supported.")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Blend file not found.")

    with open(target, "rb") as handle:
        blend_data = handle.read()

    result = Response(
        content=blend_data,
        media_type="application/x-blender",
        headers={"Content-Disposition": f'attachment; filename="{target.name}"'},
    )
    _set_owner_headers(result, resolution)
    return result


@app.post("/threads/{thread_id}/reference-images", response_model=ReferenceImageListResponse)
async def upload_reference_images(
    thread_id: str,
    request: Request,
    response: Response,
    images: list[UploadFile] = File(...),
):
    """
    Upload reference images for a thread.
    """
    resolution, proxied = await _claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied
    settings = get_settings()
    if not images:
        raise HTTPException(status_code=400, detail="No images provided.")
    if len(images) > settings.reference_image_max_count:
        raise HTTPException(status_code=400, detail="Too many images uploaded.")

    uploads: list[tuple[str, str, bytes]] = []
    for image in images:
        payload = await image.read()
        uploads.append((image.filename or "reference.png", image.content_type or "image/unknown", payload))

    memory = get_reference_image_memory()
    try:
        stored = memory.add_images(thread_id=thread_id, uploads=uploads)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = ReferenceImageListResponse(
        thread_id=thread_id,
        images=[serialize_reference_image(image) for image in stored],
    )
    _set_owner_headers(response, resolution)
    return payload


@app.get("/threads/{thread_id}/reference-images", response_model=ReferenceImageListResponse)
async def list_reference_images(thread_id: str, request: Request, response: Response):
    """
    List reference images for a thread.
    """
    resolution, proxied = await _claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied
    memory = get_reference_image_memory()
    images = memory.list_images(thread_id)
    payload = ReferenceImageListResponse(
        thread_id=thread_id,
        images=[serialize_reference_image(image) for image in images],
    )
    _set_owner_headers(response, resolution)
    return payload


@app.get("/todos/{thread_id}")
async def get_todos(thread_id: str, request: Request, response: Response):
    """
    Get current todos for a thread.
    
    Args:
        thread_id: Thread identifier
        
    Returns:
        List of todos with their status
    """
    resolution, proxied = await _claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied
    try:
        agent = await get_agent(thread_id)
        config = {"configurable": {"thread_id": thread_id}}
        
        state = await agent.aget_state(config)
        
        payload = {
            "thread_id": thread_id,
            "todos": state.values.get("todos", [])
        }
        _set_owner_headers(response, resolution)
        return payload
        
    except Exception as e:
        if isinstance(e, SessionResourceError):
            raise HTTPException(status_code=503, detail=e.detail) from e
        raise HTTPException(status_code=500, detail=str(e))


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


def _collect_headless_runtime_entries(*, frontend_client_id: str) -> list[dict[str, Any]]:
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
        client_id = _resolve_thread_frontend_client(thread_id)
        existing = entries_by_thread.get(thread_id)
        blender_port = session.port if session.port is not None else (existing or {}).get("blender_port")
        mcp_port = session.mcp_port if session.mcp_port is not None else (existing or {}).get("mcp_port")
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


def _ensure_frontend_client_can_manage_thread(thread_id: str, request_client_id: str) -> None:
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


def _release_thread_runtime(thread_id: str) -> dict[str, Any]:
    """Release headless runtime resources while keeping thread identity/history intact."""
    settings = get_settings()
    coordinator = get_session_coordinator()
    manager = get_session_manager()
    session = manager.get(thread_id)
    result: dict[str, Any] = {"thread_id": thread_id, "released": False, "cleaned": []}

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
        result["released"] = True
    else:
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
            result["released"] = True
        if meta_mcp_port is not None:
            coordinator.release_port(
                host=meta_mcp_host,
                kind="mcp",
                port=meta_mcp_port,
            )
            result["cleaned"].append(f"mcp_port:{meta_mcp_port}")
            result["released"] = True

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


@app.get("/threads")
async def list_threads():
    """
    List all active threads (sessions).
    
    Returns:
        List of thread IDs
    """
    coordinator = get_session_coordinator()
    threads = coordinator.list_threads(limit=1000)
    return {"threads": threads}


@app.get("/headless/session-capacity", response_model=HeadlessSessionCapacityResponse)
async def get_headless_session_capacity(request: Request):
    settings = get_settings()
    client_id = _resolve_frontend_client_id(request)
    quota = settings.resolve_frontend_session_quota(client_id)
    entries = _collect_headless_runtime_entries(frontend_client_id=client_id)
    occupying_threads = [entry for entry in entries if entry.get("occupying_resources")]
    return HeadlessSessionCapacityResponse(
        blender_mode=settings.blender_mode,
        frontend_client_id=client_id,
        quota=quota,
        in_use=len(occupying_threads),
        occupying_threads=[HeadlessRuntimeThreadEntry(**entry) for entry in occupying_threads],
    )


def _teardown_thread_session(thread_id: str) -> dict[str, Any]:
    """Fully tear down a headless session: kill processes, release ports, clean caches."""
    settings = get_settings()
    coordinator = get_session_coordinator()
    manager = get_session_manager()
    session = manager.get(thread_id)
    result: dict[str, Any] = {"thread_id": thread_id, "cleaned": []}

    def _safe_int(raw: object) -> int | None:
        try:
            if raw is None or raw == "":
                return None
            return int(raw)
        except (TypeError, ValueError):
            return None

    if session is not None and session.mode == "headless":
        headless_host_snapshot = session.host or settings.blender_host
        headless_port_snapshot = session.port
        mcp_host_snapshot = session.mcp_host or os.getenv("BLENDER_MCP_HOST", "localhost")
        mcp_port_snapshot = session.mcp_port

        # Persist blend before teardown (best-effort).
        try:
            manager.persist_session_blend(thread_id)
            result["cleaned"].append("blend_persisted")
        except Exception:
            pass

        # Terminate Blender & MCP processes.
        manager.terminate_session_processes(thread_id, timeout=5.0)
        result["cleaned"].append("processes_terminated")

        # Release headless port.
        coordinator.release_port(
            host=headless_host_snapshot,
            kind="headless",
            port=headless_port_snapshot,
        )
        if headless_port_snapshot:
            result["cleaned"].append(f"headless_port:{headless_port_snapshot}")

        # Release MCP port.
        coordinator.release_port(
            host=mcp_host_snapshot,
            kind="mcp",
            port=mcp_port_snapshot,
        )
        if mcp_port_snapshot:
            result["cleaned"].append(f"mcp_port:{mcp_port_snapshot}")

        # Remove from in-memory session manager.
        manager.remove(thread_id)
        result["cleaned"].append("session_removed")
    elif session is None:
        session_meta = coordinator.get_session_meta(thread_id) or {}
        meta_headless_host = str(session_meta.get("host") or settings.blender_host)
        meta_headless_port = _safe_int(session_meta.get("blender_port"))
        meta_mcp_host = str(session_meta.get("mcp_host") or os.getenv("BLENDER_MCP_HOST", "localhost"))
        meta_mcp_port = _safe_int(session_meta.get("mcp_port"))
        if meta_headless_port is not None:
            coordinator.release_port(
                host=meta_headless_host,
                kind="headless",
                port=meta_headless_port,
            )
            result["cleaned"].append(f"headless_port:{meta_headless_port}")
        if meta_mcp_port is not None:
            coordinator.release_port(
                host=meta_mcp_host,
                kind="mcp",
                port=meta_mcp_port,
            )
            result["cleaned"].append(f"mcp_port:{meta_mcp_port}")

    # Clean up cached agent graph (frees MCP client references).
    if thread_id in _agent_graphs_by_thread:
        del _agent_graphs_by_thread[thread_id]
        result["cleaned"].append("agent_graph")

    # Clean up cached VLM config.
    with _thread_vlm_lock:
        if thread_id in _thread_vlm_configs:
            del _thread_vlm_configs[thread_id]
            result["cleaned"].append("vlm_config")

    # Clean up Redis session metadata.
    coordinator.update_session_runtime_fields(thread_id, {"status": "closed"})
    coordinator.delete_session_metadata(thread_id)
    result["cleaned"].append("redis_metadata")
    _clear_frontend_client_binding(thread_id)
    result["cleaned"].append("frontend_client_binding")

    return result


@app.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str, request: Request):
    """
    Delete a thread and tear down all associated resources.

    Kills headless Blender/MCP processes, releases ports, and cleans up
    Redis metadata and in-memory caches.
    """
    resolution, proxied = await _claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied

    try:
        result = await asyncio.to_thread(_teardown_thread_session, thread_id)
        log_event(
            "info",
            "thread_deleted",
            {"thread_id": thread_id, "cleaned": result.get("cleaned", [])},
        )
        return result
    except Exception as exc:
        log_event(
            "error",
            "thread_delete_failed",
            {"thread_id": thread_id, "error": str(exc)},
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/threads/{thread_id}/release-runtime", response_model=ReleaseRuntimeResponse)
async def release_thread_runtime(thread_id: str, request: Request, response: Response):
    resolution, proxied = await _claim_or_proxy_request(request=request, thread_id=thread_id)
    if proxied is not None:
        return proxied
    try:
        request_client_id = _resolve_frontend_client_id(request)
        _ensure_frontend_client_can_manage_thread(thread_id, request_client_id)
        result = await asyncio.to_thread(_release_thread_runtime, thread_id)
        _set_owner_headers(response, resolution)
        return ReleaseRuntimeResponse(
            thread_id=thread_id,
            released=bool(result.get("released", False)),
            cleaned=[str(item) for item in result.get("cleaned", [])],
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
