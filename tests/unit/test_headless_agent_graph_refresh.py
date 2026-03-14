import asyncio
import uuid

from scene_agent.blender.session_manager import get_session_manager
from scene_agent.config import reload_settings
from scene_agent.interfaces import api as api_module
from scene_agent.interfaces.api import shared as api_shared


class DummyProcess:
    def __init__(self, running=True):
        self.running = running

    def poll(self):
        return None if self.running else 1


def _reset_runtime_state(thread_id: str) -> None:
    api_module._agent_graphs_by_thread.pop(thread_id, None)
    with api_module._agent_graph_refresh_lock:
        api_module._agent_graph_refresh_tasks.pop(thread_id, None)
    with api_module._thread_vlm_lock:
        api_module._thread_vlm_configs.pop(thread_id, None)


def test_get_agent_keeps_graph_and_restarts_runtime(monkeypatch):
    monkeypatch.setenv("BLENDER_MODE", "headless")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    reload_settings()

    thread_id = "agent-refresh-thread"
    manager = get_session_manager()
    manager.remove(thread_id)
    _reset_runtime_state(thread_id)

    created = []
    ensured = []

    async def fake_create_agent_graph(session_id=None):
        graph = object()
        created.append((session_id, graph))
        return graph

    monkeypatch.setattr(api_shared, "create_agent_graph", fake_create_agent_graph)
    async def fake_ensure_tools(session_id=None):
        ensured.append(session_id)
        return []

    monkeypatch.setattr("scene_agent.tools.blender_tools.get_blender_tools", fake_ensure_tools)

    session = manager.ensure(thread_id, "headless")
    session.process = DummyProcess(running=True)
    session.mcp_process = DummyProcess(running=True)

    first_graph = asyncio.run(api_module.get_agent(thread_id))
    assert len(created) == 1
    assert created[0][0] == thread_id

    session.process = None
    session.mcp_process = None
    second_graph = asyncio.run(api_module.get_agent(thread_id))

    assert len(created) == 1
    assert first_graph is second_graph
    assert ensured == [thread_id]

    _reset_runtime_state(thread_id)
    manager.remove(thread_id)


def test_get_agent_rebuilds_graph_and_migrates_state_on_vlm_switch(monkeypatch):
    monkeypatch.setenv("BLENDER_MODE", "headless")
    monkeypatch.setenv("VLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    reload_settings()

    thread_id = f"agent-vlm-switch-thread-{uuid.uuid4().hex[:8]}"
    coordinator = api_module.get_session_coordinator()
    coordinator.delete_session_metadata(thread_id)
    manager = get_session_manager()
    manager.remove(thread_id)
    _reset_runtime_state(thread_id)

    class _Snapshot:
        def __init__(self, values):
            self.values = values

    class DummyGraph:
        def __init__(self, provider: str, model: str, state: dict | None = None):
            self._vlm_provider = provider
            self._vlm_model = model
            self._state = state or {}
            self.updated = []

        async def aget_state(self, _config):
            return _Snapshot(dict(self._state))

        async def aupdate_state(self, _config, values, as_node=None, task_id=None):
            _ = (as_node, task_id)
            self._state = dict(values) if isinstance(values, dict) else {}
            self.updated.append(self._state)
            return _config

    created: list[DummyGraph] = []
    initial_state = {
        "messages": ["m1", "m2"],
        "todos": [{"id": "todo-1", "description": "demo", "status": "pending"}],
        "thread_id": thread_id,
        "attached_image_ids": "asset-1",
        "request_reference_image_keys": "chair_ref",
        "reference_image_catalog": [],
        "unknown_runtime_field": "drop-me",
    }
    migrated_state = {
        "messages": ["m1", "m2"],
        "todos": [{"id": "todo-1", "description": "demo", "status": "pending"}],
        "thread_id": thread_id,
        "request_reference_image_keys": [],
        "reference_image_catalog": {},
    }

    async def fake_create_graph(*, session_id=None, provider=None, model=None, api_key=None):
        assert session_id == thread_id
        assert api_key == "test-key"
        state = initial_state if not created else {}
        graph = DummyGraph(provider or "openai", model or "gpt-4o", state=state)
        created.append(graph)
        return graph

    monkeypatch.setattr(api_shared, "_create_agent_graph_for_runtime", fake_create_graph)

    session = manager.ensure(thread_id, "headless")
    session.process = DummyProcess(running=True)
    session.mcp_process = DummyProcess(running=True)

    first_graph = asyncio.run(api_module.get_agent(thread_id))
    assert len(created) == 1
    assert first_graph._state == initial_state

    resolved = api_module.resolve_thread_vlm_for_chat(
        thread_id,
        requested_provider="anthropic",
        requested_model="claude-3-5-sonnet-20241022",
    )
    assert resolved["provider"] == "anthropic"
    assert resolved["model"] == "claude-3-5-sonnet-20241022"

    second_graph = asyncio.run(api_module.get_agent(thread_id))
    assert len(created) == 2
    assert second_graph is created[1]
    assert second_graph is not first_graph
    assert second_graph._state == migrated_state
    assert second_graph.updated == [migrated_state]

    _reset_runtime_state(thread_id)
    manager.remove(thread_id)
    coordinator.delete_session_metadata(thread_id)


def test_get_agent_coalesces_concurrent_refreshes(monkeypatch):
    monkeypatch.setenv("BLENDER_MODE", "headless")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    reload_settings()

    thread_id = f"agent-concurrent-thread-{uuid.uuid4().hex[:8]}"
    manager = get_session_manager()
    manager.remove(thread_id)
    _reset_runtime_state(thread_id)

    class DummyGraph:
        def __init__(self, provider: str, model: str):
            self._vlm_provider = provider
            self._vlm_model = model

    created: list[DummyGraph] = []

    async def fake_create_graph(*, session_id=None, provider=None, model=None, api_key=None):
        assert session_id == thread_id
        assert provider is not None
        assert model is not None
        assert api_key
        await asyncio.sleep(0)
        graph = DummyGraph(provider=provider, model=model)
        created.append(graph)
        return graph

    monkeypatch.setattr(api_shared, "_create_agent_graph_for_runtime", fake_create_graph)

    session = manager.ensure(thread_id, "headless")
    session.process = DummyProcess(running=True)
    session.mcp_process = DummyProcess(running=True)

    async def run_concurrently():
        return await asyncio.gather(
            api_module.get_agent(thread_id),
            api_module.get_agent(thread_id),
        )

    first_graph, second_graph = asyncio.run(run_concurrently())

    assert len(created) == 1
    assert first_graph is second_graph

    _reset_runtime_state(thread_id)
    manager.remove(thread_id)
