"""API routes."""
import asyncio
from collections import deque
from dataclasses import dataclass, field
from importlib import import_module
import json
import logging
from pathlib import Path
import threading
import time
from typing import Any
import uuid
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from langchain_core.messages import HumanMessage
from scene_agent.agent.redis_checkpointer import get_graph_checkpointer
from scene_agent.blender.session_manager import SessionResourceError, get_session_manager
from scene_agent.config import get_settings
from scene_agent.session import get_session_coordinator

from .models import ChatRequest, ChatResponse, RetryChatRequest
from .shared import (
    assistant_message_display_text,
    build_graph_node_event_payload,
    claim_or_proxy_request,
    extract_message_reasoning_text,
    extract_message_tool_call_names,
    extract_graph_step_events,
    log_event,
    message_has_tool_calls,
    message_is_tool,
    normalize_requested_tool_names,
    normalize_stream_event,
    resolve_enabled_tool_names,
    resolve_thread_vlm_for_chat,
    sanitize_message_for_stream,
    serialize_message,
    set_owner_headers,
    resolve_thread_storage_dir,
    send_blender_command_sync,
)


def resolve_api_module():
    return import_module("scene_agent.interfaces.api")


async def get_agent(thread_id: str | None = None):
    api_module = resolve_api_module()
    return await api_module.get_agent(thread_id)


def _resolve_fast_mode_for_request(request: ChatRequest) -> bool:
    if isinstance(request.fast_mode, bool):
        return request.fast_mode
    return bool(getattr(get_settings(), "fast_mode_default", False))


def _truncate_text(value: str, *, limit: int = 240) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}...[truncated]"


def _safe_repr(value: Any, *, limit: int = 240) -> str:
    try:
        rendered = repr(value)
    except Exception as exc:  # pragma: no cover - defensive
        rendered = f"<repr failed: {type(exc).__name__}: {exc}>"
    return _truncate_text(rendered, limit=limit)


def _summarize_message_for_error(message: Any) -> dict[str, Any]:
    serialized = sanitize_message_for_stream(serialize_message(message))
    summary: dict[str, Any] = {
        "python_type": type(message).__name__,
        "message_type": serialized.get("type"),
    }
    message_id = serialized.get("id")
    if isinstance(message_id, str) and message_id:
        summary["id"] = message_id
    message_name = serialized.get("name")
    if isinstance(message_name, str) and message_name:
        summary["name"] = message_name
    display_text = assistant_message_display_text(serialized)
    if display_text:
        summary["text_preview"] = _truncate_text(display_text)
    reasoning_text = extract_message_reasoning_text(serialized)
    if reasoning_text:
        summary["reasoning_preview"] = _truncate_text(reasoning_text)
    additional_kwargs = serialized.get("additional_kwargs")
    if isinstance(additional_kwargs, dict) and additional_kwargs:
        summary["additional_kwargs_keys"] = sorted(additional_kwargs.keys())[:12]
    tool_calls = serialized.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        summary["tool_call_count"] = len(tool_calls)
    content = serialized.get("content")
    if isinstance(content, list):
        summary["content_block_count"] = len(content)
    elif isinstance(content, dict):
        summary["content_keys"] = sorted(content.keys())[:12]
    return summary


def _summarize_stream_payload_for_error(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        summary: dict[str, Any] = {
            "python_type": "dict",
            "keys": sorted(str(key) for key in payload.keys())[:20],
        }
        if "langgraph_node" in payload and isinstance(payload["langgraph_node"], str):
            summary["langgraph_node"] = payload["langgraph_node"]
        messages = payload.get("messages")
        if isinstance(messages, list):
            summary["message_count"] = len(messages)
            if messages:
                summary["message_preview"] = [
                    _summarize_message_for_error(message) for message in messages[:2]
                ]
        elif messages is not None:
            summary["message_preview"] = [_summarize_message_for_error(messages)]
        todos = payload.get("todos")
        if isinstance(todos, list):
            summary["todo_count"] = len(todos)
        return summary

    if isinstance(payload, tuple):
        summary = {
            "python_type": "tuple",
            "length": len(payload),
            "item_types": [type(item).__name__ for item in payload[:4]],
        }
        if payload:
            item_preview: list[dict[str, Any]] = []
            for item in payload[:2]:
                if isinstance(item, dict) or hasattr(item, "type") or hasattr(item, "content"):
                    item_preview.append(_summarize_stream_payload_for_error(item))
                else:
                    item_preview.append(
                        {
                            "python_type": type(item).__name__,
                            "repr": _safe_repr(item),
                        }
                    )
            summary["item_preview"] = item_preview
        return summary

    if isinstance(payload, list):
        summary = {
            "python_type": "list",
            "length": len(payload),
            "item_types": [type(item).__name__ for item in payload[:4]],
        }
        if payload:
            first = payload[0]
            if isinstance(first, dict) or hasattr(first, "type") or hasattr(first, "content"):
                summary["first_item"] = _summarize_stream_payload_for_error(first)
            else:
                summary["first_item"] = {
                    "python_type": type(first).__name__,
                    "repr": _safe_repr(first),
                }
        return summary

    if hasattr(payload, "type") or hasattr(payload, "content"):
        return _summarize_message_for_error(payload)

    return {
        "python_type": type(payload).__name__,
        "python_module": type(payload).__module__,
        "repr": _safe_repr(payload),
    }


def _summarize_stream_event_for_error(event: Any) -> dict[str, Any]:
    mode, payload = normalize_stream_event(event)
    summary: dict[str, Any] = {
        "event_python_type": type(event).__name__,
        "payload": _summarize_stream_payload_for_error(payload),
    }
    if isinstance(mode, str):
        summary["mode"] = mode
    elif mode is not None:
        summary["mode_payload"] = _summarize_stream_payload_for_error(mode)
    return summary


_RETRY_DIRNAME = "retry"
_LATEST_RETRY_METADATA_FILENAME = "latest_turn.json"
_LATEST_RETRY_SNAPSHOT_FILENAME = "latest_turn_pre.blend"


def _build_human_message(request: ChatRequest) -> HumanMessage:
    normalized_turn_id = request.turn_id.strip() if isinstance(request.turn_id, str) else ""
    if normalized_turn_id:
        return HumanMessage(content=request.message, id=normalized_turn_id)
    return HumanMessage(content=request.message)


def _retry_storage_dir(thread_id: str) -> Path:
    return resolve_thread_storage_dir(thread_id) / _RETRY_DIRNAME


def _retry_metadata_path(thread_id: str) -> Path:
    return _retry_storage_dir(thread_id) / _LATEST_RETRY_METADATA_FILENAME


def _retry_snapshot_path(thread_id: str) -> Path:
    return _retry_storage_dir(thread_id) / _LATEST_RETRY_SNAPSHOT_FILENAME


def _normalize_retry_attached_image_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        image_id
        for image_id in value
        if isinstance(image_id, str) and image_id.strip()
    ]


