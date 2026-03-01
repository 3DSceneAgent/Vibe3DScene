from __future__ import annotations

import ast
from datetime import datetime

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from scene_agent.agent.nodes import verify_node


def test_verify_node_emits_tool_message_with_internal_tool_call_id(monkeypatch):
    captured_kwargs: dict = {}

    def fake_verify_render_with_references(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "status": "mismatch",
            "reason": "render is blank",
        }

    monkeypatch.setattr(
        "scene_agent.agent.nodes.verification.verify_render_with_references",
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
    assert captured_kwargs["todo_context"] == []


def test_verify_node_passes_todo_context_even_for_scene_observe(monkeypatch):
    captured_kwargs: dict = {}

    def fake_verify_render_with_references(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "status": "partial",
            "reason": "needs more alignment",
        }

    monkeypatch.setattr(
        "scene_agent.agent.nodes.verification.verify_render_with_references",
        fake_verify_render_with_references,
    )

    now = datetime.now().isoformat()
    state = {
        "thread_id": "thread-verify-scene-observe",
        "messages": [HumanMessage(content="Build a modern reading corner.")],
        "last_render_path": "/tmp/scene_observe_render.png",
        "last_verified_path": None,
        "last_render_source": "scene_observe",
        "todos": [
            {
                "id": "todo-1",
                "description": "Move the armchair closer to the floor lamp.",
                "status": "in_progress",
                "created_at": now,
                "completed_at": None,
            }
        ],
    }

    verify_node(
        state,
        provider_name="openai",
        api_key="test-key",
        model="gpt-4o",
    )

    assert captured_kwargs["render_source"] == "scene_observe"
    assert captured_kwargs["todo_context"] == ["Move the armchair closer to the floor lamp."]


def test_verify_node_passes_scene_context_to_verifier(monkeypatch):
    captured_kwargs: dict = {}

    def fake_verify_render_with_references(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "status": "match",
            "reason": "scene context received",
        }

    monkeypatch.setattr(
        "scene_agent.agent.nodes.verification.verify_render_with_references",
        fake_verify_render_with_references,
    )

    state = {
        "thread_id": "thread-verify-scene-context",
        "messages": [HumanMessage(content="Build a cozy corner with chair and lamp.")],
        "last_render_path": "/tmp/scene_context_render.png",
        "last_verified_path": None,
        "scene_objects": {
            "Chair_A": {
                "type": "MESH",
                "location": [1.0, 0.0, 0.0],
                "dimensions": [0.8, 0.8, 1.2],
                "bounding_box": [[0.6, -0.4, 0.0], [1.4, 0.4, 1.2]],
                "visible": True,
                "material_count": 2,
            }
        },
        "scene_bbox": {"center": [0.0, 0.0, 0.6], "dimensions": [4.0, 4.0, 2.4]},
        "persistent_cameras": ["SceneCamera_NE"],
        "scene_camera_params": {"SceneCamera_NE": {"azimuth": 45, "elevation": 25}},
    }

    verify_node(
        state,
        provider_name="openai",
        api_key="test-key",
        model="gpt-4o",
    )

    scene_context = captured_kwargs.get("scene_context")
    assert isinstance(scene_context, dict)
    assert scene_context["scene_object_count"] == 1
    assert "Chair_A" in scene_context["scene_objects"]
    assert scene_context["scene_bbox"]["dimensions"] == [4.0, 4.0, 2.4]
    assert scene_context["persistent_cameras"] == ["SceneCamera_NE"]


def test_verify_node_auto_completes_todo_from_verification_assessment(monkeypatch):
    def fake_verify_render_with_references(**_kwargs):
        return {
            "status": "mismatch",
            "reason": "Dragon exists, but other objectives remain.",
            "todo_assessment": [
                {
                    "objective": "Import the low-poly dragon model",
                    "status": "done",
                    "reason": "Dragon is clearly present in the scene.",
                },
                {
                    "objective": "Add a pot of gold",
                    "status": "not_done",
                    "reason": "Pot of gold is missing.",
                },
            ],
        }

    monkeypatch.setattr(
        "scene_agent.agent.nodes.verification.verify_render_with_references",
        fake_verify_render_with_references,
    )

    now = datetime.now().isoformat()
    state = {
        "thread_id": "thread-verify-auto-todo",
        "messages": [HumanMessage(content="Create a low-poly dungeon scene with dragon and gold.")],
        "last_render_path": "/tmp/auto_todo_render.png",
        "last_verified_path": None,
        "last_render_source": "scene_observe",
        "todos": [
            {
                "id": "todo-1",
                "description": "Import the low-poly dragon model",
                "status": "in_progress",
                "created_at": now,
                "completed_at": None,
            },
            {
                "id": "todo-2",
                "description": "Add a pot of gold",
                "status": "pending",
                "created_at": now,
                "completed_at": None,
            },
        ],
    }

    result = verify_node(state)

    assert result["last_verified_path"] == "/tmp/auto_todo_render.png"
    updates = result.get("todos") or []
    assert len(updates) == 1
    assert updates[0]["id"] == "todo-1"
    assert updates[0]["status"] == "completed"
    assert isinstance(updates[0]["completed_at"], str) and updates[0]["completed_at"]

    message = result["messages"][0]
    payload = message.content if isinstance(message.content, dict) else ast.literal_eval(message.content)
    assert "todo_status_updates" in payload
    assert payload["todo_status_updates"][0]["matched_todo"] == "Import the low-poly dragon model"


def test_verify_node_uses_request_scoped_reference_images_only(monkeypatch):
    captured_kwargs: dict = {}

    def fake_verify_render_with_references(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "status": "match",
            "reason": "request-scoped references received",
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


def test_verify_node_falls_back_to_prompt_when_no_request_reference_images(monkeypatch):
    captured_kwargs: dict = {}

    def fake_verify_render_with_references(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "status": "match",
            "reason": "text-only verification received",
        }

    monkeypatch.setattr(
        "scene_agent.agent.nodes.verification.verify_render_with_references",
        fake_verify_render_with_references,
    )

    state = {
        "thread_id": "thread-verify-text-only",
        "messages": [HumanMessage(content="Verify this render against the text prompt only.")],
        "last_render_path": "/tmp/text_only_render.png",
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
            }
        },
        "request_reference_image_keys": [],
    }

    result = verify_node(state)

    assert captured_kwargs["reference_paths"] == []
    assert captured_kwargs["user_request"] == "Verify this render against the text prompt only."
    message = result["messages"][0]
    payload = message.content if isinstance(message.content, dict) else ast.literal_eval(message.content)
    assert payload["reference_count"] == 0
    assert payload["reference_ids"] == []


