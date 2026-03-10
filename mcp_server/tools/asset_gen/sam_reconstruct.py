from __future__ import annotations

import io
import json
import logging
import mimetypes
import os
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any

import requests
from mcp.server.fastmcp import Context

logger = logging.getLogger("BlenderMCPServer")

DEFAULT_SAM_HTTP_BASE_URL = "http://127.0.0.1:8004"
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_STORAGE_DIR = "/tmp/scene_agent_sam_reconstruct"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_GLB_IMPORT_SCRIPT = (
    PROJECT_ROOT / "tool_servers" / "SAMServer" / "mcp_tools" / "blender" / "glb_import.py"
)


class ReconstructToolError(Exception):
    def __init__(
        self,
        *,
        status: str,
        message: str,
        job_id: str | None = None,
        status_payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.job_id = job_id
        self.status_payload = status_payload


def reconstruct_full_scene(
    ctx: Context,
    input_image_path: str,
    output_dir: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    min_area_threshold: int = 100,
    max_masks: int = 15,
    naming_mode: str = "index",
    vlm_model: str = "gpt-4o",
    seed: int = 42,
) -> dict[str, Any]:
    """Reconstruct a scene and return a local .blend path ready for import_blend_contents()."""
    del ctx

    job_id: str | None = None
    status_payload: dict[str, Any] | None = None

    try:
        image_path = _validate_input_image(input_image_path)
        resolved_timeout = (
            _env_int("SAM_RECONSTRUCT_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
            if timeout_seconds == DEFAULT_TIMEOUT_SECONDS
            else int(timeout_seconds)
        )
        resolved_poll_interval = (
            _env_float("SAM_RECONSTRUCT_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS)
            if poll_interval_seconds == DEFAULT_POLL_INTERVAL_SECONDS
            else float(poll_interval_seconds)
        )
        if resolved_timeout <= 0:
            raise ReconstructToolError(
                status="validation_error",
                message="timeout_seconds must be greater than 0.",
            )
        if resolved_poll_interval <= 0:
            raise ReconstructToolError(
                status="validation_error",
                message="poll_interval_seconds must be greater than 0.",
            )

        sam_http_base_url = _sam_http_base_url()
        options_payload = {
            "min_area_threshold": int(min_area_threshold),
            "max_masks": int(max_masks),
            "naming_mode": str(naming_mode),
            "vlm_model": str(vlm_model),
            "seed": int(seed),
        }

        job_id = _submit_job(
            image_path=image_path,
            base_url=sam_http_base_url,
            options_payload=options_payload,
            timeout_seconds=resolved_timeout,
        )
        status_payload = _wait_for_job(
            job_id=job_id,
            base_url=sam_http_base_url,
            timeout_seconds=resolved_timeout,
            poll_interval_seconds=resolved_poll_interval,
        )

        final_status = str(status_payload.get("status") or "").strip().lower()
        if final_status != "succeeded":
            raise ReconstructToolError(
                status="failed",
                message=(
                    f"Reconstruction job did not succeed. Final status: {final_status or '<missing>'}."
                ),
                job_id=job_id,
                status_payload=status_payload,
            )

        output_root = _resolve_output_dir(output_dir, job_id)
        _download_artifacts(job_id=job_id, base_url=sam_http_base_url, output_dir=output_root)

        glb_paths = sorted(str(path) for path in output_root.glob("*.glb"))
        if not glb_paths:
            raise ReconstructToolError(
                status="validation_error",
                message="Reconstruction artifacts did not include any .glb files.",
                job_id=job_id,
                status_payload=status_payload,
            )

        transforms_path = output_root / "object_transforms.json"
        if not transforms_path.exists():
            transforms_path.write_text(
                json.dumps([{"glb_path": path} for path in glb_paths], indent=2),
                encoding="utf-8",
            )
        else:
            _rewrite_transforms_glb_paths(transforms_path=transforms_path, glb_paths=glb_paths)

        blend_file_path = output_root / "scene.blend"
        _run_blender_import(
            transforms_path=transforms_path,
            blend_file_path=blend_file_path,
            output_dir=output_root,
        )

        result_payload = status_payload.get("result")
        result_block = result_payload if isinstance(result_payload, dict) else {}
        partial_errors = result_block.get("errors")
        if not isinstance(partial_errors, list):
            partial_errors = []
        num_masks_raw = result_block.get("num_masks", 0)
        try:
            num_masks = int(num_masks_raw)
        except (TypeError, ValueError):
            num_masks = 0

        # Keep downloaded artifacts local for Blender assembly and debugging, but expose only the
        # MCP-hosted .blend path that the next tool can consume directly.
        return {
            "success": True,
            "job_id": job_id,
            "status": "succeeded",
            "blend_file_path": str(blend_file_path),
            "num_objects": len(glb_paths),
            "num_masks": num_masks,
            "partial_errors": partial_errors,
            "recommended_next_tool": "import_blend_contents",
            "recommended_next_action": (
                "Call import_blend_contents(blend_file_path=...) to merge the "
                "reconstructed scene into the current scene."
            ),
        }
    except ReconstructToolError as exc:
        return {
            "success": False,
            "job_id": exc.job_id or job_id,
            "status": exc.status,
            "error": exc.message,
            "status_payload": exc.status_payload or status_payload,
        }
    except requests.RequestException as exc:
        logger.error("SAM reconstruct request failed: %s", exc)
        return {
            "success": False,
            "job_id": job_id,
            "status": "request_error",
            "error": f"SAM reconstruct request failed: {str(exc)}",
            "status_payload": status_payload,
        }
    except Exception as exc:
        logger.exception("Unexpected error in reconstruct_full_scene")
        return {
            "success": False,
            "job_id": job_id,
            "status": "request_error",
            "error": f"Unexpected error: {str(exc)}",
            "status_payload": status_payload,
        }


def _validate_input_image(input_image_path: str) -> Path:
    candidate = Path(str(input_image_path or "")).expanduser()
    if not candidate.exists():
        raise ReconstructToolError(
            status="validation_error",
            message=f"input_image_path does not exist: {candidate}",
        )
    if not candidate.is_file():
        raise ReconstructToolError(
            status="validation_error",
            message=f"input_image_path is not a file: {candidate}",
        )
    return candidate.resolve()


def _resolve_output_dir(output_dir: str | None, job_id: str) -> Path:
    if output_dir:
        candidate = Path(output_dir).expanduser()
    else:
        storage_root = Path(os.getenv("SAM_RECONSTRUCT_STORAGE_DIR", DEFAULT_STORAGE_DIR)).expanduser()
        candidate = storage_root / job_id
    if candidate.exists() and not candidate.is_dir():
        raise ReconstructToolError(
            status="validation_error",
            message=f"output_dir is not a directory: {candidate}",
            job_id=job_id,
        )
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate.resolve()


def _sam_http_base_url() -> str:
    return (os.getenv("SAM_HTTP_BASE_URL", DEFAULT_SAM_HTTP_BASE_URL).strip() or DEFAULT_SAM_HTTP_BASE_URL).rstrip("/")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _submit_job(
    *,
    image_path: Path,
    base_url: str,
    options_payload: dict[str, Any],
    timeout_seconds: int,
) -> str:
    content_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    request_timeout = max(30, min(120, timeout_seconds))
    with image_path.open("rb") as handle:
        response = requests.post(
            f"{base_url}/v1/jobs/reconstruct-scene",
            files={"image": (image_path.name, handle, content_type)},
            data={"options": json.dumps(options_payload, ensure_ascii=False)},
            timeout=request_timeout,
        )

    payload = _parse_response_json(response, "submit reconstruct-scene job")
    if response.status_code != 200:
        raise ReconstructToolError(
            status="request_error",
            message=(
                "Failed to submit reconstruct-scene job: "
                f"{response.status_code} {response.text[:400]}"
            ),
            status_payload=payload,
        )

    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise ReconstructToolError(
            status="request_error",
            message=f"Submit response missing job_id: {payload}",
            status_payload=payload,
        )
    return job_id.strip()


def _wait_for_job(
    *,
    job_id: str,
    base_url: str,
    timeout_seconds: int,
    poll_interval_seconds: float,
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_payload: dict[str, Any] | None = None
    while time.time() < deadline:
        response = requests.get(f"{base_url}/v1/jobs/{job_id}", timeout=30)
        payload = _parse_response_json(response, f"poll job {job_id}")
        last_payload = payload
        if response.status_code != 200:
            raise ReconstructToolError(
                status="request_error",
                message=(
                    f"Failed to poll job {job_id}: {response.status_code} {response.text[:400]}"
                ),
                job_id=job_id,
                status_payload=payload,
            )

        current_status = str(payload.get("status") or "").strip().lower()
        if current_status in {"succeeded", "failed"}:
            return payload
        time.sleep(poll_interval_seconds)

    raise ReconstructToolError(
        status="timeout",
        message=f"Reconstruction job timed out after {timeout_seconds} seconds.",
        job_id=job_id,
        status_payload=last_payload,
    )


def _download_artifacts(*, job_id: str, base_url: str, output_dir: Path) -> None:
    archive_response = requests.get(
        f"{base_url}/v1/jobs/{job_id}/artifacts/download",
        params={"extensions": "glb,json"},
        timeout=300,
    )
    if archive_response.status_code == 200:
        try:
            with zipfile.ZipFile(io.BytesIO(archive_response.content)) as archive:
                archive.extractall(output_dir)
            return
        except zipfile.BadZipFile:
            logger.warning("Artifact archive for job %s was not a valid zip, falling back", job_id)

    _download_artifacts_individually(job_id=job_id, base_url=base_url, output_dir=output_dir)


def _download_artifacts_individually(*, job_id: str, base_url: str, output_dir: Path) -> None:
    response = requests.get(
        f"{base_url}/v1/jobs/{job_id}/artifacts",
        params={"extensions": "glb,json"},
        timeout=30,
    )
    payload = _parse_response_json(response, f"list artifacts for {job_id}")
    if response.status_code != 200:
        raise ReconstructToolError(
            status="request_error",
            message=(
                f"Failed to list artifacts for job {job_id}: {response.status_code} "
                f"{response.text[:400]}"
            ),
            job_id=job_id,
            status_payload=payload,
        )

    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise ReconstructToolError(
            status="validation_error",
            message=f"Artifact list response was invalid: {payload}",
            job_id=job_id,
            status_payload=payload,
        )

    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        name = artifact.get("name")
        if not isinstance(name, str) or not name:
            continue
        download_response = requests.get(
            f"{base_url}/v1/jobs/{job_id}/artifacts/{name}",
            timeout=300,
            stream=True,
        )
        if download_response.status_code != 200:
            raise ReconstructToolError(
                status="request_error",
                message=(
                    f"Failed to download artifact {name} for job {job_id}: "
                    f"{download_response.status_code} {download_response.text[:400]}"
                ),
                job_id=job_id,
            )
        target_path = output_dir / name
        with target_path.open("wb") as handle:
            for chunk in download_response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)


def _rewrite_transforms_glb_paths(*, transforms_path: Path, glb_paths: list[str]) -> None:
    local_glb_map = {Path(path).name: path for path in glb_paths}
    if not local_glb_map:
        return

    try:
        payload = json.loads(transforms_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Failed to read transforms file %s for GLB path rewrite: %s", transforms_path, exc)
        return

    if not isinstance(payload, list):
        logger.warning("Transforms file %s did not contain a list; skipping GLB path rewrite", transforms_path)
        return

    rewritten = False
    for item in payload:
        if not isinstance(item, dict):
            continue
        for key in ("glb_path", "glb"):
            raw_path = item.get(key)
            if not isinstance(raw_path, str) or not raw_path.strip():
                continue
            local_path = local_glb_map.get(Path(raw_path).name)
            if local_path and raw_path != local_path:
                item[key] = local_path
                rewritten = True

    if rewritten:
        transforms_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _run_blender_import(*, transforms_path: Path, blend_file_path: Path, output_dir: Path) -> None:
    blender_cmd = (
        os.getenv("SAM_RECONSTRUCT_BLENDER_CMD", "").strip()
        or os.getenv("BLENDER_HEADLESS_CMD", "").strip()
        or "blender"
    )
    import_script = DEFAULT_GLB_IMPORT_SCRIPT
    if not import_script.exists():
        raise ReconstructToolError(
            status="validation_error",
            message=f"GLB import script not found: {import_script}",
        )

    log_path = output_dir / "blender_import.log"
    try:
        with log_path.open("w", encoding="utf-8") as log_handle:
            subprocess.run(
                [
                    blender_cmd,
                    "-b",
                    "-P",
                    str(import_script),
                    "--",
                    str(transforms_path),
                    str(blend_file_path),
                ],
                cwd=PROJECT_ROOT,
                check=True,
                text=True,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
    except subprocess.CalledProcessError as exc:
        raise ReconstructToolError(
            status="blender_error",
            message=f"Blender import failed: {str(exc)}. See log: {log_path}",
        ) from exc
    except OSError as exc:
        raise ReconstructToolError(
            status="blender_error",
            message=f"Failed to start Blender import command '{blender_cmd}': {str(exc)}",
        ) from exc


def _parse_response_json(response: requests.Response, action: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ReconstructToolError(
            status="request_error",
            message=f"Failed to {action}: response was not valid JSON.",
        ) from exc
    if isinstance(payload, dict):
        return payload
    raise ReconstructToolError(
        status="request_error",
        message=f"Failed to {action}: response JSON was not an object.",
    )
