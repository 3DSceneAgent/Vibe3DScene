from __future__ import annotations

import json

from mcp_server.tools.asset_gen import hunyuan3d


def test_generate_hunyuan3d_model_exposes_preferred_obj_zip_asset(monkeypatch):
    def _fake_call_tencent_cloud_api(*, action: str, payload: dict, **_kwargs):
        assert _kwargs["service"] == "ai3d"
        assert _kwargs["version"] == "2025-05-13"

        if action == "SubmitHunyuanTo3DRapidJob":
            assert payload["Prompt"] == "A stylized chair"
            assert payload["EnablePBR"] is True
            assert payload["ResultFormat"] == "GLB"
            return {"Response": {"JobId": "1428029626508517376"}}
        if action == "QueryHunyuanTo3DRapidJob":
            return {
                "Response": {
                    "Status": "DONE",
                    "ResultFile3Ds": [
                        {
                            "File3D": [
                                {
                                    "Type": "GIF",
                                    "Url": "https://example.test/hunyuan/output/model.gif",
                                },
                                {
                                    "Type": "OBJ",
                                    "Url": "https://example.test/hunyuan/output/model.zip",
                                },
                            ]
                        }
                    ],
                }
            }
        raise AssertionError(f"Unexpected action: {action}")

    monkeypatch.setattr(hunyuan3d.runtime, "is_hunyuan_tool_enabled", lambda: True)
    monkeypatch.setattr(
        hunyuan3d.runtime,
        "call_tencent_cloud_api",
        _fake_call_tencent_cloud_api,
    )
    monkeypatch.setattr(hunyuan3d.runtime, "extract_hunyuan_status", lambda _resp: "DONE")
    monkeypatch.setenv("HUNYUAN3D_SECRET_ID", "secret-id")
    monkeypatch.setenv("HUNYUAN3D_SECRET_KEY", "secret-key")

    raw_result = hunyuan3d.generate_hunyuan3d_model(
        ctx=None,
        text_prompt="A stylized chair",
        timeout_seconds=5,
        poll_interval_seconds=0.01,
    )
    parsed = json.loads(raw_result)

    assert parsed["job_id"] == "job_1428029626508517376"
    assert parsed["status"] == "DONE"
    assert parsed["generation_mode"] == "rapid"
    assert parsed["preferred_model_asset"] == {
        "type": "OBJ",
        "url": "https://example.test/hunyuan/output/model.zip",
        "url_extension": "zip",
        "is_archive": True,
    }
    assert parsed["normalized_assets"] == [
        {
            "type": "GIF",
            "url": "https://example.test/hunyuan/output/model.gif",
            "url_extension": "gif",
            "is_archive": False,
        },
        {
            "type": "OBJ",
            "url": "https://example.test/hunyuan/output/model.zip",
            "url_extension": "zip",
            "is_archive": True,
        },
    ]


def test_generate_hunyuan3d_model_surfaces_query_error_detail(monkeypatch):
    def _fake_call_tencent_cloud_api(*, action: str, payload: dict, **_kwargs):
        if action == "SubmitHunyuanTo3DProJob":
            assert payload["Prompt"] == "A broken chair"
            assert payload["EnablePBR"] is True
            assert "ResultFormat" not in payload
            return {"Response": {"JobId": "1428029626508517376"}}
        if action == "QueryHunyuanTo3DProJob":
            return {
                "Response": {
                    "Status": "FAIL",
                    "ErrorCode": "ResourceInsufficient",
                    "ErrorMessage": "资源不足。",
                }
            }
        raise AssertionError(f"Unexpected action: {action}")

    monkeypatch.setattr(hunyuan3d.runtime, "is_hunyuan_tool_enabled", lambda: True)
    monkeypatch.setattr(
        hunyuan3d.runtime,
        "call_tencent_cloud_api",
        _fake_call_tencent_cloud_api,
    )
    monkeypatch.setattr(hunyuan3d.runtime, "extract_hunyuan_status", lambda _resp: "FAIL")
    monkeypatch.setenv("HUNYUAN3D_SECRET_ID", "secret-id")
    monkeypatch.setenv("HUNYUAN3D_SECRET_KEY", "secret-key")

    raw_result = hunyuan3d.generate_hunyuan3d_model(
        ctx=None,
        text_prompt="A broken chair",
        generation_mode="pro",
        timeout_seconds=5,
        poll_interval_seconds=0.01,
    )
    parsed = json.loads(raw_result)

    assert parsed["job_id"] == "job_1428029626508517376"
    assert parsed["status"] == "FAIL"
    assert parsed["generation_mode"] == "pro"
    assert parsed["detail"] == {
        "Code": "ResourceInsufficient",
        "Message": "资源不足。",
    }


def test_generate_hunyuan3d_model_supports_rapid_mode(monkeypatch):
    def _fake_call_tencent_cloud_api(*, action: str, payload: dict, **_kwargs):
        assert _kwargs["service"] == "ai3d"
        assert _kwargs["version"] == "2025-05-13"

        if action == "SubmitHunyuanTo3DRapidJob":
            assert payload["Prompt"] == "A quick chair"
            assert payload["EnablePBR"] is True
            assert payload["ResultFormat"] == "GLB"
            return {"Response": {"JobId": "1428029626508517377"}}
        if action == "QueryHunyuanTo3DRapidJob":
            return {
                "Response": {
                    "Status": "DONE",
                    "ResultFile3Ds": [
                        {
                            "Type": "GLB",
                            "Url": "https://example.test/hunyuan/output/rapid_model.glb",
                        }
                    ],
                }
            }
        raise AssertionError(f"Unexpected action: {action}")

    monkeypatch.setattr(hunyuan3d.runtime, "is_hunyuan_tool_enabled", lambda: True)
    monkeypatch.setattr(
        hunyuan3d.runtime,
        "call_tencent_cloud_api",
        _fake_call_tencent_cloud_api,
    )
    monkeypatch.setattr(hunyuan3d.runtime, "extract_hunyuan_status", lambda _resp: "DONE")
    monkeypatch.setenv("HUNYUAN3D_SECRET_ID", "secret-id")
    monkeypatch.setenv("HUNYUAN3D_SECRET_KEY", "secret-key")

    raw_result = hunyuan3d.generate_hunyuan3d_model(
        ctx=None,
        text_prompt="A quick chair",
        generation_mode="rapid",
        timeout_seconds=5,
        poll_interval_seconds=0.01,
    )
    parsed = json.loads(raw_result)

    assert parsed["job_id"] == "job_1428029626508517377"
    assert parsed["status"] == "DONE"
    assert parsed["generation_mode"] == "rapid"
    assert parsed["preferred_model_asset"] == {
        "type": "GLB",
        "url": "https://example.test/hunyuan/output/rapid_model.glb",
        "url_extension": "glb",
        "is_archive": False,
    }
