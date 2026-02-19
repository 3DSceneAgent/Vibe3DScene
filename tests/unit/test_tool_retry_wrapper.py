import asyncio

from langchain_core.messages import ToolMessage

from scene_agent.agent.graph import _awrap_tool_call_with_retry, _normalize_tool_call_args


class _FakeRequest:
    def __init__(self, tool_call: dict):
        self.tool_call = tool_call

    def override(self, **overrides):
        return _FakeRequest(overrides.get("tool_call", self.tool_call))


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
