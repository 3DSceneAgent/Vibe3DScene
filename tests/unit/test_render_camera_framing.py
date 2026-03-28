from __future__ import annotations

import importlib.util
import math
import sys
import types
from pathlib import Path

import pytest


class FakeQuaternion:
    def to_euler(self):
        return (0.0, 0.0, 0.0)


class FakeVector:
    def __init__(self, values) -> None:
        x, y, z = values
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __add__(self, other: "FakeVector") -> "FakeVector":
        return FakeVector((self.x + other.x, self.y + other.y, self.z + other.z))

    def __sub__(self, other: "FakeVector") -> "FakeVector":
        return FakeVector((self.x - other.x, self.y - other.y, self.z - other.z))

    def to_track_quat(self, _track_axis: str, _up_axis: str) -> FakeQuaternion:
        return FakeQuaternion()


def _distance(a: FakeVector, b: FakeVector) -> float:
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)


def _load_scene_tools_module(monkeypatch):
    repo_root = Path(__file__).resolve().parents[2]
    addon_dir = repo_root / "addon" / "blender_mcpv_addon"

    fake_bpy = types.ModuleType("bpy")
    fake_bpy.data = types.SimpleNamespace(objects=types.SimpleNamespace(get=lambda _name: None))
    fake_bpy.context = types.SimpleNamespace(scene=None)
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)

    fake_mathutils = types.ModuleType("mathutils")
    fake_mathutils.Vector = lambda values: FakeVector(tuple(values))
    monkeypatch.setitem(sys.modules, "mathutils", fake_mathutils)

    module_name = "addon.blender_mcpv_addon.server_scene_tools_mixin_framing_test"
    spec = importlib.util.spec_from_file_location(module_name, addon_dir / "server_scene_tools_mixin.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


def test_calculate_camera_position_uses_tighter_render_padding(monkeypatch):
    module = _load_scene_tools_module(monkeypatch)
    server = module.ServerSceneToolsMixin()
    union_bbox = [[-1.0, -2.0, 0.0], [3.0, 2.0, 4.0]]

    camera_data = server._calculate_camera_position(union_bbox, focal_length=50.0, azimuth=45.0, elevation=30.0)

    target = camera_data["target"]
    position = camera_data["position"]
    actual_distance = _distance(position, target)
    expected_distance = (
        4.0 / math.tan((2 * math.atan(36.0 / (2 * 50.0))) / 2)
    ) * module.RENDER_FROM_OBJECTS_FRAME_PADDING

    assert actual_distance == pytest.approx(expected_distance)
    assert module.RENDER_FROM_OBJECTS_FRAME_PADDING == pytest.approx(1.35)


def test_render_from_objects_reframes_reused_camera_before_render(monkeypatch):
    module = _load_scene_tools_module(monkeypatch)
    create_calls: list[dict[str, object]] = []
    render_calls: list[dict[str, object]] = []

    fake_server = types.SimpleNamespace(
        _resolve_mesh_objects=lambda object_names, allow_scene_fallback=True: [
            types.SimpleNamespace(name=name) for name in object_names
        ],
        _normalize_camera_kind=lambda camera_kind: camera_kind,
        _create_camera_for_objects=lambda object_names, **kwargs: (
            create_calls.append({"object_names": list(object_names), **kwargs})
            or types.SimpleNamespace(name=kwargs["camera_name"])
        ),
        render_from_camera=lambda camera_name, object_names=None, mode="rgb", filepath=None: (
            render_calls.append(
                {
                    "camera_name": camera_name,
                    "object_names": list(object_names or []),
                    "mode": mode,
                    "filepath": filepath,
                }
            )
            or {"success": True, "camera": camera_name}
        ),
        camera_manager=types.SimpleNamespace(
            find_matching_camera=lambda object_names, camera_kind="local_work": "Camera_Work_Cube"
        ),
    )

    result = module.BlenderMCPVisionServer.render_from_objects(
        fake_server,
        ["Cube"],
        focal_length="normal",
        azimuth=45,
        elevation=30,
        reuse_cameras=True,
    )

    assert result["success"] is True
    assert result["camera"] == "Camera_Work_Cube"
    assert create_calls == [
        {
            "object_names": ["Cube"],
            "focal_length": "normal",
            "azimuth": 45,
            "elevation": 30,
            "camera_name": "Camera_Work_Cube",
            "camera_kind": "local_work",
        }
    ]
    assert render_calls == [
        {
            "camera_name": "Camera_Work_Cube",
            "object_names": ["Cube"],
            "mode": "rgb",
            "filepath": None,
        }
    ]
