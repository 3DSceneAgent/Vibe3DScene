from __future__ import annotations

import pytest
from mcp.server.fastmcp import Image

from mcp_server.tools import base


class _FakeBlenderSuccess:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def send_command(self, command_type: str, params=None):
        payload = dict(params or {})
        self.calls.append((command_type, payload))
        output_path = payload.get("filepath")
        if output_path:
            with open(output_path, "wb") as handle:
                handle.write(b"fake_png_bytes")
        return {"success": True, "filepath": output_path}


class _FakeBlenderError:
    def send_command(self, command_type: str, params=None):
        _ = (command_type, params)
        return {"error": "viewport capture unavailable"}


def test_get_viewport_screenshot_returns_image_and_cleans_temp(monkeypatch, tmp_path):
    fake_blender = _FakeBlenderSuccess()
    monkeypatch.setattr(base.runtime, "get_blender_mode", lambda: "local-client")
    monkeypatch.setattr(base.runtime, "get_blender_connection", lambda _logger: fake_blender)
    monkeypatch.setattr(base.tempfile, "gettempdir", lambda: str(tmp_path))

    image = base.get_viewport_screenshot(ctx=None, max_size=640)

    assert isinstance(image, Image)
    assert getattr(image, "data", b"") == b"fake_png_bytes"

    assert len(fake_blender.calls) == 1
    command_type, payload = fake_blender.calls[0]
    assert command_type == "get_viewport_screenshot"
    assert payload["max_size"] == 640
    assert payload["format"] == "png"
    assert payload["filepath"].startswith(str(tmp_path))

    remaining = [path for path in tmp_path.iterdir() if path.is_file()]
    assert remaining == []


def test_get_viewport_screenshot_rejects_headless_mode(monkeypatch):
    monkeypatch.setattr(base.runtime, "get_blender_mode", lambda: "headless")

    with pytest.raises(Exception, match="only available in BLENDER_MODE=local-client"):
        base.get_viewport_screenshot(ctx=None, max_size=800)


def test_get_viewport_screenshot_raises_when_blender_reports_error(monkeypatch, tmp_path):
    monkeypatch.setattr(base.runtime, "get_blender_mode", lambda: "local-client")
    monkeypatch.setattr(base.runtime, "get_blender_connection", lambda _logger: _FakeBlenderError())
    monkeypatch.setattr(base.tempfile, "gettempdir", lambda: str(tmp_path))

    with pytest.raises(Exception, match="viewport capture unavailable"):
        base.get_viewport_screenshot(ctx=None, max_size=800)

    # No stray screenshot files should remain after failure paths.
    assert not any(path.is_file() for path in tmp_path.iterdir())
