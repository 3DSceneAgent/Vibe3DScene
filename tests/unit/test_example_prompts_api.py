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
        _available_tool_hints = {
            "get_scene_info": "Read current scene objects.",
            "camera_observe": "Render scene from a camera.",
            "unused_tool": "Should be filtered out.",
        }

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
        "tool_hints": {
            "camera_observe": "Render scene from a camera.",
            "get_scene_info": "Read current scene objects.",
        },
        "blender_mode": "headless",
    }


def test_extract_available_tool_hints_filters_invalid_entries():
    class DummyAgent:
        _available_tool_hints = {
            " get_scene_info ": "  Read   scene  ",
            "camera_observe": "",
            "invalid_type": 123,
            "": "ignored",
            9: "ignored",
        }

    hints = api_module.extract_available_tool_hints(DummyAgent())

    assert hints == {"get_scene_info": "Read scene"}


def test_get_mcp_tools_endpoint_fills_default_hint_when_missing(monkeypatch):
    class DummyAgent:
        _available_tool_names = ["get_scene_info"]

    async def fake_get_agent(thread_id: str):
        assert thread_id == "thread-default-hint"
        return DummyAgent()

    monkeypatch.setattr(api_module, "get_agent", fake_get_agent)
    monkeypatch.setenv("BLENDER_MODE", "headless")
    reload_settings()

    with TestClient(api_module.app) as client:
        response = client.get("/threads/thread-default-hint/mcp-tools")

    assert response.status_code == 200
    assert response.json()["tool_hints"] == {"get_scene_info": "MCP tool: get scene info."}


def test_resolve_enabled_tool_names_returns_intersection():
    class DummyAgent:
        _available_tool_names = ["camera_observe", "get_scene_info", "camera_observe"]

    resolved = api_module.resolve_enabled_tool_names(
        DummyAgent(),
        ["get_scene_info", "unknown_tool"],
    )

    assert resolved == ["get_scene_info"]


def test_chat_endpoint_passes_enabled_tool_names(monkeypatch):
    captured_payloads = []

    class DummyAgent:
        _available_tool_names = ["camera_observe", "get_scene_info"]

        async def ainvoke(self, payload, config=None):
            _ = config
            captured_payloads.append(payload)
            return {"messages": ["ok"], "todos": []}

    async def fake_get_agent(thread_id: str):
        assert thread_id == "thread-2"
        return DummyAgent()

    monkeypatch.setattr(api_module, "get_agent", fake_get_agent)
    monkeypatch.setattr(
        api_module,
        "_resolve_thread_vlm_for_chat",
        lambda *_args, **_kwargs: {"provider": "openai", "model": "gpt-4o", "api_key": "test"},
    )

    with TestClient(api_module.app) as client:
        response = client.post(
            "/chat",
            json={
                "message": "hello",
                "thread_id": "thread-2",
                "enabled_mcp_tools": ["camera_observe", "invalid_tool"],
            },
        )

    assert response.status_code == 200
    assert captured_payloads
    assert captured_payloads[0]["enabled_tool_names"] == ["camera_observe"]


def test_chat_stream_passes_enabled_tool_names(monkeypatch):
    captured_payloads = []

    class DummyAgent:
        _available_tool_names = ["camera_observe", "get_scene_info"]

        async def astream(self, payload, config=None, stream_mode=None):
            _ = (config, stream_mode)
            captured_payloads.append(payload)
            yield ("messages", [{"type": "ai", "content": "ok"}])

    async def fake_get_agent(thread_id: str):
        assert thread_id == "thread-3"
        return DummyAgent()

    monkeypatch.setattr(api_module, "get_agent", fake_get_agent)
    monkeypatch.setattr(
        api_module,
        "_resolve_thread_vlm_for_chat",
        lambda *_args, **_kwargs: {"provider": "openai", "model": "gpt-4o", "api_key": "test"},
    )

    with TestClient(api_module.app) as client:
        with client.stream(
            "POST",
            "/chat/stream",
            json={
                "message": "hello",
                "thread_id": "thread-3",
                "enabled_mcp_tools": ["get_scene_info", "missing_tool"],
            },
        ) as response:
            assert response.status_code == 200
            for _ in response.iter_lines():
                pass

    assert captured_payloads
    assert captured_payloads[0]["enabled_tool_names"] == ["get_scene_info"]
