import os
import json

import pytest
import requests
from fastapi.testclient import TestClient
from scene_agent.interfaces import api as api_module
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
                },
            },
        )


async def fake_get_updates_agent(_thread_id=None):
    return UpdatesAgent()


def test_chat_stream_sse(monkeypatch):
    monkeypatch.setattr(api_module, "get_agent", fake_get_agent)
    client = TestClient(api_module.app)

    with client.stream("POST", "/chat/stream", json={"message": "hi", "thread_id": "t1"}) as response:
        assert response.status_code == 200
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

    tool_payloads = [
        payload for payload in payloads if "messages" in payload and payload["messages"][0].get("type") == "tool"
    ]
    assert tool_payloads
    assert tool_payloads[0]["messages"][0].get("name") == "verification"


def test_chat_stream_emits_done(monkeypatch):
    monkeypatch.setattr(api_module, "get_agent", fake_get_agent)
    client = TestClient(api_module.app)

    with client.stream("POST", "/chat/stream", json={"message": "hi", "thread_id": "t2"}) as response:
        assert response.status_code == 200
        payloads = collect_sse_payloads(response.iter_lines())

    done_payload = find_payload(payloads, "event")
    assert done_payload is not None
    assert done_payload["event"] == "done"


def test_chat_stream_emits_error(monkeypatch):
    monkeypatch.setattr(api_module, "get_agent", fake_get_failing_agent)
    client = TestClient(api_module.app)

    with client.stream("POST", "/chat/stream", json={"message": "hi", "thread_id": "t3"}) as response:
        assert response.status_code == 200
        payloads = collect_sse_payloads(response.iter_lines())

    error_payload = find_payload(payloads, "error")
    assert error_payload is not None
    assert "stream failed" in error_payload["error"]



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