async def _get_checkpoint_tuple(
    *,
    thread_id: str,
    checkpoint_id: str | None = None,
    checkpoint_ns: str | None = None,
) -> Any:
    checkpointer = get_graph_checkpointer()
    config = {"configurable": {"thread_id": thread_id}}
    if checkpoint_ns:
        config["configurable"]["checkpoint_ns"] = checkpoint_ns
    if checkpoint_id:
        config["configurable"]["checkpoint_id"] = checkpoint_id
    return await asyncio.to_thread(checkpointer.get_tuple, config)


async def _capture_latest_turn_retry_state(
    *,
    request: ChatRequest,
    request_id: str,
) -> None:
    normalized_turn_id = request.turn_id.strip() if isinstance(request.turn_id, str) else ""
    if not normalized_turn_id:
        return

    retry_dir = _retry_storage_dir(request.thread_id)
    retry_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = _retry_snapshot_path(request.thread_id)
    metadata_path = _retry_metadata_path(request.thread_id)

    checkpoint_tuple = await _get_checkpoint_tuple(thread_id=request.thread_id)
    checkpoint_id: str | None = None
    checkpoint_ns = ""
    if checkpoint_tuple is not None:
        raw_checkpoint_id = checkpoint_tuple.checkpoint.get("id")
        checkpoint_id = raw_checkpoint_id if isinstance(raw_checkpoint_id, str) and raw_checkpoint_id else None
        configurable = checkpoint_tuple.config.get("configurable", {})
        raw_checkpoint_ns = configurable.get("checkpoint_ns")
        if isinstance(raw_checkpoint_ns, str):
            checkpoint_ns = raw_checkpoint_ns

    try:
        await asyncio.to_thread(
            send_blender_command_sync,
            "save_blend",
            {"filepath": str(snapshot_path), "copy": True},
            request.thread_id,
        )
    except Exception as exc:
        log_event(
            "warning",
            "retry_snapshot_capture_failed",
            {
                "request_id": request_id,
                "thread_id": request.thread_id,
                "turn_id": normalized_turn_id,
                "error": str(exc),
            },
        )
        try:
            metadata_path.unlink(missing_ok=True)
        except Exception:
            pass
        return

    metadata = {
        "turn_id": normalized_turn_id,
        "message": request.message,
        "attached_image_ids": _normalize_retry_attached_image_ids(request.attached_image_ids),
        "task_id": request.task_id,
        "workflow_topology": request.workflow_topology,
        "memory_profile": request.memory_profile,
        "pre_turn_checkpoint_id": checkpoint_id,
        "pre_turn_checkpoint_ns": checkpoint_ns,
        "pre_turn_blend_snapshot": str(snapshot_path),
        "updated_at_ms": int(time.time() * 1000),
    }

    try:
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        log_event(
            "warning",
            "retry_metadata_write_failed",
            {
                "request_id": request_id,
                "thread_id": request.thread_id,
                "turn_id": normalized_turn_id,
                "error": str(exc),
            },
        )
        try:
            metadata_path.unlink(missing_ok=True)
        except Exception:
            pass


def _load_latest_turn_retry_metadata(thread_id: str) -> dict[str, Any]:
    metadata_path = _retry_metadata_path(thread_id)
    if not metadata_path.exists():
        raise HTTPException(status_code=409, detail="Latest turn retry data is unavailable.")
    try:
        loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail="Latest turn retry data is unreadable.",
        ) from exc
    if not isinstance(loaded, dict):
        raise HTTPException(status_code=409, detail="Latest turn retry data is invalid.")
    return loaded


