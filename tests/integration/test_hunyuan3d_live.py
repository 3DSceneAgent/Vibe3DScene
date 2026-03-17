from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
import requests

from mcp_server.tools.asset_gen.hunyuan3d import generate_hunyuan3d_model
from scene_agent.env import load_project_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
IMAGE_TO_3D_DEMO_PATH = PROJECT_ROOT / "assets" / "image_to_3d_demo.jpg"
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "hunyuan3d_live"
SUCCESS_STATUSES = {"DONE", "SUCCEEDED", "SUCCESS", "FINISHED", "COMPLETED"}


def _ensure_live_hunyuan_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    load_project_dotenv(override=False)

    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Set RUN_INTEGRATION=1 to run live Hunyuan3D integration tests.")

    monkeypatch.setenv("BLENDER_MODE", "headless")
    monkeypatch.setenv("ENABLE_HUNYUAN", "true")

    missing = [
        name
        for name in ("HUNYUAN3D_SECRET_ID", "HUNYUAN3D_SECRET_KEY")
        if not os.getenv(name, "").strip()
    ]
    if missing:
        pytest.skip(
            "Set HUNYUAN3D credentials to run live Hunyuan3D integration tests. "
            f"Missing: {', '.join(missing)}"
        )


def _response_contains_artifact(value: Any) -> bool:
    if isinstance(value, str):
        lowered = value.lower()
        return lowered.startswith(("http://", "https://")) or lowered.endswith(
            (".glb", ".fbx", ".obj", ".stl", ".zip")
        )
    if isinstance(value, list):
        return any(_response_contains_artifact(item) for item in value)
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and "url" in key.lower() and isinstance(item, str) and item:
                return True
            if _response_contains_artifact(item):
                return True
    return False


def _assert_successful_hunyuan_response(raw_response: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        pytest.fail(
            "Hunyuan3D response is not valid JSON. "
            f"Decode error: {exc}. Raw response: {raw_response}"
        )

    assert isinstance(parsed, dict), f"Expected JSON object response, got: {parsed!r}"
    assert "error" not in parsed, (
        "Hunyuan3D generation returned an error payload.\n"
        f"{json.dumps(parsed, indent=2, ensure_ascii=False)}"
    )

    job_id = parsed.get("job_id")
    assert isinstance(job_id, str) and job_id.startswith("job_"), parsed

    status = str(parsed.get("status", "")).upper()
    assert status in SUCCESS_STATUSES, parsed

    result_files = parsed.get("result_file_3ds")
    assert isinstance(result_files, list) and result_files, parsed
    assert _response_contains_artifact(result_files), parsed

    response = parsed.get("response")
    assert isinstance(response, dict) and isinstance(response.get("Response"), dict), parsed
    return parsed


def _collect_artifact_urls(value: Any) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def _walk(node: Any) -> None:
        if isinstance(node, str):
            if node.startswith(("http://", "https://")) and node not in seen:
                seen.add(node)
                urls.append(node)
            return
        if isinstance(node, list):
            for item in node:
                _walk(item)
            return
        if isinstance(node, dict):
            for item in node.values():
                _walk(item)

    _walk(value)
    return urls


def _reset_output_dir(output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def _infer_filename(url: str, response: requests.Response, index: int) -> str:
    content_disposition = response.headers.get("Content-Disposition", "")
    if "filename=" in content_disposition:
        filename = content_disposition.split("filename=", 1)[1].strip().strip('"')
        if filename:
            return filename

    parsed = urlparse(url)
    candidate = Path(parsed.path).name
    if candidate:
        return candidate
    return f"artifact_{index:02d}.bin"


def _download_artifacts(urls: list[str], download_dir: Path) -> list[dict[str, Any]]:
    download_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    used_names: set[str] = set()

    for index, url in enumerate(urls, start=1):
        response = requests.get(url, timeout=180)
        response.raise_for_status()

        filename = _infer_filename(url, response, index)
        stem = Path(filename).stem or f"artifact_{index:02d}"
        suffix = Path(filename).suffix
        candidate = filename
        collision = 1
        while candidate in used_names:
            candidate = f"{stem}_{collision}{suffix}"
            collision += 1
        used_names.add(candidate)

        output_path = download_dir / candidate
        output_path.write_bytes(response.content)
        manifest.append(
            {
                "url": url,
                "saved_path": str(output_path),
                "size_bytes": output_path.stat().st_size,
                "content_type": response.headers.get("Content-Type", ""),
            }
        )

    return manifest


def _persist_case_result(
    *,
    case_name: str,
    raw_response: str,
    parsed_response: dict[str, Any],
    started_at_monotonic: float,
    finished_at_monotonic: float,
) -> None:
    case_dir = OUTPUT_ROOT / case_name
    downloads_dir = case_dir / "downloads"
    _reset_output_dir(case_dir)

    artifact_urls = _collect_artifact_urls(parsed_response.get("result_file_3ds"))
    assert artifact_urls, parsed_response

    download_manifest = _download_artifacts(artifact_urls, downloads_dir)
    assert download_manifest, parsed_response

    elapsed_seconds = round(finished_at_monotonic - started_at_monotonic, 3)
    metadata = {
        "case_name": case_name,
        "job_id": parsed_response.get("job_id"),
        "status": parsed_response.get("status"),
        "artifact_url_count": len(artifact_urls),
        "download_count": len(download_manifest),
        "elapsed_seconds": elapsed_seconds,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    (case_dir / "response.json").write_text(raw_response, encoding="utf-8")
    (case_dir / "downloads_manifest.json").write_text(
        json.dumps(download_manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (case_dir / "timing.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    summary_path = OUTPUT_ROOT / "timings.json"
    existing: dict[str, Any] = {}
    if summary_path.exists():
        existing = json.loads(summary_path.read_text(encoding="utf-8"))
    existing[case_name] = metadata
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def test_live_hunyuan3d_text_to_3d(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_live_hunyuan_ready(monkeypatch)

    started_at = time.perf_counter()
    raw_response = generate_hunyuan3d_model(
        None,
        text_prompt="A stylized ceramic teapot with a lid, game-ready product render.",
    )
    finished_at = time.perf_counter()

    parsed = _assert_successful_hunyuan_response(raw_response)
    _persist_case_result(
        case_name="text_to_3d",
        raw_response=raw_response,
        parsed_response=parsed,
        started_at_monotonic=started_at,
        finished_at_monotonic=finished_at,
    )


def test_live_hunyuan3d_image_to_3d(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_live_hunyuan_ready(monkeypatch)

    if not IMAGE_TO_3D_DEMO_PATH.exists():
        pytest.skip(f"Missing image fixture: {IMAGE_TO_3D_DEMO_PATH}")

    started_at = time.perf_counter()
    raw_response = generate_hunyuan3d_model(
        None,
        input_image_url=str(IMAGE_TO_3D_DEMO_PATH),
    )
    finished_at = time.perf_counter()

    parsed = _assert_successful_hunyuan_response(raw_response)
    _persist_case_result(
        case_name="image_to_3d",
        raw_response=raw_response,
        parsed_response=parsed,
        started_at_monotonic=started_at,
        finished_at_monotonic=finished_at,
    )
