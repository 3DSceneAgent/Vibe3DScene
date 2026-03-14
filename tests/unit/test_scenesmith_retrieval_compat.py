from __future__ import annotations

from typing import Optional

from fastapi.testclient import TestClient

from tool_servers.SceneSmithRetrieval.app import create_app


class DummyArtifacts:
    def resolve_file(self, artifact_id: str, key: Optional[str] = None):
        raise AssertionError(f"resolve_file should not be called in this test: {artifact_id} {key}")


class DummyHssdRuntime:
    def __init__(self) -> None:
        self.ready = True
        self.started = False
        self.stopped = False
        self.requests: list[tuple[str, int]] = []
        self.artifacts = DummyArtifacts()

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def search(self, request):
        self.requests.append((request.object_type, request.top_k))
        fixtures = {
            "FURNITURE": [
                {
                    "artifact_id": "artifact-chair-v1",
                    "hssd_id": "chair-01",
                    "name": "Chair",
                    "category": "large_objects",
                    "similarity_score": 0.62,
                    "bbox_score": 0.20,
                    "size_m": (0.8, 0.8, 1.0),
                    "expires_at": "2026-03-14T00:00:00Z",
                },
                {
                    "artifact_id": "artifact-table",
                    "hssd_id": "table-01",
                    "name": "Table",
                    "category": "large_objects",
                    "similarity_score": 0.71,
                    "bbox_score": 0.10,
                    "size_m": (1.2, 0.8, 0.7),
                    "expires_at": "2026-03-14T00:00:00Z",
                },
            ],
            "MANIPULAND": [
                {
                    "artifact_id": "artifact-chair-v2",
                    "hssd_id": "chair-01",
                    "name": "Chair refined",
                    "category": "small_objects",
                    "similarity_score": 0.95,
                    "bbox_score": 0.05,
                    "size_m": (0.8, 0.8, 1.0),
                    "expires_at": "2026-03-14T00:00:00Z",
                }
            ],
            "WALL_MOUNTED": [],
            "CEILING_MOUNTED": [
                {
                    "artifact_id": "artifact-lamp",
                    "hssd_id": "lamp-01",
                    "name": "Lamp",
                    "category": "ceiling_objects",
                    "similarity_score": 0.80,
                    "bbox_score": 0.08,
                    "size_m": (0.4, 0.4, 0.3),
                    "expires_at": "2026-03-14T00:00:00Z",
                }
            ],
        }
        return fixtures[request.object_type]


class DummyAmbientCGRuntime:
    def __init__(self) -> None:
        self.ready = True
        self.started = False
        self.stopped = False
        self.requests: list[tuple[str, int]] = []
        self.artifacts = DummyArtifacts()

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def search(self, request):
        self.requests.append((request.query, request.top_k))
        return [
            {
                "artifact_id": "material-zip-01",
                "material_id": "rough_plaster",
                "category": "wall",
                "tags": ["rough", "white"],
                "similarity_score": 0.88,
                "expires_at": "2026-03-14T00:00:00Z",
            }
        ]


def test_scenesmith_compat_health_and_ready() -> None:
    runtime = DummyHssdRuntime()
    app = create_app(hssd_runtime=runtime, enable_ambientcg=False)

    with TestClient(app) as client:
        health = client.get("/health")
        ready = client.get("/readyz")

    assert health.status_code == 200
    payload = health.json()
    assert payload["status"] == "running"
    assert payload["backend"] == "scenesmith"
    assert payload["services"]["hssd"]["enabled"] is True
    assert payload["services"]["hssd"]["ready"] is True
    assert payload["services"]["ambientcg"]["enabled"] is False
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert runtime.started is True
    assert runtime.stopped is True


def test_legacy_search_text_fans_out_and_dedupes() -> None:
    runtime = DummyHssdRuntime()
    app = create_app(hssd_runtime=runtime, enable_ambientcg=False)

    with TestClient(app) as client:
        response = client.post(
            "/search/text",
            json={"query": "chair", "top_k": 2},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["algorithm"] == "scenesmith_hssd"
    assert [item["asset_id"] for item in payload["results"]] == ["chair-01", "lamp-01"]
    assert payload["results"][0]["caption_en"] == "Chair refined"
    assert payload["results"][0]["model_url"].endswith("/hssd/v1/artifacts/artifact-chair-v2")
    assert runtime.requests == [
        ("FURNITURE", 2),
        ("MANIPULAND", 2),
        ("WALL_MOUNTED", 2),
        ("CEILING_MOUNTED", 2),
    ]


def test_ambientcg_search_returns_texture_urls() -> None:
    ambient_runtime = DummyAmbientCGRuntime()
    app = create_app(
        hssd_runtime=DummyHssdRuntime(),
        ambientcg_runtime=ambient_runtime,
        enable_hssd=True,
        enable_ambientcg=True,
    )

    with TestClient(app) as client:
        response = client.post(
            "/ambientcg/v1/search",
            json={"query": "rough plaster", "top_k": 1},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "rough plaster"
    candidate = payload["candidates"][0]
    assert candidate["material_id"] == "rough_plaster"
    assert candidate["package_download_url"].endswith(
        "/ambientcg/v1/artifacts/material-zip-01"
    )
    assert candidate["textures"]["color_url"].endswith(
        "/ambientcg/v1/artifacts/material-zip-01/color"
    )
    assert ambient_runtime.requests == [("rough plaster", 1)]
