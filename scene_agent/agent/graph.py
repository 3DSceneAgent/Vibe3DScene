"""
LangGraph state machine construction.
Creates the agent graph following LangGraph best practices.
"""
import asyncio
import json
import os
import re
import time
from typing import Any, Literal
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt.tool_node import ToolCallRequest


from scene_agent.agent.graph_factory import build_agent_state_graph
from scene_agent.agent.internal_tools import get_internal_agent_tools
from scene_agent.agent.state import AgentState
from scene_agent.agent.todo_protocol import TODO_UPDATE_TOOL_NAME
from scene_agent.agent.redis_checkpointer import get_graph_checkpointer
from scene_agent.memory.reference_image_memory import get_image_asset_memory
from scene_agent.agent.nodes import (
    agent_node,
    builder_agent_node,
    budget_evaluator_node,
    initialize_request_node,
    prepare_reference_context_node,
    quality_evaluator_node,
    finalize_node,
    planner_refresh_node,
    progress_evaluator_node,
    post_builder_node,
    post_verifier_node,
    sync_reference_catalog_node,
    scene_observe_node,
    transition_resolver_node,
    todo_commit_node,
    turn_dispatch_node,
    update_memory_node,
    verifier_camera_agent_node,
    verifier_feedback_node,
    verify_node,
    checkpoint_gate_node,
)
from scene_agent.config import get_settings
from scene_agent.vlm import get_vlm_provider
from scene_agent.tools import get_blender_tools


_TOOL_RETRY_MAX_ATTEMPTS = 2
_TOOL_RETRY_BASE_DELAY_SECONDS = 0.2
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
_SUPPORTED_VLM_PROVIDERS = frozenset({"openai", "anthropic", "gemini", "qwen"})


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


def _coerce_non_negative_int(value: Any, *, default: int = 0) -> int:
    if isinstance(value, int) and value >= 0:
        return value
    return default


def _build_context_summary_helper_model(
    *,
    provider_name: str,
    api_key: str,
    settings: Any,
) -> Any | None:
    try:
        if hasattr(settings, "get_context_summary_helper_model"):
            helper_model_name = settings.get_context_summary_helper_model(provider_name)
        else:
            helper_model_name = settings.get_reference_image_helper_model(provider_name)
        helper_provider = get_vlm_provider(
            provider_name=provider_name,
            api_key=api_key,
            model=helper_model_name,
        )
        helper_model = helper_provider.get_chat_model()
    except Exception:
        return None

    if hasattr(helper_model, "with_config"):
        try:
            return helper_model.with_config(
                tags=["nostream"],
                run_name="context_summary_helper",
            )
        except Exception:
            return helper_model
    return helper_model


def _resolve_dual_agent_verifier_runtime(
    *,
    settings: Any,
    default_provider: str,
    default_model: str,
    default_api_key: str,
) -> tuple[str, str, str]:
    raw_provider = getattr(settings, "dual_agent_verifier_vlm_provider", None)
    verifier_provider = default_provider
    if isinstance(raw_provider, str):
        candidate = raw_provider.strip().lower()
        if candidate in _SUPPORTED_VLM_PROVIDERS:
            verifier_provider = candidate

    raw_model = getattr(settings, "dual_agent_verifier_vlm_model", None)
    if isinstance(raw_model, str) and raw_model.strip():
        verifier_model = raw_model.strip()
    elif verifier_provider == default_provider:
        verifier_model = default_model
    else:
        verifier_model = settings.get_vlm_default_model(verifier_provider)
    verifier_api_key = settings.get_vlm_api_key(verifier_provider)
    if not verifier_api_key:
        return default_provider, default_model, default_api_key
    return verifier_provider, verifier_model, verifier_api_key


def _task_mode(state: AgentState) -> str:
    raw_mode = state.get("task_mode")
    if isinstance(raw_mode, str) and raw_mode.strip():
        return raw_mode.strip()
    return "plan_mode"


def _workflow_topology(state: AgentState) -> str:
    raw_topology = state.get("workflow_topology")
    if isinstance(raw_topology, str) and raw_topology.strip():
        return raw_topology.strip()
    return "single_agent"


def _is_dual_plan_mode(state: AgentState) -> bool:
    return _task_mode(state) == "plan_mode" and _workflow_topology(state) == "dual_agent"


def _has_unfinished_todos(state: AgentState) -> bool:
    todos = state.get("todos")
    if not isinstance(todos, list):
        return False
    for item in todos:
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        if status in {"pending", "in_progress"}:
            return True
    return False


def _agent_turn_budget_exhausted(state: AgentState) -> bool:
    turns = _coerce_non_negative_int(state.get("request_agent_turns"))
    max_turns = _coerce_non_negative_int(state.get("max_request_agent_turns"), default=-1)
    return max_turns >= 0 and turns >= max_turns


