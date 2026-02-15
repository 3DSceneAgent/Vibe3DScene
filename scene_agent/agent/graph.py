"""
LangGraph state machine construction.
Creates the agent graph following LangGraph best practices.
"""
import re
from typing import Literal
from langgraph.graph import StateGraph, START, END
from langchain_core.messages import AIMessage
from langgraph.prebuilt import ToolNode


from scene_agent.agent.state import AgentState
from scene_agent.agent.redis_checkpointer import get_graph_checkpointer
from scene_agent.agent.nodes import (
    agent_node,
    checkpoint_gate_node,
    finalize_node,
    post_agent_node,
    todo_check_node,
    update_memory_node,
    verify_node,
)
from scene_agent.config import get_settings
from scene_agent.vlm import get_vlm_provider
from scene_agent.tools import get_blender_tools


def _extract_tool_hint(tool: object) -> str | None:
    description = getattr(tool, "description", None)
    if not isinstance(description, str):
        return None
    normalized = re.sub(r"\s+", " ", description).strip()
    if not normalized:
        return None
    return normalized


def _route_verify_or_agent(state: AgentState) -> Literal["verify", "agent"]:
    render_path = state.get("last_render_path")
    if not render_path:
        return "agent"
    if state.get("last_verified_path") == render_path:
        return "agent"
    decision = state.get("agent_decision") or {}
    should_verify = decision.get("should_verify")
    if isinstance(should_verify, bool) and should_verify:
        return "verify"
    return "agent"


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


def _route_after_post_agent(state: AgentState) -> Literal["tools", "checkpoint_finalize"]:
    messages = state.get("messages") or []
    for message in reversed(list(messages)):
        if isinstance(message, AIMessage):
            return "tools" if _message_has_tool_calls(message) else "checkpoint_finalize"
    return "checkpoint_finalize"


def _should_run_todo_check(state: AgentState) -> bool:
    gate = state.get("todo_check_gate")
    if not isinstance(gate, dict):
        return False
    return bool(gate.get("should_run"))


def _route_after_loop_checkpoint(
    state: AgentState,
) -> Literal["todo_check", "verify", "agent"]:
    if _should_run_todo_check(state):
        return "todo_check"
    return _route_verify_or_agent(state)


def _route_after_finalize_checkpoint(state: AgentState) -> Literal["todo_check", "finalize"]:
    if _should_run_todo_check(state):
        return "todo_check"
    return "finalize"


def _route_after_todo_check(state: AgentState) -> Literal["finalize", "verify", "agent"]:
    gate = state.get("todo_check_gate")
    if isinstance(gate, dict) and gate.get("stage") == "finalize":
        todo_check = state.get("todo_check")
        if isinstance(todo_check, dict):
            status = todo_check.get("status")
            if status in {"completed", "blocked", "not_applicable"}:
                return "finalize"
            return _route_verify_or_agent(state)
        return "finalize"
    return _route_verify_or_agent(state)


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
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("update_memory", update_memory_node)
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
    builder.add_node("finalize", finalize_node)
    
    # Connect nodes
    builder.add_edge(START, "agent")

    # Persist decision/todo after each assistant response, then branch.
    builder.add_edge("agent", "post_agent")
    builder.add_conditional_edges(
        "post_agent",
        _route_after_post_agent,
    )
    
    # After tools, update memory then pass sparse todo-check gate.
    builder.add_edge("tools", "update_memory")
    builder.add_edge("update_memory", "checkpoint_loop")
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
    builder.add_edge("verify", "agent")
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
