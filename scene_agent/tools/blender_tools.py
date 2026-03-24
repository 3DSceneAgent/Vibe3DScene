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
from scene_agent.session import get_session_coordinator
from scene_agent.blender.session_manager import (
    SessionResourceError,
    SessionResourceReason,
    allocate_headless_port_strict,
    allocate_mcp_port_strict,
    build_headless_command_args,
    build_mcp_command_args,
    get_session_manager,
    start_headless_process,
    start_mcp_process,
)


def _worker_capacity() -> int:
    coordinator = get_session_coordinator()
    raw_count = coordinator.worker_count()
    if isinstance(raw_count, int) and raw_count > 0:
        return raw_count
    return 1


def _active_headless_session_count(host: str) -> int:
    coordinator = get_session_coordinator()
    reserved = coordinator.reserved_port_count(host=host, kind="headless")
    if isinstance(reserved, int) and reserved >= 0:
        return reserved
    manager = get_session_manager()
    return sum(
        1
        for session in manager.list_sessions()
        if session.port is not None and (session.host == host or session.host is None)
    )


def _build_capacity_error(
    *,
    reason: SessionResourceReason,
    message: str,
    headless_range: int,
    mcp_range: int | None = None,
    host: str | None = None,
    active_sessions: int | None = None,
    mcp_host: str | None = None,
    used_mcp_ports: int | None = None,
) -> SessionResourceError:
    coordinator = get_session_coordinator()
    worker_capacity = _worker_capacity()
    limits: dict[str, Any] = {
        "worker_capacity": worker_capacity,
        "headless_port_capacity": max(1, int(headless_range)),
    }
    if mcp_range is not None:
        limits["mcp_port_capacity"] = max(1, int(mcp_range))
    in_use: dict[str, Any] = {}
    if active_sessions is None and host:
        active_sessions = _active_headless_session_count(host)
    if active_sessions is not None:
        in_use["active_headless_sessions"] = int(active_sessions)
    if mcp_host:
        reserved_mcp = coordinator.reserved_port_count(host=mcp_host, kind="mcp")
        if isinstance(reserved_mcp, int) and reserved_mcp >= 0:
            in_use["reserved_mcp_ports"] = reserved_mcp
        elif used_mcp_ports is not None:
            in_use["reserved_mcp_ports"] = int(used_mcp_ports)
    return SessionResourceError(
        error=message,
        reason=reason,
        limits=limits,
        in_use=in_use,
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
    stub_profile = os.getenv("SCENE_AGENT_TOOL_STUB_PROFILE", "").strip().lower()
    if stub_profile:
        if stub_profile == "image_routing_compare":
            from scene_agent.tools.test_stub_tools import build_image_routing_compare_stub_tools

            return build_image_routing_compare_stub_tools()
        if stub_profile == "sam3d_reconstruct":
            from scene_agent.tools.test_stub_tools import build_sam3d_reconstruct_stub_tools

            return build_sam3d_reconstruct_stub_tools()
        raise RuntimeError(f"Unsupported SCENE_AGENT_TOOL_STUB_PROFILE: {stub_profile}")

    settings = get_settings()
    mcp_url = settings.blender_mcp_url
    coordinator = get_session_coordinator()
    manager = get_session_manager()
    headless_session = None
    host: str | None = None
    mcp_host: str | None = None
    port: int | None = None
    mcp_port: int | None = None
    port_range = max(1, int(os.getenv("BLENDER_HEADLESS_PORT_RANGE", "16")))
    mcp_range = max(1, int(os.getenv("BLENDER_MCP_PORT_RANGE", "16")))
    newly_allocated_headless_port = False
    newly_allocated_mcp_port = False
    started_headless_process = False
    started_mcp_process = False

    def rollback_partial_runtime() -> None:
        if not session_id or headless_session is None:
            return
        if not (
            newly_allocated_headless_port
            or newly_allocated_mcp_port
            or started_headless_process
            or started_mcp_process
        ):
            return
        try:
            manager.terminate_session_processes(session_id, timeout=3.0)
        except Exception:
            pass
        if newly_allocated_headless_port:
            coordinator.release_port(
                host=host or settings.blender_host,
                kind="headless",
                port=port,
            )
        if newly_allocated_mcp_port:
            coordinator.release_port(
                host=mcp_host or os.getenv("BLENDER_MCP_HOST", "localhost"),
                kind="mcp",
                port=mcp_port,
            )

    try:
        if settings.blender_mode == "headless" and session_id:
            session = manager.ensure(session_id, "headless")
            headless_session = session
            manager.ensure_session_storage(session_id)
            host = os.getenv("BLENDER_HEADLESS_HOST", settings.blender_host)
            base_port = int(os.getenv("BLENDER_HEADLESS_BASE_PORT", "9876"))
            if session.port is None:
                worker_capacity = _worker_capacity()
                active_sessions = _active_headless_session_count(host)
                if active_sessions >= worker_capacity:
                    raise _build_capacity_error(
                        reason="process_capacity_exhausted",
                        message=(
                            "Cannot create new headless session: process capacity is exhausted. "
                            f"Active sessions={active_sessions}, worker capacity={worker_capacity}."
                        ),
                        headless_range=port_range,
                        host=host,
                        active_sessions=active_sessions,
                    )

                headless_reservation = coordinator.reserve_port_detailed(
                    host=host,
                    kind="headless",
                    base_port=base_port,
                    range_size=port_range,
                    seed=session_id,
                )
                if headless_reservation.status == "reserved" and headless_reservation.port is not None:
                    port = headless_reservation.port
                elif headless_reservation.status == "exhausted":
                    raise _build_capacity_error(
                        reason="blender_port_exhausted",
                        message=(
                            "Cannot create new headless session: no available Blender port in configured range."
                        ),
                        headless_range=port_range,
                        host=host,
                        active_sessions=active_sessions,
                    )
                else:
                    used_headless_ports = {
                        int(item.port)
                        for item in manager.list_sessions()
                        if item.port is not None and (item.host == host or item.host is None)
                    }
                    blocked_mcp_ports = {
                        int(item.mcp_port)
                        for item in manager.list_sessions()
                        if item.mcp_port is not None and (item.mcp_host == host or item.mcp_host is None)
                    }
                    try:
                        port = allocate_headless_port_strict(
                            session_id,
                            base_port,
                            port_range,
                            used_ports=used_headless_ports,
                            blocked_ports=blocked_mcp_ports,
                        )
                    except RuntimeError as exc:
                        raise _build_capacity_error(
                            reason="blender_port_exhausted",
                            message=(
                                "Cannot create new headless session: no available Blender port in configured range."
                            ),
                            headless_range=port_range,
                            host=host,
                            active_sessions=active_sessions,
                        ) from exc
                manager.set_endpoint(session_id, host, port)
                newly_allocated_headless_port = True
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
                process_running_before = session.process is not None and session.process.poll() is None
                start_headless_process(session, command, args, env=headless_env)
                process_running_after = session.process is not None and session.process.poll() is None
                if not process_running_before and process_running_after:
                    started_headless_process = True

            mcp_host = os.getenv("BLENDER_MCP_HOST", "localhost")
            mcp_base_port = int(os.getenv("BLENDER_MCP_BASE_PORT", "9877"))
            if session.mcp_port is None:
                mcp_reservation = coordinator.reserve_port_detailed(
                    host=mcp_host,
                    kind="mcp",
                    base_port=mcp_base_port,
                    range_size=mcp_range,
                    seed=session_id,
                )
                if mcp_reservation.status == "reserved" and mcp_reservation.port is not None:
                    mcp_port = mcp_reservation.port
                elif mcp_reservation.status == "exhausted":
                    raise _build_capacity_error(
                        reason="mcp_port_exhausted",
                        message="Cannot create new headless session: no available MCP port in configured range.",
                        headless_range=port_range,
                        mcp_range=mcp_range,
                        host=host,
                        mcp_host=mcp_host,
                    )
                else:
                    used_mcp_ports = {
                        int(item.mcp_port)
                        for item in manager.list_sessions()
                        if item.mcp_port is not None and (item.mcp_host == mcp_host or item.mcp_host is None)
                    }
                    blocked_headless_ports = {
                        int(item.port)
                        for item in manager.list_sessions()
                        if item.port is not None and (item.host == mcp_host or item.host is None)
                    }
                    try:
                        mcp_port = allocate_mcp_port_strict(
                            session_id,
                            mcp_base_port,
                            mcp_range,
                            used_ports=used_mcp_ports,
                            blocked_ports=blocked_headless_ports,
                        )
                    except RuntimeError as exc:
                        raise _build_capacity_error(
                            reason="mcp_port_exhausted",
                            message="Cannot create new headless session: no available MCP port in configured range.",
                            headless_range=port_range,
                            mcp_range=mcp_range,
                            host=host,
                            mcp_host=mcp_host,
                            used_mcp_ports=len(used_mcp_ports),
                        ) from exc
                manager.set_mcp_endpoint(session_id, mcp_host, mcp_port)
                newly_allocated_mcp_port = True
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
                mcp_running_before = session.mcp_process is not None and session.mcp_process.poll() is None
                start_mcp_process(session, mcp_command, mcp_args, env)
                mcp_running_after = session.mcp_process is not None and session.mcp_process.poll() is None
                if not mcp_running_before and mcp_running_after:
                    started_mcp_process = True

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

        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient(
            {
                "blender": {
                    "transport": "http",
                    "url": mcp_url,
                }
            }
        )

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

        if headless_session is not None and session_id is not None:
            _assert_headless_runtime_processes(headless_session)
            coordinator.touch_activity(session_id)
            coordinator.update_session_runtime_fields(
                session_id,
                {
                    "host": host,
                    "blender_port": port,
                    "mcp_port": mcp_port,
                    "storage_dir": headless_session.storage_dir,
                    "blend_path": headless_session.blend_path,
                    "snapshot_dir": headless_session.snapshot_dir,
                    "status": "ready",
                },
            )

        print(f"✓ Loaded {len(tools)} tools from Blender MCP server")
        return tools
    except SessionResourceError:
        rollback_partial_runtime()
        raise
    except ImportError as e:
        rollback_partial_runtime()
        raise ImportError(
            "langchain-mcp-adapters not installed. "
            "Install with: pip install langchain-mcp-adapters"
        ) from e
    except Exception as e:
        rollback_partial_runtime()
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
