import time

from fastapi.testclient import TestClient

from scene_agent.config import reload_settings
from scene_agent.interfaces import api as api_module


def test_headless_scene_timeout_includes_diagnostics(monkeypatch):
    monkeypatch.setenv("BLENDER_MODE", "headless")
    monkeypatch.setenv("HEADLESS_REQUEST_TIMEOUT_SECONDS", "1")
    reload_settings()

    def slow_send(*_args, **_kwargs):
        time.sleep(2)
        return {"objects": []}

    monkeypatch.setattr(api_module, "send_blender_command_sync", slow_send)

    client = TestClient(api_module.app)
    response = client.get("/scene/thread-timeout")
    assert response.status_code == 504
    detail = response.json()["detail"]
    assert detail["status"] == "timeout"
    assert "request_id" in detail
    assert "elapsed_ms" in detail
