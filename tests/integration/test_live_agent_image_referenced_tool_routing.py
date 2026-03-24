from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest
import requests
from pydantic import BaseModel, Field

from scene_agent.config import get_settings, reload_settings
from scene_agent.env import load_project_dotenv
from scene_agent.tools.test_stub_tools import DEFAULT_IMAGE_ROUTING_COMPARE_RECORD_PATH


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_IMAGE_PATH = (
    PROJECT_ROOT / "assets" / "example_images" / "typical_creature_robot_dinosour.png"
)


class _ToolCallRecord(BaseModel):
    name: str
    args: dict[str, object] = Field(default_factory=dict)


def _real_api_key(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value == "test-key":
        return ""
    return value


def _resolve_live_vlm_request() -> dict[str, str]:
    requested_provider = os.getenv("AGENT_IMAGE_ROUTING_TEST_PROVIDER", "").strip().lower()
    settings = get_settings()
    provider_matrix = [
        ("gemini", "GEMINI_API_KEY", "AGENT_IMAGE_ROUTING_TEST_MODEL", settings.get_vlm_default_model("gemini")),
        ("openai", "OPENAI_API_KEY", "AGENT_IMAGE_ROUTING_TEST_MODEL", settings.get_vlm_default_model("openai")),
        ("qwen", "QWEN_API_KEY", "AGENT_IMAGE_ROUTING_TEST_MODEL", settings.get_vlm_default_model("qwen")),
        ("anthropic", "ANTHROPIC_API_KEY", "AGENT_IMAGE_ROUTING_TEST_MODEL", settings.get_vlm_default_model("anthropic")),
    ]

    if requested_provider:
        for provider, key_env, model_env, default_model in provider_matrix:
            if provider != requested_provider:
                continue
            api_key = _real_api_key(key_env)
            if not api_key:
                pytest.skip(
                    f"Set a real {key_env} to run the live image-referenced agent integration test."
                )
            model_name = os.getenv(model_env, "").strip() or default_model
            return {"provider": provider, "model": model_name}
        pytest.skip(
            "AGENT_IMAGE_ROUTING_TEST_PROVIDER must be one of: gemini, openai, qwen, anthropic."
        )

    for provider, key_env, model_env, default_model in provider_matrix:
        api_key = _real_api_key(key_env)
        if not api_key:
            continue
        model_name = os.getenv(model_env, "").strip() or default_model
        return {"provider": provider, "model": model_name}

    pytest.skip(
        "Set RUN_INTEGRATION=1 and provide a real multimodal API key "
        "(prefer GEMINI_API_KEY, or OPENAI_API_KEY/QWEN_API_KEY/ANTHROPIC_API_KEY)."
    )


def _resolve_reference_image_path() -> Path:
    if not REFERENCE_IMAGE_PATH.exists():
        pytest.skip(f"Missing image fixture: {REFERENCE_IMAGE_PATH}")
    return REFERENCE_IMAGE_PATH


def _record_path() -> Path:
    configured = os.getenv("SCENE_AGENT_TEST_TOOL_RECORD_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_IMAGE_ROUTING_COMPARE_RECORD_PATH


def _read_tool_calls(record_path: Path) -> list[_ToolCallRecord]:
    if not record_path.exists():
        return []
    calls: list[_ToolCallRecord] = []
    for line in record_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        calls.append(_ToolCallRecord.model_validate_json(line))
    return calls


def _extract_call(calls: list[_ToolCallRecord], name: str) -> _ToolCallRecord:
    for call in calls:
        if call.name == name:
            return call
    raise AssertionError(f"Tool call '{name}' not found. Calls: {[item.name for item in calls]}")


def test_live_agent_routes_attached_image_into_retrieval_and_hunyuan_compare_flow(
    api_base_url: str,
) -> None:
    load_project_dotenv(override=True)
    reload_settings()
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Set RUN_INTEGRATION=1 to run live API image-referenced routing integration tests.")

    live_vlm = _resolve_live_vlm_request()
    reference_image_path = _resolve_reference_image_path()
    record_path = _record_path()
    record_path.unlink(missing_ok=True)

    health_response = requests.get(f"{api_base_url}/health", timeout=10)
    if health_response.status_code != 200:
        pytest.skip(f"Headless API is not reachable at {api_base_url}/health")

    thread_id = f"thread-image-routing-live-{uuid4().hex}"
    with reference_image_path.open("rb") as image_handle:
        upload_response = requests.post(
            f"{api_base_url}/threads/{thread_id}/images",
            files=[("images", (reference_image_path.name, image_handle, "image/png"))],
            timeout=30,
        )
    assert upload_response.status_code == 200, upload_response.text
    uploaded_images = upload_response.json()["images"]
    assert len(uploaded_images) == 1
    uploaded_image_id = uploaded_images[0]["id"]

    response = requests.post(
        f"{api_base_url}/chat/stream",
        json={
                "message": (
                    "Use only tool calls. Do not answer with prose, explanations, or questions. "
                    "Execute this workflow in order for the single attached reference image: "
                    "1. Call search_3d_assets_by_text once to find one plausible retrieved model matching the image. "
                    "2. Call import_retrieved_asset with the returned model_url and object_name='RetrievedReferenceAsset'. "
                    "3. Call generate_hunyuan3d_model with no text_prompt and rely on the single attached image. "
                    "4. After Hunyuan returns a generated URL, emit import_glb_model and execute_blender_code in the same assistant response as one tool batch. "
                    "Call import_glb_model on preferred_model_asset.url when present, otherwise use the generated Type=OBJ ResultFile3Ds URL, with object_name='GeneratedReferenceAsset'. "
                    "In that same tool batch, call execute_blender_code to place RetrievedReferenceAsset at x=-1 and GeneratedReferenceAsset at x=1 so they are side by side. "
                    "Do not pause for extra verification between import_glb_model and execute_blender_code. "
                    "Stop after those tool calls are complete."
                ),
            "thread_id": thread_id,
            "attached_image_ids": [uploaded_image_id],
            "enabled_mcp_tools": [
                "get_scene_info",
                "search_3d_assets_by_text",
                "import_retrieved_asset",
                "generate_hunyuan3d_model",
                "import_glb_model",
                "execute_blender_code",
            ],
            "vlm_provider": live_vlm["provider"],
            "vlm_model": live_vlm["model"],
            "fast_mode": True,
        },
        stream=True,
        timeout=(10, 120),
    )
    assert response.status_code == 200, response.text

    stream_events: list[str] = []
    stream_errors: list[str] = []
    stop_reader = threading.Event()

    def _drain_stream() -> None:
        try:
            for raw_line in response.iter_lines(decode_unicode=True):
                if stop_reader.is_set():
                    break
                if not raw_line:
                    continue
                stream_events.append(raw_line)
                if len(stream_events) > 80:
                    del stream_events[:-80]
        except Exception as exc:  # pragma: no cover - diagnostics only
            stream_errors.append(str(exc))

    reader = threading.Thread(target=_drain_stream, daemon=True)
    reader.start()

    expected_tool_names = {
        "search_3d_assets_by_text",
        "generate_hunyuan3d_model",
        "import_retrieved_asset",
        "import_glb_model",
        "execute_blender_code",
    }

    deadline = time.monotonic() + 120.0
    try:
        while time.monotonic() < deadline:
            calls = _read_tool_calls(record_path)
            if expected_tool_names.issubset({call.name for call in calls}):
                break
            time.sleep(0.5)
    finally:
        stop_reader.set()
        response.close()
        reader.join(timeout=5.0)

    settle_deadline = time.monotonic() + 10.0
    while time.monotonic() < settle_deadline:
        calls = _read_tool_calls(record_path)
        if expected_tool_names.issubset({call.name for call in calls}):
            break
        time.sleep(0.5)

    calls = _read_tool_calls(record_path)
    call_names = [call.name for call in calls]
    missing_tools = sorted(expected_tool_names.difference(call_names))
    assert not missing_tools, (
        f"Missing expected tool calls: {missing_tools}. "
        f"Observed calls: {call_names}. "
        f"Stream errors: {stream_errors}. "
        f"Recent stream events: {stream_events[-20:]}"
    )

    assert call_names.index("search_3d_assets_by_text") < call_names.index("import_retrieved_asset")
    assert call_names.index("generate_hunyuan3d_model") < call_names.index("import_glb_model")
    placement_index = call_names.index("execute_blender_code")
    assert placement_index > call_names.index("import_retrieved_asset")
    assert placement_index > call_names.index("import_glb_model")

    hunyuan_call = _extract_call(calls, "generate_hunyuan3d_model")
    assert hunyuan_call.args.get("input_image_url")
    assert uploaded_image_id in str(hunyuan_call.args["input_image_url"])
    assert hunyuan_call.args.get("input_image_name") in {None, ""}
    assert hunyuan_call.args.get("input_image_id") in {None, ""}

    retrieved_import_call = _extract_call(calls, "import_retrieved_asset")
    assert retrieved_import_call.args.get("object_name") == "RetrievedReferenceAsset"

    generated_import_call = _extract_call(calls, "import_glb_model")
    assert generated_import_call.args.get("object_name") == "GeneratedReferenceAsset"
    assert str(generated_import_call.args.get("model_url", "")).endswith(
        "/generated_reference_asset.zip"
    )

    placement_call = _extract_call(calls, "execute_blender_code")
    placement_code = str(placement_call.args.get("code", ""))
    assert "RetrievedReferenceAsset" in placement_code
    assert "GeneratedReferenceAsset" in placement_code
    lowered_code = placement_code.lower()
    assert any(
        marker in lowered_code
        for marker in ("side by side", "offset", "translate", "location", "x = -1", "x = 1")
    )
