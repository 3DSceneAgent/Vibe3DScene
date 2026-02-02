import os

from fastapi.testclient import TestClient

from blender import session_manager
from config import reload_settings
from interfaces import api as api_module


def test_headless_session_created_on_scene_request(monkeypatch):
    os.environ["BLENDER_MODE"] = "headless"
    reload_settings()

    manager = session_manager.get_session_manager()
    manager.remove("headless-session")

    def fake_send_blender_command(_command_type, _params=None, _thread_id=None):
        return {"objects": []}

    monkeypatch.setattr(api_module, "send_blender_command_sync", fake_send_blender_command)
    client = TestClient(api_module.app)

    response = client.get("/scene/headless-session")
    assert response.status_code == 200
    assert manager.get("headless-session") is not None
