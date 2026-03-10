import io
import json
import subprocess
import zipfile
from pathlib import Path

import mcp_server.tools.asset_gen.sam_reconstruct as sam_reconstruct


class DummyResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data=None,
        text: str = "",
        content: bytes = b"",
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text
        self.content = content

    def json(self):
        if isinstance(self._json_data, Exception):
            raise self._json_data
        return self._json_data

    def iter_content(self, chunk_size: int = 0):
        del chunk_size
        if self.content:
            yield self.content


def _make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buf.getvalue()


def test_reconstruct_full_scene_success(monkeypatch, tmp_path):
    image_path = tmp_path / "input.png"
    image_path.write_bytes(b"png")
    output_dir = tmp_path / "output"
    status_calls = {"count": 0}

    def fake_post(url, files=None, data=None, timeout=None):
        del files, timeout
        assert url.endswith("/v1/jobs/reconstruct-scene")
        assert json.loads(data["options"])["seed"] == 42
        return DummyResponse(json_data={"job_id": "job-123"})

    def fake_get(url, params=None, timeout=None, stream=False):
        del timeout, stream
        if url.endswith("/v1/jobs/job-123"):
            status_calls["count"] += 1
            if status_calls["count"] == 1:
                return DummyResponse(json_data={"job_id": "job-123", "status": "running"})
            return DummyResponse(
                json_data={
                    "job_id": "job-123",
                    "status": "succeeded",
                    "result": {"num_masks": 5, "errors": [{"object": "chair", "error": "minor"}]},
                }
            )
        if url.endswith("/v1/jobs/job-123/artifacts/download"):
            assert params == {"extensions": "glb,json"}
            return DummyResponse(
                content=_make_zip(
                    {
                        "object_transforms.json": json.dumps(
                            [{"glb_path": "/mnt/afs/tool-server/job-123/chair.glb"}]
                        ).encode("utf-8"),
                        "chair.glb": b"glb-bytes",
                        "chair.json": b"{}",
                    }
                )
            )
        raise AssertionError(f"Unexpected GET URL: {url}")

    def fake_run(cmd, **kwargs):
        del kwargs
        transforms_path = Path(cmd[-2])
        payload = json.loads(transforms_path.read_text(encoding="utf-8"))
        assert payload == [{"glb_path": str(output_dir / "chair.glb")}]
        Path(cmd[-1]).write_bytes(b"blend")

    monkeypatch.setattr(sam_reconstruct.requests, "post", fake_post)
    monkeypatch.setattr(sam_reconstruct.requests, "get", fake_get)
    monkeypatch.setattr(sam_reconstruct.time, "sleep", lambda _: None)
    monkeypatch.setattr(sam_reconstruct.subprocess, "run", fake_run)

    result = sam_reconstruct.reconstruct_full_scene(
        None,
        str(image_path),
        output_dir=str(output_dir),
    )

    assert result["success"] is True
    assert result["job_id"] == "job-123"
    assert result["status"] == "succeeded"
    assert result["blend_file_path"] == str(output_dir / "scene.blend")
    assert result["num_objects"] == 1
    assert result["num_masks"] == 5
    assert result["partial_errors"] == [{"object": "chair", "error": "minor"}]
    assert result["recommended_next_tool"] == "import_blend_contents"
    assert "output_dir" not in result
    assert "glb_paths" not in result
    assert "json_paths" not in result
    assert {
        key
        for key in result
        if key.endswith("_path") or key.endswith("_paths") or key.endswith("_dir")
    } == {"blend_file_path"}


def test_reconstruct_full_scene_returns_failed_status(monkeypatch, tmp_path):
    image_path = tmp_path / "input.png"
    image_path.write_bytes(b"png")

    monkeypatch.setattr(
        sam_reconstruct.requests,
        "post",
        lambda *args, **kwargs: DummyResponse(json_data={"job_id": "job-456"}),
    )
    monkeypatch.setattr(
        sam_reconstruct.requests,
        "get",
        lambda *args, **kwargs: DummyResponse(
            json_data={"job_id": "job-456", "status": "failed", "message": "boom"}
        ),
    )
    monkeypatch.setattr(sam_reconstruct.time, "sleep", lambda _: None)

    result = sam_reconstruct.reconstruct_full_scene(None, str(image_path))

    assert result["success"] is False
    assert result["job_id"] == "job-456"
    assert result["status"] == "failed"
    assert result["status_payload"]["status"] == "failed"


def test_reconstruct_full_scene_times_out(monkeypatch, tmp_path):
    image_path = tmp_path / "input.png"
    image_path.write_bytes(b"png")
    time_values = iter([0.0, 0.1, 1.1])

    monkeypatch.setattr(
        sam_reconstruct.requests,
        "post",
        lambda *args, **kwargs: DummyResponse(json_data={"job_id": "job-timeout"}),
    )
    monkeypatch.setattr(
        sam_reconstruct.requests,
        "get",
        lambda *args, **kwargs: DummyResponse(
            json_data={"job_id": "job-timeout", "status": "running"}
        ),
    )
    monkeypatch.setattr(sam_reconstruct.time, "sleep", lambda _: None)
    monkeypatch.setattr(sam_reconstruct.time, "time", lambda: next(time_values))

    result = sam_reconstruct.reconstruct_full_scene(
        None,
        str(image_path),
        timeout_seconds=1,
        poll_interval_seconds=0.01,
    )

    assert result["success"] is False
    assert result["job_id"] == "job-timeout"
    assert result["status"] == "timeout"


