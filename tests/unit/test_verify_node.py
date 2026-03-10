from __future__ import annotations

import ast

from langchain_core.messages import HumanMessage, ToolMessage

from scene_agent.agent.nodes import verify_node


def test_verify_node_emits_tool_message_and_verification_result(monkeypatch):
    captured_kwargs: dict = {}

    def fake_verify_render_with_references(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "status": "working",
            "reason": "Object still missing.",
            "edit_suggestions": ["Add the missing object."],
        }

    monkeypatch.setattr(
        "scene_agent.agent.nodes.verification.verify_render_with_references",
        fake_verify_render_with_references,
    )

    result = verify_node(
        {
            "thread_id": "thread-verify",
            "messages": [HumanMessage(content="Create a dragon guarding treasure.")],
            "last_render_path": "/tmp/render.png",
            "last_verified_path": None,
            "last_render_source": "agent_camera",
        }
    )

    assert result["last_verified_path"] == "/tmp/render.png"
    assert result["verification_result"]["status"] == "working"
    assert captured_kwargs["render_path"] == "/tmp/render.png"
    assert captured_kwargs["reference_paths"] == []

    assert len(result["messages"]) == 1
    message = result["messages"][0]
    assert isinstance(message, ToolMessage)
    assert message.name == "verification"


def test_verify_node_uses_request_scoped_reference_images_only(monkeypatch):
    captured_kwargs: dict = {}

    def fake_verify_render_with_references(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "status": "done",
            "reason": "Looks good.",
            "edit_suggestions": [],
        }

    monkeypatch.setattr(
        "scene_agent.agent.nodes.verification.verify_render_with_references",
        fake_verify_render_with_references,
    )

    state = {
        "thread_id": "thread-verify-request-images",
        "messages": [HumanMessage(content="Match the uploaded chair reference.")],
        "last_render_path": "/tmp/request_scoped_render.png",
        "last_verified_path": None,
        "reference_image_catalog": {
            "chair_ref": {
                "asset_id": "asset-chair",
                "stored_path": "/tmp/chair.png",
                "caption": "wooden chair",
                "source_turn_at": "2026-01-01T00:00:00",
                "created_at": "2026-01-01T00:00:00",
                "last_used_at": None,
                "use_count": 0,
            },
            "lamp_ref": {
                "asset_id": "asset-lamp",
                "stored_path": "/tmp/lamp.png",
                "caption": "floor lamp",
                "source_turn_at": "2026-01-01T00:00:00",
                "created_at": "2026-01-01T00:00:00",
                "last_used_at": None,
                "use_count": 0,
            },
        },
        "request_reference_image_keys": ["chair_ref"],
    }

    result = verify_node(state)
    assert captured_kwargs["reference_paths"] == ["/tmp/chair.png"]
    message = result["messages"][0]
    payload = message.content if isinstance(message.content, dict) else ast.literal_eval(message.content)
    assert payload["reference_count"] == 1
    assert payload["reference_ids"] == ["asset-chair"]


def test_verify_node_skips_when_render_already_verified(monkeypatch):
    def fail_verify_render_with_references(**_kwargs):
        raise AssertionError("verify_render_with_references should not be called")

    monkeypatch.setattr(
        "scene_agent.agent.nodes.verification.verify_render_with_references",
        fail_verify_render_with_references,
    )

    result = verify_node(
        {
            "messages": [HumanMessage(content="Reuse current render.")],
            "last_render_path": "/tmp/render.png",
            "last_verified_path": "/tmp/render.png",
        }
    )
    assert result["verification_result"] is None


def test_verify_node_uses_vlm_result_even_with_large_scene_bbox(monkeypatch):
    monkeypatch.setattr(
        "scene_agent.agent.nodes.verification.verify_render_with_references",
        lambda **_kwargs: {
            "status": "done",
            "reason": "Structured verification result is used directly.",
            "edit_suggestions": [],
        },
    )

    result = verify_node(
        {
            "thread_id": "thread-large-scene",
            "messages": [HumanMessage(content="Build a dungeon scene.")],
            "last_render_path": "/tmp/large_scene.png",
            "last_verified_path": None,
            "scene_bbox": {"center": [0, 0, 0], "dimensions": [12000.0, 8000.0, 6000.0]},
        }
    )

    assert result["verification_result"]["status"] == "done"
    assert result["verification_result"]["reason"] == "Structured verification result is used directly."
