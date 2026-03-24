from __future__ import annotations

import importlib.util
import io
import sys
import types
import zipfile
from pathlib import Path


class FakeVector:
    def __init__(self, x: float, y: float, z: float) -> None:
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __iter__(self):
        yield self.x
        yield self.y
        yield self.z


class FakeMatrix:
    def __init__(
        self,
        translation: tuple[float, float, float] = (0.0, 0.0, 0.0),
        scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
    ) -> None:
        self._translation = tuple(float(value) for value in translation)
        self._scale = tuple(float(value) for value in scale)

    def copy(self) -> "FakeMatrix":
        return FakeMatrix(self._translation, self._scale)

    @property
    def translation(self) -> FakeVector:
        return FakeVector(*self._translation)

    def to_scale(self) -> FakeVector:
        return FakeVector(*self._scale)


class FakeMeshData:
    def __init__(self, name: str, users: int = 1) -> None:
        self.name = name
        self.users = users
        self.vertices = [None] * 8
        self.edges = [None] * 12
        self.polygons = [None] * 6
        self.applied_scale: tuple[float, float, float] | None = None
        self.materials: list[object] = []

    def copy(self) -> "FakeMeshData":
        duplicated = FakeMeshData(f"{self.name}_copy", users=1)
        duplicated.vertices = list(self.vertices)
        duplicated.edges = list(self.edges)
        duplicated.polygons = list(self.polygons)
        duplicated.applied_scale = self.applied_scale
        return duplicated


class FakeObject:
    def __init__(
        self,
        name: str,
        obj_type: str,
        *,
        parent: "FakeObject | None" = None,
        location: tuple[float, float, float] = (0.0, 0.0, 0.0),
        local_scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
        world_scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
        data: FakeMeshData | None = None,
    ) -> None:
        self.name = name
        self.type = obj_type
        self.children: list[FakeObject] = []
        self.material_slots: list[object] = []
        self._parent: FakeObject | None = None
        self._visible = True
        self.selected = False
        self.location = FakeVector(*location)
        self.rotation_euler = FakeVector(0.0, 0.0, 0.0)
        self.scale = FakeVector(*local_scale)
        self._matrix_world = FakeMatrix(location, world_scale)
        self.data = data
        self.parent = parent

    @property
    def parent(self) -> "FakeObject | None":
        return self._parent

    @parent.setter
    def parent(self, value: "FakeObject | None") -> None:
        if self._parent is value:
            return
        if self._parent is not None and self in self._parent.children:
            self._parent.children.remove(self)
        self._parent = value
        if value is not None and self not in value.children:
            value.children.append(self)

    @property
    def matrix_world(self) -> FakeMatrix:
        return self._matrix_world

    @matrix_world.setter
    def matrix_world(self, value: FakeMatrix) -> None:
        self._matrix_world = value
        self.location = FakeVector(*tuple(value.translation))
        self.scale = FakeVector(*tuple(value.to_scale()))

    def visible_get(self) -> bool:
        return self._visible

    def select_set(self, selected: bool) -> None:
        self.selected = bool(selected)


class FakeObjectStore:
    def __init__(self) -> None:
        self._objects: dict[str, FakeObject] = {}

    def add(self, obj: FakeObject) -> None:
        self._objects[obj.name] = obj

    def get(self, name: str):
        return self._objects.get(name)

    def remove(self, obj: FakeObject, do_unlink: bool = True) -> None:
        _ = do_unlink
        self._objects.pop(obj.name, None)
        if obj.parent is not None and obj in obj.parent.children:
            obj.parent.children.remove(obj)
        obj._parent = None

    def __iter__(self):
        return iter(self._objects.values())


class FakeViewLayer:
    def __init__(self) -> None:
        self.objects = types.SimpleNamespace(active=None)
        self.update_calls = 0

    def update(self) -> None:
        self.update_calls += 1


class FakeContext:
    def __init__(self, objects: FakeObjectStore) -> None:
        self._objects = objects
        self.view_layer = FakeViewLayer()
        self.scene = types.SimpleNamespace(name="TestScene", objects=[])

    @property
    def selected_objects(self) -> list[FakeObject]:
        return [obj for obj in self._objects if obj.selected]


