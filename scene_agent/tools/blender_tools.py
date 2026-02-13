"""
Blender MCP tool integration using langchain-mcp-adapters.
Automatically loads tools from the Blender MCP server.
"""
import asyncio
import os
import socket
import time
from typing import List, Any
from scene_agent.config import get_settings
from scene_agent.blender.session_manager import (
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
        manager.ensure_session_storage(session_id)
        host = os.getenv("BLENDER_HEADLESS_HOST", settings.blender_host)
        base_port = int(os.getenv("BLENDER_HEADLESS_BASE_PORT", "9876"))
        port_range = int(os.getenv("BLENDER_HEADLESS_PORT_RANGE", "16"))
        if session.port is None:
            used_ports = {item.port for item in manager.list_sessions() if item.port}
            port = allocate_headless_port(
                session_id,
                base_port,
                port_range,
                used_ports=used_ports,
            )
            manager.set_endpoint(session_id, host, port)
        else:
            port = session.port

        command, args = build_headless_command_args(
            session_id,
            host,
            port,
            blend_path=session.blend_path,
        )
        headless_env = os.environ.copy()
        headless_env.update(
            {
                "SESSION_ID": session_id,
                "SESSION_STORAGE_DIR": session.storage_dir or "",
                "SESSION_BLEND_PATH": session.blend_path or "",
                "SESSION_SNAPSHOT_DIR": session.snapshot_dir or "",
                "SESSION_MAX_SNAPSHOTS": str(session.max_snapshots),
                "SESSION_IDLE_TIMEOUT_SECONDS": str(session.idle_timeout_seconds),
                "SESSION_BLEND_ROOT": settings.session_blend_root,
                "SESSION_PERSISTENCE_ENABLED": "1",
            }
        )
        with session.lock:
            start_headless_process(session, command, args, env=headless_env)

        mcp_host = os.getenv("BLENDER_MCP_HOST", "localhost")
        mcp_base_port = int(os.getenv("BLENDER_MCP_BASE_PORT", "9877"))
        mcp_range = int(os.getenv("BLENDER_MCP_PORT_RANGE", "16"))
        if session.mcp_port is None:
            used_mcp_ports = {item.mcp_port for item in manager.list_sessions() if item.mcp_port}
            mcp_port = allocate_mcp_port(
                session_id,
                mcp_base_port,
                mcp_range,
                used_ports=used_mcp_ports,
            )
            manager.set_mcp_endpoint(session_id, mcp_host, mcp_port)
        else:
            mcp_port = session.mcp_port

        mcp_command, mcp_args = build_mcp_command_args(session_id, mcp_host, mcp_port)
        env = os.environ.copy()
        env.update(
            {
                "MCP_SERVER_HOST": mcp_host,
                "MCP_SERVER_PORT": str(mcp_port),
                "BLENDER_HOST": host,
                "BLENDER_PORT": str(port),
                "SESSION_ID": session_id,
                "SESSION_STORAGE_DIR": session.storage_dir or "",
                "SESSION_BLEND_PATH": session.blend_path or "",
                "SESSION_SNAPSHOT_DIR": session.snapshot_dir or "",
                "SESSION_MAX_SNAPSHOTS": str(session.max_snapshots),
                "SESSION_IDLE_TIMEOUT_SECONDS": str(session.idle_timeout_seconds),
                "SESSION_PERSISTENCE_ENABLED": "1",
            }
        )
        with session.lock:
            start_mcp_process(session, mcp_command, mcp_args, env)

        startup_timeout = float(os.getenv("BLENDER_MCP_STARTUP_TIMEOUT", "10"))
        deadline = time.time() + startup_timeout
        while time.time() < deadline:
            if session.mcp_process and session.mcp_process.poll() is not None:
                log_path = session.mcp_log_path
                log_detail = ""
                if log_path and os.path.exists(log_path):
                    try:
                        with open(log_path, "r") as log_file:
                            log_detail = log_file.read()
                    except OSError:
                        log_detail = ""
                raise Exception(
                    "MCP server exited before becoming ready. "
                    f"Log: {log_path or 'n/a'}\n{log_detail}"
                )
            try:
                await asyncio.to_thread(_probe_tcp, mcp_host, mcp_port)
                break
            except OSError:
                await asyncio.sleep(0.2)
        else:
            raise Exception(
                f"MCP server at {mcp_host}:{mcp_port} did not become ready within "
                f"{startup_timeout}s. Check {session.mcp_log_path or 'MCP logs'}."
            )

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
            f"Failed to connect to Blender MCP server at {mcp_url}. "
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


def _probe_tcp(host: str, port: int) -> None:
    with socket.create_connection((host, port), timeout=0.5):
        return
