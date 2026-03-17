from __future__ import annotations

from pathlib import Path

from mcp_server.tools.asset_retrieval import scenesmith_retrieval as tools


class DummyResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status_code = 200
        self.text = ""
        self.content = b"demo"

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_search_hssd_assets_formats_results(monkeypatch):
    def fake_post(url, json, timeout):
        assert url == "http://10.0.0.9:8005/hssd/v1/search"
        assert json["object_type"] == "FURNITURE"
        assert json["top_k"] == 2
        assert timeout == 60
        return DummyResponse(
            {
                "query": "chair",
                "candidates": [
                    {
                        "hssd_id": "chair-01",
                        "name": "Chair",
                        "category": "large_objects",
                        "similarity_score": 0.91,
                        "bbox_score": 0.04,
                        "size_m": [0.8, 0.7, 1.0],
                        "download_url": "http://localhost:8005/hssd/v1/artifacts/abc",
                    }
                ],
            }
        )

    monkeypatch.setenv("ASSET_RETRIEVAL_BACKEND", "scenesmith")
    monkeypatch.setenv("TOOL_SERVICE_HOST", "10.0.0.9")
    monkeypatch.setattr(tools.requests, "post", fake_post)

    result = tools.search_hssd_assets(None, query="chair", top_k=2)

    assert "Found 1 HSSD assets" in result
    assert "HSSD ID: chair-01" in result
    assert "import_hssd_asset(download_url=..., object_name=...)" in result


def test_apply_ambientcg_material_downloads_and_executes(monkeypatch, tmp_path: Path):
    downloaded: list[tuple[str, Path]] = []
    executed: dict[str, object] = {}

    def fake_get(url, timeout):
        assert timeout == 60
        return DummyResponse({})

    class DummyBlender:
        def send_command(self, command_type, params):
            executed["command_type"] = command_type
            executed["params"] = params
            return {"success": True}

    def fake_write_bytes(self, content):
        downloaded.append((self.name, self))
        return len(content)

    monkeypatch.setattr(tools.requests, "get", fake_get)
    monkeypatch.setattr(tools.runtime, "get_blender_connection", lambda _logger: DummyBlender())
    monkeypatch.setattr(tools, "_ambientcg_texture_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(Path, "write_bytes", fake_write_bytes, raising=False)

    result = tools.apply_ambientcg_material(
        None,
        object_name="Cube",
        color_url="http://localhost/color.jpg",
        normal_url="http://localhost/normal.jpg",
        roughness_url="http://localhost/roughness.jpg",
        material_name="CubeMaterial",
    )

    assert "Applied AmbientCG material 'CubeMaterial' to 'Cube'" in result
    assert executed["command_type"] == "execute_code"
    assert "CubeMaterial" in executed["params"]["code"]
    assert "image.pack()" in executed["params"]["code"]
    assert "\"sRGB\" if is_color else \"Non-Color\"" in executed["params"]["code"]
    assert {name for name, _ in downloaded} == {"color.jpg", "normal.jpg", "roughness.jpg"}


def test_ambientcg_cache_root_varies_by_texture_urls(tmp_path: Path):
    first = tools._ambientcg_cache_root(
        object_name="Cube",
        color_url="http://localhost/a-color.jpg",
        normal_url="http://localhost/a-normal.jpg",
        roughness_url="http://localhost/a-roughness.jpg",
    )
    second = tools._ambientcg_cache_root(
        object_name="Cube",
        color_url="http://localhost/b-color.jpg",
        normal_url="http://localhost/b-normal.jpg",
        roughness_url="http://localhost/b-roughness.jpg",
    )

    assert first != second
    assert first.name.startswith("Cube_")
    assert second.name.startswith("Cube_")
