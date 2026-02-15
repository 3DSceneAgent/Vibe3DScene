from __future__ import annotations

from scene_agent.blender.session_manager import SessionManager, get_session_manager


class LocalRuntimeManager:
    """Thin wrapper that keeps session_manager focused on local runtime handles."""

    def __init__(self, manager: SessionManager | None = None) -> None:
        self._manager = manager or get_session_manager()

    @property
    def manager(self) -> SessionManager:
        return self._manager

    def list_sessions(self):
        return self._manager.list_sessions()

    def get_idle_sessions(self):
        return self._manager.get_idle_sessions()

    def shutdown_if_idle(self, session_id: str, timeout: float = 5.0):
        return self._manager.shutdown_if_idle(session_id, timeout=timeout)
