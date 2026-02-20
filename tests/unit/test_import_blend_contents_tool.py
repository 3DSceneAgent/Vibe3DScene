from mcp_server.tools import base


def test_import_blend_contents_handles_structured_dict_response(monkeypatch):
    calls: list[tuple[str, dict]] = []

    class FakeBlender:
        def send_command(self, command_type: str, params=None):
            calls.append((command_type, params or {}))
            return {
                "success": True,
                "blend_file_path": "/tmp/infinigen/Pebbles.blend",
                "import_mode": "auto",
                "link": False,
                "imported_collections": ["pebbles_generator"],
                "linked_scene_collections": ["pebbles_generator"],
                "imported_objects": [],
                "linked_scene_objects": [],
                "requested_collections_missing": [],
                "requested_objects_missing": [],
            }

    monkeypatch.setattr(base.runtime, "get_blender_connection", lambda _logger: FakeBlender())

    result = base.import_blend_contents(
        ctx=None,
        blend_file_path="/tmp/infinigen/Pebbles.blend",
    )

    assert calls == [
        (
            "import_blend_contents",
            {
                "blend_file_path": "/tmp/infinigen/Pebbles.blend",
                "import_mode": "auto",
                "collection_names": [],
                "object_names": [],
                "link": False,
            },
        )
    ]
    assert "Successfully imported blend file" in result
    assert "Imported collections: 1 (pebbles_generator)" in result


def test_import_blend_contents_normalizes_name_inputs(monkeypatch):
    calls: list[tuple[str, dict]] = []

    class FakeBlender:
        def send_command(self, command_type: str, params=None):
            calls.append((command_type, params or {}))
            return {"error": "Collection not found"}

    monkeypatch.setattr(base.runtime, "get_blender_connection", lambda _logger: FakeBlender())

    result = base.import_blend_contents(
        ctx=None,
        blend_file_path="/tmp/infinigen/Fern.blend",
        collection_names=" Fern_Main ; Fern_LoD ",
        object_names=["FernObj", " FernObj "],
    )

    assert calls == [
        (
            "import_blend_contents",
            {
                "blend_file_path": "/tmp/infinigen/Fern.blend",
                "import_mode": "auto",
                "collection_names": ["Fern_Main", "Fern_LoD"],
                "object_names": ["FernObj"],
                "link": False,
            },
        )
    ]
    assert result == "Failed to import blend contents: Collection not found"


def test_import_blend_contents_handles_unexpected_response_type(monkeypatch):
    class FakeBlender:
        def send_command(self, command_type: str, params=None):
            _ = (command_type, params)
            return "ok"

    monkeypatch.setattr(base.runtime, "get_blender_connection", lambda _logger: FakeBlender())

    result = base.import_blend_contents(
        ctx=None,
        blend_file_path="/tmp/infinigen/Fern.blend",
    )

    assert "unexpected response type str" in result
