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


def test_sam_reconstruct_gate_enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_SAM_RECONSTRUCT", "true")

    assert runtime.is_sam_reconstruct_tool_enabled() is True


def test_sam_reconstruct_gate_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_SAM_RECONSTRUCT", raising=False)

    assert runtime.is_sam_reconstruct_tool_enabled() is False
