import asyncio
import time

from scene_agent.interfaces.api import routes_chat


def test_coerce_last_event_id_handles_invalid_values() -> None:
    assert routes_chat._coerce_last_event_id(None) == 0
    assert routes_chat._coerce_last_event_id("") == 0
    assert routes_chat._coerce_last_event_id("abc") == 0
    assert routes_chat._coerce_last_event_id("-5") == 0
    assert routes_chat._coerce_last_event_id("7") == 7


def test_promote_stream_timeout_seconds_for_plan_mode() -> None:
    class Settings:
        api_stream_timeout_seconds = 120
        api_plan_stream_timeout_seconds = 1800

    settings = Settings()
    assert routes_chat._promote_stream_timeout_seconds(120, "conversation_mode", settings) == 120
    assert routes_chat._promote_stream_timeout_seconds(120, "plan_mode", settings) == 1800

    settings.api_plan_stream_timeout_seconds = 0
    assert routes_chat._promote_stream_timeout_seconds(120, "plan_mode", settings) is None


def test_active_stream_session_sequences_events_and_builds_heartbeat() -> None:
    session = routes_chat._ActiveStreamSession(
        stream_request_id="stream-1",
        thread_id="thread-1",
        request_id="request-1",
    )
    session.note_task_mode("plan_mode")
    session.note_graph_node("route_mode", 1)
    session.note_todos(
        [
            {"id": "todo-1", "status": "completed"},
            {"id": "todo-2", "status": "pending"},
        ]
    )
    session.set_scene_has_change(True)
    session.publish({"delta": "hello"})
    session.publish({"event": "done"})

    payloads = session.snapshot_after(0)
    assert [payload["seq"] for payload in payloads] == [1, 2]
    assert [payload["seq"] for payload in session.snapshot_after(1)] == [2]

    heartbeat = routes_chat._build_heartbeat_payload(session)
    assert heartbeat["event"] == "heartbeat"
    assert heartbeat["stream_request_id"] == "stream-1"
    progress = heartbeat["progress"]
    assert progress["task_mode"] == "plan_mode"
    assert progress["graph_steps"] == 1
    assert progress["last_node"] == "route_mode"
    assert progress["todo_total"] == 2
    assert progress["todo_completed"] == 1
    assert progress["scene_has_change"] is True
    assert progress["latest_seq"] == 2


def test_active_stream_session_request_stop_records_reason_once() -> None:
    session = routes_chat._ActiveStreamSession(
        stream_request_id="stream-2",
        thread_id="thread-2",
        request_id="request-2",
    )
    assert session.request_stop(reason="ownership_lost") is True
    assert session.should_stop() is True
    assert session.termination_reason == "ownership_lost"
    assert session.request_stop(reason="another_reason") is False
    assert session.termination_reason == "ownership_lost"


def test_run_stream_runtime_heartbeat_touches_local_session_and_registry(monkeypatch) -> None:
    manager = routes_chat.get_session_manager().__class__()
    local_session = manager.ensure("thread-heartbeat", "headless")
    local_session.last_active_at = time.time() - 120
    local_session.shutdown_in_progress = True
    stream_session = routes_chat._ActiveStreamSession(
        stream_request_id="stream-hb",
        thread_id="thread-heartbeat",
        request_id="request-hb",
        lease_epoch=7,
    )
    touched: list[tuple[str, int | None]] = []

    class Coordinator:
        def touch_activity(self, thread_id: str, lease_epoch: int | None = None) -> None:
            touched.append((thread_id, lease_epoch))
            stream_session.mark_done()

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(routes_chat, "get_session_manager", lambda: manager)
    monkeypatch.setattr(routes_chat, "get_session_coordinator", lambda: Coordinator())
    monkeypatch.setattr(routes_chat.asyncio, "sleep", fake_sleep)

    before = local_session.last_active_at
    asyncio.run(
        routes_chat._run_stream_runtime_heartbeat(
            session=stream_session,
            interval_seconds=1.0,
        )
    )

    refreshed = manager.get("thread-heartbeat")
    assert refreshed is not None
    assert refreshed.last_active_at >= before
    assert refreshed.shutdown_in_progress is False
    assert touched == [("thread-heartbeat", 7)]


def test_run_stream_lease_heartbeat_requests_stop_on_ownership_loss(monkeypatch) -> None:
    stream_session = routes_chat._ActiveStreamSession(
        stream_request_id="stream-lease",
        thread_id="thread-lease",
        request_id="request-lease",
        lease_token="token-1",
    )

    class Coordinator:
        def refresh_lease_if_owned(self, thread_id: str, lease_token: str | None) -> bool:
            assert thread_id == "thread-lease"
            assert lease_token == "token-1"
            return False

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(routes_chat, "get_session_coordinator", lambda: Coordinator())
    monkeypatch.setattr(routes_chat.asyncio, "sleep", fake_sleep)

    asyncio.run(
        routes_chat._run_stream_lease_heartbeat(
            session=stream_session,
            interval_seconds=1.0,
        )
    )

    payloads = stream_session.snapshot_after(0)
    assert stream_session.should_stop() is True
    assert stream_session.termination_reason == "ownership_lost"
    assert payloads
    assert payloads[0]["reason"] == "ownership_lost"
    assert "ownership was lost" in payloads[0]["error"]
