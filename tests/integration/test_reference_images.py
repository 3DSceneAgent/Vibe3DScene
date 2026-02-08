import io

from fastapi.testclient import TestClient
from PIL import Image

from scene_agent.config import reload_settings
from scene_agent.interfaces import api as api_module
from scene_agent.memory.reference_image_memory import get_reference_image_memory


def _make_png_bytes(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (2, 2), color=color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_upload_and_list_reference_images(tmp_path, monkeypatch):
    monkeypatch.setenv("REFERENCE_IMAGE_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("REFERENCE_IMAGE_MAX_COUNT", "3")
    reload_settings()

    memory = get_reference_image_memory()
    memory.clear_all()

    client = TestClient(api_module.app)
    files = [
        ("images", ("ref1.png", _make_png_bytes((10, 20, 30)), "image/png")),
        ("images", ("ref2.png", _make_png_bytes((40, 50, 60)), "image/png")),
    ]
    upload_response = client.post("/threads/thread-2/reference-images", files=files)
    assert upload_response.status_code == 200
    upload_payload = upload_response.json()
    assert len(upload_payload["images"]) == 2

    list_response = client.get("/threads/thread-2/reference-images")
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert len(list_payload["images"]) == 2