class FakeOpsObject:
    def __init__(self, fake_bpy) -> None:
        self._bpy = fake_bpy

    def select_all(self, *, action: str) -> None:
        if action == "DESELECT":
            for obj in self._bpy.data.objects:
                obj.selected = False

    def transform_apply(self, *, location: bool, rotation: bool, scale: bool) -> None:
        _ = (location, rotation, scale)
        for obj in self._bpy.context.selected_objects:
            if getattr(obj, "data", None) is not None:
                obj.data.applied_scale = tuple(obj.scale)
            obj.matrix_world = FakeMatrix(
                translation=tuple(obj.matrix_world.translation),
                scale=(1.0, 1.0, 1.0),
            )


class FakeStreamingResponse:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        self._body = body
        self.headers = headers or {}
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 8192):
        for index in range(0, len(self._body), chunk_size):
            yield self._body[index : index + chunk_size]


class FakeImage:
    def __init__(self, name: str, filepath: str, *, packed: bool = False) -> None:
        self.name = name
        self.filepath = filepath
        self.filepath_raw = filepath
        self.packed_file = object() if packed else None
        self.pack_calls = 0
        self.reload_calls = 0

    def pack(self) -> None:
        self.pack_calls += 1
        self.packed_file = object()

    def reload(self) -> None:
        self.reload_calls += 1


def _load_module(monkeypatch, module_name: str, relative_path: str):
    fake_bpy = types.ModuleType("bpy")
    fake_bpy.data = types.SimpleNamespace(
        objects=FakeObjectStore(),
        images=[],
        materials=[],
    )
    fake_bpy.context = FakeContext(fake_bpy.data.objects)
    fake_bpy.ops = types.SimpleNamespace(object=FakeOpsObject(fake_bpy))
    fake_bpy.path = types.SimpleNamespace(abspath=lambda value: value)
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    monkeypatch.setitem(sys.modules, "mathutils", types.ModuleType("mathutils"))

    module_path = Path(__file__).resolve().parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, fake_bpy


def test_flatten_imported_hierarchy_unparents_meshes_and_removes_empty_wrappers(monkeypatch):
    module, fake_bpy = _load_module(
        monkeypatch,
        "asset_handlers_under_test",
        "addon/blender_mcpv_addon/asset_handlers.py",
    )

    shared_mesh = FakeMeshData("MeshData", users=2)
    root = FakeObject("ModelRoot", "EMPTY", world_scale=(100.0, 100.0, 100.0))
    group = FakeObject("Group", "EMPTY", parent=root, world_scale=(100.0, 100.0, 100.0))
    mesh = FakeObject(
        "Mesh",
        "MESH",
        parent=group,
        local_scale=(1.0, 1.0, 1.0),
        world_scale=(100.0, 100.0, 100.0),
        data=shared_mesh,
    )

    for obj in (root, group, mesh):
        fake_bpy.data.objects.add(obj)

    summary = module.AssetHandlerMixin._flatten_imported_hierarchy([root, group, mesh])

    assert mesh.parent is None
    assert tuple(mesh.scale) == (1.0, 1.0, 1.0)
    assert mesh.data is not shared_mesh
    assert mesh.data.applied_scale == (100.0, 100.0, 100.0)
    assert fake_bpy.data.objects.get("ModelRoot") is None
    assert fake_bpy.data.objects.get("Group") is None
    assert summary["processed_meshes"] == ["Mesh"]
    assert summary["unparented_meshes"] == ["Mesh"]
    assert set(summary["removed_empties"]) == {"ModelRoot", "Group"}


def test_flatten_imported_hierarchy_is_safe_for_flat_meshes(monkeypatch):
    module, fake_bpy = _load_module(
        monkeypatch,
        "asset_handlers_under_test_flat",
        "addon/blender_mcpv_addon/asset_handlers.py",
    )

    mesh = FakeObject(
        "FlatMesh",
        "MESH",
        local_scale=(1.0, 1.0, 1.0),
        world_scale=(1.0, 1.0, 1.0),
        data=FakeMeshData("FlatMeshData"),
    )
    fake_bpy.data.objects.add(mesh)

    summary = module.AssetHandlerMixin._flatten_imported_hierarchy([mesh])

    assert mesh.parent is None
    assert summary["processed_meshes"] == ["FlatMesh"]
    assert summary["unparented_meshes"] == []
    assert summary["removed_empties"] == []


