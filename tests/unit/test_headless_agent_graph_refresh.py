import asyncio

from scene_agent.blender.session_manager import get_session_manager
from scene_agent.config import reload_settings
from scene_agent.interfaces import api as api_module


class DummyProcess:
    def __init__(self, running=True):
        self.running = running

    def poll(self):
        return None if self.running else 1


def test_get_agent_keeps_graph_and_restarts_runtime(monkeypatch):
    monkeypatch.setenv("BLENDER_MODE", "headless")
    reload_settings()

    thread_id = "agent-refresh-thread"
    manager = get_session_manager()
    manager.remove(thread_id)
    api_module._agent_graphs_by_thread.pop(thread_id, None)

    created = []
    ensured = []

    async def fake_create_agent_graph(session_id=None):
        graph = object()
        created.append((session_id, graph))
        return graph

    monkeypatch.setattr(api_module, "create_agent_graph", fake_create_agent_graph)
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

    api_module._agent_graphs_by_thread.pop(thread_id, None)
    manager.remove(thread_id)
