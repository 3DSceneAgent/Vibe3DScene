from __future__ import annotations

import time
import uuid
from typing import Any

try:
    from redis import Redis
    from redis.exceptions import RedisError
    _REDIS_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency fallback
    Redis = Any  # type: ignore[assignment]
    _REDIS_AVAILABLE = False

    class RedisError(Exception):
        pass


_CLAIM_OR_GET_OWNER_LUA = """
local lease = redis.call('GET', KEYS[1])
local sid = ARGV[1]
local worker_id = ARGV[2]
local owner_url = ARGV[3]
local ttl_ms = tonumber(ARGV[4])
local now_ms = ARGV[5]
local nonce = ARGV[6]
local record_activity = ARGV[7] == '1'

local function parse_lease(value)
    local first = string.find(value, ':')
    if not first then
        return value, ''
    end
    local second = string.find(value, ':', first + 1)
    if not second then
        return string.sub(value, 1, first - 1), string.sub(value, first + 1)
    end
    return string.sub(value, 1, first - 1), string.sub(value, first + 1, second - 1)
end

local function resolve_existing_score()
    local current_score = redis.call('ZSCORE', KEYS[6], sid)
    if current_score and current_score ~= '' then
        return current_score
    end
    local last_active_ms = redis.call('HGET', KEYS[3], 'last_active_ms')
    if last_active_ms and last_active_ms ~= '' then
        return last_active_ms
    end
    local updated_at_ms = redis.call('HGET', KEYS[3], 'updated_at_ms')
    if updated_at_ms and updated_at_ms ~= '' then
        return updated_at_ms
    end
    return '0'
end

if not lease then
    local epoch = redis.call('INCR', KEYS[2])
    local token = worker_id .. ':' .. tostring(epoch) .. ':' .. nonce
    redis.call('PSETEX', KEYS[1], ttl_ms, token)
    if record_activity then
        redis.call('HSET', KEYS[3],
            'owner_worker_id', worker_id,
            'owner_url', owner_url,
            'lease_epoch', tostring(epoch),
            'status', 'active',
            'last_active_ms', now_ms,
            'updated_at_ms', now_ms
        )
        redis.call('ZADD', KEYS[6], now_ms, sid)
    else
        redis.call('HSET', KEYS[3],
            'owner_worker_id', worker_id,
            'owner_url', owner_url,
            'lease_epoch', tostring(epoch),
            'status', 'active'
        )
        redis.call('ZADD', KEYS[6], resolve_existing_score(), sid)
    end
    redis.call('SADD', KEYS[4], sid)
    redis.call('SADD', KEYS[5], worker_id)
    return {'owner', token, tostring(epoch), worker_id, owner_url, tostring(ttl_ms)}
end

local lease_owner, lease_epoch = parse_lease(lease)
if lease_owner == worker_id then
    redis.call('PSETEX', KEYS[1], ttl_ms, lease)
    if record_activity then
        redis.call('HSET', KEYS[3],
            'owner_worker_id', worker_id,
            'owner_url', owner_url,
            'lease_epoch', tostring(lease_epoch),
            'status', 'active',
            'last_active_ms', now_ms,
            'updated_at_ms', now_ms
        )
        redis.call('ZADD', KEYS[6], now_ms, sid)
    else
        redis.call('HSET', KEYS[3],
            'owner_worker_id', worker_id,
            'owner_url', owner_url,
            'lease_epoch', tostring(lease_epoch),
            'status', 'active'
        )
        redis.call('ZADD', KEYS[6], resolve_existing_score(), sid)
    end
    redis.call('SADD', KEYS[4], sid)
    redis.call('SADD', KEYS[5], worker_id)
    return {'owner', lease, tostring(lease_epoch), worker_id, owner_url, tostring(ttl_ms)}
end

local current_owner = redis.call('HGET', KEYS[3], 'owner_worker_id')
if (not current_owner) or current_owner == '' then
    current_owner = lease_owner
end
local current_url = redis.call('HGET', KEYS[3], 'owner_url')
if not current_url then
    current_url = ''
end
local current_epoch = redis.call('HGET', KEYS[3], 'lease_epoch')
if (not current_epoch) or current_epoch == '' then
    current_epoch = tostring(lease_epoch)
end
local ttl = redis.call('PTTL', KEYS[1])
return {'proxy', lease, tostring(current_epoch), current_owner, current_url, tostring(ttl)}
"""

