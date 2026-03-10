from mcp_server.tools.asset_retrieval import sketchfab


def test_build_blender_import_code_does_not_raise_on_result_payload_dict() -> None:
    code = sketchfab._build_blender_import_code("/tmp/example.glb", 1.25)

    assert "result = dict(" in code
    assert 'import_path = "/tmp/example.glb"' in code
    assert "target_size = 1.25" in code
    assert "print(marker + json.dumps(result, ensure_ascii=False))" in code


def test_import_sketchfab_asset_uses_internal_delete_bypass(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeBlender:
        def send_command(self, command_type: str, params=None):
            captured["command_type"] = command_type
            captured["params"] = params or {}
            return {
                "result": 'MCP_SKETCHFAB_IMPORT_RESULT::{"success": true, "imported_objects": []}',
            }

    monkeypatch.setattr(sketchfab.runtime, "get_blender_connection", lambda _logger: FakeBlender())

    result = sketchfab._import_sketchfab_asset_into_blender(tmp_path / "example.glb", 1.25)

    assert captured["command_type"] == "execute_code"
    assert captured["params"]["allow_internal_object_delete"] is True
    assert result["success"] is True
