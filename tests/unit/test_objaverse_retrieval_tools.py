from __future__ import annotations

from mcp_server.tools.asset_retrieval import objaverse_retrieval as tools


class DummyResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status_code = 200
        self.text = ""

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_search_3d_assets_by_text_formats_visual_import_guidance(monkeypatch):
    def fake_post(url, json, timeout):
        assert url == "http://10.0.0.9:8002/search/text"
        assert json == {"query": "dragon low poly game asset", "top_k": 2}
        assert timeout == 30
        return DummyResponse(
            {
                "results": [
                    {
                        "asset_id": "asset-1",
                        "similarity": 0.292,
                        "caption_en": "Low poly dragon statue",
                        "model_url": "http://10.0.0.9:8002/models/asset-1.glb",
                    }
                ]
            }
        )

    monkeypatch.setattr(tools, "get_retrieval_base_url", lambda: "http://10.0.0.9:8002")
    monkeypatch.setattr(tools.requests, "post", fake_post)

    result = tools.search_3d_assets_by_text(None, query="dragon low poly game asset", top_k=2)

    assert "Found 1 assets for query: 'dragon low poly game asset'" in result
    assert "import_retrieved_asset(model_url=..., object_name=...)" in result
    assert "asset_id is only a reference label" in result
    assert "Descriptions and similarity scores can be noisy" in result
    assert "import one candidate first instead of rejecting it only from the text" in result