def _route_after_initialize_request(_state: AgentState) -> Literal["sync_reference_catalog"]:
    return "sync_reference_catalog"


def _route_after_sync_reference_catalog(
    _state: AgentState,
) -> Literal["prepare_reference_context"]:
    return "prepare_reference_context"


def _route_after_prepare_reference_context(state: AgentState) -> Literal["agent", "builder_agent"]:
    if _is_dual_plan_mode(state):
        return "builder_agent"
    return "agent"


def _route_after_turn_dispatch(
    state: AgentState,
) -> Literal["todo_commit", "tools", "quality_evaluator", "agent"]:
    turn_kind = state.get("assistant_turn_kind")
    pending_updates = state.get("pending_todo_updates")
    has_pending_updates = isinstance(pending_updates, list) and len(pending_updates) > 0

    if turn_kind == "mixed":
        return "todo_commit"
    if turn_kind == "todo_only":
        if has_pending_updates:
            return "todo_commit"
        if _agent_turn_budget_exhausted(state):
            return "quality_evaluator"
        return "agent"
    if turn_kind == "external_only":
        return "tools"
    return "quality_evaluator"


def _route_after_post_agent(
    state: AgentState,
) -> Literal["todo_commit", "tools", "quality_evaluator", "agent"]:
    return _route_after_turn_dispatch(state)


def _route_after_todo_commit(
    state: AgentState,
) -> Literal["tools", "quality_evaluator", "agent"]:
    messages = state.get("messages") or []
    for message in reversed(list(messages)):
        if isinstance(message, AIMessage):
            if _message_has_tool_calls(message):
                return "tools"
            break
    if _agent_turn_budget_exhausted(state):
        return "quality_evaluator"
    return "agent"


def _route_after_post_builder(
    state: AgentState,
) -> Literal["tools", "verifier_camera_agent", "quality_evaluator"]:
    messages = state.get("messages") or []
    for message in reversed(list(messages)):
        if isinstance(message, AIMessage):
            if _message_has_tool_calls(message):
                return "tools"
            if _agent_turn_budget_exhausted(state):
                return "quality_evaluator"
            return "verifier_camera_agent"
    if _agent_turn_budget_exhausted(state):
        return "quality_evaluator"
    return "verifier_camera_agent"


def _route_after_post_verifier(
    state: AgentState,
) -> Literal["tools", "verifier_feedback", "quality_evaluator"]:
    messages = state.get("messages") or []
    for message in reversed(list(messages)):
        if isinstance(message, AIMessage):
            if _message_has_tool_calls(message):
                return "tools"
            if _agent_turn_budget_exhausted(state):
                return "quality_evaluator"
            return "verifier_feedback"
    if _agent_turn_budget_exhausted(state):
        return "quality_evaluator"
    return "verifier_feedback"


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


def _tool_validation_message(tool_call: dict[str, Any], message: str) -> ToolMessage:
    tool_name = tool_call.get("name") if isinstance(tool_call.get("name"), str) else "unknown_tool"
    return ToolMessage(
        content=message,
        name=tool_name,
        tool_call_id=_safe_tool_call_id(tool_call),
        status="error",
    )


def _coerce_attached_image_ids_from_state(state: AgentState | Any) -> list[str]:
    if not isinstance(state, dict):
        return []
    raw_value = state.get("attached_image_ids")
    if not isinstance(raw_value, list):
        return []
    attached_ids: list[str] = []
    seen: set[str] = set()
    for item in raw_value:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        attached_ids.append(value)
    return attached_ids


def _resolve_current_request_image_path(state: AgentState | Any) -> tuple[str | None, str | None]:
    attached_image_ids = _coerce_attached_image_ids_from_state(state)
    if len(attached_image_ids) != 1:
        return None, (
            "reconstruct_full_scene requires exactly one image attached to the current request. "
            "It cannot use remembered or historical images."
        )

    thread_id = "default"
    if isinstance(state, dict):
        raw_thread_id = state.get("thread_id")
        if isinstance(raw_thread_id, str) and raw_thread_id.strip():
            thread_id = raw_thread_id

    try:
        assets = get_image_asset_memory().get_assets_by_ids(thread_id, attached_image_ids)
    except Exception as exc:
        return None, (
            "reconstruct_full_scene could not resolve the attached request image: "
            f"{str(exc)}"
        )

    if len(assets) != 1:
        return None, (
            "reconstruct_full_scene could not resolve exactly one attached request image."
        )

    stored_path = str(getattr(assets[0], "stored_path", "")).strip()
    if not stored_path:
        return None, "reconstruct_full_scene found no stored path for the attached request image."
    if not os.path.isfile(stored_path):
        return None, (
            "reconstruct_full_scene resolved an attached request image, but the file is missing: "
            f"{stored_path}"
        )
    return stored_path, None


