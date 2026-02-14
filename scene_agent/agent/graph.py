"""
LangGraph state machine construction.
Creates the agent graph following LangGraph best practices.
"""
from typing import Literal
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver


from scene_agent.agent.state import AgentState
from scene_agent.agent.nodes import agent_node, update_memory_node, verify_node
from scene_agent.config import get_settings
from scene_agent.vlm import get_vlm_provider
from scene_agent.tools import get_blender_tools


def _route_after_update(state: AgentState) -> Literal["verify", "agent"]:
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
    
    # Bind tools to model
    llm_with_tools = model.bind_tools(tools)
    
    # Define agent node with bound tools
    def call_model(state: AgentState) -> dict:
        return agent_node(state, llm_with_tools, available_tool_names)
    
    # Build graph
    builder = StateGraph(AgentState)
    
    # Add nodes
    builder.add_node("agent", call_model)
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("update_memory", update_memory_node)
    builder.add_node(
        "verify",
        lambda state: verify_node(
            state,
            provider_name=selected_provider,
            api_key=selected_api_key,
            model=selected_model,
        ),
    )
    
    # Connect nodes
    builder.add_edge(START, "agent")
    
    # Conditional edge: agent -> tools if tool_calls exist
    builder.add_conditional_edges(
        "agent",
        tools_condition,  # Built-in routing function
    )
    
    # After tools, update memory then verify if needed
    builder.add_edge("tools", "update_memory")
    builder.add_conditional_edges(
        "update_memory",
        _route_after_update,
    )
    builder.add_edge("verify", "agent")
    
    # Compile with checkpointing
    memory = MemorySaver()
    app = builder.compile(checkpointer=memory)
    setattr(app, "_available_tool_names", available_tool_names)
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
