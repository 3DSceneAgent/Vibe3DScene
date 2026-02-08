from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Optional, Literal, Any, List

SessionMode = Literal["local-client", "headless"]
SessionStatus = Literal["starting", "ready", "error", "closed"]


@dataclass
class BlenderSession:
    session_id: str
    mode: SessionMode
    status: SessionStatus = "starting"
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)
    host: Optional[str] = None
    port: Optional[int] = None
    mcp_host: Optional[str] = None
    mcp_port: Optional[int] = None
    connection: Optional[Any] = None
    error: Optional[str] = None
    process: Optional[subprocess.Popen] = None
    mcp_process: Optional[subprocess.Popen] = None
    log_path: Optional[str] = None
    mcp_log_path: Optional[str] = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def mark_active(self) -> None:
        self.last_active_at = time.time()


class SessionManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: Dict[str, BlenderSession] = {}

    def get(self, session_id: str) -> Optional[BlenderSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def ensure(self, session_id: str, mode: SessionMode) -> BlenderSession:
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                existing.mark_active()
                return existing

            session = BlenderSession(session_id=session_id, mode=mode)
            self._sessions[session_id] = session
            return session

    def set_ready(self, session_id: str, connection: Any) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.connection = connection
            session.status = "ready"
            session.error = None
            session.mark_active()

    def set_error(self, session_id: str, error: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.status = "error"
            session.error = error
            session.mark_active()

    def set_endpoint(self, session_id: str, host: str, port: int) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.host = host
            session.port = port
            session.mark_active()

    def set_process(self, session_id: str, process: subprocess.Popen) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.process = process
            session.mark_active()

    def set_mcp_endpoint(self, session_id: str, host: str, port: int) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.mcp_host = host
            session.mcp_port = port
            session.mark_active()

    def set_mcp_process(self, session_id: str, process: subprocess.Popen) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.mcp_process = process
            session.mark_active()

    def close(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.status = "closed"
            session.mark_active()

    def remove(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def list_sessions(self) -> List[BlenderSession]:
        with self._lock:
            return list(self._sessions.values())

    def shutdown_all(self, timeout: float = 5.0) -> None:
        sessions = self.list_sessions()
        for session in sessions:
            self._terminate_process(session.process, timeout)
            self._terminate_process(session.mcp_process, timeout)
            with self._lock:
                session.status = "closed"
                session.mark_active()
                session.process = None
                session.mcp_process = None

    @staticmethod
    def _terminate_process(process: Optional[subprocess.Popen], timeout: float) -> None:
        if process is None:
            return
        try:
            if process.poll() is not None:
                return
        except Exception:
            return
        try:
            process.terminate()
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except Exception:
                return
        except Exception:
            return


_session_manager = SessionManager()


def get_session_manager() -> SessionManager:
    return _session_manager


def allocate_headless_port(
    session_id: str,
    base_port: int,
    range_size: int,
    used_ports: set[int] | None = None,
) -> int:
    if range_size <= 1:
        return base_port
    if not used_ports:
        return base_port + (abs(hash(session_id)) % range_size)
    start = abs(hash(session_id)) % range_size
    for offset in range(range_size):
        candidate = base_port + ((start + offset) % range_size)
        if candidate not in used_ports:
            return candidate
    return base_port + start


def allocate_mcp_port(
    session_id: str,
    base_port: int,
    range_size: int,
    used_ports: set[int] | None = None,
) -> int:
    return allocate_headless_port(
        session_id,
        base_port,
        range_size,
        used_ports=used_ports,
    )


def build_headless_command_args(session_id: str, host: str, port: int) -> tuple[str | None, list[str]]:
    command = os.getenv("BLENDER_HEADLESS_CMD")
    if not command:
        print("BLENDER_HEADLESS_CMD is not set")
        return None, []
    raw_args = os.getenv("BLENDER_HEADLESS_ARGS", "")
    args = [
        arg.format(session_id=session_id, host=host, port=port)
        for arg in shlex.split(raw_args)
    ]
    return command, args


def build_mcp_command_args(session_id: str, host: str, port: int) -> tuple[str | None, list[str]]:
    command = os.getenv("BLENDER_MCP_CMD") or sys.executable
    raw_args = os.getenv("BLENDER_MCP_ARGS", "mcp_server/server.py")
    args = [
        arg.format(session_id=session_id, host=host, port=port)
        for arg in shlex.split(raw_args)
    ]
    return command, args


def _default_project_root() -> Path | None:
    root = Path(__file__).resolve().parents[2]
    if (root / "mcp_server").exists():
        return root
    return None


def start_headless_process(session: BlenderSession, command: str | None, args: list[str]) -> None:
    if not command:
        error_msg = "BLENDER_HEADLESS_CMD environment variable not set"
        print(f"ERROR: {error_msg}")
        session.status = "error"
        session.error = error_msg
        return
        
    if session.process and session.process.poll() is None:
        print(f"Process already running for {session.session_id} (PID: {session.process.pid})")
        return
        
    try:
        full_command = f"{command} {' '.join(args)}"
        print(f"Starting headless Blender for session: {session.session_id}")
        print(f"  Command: {full_command}")
        
        log_dir = os.getenv("BLENDER_HEADLESS_LOG_DIR", "/tmp/scene_agent_headless_logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"headless_{session.session_id}_{int(time.time() * 1000)}.log")
        session.log_path = log_path
        
        with open(log_path, "w") as log_file:
            log_file.write(f"=== Headless Blender Session: {session.session_id} ===\n")
            log_file.write(f"Command: {full_command}\n")
            log_file.write(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            log_file.write("=" * 70 + "\n\n")
            log_file.flush()
            
            session.process = subprocess.Popen(
                [command, *args],
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        
        print(f"  Process started (PID: {session.process.pid})")
        print(f"  Log file: {log_path}")
        
        # 验证进程是否立即退出
        time.sleep(1.0)
        if session.process.poll() is not None:
            exit_code = session.process.returncode
            with open(log_path, 'r') as f:
                log_content = f.read()
            
            error_msg = f"Blender process exited immediately with code {exit_code}"
            print(f"ERROR: {error_msg}")
            print(f"Log content:\n{log_content}")
            
            session.status = "error"
            session.error = error_msg
        else:
            print(f"  Process confirmed running")
            
    except Exception as exc:
        error_msg = f"Failed to start headless Blender: {exc}"
        print(f"ERROR: {error_msg}")
        import traceback
        traceback.print_exc()
        session.status = "error"
        session.error = error_msg
        print("ERROR!!!")
        session.status = "error"
        session.error = f"Failed to start headless Blender: {exc}"


def start_mcp_process(
    session: BlenderSession,
    command: str | None,
    args: list[str],
    env: dict[str, str],
) -> None:
    if not command:
        return
    if session.mcp_process and session.mcp_process.poll() is None:
        return
    try:
        env.setdefault("PYTHONUNBUFFERED", "1")
        full_command = f"{command} {' '.join(args)}"
        log_dir = os.getenv("BLENDER_MCP_LOG_DIR", "/tmp/scene_agent_mcp_logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"mcp_{session.session_id}_{int(time.time() * 1000)}.log")
        session.mcp_log_path = log_path
        print(f"Starting MCP server for session: {session.session_id}")
        print(f"  Command: {full_command}")
        print(f"  Env: MCP_SERVER_HOST={env.get('MCP_SERVER_HOST')} MCP_SERVER_PORT={env.get('MCP_SERVER_PORT')}")
        print(f"  Log file: {log_path}")
        project_root = _default_project_root()
        cwd = os.getenv("BLENDER_MCP_CWD") or (str(project_root) if project_root else None)
        
        log_file = open(log_path, "w", buffering=1)
        log_file.write(f"=== MCP Server Session: {session.session_id} ===\n")
        log_file.write(f"Command: {full_command}\n")
        log_file.write(f"Env: MCP_SERVER_HOST={env.get('MCP_SERVER_HOST')} MCP_SERVER_PORT={env.get('MCP_SERVER_PORT')}\n")
        log_file.write(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        log_file.write("=" * 70 + "\n\n")
        log_file.flush()
        
        session.mcp_process = subprocess.Popen(
            [command, *args],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=cwd,
            close_fds=False,
        )
        print(f"  MCP PID: {session.mcp_process.pid}")
        time.sleep(1.5)
        if session.mcp_process.poll() is not None:
            exit_code = session.mcp_process.returncode
            log_file.flush()
            log_file.close()
            try:
                with open(log_path, "r") as read_file:
                    log_content = read_file.read()
            except Exception:
                log_content = "(failed to read log)"
            session.status = "error"
            session.error = f"MCP server exited immediately with code {exit_code}"
            print(f"ERROR: {session.error}")
            print(f"Log content:\n{log_content}")
        else:
            print(f"  MCP process started successfully")
    except Exception as exc:
        session.status = "error"
        session.error = f"Failed to start MCP server: {exc}"
        print(f"ERROR: {session.error}")
        import traceback
        traceback.print_exc()