def test_verify_node_skips_when_render_already_verified(monkeypatch):
    def fail_verify_render_with_references(**_kwargs):
        raise AssertionError("verify_render_with_references should not be called")

    monkeypatch.setattr(
        "scene_agent.agent.nodes.verification.verify_render_with_references",
        fail_verify_render_with_references,
    )

    state = {
        "messages": [HumanMessage(content="Reuse current render.")],
        "last_render_path": "/tmp/render.png",
        "last_verified_path": "/tmp/render.png",
    }

    result = verify_node(state)
    assert result == {"verify_forced_recovery": False}


def test_verify_node_reports_catastrophic_without_forced_recovery(monkeypatch):
    def fail_verify_render_with_references(**_kwargs):
        raise AssertionError("verify_render_with_references should not be called for catastrophic precheck")

    monkeypatch.setattr(
        "scene_agent.agent.nodes.verification.verify_render_with_references",
        fail_verify_render_with_references,
    )

    state = {
        "thread_id": "thread-catastrophic-undo",
        "messages": [HumanMessage(content="Build a dungeon scene.")],
        "last_render_path": "/tmp/catastrophic_undo.png",
        "last_verified_path": None,
        "scene_bbox": {"center": [0, 0, 0], "dimensions": [12000.0, 8000.0, 6000.0]},
        "enabled_tool_names": ["undo_last_snapshot", "get_scene_info", "observe_scene_global"],
    }

    result = verify_node(state)

    assert result["verify_forced_recovery"] is False
    assert result["catastrophic_recovery_attempts"] == 0
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], ToolMessage)
    payload = result["messages"][0].content
    payload = payload if isinstance(payload, dict) else ast.literal_eval(payload)
    assert isinstance(payload, dict)
    assert payload["status"] == "catastrophic"
    assert payload["hard_recovery"]["action"] == "disabled"
    assert payload["hard_recovery"]["forced"] is False
    assert payload["hard_recovery"]["tool_calls"] == []


def test_verify_node_catastrophic_ignores_prior_recovery_counters(monkeypatch):
    def fail_verify_render_with_references(**_kwargs):
        raise AssertionError("verify_render_with_references should not be called for catastrophic precheck")

    monkeypatch.setattr(
        "scene_agent.agent.nodes.verification.verify_render_with_references",
        fail_verify_render_with_references,
    )

    state = {
        "thread_id": "thread-catastrophic-ignore-counters",
        "messages": [HumanMessage(content="Continue building the scene.")],
        "last_render_path": "/tmp/catastrophic_ignore_counters.png",
        "last_verified_path": None,
        "scene_bbox": {"center": [0, 0, 0], "dimensions": [12000.0, 8000.0, 6000.0]},
        "catastrophic_recovery_attempts": 2,
        "last_tool_batch_names": ["execute_blender_code"],
        "enabled_tool_names": ["undo_last_snapshot", "clear_scene", "get_scene_info", "observe_scene_global"],
    }

    result = verify_node(state)

    assert result["verify_forced_recovery"] is False
    assert result["catastrophic_recovery_attempts"] == 0
    assert all(not isinstance(message, AIMessage) for message in result["messages"])
