import json

from mcp_server.tools import base


def test_delete_objects_tool_calls_blender_with_expected_payload(monkeypatch):
    calls: list[tuple[str, dict]] = []

    class FakeBlender:
        def send_command(self, command_type: str, params=None):
            calls.append((command_type, params or {}))
            return {
                "success": True,
                "mode": "cascade",
                "deleted": ["Parent", "Child"],
            }

    monkeypatch.setattr(base.runtime, "get_blender_connection", lambda _logger: FakeBlender())

    result = base.delete_objects(
        ctx=None,
        object_names=["Parent"],
        mode="cascade",
        strict=True,
        dry_run=False,
        ignore_missing=False,
    )

    assert calls == [
        (
            "delete_objects",
            {
                "object_names": ["Parent"],
                "mode": "cascade",
                "strict": True,
                "dry_run": False,
                "ignore_missing": False,
                "name_match_mode": "exact",
            },
        )
    ]
    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["deleted"] == ["Parent", "Child"]


def test_delete_objects_tool_supports_custom_name_match_mode(monkeypatch):
    calls: list[tuple[str, dict]] = []

    class FakeBlender:
        def send_command(self, command_type: str, params=None):
            calls.append((command_type, params or {}))
            return {"success": True, "deleted": ["Cup.001"]}

    monkeypatch.setattr(base.runtime, "get_blender_connection", lambda _logger: FakeBlender())

    base.delete_objects(
        ctx=None,
        object_names=["cup"],
        mode="cascade",
        name_match_mode="contains",
    )

    assert calls == [
        (
            "delete_objects",
            {
                "object_names": ["cup"],
                "mode": "cascade",
                "strict": True,
                "dry_run": False,
                "ignore_missing": False,
                "name_match_mode": "contains",
            },
        )
    ]


def test_delete_objects_tool_returns_error_message(monkeypatch):
    def fail_connection(_logger):
        raise RuntimeError("connection lost")

    monkeypatch.setattr(base.runtime, "get_blender_connection", fail_connection)

    result = base.delete_objects(ctx=None, object_names=["Missing"])

    assert result.startswith("Error deleting objects:")
    assert "connection lost" in result


def test_delete_objects_tool_accepts_comma_separated_string(monkeypatch):
    calls: list[tuple[str, dict]] = []

    class FakeBlender:
        def send_command(self, command_type: str, params=None):
            calls.append((command_type, params or {}))
            return {"success": True, "deleted": ["Lamp", "Camera"]}

    monkeypatch.setattr(base.runtime, "get_blender_connection", lambda _logger: FakeBlender())

    base.delete_objects(
        ctx=None,
        object_names="Lamp, Camera",
        mode="cascade",
    )

    assert calls == [
        (
            "delete_objects",
            {
                "object_names": ["Lamp", "Camera"],
                "mode": "cascade",
                "strict": True,
                "dry_run": False,
                "ignore_missing": False,
                "name_match_mode": "exact",
            },
        )
    ]
