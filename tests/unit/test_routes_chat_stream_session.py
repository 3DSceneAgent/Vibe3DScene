import asyncio
import time

from fastapi.testclient import TestClient

from scene_agent.interfaces import api as api_module
from scene_agent.interfaces.api import routes_chat


def test_build_thread_stream_session_response_handles_missing_session() -> None:
    payload = routes_chat._build_thread_stream_session_response("thread-missing", None)

    assert payload.thread_id == "thread-missing"
    assert payload.active is False
    assert payload.resumable is False
    assert payload.stream_request_id is None
    assert payload.latest_seq == 0


def test_create_stream_session_for_thread_rejects_second_active_session() -> None:
    original_sessions = dict(routes_chat._ACTIVE_STREAM_SESSIONS)
    try:
        routes_chat._ACTIVE_STREAM_SESSIONS.clear()
        first_session, first_conflict = routes_chat._create_stream_session_for_thread(
            thread_id="thread-conflict",
            request_id="request-1",
        )
        assert first_session is not None
        assert first_conflict is None

        second_session, second_conflict = routes_chat._create_stream_session_for_thread(
            thread_id="thread-conflict",
            request_id="request-2",
        )

        assert second_session is None
        assert second_conflict is first_session
        payload = routes_chat._build_thread_stream_session_response("thread-conflict", second_conflict)
        assert payload.active is True
        assert payload.resumable is True
        assert payload.stream_request_id == first_session.stream_request_id
    finally:
        routes_chat._ACTIVE_STREAM_SESSIONS.clear()
        routes_chat._ACTIVE_STREAM_SESSIONS.update(original_sessions)


def test_build_thread_stream_session_response_marks_done_session_not_resumable() -> None:
    session = routes_chat._ActiveStreamSession(
        stream_request_id="stream-done",
        thread_id="thread-done",
        request_id="request-done",
    )
    session.mark_done()

    payload = routes_chat._build_thread_stream_session_response("thread-done", session)

    assert payload.active is False
    assert payload.resumable is False
    assert payload.done is True
    assert payload.stream_request_id == "stream-done"


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
    session.note_graph_node("initialize_request", 1)
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
    assert progress["last_node"] == "initialize_request"
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


def test_active_stream_session_tracks_llm_call_progress() -> None:
    session = routes_chat._ActiveStreamSession(
        stream_request_id="stream-telemetry",
        thread_id="thread-telemetry",
        request_id="request-telemetry",
    )

    session.note_llm_call_record(
        {
            "input_tokens": 100,
            "output_tokens": 25,
            "total_tokens": 125,
            "image_input_tokens": 30,
            "has_image_inputs": True,
            "context_limit_tokens": 1048576,
        }
    )

    progress = session.progress_snapshot()
    assert progress["llm_input_tokens"] == 100
    assert progress["llm_output_tokens"] == 25
    assert progress["llm_total_tokens"] == 125
    assert progress["image_input_tokens"] == 30
    assert progress["peak_context_used_tokens"] == 100
    assert progress["peak_context_limit_tokens"] == 1048576


def test_stop_thread_stream_session_route_requests_stop(monkeypatch) -> None:
    session = routes_chat._ActiveStreamSession(
        stream_request_id="stream-stop-route",
        thread_id="thread-stop-route",
        request_id="request-stop-route",
    )

    monkeypatch.setattr(routes_chat, "resolve_frontend_client_id", lambda _request: "client-a")
    monkeypatch.setattr(routes_chat, "ensure_frontend_client_can_manage_thread", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes_chat, "_find_thread_stream_session", lambda *_args, **_kwargs: session)

    with TestClient(api_module.app) as client:
        response = client.post(
            "/threads/thread-stop-route/stream-session/stop",
            json={"stream_request_id": "stream-stop-route"},
            headers={"X-Frontend-Client-Id": "client-a"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted"] is True
    assert payload["already_requested"] is False
    assert payload["done"] is False
    assert session.should_stop() is True


def test_stop_thread_stream_session_route_rejects_mismatched_stream_id(monkeypatch) -> None:
    session = routes_chat._ActiveStreamSession(
        stream_request_id="stream-stop-current",
        thread_id="thread-stop-conflict",
        request_id="request-stop-conflict",
    )

    monkeypatch.setattr(routes_chat, "resolve_frontend_client_id", lambda _request: "client-a")
    monkeypatch.setattr(routes_chat, "ensure_frontend_client_can_manage_thread", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes_chat, "_find_thread_stream_session", lambda *_args, **_kwargs: session)

    with TestClient(api_module.app) as client:
        response = client.post(
            "/threads/thread-stop-conflict/stream-session/stop",
            json={"stream_request_id": "stream-stop-old"},
            headers={"X-Frontend-Client-Id": "client-a"},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["stream_request_id"] == "stream-stop-current"


def test_active_stream_session_caps_history_and_replays_by_sequence(monkeypatch) -> None:
    monkeypatch.setattr(routes_chat, "_STREAM_SESSION_HISTORY_LIMIT", 3)
    session = routes_chat._ActiveStreamSession(
        stream_request_id="stream-cap",
        thread_id="thread-cap",
        request_id="request-cap",
    )

    for idx in range(5):
        session.publish({"delta": f"chunk-{idx}"})

    assert [payload["seq"] for payload in session.snapshot_after(0)] == [3, 4, 5]
    assert [payload["seq"] for payload in session.snapshot_after(3)] == [4, 5]


def test_summarize_stream_event_for_error_handles_non_subscriptable_payload() -> None:
    class DummyResponse:
        status_code = 200

    summary = routes_chat._summarize_stream_event_for_error(("messages", DummyResponse()))

    assert summary["mode"] == "messages"
    payload = summary["payload"]
    assert payload["python_type"] == "DummyResponse"
    assert payload["python_module"] == __name__


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
