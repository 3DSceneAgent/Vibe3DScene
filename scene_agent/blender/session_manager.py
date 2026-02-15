from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
import sys
import re
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
    storage_dir: Optional[str] = None
    blend_path: Optional[str] = None
    snapshot_dir: Optional[str] = None
    max_snapshots: int = 20
    idle_timeout_seconds: int = 600
    last_persisted_at: Optional[float] = None
    shutdown_in_progress: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def mark_active(self) -> None:
        self.last_active_at = time.time()
        self.shutdown_in_progress = False


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
                self._ensure_storage_paths(existing)
                existing.mark_active()
                return existing

            session = BlenderSession(session_id=session_id, mode=mode)
            self._ensure_storage_paths(session)
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

    def get_idle_sessions(self, now_ts: Optional[float] = None) -> List[BlenderSession]:
        now = now_ts if now_ts is not None else time.time()
        idle_sessions: list[BlenderSession] = []
        with self._lock:
            for session in self._sessions.values():
                if self._is_session_idle(session, now):
                    idle_sessions.append(session)
        return idle_sessions

    def ensure_session_storage(self, session_id: str) -> Optional[str]:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            self._ensure_storage_paths(session)
            return session.storage_dir

    def persist_session_blend(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or not session.blend_path:
                return False
            blend_path = session.blend_path
            connection = session.connection
            process = session.process

        if process is not None and process.poll() is not None:
            process = None

        if process is None:
            return os.path.exists(blend_path)

        if connection is None or not hasattr(connection, "send_command"):
            return os.path.exists(blend_path)

        try:
            result = connection.send_command("save_blend", {"filepath": blend_path})
            success = bool(result.get("success", True)) if isinstance(result, dict) else True
            if success:
                with self._lock:
                    current = self._sessions.get(session_id)
                    if current is not None:
                        current.last_persisted_at = time.time()
                return True
            return False
        except Exception:
            return False

    def terminate_session_processes(self, session_id: str, timeout: float = 5.0) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            process = session.process
            mcp_process = session.mcp_process

        self._terminate_process(process, timeout)
        self._terminate_process(mcp_process, timeout)

        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            if session.connection and hasattr(session.connection, "disconnect"):
                try:
                    session.connection.disconnect()
                except Exception:
                    pass
            session.connection = None
            session.process = None
            session.mcp_process = None
            session.status = "closed"

    def restart_session_processes(self, session_id: str, timeout: float = 5.0) -> None:
        self.terminate_session_processes(session_id, timeout=timeout)
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.status = "starting"
            session.error = None
            session.mark_active()

    def shutdown_if_idle(
        self,
        session_id: str,
        timeout: float = 5.0,
    ) -> tuple[bool, bool]:
        """Attempt idle shutdown with race-safe rechecks.

        Returns:
            (stopped, persisted)
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False, False
            if session.shutdown_in_progress:
                return False, False
            now = time.time()
            if not self._is_session_idle(session, now):
                return False, False
            session.shutdown_in_progress = True

        persisted = False
        stopped = False
        try:
            # Serialize with request-side startup using per-session lock.
            with session.lock:
                with self._lock:
                    current = self._sessions.get(session_id)
                    if current is None or current is not session:
                        return False, False
                    if not self._is_session_idle(
                        current,
                        time.time(),
                        allow_shutdown_in_progress=True,
                    ):
                        return False, False

                try:
                    persisted = self.persist_session_blend(session_id)
                except Exception:
                    persisted = False

                with self._lock:
                    current = self._sessions.get(session_id)
                    if current is None or current is not session:
                        return False, persisted
                    # Re-check idleness right before terminate to avoid mid-request teardown.
                    if not self._is_session_idle(
                        current,
                        time.time(),
                        allow_shutdown_in_progress=True,
                    ):
                        return False, persisted
                    process = current.process
                    mcp_process = current.mcp_process

                self._terminate_process(process, timeout)
                self._terminate_process(mcp_process, timeout)

                with self._lock:
                    current = self._sessions.get(session_id)
                    if current is None or current is not session:
                        return False, persisted
                    if current.connection and hasattr(current.connection, "disconnect"):
                        try:
                            current.connection.disconnect()
                        except Exception:
                            pass
                    current.connection = None
                    current.process = None
                    current.mcp_process = None
                    current.status = "closed"
                stopped = True
                return True, persisted
        finally:
            with self._lock:
                current = self._sessions.get(session_id)
                if current is session:
                    current.shutdown_in_progress = False
        return stopped, persisted

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
                if session.connection and hasattr(session.connection, "disconnect"):
                    try:
                        session.connection.disconnect()
                    except Exception:
                        pass
                session.connection = None

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

    @staticmethod
    def _safe_session_id(session_id: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", session_id).strip("._")
        return safe or "session"

    def _ensure_storage_paths(self, session: BlenderSession) -> None:
        root = os.getenv("SESSION_SHARED_STORAGE_ROOT") or os.getenv("SESSION_BLEND_ROOT")
        max_snapshots_raw = os.getenv("SESSION_MAX_SNAPSHOTS")
        idle_timeout_raw = os.getenv("SESSION_IDLE_TIMEOUT_SECONDS")
        if root is None or max_snapshots_raw is None or idle_timeout_raw is None:
            try:
                from scene_agent.config import get_settings

                settings = get_settings()
                if root is None:
                    root = settings.session_blend_root
                if max_snapshots_raw is None:
                    max_snapshots_raw = str(settings.session_max_snapshots)
                if idle_timeout_raw is None:
                    idle_timeout_raw = str(settings.session_idle_timeout_seconds)
            except Exception:
                pass
        root = root or "/tmp/scene_agent_sessions"
        try:
            max_snapshots = int(max_snapshots_raw or "20")
        except ValueError:
            max_snapshots = 20
        try:
            idle_timeout = int(idle_timeout_raw or "600")
        except ValueError:
            idle_timeout = 600
        safe_id = self._safe_session_id(session.session_id)
        storage_dir = os.path.join(root, safe_id)
        snapshot_dir = os.path.join(storage_dir, "snapshots")
        os.makedirs(snapshot_dir, exist_ok=True)
        session.storage_dir = storage_dir
        session.blend_path = os.path.join(storage_dir, "scene.blend")
        session.snapshot_dir = snapshot_dir
        session.max_snapshots = max(1, max_snapshots)
        session.idle_timeout_seconds = max(0, idle_timeout)

    @staticmethod
    def _is_session_idle(
        session: BlenderSession,
        now: float,
        *,
        allow_shutdown_in_progress: bool = False,
    ) -> bool:
        if session.mode != "headless":
            return False
        if session.idle_timeout_seconds <= 0:
            return False
        if session.shutdown_in_progress and not allow_shutdown_in_progress:
            return False
        if session.process is None and session.mcp_process is None:
            return False
        return now - session.last_active_at >= session.idle_timeout_seconds


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


def build_headless_command_args(
    session_id: str,
    host: str,
    port: int,
    blend_path: str | None = None,
) -> tuple[str | None, list[str]]:
    command = os.getenv("BLENDER_HEADLESS_CMD")
    if not command:
        print("BLENDER_HEADLESS_CMD is not set")
        return None, []
    raw_args = os.getenv("BLENDER_HEADLESS_ARGS", "")
    args = [
        arg.format(session_id=session_id, host=host, port=port)
        for arg in shlex.split(raw_args)
    ]
    if blend_path and os.path.exists(blend_path):
        args = [blend_path, *args]
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


def start_headless_process(
    session: BlenderSession,
    command: str | None,
    args: list[str],
    env: Optional[dict[str, str]] = None,
) -> None:
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

            proc_env = env or os.environ.copy()
            session.process = subprocess.Popen(
                [command, *args],
                stdout=log_file,
                stderr=log_file,
                env=proc_env,
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
