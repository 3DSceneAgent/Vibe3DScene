from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


class FakeObjectStore:
    def __init__(self, objects=None):
        self._objects = {}
        for obj in objects or []:
            self._objects[obj.name] = obj

    def get(self, name):
        return self._objects.get(name)

    def add(self, obj):
        self._objects[obj.name] = obj

    def remove(self, obj, do_unlink=True):
        del do_unlink
        self._objects.pop(getattr(obj, "name", ""), None)

    def __iter__(self):
        return iter(self._objects.values())


class FakeLightStore:
    def remove(self, _light_data):
        return None


class FakeNodes(list):
    def get(self, name):
        for node in self:
            if getattr(node, "name", None) == name:
                return node
        return None


class FakeCameraManager:
    def __init__(self):
        self.active = []
        self.touched = []

    def set_session_active_camera(self, camera_name):
        self.active.append(camera_name)

    def touch_camera(self, camera_name):
        self.touched.append(camera_name)


def _load_scene_tools_module(monkeypatch):
    repo_root = Path(__file__).resolve().parents[2]
    addon_dir = repo_root / "addon" / "blender_mcpv_addon"

    fake_bpy = types.ModuleType("bpy")
    fake_bpy.data = types.SimpleNamespace(
        objects=FakeObjectStore(),
        lights=FakeLightStore(),
    )
    fake_bpy.context = types.SimpleNamespace(scene=None)
    fake_bpy.ops = types.SimpleNamespace(render=types.SimpleNamespace(render=lambda write_still=True: None))
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)

    fake_mathutils = types.ModuleType("mathutils")
    fake_mathutils.Vector = lambda values: values
    monkeypatch.setitem(sys.modules, "mathutils", fake_mathutils)

    module_name = "addon.blender_mcpv_addon.server_scene_tools_mixin_test"
    spec = importlib.util.spec_from_file_location(module_name, addon_dir / "server_scene_tools_mixin.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module, fake_bpy


def _make_camera(name="Camera"):
    return types.SimpleNamespace(
        name=name,
        type="CAMERA",
        data=types.SimpleNamespace(lens=50.0),
        location=(0.0, -5.0, 3.0),
    )


def _make_scene(objects=None, world=None):
    return types.SimpleNamespace(
        objects=list(objects or []),
        camera=None,
        world=world,
        render=types.SimpleNamespace(
            engine=None,
            filepath=None,
            image_settings=types.SimpleNamespace(file_format=None),
        ),
        collection=types.SimpleNamespace(
            objects=types.SimpleNamespace(link=lambda _obj: None),
        ),
    )


def test_render_default_lighting_env_defaults_to_enabled(monkeypatch):
    module, _fake_bpy = _load_scene_tools_module(monkeypatch)
    server = module.ServerSceneToolsMixin()

    monkeypatch.delenv(module.RENDER_DEFAULT_LIGHTING_ENV, raising=False)
    assert server._render_default_lighting_enabled() is True

    monkeypatch.setenv(module.RENDER_DEFAULT_LIGHTING_ENV, "off")
    assert server._render_default_lighting_enabled() is False


def test_render_from_camera_applies_default_lighting_when_enabled(monkeypatch):
    module, fake_bpy = _load_scene_tools_module(monkeypatch)
    camera = _make_camera()
    fake_bpy.data.objects = FakeObjectStore([camera])
    fake_bpy.context.scene = _make_scene(objects=[camera])

    render_calls = []
    cleanup_calls = []
    create_calls = []

    def _render(write_still=True):
        render_calls.append(write_still)

    fake_bpy.ops.render.render = _render

    server = module.ServerSceneToolsMixin()
    server.camera_manager = FakeCameraManager()
    server._cleanup_render_lighting_helpers = lambda: cleanup_calls.append("cleanup")
    server._render_default_lighting_enabled = lambda: True
    server._should_apply_default_render_lighting = lambda: (True, "no_explicit_scene_lighting")
    server._create_default_render_lighting = lambda camera_obj, object_names=None: (
        create_calls.append((camera_obj.name, tuple(object_names or [])))
        or [types.SimpleNamespace(name="__SA_VERIFY_LIGHT__Key")]
    )

    result = server.render_from_camera("Camera", object_names=["Cube"])

    assert result["success"] is True
    assert result["camera"] == "Camera"
    assert render_calls == [True]
    assert cleanup_calls == ["cleanup", "cleanup"]
    assert create_calls == [("Camera", ("Cube",))]


def test_render_from_camera_respects_env_disable(monkeypatch):
    module, fake_bpy = _load_scene_tools_module(monkeypatch)
    camera = _make_camera()
    fake_bpy.data.objects = FakeObjectStore([camera])
    fake_bpy.context.scene = _make_scene(objects=[camera])

    render_calls = []
    cleanup_calls = []

    fake_bpy.ops.render.render = lambda write_still=True: render_calls.append(write_still)

    server = module.ServerSceneToolsMixin()
    server.camera_manager = FakeCameraManager()
    server._cleanup_render_lighting_helpers = lambda: cleanup_calls.append("cleanup")
    server._render_default_lighting_enabled = lambda: False
    server._should_apply_default_render_lighting = lambda: pytest.fail("lighting check should be skipped")
    server._create_default_render_lighting = lambda *_args, **_kwargs: pytest.fail("lighting rig should not be created")

    result = server.render_from_camera("Camera", object_names=["Cube"])

    assert result["success"] is True
    assert render_calls == [True]
    assert cleanup_calls == ["cleanup"]


def test_render_from_camera_cleans_up_helpers_on_render_failure(monkeypatch):
    module, fake_bpy = _load_scene_tools_module(monkeypatch)
    camera = _make_camera()
    fake_bpy.data.objects = FakeObjectStore([camera])
    fake_bpy.context.scene = _make_scene(objects=[camera])

    cleanup_calls = []

    def _render(write_still=True):
        del write_still
        raise RuntimeError("boom")

    fake_bpy.ops.render.render = _render

    server = module.ServerSceneToolsMixin()
    server.camera_manager = FakeCameraManager()
    server._cleanup_render_lighting_helpers = lambda: cleanup_calls.append("cleanup")
    server._render_default_lighting_enabled = lambda: True
    server._should_apply_default_render_lighting = lambda: (True, "no_explicit_scene_lighting")
    server._create_default_render_lighting = lambda *_args, **_kwargs: [
        types.SimpleNamespace(name="__SA_VERIFY_LIGHT__Key")
    ]

    with pytest.raises(Exception, match="Failed to render from camera: boom"):
        server.render_from_camera("Camera", object_names=["Cube"])

    assert cleanup_calls == ["cleanup", "cleanup"]


def test_should_apply_default_lighting_skips_real_light(monkeypatch):
    module, fake_bpy = _load_scene_tools_module(monkeypatch)
    light = types.SimpleNamespace(
        name="KeyLight",
        type="LIGHT",
        hide_render=False,
        data=types.SimpleNamespace(energy=10.0),
    )
    fake_bpy.context.scene = _make_scene(objects=[light], world=None)

    server = module.ServerSceneToolsMixin()

    assert server._should_apply_default_render_lighting() == (False, "scene_has_explicit_light")


def test_should_apply_default_lighting_skips_hdri_world(monkeypatch):
    module, fake_bpy = _load_scene_tools_module(monkeypatch)
    env_node = types.SimpleNamespace(name="Environment Texture", type="TEX_ENVIRONMENT", image=object())
    world = types.SimpleNamespace(
        use_nodes=True,
        node_tree=types.SimpleNamespace(nodes=FakeNodes([env_node])),
    )
    fake_bpy.context.scene = _make_scene(objects=[], world=world)

    server = module.ServerSceneToolsMixin()

    assert server._should_apply_default_render_lighting() == (False, "scene_has_hdri_world")


def test_should_apply_default_lighting_skips_strong_world_background(monkeypatch):
    module, fake_bpy = _load_scene_tools_module(monkeypatch)
    background_node = types.SimpleNamespace(
        name="Background",
        type="BACKGROUND",
        inputs={"Strength": types.SimpleNamespace(default_value=0.6)},
    )
    world = types.SimpleNamespace(
        use_nodes=True,
        node_tree=types.SimpleNamespace(nodes=FakeNodes([background_node])),
    )
    fake_bpy.context.scene = _make_scene(objects=[], world=world)

    server = module.ServerSceneToolsMixin()

    assert server._should_apply_default_render_lighting() == (
        False,
        "scene_has_strong_world_background",
    )
