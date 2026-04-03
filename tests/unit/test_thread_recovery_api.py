from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from scene_agent.interfaces import api as api_module
from scene_agent.interfaces.api import routes_runtime as api_routes_runtime
from scene_agent.interfaces.api import shared as api_shared
from scene_agent.memory.reference_image_memory import ImageAsset


def test_build_thread_history_payload_serializes_attached_images_and_tool_media(tmp_path, monkeypatch):
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
                                content="Create a chair",
                                id="turn-1",
                                additional_kwargs={
                                    "created_at_ms": 1770000000001,
                                    "attached_image_ids": ["img-1"],
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
    assert payload.messages[0].attached_images[0].asset_url == f"/threads/{thread_id}/images/img-1"
    assert payload.messages[2].tool_media[0].value == "/renders/preview.jpg"
    assert payload.todos[0]["id"] == "todo-1"


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
    assert manifest.gltf_url == f"/threads/{thread_id}/scene-artifacts/latest.glb"
    assert manifest.renders[0].camera_name == "SceneCamera_NE"


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
