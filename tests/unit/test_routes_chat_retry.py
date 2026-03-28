import asyncio
import json

import pytest
from fastapi import HTTPException
from langgraph.checkpoint.base import CheckpointTuple

from scene_agent.interfaces.api import routes_chat


def test_build_human_message_uses_turn_id() -> None:
    request = routes_chat.ChatRequest(
        message="retry this",
        thread_id="thread-1",
        turn_id="turn-1",
    )

    message = routes_chat._build_human_message(request)

    assert message.content == "retry this"
    assert message.id == "turn-1"


def test_capture_latest_turn_retry_state_persists_metadata(tmp_path, monkeypatch) -> None:
    checkpoint_tuple = CheckpointTuple(
        config={"configurable": {"thread_id": "thread-1", "checkpoint_id": "ckpt-1"}},
        checkpoint={"id": "ckpt-1", "channel_versions": {}},
        metadata={"source": "input"},
        parent_config=None,
        pending_writes=None,
    )
    blender_calls: list[tuple[str, dict[str, object] | None, str | None]] = []

    async def fake_get_checkpoint_tuple(**_kwargs):
        return checkpoint_tuple

    def fake_send_blender_command_sync(command_type, params=None, thread_id=None):
        blender_calls.append((command_type, params, thread_id))
        return {"ok": True}

    monkeypatch.setattr(routes_chat, "_get_checkpoint_tuple", fake_get_checkpoint_tuple)
    monkeypatch.setattr(routes_chat, "resolve_thread_storage_dir", lambda thread_id: tmp_path / thread_id)
    monkeypatch.setattr(routes_chat, "send_blender_command_sync", fake_send_blender_command_sync)

    request = routes_chat.ChatRequest(
        message="build a chair",
        thread_id="thread-1",
        turn_id="turn-5",
        attached_image_ids=["img-1", "", "img-2"],
        workflow_topology="single",
    )

    asyncio.run(
        routes_chat._capture_latest_turn_retry_state(
            request=request,
            request_id="req-1",
        )
    )

    metadata_path = tmp_path / "thread-1" / "retry" / "latest_turn.json"
    assert metadata_path.exists()
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["turn_id"] == "turn-5"
    assert metadata["message"] == "build a chair"
    assert metadata["attached_image_ids"] == ["img-1", "img-2"]
    assert metadata["pre_turn_checkpoint_id"] == "ckpt-1"
    assert metadata["workflow_topology"] == "single"
    assert blender_calls == [
        (
            "save_blend",
            {
                "filepath": str(tmp_path / "thread-1" / "retry" / "latest_turn_pre.blend"),
                "copy": True,
            },
            "thread-1",
        )
    ]


def test_restore_latest_turn_retry_state_rebuilds_checkpoint_and_scene(tmp_path, monkeypatch) -> None:
    retry_dir = tmp_path / "thread-1" / "retry"
    retry_dir.mkdir(parents=True)
    snapshot_path = retry_dir / "latest_turn_pre.blend"
    snapshot_path.write_text("blend", encoding="utf-8")
    (retry_dir / "latest_turn.json").write_text(
        json.dumps(
            {
                "turn_id": "turn-5",
                "message": "build a chair",
                "attached_image_ids": ["img-1"],
                "pre_turn_checkpoint_id": "ckpt-4",
                "pre_turn_checkpoint_ns": "",
                "pre_turn_blend_snapshot": str(snapshot_path),
            }
        ),
        encoding="utf-8",
    )

    checkpoint_tuple = CheckpointTuple(
        config={
            "configurable": {
                "thread_id": "thread-1",
                "checkpoint_id": "ckpt-4",
                "checkpoint_ns": "",
            }
        },
        checkpoint={"id": "ckpt-4", "channel_versions": {"messages": "2"}},
        metadata={"source": "input"},
        parent_config=None,
        pending_writes=None,
    )
    blender_calls: list[tuple[str, dict[str, object] | None, str | None]] = []

    class FakeCheckpointer:
        def __init__(self) -> None:
            self.deleted: list[str] = []
            self.put_calls: list[tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]] = []

        def delete_thread(self, thread_id: str) -> None:
            self.deleted.append(thread_id)

        def put(self, config, checkpoint, metadata, new_versions):
            self.put_calls.append((config, checkpoint, metadata, new_versions))
            return config

    fake_checkpointer = FakeCheckpointer()

    async def fake_get_checkpoint_tuple(**kwargs):
        assert kwargs["checkpoint_id"] == "ckpt-4"
        return checkpoint_tuple

    def fake_send_blender_command_sync(command_type, params=None, thread_id=None):
        blender_calls.append((command_type, params, thread_id))
        return {"ok": True}

    monkeypatch.setattr(routes_chat, "_get_checkpoint_tuple", fake_get_checkpoint_tuple)
    monkeypatch.setattr(routes_chat, "resolve_thread_storage_dir", lambda thread_id: tmp_path / thread_id)
    monkeypatch.setattr(routes_chat, "send_blender_command_sync", fake_send_blender_command_sync)
    monkeypatch.setattr(routes_chat, "get_graph_checkpointer", lambda: fake_checkpointer)

    metadata = asyncio.run(
        routes_chat._restore_latest_turn_retry_state(
            thread_id="thread-1",
            retry_turn_id="turn-5",
            request_id="req-restore",
        )
    )

    assert metadata["turn_id"] == "turn-5"
    assert blender_calls == [
        ("load_blend", {"filepath": str(snapshot_path)}, "thread-1"),
    ]
    assert fake_checkpointer.deleted == ["thread-1"]
    assert fake_checkpointer.put_calls == [
        (
            {"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}},
            checkpoint_tuple.checkpoint,
            checkpoint_tuple.metadata,
            checkpoint_tuple.checkpoint["channel_versions"],
        )
    ]


def test_restore_latest_turn_retry_state_rejects_non_latest_turn(tmp_path, monkeypatch) -> None:
    retry_dir = tmp_path / "thread-1" / "retry"
    retry_dir.mkdir(parents=True)
    snapshot_path = retry_dir / "latest_turn_pre.blend"
    snapshot_path.write_text("blend", encoding="utf-8")
    (retry_dir / "latest_turn.json").write_text(
        json.dumps(
            {
                "turn_id": "turn-5",
                "message": "build a chair",
                "pre_turn_checkpoint_id": None,
                "pre_turn_checkpoint_ns": "",
                "pre_turn_blend_snapshot": str(snapshot_path),
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(routes_chat, "resolve_thread_storage_dir", lambda thread_id: tmp_path / thread_id)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            routes_chat._restore_latest_turn_retry_state(
                thread_id="thread-1",
                retry_turn_id="turn-4",
                request_id="req-mismatch",
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Only the latest turn can be retried."
