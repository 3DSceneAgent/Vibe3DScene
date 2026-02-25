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
    assert [camera["camera_name"] for camera in result["cameras"]] == list(SCENE_CAMERA_NAMES)
    assert "SceneCamera_TopDown" in SCENE_CAMERA_NAMES
    assert command_calls[0][0] == "get_scene_info"
    assert [name for name, _ in command_calls[1:]] == ["camera_observe"] * len(SCENE_CAMERA_NAMES)
    top_down_call = next(
        params
        for name, params in command_calls[1:]
        if name == "camera_observe" and isinstance(params, dict) and params.get("camera_name") == "SceneCamera_TopDown"
    )
    assert top_down_call.get("elevation") == 89.0


def test_update_scene_cameras_accepts_bbox_center_dimensions(monkeypatch):
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
                        "bbox": {
                            "center": [1.0, 2.0, 0.5],
                            "dimensions": [2.0, 4.0, 1.0],
                        },
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
        thread_id="thread-camera-bbox",
        send_blender_command=fake_send_blender_command,
    )

    assert result["success"] is True
    assert len(result["cameras"]) == len(SCENE_CAMERA_NAMES)
    assert len(result["image_urls"]) == len(SCENE_CAMERA_NAMES)
    assert command_calls[0][0] == "get_scene_info"
    assert [name for name, _ in command_calls[1:]] == ["camera_observe"] * len(SCENE_CAMERA_NAMES)


def test_update_scene_cameras_falls_back_to_scene_bbox(monkeypatch):
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
                        "name": "Camera",
                        "type": "CAMERA",
                    }
                ],
                "scene_bbox": {
                    "center": [0.0, 0.0, 0.0],
                    "dimensions": [3.0, 2.0, 1.0],
                },
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
        thread_id="thread-camera-scene-bbox",
        send_blender_command=fake_send_blender_command,
    )

    assert result["success"] is True
    assert len(result["cameras"]) == len(SCENE_CAMERA_NAMES)
    assert len(result["image_urls"]) == len(SCENE_CAMERA_NAMES)
    assert command_calls[0][0] == "get_scene_info"
    assert [name for name, _ in command_calls[1:]] == ["camera_observe"] * len(SCENE_CAMERA_NAMES)


def test_update_scene_cameras_can_use_direct_pose_path(monkeypatch):
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
        if command_type == "camera_set_pose":
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
        thread_id="thread-camera-direct-pose",
        send_blender_command=fake_send_blender_command,
        use_direct_pose=True,
    )

    assert result["success"] is True
    assert len(result["cameras"]) == len(SCENE_CAMERA_NAMES)
    assert len(result["image_urls"]) == len(SCENE_CAMERA_NAMES)
    assert [name for name, _ in command_calls[1:]] == ["camera_set_pose"] * len(SCENE_CAMERA_NAMES)


def test_update_scene_cameras_trims_extreme_bbox_outliers(monkeypatch):
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
                        "name": "Chair",
                        "world_bounding_box": [[0.0, 0.0, 0.0], [1.2, 1.0, 1.4]],
                    },
                    {
                        "name": "Table",
                        "world_bounding_box": [[1.4, 0.0, 0.0], [3.0, 1.6, 1.2]],
                    },
                    {
                        # Simulates a broken scale/hierarchy transform.
                        "name": "BrokenScaledGroup",
                        "world_bounding_box": [[-500.0, -500.0, -100.0], [500.0, 500.0, 100.0]],
                    },
                ],
            }
        if command_type == "camera_set_pose":
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
        thread_id="thread-camera-outlier-trim",
        send_blender_command=fake_send_blender_command,
        use_direct_pose=True,
    )

    assert result["success"] is True
    pose_calls = [params for name, params in command_calls if name == "camera_set_pose" and isinstance(params, dict)]
    assert pose_calls
    assert all(params.get("object_names") == ["Chair", "Table"] for params in pose_calls)


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
        "enabled_tool_names": ["camera_observe", "render_from_camera"],
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
        "enabled_tool_names": ["camera_observe", "render_from_camera"],
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
        "enabled_tool_names": ["camera_observe", "render_from_camera"],
        "last_render_path": "https://old-render.png",
        "messages": [],
    }

    result = scene_observe_node(state)

    # Should invalidate the stale render path
    assert result == {"last_render_path": None}


def test_scene_observe_node_treats_delete_objects_as_scene_mutation(monkeypatch):
    def fake_update_scene_cameras(*args, **kwargs):
        return {
            "success": True,
            "scene_bbox": {"center": [0.0, 0.0, 0.0], "dimensions": [1.0, 1.0, 1.0]},
            "cameras": [
                {
                    "camera_name": "SceneCamera_NE",
                    "image_url": "https://example.com/renders/scene_ne.png",
                }
            ],
            "image_urls": ["https://example.com/renders/scene_ne.png"],
        }

    monkeypatch.setattr(
        "mcp_server.tools.multimodal.camera_tools.update_scene_cameras",
        fake_update_scene_cameras,
    )

    state: AgentState = {
        "thread_id": "test-thread",
        "last_tool_batch_names": ["delete_objects"],
        "enabled_tool_names": ["camera_observe", "render_from_camera"],
        "messages": [],
    }

    result = scene_observe_node(state)

    assert result.get("last_render_source") == "scene_observe"
    assert result.get("last_render_path") == "https://example.com/renders/scene_ne.png"


