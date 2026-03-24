from __future__ import annotations

import json

from mcp_server.tools.asset_gen import hunyuan3d


def test_generate_hunyuan3d_model_exposes_preferred_obj_zip_asset(monkeypatch):
    def _fake_call_tencent_cloud_api(*, action: str, payload: dict, **_kwargs):
        if action == "SubmitHunyuanTo3DJob":
            assert payload["Prompt"] == "A stylized chair"
            return {"Response": {"JobId": "1428029626508517376"}}
        if action == "QueryHunyuanTo3DJob":
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
