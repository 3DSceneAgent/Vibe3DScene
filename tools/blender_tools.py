"""
Blender MCP tool integration using langchain-mcp-adapters.
Automatically loads tools from the Blender MCP server.
"""
import asyncio
from typing import List, Any
from config import get_settings


async def get_blender_tools() -> List[Any]:
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
    
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
        
        # Create MCP client for Blender server
        client = MultiServerMCPClient({
            "blender": {
                "transport": "http",
                "url": settings.blender_mcp_url
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
