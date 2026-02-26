from __future__ import annotations

import threading
import time
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

from scene_agent.config import get_settings


class ReferenceImageStore:
    """Redis-backed metadata store for image assets and task bindings."""

    def __init__(self, *, redis_url: str, key_prefix: str) -> None:
        if _REDIS_AVAILABLE:
            try:
                self._client = Redis.from_url(redis_url, decode_responses=True)
            except Exception:
                self._client = None
                self._fallback_mode = True
        else:
            self._client = None
            self._fallback_mode = True
        self._prefix = key_prefix.strip() or "sa"
        self._fallback_lock = threading.Lock()
        self._fallback_assets: dict[str, list[dict[str, Any]]] = {}
        self._fallback_bindings: dict[str, dict[str, list[dict[str, Any]]]] = {}
        if not hasattr(self, "_fallback_mode"):
            self._fallback_mode = False

    def _key(self, suffix: str) -> str:
        return f"{self._prefix}:{suffix}"

    def _asset_order_key(self, thread_id: str) -> str:
        return self._key(f"img:{thread_id}:order")

    def _asset_meta_key(self, thread_id: str, asset_id: str) -> str:
        return self._key(f"img:{thread_id}:meta:{asset_id}")

    def _binding_order_key(self, thread_id: str, task_id: str) -> str:
        return self._key(f"imgbind:{thread_id}:{task_id}:order")

    def _binding_meta_key(self, thread_id: str, task_id: str, binding_id: str) -> str:
        return self._key(f"imgbind:{thread_id}:{task_id}:meta:{binding_id}")

    @staticmethod
    def _score() -> int:
        return int(time.time() * 1000)

    def _with_fallback(self, fn):
        if self._fallback_mode:
            return fn(None)
        try:
            return fn(self._client)
        except RedisError:
            self._fallback_mode = True
            return fn(None)

    def list_assets(self, thread_id: str) -> list[dict[str, Any]]:
        def _run(client: Redis | None) -> list[dict[str, Any]]:
            if client is None:
                with self._fallback_lock:
                    return [dict(item) for item in self._fallback_assets.get(thread_id, [])]

            asset_ids = client.zrange(self._asset_order_key(thread_id), 0, -1)
            assets: list[dict[str, Any]] = []
            for asset_id in asset_ids:
                meta = client.hgetall(self._asset_meta_key(thread_id, asset_id))
                if not meta:
                    continue
                assets.append(dict(meta))
            return assets

        return self._with_fallback(_run)

    def add_assets(self, *, thread_id: str, assets: list[dict[str, Any]]) -> None:
        if not assets:
            return

        def _run(client: Redis | None) -> None:
            if client is None:
                with self._fallback_lock:
                    existing = list(self._fallback_assets.get(thread_id, []))
                    existing_by_id = {
                        str(item.get("id", "")).strip(): dict(item)
                        for item in existing
                        if isinstance(item, dict)
                    }
                    for asset in assets:
                        asset_id = str(asset.get("id", "")).strip()
                        if not asset_id:
                            continue
                        existing_by_id[asset_id] = dict(asset)
                    ordered = [
                        existing_by_id[str(item.get("id", "")).strip()]
                        for item in existing
                        if isinstance(item, dict)
                        and str(item.get("id", "")).strip() in existing_by_id
                    ]
                    seen_ids = {
                        str(item.get("id", "")).strip()
                        for item in ordered
                        if isinstance(item, dict)
                    }
                    for asset in assets:
                        asset_id = str(asset.get("id", "")).strip()
                        if not asset_id or asset_id in seen_ids:
                            continue
                        seen_ids.add(asset_id)
                        ordered.append(dict(asset))
                    self._fallback_assets[thread_id] = ordered
                return

            pipe = client.pipeline()
            for index, asset in enumerate(assets):
                asset_id = str(asset.get("id", "")).strip()
                if not asset_id:
                    continue
                key = self._asset_meta_key(thread_id, asset_id)
                score = self._score() + index
                payload = {str(k): str(v) for k, v in asset.items()}
                pipe.hset(key, mapping=payload)
                pipe.zadd(self._asset_order_key(thread_id), {asset_id: score})
            pipe.execute()

        self._with_fallback(_run)

    def list_bindings(self, thread_id: str, task_id: str | None = None) -> list[dict[str, Any]]:
        def _run(client: Redis | None) -> list[dict[str, Any]]:
            if client is None:
                with self._fallback_lock:
                    thread_bindings = self._fallback_bindings.get(thread_id, {})
                    if task_id is not None:
                        return [dict(item) for item in thread_bindings.get(task_id, [])]
                    merged: list[dict[str, Any]] = []
                    for rows in thread_bindings.values():
                        merged.extend(dict(item) for item in rows)
                    return merged

            task_ids: list[str]
            if task_id is not None:
                task_ids = [task_id]
            else:
                task_ids = []
                prefix = self._key(f"imgbind:{thread_id}:")
                for raw_key in client.scan_iter(self._key(f"imgbind:{thread_id}:*:order")):
                    key = str(raw_key)
                    if not key.startswith(prefix) or not key.endswith(":order"):
                        continue
                    candidate = key[len(prefix) : -len(":order")]
                    if candidate:
                        task_ids.append(candidate)

            bindings: list[dict[str, Any]] = []
            for current_task in task_ids:
                binding_ids = client.zrange(self._binding_order_key(thread_id, current_task), 0, -1)
                for binding_id in binding_ids:
                    meta = client.hgetall(self._binding_meta_key(thread_id, current_task, binding_id))
                    if not meta:
                        continue
                    bindings.append(dict(meta))
            return bindings

        return self._with_fallback(_run)

    def upsert_bindings(
        self,
        *,
        thread_id: str,
        task_id: str,
        bindings: list[dict[str, Any]],
    ) -> None:
        if not bindings:
            return

        def _run(client: Redis | None) -> None:
            if client is None:
                with self._fallback_lock:
                    thread_bindings = self._fallback_bindings.setdefault(thread_id, {})
                    existing = list(thread_bindings.get(task_id, []))
                    existing_by_id = {
                        str(item.get("id", "")).strip(): dict(item)
                        for item in existing
                        if isinstance(item, dict)
                    }
                    for binding in bindings:
                        binding_id = str(binding.get("id", "")).strip()
                        if not binding_id:
                            continue
                        existing_by_id[binding_id] = dict(binding)
                    ordered: list[dict[str, Any]] = []
                    seen: set[str] = set()
                    for item in existing:
                        if not isinstance(item, dict):
                            continue
                        binding_id = str(item.get("id", "")).strip()
                        if not binding_id or binding_id not in existing_by_id or binding_id in seen:
                            continue
                        seen.add(binding_id)
                        ordered.append(existing_by_id[binding_id])
                    for binding in bindings:
                        binding_id = str(binding.get("id", "")).strip()
                        if not binding_id or binding_id in seen:
                            continue
                        seen.add(binding_id)
                        ordered.append(dict(binding))
                    thread_bindings[task_id] = ordered
                return

            pipe = client.pipeline()
            for index, binding in enumerate(bindings):
                binding_id = str(binding.get("id", "")).strip()
                if not binding_id:
                    continue
                key = self._binding_meta_key(thread_id, task_id, binding_id)
                score = self._score() + index
                payload = {str(k): str(v) for k, v in binding.items()}
                pipe.hset(key, mapping=payload)
                pipe.zadd(self._binding_order_key(thread_id, task_id), {binding_id: score})
            pipe.execute()

        self._with_fallback(_run)

    def clear_thread(self, thread_id: str) -> None:
        def _run(client: Redis | None) -> None:
            if client is None:
                with self._fallback_lock:
                    self._fallback_assets.pop(thread_id, None)
                    self._fallback_bindings.pop(thread_id, None)
                return

            asset_ids = client.zrange(self._asset_order_key(thread_id), 0, -1)
            asset_keys = [self._asset_meta_key(thread_id, asset_id) for asset_id in asset_ids]
            if asset_keys:
                client.delete(*asset_keys)
            client.delete(self._asset_order_key(thread_id))

            binding_keys = list(client.scan_iter(self._key(f"imgbind:{thread_id}:*")))
            if binding_keys:
                client.delete(*binding_keys)

        self._with_fallback(_run)

    def clear_all(self) -> None:
        def _run(client: Redis | None) -> None:
            if client is None:
                with self._fallback_lock:
                    self._fallback_assets.clear()
                    self._fallback_bindings.clear()
                return

            keys = list(client.scan_iter(self._key("img:*")))
            keys.extend(list(client.scan_iter(self._key("imgbind:*"))))
            if keys:
                client.delete(*keys)

        self._with_fallback(_run)

    # ---------------------------------------------------------------------
    # Legacy wrapper methods (reference-images)
    # ---------------------------------------------------------------------
    def list_images(self, thread_id: str) -> list[dict[str, Any]]:
        return self.list_assets(thread_id)

    def add_images(self, *, thread_id: str, images: list[dict[str, Any]]) -> None:
        self.add_assets(thread_id=thread_id, assets=images)


_reference_image_store: ReferenceImageStore | None = None


def get_reference_image_store() -> ReferenceImageStore:
    global _reference_image_store
    if _reference_image_store is None:
        settings = get_settings()
        _reference_image_store = ReferenceImageStore(
            redis_url=settings.redis_url,
            key_prefix=settings.redis_key_prefix,
        )
    return _reference_image_store


def get_image_asset_store() -> ReferenceImageStore:
    return get_reference_image_store()
