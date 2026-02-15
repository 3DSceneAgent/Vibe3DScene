from scene_agent.config import reload_settings
from scene_agent.session import session_coordinator as coordinator_module


def test_session_coordinator_falls_back_to_local_owner(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "invalid://redis-url")
    reload_settings()
    coordinator_module._coordinator = None

    coordinator = coordinator_module.get_session_coordinator()
    resolution = coordinator.claim_or_get_owner("thread-fallback-1")

    assert resolution.is_owner
    assert resolution.owner_worker_id == coordinator.worker_id
    assert coordinator.list_threads() == []
