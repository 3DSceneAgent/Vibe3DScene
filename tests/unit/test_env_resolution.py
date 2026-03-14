import os

from scene_agent.config import reload_settings
from scene_agent import env as env_module


def test_load_project_dotenv_overrides_inherited_env_by_default(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    module_dir = project_root / "scene_agent"
    module_dir.mkdir(parents=True)
    fake_module_file = module_dir / "env.py"
    fake_module_file.write_text("# test module placeholder\n", encoding="utf-8")
    (project_root / ".env").write_text("GEMINI_API_KEY=from-dotenv\n", encoding="utf-8")

    monkeypatch.setattr(env_module, "__file__", str(fake_module_file))
    monkeypatch.setattr(env_module, "_DOTENV_LOADED", False)
    monkeypatch.setenv("GEMINI_API_KEY", "from-shell")

    loaded = env_module.load_project_dotenv()

    assert loaded is True
    assert os.environ["GEMINI_API_KEY"] == "from-dotenv"


def test_settings_ignore_vlm_api_key_fallback(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("VLM_API_KEY", "legacy-fallback")

    settings = reload_settings()

    assert settings.get_vlm_api_key("openai") is None
