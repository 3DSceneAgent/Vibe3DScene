import os
import sys
import types

from scene_agent.config import get_settings, reload_settings
from scene_agent.vlm.providers import QwenProvider, get_vlm_provider


def test_settings_accept_qwen_provider_and_key(monkeypatch):
    monkeypatch.setenv("VLM_PROVIDER", "qwen")
    monkeypatch.setenv("VLM_MODEL", "qwen-vl-max-latest")
    monkeypatch.setenv("QWEN_API_KEY", "qwen-key")
    monkeypatch.setenv("VLM_API_KEY", "fallback-key")
    reload_settings()

    settings = get_settings()
    assert settings.vlm_provider == "qwen"
    assert settings.get_vlm_api_key("qwen") == "qwen-key"
    assert settings.get_vlm_default_model("qwen") == "qwen-vl-max-latest"
    assert settings.get_vlm_provider_models("qwen")[0] == "qwen-vl-latest"


def test_qwen_provider_factory_returns_provider():
    provider = get_vlm_provider("qwen", api_key="dashscope-key")
    assert isinstance(provider, QwenProvider)
    assert provider.model == "qwen-vl-max-latest"


def test_qwen_provider_chat_model_uses_dashscope_env(monkeypatch):
    captured: dict[str, object] = {}

    class DummyChatQwen:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    fake_module = types.SimpleNamespace(ChatQwen=DummyChatQwen)
    monkeypatch.setitem(sys.modules, "langchain_qwq", fake_module)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    provider = QwenProvider(api_key="dashscope-key", model="qwen-vl-plus-latest")
    model = provider.get_chat_model()

    assert isinstance(model, DummyChatQwen)
    assert os.environ["DASHSCOPE_API_KEY"] == "dashscope-key"
    assert captured["model"] == "qwen-vl-plus-latest"
    assert captured["streaming"] is True
