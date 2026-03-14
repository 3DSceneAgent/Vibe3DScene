import asyncio
import os
import json

import pytest
import requests
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk
from scene_agent.interfaces import api as api_module
from scene_agent.interfaces.api import routes_chat
from scene_agent.session.session_coordinator import OwnerResolution
from .streaming_helpers import collect_sse_payloads, find_payload


class StubAgent:
    async def astream(self, *_args, **_kwargs):
        yield ("messages", [{"type": "ai", "content": "hello"}])
        yield ("messages", [{"type": "ai", "content": " world"}])
        yield ("messages", [{"type": "tool", "content": {"status": "ok"}, "name": "blender.test"}])


async def fake_get_agent(_thread_id=None):
    return StubAgent()


class FailingAgent:
    async def astream(self, *_args, **_kwargs):
        yield ("messages", [{"type": "ai", "content": "hello"}])
        raise RuntimeError("stream failed")


async def fake_get_failing_agent(_thread_id=None):
    return FailingAgent()


class UpdatesAgent:
    async def astream(self, *_args, **_kwargs):
        yield ("messages", [{"type": "ai", "content": "hello"}])
        yield (
            "updates",
            {
                "verify": {
                    "messages": [
                        {
                            "type": "tool",
                            "name": "verification",
                            "content": {"status": "match", "reason": "ok"},
                        }
                    ],
                    "last_verified_path": "/renders/test.jpg",
                },
                "todo_check": {
                    "todo_check": {"status": "continue", "reason": "pending_todos"},
                    "fast_mode": True,
                },
            },
        )


async def fake_get_updates_agent(_thread_id=None):
    return UpdatesAgent()


class VerifyNodeMessageAgent:
    async def astream(self, *_args, **_kwargs):
        yield (
            AIMessage(content='{"status":"match"}'),
            {"langgraph_node": "verify"},
        )
        yield (
            "updates",
            {
                "verify": {
                    "messages": [
                        {
                            "type": "tool",
                            "name": "verification",
                            "content": {"status": "match", "reason": "ok"},
                        }
                    ],
                },
            },
        )


async def fake_get_verify_message_agent(_thread_id=None):
    return VerifyNodeMessageAgent()


class InitializeRequestInternalMessageAgent:
    async def astream(self, *_args, **_kwargs):
        yield (
            AIMessage(
                content=(
                    '{"intent":"multi_step_scene_action","mode":"plan_mode","confidence":1.0}'
                )
            ),
            {"langgraph_node": "initialize_request"},
        )
        yield (
            AIMessage(content="Visible assistant response"),
            {"langgraph_node": "agent"},
        )


async def fake_get_initialize_request_internal_agent(_thread_id=None):
    return InitializeRequestInternalMessageAgent()


class DuplicateAssistantFromUpdatesAgent:
    async def astream(self, *_args, **_kwargs):
        yield (
            "messages",
            [{"type": "ai", "id": "assistant-final", "content": "Final summary text"}],
        )
        yield (
            "updates",
            {
                "finalize": {
                    "messages": [
                        {"type": "ai", "id": "assistant-final", "content": "Final summary text"}
                    ],
                },
            },
        )


async def fake_get_duplicate_assistant_agent(_thread_id=None):
    return DuplicateAssistantFromUpdatesAgent()


