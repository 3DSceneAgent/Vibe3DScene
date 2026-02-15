from __future__ import annotations

import base64
import json
import random
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.memory import InMemorySaver
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


def _encode_typed_value(value: tuple[str, bytes]) -> str:
    payload = {
        "type": value[0],
        "data": base64.b64encode(value[1]).decode("ascii"),
    }
    return json.dumps(payload, ensure_ascii=False)


def _decode_typed_value(raw: str) -> tuple[str, bytes]:
    parsed = json.loads(raw)
    value_type = str(parsed.get("type", "empty"))
    data = str(parsed.get("data", ""))
    return value_type, base64.b64decode(data.encode("ascii")) if data else b""


def _checkpoint_score(checkpoint_id: str) -> float:
    try:
        return float(checkpoint_id.split(".", 1)[0])
    except (TypeError, ValueError):
        return float(int(time.time() * 1000))


class RedisCheckpointer(BaseCheckpointSaver[str]):
    """Redis-backed LangGraph checkpointer."""

    def __init__(self, *, redis_url: str, key_prefix: str) -> None:
        super().__init__()
        if not _REDIS_AVAILABLE:
            raise RuntimeError("redis package is not installed")
        self._client = Redis.from_url(redis_url, decode_responses=True)
        self._prefix = key_prefix.strip() or "sa"

    def _key(self, suffix: str) -> str:
        return f"{self._prefix}:{suffix}"

    def _index_key(self, thread_id: str, checkpoint_ns: str) -> str:
        return self._key(f"ckpt:{thread_id}:{checkpoint_ns}:index")

    def _checkpoint_key(self, thread_id: str, checkpoint_ns: str, checkpoint_id: str) -> str:
        return self._key(f"ckpt:{thread_id}:{checkpoint_ns}:{checkpoint_id}")

    def _blob_key(
        self,
        thread_id: str,
        checkpoint_ns: str,
        channel: str,
        version: str | int | float,
    ) -> str:
        return self._key(f"ckpt_blob:{thread_id}:{checkpoint_ns}:{channel}:{version}")

    def _writes_key(self, thread_id: str, checkpoint_ns: str, checkpoint_id: str) -> str:
        return self._key(f"writes:{thread_id}:{checkpoint_ns}:{checkpoint_id}")

    def _load_blobs(
        self,
        thread_id: str,
        checkpoint_ns: str,
        versions: ChannelVersions,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for channel, version in versions.items():
            raw = self._client.get(self._blob_key(thread_id, checkpoint_ns, channel, version))
            if not raw:
                continue
            typed = _decode_typed_value(raw)
            if typed[0] == "empty":
                continue
            values[channel] = self.serde.loads_typed(typed)
        return values

    def _load_pending_writes(
        self,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
    ) -> list[tuple[str, str, Any]]:
        key = self._writes_key(thread_id, checkpoint_ns, checkpoint_id)
        rows = self._client.hgetall(key)
        unpacked: list[tuple[int, str, str, Any]] = []
        for field, raw in rows.items():
            try:
                parsed = json.loads(raw)
                idx = int(parsed.get("idx", 0))
                task_id = str(parsed.get("task_id", ""))
                channel = str(parsed.get("channel", ""))
                value = self.serde.loads_typed(_decode_typed_value(str(parsed.get("value", ""))))
            except Exception:
                continue
            unpacked.append((idx, task_id, channel, value))
        unpacked.sort(key=lambda item: item[0])
        return [(task_id, channel, value) for _idx, task_id, channel, value in unpacked]

    def _load_checkpoint_tuple(
        self,
        *,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
    ) -> CheckpointTuple | None:
        key = self._checkpoint_key(thread_id, checkpoint_ns, checkpoint_id)
        saved = self._client.hgetall(key)
        if not saved:
            return None

        checkpoint_raw = saved.get("checkpoint")
        metadata_raw = saved.get("metadata")
        if checkpoint_raw is None or metadata_raw is None:
            return None

        checkpoint = self.serde.loads_typed(_decode_typed_value(checkpoint_raw))
        metadata = self.serde.loads_typed(_decode_typed_value(metadata_raw))
        if not isinstance(checkpoint, dict) or not isinstance(metadata, dict):
            return None

        checkpoint = dict(checkpoint)
        checkpoint["channel_values"] = self._load_blobs(
            thread_id,
            checkpoint_ns,
            checkpoint.get("channel_versions", {}),
        )
        parent_checkpoint_id = saved.get("parent_checkpoint_id") or None
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                }
            },
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": parent_checkpoint_id,
                    }
                }
                if parent_checkpoint_id
                else None
            ),
            pending_writes=self._load_pending_writes(
                thread_id,
                checkpoint_ns,
                checkpoint_id,
            ),
        )

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = get_checkpoint_id(config)
        if checkpoint_id:
            return self._load_checkpoint_tuple(
                thread_id=thread_id,
                checkpoint_ns=checkpoint_ns,
                checkpoint_id=checkpoint_id,
            )
        latest = self._client.zrevrange(self._index_key(thread_id, checkpoint_ns), 0, 0)
        if not latest:
            return None
        return self._load_checkpoint_tuple(
            thread_id=thread_id,
            checkpoint_ns=checkpoint_ns,
            checkpoint_id=latest[0],
        )

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        if config is None:
            return iter(())

        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id_filter = get_checkpoint_id(config)
        before_checkpoint_id = get_checkpoint_id(before) if before else None
        checkpoint_ids = self._client.zrevrange(self._index_key(thread_id, checkpoint_ns), 0, -1)
        remaining = limit if limit is not None else None
        for checkpoint_id in checkpoint_ids:
            if checkpoint_id_filter and checkpoint_id != checkpoint_id_filter:
                continue
            if before_checkpoint_id and checkpoint_id >= before_checkpoint_id:
                continue
            item = self._load_checkpoint_tuple(
                thread_id=thread_id,
                checkpoint_ns=checkpoint_ns,
                checkpoint_id=checkpoint_id,
            )
            if item is None:
                continue
            if filter and not all(
                value == item.metadata.get(key)
                for key, value in filter.items()
            ):
                continue
            yield item
            if remaining is not None:
                remaining -= 1
                if remaining <= 0:
                    break

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]
        next_config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }
        saved_checkpoint = checkpoint.copy()
        channel_values = saved_checkpoint.pop("channel_values", {})
        for channel, version in new_versions.items():
            if channel in channel_values:
                typed = self.serde.dumps_typed(channel_values[channel])
            else:
                typed = ("empty", b"")
            self._client.set(
                self._blob_key(thread_id, checkpoint_ns, channel, version),
                _encode_typed_value(typed),
            )

        checkpoint_payload = _encode_typed_value(self.serde.dumps_typed(saved_checkpoint))
        metadata_payload = _encode_typed_value(
            self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))
        )
        self._client.hset(
            self._checkpoint_key(thread_id, checkpoint_ns, checkpoint_id),
            mapping={
                "checkpoint": checkpoint_payload,
                "metadata": metadata_payload,
                "parent_checkpoint_id": config["configurable"].get("checkpoint_id", ""),
            },
        )
        self._client.zadd(
            self._index_key(thread_id, checkpoint_ns),
            {checkpoint_id: _checkpoint_score(checkpoint_id)},
        )
        return next_config

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]
        writes_key = self._writes_key(thread_id, checkpoint_ns, checkpoint_id)
        for idx, (channel, value) in enumerate(writes):
            write_idx = WRITES_IDX_MAP.get(channel, idx)
            field = f"{task_id}|{write_idx}"
            if write_idx >= 0 and self._client.hexists(writes_key, field):
                continue
            payload = {
                "idx": write_idx,
                "task_id": task_id,
                "task_path": task_path,
                "channel": channel,
                "value": _encode_typed_value(self.serde.dumps_typed(value)),
            }
            self._client.hset(writes_key, field, json.dumps(payload, ensure_ascii=False))

    def delete_thread(self, thread_id: str) -> None:
        patterns = [
            self._key(f"ckpt:{thread_id}:*"),
            self._key(f"ckpt_blob:{thread_id}:*"),
            self._key(f"writes:{thread_id}:*"),
        ]
        keys_to_delete: list[str] = []
        for pattern in patterns:
            keys_to_delete.extend(list(self._client.scan_iter(pattern)))
        if keys_to_delete:
            self._client.delete(*keys_to_delete)

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self.get_tuple(config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        for item in self.list(config, filter=filter, before=before, limit=limit):
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self.put_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        self.delete_thread(thread_id)

    def get_next_version(self, current: str | None, channel: None) -> str:
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            current_v = int(str(current).split(".")[0])
        next_v = current_v + 1
        next_h = random.random()
        return f"{next_v:032}.{next_h:016}"


_checkpointer: BaseCheckpointSaver[str] | None = None


def get_graph_checkpointer() -> BaseCheckpointSaver[str]:
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer
    settings = get_settings()
    try:
        checkpointer: BaseCheckpointSaver[str] = RedisCheckpointer(
            redis_url=settings.redis_url,
            key_prefix=settings.redis_key_prefix,
        )
        # Fast fail if Redis is unavailable.
        checkpointer_client = getattr(checkpointer, "_client", None)
        if checkpointer_client is not None:
            checkpointer_client.ping()
        _checkpointer = checkpointer
    except Exception:
        _checkpointer = InMemorySaver()
    return _checkpointer
