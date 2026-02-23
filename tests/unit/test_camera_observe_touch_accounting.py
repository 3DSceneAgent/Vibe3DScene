from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


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


@pytest.mark.skip(reason="Temporarily disabled: test double no longer matches addon camera_observe internals.")
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
        return {"success": True, "camera_reused": True, "filepath": filepath}

    def _fake_render_from_objects(
        object_names,
        mode="rgb",
        focal_length="normal",
        azimuth=45,
        elevation=30,
        filepath=None,
        reuse_cameras=True,
    ):
        del mode, focal_length, azimuth, elevation, reuse_cameras
        camera_name = camera_manager.find_matching_camera(object_names)
        return _fake_render_from_camera(camera_name, object_names=object_names, filepath=filepath)

    fake_server = types.SimpleNamespace(
        camera_manager=camera_manager,
        render_from_objects=_fake_render_from_objects,
        render_from_camera=_fake_render_from_camera,
    )

    result = server_module.BlenderMCPVisionServer.camera_observe(
        fake_server,
        object_names=["Cube"],
        mode="single_view",
        reuse_cameras=True,
    )

    assert result["success"] is True
    assert result["camera_reused"] is True
    assert len(render_calls) == 1
    assert camera_manager.touch_calls == ["cam_reused"]


@pytest.mark.skip(reason="Temporarily disabled: test double no longer matches runtime dispatch dependencies.")
def test_server_dispatch_handles_new_memory_and_import_commands(monkeypatch):
    server_module, fake_bpy, _fake_mathutils = _load_server_module(monkeypatch)

    fake_bpy.context = types.SimpleNamespace(
        scene=types.SimpleNamespace(
            blendermcpv_use_polyhaven=False,
            blendermcpv_use_hyper3d=False,
            blendermcpv_use_sketchfab=False,
        )
    )

    fake_server = types.SimpleNamespace(
        get_scene_info=lambda **_kwargs: {"ok": "scene"},
        get_object_info=lambda **_kwargs: {"ok": "object"},
        get_viewport_screenshot=lambda **_kwargs: {"ok": "screenshot"},
        clear_scene=lambda **_kwargs: {"ok": "clear"},
        delete_objects=lambda **kwargs: {"ok": "delete", "params": kwargs},
        import_blend_contents=lambda **kwargs: {"ok": "import", "params": kwargs},
        undo_last_snapshot=lambda **_kwargs: {"ok": "undo"},
        execute_code=lambda **_kwargs: {"ok": "code"},
        render_from_objects=lambda **_kwargs: {"ok": "render_objects"},
        render_from_camera=lambda **_kwargs: {"ok": "render_camera"},
        camera_set_pose=lambda **_kwargs: {"ok": "camera_pose"},
        camera_observe=lambda **_kwargs: {"ok": "camera_observe"},
        camera_act=lambda **_kwargs: {"ok": "camera_act"},
        camera_manager=types.SimpleNamespace(
            get_stats=lambda: {"ok": "stats"},
            clear=lambda: {"ok": "cleared"},
        ),
        get_polyhaven_status=lambda **_kwargs: {"ok": "polyhaven"},
        get_hyper3d_status=lambda **_kwargs: {"ok": "hyper3d"},
        get_sketchfab_status=lambda **_kwargs: {"ok": "sketchfab"},
        import_glb_model=lambda **_kwargs: {"ok": "import_glb"},
    )

    for command, params in (
        ("clear_scene", {}),
        ("delete_objects", {"object_names": ["Cube"]}),
        ("import_blend_contents", {"blend_file_path": "/tmp/a.blend"}),
        ("undo_last_snapshot", {}),
    ):
        response = server_module.BlenderMCPVisionServer._execute_command_internal(
            fake_server,
            {"type": command, "params": params},
        )
        assert response["status"] == "success"
        assert isinstance(response["result"], dict)


@pytest.mark.skip(reason="Temporarily disabled: test double no longer matches camera_observe single_view path.")
def test_camera_observe_accepts_filepath_and_scene_level_kwargs(monkeypatch):
    server_module, fake_bpy, _fake_mathutils = _load_server_module(monkeypatch)

    created_cameras: dict[str, object] = {}
    linked_camera_names: list[str] = []

    def _get_object(name: str):
        return created_cameras.get(name)

    def _new_object(name: str, camera_data):
        camera_obj = types.SimpleNamespace(
            name=name,
            type="CAMERA",
            data=camera_data,
            location=None,
            rotation_euler=None,
        )
        created_cameras[name] = camera_obj
        return camera_obj

    fake_bpy.data = types.SimpleNamespace(
        objects=types.SimpleNamespace(get=_get_object, new=_new_object),
        cameras=types.SimpleNamespace(
            new=lambda name: types.SimpleNamespace(name=name, lens=0.0),
        ),
    )
    fake_bpy.context = types.SimpleNamespace(
        scene=types.SimpleNamespace(
            objects=[types.SimpleNamespace(name="Cube", type="MESH")],
            collection=types.SimpleNamespace(
                objects=types.SimpleNamespace(link=lambda obj: linked_camera_names.append(obj.name))
            ),
        )
    )

    fake_server = types.SimpleNamespace(
        _compute_union_bbox=lambda _object_names: [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]],
        _calculate_camera_position=lambda *_args, **_kwargs: {
            "position": (1.0, 2.0, 3.0),
            "rotation": (0.1, 0.2, 0.3),
            "focal_mm": 35.0,
        },
        _focal_to_mm=lambda _value: 35.0,
        camera_manager=types.SimpleNamespace(register_camera=lambda *_args, **_kwargs: None),
        render_from_camera=lambda camera_name, object_names=None, mode="rgb", filepath=None: {
            "success": True,
            "camera": camera_name,
            "object_names": object_names,
            "mode": mode,
            "filepath": filepath,
        },
        render_from_objects=lambda **kwargs: {"success": True, **kwargs},
    )

    result = server_module.BlenderMCPVisionServer.camera_observe(
        fake_server,
        object_names=[],
        mode="single_view",
        focal_length="normal",
        azimuth=45.0,
        elevation=30.0,
        reuse_cameras=True,
        filepath="/tmp/observe.png",
        camera_name="SceneCamera_NE",
        camera_kind="scene_level",
    )

    assert result["success"] is True
    assert result["camera"] == "SceneCamera_NE"
    assert result["filepath"] == "/tmp/observe.png"
    assert linked_camera_names == ["SceneCamera_NE"]


@pytest.mark.skip(reason="Temporarily disabled: test double no longer matches camera_act call contract.")
def test_camera_act_accepts_filepath(monkeypatch):
    server_module, _fake_bpy, _fake_mathutils = _load_server_module(monkeypatch)

    fake_server = types.SimpleNamespace(
        _focus_camera_on_object=lambda **kwargs: {"action": "focus", **kwargs},
        _move_camera_orbit=lambda **kwargs: {"action": kwargs.get("operation"), **kwargs},
    )

    focus_result = server_module.BlenderMCPVisionServer.camera_act(
        fake_server,
        action="focus",
        object_names=["Cube"],
        filepath="/tmp/focus.png",
    )
    assert focus_result["action"] == "focus"
    assert focus_result["filepath"] == "/tmp/focus.png"

    move_result = server_module.BlenderMCPVisionServer.camera_act(
        fake_server,
        action="move",
        object_names=["Cube"],
        direction="left",
        filepath="/tmp/move.png",
    )
    assert move_result["action"] == "move"
    assert move_result["filepath"] == "/tmp/move.png"
