from __future__ import annotations

import ast
from types import SimpleNamespace

from langchain_core.messages import HumanMessage, ToolMessage

from scene_agent.agent.nodes import verify_node


class _FakeReferenceMemory:
    def list_images(self, _thread_id: str) -> list:
        return []


def test_verify_node_emits_tool_message_with_internal_tool_call_id(monkeypatch):
    captured_kwargs: dict = {}

    def fake_verify_render_with_references(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "status": "mismatch",
            "reason": "render is blank",
        }

    monkeypatch.setattr(
        "scene_agent.agent.nodes.get_reference_image_memory",
        lambda: _FakeReferenceMemory(),
    )
    monkeypatch.setattr(
        "scene_agent.agent.nodes.get_settings",
        lambda: SimpleNamespace(reference_image_max_count=5),
    )
    monkeypatch.setattr(
        "scene_agent.agent.nodes.verify_render_with_references",
        fake_verify_render_with_references,
    )

    state = {
        "thread_id": "thread-verify",
        "messages": [HumanMessage(content="Create a dragon guarding treasure.")],
        "last_render_path": "/tmp/render.png",
        "last_verified_path": None,
        "last_render_source": "agent_camera",
    }

    result = verify_node(
        state,
        provider_name="openai",
        api_key="test-key",
        model="gpt-4o",
    )

    assert result["last_verified_path"] == "/tmp/render.png"
    assert len(result["messages"]) == 1

    message = result["messages"][0]
    assert isinstance(message, ToolMessage)
    assert message.name == "verification"
    assert isinstance(message.tool_call_id, str) and message.tool_call_id.startswith("verification_")

    parsed_content = message.content
    if isinstance(parsed_content, str):
        parsed_content = ast.literal_eval(parsed_content)

    assert isinstance(parsed_content, dict)
    assert parsed_content["status"] == "mismatch"
    assert parsed_content["reason"] == "render is blank"
    assert parsed_content["render_path"] == "/tmp/render.png"
    assert parsed_content["reference_count"] == 0

    assert captured_kwargs["render_path"] == "/tmp/render.png"
    assert captured_kwargs["reference_paths"] == []
    assert captured_kwargs["render_source"] == "agent_camera"


def test_verify_node_skips_when_render_already_verified(monkeypatch):
    def fail_verify_render_with_references(**_kwargs):
        raise AssertionError("verify_render_with_references should not be called")

    monkeypatch.setattr(
        "scene_agent.agent.nodes.verify_render_with_references",
        fail_verify_render_with_references,
    )

    state = {
        "messages": [HumanMessage(content="Reuse current render.")],
        "last_render_path": "/tmp/render.png",
        "last_verified_path": "/tmp/render.png",
    }

    result = verify_node(state)
    assert result == {}
