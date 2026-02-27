"""Node implementations by category."""
from .execution import extract_todo_updates
from typing import Any, Dict
from scene_agent.agent.memory_scope import merge_role_private_memory
from scene_agent.agent.state import AgentState
from .shared import ROLE_BUILDER, ROLE_GENERAL, ROLE_VERIFIER
from .shared import (
    ai_message_has_tool_calls,
    align_todo_updates_with_existing,
    coerce_non_negative_int,
    find_last_ai_message,
    invoke_role_agent,
    message_content_to_text,
)

def agent_node(
    state: AgentState,
    llm_with_tools,
    available_tool_names: list[str] | None = None,
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
    )

def builder_agent_node(
    state: AgentState,
    llm_with_tools,
    available_tool_names: list[str] | None = None,
) -> Dict[str, Any]:
    """
    Builder agent node for dual-agent plan_mode execution.
    """
    return invoke_role_agent(
        state=state,
        llm_with_tools=llm_with_tools,
        available_tool_names=available_tool_names,
        role=ROLE_BUILDER,
    )

def verifier_camera_agent_node(
    state: AgentState,
    llm_with_tools,
    available_tool_names: list[str] | None = None,
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
    )

def post_agent_node(state: AgentState) -> Dict[str, Any]:
    """
    Post-agent node: persist todo updates and per-request counters after each assistant turn.
    """
    last_messages = state["messages"][-10:]
    latest_ai_message = find_last_ai_message(last_messages)
    result: Dict[str, Any] = {}

    if latest_ai_message is not None:
        todo_updates = extract_todo_updates([latest_ai_message])
        aligned_todos = align_todo_updates_with_existing(state.get("todos"), todo_updates)
        if aligned_todos:
            result["todos"] = aligned_todos

    current_turns = coerce_non_negative_int(state.get("request_agent_turns"))
    next_turns = current_turns + 1
    result["request_agent_turns"] = next_turns

    max_turns = coerce_non_negative_int(state.get("max_request_agent_turns"), default=-1)
    if max_turns >= 0 and next_turns >= max_turns:
        result["request_stop_reason"] = "agent_turn_budget_exhausted"

    return result

def post_builder_node(state: AgentState) -> Dict[str, Any]:
    """
    Post-builder node used in plan_mode dual-agent execution.
    """
    last_messages = state["messages"][-10:]
    latest_ai_message = find_last_ai_message(last_messages)
    result: Dict[str, Any] = {"active_role": ROLE_BUILDER}

    if latest_ai_message is not None:
        todo_updates = extract_todo_updates([latest_ai_message])
        aligned_todos = align_todo_updates_with_existing(state.get("todos"), todo_updates)
        if aligned_todos:
            result["todos"] = aligned_todos

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
