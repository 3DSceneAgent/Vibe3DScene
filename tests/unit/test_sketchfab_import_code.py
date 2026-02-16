from mcp_server.tools.asset_retrieval import sketchfab


def test_build_blender_import_code_does_not_raise_on_result_payload_dict() -> None:
    code = sketchfab._build_blender_import_code("/tmp/example.glb", 1.25)

    assert "result = dict(" in code
    assert 'import_path = "/tmp/example.glb"' in code
    assert "target_size = 1.25" in code
    assert "print(marker + json.dumps(result, ensure_ascii=False))" in code
