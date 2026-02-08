import io

import pytest
from PIL import Image

from scene_agent.config import reload_settings
from scene_agent.memory.reference_image_memory import ReferenceImageMemory


def _make_png_bytes(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (2, 2), color=color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_reference_image_limit_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("REFERENCE_IMAGE_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("REFERENCE_IMAGE_MAX_COUNT", "2")
    reload_settings()

    memory = ReferenceImageMemory()
    memory.add_images(
        thread_id="thread-1",
        uploads=[
            ("a.png", "image/png", _make_png_bytes((255, 0, 0))),
            ("b.png", "image/png", _make_png_bytes((0, 255, 0))),
        ],
    )

    with pytest.raises(ValueError):
        memory.add_images(
            thread_id="thread-1",
            uploads=[("c.png", "image/png", _make_png_bytes((0, 0, 255)))],
        )
