from __future__ import annotations

from scene_agent.session import redis_registry as registry_module
from scene_agent.session.redis_registry import RedisSessionRegistry


class FakeRedisLeaseClient:
    def __init__(self) -> None:
        self._leases: dict[str, str] = {}
        self._ttl_ms: dict[str, int] = {}

    def eval(self, script: str, numkeys: int, *args: str) -> int:
        assert script == registry_module._REFRESH_LEASE_IF_OWNED_LUA
        assert numkeys == 1
        lease_key = args[0]
        expected_token = args[1]
        ttl_ms = int(args[2])

        current = self._leases.get(lease_key)
        if current is None:
            return 0
        if current != expected_token:
            return 0
        self._ttl_ms[lease_key] = ttl_ms
        return 1


def _make_registry(client: FakeRedisLeaseClient) -> RedisSessionRegistry:
    registry = object.__new__(RedisSessionRegistry)
    registry._client = client  # type: ignore[attr-defined]
    registry._prefix = "sa"  # type: ignore[attr-defined]
    return registry


def test_refresh_lease_if_owned_extends_matching_lease() -> None:
    client = FakeRedisLeaseClient()
    registry = _make_registry(client)
    lease_key = registry.session_lease_key("thread-a")
    client._leases[lease_key] = "token-a"

    refreshed = registry.refresh_lease_if_owned(
        thread_id="thread-a",
        lease_token="token-a",
        ttl_seconds=9,
    )

    assert refreshed is True
    assert client._ttl_ms[lease_key] == 9000


def test_refresh_lease_if_owned_rejects_mismatched_token() -> None:
    client = FakeRedisLeaseClient()
    registry = _make_registry(client)
    lease_key = registry.session_lease_key("thread-b")
    client._leases[lease_key] = "token-b"

    refreshed = registry.refresh_lease_if_owned(
        thread_id="thread-b",
        lease_token="wrong-token",
        ttl_seconds=5,
    )

    assert refreshed is False
    assert lease_key not in client._ttl_ms


def test_refresh_lease_if_owned_rejects_missing_lease() -> None:
    client = FakeRedisLeaseClient()
    registry = _make_registry(client)

    refreshed = registry.refresh_lease_if_owned(
        thread_id="thread-c",
        lease_token="token-c",
        ttl_seconds=5,
    )

    assert refreshed is False
