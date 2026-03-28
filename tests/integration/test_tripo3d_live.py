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

from mcp_server.tools.asset_gen.tripo import generate_tripo3d_model
from scene_agent.env import load_project_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
IMAGE_TO_3D_DEMO_PATH = PROJECT_ROOT / "assets" / "example_images" / "typical_humanoid_mech.png"
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "tripo3d_live"
SUCCESS_STATUSES = {"success"}


def _ensure_live_tripo_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    load_project_dotenv(override=False)

    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Set RUN_INTEGRATION=1 to run live Tripo integration tests.")

    monkeypatch.setenv("BLENDER_MODE", "headless")
    monkeypatch.setenv("ENABLE_TRIPO", "true")

    if not os.getenv("TRIPO_API_KEY", "").strip():
        pytest.skip("Set TRIPO_API_KEY to run live Tripo integration tests.")


def _response_contains_artifact(value: Any) -> bool:
    if isinstance(value, str):
        lowered = value.lower()
        return lowered.startswith(("http://", "https://")) or lowered.endswith(
            (".glb", ".gltf", ".fbx", ".obj", ".png", ".jpg", ".jpeg", ".webp")
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


def _assert_successful_tripo_response(raw_response: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        pytest.fail(
            "Tripo response is not valid JSON. "
            f"Decode error: {exc}. Raw response: {raw_response}"
        )

    assert isinstance(parsed, dict), f"Expected JSON object response, got: {parsed!r}"
    assert "error" not in parsed, (
        "Tripo generation returned an error payload.\n"
        f"{json.dumps(parsed, indent=2, ensure_ascii=False)}"
    )

    task_id = parsed.get("task_id")
    assert isinstance(task_id, str) and task_id.strip(), parsed

    status = str(parsed.get("status", "")).lower()
    assert status in SUCCESS_STATUSES, parsed

    preferred_model_asset = parsed.get("preferred_model_asset")
    assert isinstance(preferred_model_asset, dict), parsed
    assert isinstance(preferred_model_asset.get("url"), str) and preferred_model_asset["url"], parsed

    normalized_assets = parsed.get("normalized_assets")
    assert isinstance(normalized_assets, list) and normalized_assets, parsed
    assert _response_contains_artifact(normalized_assets), parsed

    response = parsed.get("response")
    assert isinstance(response, dict), parsed
    assert isinstance(response.get("output"), dict), parsed
    return parsed


def _collect_artifact_urls(parsed_response: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
        if url.startswith(("http://", "https://")) and url not in seen:
            seen.add(url)
            urls.append(url)

    preferred_model_asset = parsed_response.get("preferred_model_asset")
    if isinstance(preferred_model_asset, dict):
        preferred_url = preferred_model_asset.get("url")
        if isinstance(preferred_url, str):
            _add(preferred_url)

    normalized_assets = parsed_response.get("normalized_assets")
    if isinstance(normalized_assets, list):
        for asset in normalized_assets:
            if not isinstance(asset, dict):
                continue
            url = asset.get("url")
            if isinstance(url, str):
                _add(url)

    preview_url = parsed_response.get("preview_image_url")
    if isinstance(preview_url, str):
        _add(preview_url)

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

    artifact_urls = _collect_artifact_urls(parsed_response)
    assert artifact_urls, parsed_response

    download_manifest = _download_artifacts(artifact_urls, downloads_dir)
    assert download_manifest, parsed_response

    elapsed_seconds = round(finished_at_monotonic - started_at_monotonic, 3)
    metadata = {
        "case_name": case_name,
        "task_id": parsed_response.get("task_id"),
        "status": parsed_response.get("status"),
        "model_version": parsed_response.get("model_version"),
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


def test_live_tripo3d_text_to_3d(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_live_tripo_ready(monkeypatch)

    started_at = time.perf_counter()
    raw_response = generate_tripo3d_model(
        None,
        text_prompt="A stylized ceramic teapot with a lid, game-ready product render.",
    )
    finished_at = time.perf_counter()

    parsed = _assert_successful_tripo_response(raw_response)
    _persist_case_result(
        case_name="text_to_3d",
        raw_response=raw_response,
        parsed_response=parsed,
        started_at_monotonic=started_at,
        finished_at_monotonic=finished_at,
    )


def test_live_tripo3d_image_to_3d(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_live_tripo_ready(monkeypatch)

    if not IMAGE_TO_3D_DEMO_PATH.exists():
        pytest.skip(f"Missing image fixture: {IMAGE_TO_3D_DEMO_PATH}")

    started_at = time.perf_counter()
    raw_response = generate_tripo3d_model(
        None,
        input_image_url=str(IMAGE_TO_3D_DEMO_PATH),
    )
    finished_at = time.perf_counter()

    parsed = _assert_successful_tripo_response(raw_response)
    _persist_case_result(
        case_name="image_to_3d",
        raw_response=raw_response,
        parsed_response=parsed,
        started_at_monotonic=started_at,
        finished_at_monotonic=finished_at,
    )
