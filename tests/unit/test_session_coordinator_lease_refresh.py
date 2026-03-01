import pytest

from scene_agent.session import session_coordinator as coordinator_module


def _make_coordinator(registry):
    coordinator = object.__new__(coordinator_module.SessionCoordinator)
    coordinator._registry = registry
    coordinator.lease_ttl_seconds = 20
    return coordinator


def test_refresh_lease_if_owned_returns_true_when_registry_unavailable() -> None:
    coordinator = _make_coordinator(None)
    assert coordinator.refresh_lease_if_owned("thread-a", "token-a") is True


def test_refresh_lease_if_owned_returns_false_when_token_is_missing() -> None:
    class RegistryStub:
        def refresh_lease_if_owned(self, **_kwargs):
            raise AssertionError("should not be called")

    coordinator = _make_coordinator(RegistryStub())
    assert coordinator.refresh_lease_if_owned("thread-b", None) is False


def test_refresh_lease_if_owned_delegates_to_registry() -> None:
    calls: list[tuple[str, str, int]] = []

    class RegistryStub:
        def refresh_lease_if_owned(self, *, thread_id: str, lease_token: str, ttl_seconds: int) -> bool:
            calls.append((thread_id, lease_token, ttl_seconds))
            return True

    coordinator = _make_coordinator(RegistryStub())
    assert coordinator.refresh_lease_if_owned("thread-c", "token-c") is True
    assert calls == [("thread-c", "token-c", 20)]


def test_refresh_lease_if_owned_propagates_redis_errors() -> None:
    class RegistryStub:
        def refresh_lease_if_owned(self, **_kwargs):
            raise coordinator_module.RedisError("redis unavailable")

    coordinator = _make_coordinator(RegistryStub())
    with pytest.raises(coordinator_module.RedisError):
        coordinator.refresh_lease_if_owned("thread-d", "token-d")
