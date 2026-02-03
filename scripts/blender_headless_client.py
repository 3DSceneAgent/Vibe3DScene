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
import time
from typing import Any


def _extract_script_args() -> list[str]:
    if "--" not in sys.argv:
        return []
    return sys.argv[sys.argv.index("--") + 1 :]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
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


def _enable_addon(bpy: Any, module: str) -> None:
    addons = bpy.context.preferences.addons
    if module in addons:
        return
    bpy.ops.preferences.addon_enable(module=module)


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
    addon = importlib.import_module(module)
    for func_name in ("start_server", "start_addon", "start_mcpv_server"):
        func = getattr(addon, func_name, None)
        if callable(func):
            try:
                func(host=host, port=port)
            except TypeError:
                func()
            return True
    return False


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
    addon_module = args.addon or os.getenv("BLENDER_ADDON_MODULE", "blender_mcpv_addon")
    operator_path = args.operator or os.getenv("BLENDER_ADDON_START_OP", "blendermcpv.start_server")

    print(f"Starting addon '{addon_module}' on {host}:{port}")
    _enable_addon(bpy, addon_module)

    started = False
    if operator_path:
        started = _call_operator(bpy, operator_path, host, port)
    if not started:
        started = _call_addon_function(addon_module, host, port)

    if not started:
        print(
            "Addon enabled but server start entrypoint not found. "
            "Set BLENDER_ADDON_START_OP or pass --operator to call a custom operator."
        )

    if args.exit_after_start:
        return 0

    # Keep Blender alive for the socket server.
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
