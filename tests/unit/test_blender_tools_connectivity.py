import asyncio
from types import SimpleNamespace

import pytest

from scene_agent.tools import blender_tools


class DummyProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode

    def poll(self):
        return self.returncode


class DummyProbeTool:
    name = "get_scene_info"

    def __init__(self, responses):
        self._responses = list(responses)
        self._last = self._responses[-1] if self._responses else "{\"objects\": []}"

    async def ainvoke(self, _payload):
        if self._responses:
            value = self._responses.pop(0)
            self._last = value
        else:
            value = self._last

        if isinstance(value, Exception):
            raise value
        return value


def test_extract_probe_error_accepts_scene_json():
    assert blender_tools._extract_probe_error("{\"objects\": []}") is None


def test_extract_probe_error_rejects_connection_error_text():
    error = blender_tools._extract_probe_error("Error getting scene info: Could not connect to Blender.")
    assert isinstance(error, str)
    assert "could not connect to blender" in error.lower()


def test_verify_mcp_blender_connectivity_passes_after_retry():
    probe_tool = DummyProbeTool(
        [
            "Error getting scene info: Could not connect to Blender.",
            "{\"objects\": []}",
        ]
    )
    asyncio.run(
        blender_tools._verify_mcp_blender_connectivity(
            [probe_tool],
            timeout_seconds=1.0,
        )
    )


def test_verify_mcp_blender_connectivity_fails_when_probe_never_ready():
    probe_tool = DummyProbeTool(["Error getting scene info: Could not connect to Blender."])
    with pytest.raises(Exception, match="cannot access Blender through MCP tool"):
        asyncio.run(
            blender_tools._verify_mcp_blender_connectivity(
                [probe_tool],
                timeout_seconds=0.3,
            )
        )


def test_assert_headless_runtime_processes_requires_both_alive():
    session = SimpleNamespace(
        process=DummyProcess(returncode=None),
        mcp_process=DummyProcess(returncode=None),
        log_path=None,
        mcp_log_path=None,
    )
    blender_tools._assert_headless_runtime_processes(session)

    session.process = DummyProcess(returncode=1)
    with pytest.raises(Exception, match="Headless Blender process exited unexpectedly"):
        blender_tools._assert_headless_runtime_processes(session)

