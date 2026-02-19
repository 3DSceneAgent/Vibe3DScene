from __future__ import annotations

from typing import Any, Callable, Optional

from mcp_server import runtime
from mcp_server.tools.asset_gen.hunyuan3d import generate_hunyuan3d_model
from mcp_server.tools.asset_gen.rodin import (
    generate_hyper3d_model_via_images,
    generate_hyper3d_model_via_text,
    import_generated_asset,
    poll_rodin_job_status,
)
from mcp_server.tools.asset_gen.trellis2 import generate_trellis2_model
from mcp_server.tools.asset_retrieval.objaverse_retrieval import (
    import_retrieved_asset,
    search_3d_assets_by_text,
)
from mcp_server.tools.asset_retrieval.polyhaven import (
    download_polyhaven_asset,
    search_polyhaven_assets,
    set_texture,
)
from mcp_server.tools.asset_retrieval.sketchfab import (
    download_sketchfab_model,
    get_sketchfab_model_preview,
    search_sketchfab_models,
)
from mcp_server.tools.base import (
    delete_objects,
    execute_blender_code,
    get_object_info,
    get_scene_info,
    import_glb_model,
)
from mcp_server.tools.memory.session_tools import undo_last_snapshot
from mcp_server.tools.multimodal.camera_tools import (
    camera_act,
    camera_observe,
    camera_set_pose,
    observe_scene_global,
    render_from_camera,
    render_from_objects,
)
from mcp_server.tools.pcg.infinigen import (
    generate_infinigen_assets,
    get_infinigen_available_assets,
)

_tools_registered = False
_enabled_tool_names: list[str] = []


