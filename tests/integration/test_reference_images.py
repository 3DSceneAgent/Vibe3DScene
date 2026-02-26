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
    upload_response = client.post("/threads/thread-2/images", files=files)
    assert upload_response.status_code == 200
    upload_payload = upload_response.json()
    assert len(upload_payload["images"]) == 2

    list_response = client.get("/threads/thread-2/images")
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert len(list_payload["images"]) == 2


def test_list_images_limit_returns_latest(tmp_path, monkeypatch):
    monkeypatch.setenv("REFERENCE_IMAGE_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("REFERENCE_IMAGE_MAX_COUNT", "6")
    reload_settings()

    memory = get_reference_image_memory()
    memory.clear_all()

    client = TestClient(api_module.app)

    first_upload = client.post(
        "/threads/thread-asset-2/images",
        files=[("images", ("first.png", _make_png_bytes((11, 22, 33)), "image/png"))],
    )
    assert first_upload.status_code == 200
    first_id = first_upload.json()["images"][0]["id"]

    second_upload = client.post(
        "/threads/thread-asset-2/images",
        files=[("images", ("second.png", _make_png_bytes((44, 55, 66)), "image/png"))],
    )
    assert second_upload.status_code == 200
    second_id = second_upload.json()["images"][0]["id"]

    all_assets = client.get("/threads/thread-asset-2/images")
    assert all_assets.status_code == 200
    assert {item["id"] for item in all_assets.json()["images"]} == {first_id, second_id}

    limited_assets = client.get("/threads/thread-asset-2/images", params={"limit": 1})
    assert limited_assets.status_code == 200
    assert [item["id"] for item in limited_assets.json()["images"]] == [second_id]
