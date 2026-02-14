"""
Blender MCP tool integration using langchain-mcp-adapters.
Automatically loads tools from the Blender MCP server.
"""
import asyncio
import json
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
    headless_session = None

    if settings.blender_mode == "headless" and session_id:
        manager = get_session_manager()
        session = manager.ensure(session_id, "headless")
        headless_session = session
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

        try:
            probe_timeout = float(
                os.getenv(
                    "BLENDER_MCP_TOOL_PROBE_TIMEOUT",
                    str(max(1, settings.blender_headless_startup_timeout)),
                )
            )
        except ValueError:
            probe_timeout = float(max(1, settings.blender_headless_startup_timeout))

        await _verify_mcp_blender_connectivity(
            tools,
            timeout_seconds=max(1.0, probe_timeout),
        )

        if headless_session is not None:
            _assert_headless_runtime_processes(headless_session)
        
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


def _assert_headless_runtime_processes(session: Any) -> None:
    if session.process is None:
        raise Exception("Headless Blender process is not running for this session.")
    if session.process.poll() is not None:
        detail = _tail_log_file(session.log_path)
        raise Exception(
            "Headless Blender process exited unexpectedly "
            f"(code: {session.process.returncode}).\n{detail}"
        )

    if session.mcp_process is None:
        raise Exception("Headless MCP server process is not running for this session.")
    if session.mcp_process.poll() is not None:
        detail = _tail_log_file(session.mcp_log_path)
        raise Exception(
            "Headless MCP server process exited unexpectedly "
            f"(code: {session.mcp_process.returncode}).\n{detail}"
        )


def _tail_log_file(log_path: str | None, max_chars: int = 2000) -> str:
    if not log_path:
        return "Log: n/a"
    if not os.path.exists(log_path):
        return f"Log file not found: {log_path}"
    try:
        with open(log_path, "r") as log_file:
            content = log_file.read()
    except OSError:
        return f"Failed to read log: {log_path}"
    if len(content) <= max_chars:
        return content
    return content[-max_chars:]


async def _verify_mcp_blender_connectivity(
    tools: list[Any],
    *,
    timeout_seconds: float,
) -> None:
    probe_tool = _find_tool_by_name(tools, "get_scene_info")
    if probe_tool is None:
        raise Exception("MCP tool probe failed: missing required tool 'get_scene_info'.")

    deadline = time.time() + timeout_seconds
    last_error = "unknown error"
    while time.time() < deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        try:
            result = await asyncio.wait_for(_invoke_tool(probe_tool), timeout=remaining)
        except asyncio.TimeoutError:
            last_error = (
                f"Probe tool 'get_scene_info' timed out after {timeout_seconds:.1f}s."
            )
            break
        except Exception as exc:
            last_error = str(exc)
            await asyncio.sleep(0.2)
            continue

        probe_error = _extract_probe_error(result)
        if probe_error is None:
            return

        last_error = probe_error
        await asyncio.sleep(0.2)

    raise Exception(
        "MCP server is reachable but cannot access Blender through MCP tool "
        f"'get_scene_info' within {timeout_seconds:.1f}s. Last error: {last_error}"
    )


def _find_tool_by_name(tools: list[Any], name: str) -> Any | None:
    for tool in tools:
        tool_name = getattr(tool, "name", None)
        if isinstance(tool_name, str) and tool_name == name:
            return tool
    return None


async def _invoke_tool(tool: Any) -> Any:
    candidates = ({}, "", None)

    if hasattr(tool, "ainvoke"):
        for payload in candidates:
            try:
                return await tool.ainvoke(payload)
            except TypeError:
                continue

    if hasattr(tool, "arun"):
        for payload in candidates:
            try:
                return await tool.arun(payload)
            except TypeError:
                continue

    if hasattr(tool, "invoke"):
        for payload in candidates:
            try:
                return await asyncio.to_thread(tool.invoke, payload)
            except TypeError:
                continue

    if hasattr(tool, "run"):
        for payload in candidates:
            try:
                return await asyncio.to_thread(tool.run, payload)
            except TypeError:
                continue

    raise Exception("Probe tool does not support invoke/ainvoke/run/arun.")


def _extract_probe_error(result: Any) -> str | None:
    text = _coerce_result_text(result)
    if not text:
        return "Probe tool returned empty response."

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, (dict, list)):
        return None
    if isinstance(parsed, str):
        text = parsed.strip()

    lowered = text.lower()
    error_markers = (
        "error getting scene info",
        "could not connect to blender",
        "not connected to blender",
        "connection to blender lost",
        "communication error with blender",
    )
    if lowered.startswith("error") or any(marker in lowered for marker in error_markers):
        return text
    return None


def _coerce_result_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, dict):
        content = result.get("content")
        if content is not None:
            return _coerce_result_text(content)
        text = result.get("text")
        if isinstance(text, str):
            return text.strip()
        return json.dumps(result, ensure_ascii=False, default=str)
    if isinstance(result, list):
        parts = [_coerce_result_text(item) for item in result]
        return "".join(part for part in parts if part).strip()
    if hasattr(result, "content"):
        return _coerce_result_text(getattr(result, "content"))
    return str(result).strip()
