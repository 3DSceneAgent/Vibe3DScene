from pathlib import Path

from scene_agent.interfaces import api as api_module


def test_default_api_log_path_points_to_project_root_logs(monkeypatch):
    monkeypatch.delenv("SCENE_AGENT_API_LOG_PATH", raising=False)

    resolved = api_module._resolve_api_log_path()
    project_root = Path(__file__).resolve().parents[2]

    assert resolved == project_root / "logs" / "api_server.log"
