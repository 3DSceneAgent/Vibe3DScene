import threading
import asyncio

from fastapi.testclient import TestClient

from scene_agent.interfaces import api as api_module
from .streaming_helpers import collect_sse_payloads, find_payload


class SlowAgent:
    async def astream(self, *_args, **_kwargs):
        yield ("messages", [{"type": "ai", "content": "hello"}])
        await asyncio.sleep(0.1)
        yield ("messages", [{"type": "ai", "content": " world"}])


async def fake_get_agent(_thread_id=None):
    return SlowAgent()


def _run_stream(results: dict[int, list[dict]], index: int) -> None:
    client = TestClient(api_module.app)
    with client.stream(
        "POST",
        "/chat/stream",
        json={"message": "hi", "thread_id": f"t{index}"},
    ) as response:
        payloads = collect_sse_payloads(response.iter_lines(), limit=4)
        results[index] = payloads


def test_concurrent_stream_requests(monkeypatch):
    monkeypatch.setattr(api_module, "get_agent", fake_get_agent)
    results: dict[int, list[dict]] = {}

    threads = [
        threading.Thread(target=_run_stream, args=(results, 1)),
        threading.Thread(target=_run_stream, args=(results, 2)),
    ]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(timeout=5)

    assert len(results) == 2
    assert all(not thread.is_alive() for thread in threads)
    for payloads in results.values():
        assert find_payload(payloads, "delta") is not None

