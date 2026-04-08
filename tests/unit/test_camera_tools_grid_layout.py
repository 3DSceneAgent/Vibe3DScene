from __future__ import annotations

from pathlib import Path

from PIL import Image as PILImage

from mcp_server.tools.multimodal import camera_tools


def _write_image(path: Path, *, size: tuple[int, int] = (10, 10)) -> None:
    image = PILImage.new("RGB", size, color=(255, 255, 255))
    image.save(path, "PNG")
    image.close()


def test_build_scene_grid_image_uses_single_row_for_three_images(tmp_path, monkeypatch):
    image_paths = []
    for index in range(3):
        path = tmp_path / f"img-{index}.png"
        _write_image(path)
        image_paths.append(path)

    image_url_map = {
        f"/renders/{path.name}": str(path)
        for path in image_paths
    }
    captured_size: dict[str, tuple[int, int]] = {}

    monkeypatch.setattr(
        camera_tools,
        "_resolve_render_url_to_local_path",
        lambda image_url: image_url_map.get(image_url),
    )

    def fake_process_and_save_render(temp_grid_path: str, thread_id: str, camera_name: str, logger=None):
        with PILImage.open(temp_grid_path) as grid_image:
            captured_size["value"] = grid_image.size
        return f"/renders/{camera_name}.jpg"

    monkeypatch.setattr(camera_tools, "process_and_save_render", fake_process_and_save_render)

    result = camera_tools._build_scene_grid_image(
        [(f"cam-{index}", f"/renders/{path.name}") for index, path in enumerate(image_paths)],
        thread_id="thread-grid-3",
    )

    assert result == f"/renders/{camera_tools._SCENE_GRID_CAMERA_NAME}.jpg"
    assert captured_size["value"] == (30, 10)


def test_build_scene_grid_image_keeps_two_by_two_for_four_images(tmp_path, monkeypatch):
    image_paths = []
    for index in range(4):
        path = tmp_path / f"img-{index}.png"
        _write_image(path)
        image_paths.append(path)

    image_url_map = {
        f"/renders/{path.name}": str(path)
        for path in image_paths
    }
    captured_size: dict[str, tuple[int, int]] = {}

    monkeypatch.setattr(
        camera_tools,
        "_resolve_render_url_to_local_path",
        lambda image_url: image_url_map.get(image_url),
    )

    def fake_process_and_save_render(temp_grid_path: str, thread_id: str, camera_name: str, logger=None):
        with PILImage.open(temp_grid_path) as grid_image:
            captured_size["value"] = grid_image.size
        return f"/renders/{camera_name}.jpg"

    monkeypatch.setattr(camera_tools, "process_and_save_render", fake_process_and_save_render)

    result = camera_tools._build_scene_grid_image(
        [(f"cam-{index}", f"/renders/{path.name}") for index, path in enumerate(image_paths)],
        thread_id="thread-grid-4",
    )

    assert result == f"/renders/{camera_tools._SCENE_GRID_CAMERA_NAME}.jpg"
    assert captured_size["value"] == (20, 20)
