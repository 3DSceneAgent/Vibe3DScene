"""
LangGraph state machine construction.
Creates the agent graph following LangGraph best practices.
"""
from typing import Literal
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import BaseMessage

from agent.state import AgentState
from agent.nodes import agent_node, update_memory_node
from config import get_settings
from vlm import get_vlm_provider
from tools import get_blender_tools


async def create_agent_graph(session_id: str | None = None):
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
    vlm_provider = get_vlm_provider(
        provider_name=settings.vlm_provider,
        api_key=settings.vlm_api_key,
        model=settings.vlm_model
    )
    model = vlm_provider.get_chat_model()
    
    # Load tools from Blender MCP server
    tools = await get_blender_tools(session_id=session_id)
    
    # Bind tools to model
    llm_with_tools = model.bind_tools(tools)
    
    # Define agent node with bound tools
    def call_model(state: AgentState) -> dict:
        return agent_node(state, llm_with_tools)
    
    # Build graph
    builder = StateGraph(AgentState)
    
    # Add nodes
    builder.add_node("agent", call_model)
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("update_memory", update_memory_node)
    
    # Connect nodes
    builder.add_edge(START, "agent")
    
    # Conditional edge: agent -> tools if tool_calls exist
    builder.add_conditional_edges(
        "agent",
        tools_condition,  # Built-in routing function
    )
    
    # After tools, update memory then back to agent
    builder.add_edge("tools", "update_memory")
    builder.add_edge("update_memory", "agent")
    
    # Compile with checkpointing
    memory = MemorySaver()
    app = builder.compile(checkpointer=memory)
    
    return app


def create_agent_graph_sync(session_id: str | None = None):
    """Synchronous wrapper for create_agent_graph"""
    import asyncio
    return asyncio.run(create_agent_graph(session_id=session_id))
