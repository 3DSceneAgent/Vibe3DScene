import asyncio
from types import SimpleNamespace

from langchain_core.messages import ToolMessage

from scene_agent.agent.graph import (
    _awrap_tool_call_with_retry,
    _normalize_tool_call_args,
    _normalize_tool_request,
)


class _FakeRequest:
    def __init__(self, tool_call: dict, state: dict | None = None):
        self.tool_call = tool_call
        self.state = state or {}

    def override(self, **overrides):
        return _FakeRequest(
            overrides.get("tool_call", self.tool_call),
            overrides.get("state", self.state),
        )


def test_normalize_tool_call_args_coerces_delete_objects_names():
    tool_call = {
        "name": "delete_objects",
        "id": "tool_call_1",
        "args": {"object_names": "Lamp, Camera"},
    }

    normalized = _normalize_tool_call_args(tool_call)

    assert normalized["args"]["object_names"] == ["Lamp", "Camera"]


def test_awrap_tool_call_retries_retryable_error_once():
    request = _FakeRequest(
        {
            "name": "get_scene_info",
            "id": "tool_call_2",
            "args": {},
        }
    )
    attempts = {"count": 0}

    async def _execute(req):
        _ = req
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("Connection reset by peer")
        return ToolMessage(
            content='{"success": true}',
            name="get_scene_info",
            tool_call_id="tool_call_2",
        )

    result = asyncio.run(_awrap_tool_call_with_retry(request, _execute))

    assert isinstance(result, ToolMessage)
    assert result.status == "success"
    assert attempts["count"] == 2


def test_awrap_tool_call_returns_tool_error_message_after_retry_budget():
    request = _FakeRequest(
        {
            "name": "delete_objects",
            "id": "tool_call_3",
            "args": {"object_names": "Lamp"},
        }
    )
    attempts = {"count": 0}

    async def _execute(_req):
        attempts["count"] += 1
        raise RuntimeError("Validation error: object_names must be list")

    result = asyncio.run(_awrap_tool_call_with_retry(request, _execute))

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert attempts["count"] == 2
    assert "Error executing tool delete_objects after 2 attempts" in str(result.content)


def test_normalize_tool_request_injects_current_attached_image_path(monkeypatch):
    monkeypatch.setattr(
        "scene_agent.agent.graph.get_image_asset_memory",
        lambda: SimpleNamespace(
            get_assets_by_ids=lambda thread_id, asset_ids: [
                SimpleNamespace(stored_path="/tmp/current-request-image.png")
            ]
            if thread_id == "thread-1" and asset_ids == ["asset-1"]
            else []
        ),
    )
    monkeypatch.setattr("scene_agent.agent.graph.os.path.isfile", lambda path: path == "/tmp/current-request-image.png")

    request = _FakeRequest(
        {
            "name": "reconstruct_full_scene",
            "id": "tool_call_4",
            "args": {"input_image_path": "/tmp/ignored-by-model.png"},
        },
        state={"thread_id": "thread-1", "attached_image_ids": ["asset-1"]},
    )

    normalized = _normalize_tool_request(request)

    assert not isinstance(normalized, ToolMessage)
    assert normalized.tool_call["args"]["input_image_path"] == "/tmp/current-request-image.png"


def test_awrap_tool_call_blocks_sam_reconstruct_without_exactly_one_attached_image():
    request = _FakeRequest(
        {
            "name": "reconstruct_full_scene",
            "id": "tool_call_5",
            "args": {},
        },
        state={"thread_id": "thread-1", "attached_image_ids": []},
    )
    attempts = {"count": 0}

    async def _execute(_req):
        attempts["count"] += 1
        return ToolMessage(content="unexpected", name="reconstruct_full_scene", tool_call_id="tool_call_5")

    result = asyncio.run(_awrap_tool_call_with_retry(request, _execute))

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert attempts["count"] == 0
    assert "requires exactly one image attached to the current request" in str(result.content)