class ToolCallAssistantFallbackAgent:
    async def astream(self, *_args, **_kwargs):
        yield (
            AIMessage(
                id="assistant-tool",
                content="",
                tool_calls=[
                    {
                        "name": "get_scene_info",
                        "args": {},
                        "id": "tool-1",
                        "type": "tool_call",
                    }
                ],
            ),
            {"langgraph_node": "agent"},
        )
        yield (
            "updates",
            {
                "agent": {
                    "messages": [
                        AIMessage(
                            id="assistant-tool",
                            content="<thinking>Inspecting scene before edit.</thinking>Inspecting scene before edit.",
                            tool_calls=[
                                {
                                    "name": "get_scene_info",
                                    "args": {},
                                    "id": "tool-1",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    ],
                },
            },
        )


async def fake_get_tool_call_assistant_fallback_agent(_thread_id=None):
    return ToolCallAssistantFallbackAgent()


class ToolCallOnlyAssistantAgent:
    async def astream(self, *_args, **_kwargs):
        yield (
            AIMessage(
                id="assistant-tool-only",
                content="",
                tool_calls=[
                    {
                        "name": "clear_scene",
                        "args": {},
                        "id": "tool-1",
                        "type": "tool_call",
                    },
                    {
                        "name": "execute_blender_code",
                        "args": {"code": "print('hi')"},
                        "id": "tool-2",
                        "type": "tool_call",
                    },
                ],
            ),
            {"langgraph_node": "agent"},
        )
        yield (
            "updates",
            {
                "agent": {
                    "messages": [
                        AIMessage(
                            id="assistant-tool-only",
                            content="",
                            tool_calls=[
                                {
                                    "name": "clear_scene",
                                    "args": {},
                                    "id": "tool-1",
                                    "type": "tool_call",
                                },
                                {
                                    "name": "execute_blender_code",
                                    "args": {"code": "print('hi')"},
                                    "id": "tool-2",
                                    "type": "tool_call",
                                },
                            ],
                        )
                    ],
                },
            },
        )


async def fake_get_tool_call_only_assistant_agent(_thread_id=None):
    return ToolCallOnlyAssistantAgent()


class QwenReasoningStreamAgent:
    async def astream(self, *_args, **_kwargs):
        yield (
            AIMessageChunk(
                id="assistant-qwen",
                content="",
                additional_kwargs={"reasoning_content": "Need to inspect the scene first. "},
            ),
            {"langgraph_node": "agent"},
        )
        yield (
            AIMessageChunk(
                id="assistant-qwen",
                content="",
                additional_kwargs={"reasoning_content": "Then I can call the scene tool."},
            ),
            {"langgraph_node": "agent"},
        )
        yield (
            AIMessageChunk(
                id="assistant-qwen",
                content="",
                tool_calls=[
                    {
                        "name": "get_scene_info",
                        "args": {},
                        "id": "tool-1",
                        "type": "tool_call",
                    }
                ],
                tool_call_chunks=[
                    {
                        "name": "get_scene_info",
                        "args": "{}",
                        "id": "tool-1",
                        "index": 0,
                        "type": "tool_call_chunk",
                    }
                ],
            ),
            {"langgraph_node": "agent"},
        )
        yield (
            "updates",
            {
                "agent": {
                    "messages": [
                        AIMessage(
                            id="assistant-qwen",
                            content="",
                            additional_kwargs={
                                "reasoning_content": "Need to inspect the scene first. Then I can call the scene tool."
                            },
                            tool_calls=[
                                {
                                    "name": "get_scene_info",
                                    "args": {},
                                    "id": "tool-1",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    ],
                },
            },
        )


async def fake_get_qwen_reasoning_stream_agent(_thread_id=None):
    return QwenReasoningStreamAgent()


class ResumableAgent:
    async def astream(self, *_args, **_kwargs):
        yield ("messages", [{"type": "ai", "content": "hello"}])
        await asyncio.sleep(0.2)
        yield ("messages", [{"type": "ai", "content": " world"}])


async def fake_get_resumable_agent(_thread_id=None):
    return ResumableAgent()


class OwnershipLossAgent:
    async def astream(self, *_args, **_kwargs):
        await asyncio.sleep(0.5)
        yield ("messages", [{"type": "ai", "content": "should not arrive"}])


async def fake_get_ownership_loss_agent(_thread_id=None):
    return OwnershipLossAgent()


def test_chat_stream_sse(monkeypatch):
    monkeypatch.setattr(api_module, "get_agent", fake_get_agent)
    client = TestClient(api_module.app)

    with client.stream("POST", "/chat/stream", json={"message": "hi", "thread_id": "t1"}) as response:
        assert response.status_code == 200
        assert response.headers.get("X-Stream-Request-Id")
        payloads = []
        for line in response.iter_lines():
            if not line:
                continue
            if line.startswith("data:"):
                data = line.replace("data:", "", 1).strip()
                payloads.append(json.loads(data))
            if len(payloads) >= 4:
                break

    deltas = [payload["delta"] for payload in payloads if "delta" in payload]
    tool_payloads = [
        payload for payload in payloads if "messages" in payload and payload["messages"][0].get("type") == "tool"
    ]
    assert all("seq" in payload for payload in payloads)
    assert deltas[:2] == ["hello", " world"]
    assert tool_payloads
    assert tool_payloads[0].get("scene_has_change") is True


def test_chat_stream_emits_graph_node_events_and_update_messages(monkeypatch):
    monkeypatch.setattr(api_module, "get_agent", fake_get_updates_agent)
    client = TestClient(api_module.app)

    with client.stream("POST", "/chat/stream", json={"message": "hi", "thread_id": "t-updates"}) as response:
        assert response.status_code == 200
        payloads = collect_sse_payloads(response.iter_lines())

    graph_node_payloads = [payload for payload in payloads if payload.get("event") == "graph_node"]
    assert graph_node_payloads
    node_names = [payload.get("graph_node", {}).get("node") for payload in graph_node_payloads]
    assert "verify" in node_names
    assert "todo_check" in node_names
    assert any(
        payload.get("graph_node", {}).get("state_patch", {}).get("fast_mode") is True
        for payload in graph_node_payloads
    )

    tool_payloads = [
        payload for payload in payloads if "messages" in payload and payload["messages"][0].get("type") == "tool"
    ]
    assert tool_payloads
    assert tool_payloads[0]["messages"][0].get("name") == "verification"


def test_chat_stream_filters_verify_internal_message_stream(monkeypatch):
    monkeypatch.setattr(api_module, "get_agent", fake_get_verify_message_agent)
    client = TestClient(api_module.app)

    with client.stream("POST", "/chat/stream", json={"message": "hi", "thread_id": "t-verify-filter"}) as response:
        assert response.status_code == 200
        payloads = collect_sse_payloads(response.iter_lines())

    deltas = [payload for payload in payloads if "delta" in payload]
    assert not deltas

    tool_payloads = [
        payload for payload in payloads if "messages" in payload and payload["messages"][0].get("type") == "tool"
    ]
    assert tool_payloads
    assert tool_payloads[0]["messages"][0].get("name") == "verification"


def test_chat_stream_filters_initialize_request_internal_message_stream(monkeypatch):
    monkeypatch.setattr(api_module, "get_agent", fake_get_initialize_request_internal_agent)
    client = TestClient(api_module.app)

    with client.stream("POST", "/chat/stream", json={"message": "hi", "thread_id": "t-initialize-request-filter"}) as response:
        assert response.status_code == 200
        payloads = collect_sse_payloads(response.iter_lines())

    deltas = [payload["delta"] for payload in payloads if "delta" in payload]
    assert deltas == ["Visible assistant response"]
    assert all("multi_step_scene_action" not in delta for delta in deltas)

    assistant_messages = [
        payload
        for payload in payloads
        if "messages" in payload and payload["messages"][0].get("type") == "ai"
    ]
    assert not assistant_messages


def test_chat_stream_skips_non_tool_update_messages_after_message_stream(monkeypatch):
    monkeypatch.setattr(api_module, "get_agent", fake_get_duplicate_assistant_agent)
    client = TestClient(api_module.app)

    with client.stream("POST", "/chat/stream", json={"message": "hi", "thread_id": "t-dup-assistant"}) as response:
        assert response.status_code == 200
        payloads = collect_sse_payloads(response.iter_lines())

    deltas = [payload["delta"] for payload in payloads if "delta" in payload]
    assert deltas == ["Final summary text"]

    assistant_messages = [
        payload
        for payload in payloads
        if "messages" in payload and payload["messages"][0].get("type") == "ai"
    ]
    assert not assistant_messages


def test_chat_stream_emits_update_assistant_message_when_message_stream_only_carried_tool_calls(monkeypatch):
    monkeypatch.setattr(api_module, "get_agent", fake_get_tool_call_assistant_fallback_agent)
    client = TestClient(api_module.app)

    with client.stream("POST", "/chat/stream", json={"message": "hi", "thread_id": "t-tool-fallback"}) as response:
        assert response.status_code == 200
        payloads = collect_sse_payloads(response.iter_lines())

    deltas = [payload["delta"] for payload in payloads if "delta" in payload]
    assert deltas == []

    assistant_messages = [
        payload
        for payload in payloads
        if "messages" in payload and payload["messages"][0].get("type") == "ai"
    ]
    assert len(assistant_messages) == 1
    assert assistant_messages[0]["messages"][0].get("id") == "assistant-tool"
    assert (
        "Inspecting scene before edit."
        in str(assistant_messages[0]["messages"][0].get("content", ""))
    )


def test_chat_stream_skips_tool_only_assistant_message_when_no_text_exists(monkeypatch):
    monkeypatch.setattr(api_module, "get_agent", fake_get_tool_call_only_assistant_agent)
    client = TestClient(api_module.app)

    with client.stream("POST", "/chat/stream", json={"message": "hi", "thread_id": "t-tool-only"}) as response:
        assert response.status_code == 200
        payloads = collect_sse_payloads(response.iter_lines())

    assistant_messages = [
        payload
        for payload in payloads
        if "messages" in payload and payload["messages"][0].get("type") == "ai"
    ]
    assert assistant_messages == []


def test_chat_stream_emits_qwen_reasoning_chunks_without_fake_tool_summary(monkeypatch):
    monkeypatch.setattr(api_module, "get_agent", fake_get_qwen_reasoning_stream_agent)
    client = TestClient(api_module.app)

    with client.stream("POST", "/chat/stream", json={"message": "hi", "thread_id": "t-qwen-thinking"}) as response:
        assert response.status_code == 200
        payloads = collect_sse_payloads(response.iter_lines())

    thinking_deltas = [payload["thinking_delta"] for payload in payloads if "thinking_delta" in payload]
    assert thinking_deltas == [
        "Need to inspect the scene first. ",
        "Then I can call the scene tool.",
    ]

    deltas = [payload["delta"] for payload in payloads if "delta" in payload]
    assert deltas == []

    assistant_messages = [
        payload
        for payload in payloads
        if "messages" in payload and payload["messages"][0].get("type") == "ai"
    ]
    assert len(assistant_messages) == 1
    assert assistant_messages[0]["messages"][0].get("reasoning_content") == (
        "Need to inspect the scene first. Then I can call the scene tool."
    )
    assert assistant_messages[0]["messages"][0].get("content") == ""


def test_chat_stream_emits_done(monkeypatch):
    monkeypatch.setattr(api_module, "get_agent", fake_get_agent)
    client = TestClient(api_module.app)

    with client.stream("POST", "/chat/stream", json={"message": "hi", "thread_id": "t2"}) as response:
        assert response.status_code == 200
        payloads = collect_sse_payloads(response.iter_lines())

    done_payload = find_payload(payloads, "event")
    assert done_payload is not None
    assert done_payload["event"] == "done"
    assert isinstance(done_payload.get("seq"), int)


def test_chat_stream_emits_error(monkeypatch):
    monkeypatch.setattr(api_module, "get_agent", fake_get_failing_agent)
    client = TestClient(api_module.app)

    with client.stream("POST", "/chat/stream", json={"message": "hi", "thread_id": "t3"}) as response:
        assert response.status_code == 200
        payloads = collect_sse_payloads(response.iter_lines())

    error_payload = find_payload(payloads, "error")
    assert error_payload is not None
    assert "stream failed" in error_payload["error"]
    assert isinstance(error_payload.get("seq"), int)


def test_chat_stream_can_resume_with_last_event_id(monkeypatch):
    monkeypatch.setattr(api_module, "get_agent", fake_get_resumable_agent)
    client = TestClient(api_module.app)

    first_payload: dict[str, object] | None = None
    stream_request_id: str | None = None
    with client.stream(
        "POST",
        "/chat/stream",
        json={"message": "hi", "thread_id": "t-resume"},
    ) as response:
        assert response.status_code == 200
        stream_request_id = response.headers.get("X-Stream-Request-Id")
        assert stream_request_id
        for line in response.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line.replace("data:", "", 1).strip()
            payload = json.loads(data)
            if "delta" in payload:
                first_payload = payload
                break

    assert first_payload is not None
    first_seq = first_payload.get("seq")
    assert isinstance(first_seq, int)
    assert first_payload.get("delta") == "hello"

    with client.stream(
        "POST",
        "/chat/stream",
        json={"message": "hi", "thread_id": "t-resume"},
        headers={
            "X-Stream-Request-Id": stream_request_id,
            "Last-Event-ID": str(first_seq),
        },
    ) as response:
        assert response.status_code == 200
        payloads = collect_sse_payloads(response.iter_lines())

    deltas = [payload["delta"] for payload in payloads if "delta" in payload]
    assert deltas == [" world"]
    done_payload = find_payload(payloads, "event")
    assert done_payload is not None
    assert done_payload["event"] == "done"


def test_chat_stream_resume_does_not_restart_heartbeat_tasks(monkeypatch):
    runtime_starts: list[str] = []
    lease_starts: list[str] = []

    class Settings:
        api_stream_timeout_seconds = 120
        api_plan_stream_timeout_seconds = 1800
        blender_mode = "headless"
        session_idle_timeout_seconds = 600
        session_heartbeat_interval_seconds = 5
        session_lease_ttl_seconds = 20

    class Coordinator:
        def touch_activity(self, *_args, **_kwargs) -> None:
            return None

        def refresh_lease_if_owned(self, *_args, **_kwargs) -> bool:
            return True

    async def fake_claim_or_proxy_request(*, request, thread_id):  # noqa: ARG001
        return (
            OwnerResolution(
                thread_id=thread_id,
                mode="owner",
                owner_worker_id="worker-1",
                owner_url="http://127.0.0.1:8000",
                lease_epoch=3,
                lease_token="lease-token",
                lease_ttl_ms=20_000,
            ),
            None,
        )

    async def fake_runtime_heartbeat(*, session, interval_seconds):
        runtime_starts.append(f"{session.stream_request_id}:{interval_seconds}")
        while not session.is_done() and not session.should_stop():
            await asyncio.sleep(0.01)

    async def fake_lease_heartbeat(*, session, interval_seconds):
        lease_starts.append(f"{session.stream_request_id}:{interval_seconds}")
        while not session.is_done() and not session.should_stop():
            await asyncio.sleep(0.01)

    monkeypatch.setattr(routes_chat, "get_settings", lambda: Settings())
    monkeypatch.setattr(routes_chat, "get_session_coordinator", lambda: Coordinator())
    monkeypatch.setattr(routes_chat, "claim_or_proxy_request", fake_claim_or_proxy_request)
    monkeypatch.setattr(routes_chat, "get_agent", fake_get_resumable_agent)
    monkeypatch.setattr(routes_chat, "_run_stream_runtime_heartbeat", fake_runtime_heartbeat)
    monkeypatch.setattr(routes_chat, "_run_stream_lease_heartbeat", fake_lease_heartbeat)

    client = TestClient(api_module.app)

    first_payload: dict[str, object] | None = None
    stream_request_id: str | None = None
    with client.stream(
        "POST",
        "/chat/stream",
        json={"message": "hi", "thread_id": "t-heartbeat-resume"},
    ) as response:
        assert response.status_code == 200
        stream_request_id = response.headers.get("X-Stream-Request-Id")
        assert stream_request_id
        for line in response.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            payload = json.loads(line.replace("data:", "", 1).strip())
            if "delta" in payload:
                first_payload = payload
                break

    assert first_payload is not None

    with client.stream(
        "POST",
        "/chat/stream",
        json={"message": "hi", "thread_id": "t-heartbeat-resume"},
        headers={
            "X-Stream-Request-Id": stream_request_id,
            "Last-Event-ID": str(first_payload["seq"]),
        },
    ) as response:
        assert response.status_code == 200
        payloads = collect_sse_payloads(response.iter_lines())

    deltas = [payload["delta"] for payload in payloads if "delta" in payload]
    assert deltas == [" world"]
    assert len(runtime_starts) == 1
    assert len(lease_starts) == 1


def test_chat_stream_ownership_lost_emits_terminal_error(monkeypatch):
    class Settings:
        api_stream_timeout_seconds = 120
        api_plan_stream_timeout_seconds = 1800
        blender_mode = "local-client"
        session_idle_timeout_seconds = 600
        session_heartbeat_interval_seconds = 5
        session_lease_ttl_seconds = 20

    class Coordinator:
        def touch_activity(self, *_args, **_kwargs) -> None:
            return None

    async def fake_claim_or_proxy_request(*, request, thread_id):  # noqa: ARG001
        return (
            OwnerResolution(
                thread_id=thread_id,
                mode="owner",
                owner_worker_id="worker-1",
                owner_url="http://127.0.0.1:8000",
                lease_epoch=4,
                lease_token="lease-token",
                lease_ttl_ms=20_000,
            ),
            None,
        )

    async def fake_lease_heartbeat(*, session, interval_seconds):  # noqa: ARG001
        await asyncio.sleep(0)
        if session.request_stop(reason="ownership_lost"):
            session.publish(
                {
                    "error": "Stream ownership was lost during execution. Please retry.",
                    "reason": "ownership_lost",
                }
            )

    monkeypatch.setattr(routes_chat, "get_settings", lambda: Settings())
    monkeypatch.setattr(routes_chat, "get_session_coordinator", lambda: Coordinator())
    monkeypatch.setattr(routes_chat, "claim_or_proxy_request", fake_claim_or_proxy_request)
    monkeypatch.setattr(routes_chat, "get_agent", fake_get_ownership_loss_agent)
    monkeypatch.setattr(routes_chat, "_run_stream_lease_heartbeat", fake_lease_heartbeat)

    client = TestClient(api_module.app)

    with client.stream(
        "POST",
        "/chat/stream",
        json={"message": "hi", "thread_id": "t-ownership-loss"},
    ) as response:
        assert response.status_code == 200
        payloads = collect_sse_payloads(response.iter_lines())

    error_payload = find_payload(payloads, "error")
    done_payload = find_payload(payloads, "event")
    assert error_payload is not None
    assert error_payload["reason"] == "ownership_lost"
    assert "ownership was lost" in error_payload["error"]
    assert done_payload is not None
    assert done_payload["event"] == "done"



def test_chat_stream_sse(api_base_url: str) -> None:
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Set RUN_INTEGRATION=1 to run SSE integration tests.")

    response = requests.post(
        f"{api_base_url}/chat/stream",
        json={"message": "hello", "thread_id": "test-stream"},
        stream=True,
        timeout=30,
    )
    assert response.status_code == 200

    for line in response.iter_lines():
        if not line:
            continue
        decoded = line.decode("utf-8")
        if not decoded.startswith("data:"):
            continue
        data = decoded.replace("data:", "", 1).strip()
        assert data
        payload = json.loads(data)
        assert isinstance(payload, dict)
        break
    else:
        pytest.fail("No SSE data received")
