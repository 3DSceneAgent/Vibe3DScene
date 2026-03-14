from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from scene_agent.config import reload_settings
from scene_agent.interfaces import api as api_module
from scene_agent.interfaces.api import routes_chat as api_routes_chat


def _reset_vlm_runtime_state() -> None:
    api_module._agent_graphs_by_thread.clear()
    with api_module._thread_vlm_lock:
        api_module._thread_vlm_configs.clear()


def test_get_vlm_models_endpoint_returns_catalog_and_thread_selection(monkeypatch):
    monkeypatch.setenv("BLENDER_MODE", "headless")
    monkeypatch.setenv("VLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    reload_settings()
    _reset_vlm_runtime_state()

    with TestClient(api_module.app) as client:
        response = client.get("/vlm/models", params={"thread_id": "thread-vlm-1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["default_provider"] == "openai"
    assert payload["thread_selection"]["thread_id"] == "thread-vlm-1"
    assert payload["thread_selection"]["provider"] == "openai"
    assert payload["thread_selection"]["locked"] is False
    assert any(provider["provider"] == "openai" for provider in payload["providers"])
    assert any(provider["provider"] == "qwen" for provider in payload["providers"])


def test_chat_allows_model_change_after_session_starts(monkeypatch):
    monkeypatch.setenv("BLENDER_MODE", "headless")
    monkeypatch.setenv("VLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    reload_settings()
    _reset_vlm_runtime_state()

    class DummyAgent:
        async def ainvoke(self, *_args, **_kwargs):
            return {"messages": [AIMessage(content="ok")], "todos": []}

    async def fake_get_agent(_thread_id=None):
        return DummyAgent()

    monkeypatch.setattr(api_routes_chat, "get_agent", fake_get_agent)

    with TestClient(api_module.app) as client:
        first = client.post(
            "/chat",
            json={
                "message": "hello",
                "thread_id": "thread-vlm-lock",
                "vlm_provider": "openai",
                "vlm_model": "gpt-4o",
            },
        )
        second = client.post(
            "/chat",
            json={
                "message": "hello again",
                "thread_id": "thread-vlm-lock",
                "vlm_provider": "openai",
                "vlm_model": "gpt-4.1",
            },
        )
        with api_module._thread_vlm_lock:
            config = api_module._thread_vlm_configs.get("thread-vlm-lock")

    assert first.status_code == 200
    assert second.status_code == 200
    assert config is not None
    assert config["provider"] == "openai"
    assert config["model"] == "gpt-4.1"
