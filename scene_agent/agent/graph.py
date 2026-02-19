"""
LangGraph state machine construction.
Creates the agent graph following LangGraph best practices.
"""
import asyncio
import json
import re
import time
from typing import Any, Literal
from langgraph.graph import StateGraph, START, END
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt.tool_node import ToolCallRequest


from scene_agent.agent.state import AgentState
from scene_agent.agent.redis_checkpointer import get_graph_checkpointer
from scene_agent.agent.nodes import (
    TODO_STAGNATION_LIMIT,
    agent_node,
    checkpoint_gate_node,
    finalize_node,
    post_agent_node,
    scene_observe_node,
    todo_check_node,
    update_memory_node,
    verify_node,
)
from scene_agent.config import get_settings
from scene_agent.vlm import get_vlm_provider
from scene_agent.tools import get_blender_tools


_TOOL_RETRY_MAX_ATTEMPTS = 2
_TOOL_RETRY_BASE_DELAY_SECONDS = 0.2
_FINALIZE_BLOCK_GRACE_CHECKS = 2
_RETRYABLE_TOOL_ERROR_MARKERS = (
    "validation error",
    "input should",
    "type=list_type",
    "timeout",
    "timed out",
    "connection",
    "transport",
    "temporarily unavailable",
    "service unavailable",
    "rate limit",
    "try again",
    "429",
    "503",
)


def _extract_tool_hint(tool: object) -> str | None:
    description = getattr(tool, "description", None)
    if not isinstance(description, str):
        return None
    normalized = re.sub(r"\s+", " ", description).strip()
    if not normalized:
        return None
    return normalized


def _message_has_tool_calls(message: AIMessage) -> bool:
    tool_calls = getattr(message, "tool_calls", None)
    if isinstance(tool_calls, list) and len(tool_calls) > 0:
        return True

    additional_kwargs = getattr(message, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        additional_tool_calls = additional_kwargs.get("tool_calls")
        if isinstance(additional_tool_calls, list) and len(additional_tool_calls) > 0:
            return True
    return False


def _route_after_post_agent(state: AgentState) -> Literal["tools", "checkpoint_finalize", "agent"]:
    messages = state.get("messages") or []
    for message in reversed(list(messages)):
        if isinstance(message, AIMessage):
            if _message_has_tool_calls(message):
                return "tools"
            decision = state.get("agent_decision")
            should_call_tools = decision.get("should_call_tools") if isinstance(decision, dict) else None
            iteration_count = state.get("iteration_count")
            # If the model explicitly says tools should be called but emitted none,
            # retry once before finalizing to avoid premature exits.
            if should_call_tools is True and isinstance(iteration_count, int) and iteration_count < 2:
                return "agent"
            return "checkpoint_finalize"
    return "checkpoint_finalize"


def _coerce_object_name_list(raw_value: Any) -> list[str] | None:
    if isinstance(raw_value, list):
        normalized: list[str] = []
        for item in raw_value:
            if not isinstance(item, str):
                continue
            value = item.strip()
            if value:
                normalized.append(value)
        return normalized

    if not isinstance(raw_value, str):
        return None

    text = raw_value.strip()
    if not text:
        return []

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, list):
        return _coerce_object_name_list(parsed)

    if any(separator in text for separator in (",", "\n", ";")):
        chunks = text.replace("\n", ",").replace(";", ",").split(",")
    else:
        chunks = [text]

    normalized: list[str] = []
    for chunk in chunks:
        value = chunk.strip().strip("\"'`")
        if value:
            normalized.append(value)
    return normalized


def _normalize_tool_call_args(tool_call: dict[str, Any]) -> dict[str, Any]:
    tool_name = tool_call.get("name")
    if tool_name != "delete_objects":
        return tool_call

    raw_args = tool_call.get("args")
    if not isinstance(raw_args, dict):
        return tool_call

    normalized_names = _coerce_object_name_list(raw_args.get("object_names"))
    if normalized_names is None:
        return tool_call

    updated_args = dict(raw_args)
    updated_args["object_names"] = normalized_names
    return {**tool_call, "args": updated_args}


def _normalize_tool_request(request: ToolCallRequest) -> ToolCallRequest:
    tool_call = request.tool_call
    if not isinstance(tool_call, dict):
        return request

    normalized_call = _normalize_tool_call_args(tool_call)
    if normalized_call == tool_call:
        return request
    return request.override(tool_call=normalized_call)


def _tool_error_is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    error_text = str(exc).lower()
    if not error_text:
        return False
    return any(marker in error_text for marker in _RETRYABLE_TOOL_ERROR_MARKERS)