def _normalize_reconstruct_full_scene_request(
    request: ToolCallRequest,
) -> ToolCallRequest | ToolMessage:
    tool_call = request.tool_call
    if not isinstance(tool_call, dict):
        return request
    if tool_call.get("name") != "reconstruct_full_scene":
        return request

    input_image_path, error_message = _resolve_current_request_image_path(request.state)
    if error_message:
        return _tool_validation_message(tool_call, error_message)

    raw_args = tool_call.get("args")
    updated_args = dict(raw_args) if isinstance(raw_args, dict) else {}
    updated_args["input_image_path"] = input_image_path
    normalized_call = {**tool_call, "args": updated_args}
    return request.override(tool_call=normalized_call)


def _normalize_tool_request(request: ToolCallRequest) -> ToolCallRequest | ToolMessage:
    tool_call = request.tool_call
    if not isinstance(tool_call, dict):
        return request

    reconstruct_request = _normalize_reconstruct_full_scene_request(request)
    if isinstance(reconstruct_request, ToolMessage):
        return reconstruct_request
    request = reconstruct_request
    tool_call = request.tool_call if isinstance(request.tool_call, dict) else tool_call

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
    if isinstance(working_request, ToolMessage):
        return working_request
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
    if isinstance(working_request, ToolMessage):
        return working_request
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


def _route_after_loop_checkpoint(
    state: AgentState,
) -> Literal["agent", "builder_agent", "checkpoint_finalize"]:
    if _agent_turn_budget_exhausted(state):
        return "checkpoint_finalize"
    if _is_dual_plan_mode(state):
        return "builder_agent"
    return "agent"


def _route_after_finalize_checkpoint(state: AgentState) -> str:
    if _task_mode(state) != "plan_mode":
        return END
    finalize_guard = state.get("finalize_guard")
    if isinstance(finalize_guard, dict):
        status = str(finalize_guard.get("status") or "").strip().lower()
        if status == "continue":
            if _agent_turn_budget_exhausted(state):
                return "finalize"
            if _is_dual_plan_mode(state):
                return "builder_agent"
            return "agent"
    return "finalize"


def _route_after_finalize_guard(
    state: AgentState,
) -> str:
    return _route_after_finalize_checkpoint(state)


def _route_after_blocked_recovery_action(
    state: AgentState,
) -> Literal["tools", "agent", "builder_agent"]:
    messages = state.get("messages") or []
    for message in reversed(list(messages)):
        if isinstance(message, AIMessage):
            if _message_has_tool_calls(message):
                return "tools"
            if _is_dual_plan_mode(state):
                return "builder_agent"
            return "agent"
    if _is_dual_plan_mode(state):
        return "builder_agent"
    return "agent"


def _route_after_verify(
    state: AgentState,
) -> Literal["quality_evaluator"]:
    _ = state
    return "quality_evaluator"


