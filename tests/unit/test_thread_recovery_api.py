from __future__ import annotations

import json
from pathlib import Path
import re
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from scene_agent.interfaces import api as api_module
from scene_agent.interfaces.api import routes_scene as api_routes_scene
from scene_agent.interfaces.api import routes_runtime as api_routes_runtime
from scene_agent.interfaces.api import shared as api_shared
from scene_agent.memory.reference_image_memory import ImageAsset


def test_build_thread_history_payload_serializes_attached_images_tool_media_and_references(
    tmp_path,
    monkeypatch,
):
    thread_id = "thread-history"
    image_path = tmp_path / "chair.png"
    image_path.write_bytes(b"png")
    image_asset = ImageAsset(
        id="img-1",
        thread_id=thread_id,
        filename="chair.png",
        content_type="image/png",
        size_bytes=123,
        sha256="abc123",
        stored_path=str(image_path),
        uploaded_at="2026-03-28T00:00:00",
        source="upload",
    )

    class _Checkpointer:
        @staticmethod
        def get_tuple(_config):
            return SimpleNamespace(
                checkpoint={
                    "id": "1770000000123.0001",
                    "channel_values": {
                        "messages": [
                            HumanMessage(
                                content=(
                                    "Create a chair\n\n"
                                    "<referenced_scene_objects>\n"
                                    "- display_name: Chair\n"
                                    "  backend_object_id: obj-chair\n"
                                    "  frontend_object_type: MESH\n"
                                    "</referenced_scene_objects>\n"
                                ),
                                id="turn-1",
                                additional_kwargs={
                                    "created_at_ms": 1770000000001,
                                    "attached_image_ids": ["img-1"],
                                    "referenced_objects": [
                                        {
                                            "backend_object_id": "obj-chair",
                                            "display_name": "Chair",
                                            "object_type": "MESH",
                                        }
                                    ],
                                },
                            ),
                            {
                                "type": "ai",
                                "id": "assistant-1",
                                "content": "Done.",
                                "additional_kwargs": {},
                            },
                            {
                                "type": "tool",
                                "id": "tool-1",
                                "name": "render_preview",
                                "content": {"image_url": {"url": "/renders/preview.jpg"}},
                            },
                            {
                                "type": "ai",
                                "id": api_shared.SCENE_OBSERVE_MESSAGE_ID,
                                "content": "internal",
                            },
                        ],
                        "todos": [
                            {
                                "id": "todo-1",
                                "description": "Create a chair",
                                "status": "completed",
                            }
                        ],
                    },
                }
            )

    class _ImageMemory:
        @staticmethod
        def list_assets(_thread_id):
            return [image_asset]

    class _Coordinator:
        @staticmethod
        def get_session_meta(_thread_id):
            return {"title": "Chair thread"}

    monkeypatch.setattr(api_shared, "get_graph_checkpointer", lambda: _Checkpointer())
    monkeypatch.setattr(api_shared, "get_image_asset_memory", lambda: _ImageMemory())
    monkeypatch.setattr(api_shared, "get_session_coordinator", lambda: _Coordinator())

    payload = api_shared.build_thread_history_payload(thread_id)

    assert payload.thread_id == thread_id
    assert payload.title == "Chair thread"
    assert payload.scene_revision is None
    assert [message.role for message in payload.messages] == ["user", "assistant", "tool"]
    assert payload.messages[0].content == "Create a chair"
    assert payload.messages[0].attached_images[0].asset_url == f"/threads/{thread_id}/images/img-1"
    assert payload.messages[0].referenced_objects[0].backend_object_id == "obj-chair"
    assert payload.messages[0].referenced_objects[0].display_name == "Chair"
    assert payload.messages[2].tool_media[0].value == "/renders/preview.jpg"
    assert payload.todos[0]["id"] == "todo-1"


