from __future__ import annotations

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
from scene_agent.tools.test_stub_tools import (
    DEFAULT_SAM3D_RECONSTRUCT_BLEND_FILE_PATH,
    DEFAULT_SAM3D_RECONSTRUCT_RECORD_PATH,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_IMAGE_PATH = PROJECT_ROOT / "assets" / "example_images" / "sam3d_demo.png"


class _ToolCallRecord(BaseModel):
    name: str
    args: dict[str, object] = Field(default_factory=dict)


def _real_gemini_api_key() -> str:
    value = os.getenv("GEMINI_API_KEY", "").strip()
    if not value or value == "test-key":
        return ""
    return value


def _resolve_live_vlm_request() -> dict[str, str]:
    if not _real_gemini_api_key():
        pytest.skip(
            "Set RUN_INTEGRATION=1 and provide a real GEMINI_API_KEY in .env "
            "to run the live SAM3D agent integration test."
        )

    settings = get_settings()
    model_name = os.getenv("AGENT_SAM3D_TEST_MODEL", "").strip() or settings.get_vlm_default_model(
        "gemini"
    )
    return {"provider": "gemini", "model": model_name}


def _resolve_reference_image_path() -> Path:
    if not REFERENCE_IMAGE_PATH.exists():
        pytest.skip(f"Missing image fixture: {REFERENCE_IMAGE_PATH}")
    return REFERENCE_IMAGE_PATH


def _record_path() -> Path:
    configured = os.getenv("SCENE_AGENT_TEST_TOOL_RECORD_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_SAM3D_RECONSTRUCT_RECORD_PATH


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


def test_live_agent_routes_attached_image_into_sam3d_reconstruct_flow(
    api_base_url: str,
) -> None:
    load_project_dotenv(override=True)
    reload_settings()
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Set RUN_INTEGRATION=1 to run live SAM3D integration tests.")

    live_vlm = _resolve_live_vlm_request()
    reference_image_path = _resolve_reference_image_path()
    record_path = _record_path()
    record_path.unlink(missing_ok=True)

    health_response = requests.get(f"{api_base_url}/health", timeout=10)
    if health_response.status_code != 200:
        pytest.skip(f"Headless API is not reachable at {api_base_url}/health")

    thread_id = f"thread-sam3d-live-{uuid4().hex}"
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
                "Execute exactly this workflow in order for the single attached reference image: "
                "1. Call reconstruct_full_scene once to reconstruct the scene from the attached image. "
                "Do not provide input_image_name or input_image_id, and do not invent a filesystem path; "
                "rely on the single attached image. "
                "2. When reconstruct_full_scene returns a blend_file_path, immediately call "
                "import_blend_contents with that exact blend_file_path. "
                "There must be exactly these two required tool calls and no text-only answer. "
                "Stop after import_blend_contents succeeds."
            ),
            "thread_id": thread_id,
            "attached_image_ids": [uploaded_image_id],
            "enabled_mcp_tools": [
                "get_scene_info",
                "reconstruct_full_scene",
                "import_blend_contents",
            ],
            "vlm_provider": live_vlm["provider"],
            "vlm_model": live_vlm["model"],
            "fast_mode": False,
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
        "reconstruct_full_scene",
        "import_blend_contents",
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

    assert call_names.index("reconstruct_full_scene") < call_names.index("import_blend_contents")

    reconstruct_call = _extract_call(calls, "reconstruct_full_scene")
    assert reconstruct_call.args.get("input_image_path")
    assert uploaded_image_id in str(reconstruct_call.args["input_image_path"])
    assert reconstruct_call.args.get("input_image_name") in {None, ""}
    assert reconstruct_call.args.get("input_image_id") in {None, ""}

    import_call = _extract_call(calls, "import_blend_contents")
    assert import_call.args.get("blend_file_path") == DEFAULT_SAM3D_RECONSTRUCT_BLEND_FILE_PATH
    assert import_call.args.get("import_mode") == "auto"
    assert import_call.args.get("link") is False
