"""API routes."""
import asyncio
from importlib import import_module
import json
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from langchain_core.messages import HumanMessage
from scene_agent.blender.session_manager import SessionResourceError
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
            resolve_thread_vlm_for_chat(
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
                    "task_id": request.task_id,
                    "workflow_topology_request": request.workflow_topology,
                    "memory_profile_request": request.memory_profile,
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
                step_events = extract_graph_step_events(mode, payload)
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
                        graph_event_payload = build_graph_node_event_payload(
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