def test_reconstruct_full_scene_requires_glb_artifact(monkeypatch, tmp_path):
    image_path = tmp_path / "input.png"
    image_path.write_bytes(b"png")

    monkeypatch.setattr(
        sam_reconstruct.requests,
        "post",
        lambda *args, **kwargs: DummyResponse(json_data={"job_id": "job-no-glb"}),
    )

    def fake_get(url, params=None, **kwargs):
        del kwargs
        if url.endswith("/v1/jobs/job-no-glb"):
            return DummyResponse(
                json_data={"job_id": "job-no-glb", "status": "succeeded", "result": {"num_masks": 1}}
            )
        if url.endswith("/v1/jobs/job-no-glb/artifacts/download"):
            assert params == {"extensions": "glb,json"}
            return DummyResponse(content=_make_zip({"object_transforms.json": b"[]"}))
        raise AssertionError(f"Unexpected GET URL: {url}")

    monkeypatch.setattr(sam_reconstruct.requests, "get", fake_get)
    monkeypatch.setattr(sam_reconstruct.time, "sleep", lambda _: None)

    result = sam_reconstruct.reconstruct_full_scene(None, str(image_path))

    assert result["success"] is False
    assert result["status"] == "validation_error"


def test_reconstruct_full_scene_reports_blender_failure(monkeypatch, tmp_path):
    image_path = tmp_path / "input.png"
    image_path.write_bytes(b"png")
    output_dir = tmp_path / "output"

    monkeypatch.setattr(
        sam_reconstruct.requests,
        "post",
        lambda *args, **kwargs: DummyResponse(json_data={"job_id": "job-blender"}),
    )

    def fake_get(url, params=None, **kwargs):
        del kwargs
        if url.endswith("/v1/jobs/job-blender"):
            return DummyResponse(
                json_data={"job_id": "job-blender", "status": "succeeded", "result": {"num_masks": 2}}
            )
        if url.endswith("/v1/jobs/job-blender/artifacts/download"):
            assert params == {"extensions": "glb,json"}
            return DummyResponse(
                content=_make_zip(
                    {
                        "object_transforms.json": json.dumps(
                            [{"glb_path": str(output_dir / "desk.glb")}]
                        ).encode("utf-8"),
                        "desk.glb": b"glb-bytes",
                    }
                )
            )
        raise AssertionError(f"Unexpected GET URL: {url}")

    monkeypatch.setattr(sam_reconstruct.requests, "get", fake_get)
    monkeypatch.setattr(sam_reconstruct.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        sam_reconstruct.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(returncode=1, cmd="blender")
        ),
    )

    result = sam_reconstruct.reconstruct_full_scene(
        None,
        str(image_path),
        output_dir=str(output_dir),
    )

    assert result["success"] is False
    assert result["status"] == "blender_error"


def test_reconstruct_full_scene_generates_fallback_transforms(monkeypatch, tmp_path):
    image_path = tmp_path / "input.png"
    image_path.write_bytes(b"png")
    output_dir = tmp_path / "output"

    monkeypatch.setattr(
        sam_reconstruct.requests,
        "post",
        lambda *args, **kwargs: DummyResponse(json_data={"job_id": "job-fallback"}),
    )

    def fake_get(url, params=None, **kwargs):
        del kwargs
        if url.endswith("/v1/jobs/job-fallback"):
            return DummyResponse(
                json_data={"job_id": "job-fallback", "status": "succeeded", "result": {"num_masks": 3}}
            )
        if url.endswith("/v1/jobs/job-fallback/artifacts/download"):
            assert params == {"extensions": "glb,json"}
            return DummyResponse(content=_make_zip({"lamp.glb": b"glb-bytes"}))
        raise AssertionError(f"Unexpected GET URL: {url}")

    def fake_run(cmd, **kwargs):
        del kwargs
        transforms_path = Path(cmd[-2])
        payload = json.loads(transforms_path.read_text(encoding="utf-8"))
        assert payload == [{"glb_path": str(output_dir / "lamp.glb")}]
        Path(cmd[-1]).write_bytes(b"blend")

    monkeypatch.setattr(sam_reconstruct.requests, "get", fake_get)
    monkeypatch.setattr(sam_reconstruct.time, "sleep", lambda _: None)
    monkeypatch.setattr(sam_reconstruct.subprocess, "run", fake_run)

    result = sam_reconstruct.reconstruct_full_scene(
        None,
        str(image_path),
        output_dir=str(output_dir),
    )

    assert result["success"] is True
    assert (output_dir / "object_transforms.json").exists()
    assert "json_paths" not in result
