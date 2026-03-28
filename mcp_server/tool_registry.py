from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

from mcp_server import runtime
from mcp_server.tools.asset_gen.hunyuan3d import generate_hunyuan3d_model
from mcp_server.tools.asset_gen.rodin import (
    generate_hyper3d_model_via_images,
    generate_hyper3d_model_via_text,
    import_generated_asset,
    poll_rodin_job_status,
)
from mcp_server.tools.asset_gen.sam_reconstruct import reconstruct_full_scene
from mcp_server.tools.asset_gen.trellis2 import generate_trellis2_model
from mcp_server.tools.asset_gen.tripo import generate_tripo3d_model
from mcp_server.tools.asset_retrieval.objaverse_retrieval import (
    import_retrieved_asset,
    search_3d_assets_by_text,
)
from mcp_server.tools.asset_retrieval.polyhaven import (
    download_polyhaven_asset,
    search_polyhaven_assets,
    set_texture,
)
from mcp_server.tools.asset_retrieval.scenesmith_retrieval import (
    apply_ambientcg_material,
    import_hssd_asset,
    search_ambientcg_materials,
    search_hssd_assets,
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
    get_viewport_screenshot,
    import_blend_contents,
    import_glb_model,
)
from mcp_server.tools.memory.session_tools import clear_scene, undo_last_snapshot
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

ToolCondition = Callable[[], Optional[str]]


@dataclass(frozen=True)
class ToolSpec:
    func: Any
    conditions: tuple[ToolCondition, ...] = ()
    service_dependency: str | None = None
    service_reason: str | None = None