async def _restore_latest_turn_retry_state(
    *,
    thread_id: str,
    retry_turn_id: str,
    request_id: str,
) -> dict[str, Any]:
    metadata = _load_latest_turn_retry_metadata(thread_id)
    stored_turn_id = metadata.get("turn_id")
    if not isinstance(stored_turn_id, str) or not stored_turn_id:
        raise HTTPException(status_code=409, detail="Latest turn retry data is invalid.")
    if stored_turn_id != retry_turn_id:
        raise HTTPException(status_code=409, detail="Only the latest turn can be retried.")

    snapshot_path = Path(str(metadata.get("pre_turn_blend_snapshot", "")))
    if not snapshot_path.exists():
        raise HTTPException(status_code=409, detail="Retry scene snapshot is missing.")

    checkpoint_id_raw = metadata.get("pre_turn_checkpoint_id")
    checkpoint_id = checkpoint_id_raw if isinstance(checkpoint_id_raw, str) and checkpoint_id_raw else None
    checkpoint_ns_raw = metadata.get("pre_turn_checkpoint_ns")
    checkpoint_ns = checkpoint_ns_raw if isinstance(checkpoint_ns_raw, str) else ""

    checkpoint_tuple = None
    if checkpoint_id:
        checkpoint_tuple = await _get_checkpoint_tuple(
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
            checkpoint_ns=checkpoint_ns,
        )
        if checkpoint_tuple is None:
            raise HTTPException(status_code=409, detail="Retry checkpoint is missing.")

    await asyncio.to_thread(
        send_blender_command_sync,
        "load_blend",
        {"filepath": str(snapshot_path)},
        thread_id,
    )

    checkpointer = get_graph_checkpointer()
    await asyncio.to_thread(checkpointer.delete_thread, thread_id)

    if checkpoint_tuple is not None:
        await asyncio.to_thread(
            checkpointer.put,
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}},
            checkpoint_tuple.checkpoint,
            checkpoint_tuple.metadata,
            checkpoint_tuple.checkpoint["channel_versions"],
        )

    log_event(
        "info",
        "retry_state_restored",
        {
            "request_id": request_id,
            "thread_id": thread_id,
            "turn_id": retry_turn_id,
            "checkpoint_id": checkpoint_id,
        },
    )
    return metadata


router = APIRouter()
_INTERNAL_NON_USER_MESSAGE_NODES = frozenset(
    {
        "verify",
        "initialize_request",
        "sync_reference_catalog",
        "prepare_reference_context",
        "router",
        "plan_node",
        "evaluator",
        "verifier_feedback",
    }
)
_STREAM_REQUEST_ID_HEADER = "X-Stream-Request-Id"
_STREAM_SESSION_RETAIN_SECONDS = 120.0
_STREAM_SESSION_HISTORY_LIMIT = 2000
_STREAM_POLL_INTERVAL_SECONDS = 0.25
_STREAM_ERROR_EVENT_HISTORY_LIMIT = 5
_ACTIVE_STREAM_SESSIONS: dict[str, "_ActiveStreamSession"] = {}
_ACTIVE_STREAM_SESSIONS_LOCK = threading.Lock()


@dataclass
class _ActiveStreamSession:
    stream_request_id: str
    thread_id: str
    request_id: str
    lease_token: str | None = None
    lease_epoch: int | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    next_seq: int = 1
    done: bool = False
    updated_at: float = field(default_factory=time.time)
    retain_until: float | None = None
    scene_has_change: bool = False
    producer_task: asyncio.Task | None = None
    runtime_heartbeat_task: asyncio.Task | None = None
    lease_heartbeat_task: asyncio.Task | None = None
    stop_requested: bool = False
    termination_reason: str | None = None
    progress: dict[str, Any] = field(
        default_factory=lambda: {
            "task_mode": "unknown",
            "graph_steps": 0,
            "last_node": None,
            "tool_events": 0,
            "assistant_chunks": 0,
            "todo_total": 0,
            "todo_completed": 0,
        }
    )
    lock: threading.Lock = field(default_factory=threading.Lock)

    def publish(self, payload: dict[str, Any]) -> None:
        event_payload = dict(payload)
        with self.lock:
            event_payload["seq"] = self.next_seq
            self.next_seq += 1
            event_payload.setdefault("stream_request_id", self.stream_request_id)
            self.updated_at = time.time()
            self.history.append(event_payload)
            overflow = len(self.history) - _STREAM_SESSION_HISTORY_LIMIT
            if overflow > 0:
                del self.history[:overflow]

    def snapshot_after(self, last_seq: int) -> list[dict[str, Any]]:
        with self.lock:
            normalized_last_seq = max(0, int(last_seq))
            return [
                dict(item)
                for item in self.history
                if int(item.get("seq", 0)) > normalized_last_seq
            ]

    def latest_seq(self) -> int:
        with self.lock:
            return self.next_seq - 1

    def mark_done(self) -> None:
        with self.lock:
            self.done = True
            self.updated_at = time.time()
            self.retain_until = self.updated_at + _STREAM_SESSION_RETAIN_SECONDS

    def is_done(self) -> bool:
        with self.lock:
            return self.done

    def should_stop(self) -> bool:
        with self.lock:
            return self.stop_requested

    def request_stop(self, *, reason: str | None = None) -> bool:
        with self.lock:
            already_requested = self.stop_requested
            self.stop_requested = True
            if reason and not self.termination_reason:
                self.termination_reason = reason
            self.updated_at = time.time()
            return not already_requested

    def set_scene_has_change(self, value: bool) -> None:
        with self.lock:
            self.scene_has_change = bool(value)

    def note_task_mode(self, task_mode: str) -> None:
        normalized = task_mode.strip() if isinstance(task_mode, str) else ""
        if not normalized:
            return
        with self.lock:
            self.progress["task_mode"] = normalized

    def note_graph_node(self, node_name: str, step_index: int) -> None:
        with self.lock:
            self.progress["graph_steps"] = int(step_index)
            self.progress["last_node"] = node_name

    def note_tool_event(self) -> None:
        with self.lock:
            self.progress["tool_events"] = int(self.progress["tool_events"]) + 1

    def note_assistant_chunk(self) -> None:
        with self.lock:
            self.progress["assistant_chunks"] = int(self.progress["assistant_chunks"]) + 1

    def note_todos(self, todos: list[dict[str, Any]]) -> None:
        total = len(todos)
        completed = 0
        for item in todos:
            if not isinstance(item, dict):
                continue
            if item.get("status") == "completed":
                completed += 1
        with self.lock:
            self.progress["todo_total"] = total
            self.progress["todo_completed"] = completed

    def progress_snapshot(self) -> dict[str, Any]:
        with self.lock:
            progress = dict(self.progress)
            progress["request_id"] = self.request_id
            progress["stream_request_id"] = self.stream_request_id
            progress["latest_seq"] = self.next_seq - 1
            progress["scene_has_change"] = self.scene_has_change
            progress["done"] = self.done
            return progress


