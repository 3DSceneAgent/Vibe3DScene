from __future__ import annotations

import json

from mcp_server.tools.asset_gen import tripo


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict, *, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _enable_tripo(monkeypatch) -> None:
    monkeypatch.setattr(tripo.runtime, "is_tripo_tool_enabled", lambda: True)
    monkeypatch.setattr(tripo.runtime, "get_tripo_api_key", lambda: "tripo-api-key")


def test_generate_tripo3d_model_requires_exactly_one_input(monkeypatch):
    _enable_tripo(monkeypatch)

    result = tripo.generate_tripo3d_model(
        None,
        text_prompt="chair",
        input_image_url="/tmp/chair.png",
    )

    assert "Provide exactly one" in result


def test_generate_tripo3d_model_text_prefers_pbr_model(monkeypatch):
    _enable_tripo(monkeypatch)
    captured_submit_payload: dict[str, object] = {}

    def fake_post(url, headers=None, json=None, files=None, timeout=60):
        _ = (headers, files, timeout)
        if url.endswith("/task"):
            if isinstance(json, dict):
                captured_submit_payload.update(json)
            return _FakeResponse(
                200,
                {"code": 0, "data": {"task_id": "task-text-123"}},
                headers={"X-Tripo-Trace-ID": "trace-submit"},
            )
        raise AssertionError(f"Unexpected POST url: {url}")

    def fake_get(url, headers=None, timeout=30):
        _ = (headers, timeout)
        assert url.endswith("/task/task-text-123")
        return _FakeResponse(
            200,
            {
                "code": 0,
                "data": {
                    "task_id": "task-text-123",
                    "status": "success",
                    "progress": 100,
                    "output": {
                        "pbr_model": "https://example.test/tripo/output/model_pbr.glb",
                        "model": "https://example.test/tripo/output/model.glb",
                        "rendered_image": "https://example.test/tripo/output/preview.png",
                    },
                },
            },
            headers={"X-Tripo-Trace-ID": "trace-poll"},
        )

    monkeypatch.setattr(tripo.requests, "post", fake_post)
    monkeypatch.setattr(tripo.requests, "get", fake_get)

    raw_result = tripo.generate_tripo3d_model(
        None,
        text_prompt="a low poly chair",
        timeout_seconds=5,
        poll_interval_seconds=0.01,
    )
    parsed = json.loads(raw_result)

    assert captured_submit_payload == {
        "type": "text_to_model",
        "model_version": "P1-20260311",
        "texture": True,
        "pbr": True,
        "texture_quality": "standard",
        "export_uv": False,
        "auto_size": False,
        "prompt": "a low poly chair",
    }
    assert parsed["preferred_model_asset"] == {
        "type": "PBR_MODEL",
        "output_field": "pbr_model",
        "url": "https://example.test/tripo/output/model_pbr.glb",
        "url_extension": "glb",
        "resolved_type": "GLB",
        "is_archive": False,
    }
    assert parsed["recommended_next_tool"] == "import_glb_model"
    assert parsed["trace_ids"] == {
        "submit": "trace-submit",
        "last_poll": "trace-poll",
    }


def test_generate_tripo3d_model_image_uploads_local_file(monkeypatch, tmp_path):
    _enable_tripo(monkeypatch)
    image_path = tmp_path / "chair.png"
    image_path.write_bytes(b"png-bytes")
    call_sequence: list[str] = []

    def fake_post(url, headers=None, json=None, files=None, timeout=60):
        _ = timeout
        if url.endswith("/upload/sts"):
            call_sequence.append("upload")
            assert headers == {"Authorization": "Bearer tripo-api-key"}
            assert files is not None
            filename, content, mime_type = files["file"]
            assert filename == "chair.png"
            assert content == b"png-bytes"
            assert mime_type == "image/png"
            return _FakeResponse(200, {"code": 0, "data": {"image_token": "image-token-1"}})
        if url.endswith("/task"):
            call_sequence.append("submit")
            assert headers == {
                "Authorization": "Bearer tripo-api-key",
                "Content-Type": "application/json",
            }
            assert json == {
                "type": "image_to_model",
                "model_version": "P1-20260311",
                "texture": True,
                "pbr": True,
                "texture_quality": "standard",
                "export_uv": False,
                "auto_size": False,
                "file": {
                    "type": "png",
                    "file_token": "image-token-1",
                },
                "enable_image_autofix": False,
            }
            return _FakeResponse(200, {"code": 0, "data": {"task_id": "task-image-123"}})
        raise AssertionError(f"Unexpected POST url: {url}")

    def fake_get(url, headers=None, timeout=30):
        _ = (headers, timeout)
        call_sequence.append("poll")
        assert url.endswith("/task/task-image-123")
        return _FakeResponse(
            200,
            {
                "code": 0,
                "data": {
                    "task_id": "task-image-123",
                    "status": "success",
                    "progress": 100,
                    "output": {
                        "model": "https://example.test/tripo/output/generated.glb",
                        "rendered_image": "https://example.test/tripo/output/preview.png",
                    },
                },
            },
        )

    monkeypatch.setattr(tripo.requests, "post", fake_post)
    monkeypatch.setattr(tripo.requests, "get", fake_get)

    raw_result = tripo.generate_tripo3d_model(
        None,
        input_image_url=str(image_path),
        timeout_seconds=5,
        poll_interval_seconds=0.01,
    )
    parsed = json.loads(raw_result)

    assert call_sequence == ["upload", "submit", "poll"]
    assert parsed["preferred_model_asset"]["url"] == "https://example.test/tripo/output/generated.glb"
    assert parsed["preferred_model_asset"]["type"] == "MODEL"
