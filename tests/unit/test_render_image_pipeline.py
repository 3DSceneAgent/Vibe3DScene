"""
Tests for the render image pipeline: verifies that images produced by MCP
camera/render tools are correctly extracted, converted, and delivered to the
VLM backend for multimodal reasoning.

The pipeline under test:
  1. MCP tool (camera_tools) returns a CallToolResult with a markdown image link.
  2. update_memory_node resolves the render URL to a data URL and injects a
     fixed-ID HumanMessage so the VLM can see the image.  The fixed ID ensures
     at most one visual message exists in context at any time.
  3. verify_render_with_references sends the image to the VLM for verification.
"""
from __future__ import annotations

import base64
import io
import json
import os
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image as PILImage
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from scene_agent.agent.nodes import (
    _RENDER_VISION_MESSAGE_ID,
    _extract_render_path,
    _resolve_render_message_to_data_url,
    update_memory_node,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_test_image(width: int = 64, height: int = 64, color: tuple = (255, 0, 0)) -> PILImage.Image:
    return PILImage.new("RGB", (width, height), color=color)


def _make_png_bytes(width: int = 64, height: int = 64, color: tuple = (255, 0, 0)) -> bytes:
    img = _make_test_image(width, height, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_jpeg_bytes(width: int = 64, height: int = 64, color: tuple = (0, 128, 255)) -> bytes:
    img = _make_test_image(width, height, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _save_test_image(path: str, fmt: str = "PNG", color: tuple = (255, 0, 0)) -> str:
    img = _make_test_image(color=color)
    img.save(path, format=fmt)
    return path


def _make_base64_png(width: int = 4, height: int = 4, color: tuple = (255, 0, 0)) -> str:
    return base64.b64encode(_make_png_bytes(width, height, color)).decode("ascii")


# ── Test: extract render path from various ToolMessage formats ───────────


class TestExtractRenderPath:
    def test_from_plain_path_string(self):
        msg = ToolMessage(name="render_from_camera", content="/tmp/render.png", tool_call_id="t1")
        assert _extract_render_path(msg) == "/tmp/render.png"

    def test_from_markdown_string_returns_url(self):
        md = "![Render](https://example.com/render.jpg)"
        msg = ToolMessage(name="render_from_objects", content=md, tool_call_id="t1")
        path = _extract_render_path(msg)
        assert path == "https://example.com/render.jpg"

    def test_from_list_content_with_image_item(self):
        msg = ToolMessage(
            name="camera_observe",
            content=[{"type": "image", "url": "file:///tmp/obs.png"}],
            tool_call_id="t1",
        )
        assert _extract_render_path(msg) == "/tmp/obs.png"

    def test_none_when_no_message(self):
        assert _extract_render_path(None) is None

    def test_none_when_content_empty(self):
        msg = ToolMessage(name="render_from_camera", content="", tool_call_id="t1")
        assert _extract_render_path(msg) is None


# ── Test: _resolve_render_message_to_data_url ────────────────────────────


class TestResolveRenderMessageToDataUrl:
    def test_from_markdown_string_local_file(self, tmp_path):
        img_path = str(tmp_path / "render.png")
        _save_test_image(img_path)
        md = f"![Render]({img_path})"
        msg = ToolMessage(name="render_from_objects", content=md, tool_call_id="t1")
        result = _resolve_render_message_to_data_url(msg)
        assert result is not None
        assert result.startswith("data:image/png;base64,")

    def test_from_http_url_passed_through(self):
        md = "![Render](https://example.com/render.jpg)"
        msg = ToolMessage(name="render_from_objects", content=md, tool_call_id="t1")
        result = _resolve_render_message_to_data_url(msg)
        assert result == "https://example.com/render.jpg"

    def test_from_localhost_renders_url_converted_to_data_url(self):
        from scene_agent.utils.rendering import RENDERS_DIR

        filename = "resolve_localhost.png"
        render_path = RENDERS_DIR / filename
        _save_test_image(str(render_path), fmt="PNG", color=(40, 180, 80))
        md = f"![cam](http://localhost:8000/renders/{filename})"
        msg = ToolMessage(name="render_from_objects", content=md, tool_call_id="t1")
        result = _resolve_render_message_to_data_url(msg)
        assert result is not None
        assert result.startswith("data:image/png;base64,")
        try:
            render_path.unlink()
        except OSError:
            pass

    def test_from_relative_renders_url(self):
        from scene_agent.utils.rendering import RENDERS_DIR

        filename = "resolve_relative.png"
        render_path = RENDERS_DIR / filename
        _save_test_image(str(render_path), fmt="PNG", color=(12, 230, 98))
        md = f"![obs](/renders/{filename})"
        msg = ToolMessage(name="camera_observe", content=md, tool_call_id="t1")
        result = _resolve_render_message_to_data_url(msg)
        assert result is not None
        assert result.startswith("data:image/png;base64,")
        try:
            render_path.unlink()
        except OSError:
            pass

    def test_from_legacy_base64_image_block(self):
        b64 = _make_base64_png()
        msg = ToolMessage(
            name="render_from_objects",
            content=[{"type": "image", "base64": b64, "mime_type": "image/png"}],
            tool_call_id="t1",
        )
        result = _resolve_render_message_to_data_url(msg)
        assert result is not None
        assert result.startswith("data:image/png;base64,")

    def test_returns_none_for_plain_text_tool_message(self):
        msg = ToolMessage(name="get_scene_info", content="scene data...", tool_call_id="t1")
        assert _resolve_render_message_to_data_url(msg) is None


# ── Test: update_memory_node end-to-end with render extraction ───────────


class TestUpdateMemoryNodeRenderExtraction:
    def _make_state(self, messages: list, **extra) -> dict:
        return {
            "messages": messages,
            "last_tool_batch_names": None,
            "tool_round_count": 0,
            **extra,
        }

    def test_extracts_render_from_markdown_tool_message(self, tmp_path):
        render_path = str(tmp_path / "test_render.jpg")
        _save_test_image(render_path, fmt="JPEG", color=(0, 128, 255))
        url = f"file://{render_path}"
        md = f"![Render of dragon]({url})"
        tool_msg = ToolMessage(name="render_from_objects", content=md, tool_call_id="t1")
        state = self._make_state([tool_msg])
        result = update_memory_node(state)
        assert "last_render_path" in result
        assert result["last_render_path"] == render_path

    def test_injects_vlm_image_message_with_fixed_id(self, tmp_path):
        render_path = str(tmp_path / "test_render.png")
        _save_test_image(render_path, fmt="PNG")
        url = f"file://{render_path}"
        md = f"![Observation view]({url})"
        tool_msg = ToolMessage(name="camera_observe", content=md, tool_call_id="t1")
        state = self._make_state([tool_msg])
        result = update_memory_node(state)
        messages = result.get("messages", [])
        assert len(messages) == 1
        msg = messages[0]
        assert isinstance(msg, HumanMessage)
        assert msg.id == _RENDER_VISION_MESSAGE_ID, "Fixed ID ensures at most one visual message in context"
        has_image = any(
            item.get("type") == "image_url"
            for item in msg.content
            if isinstance(item, dict)
        )
        assert has_image, "HumanMessage should contain an image_url block for the VLM"

    def test_http_render_url_passed_through(self):
        md = "![Render of scene](https://example.com/renders/thread1_cam_123.jpg)"
        tool_msg = ToolMessage(name="render_from_camera", content=md, tool_call_id="t1")
        state = self._make_state([tool_msg])
        result = update_memory_node(state)
        assert result.get("last_render_path") == "https://example.com/renders/thread1_cam_123.jpg"
        messages = result.get("messages", [])
        assert len(messages) == 1
        image_content = messages[0].content[1]
        assert image_content["image_url"]["url"] == "https://example.com/renders/thread1_cam_123.jpg"

    def test_global_observe_sets_scene_render_source(self):
        md = "![SceneCamera_NE](https://example.com/renders/scene_ne.jpg)"
        tool_msg = ToolMessage(name="observe_scene_global", content=md, tool_call_id="t1")
        state = self._make_state([tool_msg])

        result = update_memory_node(state)

        assert result.get("last_render_path") == "https://example.com/renders/scene_ne.jpg"
        assert result.get("last_render_source") == "scene_observe"


# ── Test: verify_render_with_references with mocked VLM ─────────────────


class TestVerifyRenderImageDelivery:
    """Verify that rendered images are correctly packaged and sent to the VLM."""

    def test_render_image_reaches_vlm(self, tmp_path, monkeypatch):
        render_path = str(tmp_path / "scene_render.png")
        _save_test_image(render_path, fmt="PNG", color=(0, 255, 0))

        captured_messages: list[Any] = []

        class FakeModel:
            def invoke(self, messages):
                captured_messages.extend(messages)
                return SimpleNamespace(
                    content=json.dumps({"status": "match", "reason": "Scene looks correct"})
                )

        class FakeProvider:
            def get_chat_model(self):
                return FakeModel()

        monkeypatch.setattr(
            "scene_agent.vlm.verification.get_vlm_provider",
            lambda **kwargs: FakeProvider(),
        )

        from scene_agent.vlm.verification import verify_render_with_references

        result = verify_render_with_references(
            render_path=render_path,
            reference_paths=[],
            user_request="Create a green cube",
            provider_name="openai",
            api_key="test-key",
            model="gpt-4o",
        )

        assert result["status"] == "match"
        assert len(captured_messages) == 1
        msg = captured_messages[0]
        assert isinstance(msg, HumanMessage)
        image_items = [
            item for item in msg.content
            if isinstance(item, dict) and item.get("type") == "image_url"
        ]
        assert len(image_items) == 1, "Exactly one image (the render) should reach the VLM"
        data_url = image_items[0]["image_url"]["url"]
        assert data_url.startswith("data:image/png;base64,")

    def test_render_plus_reference_images_reach_vlm(self, tmp_path, monkeypatch):
        render_path = str(tmp_path / "render.png")
        ref1_path = str(tmp_path / "ref1.png")
        ref2_path = str(tmp_path / "ref2.jpg")
        _save_test_image(render_path, fmt="PNG", color=(255, 0, 0))
        _save_test_image(ref1_path, fmt="PNG", color=(0, 255, 0))
        _save_test_image(ref2_path, fmt="JPEG", color=(0, 0, 255))

        captured_messages: list[Any] = []

        class FakeModel:
            def invoke(self, messages):
                captured_messages.extend(messages)
                return SimpleNamespace(
                    content=json.dumps({"status": "mismatch", "reason": "Colors differ"})
                )

        class FakeProvider:
            def get_chat_model(self):
                return FakeModel()

        monkeypatch.setattr(
            "scene_agent.vlm.verification.get_vlm_provider",
            lambda **kwargs: FakeProvider(),
        )

        from scene_agent.vlm.verification import verify_render_with_references

        result = verify_render_with_references(
            render_path=render_path,
            reference_paths=[ref1_path, ref2_path],
            user_request="Create a scene matching the reference",
            provider_name="openai",
            api_key="test-key",
            model="gpt-4o",
        )

        assert result["status"] == "mismatch"
        msg = captured_messages[0]
        image_items = [
            item for item in msg.content
            if isinstance(item, dict) and item.get("type") == "image_url"
        ]
        assert len(image_items) == 3, "1 render + 2 reference images should reach the VLM"

    def test_user_request_text_reaches_vlm(self, tmp_path, monkeypatch):
        render_path = str(tmp_path / "render.png")
        _save_test_image(render_path)

        captured_messages: list[Any] = []

        class FakeModel:
            def invoke(self, messages):
                captured_messages.extend(messages)
                return SimpleNamespace(
                    content=json.dumps({"status": "match", "reason": "ok"})
                )

        class FakeProvider:
            def get_chat_model(self):
                return FakeModel()

        monkeypatch.setattr(
            "scene_agent.vlm.verification.get_vlm_provider",
            lambda **kwargs: FakeProvider(),
        )

        from scene_agent.vlm.verification import verify_render_with_references

        verify_render_with_references(
            render_path=render_path,
            reference_paths=[],
            user_request="A dungeon with a dragon guarding gold",
            provider_name="openai",
            api_key="test-key",
            model="gpt-4o",
        )

        msg = captured_messages[0]
        text_items = [
            item["text"] for item in msg.content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        full_text = " ".join(text_items)
        assert "dungeon" in full_text.lower()
        assert "dragon" in full_text.lower()

    def test_verify_accepts_relative_renders_path(self, monkeypatch):
        from scene_agent.utils.rendering import RENDERS_DIR

        filename = "verify_relative_path_render.png"
        render_path = RENDERS_DIR / filename
        _save_test_image(str(render_path), fmt="PNG", color=(200, 60, 60))

        captured_messages: list[Any] = []

        class FakeModel:
            def invoke(self, messages):
                captured_messages.extend(messages)
                return SimpleNamespace(
                    content=json.dumps({"status": "match", "reason": "looks red"})
                )

        class FakeProvider:
            def get_chat_model(self):
                return FakeModel()

        monkeypatch.setattr(
            "scene_agent.vlm.verification.get_vlm_provider",
            lambda **kwargs: FakeProvider(),
        )

        from scene_agent.vlm.verification import verify_render_with_references

        result = verify_render_with_references(
            render_path=f"/renders/{filename}",
            reference_paths=[],
            user_request="Create a red square",
            provider_name="openai",
            api_key="test-key",
            model="gpt-4o",
        )

        assert result["status"] == "match"
        assert len(captured_messages) == 1
        image_items = [
            item
            for item in captured_messages[0].content
            if isinstance(item, dict) and item.get("type") == "image_url"
        ]
        assert image_items
        assert str(image_items[0]["image_url"]["url"]).startswith("data:image/")
        try:
            render_path.unlink()
        except OSError:
            pass

    def test_verify_accepts_localhost_renders_http_url(self, monkeypatch):
        from scene_agent.utils.rendering import RENDERS_DIR

        filename = "verify_localhost_renders_url.png"
        render_path = RENDERS_DIR / filename
        _save_test_image(str(render_path), fmt="PNG", color=(20, 20, 220))

        captured_messages: list[Any] = []

        class FakeModel:
            def invoke(self, messages):
                captured_messages.extend(messages)
                return SimpleNamespace(
                    content=json.dumps({"status": "match", "reason": "looks blue"})
                )

        class FakeProvider:
            def get_chat_model(self):
                return FakeModel()

        monkeypatch.setattr(
            "scene_agent.vlm.verification.get_vlm_provider",
            lambda **kwargs: FakeProvider(),
        )

        from scene_agent.vlm.verification import verify_render_with_references

        result = verify_render_with_references(
            render_path=f"http://localhost:8000/renders/{filename}",
            reference_paths=[],
            user_request="Create a blue square",
            provider_name="openai",
            api_key="test-key",
            model="gpt-4o",
        )

        assert result["status"] == "match"
        assert len(captured_messages) == 1
        image_items = [
            item
            for item in captured_messages[0].content
            if isinstance(item, dict) and item.get("type") == "image_url"
        ]
        assert image_items
        assert str(image_items[0]["image_url"]["url"]).startswith("data:image/")
        try:
            render_path.unlink()
        except OSError:
            pass

    @pytest.mark.parametrize("render_source", ["scene_observe", "agent_camera"])
    def test_todo_context_is_primary_verification_target(self, tmp_path, monkeypatch, render_source):
        render_path = str(tmp_path / f"todo_target_{render_source}.png")
        _save_test_image(render_path, fmt="PNG", color=(120, 120, 120))

        captured_messages: list[Any] = []

        class FakeModel:
            def invoke(self, messages):
                captured_messages.extend(messages)
                return SimpleNamespace(
                    content=json.dumps({"status": "partial", "reason": "todo target not fully matched"})
                )

        class FakeProvider:
            def get_chat_model(self):
                return FakeModel()

        monkeypatch.setattr(
            "scene_agent.vlm.verification.get_vlm_provider",
            lambda **kwargs: FakeProvider(),
        )

        from scene_agent.vlm.verification import verify_render_with_references

        verify_render_with_references(
            render_path=render_path,
            reference_paths=[],
            user_request="Create a complete cozy living room scene with bookshelves and wall art.",
            todo_context=["Move the sofa to align with the carpet center line."],
            render_source=render_source,
            provider_name="openai",
            api_key="test-key",
            model="gpt-4o",
        )

        msg = captured_messages[0]
        text_items = [
            item["text"] for item in msg.content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        full_text = " ".join(text_items)
        assert "current todo objectives (primary verification target)" in full_text.lower()
        assert "move the sofa to align with the carpet center line." in full_text.lower()
        assert "background context only" in full_text.lower()
        assert "todo_assessment" in full_text
