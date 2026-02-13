from fastapi.testclient import TestClient

from scene_agent.config import reload_settings
from scene_agent.interfaces import api as api_module


def test_parse_example_prompts_supports_numbered_markdown():
    raw = "\n".join(
        [
            "1. first prompt",
            "2) second prompt",
            "- 3. third prompt",
            "* 4) fourth prompt",
        ]
    )

    parsed = api_module.parse_example_prompts(raw)

    assert parsed == ["first prompt", "second prompt", "third prompt", "fourth prompt"]


def test_get_example_prompts_endpoint_reads_markdown(monkeypatch, tmp_path):
    prompts_file = tmp_path / "example_prompts.md"
    prompts_file.write_text("1. Build a wooden chair\n2. Add studio lighting\n", encoding="utf-8")
    monkeypatch.setattr(api_module, "EXAMPLE_PROMPTS_PATH", prompts_file)
    monkeypatch.setenv("BLENDER_MODE", "headless")
    reload_settings()

    with TestClient(api_module.app) as client:
        response = client.get("/example-prompts")

    assert response.status_code == 200
    assert response.json() == {"prompts": ["Build a wooden chair", "Add studio lighting"]}


def test_get_mcp_tools_endpoint_returns_loaded_tools(monkeypatch):
    class DummyAgent:
        _available_tool_names = ["get_scene_info", "camera_observe", "get_scene_info"]

    async def fake_get_agent(thread_id: str):
        assert thread_id == "thread-1"
        return DummyAgent()

    monkeypatch.setattr(api_module, "get_agent", fake_get_agent)
    monkeypatch.setenv("BLENDER_MODE", "headless")
    reload_settings()

    with TestClient(api_module.app) as client:
        response = client.get("/threads/thread-1/mcp-tools")

    assert response.status_code == 200
    assert response.json() == {
        "thread_id": "thread-1",
        "loaded": True,
        "tool_count": 2,
        "tools": ["camera_observe", "get_scene_info"],
        "blender_mode": "headless",
    }
