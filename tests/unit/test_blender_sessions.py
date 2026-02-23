import pytest

from scene_agent.blender.session_manager import (
    SessionManager,
    allocate_headless_port_strict,
    allocate_mcp_port_strict,
)


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


def test_allocate_headless_port_strict_raises_when_range_exhausted():
    with pytest.raises(RuntimeError, match="No available headless port"):
        allocate_headless_port_strict(
            "thread-exhausted",
            base_port=9900,
            range_size=2,
            used_ports={9900},
            blocked_ports={9901},
        )


def test_allocate_mcp_port_strict_respects_headless_blocked_ports():
    port = allocate_mcp_port_strict(
        "thread-conflict-safe",
        base_port=9950,
        range_size=3,
        used_ports={9950},
        blocked_ports={9951},
    )
    assert port == 9952
