import json

from mcp_server.tools.asset_gen import rodin
from mcp_server.tools.asset_retrieval import polyhaven, sketchfab


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _enable_rodin_main_site(monkeypatch) -> None:
    monkeypatch.setattr(rodin.runtime, "is_rodin_tool_enabled", lambda: True)
    monkeypatch.setattr(rodin.runtime, "get_rodin_api_key", lambda: "rodin-api-key")


def test_poll_rodin_main_site_requires_subscription_id(monkeypatch):
    _enable_rodin_main_site(monkeypatch)

    result = rodin.poll_rodin_job_status(None)

    assert "requires subscription_id" in result


def test_poll_rodin_main_site_uses_subscription_id(monkeypatch):
    _enable_rodin_main_site(monkeypatch)
    captured_payload: dict[str, str] = {}

    def fake_post(url, headers=None, json=None, timeout=60):
        _ = (url, headers, timeout)
        if isinstance(json, dict):
            captured_payload.update(json)
        return _FakeResponse(200, {"jobs": [{"status": "Done"}]})

    monkeypatch.setattr(rodin.requests, "post", fake_post)

    result = rodin.poll_rodin_job_status(None, subscription_id="sub-id-123")

    assert captured_payload == {"subscription_id": "sub-id-123"}
    assert json.loads(result) == {"status_list": ["Done"]}


def test_search_sketchfab_models_caps_count_to_five(monkeypatch):
    monkeypatch.setattr(sketchfab.runtime, "is_sketchfab_tool_enabled", lambda: True)
    monkeypatch.setattr(sketchfab.runtime, "get_sketchfab_api_key", lambda: "sketchfab-key")

    def fake_get(url, headers=None, params=None, timeout=30):
        _ = (url, headers, timeout)
        count = int((params or {}).get("count", 0))
        assert count == 5
        payload = {
            "results": [
                {
                    "name": f"Model {idx}",
                    "uid": f"uid-{idx}",
                    "user": {"username": "author"},
                    "license": {"label": "CC"},
                    "faceCount": 100 + idx,
                    "isDownloadable": True,
                }
                for idx in range(count)
            ]
        }
        return _FakeResponse(200, payload)

    monkeypatch.setattr(sketchfab.requests, "get", fake_get)

    output = sketchfab.search_sketchfab_models(None, query="chair", count=999)

    assert "Found 5 models matching 'chair'" in output


def test_search_polyhaven_assets_returns_at_most_five(monkeypatch):
    assets = {
        f"id_{idx}": {
            "name": f"Asset {idx}",
            "type": 0,
            "categories": ["test"],
            "download_count": idx,
        }
        for idx in range(8)
    }

    def fake_get(url, params=None, headers=None, timeout=30):
        _ = (url, params, headers, timeout)
        return _FakeResponse(200, assets)

    monkeypatch.setattr(polyhaven.requests, "get", fake_get)

    output = polyhaven.search_polyhaven_assets(None)

    assert "Showing 5 assets" in output
    assert output.count("\n- ") == 5
    assert "Asset 7" in output
    assert "Asset 2" not in output
