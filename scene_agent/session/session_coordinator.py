from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from scene_agent.config import get_settings
from scene_agent.session.redis_registry import RedisError, RedisSessionRegistry


@dataclass
class OwnerResolution:
    thread_id: str
    mode: Literal["owner", "proxy"]
    owner_worker_id: str
    owner_url: str
    lease_epoch: int
    lease_token: str | None
    lease_ttl_ms: int

    @property
    def is_owner(self) -> bool:
        return self.mode == "owner"

    @property
    def is_proxy(self) -> bool:
        return self.mode == "proxy"


@dataclass
class PortReservationResult:
    status: Literal["reserved", "exhausted", "registry_unavailable"]
    port: int | None = None


class SessionCoordinator:
    def __init__(self) -> None:
        settings = get_settings()
        self.worker_id = settings.api_worker_id
        self.owner_url = (
            settings.api_worker_advertise_url
            or f"http://127.0.0.1:{settings.api_port}"
        ).rstrip("/")
        self.lease_ttl_seconds = max(1, settings.session_lease_ttl_seconds)
        self.owner_unreachable_grace_seconds = max(
            0, settings.session_owner_unreachable_grace_seconds
        )
        self._registry: RedisSessionRegistry | None
        try:
            self._registry = RedisSessionRegistry(
                redis_url=settings.redis_url,
                key_prefix=settings.redis_key_prefix,
            )
        except Exception:
            self._registry = None

    @property
    def registry(self) -> RedisSessionRegistry | None:
        return self._registry

    def register_worker(self) -> None:
        if self._registry is None:
            return
        try:
            self._registry.register_worker(self.worker_id)
        except RedisError:
            return

    def unregister_worker(self) -> None:
        if self._registry is None:
            return
        try:
            self._registry.unregister_worker(self.worker_id)
        except RedisError:
            return

    def claim_or_get_owner(self, thread_id: str) -> OwnerResolution:
        if self._registry is None:
            return OwnerResolution(
                thread_id=thread_id,
                mode="owner",
                owner_worker_id=self.worker_id,
                owner_url=self.owner_url,
                lease_epoch=0,
                lease_token=None,
                lease_ttl_ms=0,
            )
        try:
            payload = self._registry.claim_or_get_owner(
                thread_id=thread_id,
                worker_id=self.worker_id,
                owner_url=self.owner_url,
                ttl_seconds=self.lease_ttl_seconds,
            )
        except RedisError:
            return OwnerResolution(
                thread_id=thread_id,
                mode="owner",
                owner_worker_id=self.worker_id,
                owner_url=self.owner_url,
                lease_epoch=0,
                lease_token=None,
                lease_ttl_ms=0,
            )
        return OwnerResolution(
            thread_id=thread_id,
            mode="owner" if payload.get("mode") == "owner" else "proxy",
            owner_worker_id=str(payload.get("owner_worker_id") or ""),
            owner_url=str(payload.get("owner_url") or ""),
            lease_epoch=int(payload.get("lease_epoch") or 0),
            lease_token=(payload.get("lease_token") or None),
            lease_ttl_ms=int(payload.get("lease_ttl_ms") or 0),
        )

    def force_takeover(self, thread_id: str) -> OwnerResolution:
        if self._registry is None:
            return OwnerResolution(
                thread_id=thread_id,
                mode="owner",
                owner_worker_id=self.worker_id,
                owner_url=self.owner_url,
                lease_epoch=0,
                lease_token=None,
                lease_ttl_ms=0,
            )
        try:
            payload = self._registry.force_takeover(
                thread_id=thread_id,
                worker_id=self.worker_id,
                owner_url=self.owner_url,
                ttl_seconds=self.lease_ttl_seconds,
            )
        except RedisError:
            return OwnerResolution(
                thread_id=thread_id,
                mode="owner",
                owner_worker_id=self.worker_id,
                owner_url=self.owner_url,
                lease_epoch=0,
                lease_token=None,
                lease_ttl_ms=0,
            )
        return OwnerResolution(
            thread_id=thread_id,
            mode="owner" if payload.get("mode") == "owner" else "proxy",
            owner_worker_id=str(payload.get("owner_worker_id") or ""),
            owner_url=str(payload.get("owner_url") or ""),
            lease_epoch=int(payload.get("lease_epoch") or 0),
            lease_token=(payload.get("lease_token") or None),
            lease_ttl_ms=int(payload.get("lease_ttl_ms") or 0),
        )

    def release_if_owned(self, thread_id: str, lease_token: str | None) -> bool:
        if self._registry is None or not lease_token:
            return False
        try:
            return self._registry.release_if_owned(
                thread_id=thread_id,
                lease_token=lease_token,
            )
        except RedisError:
            return False

    def refresh_lease_if_owned(self, thread_id: str, lease_token: str | None) -> bool:
        if self._registry is None:
            return True
        if not lease_token:
            return False
        return self._registry.refresh_lease_if_owned(
            thread_id=thread_id,
            lease_token=lease_token,
            ttl_seconds=self.lease_ttl_seconds,
        )

    def touch_activity(self, thread_id: str, lease_epoch: int | None = None) -> None:
        if self._registry is None:
            return
        try:
            self._registry.touch_activity(
                thread_id=thread_id,
                worker_id=self.worker_id,
                owner_url=self.owner_url,
                lease_epoch=lease_epoch,
            )
        except RedisError:
            return

    def update_session_runtime_fields(self, thread_id: str, fields: dict[str, object]) -> None:
        if self._registry is None:
            return
        try:
            self._registry.update_session_runtime_fields(thread_id, fields)
        except RedisError:
            return

    def get_owner(self, thread_id: str) -> OwnerResolution | None:
        if self._registry is None:
            return None
        try:
            payload = self._registry.get_owner(thread_id)
        except RedisError:
            return None
        owner_worker_id = str(payload.get("owner_worker_id") or "")
        owner_url = str(payload.get("owner_url") or "")
        if not owner_worker_id:
            return None
        return OwnerResolution(
            thread_id=thread_id,
            mode="owner" if owner_worker_id == self.worker_id else "proxy",
            owner_worker_id=owner_worker_id,
            owner_url=owner_url,
            lease_epoch=int(payload.get("lease_epoch") or 0),
            lease_token=(payload.get("lease_token") or None),
            lease_ttl_ms=int(payload.get("lease_ttl_ms") or 0),
        )

    def is_owned_by_current_worker(self, thread_id: str) -> bool:
        owner = self.get_owner(thread_id)
        if owner is None:
            # Redis unavailable or no ownership yet: keep single-worker behavior.
            return True
        return owner.owner_worker_id == self.worker_id and owner.lease_ttl_ms > 0

    def list_threads(self, *, limit: int = 500) -> list[str]:
        if self._registry is None:
            return []
        try:
            return self._registry.list_threads(limit=limit)
        except RedisError:
            return []

    def should_attempt_takeover(self, resolution: OwnerResolution) -> bool:
        if resolution.is_owner:
            return False
        if resolution.lease_ttl_ms <= 0:
            return True
        grace_ms = max(0, self.owner_unreachable_grace_seconds) * 1000
        if grace_ms <= 0:
            return True
        return resolution.lease_ttl_ms <= grace_ms

    def reserve_port(
        self,
        *,
        host: str,
        kind: Literal["headless", "mcp"],
        base_port: int,
        range_size: int,
        seed: str,
    ) -> int | None:
        reservation = self.reserve_port_detailed(
            host=host,
            kind=kind,
            base_port=base_port,
            range_size=range_size,
            seed=seed,
        )
        if reservation.status == "reserved":
            return reservation.port
        return None

    def reserve_port_detailed(
        self,
        *,
        host: str,
        kind: Literal["headless", "mcp"],
        base_port: int,
        range_size: int,
        seed: str,
    ) -> PortReservationResult:
        if self._registry is None:
            return PortReservationResult(status="registry_unavailable", port=None)
        try:
            reserved_port = self._registry.reserve_port(
                host=host,
                kind=kind,
                base_port=base_port,
                range_size=range_size,
                seed=seed,
            )
            return PortReservationResult(status="reserved", port=reserved_port)
        except RedisError:
            return PortReservationResult(status="registry_unavailable", port=None)
        except RuntimeError:
            return PortReservationResult(status="exhausted", port=None)

    def release_port(
        self,
        *,
        host: str,
        kind: Literal["headless", "mcp"],
        port: int | None,
    ) -> None:
        if self._registry is None:
            return
        try:
            self._registry.release_port(host=host, kind=kind, port=port)
        except RedisError:
            return

    def get_thread_vlm(self, thread_id: str) -> dict[str, str] | None:
        if self._registry is None:
            return None
        try:
            return self._registry.get_thread_vlm(thread_id)
        except RedisError:
            return None

    def set_thread_vlm(
        self,
        *,
        thread_id: str,
        provider: str,
        model: str,
        locked: bool = False,
    ) -> None:
        if self._registry is None:
            return
        try:
            self._registry.set_thread_vlm(
                thread_id=thread_id,
                provider=provider,
                model=model,
                locked=locked,
            )
        except RedisError:
            return

    def delete_session_metadata(self, thread_id: str) -> None:
        """Remove all Redis keys associated with a session."""
        if self._registry is None:
            return
        try:
            client = self._registry.client
            keys_to_delete = [
                self._registry.session_lease_key(thread_id),
                self._registry.session_meta_key(thread_id),
                self._registry.session_fence_key(thread_id),
                self._registry.session_vlm_key(thread_id),
            ]
            client.delete(*keys_to_delete)
            client.zrem(self._registry.sessions_last_active_key(), thread_id)
            client.srem(
                self._registry.worker_sessions_key(self.worker_id),
                thread_id,
            )
        except (RedisError, Exception):
            return

    def redis_health(self) -> tuple[bool, int | None]:
        if self._registry is None:
            return False, None
        try:
            return self._registry.health()
        except RedisError:
            return False, None

    def worker_count(self) -> int | None:
        if self._registry is None:
            return None
        try:
            return self._registry.worker_count()
        except RedisError:
            return None

    def reserved_port_count(self, *, host: str, kind: Literal["headless", "mcp"]) -> int | None:
        if self._registry is None:
            return None
        try:
            return self._registry.reserved_port_count(host=host, kind=kind)
        except RedisError:
            return None

    def get_session_meta(self, thread_id: str) -> dict[str, str] | None:
        if self._registry is None:
            return None
        try:
            return self._registry.get_session_meta(thread_id)
        except RedisError:
            return None

    def clear_runtime_state(self) -> dict[str, int] | None:
        if self._registry is None:
            return None
        try:
            return self._registry.clear_runtime_state()
        except RedisError:
            return None


_coordinator: SessionCoordinator | None = None


def get_session_coordinator() -> SessionCoordinator:
    global _coordinator
    if _coordinator is None:
        _coordinator = SessionCoordinator()
    return _coordinator