def _prune_expired_stream_sessions(now: float | None = None) -> None:
    current_time = time.time() if now is None else now
    with _ACTIVE_STREAM_SESSIONS_LOCK:
        expired_ids = [
            stream_id
            for stream_id, session in _ACTIVE_STREAM_SESSIONS.items()
            if session.retain_until is not None and session.retain_until <= current_time
        ]
        for stream_id in expired_ids:
            _ACTIVE_STREAM_SESSIONS.pop(stream_id, None)


def _register_stream_session(session: _ActiveStreamSession) -> None:
    _prune_expired_stream_sessions()
    with _ACTIVE_STREAM_SESSIONS_LOCK:
        _ACTIVE_STREAM_SESSIONS[session.stream_request_id] = session


def _get_stream_session(stream_request_id: str) -> _ActiveStreamSession | None:
    _prune_expired_stream_sessions()
    with _ACTIVE_STREAM_SESSIONS_LOCK:
        return _ACTIVE_STREAM_SESSIONS.get(stream_request_id)


async def _expire_stream_session_later(stream_request_id: str) -> None:
    await asyncio.sleep(_STREAM_SESSION_RETAIN_SECONDS)
    with _ACTIVE_STREAM_SESSIONS_LOCK:
        session = _ACTIVE_STREAM_SESSIONS.get(stream_request_id)
        if session is None:
            return
        retain_until = session.retain_until
        if retain_until is None or retain_until > time.time():
            return
        _ACTIVE_STREAM_SESSIONS.pop(stream_request_id, None)


def _coerce_last_event_id(raw_value: str | None) -> int:
    if raw_value is None:
        return 0
    text = raw_value.strip()
    if not text:
        return 0
    try:
        parsed = int(text)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _resolve_base_stream_timeout_seconds(settings: Any) -> int:
    raw_value = getattr(settings, "api_stream_timeout_seconds", 120)
    try:
        return max(1, int(raw_value))
    except (TypeError, ValueError):
        return 120


def _resolve_plan_stream_timeout_seconds(settings: Any) -> int | None:
    raw_value = getattr(settings, "api_plan_stream_timeout_seconds", 1800)
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        parsed = 1800
    if parsed <= 0:
        return None
    return max(parsed, _resolve_base_stream_timeout_seconds(settings))


def _promote_stream_timeout_seconds(
    current_timeout: int | None,
    task_mode: str,
    settings: Any,
) -> int | None:
    if task_mode != "plan_mode":
        return current_timeout
    plan_timeout = _resolve_plan_stream_timeout_seconds(settings)
    if plan_timeout is None or current_timeout is None:
        return plan_timeout
    return max(current_timeout, plan_timeout)


def _resolve_keepalive_interval_seconds(settings: Any) -> float:
    base_timeout = _resolve_base_stream_timeout_seconds(settings)
    return min(15.0, max(5.0, base_timeout / 4))


