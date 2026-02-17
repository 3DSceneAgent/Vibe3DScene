from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def _load_camera_manager_module():
    """
    Load `addon/blender_mcpv_addon/camera_manager.py` with stubbed `bpy` / `mathutils`
    so we can unit-test pure-Python eviction logic outside Blender.
    """
    # Stub minimal modules required at import time.
    fake_bpy = types.ModuleType("bpy")

    class _FakeObjects:
        def __init__(self, existing: set[str]):
            self._existing = existing

        def get(self, name: str):
            return object() if name in self._existing else None

    fake_bpy.data = types.SimpleNamespace(objects=_FakeObjects(set()))
    sys.modules.setdefault("bpy", fake_bpy)

    fake_mathutils = types.ModuleType("mathutils")
    sys.modules.setdefault("mathutils", fake_mathutils)

    module_path = Path(__file__).resolve().parents[2] / "addon" / "blender_mcpv_addon" / "camera_manager.py"
    spec = importlib.util.spec_from_file_location("camera_manager_under_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, fake_bpy


def test_prune_local_cameras_enforces_limit_by_eviction_even_if_all_are_session_protected():
    """
    Regression test: if local cameras exceed the limit and all cameras are
    session-protected (active camera or focus target), we should still evict
    cameras (excluding pinned) to enforce `max_local_cameras`.
    """
    cm, fake_bpy = _load_camera_manager_module()

    CameraManager = cm.CameraManager
    CameraSessionState = cm.CameraSessionState

    manager = CameraManager.__new__(CameraManager)
    manager.cameras = {}
    manager.object_to_cameras = {}
    manager.session_state = CameraSessionState(target_object="Cube", active_camera_name="cam_active")
    manager.enabled = True
    manager.max_local_cameras = 2
    manager.idle_ttl_seconds = 0.0

    # Avoid Blender deletion side-effects in unit tests.
    manager._delete_camera_object = lambda *_args, **_kwargs: None  # type: ignore[assignment]

    # All cameras exist in Blender (so prune step 1 won't remove them).
    existing = {"cam_oldest", "cam_mid", "cam_active"}
    fake_bpy.data.objects._existing = existing  # type: ignore[attr-defined]

    def meta(*, last_used: float, pinned: bool = False):
        return types.SimpleNamespace(
            camera_kind="local_work",
            pinned=pinned,
            target_objects={"Cube"},  # focus target -> session-protected
            visible_objects=set(),
            is_valid=True,
            last_used=last_used,
            use_count=0,
        )

    manager.cameras["cam_oldest"] = meta(last_used=10.0)
    manager.cameras["cam_mid"] = meta(last_used=20.0)
    manager.cameras["cam_active"] = meta(last_used=30.0)

    result = manager.prune_local_cameras()

    assert result["remaining_local"] == 2
    assert "cam_active" in manager.cameras
