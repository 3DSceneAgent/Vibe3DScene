import os
import json

import pytest
import requests
from fastapi.testclient import TestClient
from scene_agent.interfaces import api as api_module


class StubAgent:
    async def astream(self, *_args, **_kwargs):
        yield ("messages", [{"type": "ai", "content": "hello"}])
        yield ("messages", [{"type": "ai", "content": " world"}])


async def fake_get_agent():
    return StubAgent()


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
            if len(payloads) >= 2:
                break

    assert payloads[0]["delta"] == "hello"
    assert payloads[1]["delta"] == " world"



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
