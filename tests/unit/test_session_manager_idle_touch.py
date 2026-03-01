from scene_agent.blender.session_manager import SessionManager


def test_touch_session_marks_session_active() -> None:
    manager = SessionManager()
    session = manager.ensure("session-a", "headless")
    session.last_active_at -= 120
    session.shutdown_in_progress = True

    before = session.last_active_at
    assert manager.touch_session("session-a") is True

    refreshed = manager.get("session-a")
    assert refreshed is not None
    assert refreshed.last_active_at >= before
    assert refreshed.shutdown_in_progress is False


def test_touch_session_returns_false_when_missing() -> None:
    manager = SessionManager()
    assert manager.touch_session("missing-session") is False
