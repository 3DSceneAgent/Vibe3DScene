"""Node implementations by category."""
import copy
from typing import Any, Dict
from langchain_core.messages import ToolMessage
from scene_agent.agent.memory_scope import merge_role_private_memory
from scene_agent.agent.state import AgentState
from scene_agent.agent.todo_protocol import TODO_UPDATE_TOOL_NAME, parse_todo_update_request
from .shared import ROLE_BUILDER, ROLE_GENERAL, ROLE_VERIFIER
from .shared import (
    ai_message_has_tool_calls,
    coerce_non_negative_int,
    find_last_ai_message,
    invoke_role_agent,
    message_content_to_text,
)

def agent_node(
    state: AgentState,
    llm_with_tools,
    available_tool_names: list[str] | None = None,
    summary_model: Any | None = None,
) -> Dict[str, Any]:
    """
    Agent node: VLM reasoning with all tools bound.
    The agent decides when to perceive, render, and manipulate the scene.
    
    Args:
        state: Current agent state
        llm_with_tools: LLM with tools bound via bind_tools()
        
    Returns:
        Partial state update with new messages
    """
    return invoke_role_agent(
        state=state,
        llm_with_tools=llm_with_tools,
        available_tool_names=available_tool_names,
        role=ROLE_GENERAL,
        summary_model=summary_model,
    )

def builder_agent_node(
    state: AgentState,
    llm_with_tools,
    available_tool_names: list[str] | None = None,
    summary_model: Any | None = None,
) -> Dict[str, Any]:
    """
    Builder agent node for dual-agent plan_mode execution.
    """
    return invoke_role_agent(
        state=state,
        llm_with_tools=llm_with_tools,
        available_tool_names=available_tool_names,
        role=ROLE_BUILDER,
        summary_model=summary_model,
    )

def verifier_camera_agent_node(
    state: AgentState,
    llm_with_tools,
    available_tool_names: list[str] | None = None,
    summary_model: Any | None = None,
) -> Dict[str, Any]:
    """
    Tool-capable verifier agent.

    This role can operate camera/render inspection tools (including camera
    adjustments) but is blocked from scene asset mutation tools.
    """
    return invoke_role_agent(
        state=state,
        llm_with_tools=llm_with_tools,
        available_tool_names=available_tool_names,
        role=ROLE_VERIFIER,
        summary_model=summary_model,
    )

