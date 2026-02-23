from __future__ import annotations

import fnmatch

import pytest

from scene_agent.session import redis_registry as registry_module
from scene_agent.session.redis_registry import RedisSessionRegistry


class FakeRedisPortClient:
    def __init__(self) -> None:
        self._sets: dict[str, set[int | str]] = {}

    @staticmethod
    def _member(value: int | str) -> int | str:
        try:
            return int(value)
        except (TypeError, ValueError):
            return str(value)

    def _bucket(self, key: str) -> set[int | str]:
        return self._sets.setdefault(key, set())

    def sadd(self, key: str, value: int | str) -> int:
        member = self._member(value)
        bucket = self._bucket(key)
        before = len(bucket)
        bucket.add(member)
        return 1 if len(bucket) > before else 0

    def srem(self, key: str, value: int | str) -> int:
        member = self._member(value)
        bucket = self._bucket(key)
        if member in bucket:
            bucket.remove(member)
            return 1
        return 0

    def sismember(self, key: str, value: int | str) -> int:
        member = self._member(value)
        return 1 if member in self._bucket(key) else 0

    def scan_iter(self, match: str, count: int = 500):  # noqa: ARG002
        for key in sorted(self._sets):
            if fnmatch.fnmatch(key, match):
                yield key

    def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self._sets:
                del self._sets[key]
                deleted += 1
        return deleted

    def eval(self, script: str, numkeys: int, *args: str) -> int:
        assert script == registry_module._RESERVE_PORT_LUA
        assert numkeys == 2
        primary_key, secondary_key = args[0], args[1]
        base_port = int(args[2])
        range_size = int(args[3])
        start = int(args[4])

        if range_size <= 1:
            candidate = base_port
            if self.sismember(secondary_key, candidate) == 1:
                return -1
            self.sadd(primary_key, candidate)
            return candidate

        for offset in range(range_size):
            candidate = base_port + ((start + offset) % range_size)
            if self.sismember(primary_key, candidate) == 0 and self.sismember(secondary_key, candidate) == 0:
                self.sadd(primary_key, candidate)
                return candidate
        return -1


def _make_registry(client: FakeRedisPortClient) -> RedisSessionRegistry:
    registry = object.__new__(RedisSessionRegistry)
    registry._client = client  # type: ignore[attr-defined]
    registry._prefix = "sa"  # type: ignore[attr-defined]
    return registry


def test_reserve_port_avoids_cross_kind_collisions() -> None:
    client = FakeRedisPortClient()
    registry = _make_registry(client)

    mcp_port = registry.reserve_port(
        host="localhost",
        kind="mcp",
        base_port=9900,
        range_size=8,
        seed="thread-1",
    )
    headless_port = registry.reserve_port(
        host="localhost",
        kind="headless",
        base_port=9900,
        range_size=8,
        seed="thread-1",
    )

    assert headless_port != mcp_port


def test_reserve_port_errors_when_only_port_is_taken_by_other_kind() -> None:
    client = FakeRedisPortClient()
    registry = _make_registry(client)

    registry.reserve_port(
        host="localhost",
        kind="mcp",
        base_port=9977,
        range_size=1,
        seed="thread-1",
    )

    with pytest.raises(RuntimeError, match="No available headless port"):
        registry.reserve_port(
            host="localhost",
            kind="headless",
            base_port=9977,
            range_size=1,
            seed="thread-2",
        )


def test_release_port_only_clears_requested_kind() -> None:
    client = FakeRedisPortClient()
    registry = _make_registry(client)

    mcp_port = registry.reserve_port(
        host="localhost",
        kind="mcp",
        base_port=9960,
        range_size=4,
        seed="thread-1",
    )
    headless_port = registry.reserve_port(
        host="localhost",
        kind="headless",
        base_port=9960,
        range_size=4,
        seed="thread-2",
    )
    registry.release_port(host="localhost", kind="headless", port=headless_port)

    assert client.sismember(registry.ports_key("localhost", "headless"), headless_port) == 0
    assert client.sismember(registry.ports_key("localhost", "mcp"), mcp_port) == 1


def test_clear_runtime_state_removes_stale_runtime_keys() -> None:
    client = FakeRedisPortClient()
    registry = _make_registry(client)

    client.sadd(registry.workers_key(), "worker-a")
    client.sadd(registry.worker_sessions_key("worker-a"), "thread-a")
    client.sadd(registry.ports_key("localhost", "headless"), 9876)
    client.sadd(registry.session_meta_key("thread-a"), "placeholder")
    client.sadd(registry.session_lease_key("thread-a"), "placeholder")
    client.sadd(registry.session_fence_key("thread-a"), "placeholder")
    client.sadd(registry.session_vlm_key("thread-a"), "placeholder")

    summary = registry.clear_runtime_state()

    assert summary["deleted_keys"] >= 6
    assert registry.workers_key() not in client._sets
    assert registry.worker_sessions_key("worker-a") not in client._sets
    assert registry.ports_key("localhost", "headless") not in client._sets
    assert registry.session_meta_key("thread-a") not in client._sets
    assert registry.session_vlm_key("thread-a") not in client._sets
