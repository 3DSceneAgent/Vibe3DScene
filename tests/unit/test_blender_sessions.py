from scene_agent.blender.session_manager import SessionManager


def test_session_manager_lifecycle():
    manager = SessionManager()
    session = manager.ensure("session-1", "local-client")
    assert session.session_id == "session-1"
    assert session.mode == "local-client"
    assert session.status == "starting"

    manager.set_ready("session-1", connection="conn")
    session = manager.get("session-1")
    assert session is not None
    assert session.status == "ready"
    assert session.connection == "conn"

    manager.set_error("session-1", "boom")
    session = manager.get("session-1")
    assert session is not None
    assert session.status == "error"
    assert session.error == "boom"

    manager.close("session-1")
    session = manager.get("session-1")
    assert session is not None
    assert session.status == "closed"


def test_session_manager_list_sessions():
    manager = SessionManager()
    manager.ensure("a", "local-client")
    manager.ensure("b", "headless")
    sessions = manager.list_sessions()
    assert {session.session_id for session in sessions} == {"a", "b"}
