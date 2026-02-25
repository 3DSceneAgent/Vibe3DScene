"""
Helper script to enable the Blender addon and start its socket server in headless mode.

Usage (macOS example):
  /Applications/Blender.app/Contents/MacOS/Blender \\
    --background --python scripts/blender_headless_client.py -- \\
    --host 127.0.0.1 --port 9876 \\
    --addon blender_mcpv_addon

Optional environment variables:
  - BLENDER_ADDON_MODULE: addon module name (default: blender_mcpv_addon)
  - BLENDER_ADDON_START_OP: Blender operator path to start server (e.g. blender_mcpv.start_server)
  - BLENDER_HOST / BLENDER_PORT: fallback host/port
  - BLENDER_HEADLESS_HOST / BLENDER_HEADLESS_PORT: fallback host/port
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path
from typing import Any


def _extract_script_args() -> list[str]:
    if "--" not in sys.argv:
        return []
    return sys.argv[sys.argv.index("--") + 1 :]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--blend-path")
    parser.add_argument("--addon")
    parser.add_argument("--operator")
    parser.add_argument(
        "--exit-after-start",
        action="store_true",
        help="Exit Blender after starting the addon server.",
    )
    return parser.parse_args(_extract_script_args())


def _get_env_first(*keys: str, default: str | None = None) -> str | None:
    for key in keys:
        value = os.getenv(key)
        if value:
            return value
    return default


def _ensure_repo_addon_path() -> Path | None:
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent
    addon_dir = project_root / "addon"
    if addon_dir.exists() and str(addon_dir) not in sys.path:
        sys.path.insert(0, str(addon_dir))
    return addon_dir if addon_dir.exists() else None


def _import_addon_module(module: str) -> Any | None:
    repo_addon_dir = _ensure_repo_addon_path()
    try:
        addon = importlib.import_module(module)
    except Exception as exc:
        print(f"Failed to import addon module '{module}': {exc}")
        return None
    addon_file = getattr(addon, "__file__", "") or ""
    if repo_addon_dir and addon_file and not addon_file.startswith(str(repo_addon_dir)):
        # Prefer the repo addon if a different install is loaded.
        sys.modules.pop(module, None)
        try:
            addon = importlib.import_module(module)
        except Exception as exc:
            print(f"Failed to reload addon module '{module}' from repo: {exc}")
            return None
    return addon


def _enable_addon(bpy: Any, module: str) -> None:
    _ensure_repo_addon_path()
    addons = bpy.context.preferences.addons
    if module in addons:
        return
    try:
        bpy.ops.preferences.addon_enable(module=module)
    except Exception as exc:
        print(f"Failed to enable addon via preferences: {exc}")

    if module not in addons:
        addon = _import_addon_module(module)
        if addon and hasattr(addon, "register"):
            try:
                addon.register()
            except Exception as exc:
                print(f"Failed to register addon module '{module}': {exc}")


def _call_operator(bpy: Any, operator_path: str, host: str, port: int) -> bool:
    parts = operator_path.split(".")
    if len(parts) != 2:
        print(f"Invalid operator path: {operator_path}. Expected form 'category.operator'.")
        return False
    category, operator = parts
    ops_category = getattr(bpy.ops, category, None)
    if ops_category is None:
        print(f"Operator category not found: {category}")
        return False
    op = getattr(ops_category, operator, None)
    if op is None:
        print(f"Operator not found: {operator_path}")
        return False
    try:
        op(host=host, port=port)
    except TypeError:
        op()
    return True


def _call_addon_function(module: str, host: str, port: int) -> bool:
    """
    Call addon's blocking server start function.
    
    Priority order:
    1. start_server_blocking (new headless-optimized function)
    2. start_server (legacy, may not work in headless)
    """
    addon = _import_addon_module(module)
    if addon is None:
        return False
    
    # Try blocking function first (headless-optimized)
    for func_name in ("start_server_blocking", "start_server", "start_addon", "start_mcpv_server"):
        func = getattr(addon, func_name, None)
        if callable(func):
            try:
                print(f"Calling addon function: {func_name}")
                func(host=host, port=port, blocking=True)
                return True
            except TypeError:
                # Function doesn't accept blocking parameter, try without
                try:
                    func(host=host, port=port)
                    return True
                except TypeError:
                    func()
                    return True

    # Fallback: call server class directly if present
    try:
        server_module = importlib.import_module(f"{module}.server")
        server_cls = getattr(server_module, "BlenderMCPVisionServer", None)
        if server_cls:
            print("Calling addon server class directly")
            server = server_cls(host, port)
            server.start(blocking=True)
            return True
    except Exception as exc:
        print(f"Failed to start addon server directly: {exc}")
    return False


def _prepare_scene(bpy: Any, blend_path: str | None) -> None:
    loaded_blend = False
    if blend_path:
        candidate = os.path.abspath(blend_path)
        if os.path.exists(candidate):
            try:
                current_path = bpy.data.filepath or ""
                if os.path.abspath(current_path) != candidate:
                    print(f"Loading persisted blend before server start: {candidate}")
                    bpy.ops.wm.open_mainfile(filepath=candidate, load_ui=False)
                loaded_blend = True
            except Exception as exc:
                print(f"Failed to load blend '{candidate}': {exc}")
        else:
            print(f"Persisted blend not found, starting from empty scene: {candidate}")

    if loaded_blend:
        return

    try:
        bpy.ops.wm.read_factory_settings(use_empty=False)
        print("Initialized default startup scene for headless session")
    except Exception as exc:
        print(f"Failed to initialize default startup scene: {exc}")
        return

    try:
        cube = bpy.data.objects.get("Cube")
        if cube is not None:
            bpy.data.objects.remove(cube, do_unlink=True)
            print("Removed default Cube object")
    except Exception as exc:
        print(f"Failed to remove default Cube: {exc}")

    try:
        camera = bpy.data.objects.get("Camera")
        if camera is not None:
            bpy.data.objects.remove(camera, do_unlink=True)
            print("Removed default Camera object")
    except Exception as exc:
        print(f"Failed to remove default Camera: {exc}")

    try:
        scene = bpy.context.scene
        world = scene.world
        if world is None:
            world = bpy.data.worlds.new("World")
            scene.world = world
        world.use_nodes = True
        node_tree = world.node_tree
        if node_tree is None:
            raise RuntimeError("World node tree is unavailable")

        background = node_tree.nodes.get("Background")
        if background is None:
            background = node_tree.nodes.new(type="ShaderNodeBackground")
        world_output = node_tree.nodes.get("World Output")
        if world_output is None:
            world_output = node_tree.nodes.new(type="ShaderNodeOutputWorld")

        if "Color" in background.inputs:
            background.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
        if "Strength" in background.inputs:
            background.inputs["Strength"].default_value = 0.15

        has_background_link = any(
            link.from_node == background and link.to_node == world_output
            for link in node_tree.links
        )
        if not has_background_link:
            node_tree.links.new(background.outputs["Background"], world_output.inputs["Surface"])
        print("Configured world environment light with strength=0.15")
    except Exception as exc:
        print(f"Failed to configure world environment light: {exc}")


def main() -> int:
    try:
        import bpy  # type: ignore
    except Exception as exc:
        print(f"Failed to import bpy. Run inside Blender. Error: {exc}")
        return 1

    args = _parse_args()
    host = args.host or _get_env_first("BLENDER_HOST", "BLENDER_HEADLESS_HOST", default="localhost")
    port_value = args.port or _get_env_first("BLENDER_PORT", "BLENDER_HEADLESS_PORT")
    port = int(port_value) if port_value else 9876
    blend_path = args.blend_path or _get_env_first("SESSION_BLEND_PATH", "BLENDER_SESSION_BLEND_PATH")
    addon_module = args.addon or os.getenv("BLENDER_ADDON_MODULE", "blender_mcpv_addon")
    # Operator removed in headless refactor - use function entry point only
    operator_path = args.operator or os.getenv("BLENDER_ADDON_START_OP", "")

    _prepare_scene(bpy, blend_path)

    print(f"Starting addon '{addon_module}' on {host}:{port}")
    _enable_addon(bpy, addon_module)

    # Directly call addon function for headless mode (operators removed)
    started = _call_addon_function(addon_module, host, port)
    
    # Fallback to operator only if explicitly specified
    if not started and operator_path:
        started = _call_operator(bpy, operator_path, host, port)

    if not started:
        print(
            "Addon enabled but server start entrypoint not found. "
            "Set BLENDER_ADDON_START_OP or pass --operator to call a custom operator."
        )
        return 1

    if args.exit_after_start:
        return 0

    # No keepalive loop needed - server.start(blocking=True) runs in main thread
    # and blocks until server is stopped. Process stays alive naturally.
    print("Server started in blocking mode - process will stay alive")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
