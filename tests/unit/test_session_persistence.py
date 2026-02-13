import time

from scene_agent.blender.session_manager import SessionManager


class DummyProcess:
    def __init__(self):
        self.terminated = False
        self.killed = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


class DummyConnection:
    def __init__(self):
        self.calls = []
        self.disconnected = False

    def send_command(self, command_type, params=None):
        self.calls.append((command_type, params or {}))
        if command_type == "save_blend":
            return {"success": True, "filepath": params.get("filepath")}
        return {"success": True}

    def disconnect(self):
        self.disconnected = True


def test_persist_session_blend_uses_addon_save(tmp_path):
    manager = SessionManager()
    session = manager.ensure("persist-session", "headless")
    blend_path = tmp_path / "scene.blend"
    session.blend_path = str(blend_path)
    session.process = DummyProcess()
    connection = DummyConnection()
    session.connection = connection

    persisted = manager.persist_session_blend("persist-session")

    assert persisted is True
    assert connection.calls
    command_name, params = connection.calls[-1]
    assert command_name == "save_blend"
    assert params["filepath"] == str(blend_path)
    assert session.last_persisted_at is not None


def test_get_idle_sessions_respects_idle_timeout():
    manager = SessionManager()
    session = manager.ensure("idle-session", "headless")
    session.process = DummyProcess()
    session.mcp_process = DummyProcess()
    session.idle_timeout_seconds = 600
    session.last_active_at = time.time() - 700

    idle_sessions = manager.get_idle_sessions(now_ts=time.time())

    assert len(idle_sessions) == 1
    assert idle_sessions[0].session_id == "idle-session"


def test_terminate_session_processes_clears_runtime_state():
    manager = SessionManager()
    session = manager.ensure("terminate-session", "headless")
    process = DummyProcess()
    mcp_process = DummyProcess()
    connection = DummyConnection()
    session.process = process
    session.mcp_process = mcp_process
    session.connection = connection

    manager.terminate_session_processes("terminate-session", timeout=0.1)

    session = manager.get("terminate-session")
    assert session is not None
    assert session.process is None
    assert session.mcp_process is None
    assert session.connection is None
    assert session.status == "closed"
    assert process.terminated is True
    assert mcp_process.terminated is True
    assert connection.disconnected is True


def test_shutdown_if_idle_rechecks_before_terminate():
    manager = SessionManager()
    session = manager.ensure("idle-race-session", "headless")
    process = DummyProcess()
    mcp_process = DummyProcess()
    session.process = process
    session.mcp_process = mcp_process
    session.idle_timeout_seconds = 1
    session.last_active_at = time.time() - 10

    def fake_persist(_session_id):
        # Simulate a concurrent request that refreshed activity.
        session.last_active_at = time.time()
        return True

    manager.persist_session_blend = fake_persist  # type: ignore[assignment]
    stopped, persisted = manager.shutdown_if_idle("idle-race-session")

    assert persisted is True
    assert stopped is False
    assert process.terminated is False
    assert mcp_process.terminated is False
