from __future__ import annotations

import importlib.util
import types
from pathlib import Path


class FakeObjectCollection:
    def __init__(self, objects):
        self._objects = {obj.name: obj for obj in objects}
        self.removed_names: list[str] = []

    def get(self, name):
        return self._objects.get(name)

    def remove(self, obj, do_unlink=True):
        del do_unlink
        self.removed_names.append(obj.name)
        self._objects.pop(obj.name, None)

    def __iter__(self):
        return iter(self._objects.values())


class FakeLightCollection:
    def __init__(self):
        self.removed = []

    def remove(self, light_data):
        self.removed.append(getattr(light_data, "name", ""))


def _load_module():
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "blender_headless_client.py"
    spec = importlib.util.spec_from_file_location("blender_headless_client_test", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prepare_scene_removes_default_light_objects():
    module = _load_module()

    cube = types.SimpleNamespace(name="Cube", type="MESH", data=None)
    camera = types.SimpleNamespace(name="Camera", type="CAMERA", data=None)
    light_data = types.SimpleNamespace(name="LightData", users=0)
    light = types.SimpleNamespace(name="Light", type="LIGHT", data=light_data)
    fill_data = types.SimpleNamespace(name="FillData", users=0)
    fill = types.SimpleNamespace(name="Fill", type="LIGHT", data=fill_data)
    mesh = types.SimpleNamespace(name="Chair", type="MESH", data=None)

    fake_objects = FakeObjectCollection([cube, camera, light, fill, mesh])
    fake_lights = FakeLightCollection()
    fake_bpy = types.SimpleNamespace(
        data=types.SimpleNamespace(
            filepath="",
            objects=fake_objects,
            lights=fake_lights,
            worlds=types.SimpleNamespace(new=lambda name: None),
        ),
        context=types.SimpleNamespace(
            scene=types.SimpleNamespace(world=None),
        ),
        ops=types.SimpleNamespace(
            wm=types.SimpleNamespace(
                read_factory_settings=lambda use_empty=False: None,
                open_mainfile=lambda filepath, load_ui=False: None,
            )
        ),
    )

    module._prepare_scene(fake_bpy, blend_path=None)

    assert "Cube" in fake_objects.removed_names
    assert "Camera" in fake_objects.removed_names
    assert "Light" in fake_objects.removed_names
    assert "Fill" in fake_objects.removed_names
    assert "Chair" not in fake_objects.removed_names
    assert fake_lights.removed == ["LightData", "FillData"]