def register_mcp_tools(mcp, logger) -> list[str]:
    global _tools_registered, _enabled_tool_names
    if _tools_registered:
        return _enabled_tool_names

    mode_name = runtime.get_blender_mode() or "<unset>"
    enable_hunyuan = runtime.is_hunyuan_tool_enabled()
    enable_rodin = runtime.is_rodin_tool_enabled()
    enable_trellis2 = runtime.is_trellis2_tool_enabled()
    enable_retrieval = runtime.is_retrieval_tool_enabled()
    enable_infinigen = runtime.is_infinigen_tool_enabled()
    enable_sketchfab = runtime.is_sketchfab_tool_enabled()
    has_rodin_key = bool(runtime.get_rodin_api_key())
    has_sketchfab_key = bool(runtime.get_sketchfab_api_key())
    generator_switches = {
        "ENABLE_RODIN": runtime.parse_env_bool("ENABLE_RODIN", False),
        "ENABLE_TRELLIS2": runtime.parse_env_bool("ENABLE_TRELLIS2", False),
        "ENABLE_HUNYUAN": runtime.parse_env_bool("ENABLE_HUNYUAN", False),
    }

    def _is_rodin_fully_enabled() -> bool:
        return runtime.is_rodin_tool_enabled() and bool(runtime.get_rodin_api_key())

    def _is_sketchfab_fully_enabled() -> bool:
        return runtime.is_sketchfab_tool_enabled() and bool(runtime.get_sketchfab_api_key())

    enabled_generator_switches = [
        name for name, is_enabled in generator_switches.items() if is_enabled
    ]
    if len(enabled_generator_switches) > 1:
        raise RuntimeError(
            "Invalid tool configuration: ENABLE_RODIN, ENABLE_TRELLIS2, and "
            "ENABLE_HUNYUAN are mutually exclusive; enable only one. "
            f"Currently enabled: {', '.join(enabled_generator_switches)}."
        )
    if enable_retrieval and _is_sketchfab_fully_enabled():
        raise RuntimeError(
            "Invalid tool configuration: ENABLE_RETRIEVAL and ENABLE_SKETCHFAB cannot both be enabled."
        )

    service_status = runtime.probe_conditional_services(logger)
    logger.info(
        (
            "Tool-gating context: BLENDER_MODE=%s ENABLE_HUNYUAN=%s "
            "ENABLE_RODIN=%s ENABLE_TRELLIS2=%s ENABLE_RETRIEVAL=%s "
            "ENABLE_INFINIGEN=%s ENABLE_SKETCHFAB=%s RODIN_API_KEY_SET=%s SKETCHFAB_API_KEY_SET=%s"
        ),
        mode_name,
        enable_hunyuan,
        enable_rodin,
        enable_trellis2,
        enable_retrieval,
        enable_infinigen,
        enable_sketchfab,
        has_rodin_key,
        has_sketchfab_key,
    )

    tool_specs: list[
        tuple[Any, Optional[str], Optional[Callable[[], bool]], Optional[str]]
    ] = [
        (get_scene_info, None, None, None),
        (get_object_info, None, None, None),
        (delete_objects, None, None, None),
        (execute_blender_code, None, None, None),
        (search_polyhaven_assets, None, None, None),
        (download_polyhaven_asset, None, None, None),
        (set_texture, None, None, None),
        (import_glb_model, None, None, None),
        (
            get_infinigen_available_assets,
            "pcg_integrator",
            runtime.is_infinigen_tool_enabled,
            "requires ENABLE_INFINIGEN=true",
        ),
        (
            generate_infinigen_assets,
            "pcg_integrator",
            runtime.is_infinigen_tool_enabled,
            "requires ENABLE_INFINIGEN=true",
        ),
        (
            generate_trellis2_model,
            "trellis2",
            runtime.is_trellis2_tool_enabled,
            "requires BLENDER_MODE=headless and ENABLE_TRELLIS2=true",
        ),
        (
            generate_hyper3d_model_via_text,
            None,
            _is_rodin_fully_enabled,
            "requires BLENDER_MODE in {local-client, headless} and ENABLE_RODIN=true and RODIN_API_KEY configured",
        ),
        (
            generate_hyper3d_model_via_images,
            None,
            _is_rodin_fully_enabled,
            "requires BLENDER_MODE in {local-client, headless} and ENABLE_RODIN=true and RODIN_API_KEY configured",
        ),
        (
            poll_rodin_job_status,
            None,
            _is_rodin_fully_enabled,
            "requires BLENDER_MODE in {local-client, headless} and ENABLE_RODIN=true and RODIN_API_KEY configured",
        ),
        (
            import_generated_asset,
            None,
            _is_rodin_fully_enabled,
            "requires BLENDER_MODE in {local-client, headless} and ENABLE_RODIN=true and RODIN_API_KEY configured",
        ),
        (
            search_sketchfab_models,
            None,
            _is_sketchfab_fully_enabled,
            "requires ENABLE_SKETCHFAB=true and SKETCHFAB_API_KEY configured",
        ),
        (
            get_sketchfab_model_preview,
            None,
            _is_sketchfab_fully_enabled,
            "requires ENABLE_SKETCHFAB=true and SKETCHFAB_API_KEY configured",
        ),
        (
            download_sketchfab_model,
            None,
            _is_sketchfab_fully_enabled,
            "requires ENABLE_SKETCHFAB=true and SKETCHFAB_API_KEY configured",
        ),
        (
            generate_hunyuan3d_model,
            None,
            runtime.is_hunyuan_tool_enabled,
            "requires BLENDER_MODE=headless and ENABLE_HUNYUAN=true",
        ),
        (
            search_3d_assets_by_text,
            "retrieval",
            runtime.is_retrieval_tool_enabled,
            "requires ENABLE_RETRIEVAL=true",
        ),
        (
            import_retrieved_asset,
            "retrieval",
            runtime.is_retrieval_tool_enabled,
            "requires ENABLE_RETRIEVAL=true",
        ),
        (render_from_objects, None, None, None),
        (render_from_camera, None, None, None),
        (camera_set_pose, None, None, None),
        (camera_observe, None, None, None),
        (camera_act, None, None, None),
        (observe_scene_global, None, None, None),
        (undo_last_snapshot, None, None, None),
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
