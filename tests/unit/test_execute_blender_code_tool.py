from __future__ import annotations

from mcp_server.tools.base import execute_blender_code


def test_execute_blender_code_uses_transactional_defaults(monkeypatch):
    captured: dict[str, object] = {}

    class FakeBlender:
        def send_command(self, command_type: str, params: dict[str, object]):
            captured["command_type"] = command_type
            captured["params"] = params
            return {
                "executed": True,
                "committed": True,
                "rolled_back": False,
                "result": "ok",
                "scene_guard": {"passed": True, "violations": []},
            }

    monkeypatch.setattr(
        "mcp_server.tools.base.runtime.get_blender_connection",
        lambda _logger: FakeBlender(),
    )

    result = execute_blender_code(ctx=None, code="print('hello')")

    assert captured["command_type"] == "execute_code"
    assert captured["params"] == {
        "code": "print('hello')",
        "safe_mode": True,
        "rollback_on_guard_fail": True,
        "validate_scene": True,
    }
    assert "Status: success" in result
    assert "Mode: execute_code(transactional)" in result
    assert '"committed": true' in result


def test_execute_blender_code_forwards_custom_options(monkeypatch):
    captured: dict[str, object] = {}

    class FakeBlender:
        def send_command(self, command_type: str, params: dict[str, object]):
            captured["command_type"] = command_type
            captured["params"] = params
            return {"executed": True, "committed": True, "rolled_back": False, "result": ""}

    monkeypatch.setattr(
        "mcp_server.tools.base.runtime.get_blender_connection",
        lambda _logger: FakeBlender(),
    )

    execute_blender_code(
        ctx=None,
        code="print('hello')",
        safe_mode=False,
        rollback_on_guard_fail=False,
        validate_scene=False,
    )

    assert captured["command_type"] == "execute_code"
    assert captured["params"] == {
        "code": "print('hello')",
        "safe_mode": False,
        "rollback_on_guard_fail": False,
        "validate_scene": False,
    }


def test_execute_blender_code_returns_error_status_on_failure(monkeypatch):
    class FakeBlender:
        def send_command(self, command_type: str, params: dict[str, object]):
            _ = command_type
            _ = params
            raise RuntimeError("guard rollback failed")

    monkeypatch.setattr(
        "mcp_server.tools.base.runtime.get_blender_connection",
        lambda _logger: FakeBlender(),
    )

    result = execute_blender_code(ctx=None, code="print('hello')")

    assert "Status: error" in result
    assert "guard rollback failed" in result
