from __future__ import annotations

from typing import Any, Callable, Optional

from mcp_server import runtime
from mcp_server.tools.asset_tools import (
    download_sketchfab_model,
    generate_hunyuan3d_model,
    generate_hyper3d_model_via_images,
    generate_hyper3d_model_via_text,
    generate_infinigen_assets,
    generate_trellis2_model,
    get_hyper3d_status,
    get_infinigen_available_assets,
    get_sketchfab_model_preview,
    get_sketchfab_status,
    import_generated_asset,
    import_retrieved_asset,
    poll_rodin_job_status,
    search_3d_assets_by_text,
    search_sketchfab_models,
)
from mcp_server.tools.core_blender_tools import (
    create_camera_from_objects,
    create_camera_from_params,
    download_polyhaven_asset,
    execute_blender_code,
    get_object_info,
    get_scene_info,
    get_viewport_screenshot,
    import_glb_model,
    render_from_camera,
    render_from_objects,
    search_polyhaven_assets,
    set_texture,
)

_tools_registered = False
_enabled_tool_names: list[str] = []


def register_mcp_tools(mcp, logger) -> list[str]:
    global _tools_registered, _enabled_tool_names
    if _tools_registered:
        return _enabled_tool_names

    service_status = runtime.probe_conditional_services(logger)
    mode_name = runtime.get_blender_mode() or "<unset>"
    enable_hunyuan = runtime.is_hunyuan_tool_enabled()
    enable_rodin = runtime.is_rodin_tool_enabled()
    enable_trellis2 = runtime.is_trellis2_tool_enabled()
    enable_sketchfab = runtime.is_sketchfab_tool_enabled()
    logger.info(
        (
            "Tool-gating context: BLENDER_MODE=%s ENABLE_HUNYUAN=%s "
            "ENABLE_RODIN=%s ENABLE_TRELLIS2=%s ENABLE_SKETCHFAB=%s"
        ),
        mode_name,
        enable_hunyuan,
        enable_rodin,
        enable_trellis2,
        enable_sketchfab,
    )

    tool_specs: list[
        tuple[Any, Optional[str], Optional[Callable[[], bool]], Optional[str]]
    ] = [
        (get_scene_info, None, None, None),
        (get_object_info, None, None, None),
        (get_viewport_screenshot, None, None, None),
        (execute_blender_code, None, None, None),
        (search_polyhaven_assets, None, None, None),
        (download_polyhaven_asset, None, None, None),
        (set_texture, None, None, None),
        (import_glb_model, None, None, None),
        (get_infinigen_available_assets, "pcg_integrator", None, None),
        (generate_infinigen_assets, "pcg_integrator", None, None),
        (
            generate_trellis2_model,
            "trellis2",
            runtime.is_trellis2_tool_enabled,
            "requires BLENDER_MODE=headless and ENABLE_TRELLIS2=true",
        ),
        (
            get_hyper3d_status,
            None,
            runtime.is_rodin_tool_enabled,
            "requires BLENDER_MODE=local-client and ENABLE_RODIN=true",
        ),
        (
            generate_hyper3d_model_via_text,
            None,
            runtime.is_rodin_tool_enabled,
            "requires BLENDER_MODE=local-client and ENABLE_RODIN=true",
        ),
        (
            generate_hyper3d_model_via_images,
            None,
            runtime.is_rodin_tool_enabled,
            "requires BLENDER_MODE=local-client and ENABLE_RODIN=true",
        ),
        (
            poll_rodin_job_status,
            None,
            runtime.is_rodin_tool_enabled,
            "requires BLENDER_MODE=local-client and ENABLE_RODIN=true",
        ),
        (
            import_generated_asset,
            None,
            runtime.is_rodin_tool_enabled,
            "requires BLENDER_MODE=local-client and ENABLE_RODIN=true",
        ),
        (
            get_sketchfab_status,
            None,
            runtime.is_sketchfab_tool_enabled,
            "requires ENABLE_SKETCHFAB=true and SKETCHFAB_API_KEY configured",
        ),
        (
            search_sketchfab_models,
            None,
            runtime.is_sketchfab_tool_enabled,
            "requires ENABLE_SKETCHFAB=true and SKETCHFAB_API_KEY configured",
        ),
        (
            get_sketchfab_model_preview,
            None,
            runtime.is_sketchfab_tool_enabled,
            "requires ENABLE_SKETCHFAB=true and SKETCHFAB_API_KEY configured",
        ),
        (
            download_sketchfab_model,
            None,
            runtime.is_sketchfab_tool_enabled,
            "requires ENABLE_SKETCHFAB=true and SKETCHFAB_API_KEY configured",
        ),
        (
            generate_hunyuan3d_model,
            None,
            runtime.is_hunyuan_tool_enabled,
            "requires BLENDER_MODE=headless and ENABLE_HUNYUAN=true",
        ),
        (search_3d_assets_by_text, "retrieval", None, None),
        (import_retrieved_asset, None, None, None),
        (create_camera_from_objects, None, None, None),
        (create_camera_from_params, None, None, None),
        (render_from_objects, None, None, None),
        (render_from_camera, None, None, None),
    ]

    enabled: list[str] = []
    skipped: list[str] = []
    for func, dependency, gate_fn, gate_reason in tool_specs:
        if dependency and not service_status.get(dependency, False):
            skipped.append(f"{func.__name__} (requires {dependency})")
            continue
        if gate_fn and not gate_fn():
            skipped.append(f"{func.__name__} ({gate_reason or 'disabled by runtime gate'})")
            continue
        mcp.add_tool(func)
        enabled.append(func.__name__)

    _enabled_tool_names = enabled
    _tools_registered = True

    print(f"[mcp_server] enabled_tools={','.join(enabled)}", flush=True)
    if skipped:
        print(f"[mcp_server] skipped_tools={'; '.join(skipped)}", flush=True)
    logger.info("Enabled MCP tools: %s", ", ".join(enabled))
    if skipped:
        logger.info("Skipped MCP tools: %s", "; ".join(skipped))

    return enabled