def test_import_glb_model_uses_obj_importer_for_hunyuan_zip(monkeypatch):
    module, fake_bpy = _load_module(
        monkeypatch,
        "asset_handlers_under_test_zip_import",
        "addon/blender_mcpv_addon/asset_handlers.py",
    )

    import_calls: list[tuple[str, str]] = []

    def _obj_import(*, filepath: str):
        import_calls.append(("obj", filepath))
        image_path = str(Path(filepath).with_name("material_0.png"))
        fake_bpy.data.images.append(FakeImage("material_0.png", image_path))
        fake_bpy.data.objects.add(
            FakeObject(
                "GeneratedMesh",
                "MESH",
                data=FakeMeshData("GeneratedMeshData"),
            )
        )
        return {"FINISHED"}

    def _unexpected_gltf_import(*, filepath: str):
        raise AssertionError(f"GLTF importer should not be used for {filepath}")

    def _unexpected_fbx_import(*, filepath: str):
        raise AssertionError(f"FBX importer should not be used for {filepath}")

    fake_bpy.ops.import_scene = types.SimpleNamespace(
        gltf=_unexpected_gltf_import,
        fbx=_unexpected_fbx_import,
        obj=_obj_import,
    )
    fake_bpy.ops.wm = types.SimpleNamespace(
        obj_import=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError(f"wm.obj_import fallback should not be used: {kwargs}")
        )
    )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as archive:
        archive.writestr("nested/model.obj", "mtllib model.mtl\nv 0 0 0\n")
        archive.writestr("nested/model.mtl", "newmtl default\n")

    monkeypatch.setattr(
        module.requests,
        "get",
        lambda *args, **kwargs: FakeStreamingResponse(
            zip_buffer.getvalue(),
            headers={"Content-Disposition": 'attachment; filename="generated_reference_asset.zip"'},
        ),
    )

    handler = module.AssetHandlerMixin()
    handler._get_aabb = lambda _obj: [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]

    result = handler.import_glb_model(
        "https://example.test/hunyuan/generated_reference_asset.zip?sig=123",
        object_name="GeneratedReferenceAsset",
    )

    assert result["success"] is True
    assert result["archive_format"] == "zip"
    assert result["source_format"] == "obj"
    assert result["imported_objects"] == ["GeneratedReferenceAsset"]
    assert result["packed_images"] == ["material_0.png"]
    assert result["failed_packing_images"] == []
    assert fake_bpy.data.images[0].pack_calls == 1
    assert import_calls and import_calls[0][0] == "obj"
    assert import_calls[0][1].endswith("model.obj")


def test_scene_tools_report_parent_children_and_world_transforms(monkeypatch):
    module, fake_bpy = _load_module(
        monkeypatch,
        "scene_tools_under_test",
        "addon/blender_mcpv_addon/server_scene_tools_mixin.py",
    )

    root = FakeObject(
        "ModelRoot",
        "EMPTY",
        location=(1.0, 2.0, 3.0),
        world_scale=(10.0, 10.0, 10.0),
    )
    mesh = FakeObject(
        "Mesh",
        "MESH",
        parent=root,
        location=(4.0, 5.0, 6.0),
        local_scale=(1.0, 1.0, 1.0),
        world_scale=(10.0, 10.0, 10.0),
        data=FakeMeshData("MeshData"),
    )

    for obj in (root, mesh):
        fake_bpy.data.objects.add(obj)
    fake_bpy.context.scene.objects = [root, mesh]

    server = module.ServerSceneToolsMixin()
    server._get_aabb = lambda _obj: [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]
    scene_info = server.get_scene_info(include_bbox=False)
    scene_objects = {entry["name"]: entry for entry in scene_info["objects"]}
    mesh_info = server.get_object_info("Mesh")

    assert scene_objects["Mesh"]["parent"] == "ModelRoot"
    assert mesh_info["parent"] == "ModelRoot"
    assert mesh_info["children"] == []
    assert mesh_info["world_location"] == [4.0, 5.0, 6.0]
    assert mesh_info["world_scale"] == [10.0, 10.0, 10.0]