def _safe_tool_call_id(tool_call: dict[str, Any]) -> str:
    tool_call_id = tool_call.get("id")
    if isinstance(tool_call_id, str) and tool_call_id:
        return tool_call_id
    return f"tool_call_{int(time.time() * 1000)}"


def _tool_error_message(tool_name: str, exc: Exception, attempts: int) -> str:
    attempt_word = "attempt" if attempts == 1 else "attempts"
    return f"Error executing tool {tool_name} after {attempts} {attempt_word}: {exc}"


async def _awrap_tool_call_with_retry(
    request: ToolCallRequest,
    execute,
):
    working_request = _normalize_tool_request(request)
    for attempt in range(1, _TOOL_RETRY_MAX_ATTEMPTS + 1):
        try:
            return await execute(working_request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            raw_tool_call = (
                working_request.tool_call
                if isinstance(working_request.tool_call, dict)
                else request.tool_call
            )
            tool_call = raw_tool_call if isinstance(raw_tool_call, dict) else {}
            tool_name = tool_call.get("name")
            if not isinstance(tool_name, str) or not tool_name:
                tool_name = "unknown_tool"

            should_retry = attempt < _TOOL_RETRY_MAX_ATTEMPTS and _tool_error_is_retryable(exc)
            if not should_retry:
                return ToolMessage(
                    content=_tool_error_message(tool_name, exc, attempt),
                    name=tool_name,
                    tool_call_id=_safe_tool_call_id(tool_call),
                    status="error",
                )

            # Keep retries lightweight and only apply deterministic arg normalization.
            working_request = _normalize_tool_request(working_request)
            await asyncio.sleep(_TOOL_RETRY_BASE_DELAY_SECONDS * attempt)

    tool_call = request.tool_call if isinstance(request.tool_call, dict) else {}
    tool_name = tool_call.get("name") if isinstance(tool_call.get("name"), str) else "unknown_tool"
    return ToolMessage(
        content=f"Error executing tool {tool_name}: retry budget exhausted.",
        name=tool_name,
        tool_call_id=_safe_tool_call_id(tool_call),
        status="error",
    )


def _wrap_tool_call_with_retry(
    request: ToolCallRequest,
    execute,
):
    import time as _time

    working_request = _normalize_tool_request(request)
    for attempt in range(1, _TOOL_RETRY_MAX_ATTEMPTS + 1):
        try:
            return execute(working_request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            raw_tool_call = (
                working_request.tool_call
                if isinstance(working_request.tool_call, dict)
                else request.tool_call
            )
            tool_call = raw_tool_call if isinstance(raw_tool_call, dict) else {}
            tool_name = tool_call.get("name")
            if not isinstance(tool_name, str) or not tool_name:
                tool_name = "unknown_tool"

            should_retry = attempt < _TOOL_RETRY_MAX_ATTEMPTS and _tool_error_is_retryable(exc)
            if not should_retry:
                return ToolMessage(
                    content=_tool_error_message(tool_name, exc, attempt),
                    name=tool_name,
                    tool_call_id=_safe_tool_call_id(tool_call),
                    status="error",
                )

            working_request = _normalize_tool_request(working_request)
            _time.sleep(_TOOL_RETRY_BASE_DELAY_SECONDS * attempt)

    tool_call = request.tool_call if isinstance(request.tool_call, dict) else {}
    tool_name = tool_call.get("name") if isinstance(tool_call.get("name"), str) else "unknown_tool"
    return ToolMessage(
        content=f"Error executing tool {tool_name}: retry budget exhausted.",
        name=tool_name,
        tool_call_id=_safe_tool_call_id(tool_call),
        status="error",
    )


def _should_run_todo_check(state: AgentState) -> bool:
    gate = state.get("todo_check_gate")
    if not isinstance(gate, dict):
        return False
    return bool(gate.get("should_run"))


def _route_after_loop_checkpoint(
    state: AgentState,
) -> Literal["todo_check", "agent"]:
    if _should_run_todo_check(state):
        return "todo_check"
    return "agent"


def _route_after_finalize_checkpoint(state: AgentState) -> Literal["todo_check", "finalize"]:
    if _should_run_todo_check(state):
        return "todo_check"
    return "finalize"


def _route_after_todo_check(state: AgentState) -> Literal["finalize", "agent"]:
    gate = state.get("todo_check_gate")
    if isinstance(gate, dict) and gate.get("stage") == "finalize":
        todo_check = state.get("todo_check")
        if isinstance(todo_check, dict):
            status = todo_check.get("status")
            if status in {"completed", "not_applicable"}:
                return "finalize"
            if status == "blocked":
                stagnation_count = todo_check.get("stagnation_count")
                if (
                    isinstance(stagnation_count, int)
                    and stagnation_count < (TODO_STAGNATION_LIMIT + _FINALIZE_BLOCK_GRACE_CHECKS)
                ):
                    return "agent"
                return "finalize"
        else:
            return "finalize"
    return "agent"


async def create_agent_graph(
    session_id: str | None = None,
    *,
    provider_name: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
):
    """
    Create and compile the LangGraph agent.
    
    Following langchain-mcp-adapters best practices:
    - Standard agent-tools loop
    - Agent decides when to perceive/render
    - Checkpointing with MemorySaver
    - Streaming support
    
    Returns:
        Compiled LangGraph application
    """
    settings = get_settings()
    
    # Initialize VLM provider
    selected_provider = (provider_name or settings.vlm_provider).lower()
    selected_model = model or settings.get_vlm_default_model(selected_provider)
    selected_api_key = api_key or settings.get_vlm_api_key(selected_provider)
    if not selected_api_key:
        raise ValueError(
            f"No API key configured for provider '{selected_provider}'. "
            "Set provider-specific API key or VLM_API_KEY."
        )
    vlm_provider = get_vlm_provider(
        provider_name=selected_provider,
        api_key=selected_api_key,
        model=selected_model,
    )
    model = vlm_provider.get_chat_model()
    
    # Load tools from Blender MCP server
    tools = await get_blender_tools(session_id=session_id)
    available_tool_names = [
        tool.name
        for tool in tools
        if hasattr(tool, "name") and isinstance(tool.name, str) and tool.name
    ]
    available_tool_hints: dict[str, str] = {}
    for tool in tools:
        tool_name = getattr(tool, "name", None)
        if not isinstance(tool_name, str) or not tool_name or tool_name in available_tool_hints:
            continue
        hint = _extract_tool_hint(tool)
        if hint:
            available_tool_hints[tool_name] = hint
    
    # Bind tools to model
    llm_with_tools = model.bind_tools(tools)
    
    # Define agent node with bound tools
    def call_model(state: AgentState) -> dict:
        return agent_node(state, llm_with_tools, available_tool_names)
    
    # Build graph
    builder = StateGraph(AgentState)
    
    # Add nodes
    builder.add_node("agent", call_model)
    builder.add_node("post_agent", post_agent_node)
    builder.add_node(
        "tools",
        ToolNode(
            tools,
            handle_tool_errors=False,
            wrap_tool_call=_wrap_tool_call_with_retry,
            awrap_tool_call=_awrap_tool_call_with_retry,
        ),
    )
    builder.add_node("update_memory", update_memory_node)
    builder.add_node("scene_observe", scene_observe_node)
    builder.add_node(
        "checkpoint_loop",
        lambda state: checkpoint_gate_node(state, stage="loop"),
    )
    builder.add_node(
        "checkpoint_finalize",
        lambda state: checkpoint_gate_node(state, stage="finalize"),
    )
    builder.add_node("todo_check", todo_check_node)
    builder.add_node(
        "verify",
        lambda state: verify_node(
            state,
            provider_name=selected_provider,
            api_key=selected_api_key,
            model=selected_model,
        ),
    )
    builder.add_node(
        "finalize",
        lambda state: finalize_node(state, finalizer_model=model),
    )
    
    # Connect nodes
    builder.add_edge(START, "agent")

    # Persist decision/todo after each assistant response, then branch.
    builder.add_edge("agent", "post_agent")
    builder.add_conditional_edges(
        "post_agent",
        _route_after_post_agent,
    )
    
    # After tools: update_memory -> scene_observe -> verify -> checkpoint_loop
    builder.add_edge("tools", "update_memory")
    builder.add_edge("update_memory", "scene_observe")
    builder.add_edge("scene_observe", "verify")
    builder.add_edge("verify", "checkpoint_loop")
    builder.add_conditional_edges(
        "checkpoint_loop",
        _route_after_loop_checkpoint,
    )
    builder.add_conditional_edges(
        "checkpoint_finalize",
        _route_after_finalize_checkpoint,
    )
    builder.add_conditional_edges(
        "todo_check",
        _route_after_todo_check,
    )
    builder.add_edge("finalize", END)
    
    # Compile with checkpointing
    checkpointer = get_graph_checkpointer()
    app = builder.compile(checkpointer=checkpointer)
    setattr(app, "_available_tool_names", available_tool_names)
    setattr(app, "_available_tool_hints", available_tool_hints)
    setattr(app, "_vlm_provider", selected_provider)
    setattr(app, "_vlm_model", selected_model)
    
    return app


def create_agent_graph_sync(
    session_id: str | None = None,
    *,
    provider_name: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
):
    """Synchronous wrapper for create_agent_graph"""
    import asyncio
    return asyncio.run(
        create_agent_graph(
            session_id=session_id,
            provider_name=provider_name,
            api_key=api_key,
            model=model,
        )
    )
