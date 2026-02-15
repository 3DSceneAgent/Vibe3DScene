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
    """Redis-backed metadata store for reference images."""

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
        self._fallback_images: dict[str, list[dict[str, Any]]] = {}
        if not hasattr(self, "_fallback_mode"):
            self._fallback_mode = False

    def _key(self, suffix: str) -> str:
        return f"{self._prefix}:{suffix}"

    def _order_key(self, thread_id: str) -> str:
        return self._key(f"ref:{thread_id}:order")

    def _meta_key(self, thread_id: str, image_id: str) -> str:
        return self._key(f"ref:{thread_id}:meta:{image_id}")

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

    def list_images(self, thread_id: str) -> list[dict[str, Any]]:
        def _run(client: Redis | None) -> list[dict[str, Any]]:
            if client is None:
                with self._fallback_lock:
                    return [dict(item) for item in self._fallback_images.get(thread_id, [])]
            image_ids = client.zrange(self._order_key(thread_id), 0, -1)
            images: list[dict[str, Any]] = []
            for image_id in image_ids:
                meta = client.hgetall(self._meta_key(thread_id, image_id))
                if not meta:
                    continue
                images.append(dict(meta))
            return images

        return self._with_fallback(_run)

    def add_images(self, *, thread_id: str, images: list[dict[str, Any]]) -> None:
        if not images:
            return

        def _run(client: Redis | None) -> None:
            if client is None:
                with self._fallback_lock:
                    existing = list(self._fallback_images.get(thread_id, []))
                    self._fallback_images[thread_id] = existing + [dict(item) for item in images]
                return
            pipe = client.pipeline()
            for index, image in enumerate(images):
                image_id = str(image.get("id", "")).strip()
                if not image_id:
                    continue
                key = self._meta_key(thread_id, image_id)
                score = self._score() + index
                payload = {str(k): str(v) for k, v in image.items()}
                pipe.hset(key, mapping=payload)
                pipe.zadd(self._order_key(thread_id), {image_id: score})
            pipe.execute()

        self._with_fallback(_run)

    def clear_thread(self, thread_id: str) -> None:
        def _run(client: Redis | None) -> None:
            if client is None:
                with self._fallback_lock:
                    self._fallback_images.pop(thread_id, None)
                return
            image_ids = client.zrange(self._order_key(thread_id), 0, -1)
            keys = [self._meta_key(thread_id, image_id) for image_id in image_ids]
            if keys:
                client.delete(*keys)
            client.delete(self._order_key(thread_id))

        self._with_fallback(_run)

    def clear_all(self) -> None:
        def _run(client: Redis | None) -> None:
            if client is None:
                with self._fallback_lock:
                    self._fallback_images.clear()
                return
            keys = list(client.scan_iter(self._key("ref:*")))
            if keys:
                client.delete(*keys)

        self._with_fallback(_run)


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