def _tool_calls_from_message(message: Any) -> list[dict[str, Any]]:
    if message is None:
        return []
    raw_tool_calls = getattr(message, "tool_calls", None)
    if isinstance(raw_tool_calls, list):
        return [call for call in raw_tool_calls if isinstance(call, dict)]
    additional_kwargs = getattr(message, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        nested_calls = additional_kwargs.get("tool_calls")
        if isinstance(nested_calls, list):
            return [call for call in nested_calls if isinstance(call, dict)]
    return []


def _replace_ai_message_tool_calls(message: Any, tool_calls: list[dict[str, Any]]) -> Any:
    updated = copy.deepcopy(message)
    updated.tool_calls = tool_calls
    additional_kwargs = getattr(updated, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        if tool_calls:
            additional_kwargs["tool_calls"] = tool_calls
        else:
            additional_kwargs.pop("tool_calls", None)
    return updated


def turn_dispatch_node(state: AgentState) -> Dict[str, Any]:
    """Deterministically split internal todo calls from external tool calls."""
    last_messages = state["messages"][-10:]
    latest_ai_message = find_last_ai_message(last_messages)
    result: Dict[str, Any] = {}

    if latest_ai_message is not None:
        tool_calls = _tool_calls_from_message(latest_ai_message)
        pending_todo_updates: list[dict[str, Any]] = []
        external_tool_calls: list[dict[str, Any]] = []
        dispatch_messages: list[Any] = []

        for tool_call in tool_calls:
            tool_name = tool_call.get("name")
            if tool_name != TODO_UPDATE_TOOL_NAME:
                external_tool_calls.append(tool_call)
                continue

            tool_call_id = tool_call.get("id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                tool_call_id = "todo_update_dispatch"
            try:
                request = parse_todo_update_request(tool_call.get("args"))
            except Exception as exc:
                dispatch_messages.append(
                    ToolMessage(
                        name=TODO_UPDATE_TOOL_NAME,
                        content=f"Invalid todo_update payload: {exc}",
                        tool_call_id=tool_call_id,
                        status="error",
                    )
                )
                continue

            pending_todo_updates.append(
                {
                    "tool_call_id": tool_call_id,
                    "request": request.model_dump(mode="json"),
                }
            )

        saw_todo_calls = len(tool_calls) > len(external_tool_calls)
        if saw_todo_calls:
            sanitized = _replace_ai_message_tool_calls(latest_ai_message, external_tool_calls)
            dispatch_messages.insert(0, sanitized)
            result["messages"] = dispatch_messages
            result["pending_todo_updates"] = pending_todo_updates
            if external_tool_calls:
                result["assistant_turn_kind"] = "mixed"
            else:
                result["assistant_turn_kind"] = "todo_only"
        elif external_tool_calls:
            result["assistant_turn_kind"] = "external_only"
        else:
            result["assistant_turn_kind"] = "no_calls"

    current_turns = coerce_non_negative_int(state.get("request_agent_turns"))
    next_turns = current_turns + 1
    result["request_agent_turns"] = next_turns

    max_turns = coerce_non_negative_int(state.get("max_request_agent_turns"), default=-1)
    if max_turns >= 0 and next_turns >= max_turns:
        result["request_stop_reason"] = "agent_turn_budget_exhausted"

    return result


def post_agent_node(state: AgentState) -> Dict[str, Any]:
    """Compatibility alias for the old post-agent stage."""
    return turn_dispatch_node(state)

def post_builder_node(state: AgentState) -> Dict[str, Any]:
    """
    Post-builder node used in plan_mode dual-agent execution.
    """
    last_messages = state["messages"][-10:]
    latest_ai_message = find_last_ai_message(last_messages)
    result: Dict[str, Any] = {"active_role": ROLE_BUILDER}

    current_turns = coerce_non_negative_int(state.get("request_agent_turns"))
    next_turns = current_turns + 1
    result["request_agent_turns"] = next_turns

    current_builder_turns = coerce_non_negative_int(state.get("builder_turn_count"))
    result["builder_turn_count"] = current_builder_turns + 1

    if ai_message_has_tool_calls(latest_ai_message):
        result["builder_stall_count"] = 0
    else:
        stall = coerce_non_negative_int(state.get("builder_stall_count"))
        result["builder_stall_count"] = stall + 1

    max_turns = coerce_non_negative_int(state.get("max_request_agent_turns"), default=-1)
    if max_turns >= 0 and next_turns >= max_turns:
        result["request_stop_reason"] = "agent_turn_budget_exhausted"

    if latest_ai_message is not None:
        builder_note = message_content_to_text(latest_ai_message.content).strip()
        if builder_note:
            result["role_private_memory"] = merge_role_private_memory(
                state.get("role_private_memory"),
                role=ROLE_BUILDER,
                patch={
                    "last_action_summary": builder_note[:1200],
                },
            )

    return result

def post_verifier_node(state: AgentState) -> Dict[str, Any]:
    """
    Post-verifier node used in plan_mode dual-agent execution.
    """
    last_messages = state["messages"][-10:]
    latest_ai_message = find_last_ai_message(last_messages)
    result: Dict[str, Any] = {"active_role": ROLE_VERIFIER}

    current_turns = coerce_non_negative_int(state.get("request_agent_turns"))
    next_turns = current_turns + 1
    result["request_agent_turns"] = next_turns

    current_verifier_turns = coerce_non_negative_int(state.get("verifier_turn_count"))
    result["verifier_turn_count"] = current_verifier_turns + 1

    max_turns = coerce_non_negative_int(state.get("max_request_agent_turns"), default=-1)
    if max_turns >= 0 and next_turns >= max_turns:
        result["request_stop_reason"] = "agent_turn_budget_exhausted"

    if latest_ai_message is not None:
        verifier_note = message_content_to_text(latest_ai_message.content).strip()
        if verifier_note:
            result["role_private_memory"] = merge_role_private_memory(
                state.get("role_private_memory"),
                role=ROLE_VERIFIER,
                patch={
                    "last_verifier_action_summary": verifier_note[:1200],
                },
            )

    return result
