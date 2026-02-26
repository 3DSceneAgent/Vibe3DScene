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


def test_image_assets_resolve_task_and_global_bindings(tmp_path, monkeypatch):
    monkeypatch.setenv("REFERENCE_IMAGE_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("REFERENCE_IMAGE_MAX_COUNT", "6")
    reload_settings()

    memory = get_reference_image_memory()
    memory.clear_all()

    client = TestClient(api_module.app)

    scene_upload = client.post(
        "/threads/thread-asset-2/images",
        params={"task_id": "plan-task", "role": "scene_reference", "source": "task_upload"},
        files=[("images", ("scene.png", _make_png_bytes((11, 22, 33)), "image/png"))],
    )
    assert scene_upload.status_code == 200
    scene_image_id = scene_upload.json()["images"][0]["id"]

    legacy_upload = client.post(
        "/threads/thread-asset-2/reference-images",
        files=[("images", ("verify.png", _make_png_bytes((44, 55, 66)), "image/png"))],
    )
    assert legacy_upload.status_code == 200
    verification_image_id = legacy_upload.json()["images"][0]["id"]

    scene_assets = client.get(
        "/threads/thread-asset-2/images",
        params={"task_id": "plan-task", "role": "scene_reference"},
    )
    assert scene_assets.status_code == 200
    assert [item["id"] for item in scene_assets.json()["images"]] == [scene_image_id]

    verification_assets = client.get(
        "/threads/thread-asset-2/images",
        params={"task_id": "plan-task", "role": "verification_reference"},
    )
    assert verification_assets.status_code == 200
    assert [item["id"] for item in verification_assets.json()["images"]] == [verification_image_id]