def test_build_thread_history_payload_strips_referenced_objects_context_from_title(monkeypatch):
    thread_id = "thread-history-title"

    class _Checkpointer:
        @staticmethod
        def get_tuple(_config):
            return SimpleNamespace(
                checkpoint={
                    "channel_values": {
                        "messages": [
                            HumanMessage(
                                content=(
                                    "Hi\n\n"
                                    "<reference_scene_objects>\n"
                                    "- display_name: Lamp\n"
                                    "  backend_object_id: obj-lamp\n"
                                    "</reference_scene_objects>\n"
                                ),
                                id="turn-1",
                                additional_kwargs={
                                    "created_at_ms": 1770000000123,
                                    "referenced_objects": [
                                        {
                                            "backend_object_id": "obj-lamp",
                                            "display_name": "Lamp",
                                        }
                                    ],
                                },
                            ),
                        ],
                    },
                }
            )

    class _ImageMemory:
        @staticmethod
        def list_assets(_thread_id):
            return []

    class _Coordinator:
        @staticmethod
        def get_session_meta(_thread_id):
            return {}

    monkeypatch.setattr(api_shared, "get_graph_checkpointer", lambda: _Checkpointer())
    monkeypatch.setattr(api_shared, "get_image_asset_memory", lambda: _ImageMemory())
    monkeypatch.setattr(api_shared, "get_session_coordinator", lambda: _Coordinator())

    payload = api_shared.build_thread_history_payload(thread_id)

    assert payload.title == "Hi"
    assert payload.messages[0].content == "Hi"


def test_build_thread_history_payload_omits_asset_url_for_missing_files(monkeypatch):
    thread_id = "thread-history-missing"
    image_asset = ImageAsset(
        id="img-missing",
        thread_id=thread_id,
        filename="dog2.jpg",
        content_type="image/jpeg",
        size_bytes=123,
        sha256="abc123",
        stored_path="/tmp/definitely-missing-dog2.jpg",
        uploaded_at="2026-03-28T00:00:00",
        source="upload",
    )

    class _Checkpointer:
        @staticmethod
        def get_tuple(_config):
            return SimpleNamespace(
                checkpoint={
                    "id": "1770000000123.0001",
                    "channel_values": {
                        "messages": [
                            HumanMessage(
                                content="Use this dog reference",
                                id="turn-1",
                                additional_kwargs={
                                    "created_at_ms": 1770000000001,
                                    "attached_image_ids": ["img-missing"],
                                },
                            ),
                        ],
                    },
                }
            )

    class _ImageMemory:
        @staticmethod
        def list_assets(_thread_id):
            return [image_asset]

    class _Coordinator:
        @staticmethod
        def get_session_meta(_thread_id):
            return {"title": "Dog thread"}

    monkeypatch.setattr(api_shared, "get_graph_checkpointer", lambda: _Checkpointer())
    monkeypatch.setattr(api_shared, "get_image_asset_memory", lambda: _ImageMemory())
    monkeypatch.setattr(api_shared, "get_session_coordinator", lambda: _Coordinator())

    payload = api_shared.build_thread_history_payload(thread_id)

    assert payload.messages[0].attached_images[0].filename == "dog2.jpg"
    assert payload.messages[0].attached_images[0].asset_url is None


def test_build_thread_history_payload_uses_message_timestamp_when_checkpoint_time_is_missing(monkeypatch):
    thread_id = "thread-history-message-ts"

    class _Checkpointer:
        @staticmethod
        def get_tuple(_config):
            return SimpleNamespace(
                checkpoint={
                    "channel_values": {
                        "messages": [
                            HumanMessage(
                                content="Open the shutters",
                                id="turn-1",
                                additional_kwargs={"created_at_ms": 1770000000123},
                            ),
                        ],
                    },
                }
            )

    class _ImageMemory:
        @staticmethod
        def list_assets(_thread_id):
            return []

    class _Coordinator:
        @staticmethod
        def get_session_meta(_thread_id):
            return {}

    monkeypatch.setattr(api_shared, "get_graph_checkpointer", lambda: _Checkpointer())
    monkeypatch.setattr(api_shared, "get_image_asset_memory", lambda: _ImageMemory())
    monkeypatch.setattr(api_shared, "get_session_coordinator", lambda: _Coordinator())

    payload = api_shared.build_thread_history_payload(thread_id)

    assert payload.updated_at_ms == 1770000000123