def _route_after_transition_resolver(
    state: AgentState,
) -> Literal["agent", "builder_agent", "planner_refresh", "checkpoint_finalize"]:
    transition_next = state.get("transition_next")
    if transition_next == "agent":
        return "agent"
    if transition_next == "builder_agent":
        return "builder_agent"
    if transition_next == "planner_refresh":
        return "planner_refresh"
    if transition_next == "checkpoint_finalize":
        return "checkpoint_finalize"
    if _is_dual_plan_mode(state):
        return "builder_agent"
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
    primary_chat_model = vlm_provider.get_chat_model()
    context_summary_model = _build_context_summary_helper_model(
        provider_name=selected_provider,
        api_key=selected_api_key,
        settings=settings,
    )
    verifier_provider_name, verifier_model_name, verifier_api_key = _resolve_dual_agent_verifier_runtime(
        settings=settings,
        default_provider=selected_provider,
        default_model=selected_model,
        default_api_key=selected_api_key,
    )
    verifier_chat_model = primary_chat_model
    verifier_context_summary_model = context_summary_model
    if (
        verifier_provider_name != selected_provider
        or verifier_model_name != selected_model
        or verifier_api_key != selected_api_key
    ):
        try:
            verifier_provider = get_vlm_provider(
                provider_name=verifier_provider_name,
                api_key=verifier_api_key,
                model=verifier_model_name,
            )
            verifier_chat_model = verifier_provider.get_chat_model()
            verifier_context_summary_model = _build_context_summary_helper_model(
                provider_name=verifier_provider_name,
                api_key=verifier_api_key,
                settings=settings,
            )
        except Exception:
            verifier_provider_name = selected_provider
            verifier_model_name = selected_model
            verifier_api_key = selected_api_key
            verifier_chat_model = primary_chat_model
            verifier_context_summary_model = context_summary_model
    
    # Load tools from Blender MCP server
    tools = await get_blender_tools(session_id=session_id)
    tools.extend(get_internal_agent_tools())
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
    public_tool_names = [name for name in available_tool_names if name != TODO_UPDATE_TOOL_NAME]
    public_tool_hints = {
        name: hint
        for name, hint in available_tool_hints.items()
        if name != TODO_UPDATE_TOOL_NAME
    }
    
    # Bind tools to model
    llm_with_tools = primary_chat_model.bind_tools(tools)
    verifier_llm_with_tools = verifier_chat_model.bind_tools(tools)
    
    # Define agent node with bound tools
    def call_model(state: AgentState) -> dict:
        return agent_node(
            state,
            llm_with_tools,
            available_tool_names,
            summary_model=context_summary_model,
        )

    def call_builder_model(state: AgentState) -> dict:
        return builder_agent_node(
            state,
            llm_with_tools,
            available_tool_names,
            summary_model=context_summary_model,
        )

    def call_verifier_camera_model(state: AgentState) -> dict:
        return verifier_camera_agent_node(
            state,
            verifier_llm_with_tools,
            available_tool_names,
            summary_model=verifier_context_summary_model,
        )

    def call_prepare_reference_context(state: AgentState) -> dict:
        return prepare_reference_context_node(
            state,
            provider_name=selected_provider,
            api_key=selected_api_key,
        )

    def call_sync_reference_catalog(state: AgentState) -> dict:
        return sync_reference_catalog_node(
            state,
            provider_name=selected_provider,
            api_key=selected_api_key,
        )
    
    builder = build_agent_state_graph(
        initialize_request_node=initialize_request_node,
        sync_reference_catalog_node=call_sync_reference_catalog,
        prepare_reference_context_node=call_prepare_reference_context,
        agent_node=call_model,
        turn_dispatch_node=turn_dispatch_node,
        builder_agent_node=call_builder_model,
        post_builder_node=post_builder_node,
        verifier_camera_agent_node=call_verifier_camera_model,
        post_verifier_node=post_verifier_node,
        verifier_feedback_node=verifier_feedback_node,
        quality_evaluator_node=quality_evaluator_node,
        progress_evaluator_node=progress_evaluator_node,
        budget_evaluator_node=budget_evaluator_node,
        tools_node=ToolNode(
            tools,
            handle_tool_errors=False,
            wrap_tool_call=_wrap_tool_call_with_retry,
            awrap_tool_call=_awrap_tool_call_with_retry,
        ),
        todo_commit_node=todo_commit_node,
        update_memory_node=update_memory_node,
        scene_observe_node=scene_observe_node,
        checkpoint_finalize_node=lambda state: checkpoint_gate_node(state, stage="finalize"),
        verify_node=lambda state: verify_node(
            state,
            provider_name=verifier_provider_name if _is_dual_plan_mode(state) else selected_provider,
            api_key=verifier_api_key if _is_dual_plan_mode(state) else selected_api_key,
            model=verifier_model_name if _is_dual_plan_mode(state) else selected_model,
        ),
        transition_resolver_node=transition_resolver_node,
        planner_refresh_node=planner_refresh_node,
        finalize_node=lambda state: finalize_node(state, finalizer_model=primary_chat_model),
        route_after_initialize_request=_route_after_initialize_request,
        route_after_sync_reference_catalog=_route_after_sync_reference_catalog,
        route_after_prepare_reference_context=_route_after_prepare_reference_context,
        route_after_turn_dispatch=_route_after_turn_dispatch,
        route_after_todo_commit=_route_after_todo_commit,
        route_after_post_builder=_route_after_post_builder,
        route_after_post_verifier=_route_after_post_verifier,
        route_after_verify=_route_after_verify,
        route_after_transition_resolver=_route_after_transition_resolver,
        route_after_finalize_checkpoint=_route_after_finalize_checkpoint,
    )
    
    # Compile with checkpointing
    checkpointer = get_graph_checkpointer()
    app = builder.compile(checkpointer=checkpointer)
    setattr(app, "_available_tool_names", available_tool_names)
    setattr(app, "_available_tool_hints", available_tool_hints)
    setattr(app, "_public_tool_names", public_tool_names)
    setattr(app, "_public_tool_hints", public_tool_hints)
    setattr(app, "_vlm_provider", selected_provider)
    setattr(app, "_vlm_model", selected_model)
    setattr(app, "_verifier_vlm_provider", verifier_provider_name)
    setattr(app, "_verifier_vlm_model", verifier_model_name)
    
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
