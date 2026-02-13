import logging

import pytest

from mcp_server import tool_registry


class FakeMCP:
    def __init__(self) -> None:
        self.enabled: list[str] = []

    def add_tool(self, func) -> None:
        self.enabled.append(func.__name__)


def _reset_registry_state(monkeypatch) -> None:
    monkeypatch.setattr(tool_registry, "_tools_registered", False)
    monkeypatch.setattr(tool_registry, "_enabled_tool_names", [])


def _configure_runtime(
    monkeypatch,
    *,
    mode: str = "headless",
    hunyuan: bool = False,
    rodin: bool = False,
    trellis2: bool = False,
    retrieval: bool = False,
    infinigen: bool = False,
    sketchfab: bool = False,
) -> None:
    monkeypatch.setattr(tool_registry.runtime, "get_blender_mode", lambda: mode)
    monkeypatch.setattr(tool_registry.runtime, "is_hunyuan_tool_enabled", lambda: hunyuan)
    monkeypatch.setattr(tool_registry.runtime, "is_rodin_tool_enabled", lambda: rodin)
    monkeypatch.setattr(tool_registry.runtime, "is_trellis2_tool_enabled", lambda: trellis2)
    monkeypatch.setattr(tool_registry.runtime, "is_retrieval_tool_enabled", lambda: retrieval)
    monkeypatch.setattr(tool_registry.runtime, "is_infinigen_tool_enabled", lambda: infinigen)
    monkeypatch.setattr(tool_registry.runtime, "is_sketchfab_tool_enabled", lambda: sketchfab)
    monkeypatch.setattr(
        tool_registry.runtime,
        "probe_conditional_services",
        lambda _logger: {"trellis2": True, "retrieval": True, "pcg_integrator": True},
    )


def test_register_mcp_tools_fails_on_hunyuan_trellis2_conflict(monkeypatch):
    _reset_registry_state(monkeypatch)
    _configure_runtime(monkeypatch, hunyuan=True, trellis2=True)

    with pytest.raises(RuntimeError, match="ENABLE_HUNYUAN and ENABLE_TRELLIS2"):
        tool_registry.register_mcp_tools(FakeMCP(), logging.getLogger(__name__))


def test_register_mcp_tools_fails_on_retrieval_sketchfab_conflict(monkeypatch):
    _reset_registry_state(monkeypatch)
    _configure_runtime(monkeypatch, retrieval=True, sketchfab=True)

    with pytest.raises(RuntimeError, match="ENABLE_RETRIEVAL and ENABLE_SKETCHFAB"):
        tool_registry.register_mcp_tools(FakeMCP(), logging.getLogger(__name__))


def test_register_mcp_tools_respects_retrieval_and_infinigen_switches(monkeypatch):
    _reset_registry_state(monkeypatch)
    _configure_runtime(monkeypatch, retrieval=False, infinigen=False)
    mcp = FakeMCP()

    enabled = tool_registry.register_mcp_tools(mcp, logging.getLogger(__name__))

    assert "search_3d_assets_by_text" not in enabled
    assert "import_retrieved_asset" not in enabled
    assert "get_infinigen_available_assets" not in enabled
    assert "generate_infinigen_assets" not in enabled
