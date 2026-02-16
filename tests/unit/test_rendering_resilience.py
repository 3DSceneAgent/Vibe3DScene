from types import SimpleNamespace

import pytest

from mcp_server.tools.multimodal import camera_tools
from scene_agent.blender.connection import BlenderCommandError, BlenderConnection


def test_render_from_objects_falls_back_when_targets_have_no_mesh(monkeypatch, tmp_path):
    render_path = tmp_path / "fallback.png"
    render_path.write_bytes(b"fake-image-bytes")
    command_calls: list[str] = []

    class FakeBlender:
        def send_command(self, command_type: str, params=None):
            command_calls.append(command_type)
            if command_type == "render_from_objects":
                raise Exception("Failed to render from objects: Failed to create camera for objects: No valid mesh objects found")
            if command_type == "camera_observe":
                return {"success": True, "filepath": str(render_path), "camera": "Camera_Fallback"}
            raise AssertionError(f"Unexpected command: {command_type}")

    monkeypatch.setattr(camera_tools.runtime, "get_blender_connection", lambda _logger: FakeBlender())
    monkeypatch.setattr(
        camera_tools,
        "process_and_save_render",
        lambda filepath, thread_id, camera_name, logger=None: f"https://example.test/{camera_name}.png",
    )

    ctx = SimpleNamespace(request_context={"thread_id": "thread-fallback"})
    result = camera_tools.render_from_objects(ctx=ctx, object_names=["NotAMeshParent"])

    assert result.isError is False
    assert command_calls[:2] == ["render_from_objects", "camera_observe"]
    assert result.content
    assert "https://example.test/Camera_Fallback.png" in str(result.content[0])


def test_blender_connection_preserves_command_error(monkeypatch):
    class DummySocket:
        def sendall(self, _payload: bytes):
            return None

    connection = BlenderConnection(host="localhost", port=9876)
    connection.sock = DummySocket()
    monkeypatch.setattr(
        connection,
        "receive_full_response",
        lambda buffer_size=8192: b'{"status":"error","message":"No valid mesh objects found"}',
    )

    with pytest.raises(BlenderCommandError, match="No valid mesh objects found"):
        connection.send_command("render_from_objects", {"object_names": ["x"]})
