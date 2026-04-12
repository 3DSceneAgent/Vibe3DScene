from mcp_server.tools import base


def test_import_glb_model_handles_structured_dict_response(monkeypatch):
    calls: list[tuple[str, dict]] = []

    class FakeBlender:
        def send_command(self, command_type: str, params=None):
            calls.append((command_type, params or {}))
            return {
                "success": True,
                "imported_objects": ["Table_A"],
                "packed_images": ["table_basecolor.png"],
                "bounding_box": {"min": [0, 0, 0], "max": [1, 1, 1]},
            }

    monkeypatch.setattr(base.runtime, "get_blender_connection", lambda _logger: FakeBlender())

    result = base.import_glb_model(
        ctx=None,
        model_url="https://example.com/table.glb",
        object_name="Table_A",
    )

    assert calls == [
        (
            "import_glb_model",
            {
                "model_url": "https://example.com/table.glb",
                "object_name": "Table_A",
            },
        )
    ]
    assert "Successfully imported model" in result
    assert "Imported 1 object(s): Table_A" in result
    assert "Packed 1 texture image(s) into the Blender scene." in result
    assert "Bounding box: min=[0, 0, 0], max=[1, 1, 1]" in result


def test_import_glb_model_handles_legacy_list_response(monkeypatch):
    class FakeBlender:
        def send_command(self, command_type: str, params=None):
            _ = (command_type, params)
            return ["Table_A", "Table_A.001"]

    monkeypatch.setattr(base.runtime, "get_blender_connection", lambda _logger: FakeBlender())

    result = base.import_glb_model(
        ctx=None,
        model_url="https://example.com/table.glb",
        object_name="Table_A",
    )

    assert "Successfully imported model" in result
    assert "Imported 2 object(s): Table_A, Table_A.001" in result


def test_import_glb_model_handles_structured_list_bbox_response(monkeypatch):
    class FakeBlender:
        def send_command(self, command_type: str, params=None):
            _ = (command_type, params)
            return {
                "success": True,
                "imported_objects": ["Dragon"],
                "bounding_box": [[0, 0, 0], [1, 2, 3]],
            }

    monkeypatch.setattr(base.runtime, "get_blender_connection", lambda _logger: FakeBlender())

    result = base.import_glb_model(
        ctx=None,
        model_url="https://example.com/dragon.glb",
        object_name="Dragon",
    )

    assert "Successfully imported model" in result
    assert "Imported 1 object(s): Dragon" in result
    assert "Bounding box: min=[0, 0, 0], max=[1, 2, 3]" in result


def test_import_glb_model_handles_json_string_list_response(monkeypatch):
    class FakeBlender:
        def send_command(self, command_type: str, params=None):
            _ = (command_type, params)
            return "[\"Desk\", \"Desk.001\"]"

    monkeypatch.setattr(base.runtime, "get_blender_connection", lambda _logger: FakeBlender())

    result = base.import_glb_model(
        ctx=None,
        model_url="https://example.com/desk.glb",
        object_name="Desk",
    )

    assert "Successfully imported model" in result
    assert "Imported 2 object(s): Desk, Desk.001" in result


def test_import_glb_model_reports_scale_normalization_summary(monkeypatch):
    class FakeBlender:
        def send_command(self, command_type: str, params=None):
            _ = (command_type, params)
            return {
                "success": True,
                "imported_objects": ["TinyAsset"],
                "bounding_box": {"min": [-0.5, -0.1, -0.1], "max": [0.5, 0.1, 0.1]},
                "scale_normalization": {
                    "applied": True,
                    "reason": "too_small",
                    "original_max_dimension_m": 0.05,
                    "target_max_dimension_m": 1.0,
                    "normalized_max_dimension_m": 1.0,
                    "scale_factor": 20.0,
                    "applied_root_objects": ["TinyAsset"],
                    "normalized_bounding_box": {
                        "min": [-0.5, -0.1, -0.1],
                        "max": [0.5, 0.1, 0.1],
                    },
                },
            }

    monkeypatch.setattr(base.runtime, "get_blender_connection", lambda _logger: FakeBlender())

    result = base.import_glb_model(
        ctx=None,
        model_url="https://example.com/tiny.glb",
        object_name="TinyAsset",
    )

    assert "Scale normalization: applied (reason=too_small)" in result
    assert "Original max dimension (m): 0.050000" in result
    assert "Target max dimension (m): 1.000000" in result
    assert "Normalized max dimension (m): 1.000000" in result
    assert "Scale factor: 20.000000" in result
    assert "Scaled root objects: TinyAsset" in result
