"""API routes."""
import asyncio
from dataclasses import dataclass, field
from importlib import import_module
import json
import threading
import time
import uuid
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from langchain_core.messages import HumanMessage
from scene_agent.blender.session_manager import SessionResourceError, get_session_manager
from scene_agent.config import get_settings
from scene_agent.session import get_session_coordinator
from typing import Any

from .models import ChatRequest, ChatResponse
from .shared import (
    build_graph_node_event_payload,
    claim_or_proxy_request,
    extract_graph_step_events,
    log_event,
    message_content_to_text,
    message_has_tool_calls,
    message_is_tool,
    normalize_requested_tool_names,
    normalize_stream_event,
    resolve_enabled_tool_names,
    resolve_thread_vlm_for_chat,
    sanitize_message_for_stream,
    serialize_message,
    set_owner_headers,
)


def resolve_api_module():
    return import_module("scene_agent.interfaces.api")


async def get_agent(thread_id: str | None = None):
    api_module = resolve_api_module()
    return await api_module.get_agent(thread_id)


router = APIRouter()
_INTERNAL_NON_USER_MESSAGE_NODES = frozenset(
    {
        "verify",
        "route_mode",
        "sync_reference_catalog",
        "prepare_reference_context",
    }
)
_STREAM_REQUEST_ID_HEADER = "X-Stream-Request-Id"
_STREAM_SESSION_RETAIN_SECONDS = 120.0
_STREAM_POLL_INTERVAL_SECONDS = 0.25
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

    def snapshot_after(self, last_seq: int) -> list[dict[str, Any]]:
        with self.lock:
            start_index = max(0, int(last_seq))
            return [dict(item) for item in self.history[start_index:]]

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
    scene_has_change = False
    done_payload: dict[str, Any] | None = None
    next_event_task: asyncio.Task | None = None
    stream: Any = None

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
                "attached_image_ids": request.attached_image_ids,
                "task_id": request.task_id,
                "workflow_topology_request": request.workflow_topology,
                "memory_profile_request": request.memory_profile,
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
                        and stream_source_node in _INTERNAL_NON_USER_MESSAGE_NODES
                        and not is_tool_message
                    ):
                        # Internal nodes may produce model tokens (e.g. router JSON);
                        # user-facing output should come from external assistant/tool messages only.
                        continue
                    serialized_stream = sanitize_message_for_stream(serialized)
                    message_type = serialized_stream.get("type")
                    if message_has_tool_calls(serialized) or is_tool_message:
                        scene_has_change = True
                        session.set_scene_has_change(True)
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
                    session.note_assistant_chunk()
                    if is_message_stream:
                        event_payload = {"delta": delta, "message_id": message_id}
                        session.publish(event_payload)
                    else:
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
        log_event(
            "error",
            "stream_failed",
            {
                "request_id": session.request_id,
                "thread_id": request.thread_id,
                "error": str(e),
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
        
        # Run agent
        result = await agent.ainvoke(
            {
                "messages": [HumanMessage(content=request.message)],
                "thread_id": request.thread_id,
                "enabled_tool_names": enabled_tool_names,
                "attached_image_ids": request.attached_image_ids,
                "task_id": request.task_id,
                "workflow_topology_request": request.workflow_topology,
                "memory_profile_request": request.memory_profile,
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
        set_owner_headers(response, resolution)
        return payload

    except HTTPException:
        raise
    except SessionResourceError as e:
        raise HTTPException(status_code=503, detail=e.detail) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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
