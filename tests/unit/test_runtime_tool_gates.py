from mcp_server import runtime


def test_rodin_gate_enabled_in_local_client_mode(monkeypatch):
    monkeypatch.setenv("BLENDER_MODE", "local-client")
    monkeypatch.setenv("ENABLE_RODIN", "true")

    assert runtime.is_rodin_tool_enabled() is True


def test_rodin_gate_enabled_in_headless_mode(monkeypatch):
    monkeypatch.setenv("BLENDER_MODE", "headless")
    monkeypatch.setenv("ENABLE_RODIN", "1")

    assert runtime.is_rodin_tool_enabled() is True


def test_rodin_gate_disabled_outside_supported_modes(monkeypatch):
    monkeypatch.setenv("BLENDER_MODE", "other")
    monkeypatch.setenv("ENABLE_RODIN", "true")

    assert runtime.is_rodin_tool_enabled() is False
