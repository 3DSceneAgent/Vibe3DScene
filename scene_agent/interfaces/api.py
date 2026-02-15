"""
FastAPI REST server for the 3D scene agent.
Provides HTTP endpoints and WebSocket support with streaming.
"""
import asyncio
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse, Response, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage
from scene_agent.blender.connection import BlenderConnection

from scene_agent.agent.graph import create_agent_graph
from scene_agent.blender.session_manager import (
    allocate_headless_port,
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
_SUPPORTED_VLM_PROVIDERS = ("openai", "anthropic", "gemini")
_VLM_PROVIDER_DISPLAY_NAMES = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Gemini",
}


def _normalize_optional(value: str | None, *, lower: bool = False) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized.lower() if lower else normalized


def _headless_timeout_seconds_for_session(settings: Any, session: Any | None = None) -> float:
    # Scene/render export operations are frequently heavier than tool RPCs.
    base_timeout = max(30.0, float(getattr(settings, "headless_request_timeout_seconds", 15)))
    if session is None:
        return base_timeout
    session_status = getattr(session, "status", None)
    session_process = getattr(session, "process", None)
    # Cold-start requests need extra budget for launching Blender/MCP.
    if session_status != "ready" or session_process is None:
        startup_timeout = max(1.0, float(getattr(settings, "blender_headless_startup_timeout", 10)))
        return base_timeout + startup_timeout
    return base_timeout


def _restart_headless_session_after_timeout(thread_id: str) -> None:
    try:
        manager = get_session_manager()
        manager.restart_session_processes(thread_id, timeout=3.0)
        log_event(
            "warning",
            "headless_session_restarted_after_timeout",
            {"thread_id": thread_id},
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

    manager = get_session_manager()
    session = manager.ensure(thread_id, "headless")
    manager.ensure_session_storage(thread_id)
    host = os.getenv("BLENDER_HEADLESS_HOST", settings.blender_host)
    base_port = int(os.getenv("BLENDER_HEADLESS_BASE_PORT", "9876"))
    port_range = int(os.getenv("BLENDER_HEADLESS_PORT_RANGE", "16"))
    if session.port is None:
        used_ports = {item.port for item in manager.list_sessions() if item.port}
        port = allocate_headless_port(
            thread_id,
            base_port,
            port_range,
            used_ports=used_ports,
        )
        manager.set_endpoint(thread_id, host, port)
    else:
        port = session.port

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
    print("Building headless command and args...")
    print(f"Command: {command}")
    print(f"Args: {args}")

    with session.lock:
        print("Lock acquired.")
        connection = session.connection
        if not isinstance(connection, BlenderConnection):
            print("Creating new connection for the thread...")
            connection = BlenderConnection(host=host, port=port)
            session.connection = connection
        if not connection.connect():
            print("Starting headless process...")
            start_headless_process(session, command, args, env=headless_env)
            
            # 等待进程启动并监控
            deadline = time.time() + settings.blender_headless_startup_timeout
            last_check = time.time()
            connected = False
            
            while time.time() < deadline:
                # 定期检查进程状态
                if time.time() - last_check > 2.0:
                    if session.process:
                        if session.process.poll() is not None:
                            error_msg = f"Blender process exited with code {session.process.returncode}"
                            if session.log_path and os.path.exists(session.log_path):
                                with open(session.log_path, 'r') as f:
                                    log_content = f.read()
                                error_msg += f"\n\nProcess Log:\n{log_content[-2000:]}"
                            manager.set_error(thread_id, error_msg)
                            raise Exception(error_msg)
                        print(f"  Process still running (PID: {session.process.pid}), waiting for connection...")
                    last_check = time.time()
                
                if connection.connect():
                    print(f"Successfully connected to Blender on {host}:{port}")
                    connected = True
                    break
                time.sleep(0.5)
            
            if not connected:
                error_msg = f"Connection timeout after {settings.blender_headless_startup_timeout}s"
                if session.log_path and os.path.exists(session.log_path):
                    with open(session.log_path, 'r') as f:
                        log_content = f.read()
                    error_msg += f"\n\nProcess Log:\n{log_content[-2000:]}"
                manager.set_error(thread_id, error_msg)
                raise Exception(error_msg)
        else:
            print("Connection already established.")

        if not connection.sock:
            error_message = "Could not connect to headless Blender session."
            manager.set_error(thread_id, error_message)
            raise Exception(error_message)

        manager.set_ready(thread_id, connection)
        print(f"Session ready for thread: {thread_id}")

    return connection


def send_blender_command_sync(
    command_type: str,
    params: Dict[str, Any] | None = None,
    thread_id: str | None = None
) -> Dict[str, Any]:
    global _blender_connection
    settings = get_settings()
    if settings.blender_mode == "headless" and thread_id:
        manager = get_session_manager()
        session = manager.ensure(thread_id, "headless")
        last_error: Exception | None = None
        for _attempt in range(2):
            blender = get_blender_connection_for_thread(thread_id)
            with session.lock:
                try:
                    return blender.send_command(command_type, params)
                except Exception as exc:
                    last_error = exc
                    try:
                        blender.disconnect()
                    except Exception:
                        pass
                    session.connection = None
                    manager.set_error(thread_id, f"{command_type} failed: {exc}")
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


async def _idle_session_sweeper() -> None:
    while True:
        settings = get_settings()
        interval = max(1, settings.session_sweep_interval_seconds)
        await asyncio.sleep(interval)
        manager = get_session_manager()
        idle_sessions = manager.get_idle_sessions()
        for session in idle_sessions:
            stopped, persisted = await asyncio.to_thread(
                manager.shutdown_if_idle,
                session.session_id,
            )
            if not stopped:
                continue
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
        settings = get_settings()
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
        print("✓ Agent initialized successfully")
    except Exception as e:
        print(f"✗ Failed to initialize agent: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Ensure headless processes are cleaned up on shutdown."""
    global _idle_sweeper_task
    try:
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
        manager.shutdown_all()
        _agent_graphs_by_thread.clear()
        with _thread_vlm_lock:
            _thread_vlm_configs.clear()
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
            "todos": "GET /todos/{thread_id}",
            "threads": "GET /threads",
            "websocket": "WS /ws"
        }
    }


