import io
from pathlib import Path

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
    assert all(image["asset_url"].startswith("/threads/thread-2/images/") for image in upload_payload["images"])

    list_response = client.get("/threads/thread-2/images")
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert len(list_payload["images"]) == 2
    first_asset = list_payload["images"][0]
    assert first_asset["asset_url"] == f"/threads/thread-2/images/{first_asset['id']}"

    download_response = client.get(first_asset["asset_url"])
    assert download_response.status_code == 200
    assert download_response.headers["content-type"] == "image/png"
    assert download_response.content


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


def test_missing_historical_image_file_returns_404_and_omits_asset_url(tmp_path, monkeypatch):
    monkeypatch.setenv("REFERENCE_IMAGE_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("REFERENCE_IMAGE_MAX_COUNT", "3")
    reload_settings()

    memory = get_reference_image_memory()
    memory.clear_all()

    client = TestClient(api_module.app)
    upload_response = client.post(
        "/threads/thread-missing-image/images",
        files=[("images", ("dog2.jpg", _make_png_bytes((77, 88, 99)), "image/png"))],
    )
    assert upload_response.status_code == 200
    uploaded = upload_response.json()["images"][0]
    assert uploaded["asset_url"] == f"/threads/thread-missing-image/images/{uploaded['id']}"

    stored_files = list(Path(tmp_path).glob("thread-missing-image/*"))
    assert stored_files
    stored_files[0].unlink()

    list_response = client.get("/threads/thread-missing-image/images")
    assert list_response.status_code == 200
    listed = list_response.json()["images"][0]
    assert listed["id"] == uploaded["id"]
    assert listed["asset_url"] is None

    download_response = client.get(f"/threads/thread-missing-image/images/{uploaded['id']}")
    assert download_response.status_code == 404
    assert download_response.json()["detail"] == "Image asset file is missing."