def register_mcp_tools(mcp, logger) -> list[str]:
    global _tools_registered, _enabled_tool_names
    if _tools_registered:
        return _enabled_tool_names

    mode_name = runtime.get_blender_mode() or "<unset>"
    retrieval_backend = runtime.get_retrieval_provider()
    enable_hunyuan = runtime.is_hunyuan_tool_enabled()
    enable_polyhaven = runtime.is_polyhaven_tool_enabled()
    enable_rodin = runtime.is_rodin_tool_enabled()
    enable_tripo = runtime.is_tripo_tool_enabled()
    enable_trellis2 = runtime.is_trellis2_tool_enabled()
    enable_retrieval = runtime.is_retrieval_tool_enabled()
    enable_scenesmith_hssd = runtime.is_scenesmith_hssd_tool_enabled()
    enable_scenesmith_ambientcg = runtime.is_scenesmith_ambientcg_tool_enabled()
    enable_infinigen = runtime.is_infinigen_tool_enabled()
    enable_sketchfab = runtime.is_sketchfab_tool_enabled()
    sketchfab_api_reachable = runtime.probe_sketchfab_api(logger) if enable_sketchfab else None
    has_rodin_key = bool(runtime.get_rodin_api_key())
    has_tripo_key = bool(runtime.get_tripo_api_key())
    has_sketchfab_key = bool(runtime.get_sketchfab_api_key())
    generator_switches = {
        "ENABLE_RODIN": runtime.parse_env_bool("ENABLE_RODIN", False),
        "ENABLE_TRIPO": runtime.parse_env_bool("ENABLE_TRIPO", False),
        "ENABLE_TRELLIS2": runtime.parse_env_bool("ENABLE_TRELLIS2", False),
        "ENABLE_HUNYUAN": runtime.parse_env_bool("ENABLE_HUNYUAN", False),
    }

    def _env_display(name: str) -> str:
        raw = os.getenv(name)
        if raw is None:
            return "<unset>"
        value = raw.strip()
        return value or "<empty>"

    def _format_mode_requirement(*allowed: str) -> str:
        if len(allowed) == 1:
            return allowed[0]
        return "{" + ", ".join(allowed) + "}"

    def _require_blender_mode(*allowed: str) -> ToolCondition:
        def _check() -> Optional[str]:
            actual = runtime.get_blender_mode() or "<unset>"
            if actual in allowed:
                return None
            return (
                f"BLENDER_MODE={_format_mode_requirement(*allowed)} required "
                f"(actual={actual})"
            )

        return _check

    def _require_env_true(name: str, default: bool = False) -> ToolCondition:
        def _check() -> Optional[str]:
            if runtime.parse_env_bool(name, default):
                return None
            return f"{name}=true required (actual={_env_display(name)})"

        return _check

    def _require_env_configured(name: str) -> ToolCondition:
        def _check() -> Optional[str]:
            if _env_display(name) not in {"<unset>", "<empty>"}:
                return None
            return f"{name} must be configured"

        return _check

    def _require_retrieval_provider(expected: str) -> ToolCondition:
        def _check() -> Optional[str]:
            actual = runtime.get_retrieval_provider()
            if actual == expected:
                return None
            return (
                f"ASSET_RETRIEVAL_BACKEND={expected} required "
                f"(actual={actual or '<unset>'})"
            )

        return _check

    def _require_sketchfab_api_reachable() -> ToolCondition:
        def _check() -> Optional[str]:
            if sketchfab_api_reachable:
                return None
            return "Sketchfab API must be reachable"

        return _check

    def _is_sketchfab_fully_enabled() -> bool:
        return (
            runtime.is_sketchfab_tool_enabled()
            and bool(runtime.get_sketchfab_api_key())
            and bool(sketchfab_api_reachable)
        )

    enabled_generator_switches = [
        name for name, is_enabled in generator_switches.items() if is_enabled
    ]
    if len(enabled_generator_switches) > 1:
        raise RuntimeError(
            "Invalid tool configuration: ENABLE_RODIN, ENABLE_TRIPO, ENABLE_TRELLIS2, "
            "and ENABLE_HUNYUAN are mutually exclusive; enable only one. "
            f"Currently enabled: {', '.join(enabled_generator_switches)}."
        )
    if enable_retrieval and _is_sketchfab_fully_enabled():
        raise RuntimeError(
            "Invalid tool configuration: ASSET_RETRIEVAL_BACKEND must be 'disabled' when ENABLE_SKETCHFAB=true."
        )

    service_status = runtime.probe_conditional_services(logger)
    logger.info(
        (
            "Tool-gating context: BLENDER_MODE=%s ENABLE_HUNYUAN=%s "
            "ENABLE_POLYHAVEN=%s "
            "ENABLE_RODIN=%s ENABLE_TRIPO=%s ENABLE_TRELLIS2=%s ASSET_RETRIEVAL_BACKEND=%s "
            "SCENESMITH_HSSD=%s ENABLE_AMBIENTCG=%s "
            "ENABLE_INFINIGEN=%s ENABLE_SKETCHFAB=%s RODIN_API_KEY_SET=%s "
            "TRIPO_API_KEY_SET=%s "
            "SKETCHFAB_API_KEY_SET=%s SKETCHFAB_API_REACHABLE=%s"
        ),
        mode_name,
        enable_hunyuan,
        enable_polyhaven,
        enable_rodin,
        enable_tripo,
        enable_trellis2,
        retrieval_backend,
        enable_scenesmith_hssd,
        enable_scenesmith_ambientcg,
        enable_infinigen,
        enable_sketchfab,
        has_rodin_key,
        has_tripo_key,
        has_sketchfab_key,
        sketchfab_api_reachable,
    )

    tool_specs: list[ToolSpec] = [
        ToolSpec(get_scene_info),
        ToolSpec(get_object_info),
        ToolSpec(
            get_viewport_screenshot,
            conditions=(_require_blender_mode("local-client"),),
        ),
        ToolSpec(clear_scene),
        ToolSpec(delete_objects),
        ToolSpec(execute_blender_code),
        ToolSpec(
            search_polyhaven_assets,
            conditions=(_require_env_true("ENABLE_POLYHAVEN", True),),
        ),
        ToolSpec(
            download_polyhaven_asset,
            conditions=(_require_env_true("ENABLE_POLYHAVEN", True),),
        ),
        ToolSpec(
            set_texture,
            conditions=(_require_env_true("ENABLE_POLYHAVEN", True),),
        ),
        ToolSpec(import_glb_model),
        ToolSpec(import_blend_contents),
        ToolSpec(
            get_infinigen_available_assets,
            conditions=(_require_env_true("ENABLE_INFINIGEN"),),
            service_dependency="pcg_integrator",
            service_reason="pcg_integrator service must be healthy",
        ),
        ToolSpec(
            generate_infinigen_assets,
            conditions=(_require_env_true("ENABLE_INFINIGEN"),),
            service_dependency="pcg_integrator",
            service_reason="pcg_integrator service must be healthy",
        ),
        ToolSpec(
            reconstruct_full_scene,
            conditions=(_require_env_true("ENABLE_SAM_RECONSTRUCT"),),
            service_dependency="sam_reconstruct",
            service_reason="sam_reconstruct service must be healthy",
        ),
        ToolSpec(
            generate_trellis2_model,
            conditions=(
                _require_blender_mode("headless"),
                _require_env_true("ENABLE_TRELLIS2"),
            ),
            service_dependency="trellis2",
            service_reason="trellis2 service must be healthy",
        ),
        ToolSpec(
            generate_tripo3d_model,
            conditions=(
                _require_blender_mode("local-client", "headless"),
                _require_env_true("ENABLE_TRIPO"),
                _require_env_configured("TRIPO_API_KEY"),
            ),
        ),
        ToolSpec(
            generate_hyper3d_model_via_text,
            conditions=(
                _require_blender_mode("local-client", "headless"),
                _require_env_true("ENABLE_RODIN"),
                _require_env_configured("RODIN_API_KEY"),
            ),
        ),
        ToolSpec(
            generate_hyper3d_model_via_images,
            conditions=(
                _require_blender_mode("local-client", "headless"),
                _require_env_true("ENABLE_RODIN"),
                _require_env_configured("RODIN_API_KEY"),
            ),
        ),
        ToolSpec(
            poll_rodin_job_status,
            conditions=(
                _require_blender_mode("local-client", "headless"),
                _require_env_true("ENABLE_RODIN"),
                _require_env_configured("RODIN_API_KEY"),
            ),
        ),
        ToolSpec(
            import_generated_asset,
            conditions=(
                _require_blender_mode("local-client", "headless"),
                _require_env_true("ENABLE_RODIN"),
                _require_env_configured("RODIN_API_KEY"),
            ),
        ),
        ToolSpec(
            search_sketchfab_models,
            conditions=(
                _require_env_true("ENABLE_SKETCHFAB"),
                _require_env_configured("SKETCHFAB_API_KEY"),
                _require_sketchfab_api_reachable(),
            ),
        ),
        ToolSpec(
            get_sketchfab_model_preview,
            conditions=(
                _require_env_true("ENABLE_SKETCHFAB"),
                _require_env_configured("SKETCHFAB_API_KEY"),
                _require_sketchfab_api_reachable(),
            ),
        ),
        ToolSpec(
            download_sketchfab_model,
            conditions=(
                _require_env_true("ENABLE_SKETCHFAB"),
                _require_env_configured("SKETCHFAB_API_KEY"),
                _require_sketchfab_api_reachable(),
            ),
        ),
        ToolSpec(
            generate_hunyuan3d_model,
            conditions=(
                _require_blender_mode("headless"),
                _require_env_true("ENABLE_HUNYUAN"),
            ),
        ),
        ToolSpec(
            search_3d_assets_by_text,
            conditions=(_require_retrieval_provider("objaverse"),),
            service_dependency="objaverse_retrieval",
            service_reason="objaverse_retrieval service must be healthy",
        ),
        ToolSpec(
            import_retrieved_asset,
            conditions=(_require_retrieval_provider("objaverse"),),
            service_dependency="objaverse_retrieval",
            service_reason="objaverse_retrieval service must be healthy",
        ),
        ToolSpec(
            search_hssd_assets,
            conditions=(_require_retrieval_provider("scenesmith"),),
            service_dependency="scenesmith_hssd",
            service_reason="scenesmith_hssd service must be healthy (/hssd/healthz)",
        ),
        ToolSpec(
            import_hssd_asset,
            conditions=(_require_retrieval_provider("scenesmith"),),
            service_dependency="scenesmith_hssd",
            service_reason="scenesmith_hssd service must be healthy (/hssd/healthz)",
        ),
        ToolSpec(
            search_ambientcg_materials,
            conditions=(_require_env_true("ENABLE_AMBIENTCG"),),
            service_dependency="scenesmith_ambientcg",
            service_reason="scenesmith_ambientcg service must be healthy (/ambientcg/healthz)",
        ),
        ToolSpec(
            apply_ambientcg_material,
            conditions=(_require_env_true("ENABLE_AMBIENTCG"),),
            service_dependency="scenesmith_ambientcg",
            service_reason="scenesmith_ambientcg service must be healthy (/ambientcg/healthz)",
        ),
        ToolSpec(
            render_from_objects,
            conditions=(_require_blender_mode("headless"),),
        ),
        ToolSpec(
            render_from_camera,
            conditions=(_require_blender_mode("headless"),),
        ),
        ToolSpec(
            camera_set_pose,
            conditions=(_require_blender_mode("headless"),),
        ),
        ToolSpec(
            camera_observe,
            conditions=(_require_blender_mode("headless"),),
        ),
        ToolSpec(
            camera_act,
            conditions=(_require_blender_mode("headless"),),
        ),
        ToolSpec(
            observe_scene_global,
            conditions=(_require_blender_mode("headless"),),
        ),
        ToolSpec(
            undo_last_snapshot,
            conditions=(_require_blender_mode("headless"),),
        ),
    ]

    enabled: list[str] = []
    disabled: list[tuple[str, list[str]]] = []
    tool_status_lines: list[tuple[str, str, str]] = []
    for spec in tool_specs:
        unmet_conditions = [
            reason
            for condition in spec.conditions
            if (reason := condition()) is not None
        ]
        if not unmet_conditions and spec.service_dependency and not service_status.get(
            spec.service_dependency, False
        ):
            unmet_conditions.append(
                spec.service_reason
                or f"{spec.service_dependency} service must be healthy"
            )
        if unmet_conditions:
            disabled.append((spec.func.__name__, unmet_conditions))
            tool_status_lines.append(
                (spec.func.__name__, "DISABLED", "; ".join(unmet_conditions))
            )
            continue
        mcp.add_tool(spec.func)
        enabled.append(spec.func.__name__)
        tool_status_lines.append((spec.func.__name__, "ENABLED", "ready"))

    _enabled_tool_names = enabled
    _tools_registered = True

    summary = (
        f"enabled={len(enabled)} disabled={len(disabled)} total={len(tool_specs)}"
    )
    print(f"[mcp_server] tool_registry_summary {summary}", flush=True)
    logger.info("MCP tool registry summary: %s", summary)
    for tool_name, status, detail in tool_status_lines:
        print(
            f"[mcp_server] tool_status name={tool_name} status={status.lower()} detail={detail}",
            flush=True,
        )
        logger.info(
            "MCP tool status | %-32s | %-8s | %s",
            tool_name,
            status,
            detail,
        )

    return enabled
