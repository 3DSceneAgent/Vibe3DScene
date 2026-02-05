from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
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


def allocate_headless_port(session_id: str, base_port: int, range_size: int) -> int:
    if range_size <= 1:
        return base_port
    return base_port + (abs(hash(session_id)) % range_size)


def allocate_mcp_port(session_id: str, base_port: int, range_size: int) -> int:
    return allocate_headless_port(session_id, base_port, range_size)


def build_headless_command_args(session_id: str, host: str, port: int) -> tuple[str | None, list[str]]:
    command = os.getenv("BLENDER_HEADLESS_CMD")
    if not command:
        return None, []
    raw_args = os.getenv("BLENDER_HEADLESS_ARGS", "")
    args = [
        arg.format(session_id=session_id, host=host, port=port)
        for arg in shlex.split(raw_args)
    ]
    return command, args


def build_mcp_command_args(session_id: str, host: str, port: int) -> tuple[str | None, list[str]]:
    command = os.getenv("BLENDER_MCP_CMD", "python")
    raw_args = os.getenv("BLENDER_MCP_ARGS", "mcp/server.py")
    args = [
        arg.format(session_id=session_id, host=host, port=port)
        for arg in shlex.split(raw_args)
    ]
    return command, args


def start_headless_process(session: BlenderSession, command: str | None, args: list[str]) -> None:
    if not command:
        return
    if session.process and session.process.poll() is None:
        return
    try:
        print(f"Starting headless Blender process: {command} {args}")
        session.process = subprocess.Popen(
            [command, *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
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
        session.mcp_process = subprocess.Popen(
            [command, *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    except Exception as exc:
        session.status = "error"
        session.error = f"Failed to start MCP server: {exc}"
