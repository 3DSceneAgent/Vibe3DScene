from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


class FakeObject(types.SimpleNamespace):
    def visible_get(self):
        return getattr(self, "visible", True)


def _load_scene_tools_module(monkeypatch):
    repo_root = Path(__file__).resolve().parents[2]
    addon_dir = repo_root / "addon" / "blender_mcpv_addon"

    fake_bpy = types.ModuleType("bpy")
    fake_bpy.data = types.SimpleNamespace(objects=types.SimpleNamespace(get=lambda _name: None))
    fake_bpy.context = types.SimpleNamespace(scene=types.SimpleNamespace(objects=[]))
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)

    fake_mathutils = types.ModuleType("mathutils")
    fake_mathutils.Vector = lambda values: values
    monkeypatch.setitem(sys.modules, "mathutils", fake_mathutils)

    module_name = "addon.blender_mcpv_addon.server_scene_tools_mixin_penetration_test"
    spec = importlib.util.spec_from_file_location(module_name, addon_dir / "server_scene_tools_mixin.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


def test_should_skip_penetration_pair_for_shared_root(monkeypatch):
    module = _load_scene_tools_module(monkeypatch)
    server = module.ServerSceneToolsMixin()

    root = FakeObject(name="AssetRoot", parent=None)
    child_a = FakeObject(name="Chair_Seat", parent=root)
    child_b = FakeObject(name="Chair_Back", parent=root)

    assert server._should_skip_penetration_pair(
        {
            "obj": child_a,
            "name": "Chair_Seat",
            "bbox": [[0, 0, 0], [1, 1, 1]],
            "root_name": server._penetration_root_name(child_a),
        },
        {
            "obj": child_b,
            "name": "Chair_Back",
            "bbox": [[0, 0, 0], [1, 1, 1]],
            "root_name": server._penetration_root_name(child_b),
        },
    ) is True


def test_check_scene_penetration_marks_degraded_when_candidates_truncated(monkeypatch):
    module = _load_scene_tools_module(monkeypatch)
    server = module.ServerSceneToolsMixin()
    obj_a = FakeObject(name="A", parent=None)
    obj_b = FakeObject(name="B", parent=None)
    obj_c = FakeObject(name="C", parent=None)

    monkeypatch.setattr(
        server,
        "_collect_penetration_mesh_records",
        lambda _object_names=None: (
            [
                {"obj": obj_a, "name": "A", "bbox": [[0, 0, 0], [1, 1, 1]], "root_name": "A"},
                {"obj": obj_b, "name": "B", "bbox": [[0, 0, 0], [1, 1, 1]], "root_name": "B"},
                {"obj": obj_c, "name": "C", "bbox": [[0, 0, 0], [1, 1, 1]], "root_name": "C"},
            ],
            2,
        ),
    )
    monkeypatch.setattr(
        server,
        "_rank_penetration_candidates",
        lambda _records: (
            [
                {"obj_a": obj_a, "obj_b": obj_b, "object_a": "A", "object_b": "B", "depth_m": 0.03},
                {"obj_a": obj_b, "obj_b": obj_c, "object_a": "B", "object_b": "C", "depth_m": 0.02},
                {"obj_a": obj_a, "obj_b": obj_c, "object_a": "A", "object_b": "C", "depth_m": 0.004},
            ],
            0,
        ),
    )
    monkeypatch.setattr(server, "_pair_has_mesh_overlap", lambda *_args, **_kwargs: True)

    result = server.check_scene_penetration(
        penetration_threshold_m=0.005,
        max_candidate_pairs=2,
        max_reported_pairs=1,
    )

    assert result["has_penetration"] is True
    assert result["pair_count"] == 2
    assert len(result["pairs"]) == 1
    assert result["pairs"][0]["object_a"] == "A"
    assert result["degraded"] is True
    assert result["filtered_pair_count"] == 3


def test_check_scene_penetration_filters_aabb_false_positive(monkeypatch):
    module = _load_scene_tools_module(monkeypatch)
    server = module.ServerSceneToolsMixin()
    obj_a = FakeObject(name="A", parent=None)
    obj_b = FakeObject(name="B", parent=None)

    monkeypatch.setattr(
        server,
        "_collect_penetration_mesh_records",
        lambda _object_names=None: (
            [
                {"obj": obj_a, "name": "A", "bbox": [[0, 0, 0], [1, 1, 1]], "root_name": "A"},
                {"obj": obj_b, "name": "B", "bbox": [[0, 0, 0], [1, 1, 1]], "root_name": "B"},
            ],
            0,
        ),
    )
    monkeypatch.setattr(
        server,
        "_rank_penetration_candidates",
        lambda _records: (
            [
                {"obj_a": obj_a, "obj_b": obj_b, "object_a": "A", "object_b": "B", "depth_m": 0.03},
            ],
            0,
        ),
    )
    monkeypatch.setattr(server, "_pair_has_mesh_overlap", lambda *_args, **_kwargs: False)

    result = server.check_scene_penetration(
        penetration_threshold_m=0.005,
        max_candidate_pairs=4,
        max_reported_pairs=4,
    )

    assert result["has_penetration"] is False
    assert result["pair_count"] == 0
    assert result["filtered_pair_count"] == 1
    assert result["summary"] == "No penetration detected."
