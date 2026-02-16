from scene_agent.config import reload_settings
from scene_agent.session import session_coordinator as coordinator_module
from scene_agent.session.session_coordinator import OwnerResolution


def _make_proxy_resolution(ttl_ms: int) -> OwnerResolution:
    return OwnerResolution(
        thread_id="thread-grace-test",
        mode="proxy",
        owner_worker_id="worker-other",
        owner_url="http://127.0.0.1:18002",
        lease_epoch=1,
        lease_token="worker-other:1:nonce",
        lease_ttl_ms=ttl_ms,
    )


def test_should_attempt_takeover_when_ttl_expired(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "invalid://redis-url")
    monkeypatch.setenv("SESSION_OWNER_UNREACHABLE_GRACE_SECONDS", "10")
    reload_settings()
    coordinator_module._coordinator = None

    coordinator = coordinator_module.get_session_coordinator()
    assert coordinator.should_attempt_takeover(_make_proxy_resolution(ttl_ms=0)) is True


def test_should_attempt_takeover_inside_grace_window(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "invalid://redis-url")
    monkeypatch.setenv("SESSION_OWNER_UNREACHABLE_GRACE_SECONDS", "10")
    reload_settings()
    coordinator_module._coordinator = None

    coordinator = coordinator_module.get_session_coordinator()
    assert coordinator.should_attempt_takeover(_make_proxy_resolution(ttl_ms=9_000)) is True
    assert coordinator.should_attempt_takeover(_make_proxy_resolution(ttl_ms=15_000)) is False


def test_should_attempt_takeover_immediately_when_grace_disabled(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "invalid://redis-url")
    monkeypatch.setenv("SESSION_OWNER_UNREACHABLE_GRACE_SECONDS", "0")
    reload_settings()
    coordinator_module._coordinator = None

    coordinator = coordinator_module.get_session_coordinator()
    assert coordinator.should_attempt_takeover(_make_proxy_resolution(ttl_ms=19_000)) is True