def test_build_thread_history_payload_aggregates_telemetry_metrics(monkeypatch):
    thread_id = "thread-history-metrics"

    class _Checkpointer:
        @staticmethod
        def get_tuple(_config):
            return SimpleNamespace(
                checkpoint={
                    "id": "1770000000123.0001",
                    "channel_values": {
                        "messages": [
                            HumanMessage(
                                content="Build a chair",
                                id="turn-1",
                                additional_kwargs={"created_at_ms": 1770000000001},
                            ),
                            {
                                "type": "ai",
                                "id": "assistant-1",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "name": "get_scene_info",
                                        "args": {},
                                        "id": "tool-1",
                                        "type": "tool_call",
                                    }
                                ],
                            },
                            HumanMessage(
                                content="Now add a lamp",
                                id="turn-2",
                                additional_kwargs={"created_at_ms": 1770000001001},
                            ),
                        ],
                        "todos": [
                            {
                                "id": "todo-1",
                                "description": "Place the chair",
                                "status": "completed",
                                "created_at": "2026-01-01T00:00:00",
                                "completed_at": "2026-01-01T00:01:00",
                            },
                            {
                                "id": "todo-2",
                                "description": "Add the lamp",
                                "status": "pending",
                                "created_at": "2026-01-01T00:02:00",
                                "completed_at": None,
                            },
                        ],
                        "active_todo_id": "todo-2",
                        "llm_call_records": [
                            {
                                "call_id": "call-1",
                                "thread_id": thread_id,
                                "turn_id": "turn-1",
                                "node_name": "router",
                                "call_role": "router",
                                "provider": "gemini",
                                "model": "gemini-2.5-flash",
                                "input_tokens": 120,
                                "output_tokens": 20,
                                "total_tokens": 140,
                                "image_input_tokens": 40,
                                "has_image_inputs": True,
                                "context_limit_tokens": 1048576,
                                "created_at_ms": 1770000000100,
                            },
                            {
                                "call_id": "call-2",
                                "thread_id": thread_id,
                                "turn_id": "turn-1",
                                "node_name": "agent",
                                "call_role": "general",
                                "provider": "gemini",
                                "model": "gemini-2.5-flash",
                                "input_tokens": 80,
                                "output_tokens": 10,
                                "total_tokens": 90,
                                "image_input_tokens": 0,
                                "has_image_inputs": False,
                                "context_limit_tokens": 1048576,
                                "created_at_ms": 1770000000200,
                            },
                        ],
                    },
                }
            )

    class _ImageMemory:
        @staticmethod
        def list_assets(_thread_id):
            return []

    class _Coordinator:
        @staticmethod
        def get_session_meta(_thread_id):
            return {"title": "Metrics thread"}

    monkeypatch.setattr(api_shared, "get_graph_checkpointer", lambda: _Checkpointer())
    monkeypatch.setattr(api_shared, "get_image_asset_memory", lambda: _ImageMemory())
    monkeypatch.setattr(api_shared, "get_session_coordinator", lambda: _Coordinator())

    payload = api_shared.build_thread_history_payload(thread_id)

    assert payload.thread_metrics.input_tokens == 200
    assert payload.thread_metrics.output_tokens == 30
    assert payload.thread_metrics.total_tokens == 230
    assert payload.thread_metrics.image_input_tokens == 40
    assert payload.thread_metrics.tool_call_count == 1
    assert payload.thread_metrics.peak_context_used_tokens == 120
    assert payload.thread_metrics.peak_context_limit_tokens == 1048576
    assert payload.turn_metrics_by_turn_id["turn-1"].input_tokens == 200
    assert payload.turn_metrics_by_turn_id["turn-1"].tool_call_count == 1
    assert payload.active_todo_id == "todo-2"