@app.get("/health")
async def healthcheck():
    """Healthcheck endpoint."""
    settings = get_settings()
    return {
        "status": "ok",
        "timestamp": time.time(),
        "blender_mode": settings.blender_mode,
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
async def get_mcp_tools(thread_id: str):
    settings = get_settings()
    try:
        agent = await get_agent(thread_id)
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
    return MCPToolsResponse(
        thread_id=thread_id,
        loaded=len(tools) > 0,
        tool_count=len(tools),
        tools=tools,
        tool_hints=tool_hints,
        blender_mode=settings.blender_mode,
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Chat with the agent (non-streaming).
    
    Args:
        request: ChatRequest with message and thread_id
        
    Returns:
        ChatResponse with agent's response and todos
    """
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
        
        # Extract response
        last_message = result["messages"][-1]
        response_text = last_message.content if hasattr(last_message, "content") else str(last_message)
        
        return ChatResponse(
            response=response_text,
            thread_id=request.thread_id,
            todos=result.get("todos", [])
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    Chat with the agent (streaming via Server-Sent Events).
    
    Args:
        request: ChatRequest with message and thread_id
        
    Returns:
        StreamingResponse with SSE events
    """
    async def event_generator():
        import time 
        settings = get_settings()
        timeout_seconds = max(1, settings.api_stream_timeout_seconds)
        keepalive_interval = min(15.0, max(5.0, timeout_seconds / 4))
        deadline = time.time() + timeout_seconds
        request_id = f"{request.thread_id}:{int(time.time() * 1000)}"
        saw_message_stream = False
        saw_new_message = False
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
                stream_mode=["messages", "values"]
            )
            next_event_task: asyncio.Task | None = None
            while True:
                if time.time() >= deadline:
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

                mode, payload = normalize_stream_event(event)
                is_message_stream = mode == "messages" or hasattr(mode, "content") or hasattr(mode, "type")
                if is_message_stream:
                    saw_message_stream = True
                if isinstance(payload, dict) and "todos" in payload and payload["todos"]:
                    yield f"data: {json.dumps({'todos': payload['todos']}, default=str)}\n\n"

                messages = None
                if is_message_stream:
                    messages = payload if mode == "messages" else [mode]
                elif isinstance(payload, dict) and "messages" in payload:
                    if not saw_message_stream:
                        messages = payload["messages"]

                if messages:
                    if not isinstance(messages, list):
                        messages = [messages]
                    for message in messages:
                        serialized = serialize_message(message)
                        serialized_stream = sanitize_message_for_stream(serialized)
                        message_type = serialized_stream.get("type")
                        if message_has_tool_calls(serialized) or message_is_tool(serialized):
                            scene_has_change = True
                        if message_type in {"human", "system"}:
                            continue
                        if message_type == "tool":
                            payload = {"messages": [serialized_stream], "scene_has_change": scene_has_change}
                            yield f"data: {json.dumps(payload, default=str)}\n\n"
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
                            payload = {"messages": [serialized_stream]}
                            yield f"data: {json.dumps(payload, default=str)}\n\n"

            done_payload = {"event": "done", "scene_has_change": scene_has_change}
        except HTTPException as e:
            detail = e.detail if isinstance(e.detail, str) else json.dumps(e.detail, ensure_ascii=False)
            error_event = {"error": detail, "status_code": e.status_code}
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
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/scene/{thread_id}")
async def get_scene(thread_id: str):
    """
    Get current scene state for a thread.
    
    Args:
        thread_id: Thread identifier
        
    Returns:
        Scene objects and metadata
    """
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
        return {
            "thread_id": thread_id,
            "scene_objects": scene_objects,
            "persistent_cameras": cameras,
            "iteration_count": 0
        }
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        settings = get_settings()
        if settings.blender_mode == "local-client":
            raise HTTPException(
                status_code=503,
                detail="Blender client not connected. Start the Blender addon or enable headless mode."
            ) from e
        raise


@app.get("/scene/{thread_id}/renders")
async def get_scene_renders(thread_id: str, mode: str = "rgb"):
    """
    Render all cameras in the current Blender scene and return processed image URLs.
    """
    try:
        settings = get_settings()
        diagnostics = None
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
        objects = scene_info.get("objects", [])
        cameras = [obj.get("name") for obj in objects if obj.get("type") == "CAMERA"]

        renders = []
        start_time = start_timer()
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
        return {"thread_id": thread_id, "renders": renders, "diagnostics": diagnostics}
    except Exception as e:
        import traceback
        traceback.print_exc()
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/scene/{thread_id}/gltf")
async def get_scene_gltf(thread_id: str):
    """
    Export current Blender scene to GLB and return the binary.
    """
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
        return Response(
            content=glb_data,
            media_type="model/gltf-binary",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/scene/{thread_id}/blend")
async def get_scene_blend(thread_id: str):
    """
    Export current Blender scene to .blend file and return the binary.
    """
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
        return Response(
            content=blend_data,
            media_type="application/x-blender",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/threads/{thread_id}/reference-images", response_model=ReferenceImageListResponse)
async def upload_reference_images(thread_id: str, images: list[UploadFile] = File(...)):
    """
    Upload reference images for a thread.
    """
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

    return ReferenceImageListResponse(
        thread_id=thread_id,
        images=[serialize_reference_image(image) for image in stored],
    )


@app.get("/threads/{thread_id}/reference-images", response_model=ReferenceImageListResponse)
async def list_reference_images(thread_id: str):
    """
    List reference images for a thread.
    """
    memory = get_reference_image_memory()
    images = memory.list_images(thread_id)
    return ReferenceImageListResponse(
        thread_id=thread_id,
        images=[serialize_reference_image(image) for image in images],
    )


@app.get("/todos/{thread_id}")
async def get_todos(thread_id: str):
    """
    Get current todos for a thread.
    
    Args:
        thread_id: Thread identifier
        
    Returns:
        List of todos with their status
    """
    try:
        agent = await get_agent(thread_id)
        config = {"configurable": {"thread_id": thread_id}}
        
        state = await agent.aget_state(config)
        
        return {
            "thread_id": thread_id,
            "todos": state.values.get("todos", [])
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/threads")
async def list_threads():
    """
    List all active threads (sessions).
    
    Returns:
        List of thread IDs
    """
    # TODO: Implement thread listing from checkpointer
    return {
        "threads": [],
        "message": "Thread listing not yet implemented"
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time bidirectional communication.
    
    Args:
        websocket: WebSocket connection
    """
    await websocket.accept()
    thread_id = "websocket-session"
    
    try:
        agent = await get_agent(thread_id)
        config = {"configurable": {"thread_id": thread_id}}
        
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            message_data = json.loads(data)
            
            if "message" in message_data:
                # Stream response back to client
                async for event in agent.astream(
                    {"messages": [HumanMessage(content=message_data["message"])], "thread_id": thread_id},
                    config=config,
                    stream_mode=["messages", "values"]
                ):
                    await websocket.send_json(serialize_event(event), default=str)
            
    except WebSocketDisconnect:
        print(f"WebSocket disconnected: {thread_id}")
    except Exception as e:
        await websocket.send_json({"error": str(e)})
        await websocket.close()


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
    settings = get_settings()
    worker_count = workers if workers is not None else settings.api_workers
    worker_count = max(1, worker_count)
    if worker_count > 1:
        print(
            "Warning: API_WORKERS > 1 is not supported with in-memory thread/session state. "
            "Forcing single worker to avoid cross-process timeout/race issues."
        )
        worker_count = 1
    uvicorn.run("scene_agent.interfaces.api:app", host=host, port=port, workers=worker_count)
