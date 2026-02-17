from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def _load_server_module(monkeypatch):
    """
    Load addon server module with stubbed Blender dependencies.
    """
    repo_root = Path(__file__).resolve().parents[2]
    addon_dir = repo_root / "addon" / "blender_mcpv_addon"

    fake_bpy = types.ModuleType("bpy")
    fake_bpy.data = types.SimpleNamespace(
        objects=types.SimpleNamespace(get=lambda _name: None),
    )
    fake_bpy.context = types.SimpleNamespace(
        scene=types.SimpleNamespace(camera=None),
    )
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)

    fake_mathutils = types.ModuleType("mathutils")
    monkeypatch.setitem(sys.modules, "mathutils", fake_mathutils)

    addon_pkg = types.ModuleType("addon")
    addon_pkg.__path__ = [str(repo_root / "addon")]
    monkeypatch.setitem(sys.modules, "addon", addon_pkg)

    addon_subpkg = types.ModuleType("addon.blender_mcpv_addon")
    addon_subpkg.__path__ = [str(addon_dir)]
    monkeypatch.setitem(sys.modules, "addon.blender_mcpv_addon", addon_subpkg)

    def _load(module_name: str, file_name: str):
        spec = importlib.util.spec_from_file_location(module_name, addon_dir / file_name)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, module_name, module)
        spec.loader.exec_module(module)
        return module

    _load("addon.blender_mcpv_addon.camera_manager", "camera_manager.py")
    _load("addon.blender_mcpv_addon.asset_handlers", "asset_handlers.py")
    server_module = _load("addon.blender_mcpv_addon.server", "server.py")
    return server_module, fake_bpy, fake_mathutils


def test_camera_observe_reuse_does_not_double_touch_camera(monkeypatch):
    """
    Regression test: camera_observe should not touch a reused camera directly
    because find_matching_camera already touches it.
    """
    server_module, fake_bpy, fake_mathutils = _load_server_module(monkeypatch)

    class _FakeEuler:
        def copy(self):
            return _FakeEuler()

    class _FakeQuat:
        def to_euler(self):
            return _FakeEuler()

    class _FakeVector:
        def __init__(self, coords):
            self.coords = tuple(float(v) for v in coords)

        def __sub__(self, other):
            if isinstance(other, tuple):
                ox, oy, oz = other
            else:
                ox, oy, oz = float(other.x), float(other.y), float(other.z)
            return _FakeVector((self.coords[0] - ox, self.coords[1] - oy, self.coords[2] - oz))

        def to_track_quat(self, _forward, _up):
            return _FakeQuat()

    class _FakeLocation:
        def __init__(self, x=0.0, y=0.0, z=0.0):
            self.x = float(x)
            self.y = float(y)
            self.z = float(z)

        def copy(self):
            return _FakeLocation(self.x, self.y, self.z)

    fake_mathutils.Vector = _FakeVector
    server_module.mathutils.Vector = _FakeVector

    camera_obj = types.SimpleNamespace(
        name="cam_reused",
        type="CAMERA",
        data=types.SimpleNamespace(lens=35.0),
        location=_FakeLocation(),
        rotation_euler=_FakeEuler(),
    )
    fake_bpy.data.objects = types.SimpleNamespace(
        get=lambda name: camera_obj if name == "cam_reused" else None,
    )
    fake_bpy.context = types.SimpleNamespace(scene=types.SimpleNamespace(camera=None))

    class _FakeCameraManager:
        def __init__(self):
            self.touch_calls: list[str] = []
            self.active_calls: list[str] = []
            self.observed_calls: list[list[str]] = []

        def set_session_last_observed(self, names):
            self.observed_calls.append(list(names))

        def find_matching_camera(self, requested_objects, tolerance=0.7, camera_kind="local_work"):
            del requested_objects, tolerance, camera_kind
            # Mirror real behavior: find_matching_camera already touches a reused camera.
            self.touch_camera("cam_reused")
            return "cam_reused"

        def set_session_active_camera(self, camera_name):
            self.active_calls.append(camera_name)

        def touch_camera(self, camera_name):
            self.touch_calls.append(camera_name)

    camera_manager = _FakeCameraManager()
    render_calls: list[str] = []

    def _fake_render_from_camera(camera_name, object_names=None, mode="rgb", filepath=None):
        del object_names, mode
        render_calls.append(camera_name)
        return {"filepath": filepath}

    fake_server = types.SimpleNamespace(
        _iter_observation_objects=lambda object_names: [
            types.SimpleNamespace(name=name) for name in object_names
        ],
        camera_manager=camera_manager,
        _focal_to_mm=lambda _focal: 35.0,
        _create_camera_for_objects=lambda *args, **kwargs: types.SimpleNamespace(name="unused"),
        _compute_union_bbox=lambda _names: {},
        _bbox_to_center_size=lambda _bbox: ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)),
        render_from_camera=_fake_render_from_camera,
        _stitch_images_horizontally=lambda _paths, stitched_path: stitched_path,
    )

    result = server_module.BlenderMCPVisionServer.camera_observe(
        fake_server,
        object_names=["Cube"],
        mode="multi_view",
        reuse_cameras=True,
    )

    assert result["success"] is True
    assert result["camera_reused"] is True
    assert len(render_calls) == 4
    assert camera_manager.touch_calls == ["cam_reused"]
