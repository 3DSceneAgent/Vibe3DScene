import io

from fastapi.testclient import TestClient
from PIL import Image

from scene_agent.config import reload_settings
from scene_agent.interfaces import api as api_module
from scene_agent.memory.reference_image_memory import get_reference_image_memory


def _make_png_bytes(color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    image = Image.new("RGB", (2, 2), color=color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_reference_images_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("REFERENCE_IMAGE_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("REFERENCE_IMAGE_MAX_COUNT", "3")
    reload_settings()

    memory = get_reference_image_memory()
    memory.clear_all()

    client = TestClient(api_module.app)
    files = [("images", ("ref.png", _make_png_bytes(), "image/png"))]
    response = client.post("/threads/thread-1/reference-images", files=files)
    assert response.status_code == 200
    payload = response.json()

    assert payload["thread_id"] == "thread-1"
    assert len(payload["images"]) == 1
    assert payload["images"][0]["filename"] == "ref.png"

    list_response = client.get("/threads/thread-1/reference-images")
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert list_payload["thread_id"] == "thread-1"
    assert len(list_payload["images"]) == 1