def test_scene_observe_node_treats_clear_scene_as_scene_mutation(monkeypatch):
    def fake_update_scene_cameras(*args, **kwargs):
        return {
            "success": True,
            "scene_bbox": {"center": [0.0, 0.0, 0.0], "dimensions": [1.0, 1.0, 1.0]},
            "cameras": [
                {
                    "camera_name": "SceneCamera_NE",
                    "image_url": "https://example.com/renders/scene_ne.png",
                }
            ],
            "image_urls": ["https://example.com/renders/scene_ne.png"],
        }

    monkeypatch.setattr(
        "mcp_server.tools.multimodal.camera_tools.update_scene_cameras",
        fake_update_scene_cameras,
    )

    state: AgentState = {
        "thread_id": "test-thread",
        "last_tool_batch_names": ["clear_scene"],
        "enabled_tool_names": ["camera_observe", "render_from_camera"],
        "messages": [],
    }

    result = scene_observe_node(state)

    assert result.get("last_render_source") == "scene_observe"
    assert result.get("last_render_path") == "https://example.com/renders/scene_ne.png"


def test_scene_observe_node_uses_viewport_screenshot_in_local_client(monkeypatch):
    command_calls: list[tuple[str, dict | None, str | None]] = []

    def fail_update_scene_cameras(*args, **kwargs):
        raise AssertionError("update_scene_cameras should not run in local-client screenshot mode")

    def fake_process_and_save_render(
        filepath: str,
        thread_id: str,
        camera_name: str,
        *,
        logger=None,
    ) -> str:
        _ = logger
        assert Path(filepath).exists()
        assert camera_name == "SceneObserveViewport"
        return f"https://example.com/renders/{thread_id}/viewport.jpg"

    def fake_send_blender_command_sync(
        command_type: str,
        params: dict | None = None,
        thread_id: str | None = None,
    ) -> dict:
        command_calls.append((command_type, params, thread_id))
        if command_type != "get_viewport_screenshot":
            raise AssertionError(f"Unexpected command: {command_type}")
        filepath = str((params or {}).get("filepath", ""))
        if filepath:
            Path(filepath).write_bytes(b"fake-image")
        return {"success": True, "filepath": filepath, "width": 640, "height": 360}

    monkeypatch.setattr(
        "mcp_server.tools.multimodal.camera_tools.update_scene_cameras",
        fail_update_scene_cameras,
    )
    monkeypatch.setattr(
        "scene_agent.interfaces.api.send_blender_command_sync",
        fake_send_blender_command_sync,
    )
    monkeypatch.setattr(
        "scene_agent.utils.rendering.process_and_save_render",
        fake_process_and_save_render,
    )

    state: AgentState = {
        "thread_id": "thread-local-client",
        "last_tool_batch_names": ["execute_blender_code"],
        "enabled_tool_names": ["get_scene_info", "get_viewport_screenshot", "clear_scene"],
        "messages": [],
    }

    result = scene_observe_node(state)

    assert result.get("last_render_source") == "scene_observe"
    assert result.get("last_render_path") == "https://example.com/renders/thread-local-client/viewport.jpg"
    messages = result.get("messages")
    assert isinstance(messages, list) and len(messages) == 1
    message_content = messages[0].content
    assert isinstance(message_content, list)
    assert any(
        isinstance(item, dict)
        and item.get("type") == "image_url"
        and isinstance(item.get("image_url"), dict)
        and item["image_url"].get("url") == "https://example.com/renders/thread-local-client/viewport.jpg"
        for item in message_content
    )
    assert command_calls[0][0] == "get_viewport_screenshot"
    assert command_calls[0][2] == "thread-local-client"


def test_scene_observe_node_invalidates_render_path_when_local_screenshot_fails(monkeypatch):
    def fail_update_scene_cameras(*args, **kwargs):
        raise AssertionError("update_scene_cameras should not run in local-client screenshot mode")

    def fake_send_blender_command_sync(
        command_type: str,
        params: dict | None = None,
        thread_id: str | None = None,
    ) -> dict:
        _ = params
        _ = thread_id
        if command_type != "get_viewport_screenshot":
            raise AssertionError(f"Unexpected command: {command_type}")
        return {"error": "No active 3D viewport found"}

    monkeypatch.setattr(
        "mcp_server.tools.multimodal.camera_tools.update_scene_cameras",
        fail_update_scene_cameras,
    )
    monkeypatch.setattr(
        "scene_agent.interfaces.api.send_blender_command_sync",
        fake_send_blender_command_sync,
    )

    state: AgentState = {
        "thread_id": "thread-local-client",
        "last_tool_batch_names": ["import_blend_contents"],
        "enabled_tool_names": ["get_scene_info", "get_viewport_screenshot", "clear_scene"],
        "last_render_path": "https://old-render.png",
        "messages": [],
    }

    result = scene_observe_node(state)

    assert result == {"last_render_path": None}
