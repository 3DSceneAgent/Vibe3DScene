from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from scene_agent.interfaces import api as api_module
from scene_agent.interfaces.api import routes_scene as api_routes_scene
from scene_agent.interfaces.api import shared as api_shared


def test_add_primitive_route_success(monkeypatch):
    persisted_calls: list[tuple[str, float]] = []
    blender_calls: list[tuple[str, dict[str, object] | None, str | None]] = []
    metadata_calls: list[str] = []

    async def fake_claim_or_proxy_request(*, request, thread_id):  # type: ignore[no-untyped-def]
        return None, None

    class _Manager:
        @staticmethod
        def ensure(thread_id, mode):
            assert thread_id == "thread-add"
            assert mode == "headless"
            return SimpleNamespace()

        @staticmethod
        def persist_session_blend(thread_id, min_interval_seconds=8.0):
            persisted_calls.append((thread_id, float(min_interval_seconds)))

    def _fake_send_blender_command_sync(command_type, params=None, thread_id=None):
        blender_calls.append((command_type, params, thread_id))
        if command_type == "execute_code":
            code = str((params or {}).get("code") or "")
            assert "primitive_cube_add" in code
            assert "uuid-cube-123" in code
            return {"result": "CREATED:Cube|uuid-cube-123\n"}
        raise AssertionError(f"Unexpected Blender command: {command_type}")

    def _fake_ensure_scene_agent_object_metadata_sync(thread_id, preserve_activity=False):
        metadata_calls.append(thread_id)
        return {"success": True, "assigned_objects": []}

    monkeypatch.setattr(api_routes_scene, "claim_or_proxy_request", fake_claim_or_proxy_request)
    monkeypatch.setattr(api_routes_scene, "get_settings", lambda: SimpleNamespace(blender_mode="headless"))
    monkeypatch.setattr(api_routes_scene, "get_session_manager", lambda: _Manager())
    monkeypatch.setattr(api_routes_scene, "send_blender_command_sync", _fake_send_blender_command_sync)
    monkeypatch.setattr(
        api_routes_scene,
        "ensure_scene_agent_object_metadata_sync",
        _fake_ensure_scene_agent_object_metadata_sync,
    )
    monkeypatch.setattr(api_routes_scene.uuid, "uuid4", lambda: "uuid-cube-123")
    monkeypatch.setattr(
        api_routes_scene,
        "persist_thread_scene_artifacts_sync",
        lambda thread_id: api_shared.SceneArtifactManifestResponse(
            thread_id=thread_id,
            has_persisted_blend=True,
            scene_revision=1770000001234,
            generated_at_ms=1770000002234,
            gltf_url=f"/threads/{thread_id}/scene-artifacts/latest.glb?rev=1770000001234",
            renders=[],
        ),
    )

    with TestClient(api_module.app) as client:
        response = client.post(
            "/scene/thread-add/objects/add-primitive",
            json={
                "primitive_type": "cube",
                "location": [0.0, 1.5, 2.0],
                "size": 1.25,
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "thread_id": "thread-add",
        "primitive_type": "cube",
        "object_name": "Cube",
        "backend_object_id": "uuid-cube-123",
        "backend_object_name": "Cube",
        "scene_revision": 1770000001234,
        "manifest_generated_at_ms": 1770000002234,
        "has_persisted_blend": True,
    }
    assert persisted_calls == [("thread-add", 0.0)]
    assert metadata_calls == ["thread-add"]
    assert [call[0] for call in blender_calls] == ["execute_code"]


def test_add_primitive_route_rejects_local_client(monkeypatch):
    async def fake_claim_or_proxy_request(*, request, thread_id):  # type: ignore[no-untyped-def]
        return None, None

    monkeypatch.setattr(api_routes_scene, "claim_or_proxy_request", fake_claim_or_proxy_request)
    monkeypatch.setattr(api_routes_scene, "get_settings", lambda: SimpleNamespace(blender_mode="local-client"))

    with TestClient(api_module.app) as client:
        response = client.post(
            "/scene/thread-add-local/objects/add-primitive",
            json={"primitive_type": "cube"},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "Add primitive is only available in headless mode."


def test_add_primitive_route_requires_primitive_type():
    with TestClient(api_module.app) as client:
        response = client.post(
            "/scene/thread-add-missing/objects/add-primitive",
            json={},
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert any(entry.get("loc") == ["body", "primitive_type"] for entry in detail if isinstance(entry, dict))