def test_load_thread_scene_artifact_manifest_reads_persisted_artifacts(tmp_path, monkeypatch):
    thread_id = "thread-artifacts"
    storage_dir = tmp_path / thread_id
    artifacts_dir = storage_dir / "artifacts"
    renders_dir = artifacts_dir / "renders"
    renders_dir.mkdir(parents=True)
    (storage_dir / "scene.blend").write_bytes(b"blend")
    (artifacts_dir / "latest.glb").write_bytes(b"glb")
    manifest_path = artifacts_dir / "scene_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "thread_id": thread_id,
                "scene_revision": 1770000000555,
                "generated_at_ms": 1770000000666,
                "gltf_url": f"/threads/{thread_id}/scene-artifacts/latest.glb",
                "renders": [
                    {
                        "camera_name": "SceneCamera_NE",
                        "image_url": f"/threads/{thread_id}/scene-artifacts/renders/scene_ne.jpg",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        api_shared,
        "get_settings",
        lambda: SimpleNamespace(session_shared_storage_root=str(tmp_path)),
    )

    manifest = api_shared.load_thread_scene_artifact_manifest(thread_id)

    assert manifest.has_persisted_blend is True
    assert manifest.scene_revision == 1770000000555
    assert manifest.gltf_url == f"/threads/{thread_id}/scene-artifacts/latest.glb?rev=1770000000555"
    assert manifest.renders[0].camera_name == "SceneCamera_NE"


def test_export_scene_gltf_to_path_ensures_metadata_and_extras(tmp_path, monkeypatch):
    thread_id = "thread-export"
    target_path = tmp_path / "artifacts" / "latest.glb"
    command_calls: list[tuple[str, dict[str, object] | None, str | None, bool]] = []

    def _fake_send_blender_command_sync(
        command_type: str,
        params=None,
        thread_id_arg=None,
        *,
        preserve_activity: bool = False,
    ):
        command_calls.append((command_type, params, thread_id_arg, preserve_activity))
        if command_type == "execute_code":
            code = str((params or {}).get("code") or "")
            match = re.search(r'filepath=r"(.*?)"', code)
            assert match is not None
            export_path = Path(match.group(1))
            export_path.parent.mkdir(parents=True, exist_ok=True)
            export_path.write_bytes(b"glb")
        return {}

    monkeypatch.setattr(api_shared, "send_blender_command_sync", _fake_send_blender_command_sync)

    assert api_shared._export_scene_gltf_to_path(thread_id, target_path) is True
    assert command_calls[0] == ("ensure_scene_agent_object_metadata", {}, thread_id, True)
    assert command_calls[1][0] == "execute_code"
    assert "export_extras=True" in str(command_calls[1][1]["code"])
    assert command_calls[1][1]["validate_scene"] is False
    assert target_path.read_bytes() == b"glb"


def test_thread_history_route_returns_ui_ready_payload(monkeypatch):
    payload = api_shared.ThreadHistoryResponse(
        thread_id="thread-route",
        title="Route thread",
        updated_at_ms=1770000000000,
        scene_revision=1770000000111,
        messages=[],
        todos=[],
    )

    monkeypatch.setattr(api_shared, "ensure_frontend_client_can_manage_thread", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api_module, "ensure_frontend_client_can_manage_thread", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api_routes_runtime, "ensure_frontend_client_can_manage_thread", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api_shared, "build_thread_history_payload", lambda _thread_id: payload)
    monkeypatch.setattr(api_module, "build_thread_history_payload", lambda _thread_id: payload)
    monkeypatch.setattr(api_routes_runtime, "build_thread_history_payload", lambda _thread_id: payload)

    with TestClient(api_module.app) as client:
        response = client.get(
            "/threads/thread-route/history",
            headers={"X-Frontend-Client-Id": "client-a"},
        )

    assert response.status_code == 200
    assert response.json()["title"] == "Route thread"


def test_list_threads_route_returns_backend_summaries(monkeypatch):
    summaries = [
        api_shared.ThreadSummaryResponse(
            thread_id="thread-summary",
            title="Summary thread",
            updated_at_ms=1770000000999,
            has_persisted_scene=True,
            scene_revision=1770000000111,
            has_runtime=False,
        )
    ]

    monkeypatch.setattr(api_shared, "build_thread_summaries", lambda frontend_client_id: summaries)
    monkeypatch.setattr(api_module, "build_thread_summaries", lambda frontend_client_id: summaries)
    monkeypatch.setattr(api_routes_runtime, "build_thread_summaries", lambda frontend_client_id: summaries)

    with TestClient(api_module.app) as client:
        response = client.get(
            "/threads",
            headers={"X-Frontend-Client-Id": "client-a"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["threads"] == ["thread-summary"]
    assert payload["summaries"][0]["has_persisted_scene"] is True


def test_build_thread_summaries_uses_latest_scene_or_chat_activity(monkeypatch):
    monkeypatch.setattr(
        api_shared,
        "collect_headless_runtime_entries",
        lambda frontend_client_id: [
            {
                "thread_id": "thread-scene",
                "occupying_resources": False,
            }
        ],
    )
    monkeypatch.setattr(
        api_shared,
        "list_accessible_thread_ids",
        lambda frontend_client_id: ["thread-chat", "thread-scene"],
    )
    monkeypatch.setattr(
        api_shared,
        "build_thread_history_payload",
        lambda thread_id: api_shared.ThreadHistoryResponse(
            thread_id=thread_id,
            title=f"title-{thread_id}",
            updated_at_ms=1770000000100 if thread_id == "thread-chat" else 1770000000200,
            scene_revision=None,
            messages=[],
            todos=[],
        ),
    )
    monkeypatch.setattr(
        api_shared,
        "load_thread_scene_artifact_manifest",
        lambda thread_id: api_shared.SceneArtifactManifestResponse(
            thread_id=thread_id,
            has_persisted_blend=thread_id == "thread-scene",
            scene_revision=1770000000999 if thread_id == "thread-scene" else None,
            generated_at_ms=1770000000300 if thread_id == "thread-scene" else None,
            gltf_url=None,
            renders=[],
        ),
    )

    summaries = api_shared.build_thread_summaries(frontend_client_id="client-a")

    assert [summary.thread_id for summary in summaries] == ["thread-scene", "thread-chat"]
    assert summaries[0].updated_at_ms == 1770000000300
    assert summaries[1].updated_at_ms == 1770000000100


def test_delete_all_threads_route_tears_down_each_accessible_thread(monkeypatch):
    deleted_thread_ids: list[str] = []

    monkeypatch.setattr(
        api_shared,
        "list_accessible_thread_ids",
        lambda frontend_client_id: ["thread-a", "thread-b"],
    )
    monkeypatch.setattr(
        api_module,
        "list_accessible_thread_ids",
        lambda frontend_client_id: ["thread-a", "thread-b"],
    )
    monkeypatch.setattr(
        api_routes_runtime,
        "list_accessible_thread_ids",
        lambda frontend_client_id: ["thread-a", "thread-b"],
    )

    def _fake_teardown(thread_id: str):
        deleted_thread_ids.append(thread_id)
        return {"thread_id": thread_id, "cleaned": ["graph_checkpoints"]}

    monkeypatch.setattr(api_shared, "teardown_thread_session", _fake_teardown)
    monkeypatch.setattr(api_module, "teardown_thread_session", _fake_teardown)
    monkeypatch.setattr(api_routes_runtime, "teardown_thread_session", _fake_teardown)

    with TestClient(api_module.app) as client:
        response = client.delete(
            "/threads",
            headers={"X-Frontend-Client-Id": "client-a"},
        )

    assert response.status_code == 200
    assert deleted_thread_ids == ["thread-a", "thread-b"]
    assert response.json() == {
        "deleted_thread_ids": ["thread-a", "thread-b"],
        "failed_thread_ids": [],
    }


def test_thread_artifact_file_routes_serve_persisted_outputs(tmp_path, monkeypatch):
    thread_id = "thread-files"
    storage_dir = tmp_path / thread_id / "artifacts" / "renders"
    storage_dir.mkdir(parents=True)
    gltf_path = tmp_path / thread_id / "artifacts" / "latest.glb"
    gltf_path.write_bytes(b"glb")
    render_path = storage_dir / "render.jpg"
    render_path.write_bytes(b"jpg")

    monkeypatch.setattr(
        api_shared,
        "get_settings",
        lambda: SimpleNamespace(session_shared_storage_root=str(tmp_path)),
    )

    with TestClient(api_module.app) as client:
        gltf_response = client.get(f"/threads/{thread_id}/scene-artifacts/latest.glb")
        render_response = client.get(f"/threads/{thread_id}/scene-artifacts/renders/render.jpg")

    assert gltf_response.status_code == 200
    assert gltf_response.content == b"glb"
    assert render_response.status_code == 200
    assert render_response.content == b"jpg"


def test_delete_scene_object_route_refreshes_artifacts(monkeypatch):
    persisted_calls: list[tuple[str, float]] = []
    blender_calls: list[tuple[str, dict[str, object] | None, str | None]] = []

    async def fake_claim_or_proxy_request(*, request, thread_id):  # type: ignore[no-untyped-def]
        return None, None

    class _Manager:
        @staticmethod
        def ensure(thread_id, mode):
            assert thread_id == "thread-delete"
            assert mode == "headless"
            return SimpleNamespace()

        @staticmethod
        def persist_session_blend(thread_id, min_interval_seconds=8.0):
            persisted_calls.append((thread_id, float(min_interval_seconds)))

    def _fake_send_blender_command_sync(command_type, params=None, thread_id=None):
        blender_calls.append((command_type, params, thread_id))
        if command_type == "resolve_scene_agent_object":
            return {
                "found": True,
                "backend_object_id": "obj-123",
                "backend_object_name": "Cube",
                "object_name": "Cube",
                "object_type": "MESH",
            }
        if command_type == "delete_objects":
            return {"deleted": ["Cube", "CubeChild"]}
        raise AssertionError(f"Unexpected Blender command: {command_type}")

    monkeypatch.setattr(api_routes_scene, "claim_or_proxy_request", fake_claim_or_proxy_request)
    monkeypatch.setattr(api_routes_scene, "get_settings", lambda: SimpleNamespace(blender_mode="headless"))
    monkeypatch.setattr(api_routes_scene, "get_session_manager", lambda: _Manager())
    monkeypatch.setattr(api_routes_scene, "send_blender_command_sync", _fake_send_blender_command_sync)
    monkeypatch.setattr(
        api_routes_scene,
        "persist_thread_scene_artifacts_sync",
        lambda thread_id: api_shared.SceneArtifactManifestResponse(
            thread_id=thread_id,
            has_persisted_blend=True,
            scene_revision=1770000000777,
            generated_at_ms=1770000000888,
            gltf_url=f"/threads/{thread_id}/scene-artifacts/latest.glb?rev=1770000000777",
            renders=[],
        ),
    )

    with TestClient(api_module.app) as client:
        response = client.post(
            "/scene/thread-delete/objects/delete",
            json={
                "backend_object_id": "obj-123",
                "backend_object_name": "Cube",
                "mode": "cascade",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "thread_id": "thread-delete",
        "backend_object_id": "obj-123",
        "backend_object_name": "Cube",
        "deleted_names": ["Cube", "CubeChild"],
        "scene_revision": 1770000000777,
        "manifest_generated_at_ms": 1770000000888,
        "has_persisted_blend": True,
    }
    assert persisted_calls == [("thread-delete", 0.0)]
    assert blender_calls[0][0] == "resolve_scene_agent_object"
    assert blender_calls[1][0] == "delete_objects"


def test_delete_scene_object_route_rejects_local_client(monkeypatch):
    async def fake_claim_or_proxy_request(*, request, thread_id):  # type: ignore[no-untyped-def]
        return None, None

    monkeypatch.setattr(api_routes_scene, "claim_or_proxy_request", fake_claim_or_proxy_request)
    monkeypatch.setattr(api_routes_scene, "get_settings", lambda: SimpleNamespace(blender_mode="local-client"))

    with TestClient(api_module.app) as client:
        response = client.post(
            "/scene/thread-delete-local/objects/delete",
            json={
                "backend_object_id": "obj-123",
                "backend_object_name": "Cube",
                "mode": "cascade",
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "Hierarchy delete is only available in headless mode."


def test_transform_scene_object_route_refreshes_artifacts(monkeypatch):
    persisted_calls: list[tuple[str, float]] = []
    blender_calls: list[tuple[str, dict[str, object] | None, str | None]] = []

    async def fake_claim_or_proxy_request(*, request, thread_id):  # type: ignore[no-untyped-def]
        return None, None

    class _Manager:
        @staticmethod
        def ensure(thread_id, mode):
            assert thread_id == "thread-transform"
            assert mode == "headless"
            return SimpleNamespace()

        @staticmethod
        def persist_session_blend(thread_id, min_interval_seconds=8.0):
            persisted_calls.append((thread_id, float(min_interval_seconds)))

    def _fake_send_blender_command_sync(command_type, params=None, thread_id=None):
        blender_calls.append((command_type, params, thread_id))
        if command_type == "resolve_scene_agent_object":
            return {
                "found": True,
                "backend_object_id": "obj-123",
                "backend_object_name": "Cube",
                "object_name": "Cube",
                "object_type": "MESH",
            }
        if command_type == "set_object_transform":
            assert params["world_matrix"] == [
                [1.0, 0.0, 0.0, 2.5],
                [0.0, 1.0, 0.0, 1.25],
                [0.0, 0.0, 1.0, -3.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
            return {
                "success": True,
                "backend_object_id": "obj-123",
                "backend_object_name": "Cube",
                "object_name": "Cube",
                "object_type": "MESH",
                "world_location": [2.5, 1.25, -3.0],
                "world_rotation_quaternion": [1.0, 0.0, 0.0, 0.0],
                "world_scale": [1.0, 1.0, 1.0],
            }
        raise AssertionError(f"Unexpected Blender command: {command_type}")

    monkeypatch.setattr(api_routes_scene, "claim_or_proxy_request", fake_claim_or_proxy_request)
    monkeypatch.setattr(api_routes_scene, "get_settings", lambda: SimpleNamespace(blender_mode="headless"))
    monkeypatch.setattr(api_routes_scene, "get_session_manager", lambda: _Manager())
    monkeypatch.setattr(api_routes_scene, "send_blender_command_sync", _fake_send_blender_command_sync)
    monkeypatch.setattr(
        api_routes_scene,
        "persist_thread_scene_artifacts_sync",
        lambda thread_id: api_shared.SceneArtifactManifestResponse(
            thread_id=thread_id,
            has_persisted_blend=True,
            scene_revision=1770000000999,
            generated_at_ms=1770000001111,
            gltf_url=f"/threads/{thread_id}/scene-artifacts/latest.glb?rev=1770000000999",
            renders=[],
        ),
    )

    with TestClient(api_module.app) as client:
        response = client.post(
            "/scene/thread-transform/objects/transform",
            json={
                "backend_object_id": "obj-123",
                "backend_object_name": "Cube",
                "world_matrix": [
                    [1.0, 0.0, 0.0, 2.5],
                    [0.0, 1.0, 0.0, 1.25],
                    [0.0, 0.0, 1.0, -3.0],
                    [0.0, 0.0, 0.0, 1.0],
                ],
                "refresh_artifacts": True,
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "thread_id": "thread-transform",
        "backend_object_id": "obj-123",
        "backend_object_name": "Cube",
        "object_name": "Cube",
        "object_type": "MESH",
        "world_location": [2.5, 1.25, -3.0],
        "world_rotation_quaternion": [1.0, 0.0, 0.0, 0.0],
        "world_scale": [1.0, 1.0, 1.0],
        "scene_revision": 1770000000999,
        "manifest_generated_at_ms": 1770000001111,
        "has_persisted_blend": True,
        "artifacts_refreshed": True,
    }
    assert persisted_calls == [("thread-transform", 0.0)]
    assert blender_calls[0][0] == "resolve_scene_agent_object"
    assert blender_calls[1][0] == "set_object_transform"


def test_transform_scene_object_route_supports_live_sync_without_artifact_refresh(monkeypatch):
    persisted_calls: list[tuple[str, float]] = []
    blender_calls: list[tuple[str, dict[str, object] | None, str | None]] = []

    async def fake_claim_or_proxy_request(*, request, thread_id):  # type: ignore[no-untyped-def]
        return None, None

    class _Manager:
        @staticmethod
        def ensure(thread_id, mode):
            assert thread_id == "thread-transform-live"
            assert mode == "headless"
            return SimpleNamespace()

        @staticmethod
        def persist_session_blend(thread_id, min_interval_seconds=8.0):
            persisted_calls.append((thread_id, float(min_interval_seconds)))

    def _fake_send_blender_command_sync(command_type, params=None, thread_id=None):
        blender_calls.append((command_type, params, thread_id))
        if command_type == "resolve_scene_agent_object":
            return {
                "found": True,
                "backend_object_id": "obj-456",
                "backend_object_name": "Lamp",
                "object_name": "Lamp",
                "object_type": "LIGHT",
            }
        if command_type == "set_object_transform":
            return {
                "success": True,
                "backend_object_id": "obj-456",
                "backend_object_name": "Lamp",
                "object_name": "Lamp",
                "object_type": "LIGHT",
                "world_location": [0.0, 2.0, 4.0],
                "world_rotation_quaternion": [1.0, 0.0, 0.0, 0.0],
                "world_scale": [1.0, 1.0, 1.0],
            }
        raise AssertionError(f"Unexpected Blender command: {command_type}")

    monkeypatch.setattr(api_routes_scene, "claim_or_proxy_request", fake_claim_or_proxy_request)
    monkeypatch.setattr(api_routes_scene, "get_settings", lambda: SimpleNamespace(blender_mode="headless"))
    monkeypatch.setattr(api_routes_scene, "get_session_manager", lambda: _Manager())
    monkeypatch.setattr(api_routes_scene, "send_blender_command_sync", _fake_send_blender_command_sync)
    monkeypatch.setattr(
        api_routes_scene,
        "persist_thread_scene_artifacts_sync",
        lambda thread_id: (_ for _ in ()).throw(AssertionError(f"Artifacts should not refresh for {thread_id}")),
    )

    with TestClient(api_module.app) as client:
        response = client.post(
            "/scene/thread-transform-live/objects/transform",
            json={
                "backend_object_id": "obj-456",
                "backend_object_name": "Lamp",
                "world_matrix": [
                    [1.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 2.0],
                    [0.0, 0.0, 1.0, 4.0],
                    [0.0, 0.0, 0.0, 1.0],
                ],
                "refresh_artifacts": False,
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "thread_id": "thread-transform-live",
        "backend_object_id": "obj-456",
        "backend_object_name": "Lamp",
        "object_name": "Lamp",
        "object_type": "LIGHT",
        "world_location": [0.0, 2.0, 4.0],
        "world_rotation_quaternion": [1.0, 0.0, 0.0, 0.0],
        "world_scale": [1.0, 1.0, 1.0],
        "scene_revision": None,
        "manifest_generated_at_ms": None,
        "has_persisted_blend": False,
        "artifacts_refreshed": False,
    }
    assert persisted_calls == []
    assert blender_calls[0][0] == "resolve_scene_agent_object"
    assert blender_calls[1][0] == "set_object_transform"


def test_transform_scene_object_route_rejects_local_client(monkeypatch):
    async def fake_claim_or_proxy_request(*, request, thread_id):  # type: ignore[no-untyped-def]
        return None, None

    monkeypatch.setattr(api_routes_scene, "claim_or_proxy_request", fake_claim_or_proxy_request)
    monkeypatch.setattr(api_routes_scene, "get_settings", lambda: SimpleNamespace(blender_mode="local-client"))

    with TestClient(api_module.app) as client:
        response = client.post(
            "/scene/thread-transform-local/objects/transform",
            json={
                "backend_object_id": "obj-123",
                "backend_object_name": "Cube",
                "world_matrix": [
                    [1.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0, 0.0],
                    [0.0, 0.0, 0.0, 1.0],
                ],
                "refresh_artifacts": True,
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "Object transform is only available in headless mode."
