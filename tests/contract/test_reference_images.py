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


def test_image_assets_and_bindings_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("REFERENCE_IMAGE_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("REFERENCE_IMAGE_MAX_COUNT", "5")
    reload_settings()

    memory = get_reference_image_memory()
    memory.clear_all()

    client = TestClient(api_module.app)
    files = [("images", ("scene_ref.png", _make_png_bytes((1, 2, 3)), "image/png"))]
    upload_response = client.post(
        "/threads/thread-asset-1/images",
        params={"task_id": "task-a", "role": "scene_reference", "source": "user_upload"},
        files=files,
    )
    assert upload_response.status_code == 200
    upload_payload = upload_response.json()
    assert upload_payload["thread_id"] == "thread-asset-1"
    assert len(upload_payload["images"]) == 1
    uploaded = upload_payload["images"][0]
    assert uploaded["source"] == "user_upload"
    image_id = uploaded["id"]

    list_assets = client.get(
        "/threads/thread-asset-1/images",
        params={"task_id": "task-a", "role": "scene_reference"},
    )
    assert list_assets.status_code == 200
    list_payload = list_assets.json()
    assert [item["id"] for item in list_payload["images"]] == [image_id]

    list_bindings = client.get("/threads/thread-asset-1/tasks/task-a/image-bindings")
    assert list_bindings.status_code == 200
    bindings_payload = list_bindings.json()
    assert len(bindings_payload["bindings"]) == 1
    assert bindings_payload["bindings"][0]["role"] == "scene_reference"
    assert bindings_payload["bindings"][0]["image_id"] == image_id

    upsert_response = client.post(
        "/threads/thread-asset-1/tasks/task-a/image-bindings",
        json={"bindings": [{"image_id": image_id, "role": "style_reference", "weight": 0.5}]},
    )
    assert upsert_response.status_code == 200
    upsert_payload = upsert_response.json()
    assert len(upsert_payload["bindings"]) == 1
    assert upsert_payload["bindings"][0]["role"] == "style_reference"
    assert upsert_payload["bindings"][0]["weight"] == 0.5
