from types import SimpleNamespace

import pytest
from PIL import Image as PILImage

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


def test_observe_scene_global_returns_single_grid_markdown(monkeypatch):
    monkeypatch.setattr(
        camera_tools,
        "update_scene_cameras",
        lambda **kwargs: {
            "success": True,
            "scene_bbox": {"center": [0.0, 0.0, 0.5], "dimensions": [2.0, 2.0, 1.0]},
            "cameras": [
                {
                    "camera_name": "SceneCamera_NE",
                    "image_url": "https://example.test/ne.png",
                },
                {
                    "camera_name": "SceneCamera_NW",
                    "image_url": "https://example.test/nw.png",
                },
            ],
            "image_urls": ["https://example.test/ne.png", "https://example.test/nw.png"],
        },
    )
    monkeypatch.setattr(
        camera_tools,
        "_build_scene_grid_image",
        lambda image_entries, *, thread_id: "https://example.test/scene_grid.jpg",
    )

    ctx = SimpleNamespace(request_context={"thread_id": "thread-global-observe"})
    result = camera_tools.observe_scene_global(ctx=ctx)

    assert result.isError is False
    assert result.content
    payload = str(result.content[0])
    assert "Grid overview" in payload
    assert "https://example.test/scene_grid.jpg" in payload
    assert "scene_bbox.center" in payload
    assert "Captured views:" not in payload
    assert "https://example.test/ne.png" not in payload
    assert "https://example.test/nw.png" not in payload


def test_observe_scene_global_builds_grid_for_local_render_urls(monkeypatch):
    filenames = []
    for idx, color in enumerate(((255, 20, 20), (20, 255, 20), (20, 20, 255), (230, 200, 40))):
        filename = f"observe_grid_src_{idx}.png"
        image_path = camera_tools.RENDERS_DIR / filename
        PILImage.new("RGB", (48, 32), color=color).save(image_path, format="PNG")
        filenames.append(filename)

    try:
        monkeypatch.setattr(
            camera_tools,
            "update_scene_cameras",
            lambda **kwargs: {
                "success": True,
                "scene_bbox": {"center": [0.0, 0.0, 0.5], "dimensions": [2.0, 2.0, 1.0]},
                "cameras": [
                    {"camera_name": "SceneCamera_NE", "image_url": f"/renders/{filenames[0]}"},
                    {"camera_name": "SceneCamera_NW", "image_url": f"/renders/{filenames[1]}"},
                    {"camera_name": "SceneCamera_SE", "image_url": f"/renders/{filenames[2]}"},
                    {"camera_name": "SceneCamera_SW", "image_url": f"/renders/{filenames[3]}"},
                ],
            },
        )
        monkeypatch.setattr(
            camera_tools,
            "process_and_save_render",
            lambda filepath, thread_id, camera_name, logger=None: f"/renders/{camera_name}_grid.jpg",
        )

        ctx = SimpleNamespace(request_context={"thread_id": "thread-global-grid"})
        result = camera_tools.observe_scene_global(ctx=ctx)

        assert result.isError is False
        assert result.content
        payload = str(result.content[0])
        assert "SceneGlobalGrid" in payload
        assert "/renders/SceneGlobalGrid_grid.jpg" in payload
        assert "Captured views:" not in payload
    finally:
        for filename in filenames:
            try:
                (camera_tools.RENDERS_DIR / filename).unlink()
            except OSError:
                pass


def test_observe_scene_global_falls_back_to_single_view_when_grid_missing(monkeypatch):
    monkeypatch.setattr(
        camera_tools,
        "update_scene_cameras",
        lambda **kwargs: {
            "success": True,
            "scene_bbox": {"center": [0.0, 0.0, 0.5], "dimensions": [2.0, 2.0, 1.0]},
            "cameras": [
                {
                    "camera_name": "SceneCamera_NE",
                    "image_url": "https://example.test/ne.png",
                },
                {
                    "camera_name": "SceneCamera_NW",
                    "image_url": "https://example.test/nw.png",
                },
            ],
        },
    )
    monkeypatch.setattr(camera_tools, "_build_scene_grid_image", lambda image_entries, *, thread_id: None)

    ctx = SimpleNamespace(request_context={"thread_id": "thread-global-fallback"})
    result = camera_tools.observe_scene_global(ctx=ctx)

    assert result.isError is False
    assert result.content
    payload = str(result.content[0])
    assert "Fallback view" in payload
    assert "https://example.test/ne.png" in payload
    assert "https://example.test/nw.png" not in payload
    assert "Captured views:" not in payload


def test_observe_scene_global_gracefully_reports_failure(monkeypatch):
    monkeypatch.setattr(
        camera_tools,
        "update_scene_cameras",
        lambda **kwargs: {"success": False, "error": "No mesh objects with bounding boxes found"},
    )

    ctx = SimpleNamespace(request_context={"thread_id": "thread-global-observe-fail"})
    result = camera_tools.observe_scene_global(ctx=ctx)

    assert result.isError is False
    assert "Global scene observation failed" in str(result.content[0])