_FORCE_TAKEOVER_LUA = """
local lease = redis.call('GET', KEYS[1])
local sid = ARGV[1]
local worker_id = ARGV[2]
local owner_url = ARGV[3]
local ttl_ms = tonumber(ARGV[4])
local now_ms = ARGV[5]
local nonce = ARGV[6]

local function parse_lease(value)
    local first = string.find(value, ':')
    if not first then
        return value, ''
    end
    local second = string.find(value, ':', first + 1)
    if not second then
        return string.sub(value, 1, first - 1), string.sub(value, first + 1)
    end
    return string.sub(value, 1, first - 1), string.sub(value, first + 1, second - 1)
end

if lease then
    local lease_owner, lease_epoch = parse_lease(lease)
    local current_owner = redis.call('HGET', KEYS[3], 'owner_worker_id')
    if (not current_owner) or current_owner == '' then
        current_owner = lease_owner
    end
    local current_url = redis.call('HGET', KEYS[3], 'owner_url')
    if not current_url then
        current_url = ''
    end
    local current_epoch = redis.call('HGET', KEYS[3], 'lease_epoch')
    if (not current_epoch) or current_epoch == '' then
        current_epoch = tostring(lease_epoch)
    end
    local ttl = redis.call('PTTL', KEYS[1])
    return {'conflict', lease, tostring(current_epoch), current_owner, current_url, tostring(ttl)}
end

local epoch = redis.call('INCR', KEYS[2])
local token = worker_id .. ':' .. tostring(epoch) .. ':' .. nonce
redis.call('PSETEX', KEYS[1], ttl_ms, token)
redis.call('HSET', KEYS[3],
    'owner_worker_id', worker_id,
    'owner_url', owner_url,
    'lease_epoch', tostring(epoch),
    'status', 'active',
    'last_active_ms', now_ms,
    'updated_at_ms', now_ms
)
redis.call('SADD', KEYS[4], sid)
redis.call('SADD', KEYS[5], worker_id)
redis.call('ZADD', KEYS[6], now_ms, sid)
return {'owner', token, tostring(epoch), worker_id, owner_url, tostring(ttl_ms)}
"""

_RELEASE_IF_OWNED_LUA = """
local lease = redis.call('GET', KEYS[1])
if not lease then
    return 0
end
if lease ~= ARGV[1] then
    return 0
end
redis.call('DEL', KEYS[1])
redis.call('HSET', KEYS[2], 'status', 'closed', 'updated_at_ms', ARGV[2])
return 1
"""

_REFRESH_LEASE_IF_OWNED_LUA = """
local lease = redis.call('GET', KEYS[1])
if not lease then
    return 0
end
if lease ~= ARGV[1] then
    return 0
end
local ttl_ms = tonumber(ARGV[2])
if ttl_ms <= 0 then
    return 0
end
redis.call('PEXPIRE', KEYS[1], ttl_ms)
return 1
"""

_RESERVE_PORT_LUA = """
local primary_key = KEYS[1]
local secondary_key = KEYS[2]
local base_port = tonumber(ARGV[1])
local range_size = tonumber(ARGV[2])
local start = tonumber(ARGV[3])

if range_size <= 1 then
    local candidate = base_port
    if redis.call('SISMEMBER', primary_key, candidate) == 1 then
        return -1
    end
    if redis.call('SISMEMBER', secondary_key, candidate) == 1 then
        return -1
    end
    redis.call('SADD', primary_key, candidate)
    return candidate
end

for offset = 0, range_size - 1 do
    local candidate = base_port + ((start + offset) % range_size)
    if redis.call('SISMEMBER', primary_key, candidate) == 0
        and redis.call('SISMEMBER', secondary_key, candidate) == 0 then
        redis.call('SADD', primary_key, candidate)
        return candidate
    end
end

return -1
"""


