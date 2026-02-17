from __future__ import annotations

from pathlib import Path

from mcp_server.tools.multimodal.camera_tools import SCENE_CAMERA_NAMES, update_scene_cameras


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
