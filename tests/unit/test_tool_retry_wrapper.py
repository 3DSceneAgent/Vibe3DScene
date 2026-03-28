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


def test_normalize_tool_request_resolves_selected_reference_image_for_reconstruct(monkeypatch):
    monkeypatch.setattr("scene_agent.agent.graph.os.path.isfile", lambda path: path == "/tmp/chair-ref.png")

    request = _FakeRequest(
        {
            "name": "reconstruct_full_scene",
            "id": "tool_call_4b",
            "args": {"input_image_name": "chair_ref"},
        },
        state={
            "thread_id": "thread-1",
            "reference_image_catalog": {
                "chair_ref": {
                    "asset_id": "asset-chair",
                    "stored_path": "/tmp/chair-ref.png",
                }
            },
            "request_reference_image_keys": ["chair_ref"],
        },
    )

    normalized = _normalize_tool_request(request)

    assert not isinstance(normalized, ToolMessage)
    assert normalized.tool_call["args"]["input_image_path"] == "/tmp/chair-ref.png"
    assert "input_image_name" not in normalized.tool_call["args"]
    assert "input_image_id" not in normalized.tool_call["args"]


def test_normalize_tool_request_falls_back_to_attached_image_for_fake_hunyuan_id(monkeypatch):
    monkeypatch.setattr(
        "scene_agent.agent.graph.get_image_asset_memory",
        lambda: SimpleNamespace(
            get_assets_by_ids=lambda thread_id, asset_ids: [
                SimpleNamespace(id="asset-1", stored_path="/tmp/current-request-image.png")
            ]
            if thread_id == "thread-1" and asset_ids == ["asset-1"]
            else [],
            list_assets=lambda _thread_id: [],
        ),
    )
    monkeypatch.setattr("scene_agent.agent.graph.os.path.isfile", lambda path: path == "/tmp/current-request-image.png")

    request = _FakeRequest(
        {
            "name": "generate_hunyuan3d_model",
            "id": "tool_call_4c",
            "args": {"input_image_id": "fake-image-id"},
        },
        state={"thread_id": "thread-1", "attached_image_ids": ["asset-1"]},
    )

    normalized = _normalize_tool_request(request)

    assert not isinstance(normalized, ToolMessage)
    assert normalized.tool_call["args"]["input_image_url"] == "/tmp/current-request-image.png"
    assert "input_image_id" not in normalized.tool_call["args"]
    assert "input_image_name" not in normalized.tool_call["args"]


def test_normalize_tool_request_preserves_explicit_hunyuan_image_url(monkeypatch):
    monkeypatch.setattr(
        "scene_agent.agent.graph.get_image_asset_memory",
        lambda: SimpleNamespace(
            get_assets_by_ids=lambda thread_id, asset_ids: [
                SimpleNamespace(id="asset-1", stored_path="/tmp/current-request-image.png")
            ]
            if thread_id == "thread-1" and asset_ids == ["asset-1"]
            else [],
            list_assets=lambda _thread_id: [],
        ),
    )
    monkeypatch.setattr("scene_agent.agent.graph.os.path.isfile", lambda path: path == "/tmp/current-request-image.png")

    request = _FakeRequest(
        {
            "name": "generate_hunyuan3d_model",
            "id": "tool_call_4c2",
            "args": {"input_image_url": "https://example.com/user-specified.png"},
        },
        state={"thread_id": "thread-1", "attached_image_ids": ["asset-1"]},
    )

    normalized = _normalize_tool_request(request)

    assert not isinstance(normalized, ToolMessage)
    assert normalized.tool_call["args"]["input_image_url"] == "https://example.com/user-specified.png"


def test_normalize_tool_request_resolves_selected_reference_image_for_tripo(monkeypatch):
    monkeypatch.setattr("scene_agent.agent.graph.os.path.isfile", lambda path: path == "/tmp/stool-ref.png")

    request = _FakeRequest(
        {
            "name": "generate_tripo3d_model",
            "id": "tool_call_4c3",
            "args": {"input_image_name": "stool_ref"},
        },
        state={
            "thread_id": "thread-1",
            "reference_image_catalog": {
                "stool_ref": {
                    "asset_id": "asset-stool",
                    "stored_path": "/tmp/stool-ref.png",
                }
            },
            "request_reference_image_keys": ["stool_ref"],
        },
    )

    normalized = _normalize_tool_request(request)

    assert not isinstance(normalized, ToolMessage)
    assert normalized.tool_call["args"]["input_image_url"] == "/tmp/stool-ref.png"
    assert "input_image_name" not in normalized.tool_call["args"]
    assert "input_image_id" not in normalized.tool_call["args"]


def test_normalize_tool_request_resolves_selected_reference_image_for_hyper3d(monkeypatch):
    monkeypatch.setattr("scene_agent.agent.graph.os.path.isfile", lambda path: path == "/tmp/lamp-ref.png")

    request = _FakeRequest(
        {
            "name": "generate_hyper3d_model_via_images",
            "id": "tool_call_4d",
            "args": {"input_image_name": "lamp_ref"},
        },
        state={
            "thread_id": "thread-1",
            "reference_image_catalog": {
                "lamp_ref": {
                    "asset_id": "asset-lamp",
                    "stored_path": "/tmp/lamp-ref.png",
                }
            },
            "request_reference_image_keys": ["lamp_ref"],
        },
    )

    normalized = _normalize_tool_request(request)

    assert not isinstance(normalized, ToolMessage)
    assert normalized.tool_call["args"]["input_image_paths"] == ["/tmp/lamp-ref.png"]
    assert "input_image_name" not in normalized.tool_call["args"]
    assert "input_image_id" not in normalized.tool_call["args"]
    assert "input_image_urls" not in normalized.tool_call["args"]


def test_normalize_tool_request_preserves_explicit_hyper3d_image_paths(monkeypatch):
    monkeypatch.setattr(
        "scene_agent.agent.graph.get_image_asset_memory",
        lambda: SimpleNamespace(
            get_assets_by_ids=lambda thread_id, asset_ids: [
                SimpleNamespace(id="asset-1", stored_path="/tmp/current-request-image.png")
            ]
            if thread_id == "thread-1" and asset_ids == ["asset-1"]
            else [],
            list_assets=lambda _thread_id: [],
        ),
    )
    monkeypatch.setattr("scene_agent.agent.graph.os.path.isfile", lambda path: path == "/tmp/current-request-image.png")

    request = _FakeRequest(
        {
            "name": "generate_hyper3d_model_via_images",
            "id": "tool_call_4e",
            "args": {"input_image_paths": ["/tmp/keep-this-path.png"]},
        },
        state={"thread_id": "thread-1", "attached_image_ids": ["asset-1"]},
    )

    normalized = _normalize_tool_request(request)

    assert not isinstance(normalized, ToolMessage)
    assert normalized.tool_call["args"]["input_image_paths"] == ["/tmp/keep-this-path.png"]


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
    assert "requires exactly one attached image or one request-selected reference image" in str(result.content)