class RedisSessionRegistry:
    """Low-level Redis operations for multiprocess session coordination."""

    def __init__(self, *, redis_url: str, key_prefix: str) -> None:
        if not _REDIS_AVAILABLE:
            raise RedisError("redis package is not installed")
        self._client = Redis.from_url(redis_url, decode_responses=True)
        self._prefix = key_prefix.strip() or "sa"

    @property
    def client(self) -> Redis:
        return self._client

    def _key(self, suffix: str) -> str:
        return f"{self._prefix}:{suffix}"

    def session_meta_key(self, thread_id: str) -> str:
        return self._key(f"{{session:{thread_id}}}:meta")

    def session_lease_key(self, thread_id: str) -> str:
        return self._key(f"{{session:{thread_id}}}:lease")

    def session_fence_key(self, thread_id: str) -> str:
        return self._key(f"{{session:{thread_id}}}:fence")

    def worker_sessions_key(self, worker_id: str) -> str:
        return self._key(f"worker:{worker_id}:sessions")

    def workers_key(self) -> str:
        return self._key("workers")

    def sessions_last_active_key(self) -> str:
        return self._key("sessions:last_active")

    def session_vlm_key(self, thread_id: str) -> str:
        return self._key(f"thread_vlm:{thread_id}")

    def ports_key(self, host: str, kind: str) -> str:
        return self._key(f"ports:{host}:{kind}")

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _parse_lease_token(token: str | None) -> tuple[str | None, int | None]:
        if not token:
            return None, None
        parts = token.split(":", 2)
        if len(parts) < 2:
            return parts[0], None
        owner = parts[0].strip() or None
        try:
            epoch = int(parts[1])
        except (TypeError, ValueError):
            epoch = None
        return owner, epoch

    @staticmethod
    def _to_int(raw: Any, default: int = 0) -> int:
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    def register_worker(self, worker_id: str) -> None:
        self._client.sadd(self.workers_key(), worker_id)

    def unregister_worker(self, worker_id: str) -> None:
        self._client.srem(self.workers_key(), worker_id)

    def worker_count(self) -> int:
        return int(self._client.scard(self.workers_key()))

    def reserved_port_count(self, *, host: str, kind: str) -> int:
        return int(self._client.scard(self.ports_key(host, kind)))

    def clear_runtime_state(self) -> dict[str, int]:
        keys_to_delete: set[str] = set()

        for key in (self.workers_key(), self.sessions_last_active_key()):
            keys_to_delete.add(key)

        patterns = (
            self._key("worker:*:sessions"),
            self._key("{session:*}:meta"),
            self._key("{session:*}:lease"),
            self._key("{session:*}:fence"),
            self._key("ports:*"),
            self._key("thread_vlm:*"),
        )
        for pattern in patterns:
            for key in self._client.scan_iter(match=pattern, count=500):
                if isinstance(key, str) and key:
                    keys_to_delete.add(key)

        deleted = 0
        if keys_to_delete:
            deleted = int(self._client.delete(*sorted(keys_to_delete)))

        return {
            "deleted_keys": deleted,
            "matched_patterns": len(patterns),
        }

    def claim_or_get_owner(
        self,
        *,
        thread_id: str,
        worker_id: str,
        owner_url: str,
        ttl_seconds: int,
        record_activity: bool = True,
    ) -> dict[str, Any]:
        ttl_ms = max(1, ttl_seconds) * 1000
        now_ms = str(self._now_ms())
        nonce = uuid.uuid4().hex
        result = self._client.eval(
            _CLAIM_OR_GET_OWNER_LUA,
            6,
            self.session_lease_key(thread_id),
            self.session_fence_key(thread_id),
            self.session_meta_key(thread_id),
            self.worker_sessions_key(worker_id),
            self.workers_key(),
            self.sessions_last_active_key(),
            thread_id,
            worker_id,
            owner_url,
            str(ttl_ms),
            now_ms,
            nonce,
            "1" if record_activity else "0",
        )
        rows = list(result) if isinstance(result, (list, tuple)) else []
        mode = str(rows[0]) if rows else "proxy"
        lease_token = str(rows[1]) if len(rows) > 1 else ""
        lease_epoch = self._to_int(rows[2] if len(rows) > 2 else 0)
        resolved_worker = str(rows[3]) if len(rows) > 3 else ""
        resolved_url = str(rows[4]) if len(rows) > 4 else ""
        lease_ttl_ms = self._to_int(rows[5] if len(rows) > 5 else 0, default=0)
        return {
            "mode": "owner" if mode == "owner" else "proxy",
            "lease_token": lease_token,
            "lease_epoch": lease_epoch,
            "owner_worker_id": resolved_worker,
            "owner_url": resolved_url,
            "lease_ttl_ms": lease_ttl_ms,
        }

    def force_takeover(
        self,
        *,
        thread_id: str,
        worker_id: str,
        owner_url: str,
        ttl_seconds: int,
    ) -> dict[str, Any]:
        ttl_ms = max(1, ttl_seconds) * 1000
        now_ms = str(self._now_ms())
        nonce = uuid.uuid4().hex
        result = self._client.eval(
            _FORCE_TAKEOVER_LUA,
            6,
            self.session_lease_key(thread_id),
            self.session_fence_key(thread_id),
            self.session_meta_key(thread_id),
            self.worker_sessions_key(worker_id),
            self.workers_key(),
            self.sessions_last_active_key(),
            thread_id,
            worker_id,
            owner_url,
            str(ttl_ms),
            now_ms,
            nonce,
        )
        rows = list(result) if isinstance(result, (list, tuple)) else []
        mode = str(rows[0]) if rows else "conflict"
        lease_token = str(rows[1]) if len(rows) > 1 else ""
        lease_epoch = self._to_int(rows[2] if len(rows) > 2 else 0)
        resolved_worker = str(rows[3]) if len(rows) > 3 else ""
        resolved_url = str(rows[4]) if len(rows) > 4 else ""
        lease_ttl_ms = self._to_int(rows[5] if len(rows) > 5 else 0, default=0)
        return {
            "mode": "owner" if mode == "owner" else "conflict",
            "lease_token": lease_token,
            "lease_epoch": lease_epoch,
            "owner_worker_id": resolved_worker,
            "owner_url": resolved_url,
            "lease_ttl_ms": lease_ttl_ms,
        }

    def release_if_owned(self, *, thread_id: str, lease_token: str) -> bool:
        result = self._client.eval(
            _RELEASE_IF_OWNED_LUA,
            2,
            self.session_lease_key(thread_id),
            self.session_meta_key(thread_id),
            lease_token,
            str(self._now_ms()),
        )
        return bool(int(result))

    def refresh_lease_if_owned(
        self,
        *,
        thread_id: str,
        lease_token: str,
        ttl_seconds: int,
    ) -> bool:
        ttl_ms = max(1, int(ttl_seconds)) * 1000
        result = self._client.eval(
            _REFRESH_LEASE_IF_OWNED_LUA,
            1,
            self.session_lease_key(thread_id),
            lease_token,
            str(ttl_ms),
        )
        return bool(int(result))

    def touch_activity(
        self,
        *,
        thread_id: str,
        worker_id: str,
        owner_url: str,
        lease_epoch: int | None = None,
    ) -> None:
        now_ms = str(self._now_ms())
        mapping: dict[str, str] = {
            "owner_worker_id": worker_id,
            "owner_url": owner_url,
            "status": "active",
            "last_active_ms": now_ms,
            "updated_at_ms": now_ms,
        }
        if lease_epoch is not None:
            mapping["lease_epoch"] = str(lease_epoch)
        self._client.hset(self.session_meta_key(thread_id), mapping=mapping)
        self._client.zadd(self.sessions_last_active_key(), {thread_id: int(now_ms)})
        self._client.sadd(self.worker_sessions_key(worker_id), thread_id)
        self._client.sadd(self.workers_key(), worker_id)

    def update_session_runtime_fields(
        self,
        thread_id: str,
        fields: dict[str, Any],
        *,
        bump_updated_at: bool = True,
    ) -> None:
        if not fields:
            return
        payload = {str(k): str(v) for k, v in fields.items() if v is not None}
        if bump_updated_at:
            payload["updated_at_ms"] = str(self._now_ms())
        if payload:
            self._client.hset(self.session_meta_key(thread_id), mapping=payload)

    def get_session_meta(self, thread_id: str) -> dict[str, str]:
        return dict(self._client.hgetall(self.session_meta_key(thread_id)))

    def get_owner(self, thread_id: str) -> dict[str, Any]:
        lease_token = self._client.get(self.session_lease_key(thread_id))
        lease_owner, lease_epoch = self._parse_lease_token(lease_token)
        meta = self.get_session_meta(thread_id)
        owner_worker = meta.get("owner_worker_id") or lease_owner
        owner_url = meta.get("owner_url") or ""
        resolved_epoch = self._to_int(meta.get("lease_epoch"), default=lease_epoch or 0)
        return {
            "owner_worker_id": owner_worker,
            "owner_url": owner_url,
            "lease_epoch": resolved_epoch,
            "lease_token": lease_token,
            "lease_ttl_ms": self._to_int(self._client.pttl(self.session_lease_key(thread_id)), default=-2),
        }

    def list_threads(self, *, limit: int = 500) -> list[str]:
        size = max(1, limit)
        entries = self._client.zrevrange(self.sessions_last_active_key(), 0, size - 1)
        return [entry for entry in entries if isinstance(entry, str) and entry]

    def reserve_port(
        self,
        *,
        host: str,
        kind: str,
        base_port: int,
        range_size: int,
        seed: str,
    ) -> int:
        primary_key = self.ports_key(host, kind)
        secondary_kind = "mcp" if kind == "headless" else "headless"
        secondary_key = self.ports_key(host, secondary_kind)
        normalized_range = max(1, int(range_size))
        start = abs(hash(seed)) % normalized_range
        result = self._client.eval(
            _RESERVE_PORT_LUA,
            2,
            primary_key,
            secondary_key,
            str(int(base_port)),
            str(normalized_range),
            str(start),
        )
        candidate = self._to_int(result, default=-1)
        if candidate >= 0:
            return candidate
        raise RuntimeError(f"No available {kind} port in configured range for host {host}.")

    def release_port(self, *, host: str, kind: str, port: int | None) -> None:
        if port is None:
            return
        self._client.srem(self.ports_key(host, kind), int(port))

    def set_thread_vlm(self, *, thread_id: str, provider: str, model: str, locked: bool = False) -> None:
        self._client.hset(
            self.session_vlm_key(thread_id),
            mapping={
                "provider": provider,
                "model": model,
                "locked": "1" if locked else "0",
            },
        )

    def get_thread_vlm(self, thread_id: str) -> dict[str, str] | None:
        value = self._client.hgetall(self.session_vlm_key(thread_id))
        if not value:
            return None
        provider = value.get("provider")
        model = value.get("model")
        if not provider or not model:
            return None
        return {
            "provider": provider,
            "model": model,
            "locked": value.get("locked", "0"),
        }

    def delete_thread_vlm(self, thread_id: str) -> None:
        self._client.delete(self.session_vlm_key(thread_id))

    def health(self) -> tuple[bool, int | None]:
        start = time.perf_counter()
        try:
            self._client.ping()
        except RedisError:
            return False, None
        latency_ms = int((time.perf_counter() - start) * 1000)
        return True, latency_ms