def _resolve_runtime_heartbeat_interval_seconds(
    *,
    settings: Any,
    session: _ActiveStreamSession,
) -> float | None:
    session_manager = get_session_manager()
    local_session = session_manager.get(session.thread_id)
    raw_idle_timeout = getattr(local_session, "idle_timeout_seconds", None)
    if raw_idle_timeout is None:
        raw_idle_timeout = getattr(settings, "session_idle_timeout_seconds", 0)
    try:
        idle_timeout_seconds = int(raw_idle_timeout)
    except (TypeError, ValueError):
        idle_timeout_seconds = 0
    if idle_timeout_seconds <= 0:
        return None
    return float(max(5, min(30, idle_timeout_seconds // 3)))


def _resolve_lease_heartbeat_interval_seconds(settings: Any) -> float:
    raw_interval = getattr(settings, "session_heartbeat_interval_seconds", 5)
    raw_ttl = getattr(settings, "session_lease_ttl_seconds", 20)
    try:
        heartbeat_interval = int(raw_interval)
    except (TypeError, ValueError):
        heartbeat_interval = 5
    try:
        lease_ttl_seconds = int(raw_ttl)
    except (TypeError, ValueError):
        lease_ttl_seconds = 20
    return float(max(1, min(heartbeat_interval, max(1, lease_ttl_seconds // 2))))


def _format_sse(payload: dict[str, Any]) -> str:
    seq = payload.get("seq")
    lines: list[str] = []
    if isinstance(seq, int) and seq > 0:
        lines.append(f"id: {seq}")
    lines.append(f"data: {json.dumps(payload, ensure_ascii=False, default=str)}")
    return "\n".join(lines) + "\n\n"


def _build_heartbeat_payload(session: _ActiveStreamSession) -> dict[str, Any]:
    return {
        "event": "heartbeat",
        "stream_request_id": session.stream_request_id,
        "progress": session.progress_snapshot(),
    }


async def _run_stream_runtime_heartbeat(
    *,
    session: _ActiveStreamSession,
    interval_seconds: float,
) -> None:
    session_manager = get_session_manager()
    coordinator = get_session_coordinator()
    while not session.is_done() and not session.should_stop():
        await asyncio.sleep(interval_seconds)
        if session.is_done() or session.should_stop():
            break
        try:
            session_manager.touch_session(session.thread_id)
            coordinator.touch_activity(session.thread_id, lease_epoch=session.lease_epoch)
        except Exception as exc:
            log_event(
                "warning",
                "stream_runtime_heartbeat_failed",
                {
                    "request_id": session.request_id,
                    "thread_id": session.thread_id,
                    "stream_request_id": session.stream_request_id,
                    "error": str(exc),
                },
            )


async def _run_stream_lease_heartbeat(
    *,
    session: _ActiveStreamSession,
    interval_seconds: float,
) -> None:
    coordinator = get_session_coordinator()
    while not session.is_done() and not session.should_stop():
        await asyncio.sleep(interval_seconds)
        if session.is_done() or session.should_stop():
            break
        try:
            still_owned = coordinator.refresh_lease_if_owned(session.thread_id, session.lease_token)
        except Exception as exc:
            log_event(
                "warning",
                "stream_lease_heartbeat_failed",
                {
                    "request_id": session.request_id,
                    "thread_id": session.thread_id,
                    "stream_request_id": session.stream_request_id,
                    "error": str(exc),
                },
            )
            continue
        if still_owned:
            continue
        first_stop = session.request_stop(reason="ownership_lost")
        if first_stop:
            session.publish(
                {
                    "error": "Stream ownership was lost during execution. Please retry.",
                    "reason": "ownership_lost",
                }
            )
        break

async def _produce_stream_events(
    *,
    session: _ActiveStreamSession,
    request: ChatRequest,
) -> None:
    settings = get_settings()
    timeout_seconds: int | None = _resolve_base_stream_timeout_seconds(settings)
    idle_deadline = time.time() + timeout_seconds if timeout_seconds is not None else None
    saw_message_stream = False
    saw_new_message = False
    graph_step_index = 0
    existing_message_ids: set[str] = set()
    last_assistant_text: str | None = None
    streamed_assistant_message_ids: set[str] = set()
    announced_tool_call_names: set[str] = set()
    saw_unidentified_assistant_delta = False
    scene_has_change = False
    done_payload: dict[str, Any] | None = None
    next_event_task: asyncio.Task | None = None
    stream: Any = None
    recent_stream_events: deque[dict[str, Any]] = deque(maxlen=_STREAM_ERROR_EVENT_HISTORY_LIMIT)

    try:
        resolve_thread_vlm_for_chat(
            request.thread_id,
            request.vlm_provider,
            request.vlm_model,
        )
        agent = await get_agent(request.thread_id)
        coordinator = get_session_coordinator()
        coordinator.touch_activity(request.thread_id, lease_epoch=session.lease_epoch)

        if settings.blender_mode == "headless" and session.runtime_heartbeat_task is None:
            runtime_interval = _resolve_runtime_heartbeat_interval_seconds(
                settings=settings,
                session=session,
            )
            if runtime_interval is not None:
                session.runtime_heartbeat_task = asyncio.create_task(
                    _run_stream_runtime_heartbeat(
                        session=session,
                        interval_seconds=runtime_interval,
                    )
                )

        if session.lease_token and session.lease_heartbeat_task is None:
            session.lease_heartbeat_task = asyncio.create_task(
                _run_stream_lease_heartbeat(
                    session=session,
                    interval_seconds=_resolve_lease_heartbeat_interval_seconds(settings),
                )
            )

        enabled_tool_names = resolve_enabled_tool_names(
            agent,
            normalize_requested_tool_names(request.enabled_mcp_tools),
        )
        config = {"configurable": {"thread_id": request.thread_id}}
        resolved_fast_mode = _resolve_fast_mode_for_request(request)
        await _capture_latest_turn_retry_state(
            request=request,
            request_id=session.request_id,
        )
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
                    reasoning_text = extract_message_reasoning_text(serialized)
                    content_text = assistant_message_display_text(serialized)
                    if not content_text and reasoning_text:
                        content_text = reasoning_text
                    if content_text:
                        last_assistant_text = content_text
        except Exception:
            pass

        stream = agent.astream(
            {
                "messages": [_build_human_message(request)],
                "thread_id": request.thread_id,
                "enabled_tool_names": enabled_tool_names,
                "attached_image_ids": request.attached_image_ids,
                "task_id": request.task_id,
                "workflow_topology_request": request.workflow_topology,
                "memory_profile_request": request.memory_profile,
                "fast_mode": resolved_fast_mode,
            },
            config=config,
            stream_mode=["messages", "values", "updates"],
        )

        while True:
            if session.should_stop():
                if next_event_task is not None and not next_event_task.done():
                    next_event_task.cancel()
                break

            if idle_deadline is not None:
                now = time.time()
                if now >= idle_deadline:
                    if next_event_task is not None:
                        next_event_task.cancel()
                    raise TimeoutError("Stream timed out")
                wait_timeout = max(0.05, idle_deadline - now)
            else:
                wait_timeout = None

            if next_event_task is None:
                next_event_task = asyncio.create_task(stream.__anext__())

            poll_timeout = _STREAM_POLL_INTERVAL_SECONDS
            if wait_timeout is not None:
                poll_timeout = min(wait_timeout, _STREAM_POLL_INTERVAL_SECONDS)
            done, _pending = await asyncio.wait({next_event_task}, timeout=poll_timeout)
            if not done:
                continue
            try:
                event = next_event_task.result()
            except StopAsyncIteration:
                break
            finally:
                next_event_task = None

            recent_stream_events.append(_summarize_stream_event_for_error(event))

            if timeout_seconds is not None:
                idle_deadline = time.time() + timeout_seconds

            mode, payload = normalize_stream_event(event)
            step_events = extract_graph_step_events(mode, payload)
            for step_event in step_events:
                log_event(
                    "info",
                    "agent_graph_step",
                    {
                        "request_id": session.request_id,
                        "thread_id": request.thread_id,
                        "step": step_event["step"],
                        "update_keys": step_event["update_keys"],
                    },
                )

            if session.should_stop():
                break

            is_message_stream = mode == "messages" or hasattr(mode, "content") or hasattr(mode, "type")
            if is_message_stream:
                saw_message_stream = True
            if isinstance(payload, dict) and "todos" in payload and payload["todos"]:
                session.note_todos(payload["todos"])
                session.publish({"todos": payload["todos"]})

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

                    raw_task_mode = node_update.get("task_mode")
                    if isinstance(raw_task_mode, str) and raw_task_mode.strip():
                        normalized_task_mode = raw_task_mode.strip()
                        session.note_task_mode(normalized_task_mode)
                        promoted_timeout = _promote_stream_timeout_seconds(
                            timeout_seconds,
                            normalized_task_mode,
                            settings,
                        )
                        if promoted_timeout != timeout_seconds:
                            timeout_seconds = promoted_timeout
                            if timeout_seconds is None:
                                idle_deadline = None
                            else:
                                idle_deadline = time.time() + timeout_seconds

                    graph_step_index += 1
                    session.note_graph_node(node_name, graph_step_index)
                    graph_event_payload = build_graph_node_event_payload(
                        request_id=session.request_id,
                        thread_id=request.thread_id,
                        node_name=node_name,
                        step_index=graph_step_index,
                        update=node_update,
                    )
                    session.publish(graph_event_payload)

                    todos_payload = node_update.get("todos")
                    if isinstance(todos_payload, list) and len(todos_payload) > 0:
                        session.note_todos(todos_payload)
                        session.publish({"todos": todos_payload})

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
            filtered_update_non_tool_messages: list[Any] = []
            if update_mode_non_tool_messages:
                for node_message in update_mode_non_tool_messages:
                    serialized_node_message = serialize_message(node_message)
                    content_text = assistant_message_display_text(serialized_node_message)
                    reasoning_text = extract_message_reasoning_text(serialized_node_message)
                    if not content_text and not reasoning_text:
                        continue
                    node_message_id = serialized_node_message.get("id")
                    if (
                        isinstance(node_message_id, str)
                        and node_message_id
                        and node_message_id in streamed_assistant_message_ids
                    ):
                        continue
                    if (
                        (not isinstance(node_message_id, str) or not node_message_id)
                        and saw_unidentified_assistant_delta
                    ):
                        continue
                    filtered_update_non_tool_messages.append(node_message)
            if is_message_stream:
                messages = stream_payload if mode == "messages" else [mode]
            elif update_mode_tool_messages or filtered_update_non_tool_messages:
                messages = [*update_mode_tool_messages, *filtered_update_non_tool_messages]
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
                        and stream_source_node in _INTERNAL_NON_USER_MESSAGE_NODES
                        and not is_tool_message
                    ):
                        # Internal nodes may produce model tokens (e.g. router JSON);
                        # user-facing output should come from external assistant/tool messages only.
                        continue
                    serialized_stream = sanitize_message_for_stream(serialized)
                    message_type = serialized_stream.get("type")
                    reasoning_text = extract_message_reasoning_text(serialized_stream)
                    if reasoning_text:
                        serialized_stream["reasoning_content"] = reasoning_text
                    display_text = assistant_message_display_text(serialized_stream)
                    if message_type not in {"human", "system", "tool"} and display_text:
                        serialized_stream["content"] = display_text
                        serialized_stream["text"] = display_text
                    if message_has_tool_calls(serialized) or is_tool_message:
                        scene_has_change = True
                        session.set_scene_has_change(True)
                    if not is_tool_message and message_has_tool_calls(serialized):
                        for tool_call_name in extract_message_tool_call_names(serialized):
                            if tool_call_name in announced_tool_call_names:
                                continue
                            announced_tool_call_names.add(tool_call_name)
                            session.publish(
                                {
                                    "event": "tool_call_started",
                                    "tool_call": {"name": tool_call_name},
                                }
                            )
                    if message_type in {"human", "system"}:
                        continue
                    if message_type == "tool":
                        session.note_tool_event()
                        tool_event_payload = {
                            "messages": [serialized_stream],
                            "scene_has_change": scene_has_change,
                        }
                        session.publish(tool_event_payload)
                        continue
                    message_id = serialized_stream.get("id")
                    if isinstance(message_id, str) and message_id in existing_message_ids:
                        continue
                    if is_message_stream and reasoning_text:
                        session.note_assistant_chunk()
                        session.publish(
                            {
                                "thinking_delta": reasoning_text,
                                "message_id": message_id,
                            }
                        )
                    delta = display_text
                    if not delta and not (reasoning_text and not is_message_stream):
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
                        session.note_assistant_chunk()
                        if isinstance(message_id, str) and message_id:
                            streamed_assistant_message_ids.add(message_id)
                        else:
                            saw_unidentified_assistant_delta = True
                        event_payload = {"delta": delta, "message_id": message_id}
                        session.publish(event_payload)
                    else:
                        session.note_assistant_chunk()
                        message_event_payload = {"messages": [serialized_stream]}
                        session.publish(message_event_payload)

        done_payload = {"event": "done", "scene_has_change": scene_has_change}
        coordinator.touch_activity(request.thread_id, lease_epoch=session.lease_epoch)
    except HTTPException as e:
        detail = e.detail if isinstance(e.detail, str) else json.dumps(e.detail, ensure_ascii=False)
        session.publish({"error": detail, "status_code": e.status_code})
        done_payload = {"event": "done", "scene_has_change": scene_has_change}
    except SessionResourceError as e:
        session.publish(
            {
                "error": e.error,
                "reason": e.reason,
                "limits": e.limits,
                "in_use": e.in_use,
                "status_code": 503,
            }
        )
        done_payload = {"event": "done", "scene_has_change": scene_has_change}
    except Exception as e:
        logging.getLogger("scene_agent").exception(
            "stream_failed_traceback request_id=%s thread_id=%s stream_request_id=%s",
            session.request_id,
            request.thread_id,
            session.stream_request_id,
        )
        log_event(
            "error",
            "stream_failed",
            {
                "request_id": session.request_id,
                "thread_id": request.thread_id,
                "stream_request_id": session.stream_request_id,
                "exception_type": type(e).__name__,
                "error": str(e),
                "stream_progress": _build_heartbeat_payload(session)["progress"],
                "recent_stream_events": list(recent_stream_events),
            },
        )
        session.publish({"error": str(e)})
        done_payload = {"event": "done", "scene_has_change": scene_has_change}
    finally:
        if next_event_task is not None and not next_event_task.done():
            next_event_task.cancel()
            try:
                await next_event_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        for heartbeat_task in (session.runtime_heartbeat_task, session.lease_heartbeat_task):
            if heartbeat_task is not None and not heartbeat_task.done():
                heartbeat_task.cancel()
        for heartbeat_task in (session.runtime_heartbeat_task, session.lease_heartbeat_task):
            if heartbeat_task is None:
                continue
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        session.runtime_heartbeat_task = None
        session.lease_heartbeat_task = None
        if stream is not None and hasattr(stream, "aclose"):
            try:
                await stream.aclose()
            except Exception:
                pass
        if done_payload is not None:
            session.publish(done_payload)
        session.mark_done()
        asyncio.create_task(_expire_stream_session_later(session.stream_request_id))


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, request_http: Request, response: Response):
    """
    Chat with the agent (non-streaming).
    
    Args:
        request: ChatRequest with message and thread_id
        
    Returns:
        ChatResponse with agent's response and todos
    """
    resolution, proxied = await claim_or_proxy_request(
        request=request_http,
        thread_id=request.thread_id,
    )
    if proxied is not None:
        return proxied
    try:
        resolve_thread_vlm_for_chat(
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
        resolved_fast_mode = _resolve_fast_mode_for_request(request)
        
        # Run agent
        result = await agent.ainvoke(
            {
                "messages": [_build_human_message(request)],
                "thread_id": request.thread_id,
                "enabled_tool_names": enabled_tool_names,
                "attached_image_ids": request.attached_image_ids,
                "task_id": request.task_id,
                "workflow_topology_request": request.workflow_topology,
                "memory_profile_request": request.memory_profile,
                "fast_mode": resolved_fast_mode,
            },
            config=config
        )
        get_session_coordinator().touch_activity(request.thread_id)
        
        # Extract response as plain text (LangChain message content can be list/dict blocks).
        last_message = result["messages"][-1]
        serialized_last = serialize_message(last_message)
        reasoning_text = extract_message_reasoning_text(serialized_last).strip()
        response_text = assistant_message_display_text(serialized_last).strip()
        if not response_text and reasoning_text:
            response_text = reasoning_text
        if not response_text:
            response_text = ""
        
        payload = ChatResponse(
            response=response_text,
            thread_id=request.thread_id,
            todos=result.get("todos", [])
        )
        set_owner_headers(response, resolution)
        return payload

    except HTTPException:
        raise
    except SessionResourceError as e:
        raise HTTPException(status_code=503, detail=e.detail) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _build_chat_stream_response(
    *,
    request: ChatRequest,
    request_http: Request,
    resolution: Any,
) -> StreamingResponse:
    settings = get_settings()
    keepalive_interval = _resolve_keepalive_interval_seconds(settings)
    requested_stream_id = (request_http.headers.get(_STREAM_REQUEST_ID_HEADER) or "").strip()
    if requested_stream_id:
        session = _get_stream_session(requested_stream_id)
        if session is None or session.thread_id != request.thread_id:
            raise HTTPException(
                status_code=409,
                detail="Stream session not found or expired. Start a new stream request.",
            )
    else:
        session = _ActiveStreamSession(
            stream_request_id=f"{request.thread_id}:{uuid.uuid4().hex}",
            thread_id=request.thread_id,
            request_id=f"{request.thread_id}:{int(time.time() * 1000)}",
            lease_token=getattr(resolution, "lease_token", None),
            lease_epoch=getattr(resolution, "lease_epoch", None),
        )
        _register_stream_session(session)
        session.producer_task = asyncio.create_task(
            _produce_stream_events(
                session=session,
                request=request,
            )
        )

    last_event_id = _coerce_last_event_id(request_http.headers.get("Last-Event-ID"))

    async def event_generator():
        last_sent_seq = last_event_id
        last_heartbeat_at = time.monotonic()
        while True:
            if await request_http.is_disconnected():
                log_event(
                    "info",
                    "stream_client_disconnected",
                    {
                        "request_id": session.request_id,
                        "thread_id": request.thread_id,
                        "stream_request_id": session.stream_request_id,
                        "last_seq": last_sent_seq,
                    },
                )
                break

            pending_events = session.snapshot_after(last_sent_seq)
            if pending_events:
                for payload in pending_events:
                    raw_seq = payload.get("seq")
                    if isinstance(raw_seq, int):
                        last_sent_seq = raw_seq
                    yield _format_sse(payload)
                last_heartbeat_at = time.monotonic()
                if session.is_done() and last_sent_seq >= session.latest_seq():
                    break
                continue

            if session.is_done():
                break

            now = time.monotonic()
            if now - last_heartbeat_at >= keepalive_interval:
                yield _format_sse(_build_heartbeat_payload(session))
                last_heartbeat_at = now
                continue

            await asyncio.sleep(min(_STREAM_POLL_INTERVAL_SECONDS, keepalive_interval))

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        _STREAM_REQUEST_ID_HEADER: session.stream_request_id,
        "Access-Control-Expose-Headers": (
            f"X-Session-Owner, X-Session-Lease-Epoch, {_STREAM_REQUEST_ID_HEADER}"
        ),
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


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest, request_http: Request):
    """
    Chat with the agent (streaming via Server-Sent Events).
    
    Args:
        request: ChatRequest with message and thread_id
        
    Returns:
        StreamingResponse with SSE events
    """
    resolution, proxied = await claim_or_proxy_request(
        request=request_http,
        thread_id=request.thread_id,
    )
    if proxied is not None:
        return proxied
    return await _build_chat_stream_response(
        request=request,
        request_http=request_http,
        resolution=resolution,
    )


@router.post("/chat/retry/stream")
async def retry_chat_stream(request: RetryChatRequest, request_http: Request):
    resolution, proxied = await claim_or_proxy_request(
        request=request_http,
        thread_id=request.thread_id,
    )
    if proxied is not None:
        return proxied

    request_id = f"{request.thread_id}:{int(time.time() * 1000)}:retry"
    metadata = await _restore_latest_turn_retry_state(
        thread_id=request.thread_id,
        retry_turn_id=request.retry_turn_id,
        request_id=request_id,
    )

    message = metadata.get("message")
    if not isinstance(message, str) or not message.strip():
        raise HTTPException(status_code=409, detail="Retry source message is unavailable.")

    retry_chat_request = ChatRequest(
        message=message,
        thread_id=request.thread_id,
        turn_id=request.retry_turn_id,
        vlm_provider=request.vlm_provider,
        vlm_model=request.vlm_model,
        enabled_mcp_tools=request.enabled_mcp_tools,
        attached_image_ids=_normalize_retry_attached_image_ids(metadata.get("attached_image_ids")),
        task_id=metadata.get("task_id") if isinstance(metadata.get("task_id"), str) else None,
        workflow_topology=(
            metadata.get("workflow_topology")
            if isinstance(metadata.get("workflow_topology"), str)
            else None
        ),
        memory_profile=(
            metadata.get("memory_profile")
            if isinstance(metadata.get("memory_profile"), str)
            else None
        ),
        fast_mode=request.fast_mode,
    )
    return await _build_chat_stream_response(
        request=retry_chat_request,
        request_http=request_http,
        resolution=resolution,
    )
