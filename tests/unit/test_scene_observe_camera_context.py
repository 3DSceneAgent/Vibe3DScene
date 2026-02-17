from __future__ import annotations

from pathlib import Path

from mcp_server.tools.multimodal.camera_tools import SCENE_CAMERA_NAMES, update_scene_cameras
from scene_agent.agent.nodes import scene_observe_node
from scene_agent.agent.state import AgentState


def test_update_scene_cameras_uses_injected_sender_without_runtime(monkeypatch):
    command_calls: list[tuple[str, dict | None]] = []

    def fail_runtime_lookup(_logger):
        raise AssertionError("runtime.get_blender_connection should not be called")

    def fake_process_and_save_render(
        filepath: str,
        thread_id: str,
        camera_name: str,
        *,
        logger=None,
    ) -> str:
        _ = logger
        return f"https://example.com/renders/{thread_id}/{camera_name}.png"

    def fake_send_blender_command(command_type: str, params: dict | None = None) -> dict:
        command_calls.append((command_type, params))
        if command_type == "get_scene_info":
            return {
                "success": True,
                "objects": [
                    {
                        "name": "Cube",
                        "world_bounding_box": [[0.0, 0.0, 0.0], [2.0, 2.0, 2.0]],
                    }
                ],
            }
        if command_type == "camera_observe":
            filepath = str((params or {}).get("filepath", ""))
            if filepath:
                Path(filepath).write_bytes(b"fake-image")
            return {"success": True, "filepath": filepath}
        raise AssertionError(f"Unexpected command: {command_type}")

    monkeypatch.setattr(
        "mcp_server.tools.multimodal.camera_tools.runtime.get_blender_connection",
        fail_runtime_lookup,
    )
    monkeypatch.setattr(
        "mcp_server.tools.multimodal.camera_tools.process_and_save_render",
        fake_process_and_save_render,
    )

    result = update_scene_cameras(
        thread_id="thread-camera-context",
        send_blender_command=fake_send_blender_command,
    )

    assert result["success"] is True
    assert len(result["cameras"]) == len(SCENE_CAMERA_NAMES)
    assert len(result["image_urls"]) == len(SCENE_CAMERA_NAMES)
    assert command_calls[0][0] == "get_scene_info"
    assert [name for name, _ in command_calls[1:]] == ["camera_observe"] * len(SCENE_CAMERA_NAMES)


def test_scene_observe_node_invalidates_render_path_on_failure(monkeypatch):
    """
    Regression test: when scene_observe_node detects a scene-mutating tool
    but all rendering attempts fail, it should return {"last_render_path": None}
    to invalidate any stale render path. This prevents verify_node from
    incorrectly verifying an old render against the mutated scene.
    """

    def fake_update_scene_cameras_fails(*args, **kwargs):
        return {"success": False}

    monkeypatch.setattr(
        "mcp_server.tools.multimodal.camera_tools.update_scene_cameras",
        fake_update_scene_cameras_fails,
    )

    # State with scene-mutating tool and a stale render path
    state: AgentState = {
        "thread_id": "test-thread",
        "last_tool_batch_names": ["execute_blender_code"],
        "last_render_path": "https://old-render.png",  # stale render
        "messages": [],
    }

    result = scene_observe_node(state)

    # Should invalidate the stale render path, not return empty dict
    assert result == {"last_render_path": None}


def test_scene_observe_node_invalidates_render_path_when_no_images(monkeypatch):
    """
    Regression test: when scene_observe_node gets success=True but no
    image_urls from update_scene_cameras, it should still invalidate the
    render path to prevent stale verification.
    """

    def fake_update_scene_cameras_no_images(*args, **kwargs):
        return {"success": True, "cameras": [], "image_urls": [], "scene_bbox": {}}

    monkeypatch.setattr(
        "mcp_server.tools.multimodal.camera_tools.update_scene_cameras",
        fake_update_scene_cameras_no_images,
    )

    state: AgentState = {
        "thread_id": "test-thread",
        "last_tool_batch_names": ["import_glb_model"],
        "last_render_path": "https://old-render.png",
        "messages": [],
    }

    result = scene_observe_node(state)

    # Should invalidate the stale render path
    assert result == {"last_render_path": None}


def test_scene_observe_node_invalidates_render_path_on_exception(monkeypatch):
    """
    Regression test: when scene_observe_node encounters an exception during
    update_scene_cameras, it should invalidate the render path.
    """

    def fake_update_scene_cameras_raises(*args, **kwargs):
        raise RuntimeError("Blender connection lost")

    monkeypatch.setattr(
        "mcp_server.tools.multimodal.camera_tools.update_scene_cameras",
        fake_update_scene_cameras_raises,
    )

    state: AgentState = {
        "thread_id": "test-thread",
        "last_tool_batch_names": ["generate_trellis2_model"],
        "last_render_path": "https://old-render.png",
        "messages": [],
    }

    result = scene_observe_node(state)

    # Should invalidate the stale render path
    assert result == {"last_render_path": None}
