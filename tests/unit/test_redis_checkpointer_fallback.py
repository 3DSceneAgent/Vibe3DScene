from langgraph.checkpoint.memory import InMemorySaver

from scene_agent.agent import redis_checkpointer as checkpointer_module
from scene_agent.config import reload_settings


def test_get_graph_checkpointer_falls_back_without_redis(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "invalid://redis-url")
    reload_settings()
    checkpointer_module._checkpointer = None

    checkpointer = checkpointer_module.get_graph_checkpointer()
    assert isinstance(checkpointer, InMemorySaver)
