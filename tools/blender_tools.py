"""
Blender MCP tool integration using langchain-mcp-adapters.
Automatically loads tools from the Blender MCP server.
"""
import asyncio
import os
from typing import List, Any
from config import get_settings
from blender.session_manager import (
    allocate_headless_port,
    allocate_mcp_port,
    build_headless_command_args,
    build_mcp_command_args,
    get_session_manager,
    start_headless_process,
    start_mcp_process,
)


async def get_blender_tools(session_id: str | None = None) -> List[Any]:
    """
    Get all tools from the Blender MCP server using langchain-mcp-adapters.
    
    This uses the native MCP integration following LangGraph best practices.
    See: https://github.com/langchain-ai/langchain-mcp-adapters
    
    Returns:
        List of LangChain-compatible tools from the Blender MCP server
        
    Raises:
        Exception: If unable to connect to Blender MCP server
    """
    settings = get_settings()
    mcp_url = settings.blender_mcp_url

    if settings.blender_mode == "headless" and session_id:
        manager = get_session_manager()
        session = manager.ensure(session_id, "headless")
        host = os.getenv("BLENDER_HEADLESS_HOST", settings.blender_host)
        base_port = int(os.getenv("BLENDER_HEADLESS_BASE_PORT", "9876"))
        port_range = int(os.getenv("BLENDER_HEADLESS_PORT_RANGE", "1"))
        port = allocate_headless_port(session_id, base_port, port_range)
        manager.set_endpoint(session_id, host, port)

        command, args = build_headless_command_args(session_id, host, port)
        with session.lock:
            start_headless_process(session, command, args)

        mcp_host = os.getenv("BLENDER_MCP_HOST", "localhost")
        mcp_base_port = int(os.getenv("BLENDER_MCP_BASE_PORT", "9877"))
        mcp_range = int(os.getenv("BLENDER_MCP_PORT_RANGE", "1"))
        mcp_port = allocate_mcp_port(session_id, mcp_base_port, mcp_range)
        manager.set_mcp_endpoint(session_id, mcp_host, mcp_port)

        mcp_command, mcp_args = build_mcp_command_args(session_id, mcp_host, mcp_port)
        env = os.environ.copy()
        env.update(
            {
                "MCP_SERVER_HOST": mcp_host,
                "MCP_SERVER_PORT": str(mcp_port),
                "BLENDER_HOST": host,
                "BLENDER_PORT": str(port),
            }
        )
        with session.lock:
            start_mcp_process(session, mcp_command, mcp_args, env)

        mcp_url = f"http://{mcp_host}:{mcp_port}/mcp"
    
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
        
        # Create MCP client for Blender server
        client = MultiServerMCPClient({
            "blender": {
                "transport": "http",
                "url": mcp_url
            }
        })
        
        # Get all tools from the Blender server
        tools = await client.get_tools()
        
        print(f"✓ Loaded {len(tools)} tools from Blender MCP server")
        return tools
        
    except ImportError as e:
        raise ImportError(
            "langchain-mcp-adapters not installed. "
            "Install with: pip install langchain-mcp-adapters"
        ) from e
    except Exception as e:
        import traceback 
        traceback.print_exc()
        raise Exception(
            f"Failed to connect to Blender MCP server at {settings.blender_mcp_url}. "
            f"Make sure the server is running. Error: {str(e)}"
        ) from e


def get_blender_tools_sync() -> List[Any]:
    """
    Synchronous wrapper for get_blender_tools().
    Useful for non-async contexts.
    
    Returns:
        List of tools from Blender MCP server
    """
    return asyncio.run(get_blender_tools())
