from __future__ import annotations

import logging
from collections.abc import Iterable

from mcp_server import runtime
from scene_agent.agent.strategy_prompt import (
    asset_creation_strategy_text_from_tools,
    build_asset_creation_strategy_text,
)

logger = logging.getLogger("BlenderMCPServer")

_GLOBAL_FIRST_HINT = (
    "Global-first modeling principle:\n"
    "- Build and validate scene-level layout/composition first.\n"
    "- Refine object-level alignment and local details only after global checks are stable."
)


def _prepend_global_first_hint(strategy_text: str) -> str:
    if "Global-first modeling principle" in strategy_text:
        return strategy_text
    return f"{_GLOBAL_FIRST_HINT}\n\n{strategy_text}"


def asset_creation_strategy_text(
    available_tool_names: Iterable[str] | None = None,
) -> str:
    if available_tool_names is not None:
        return _prepend_global_first_hint(
            asset_creation_strategy_text_from_tools(available_tool_names)
        )

    service_status = runtime.probe_conditional_services(logger)

    sketchfab_ready = (
        runtime.is_sketchfab_tool_enabled()
        and bool(runtime.get_sketchfab_api_key())
        and runtime.probe_sketchfab_api(logger)
    )
    infinigen_ready = runtime.is_infinigen_tool_enabled() and service_status.get("pcg_integrator", False)
    trellis2_ready = runtime.is_trellis2_tool_enabled() and service_status.get("trellis2", False)
    rodin_ready = runtime.is_rodin_tool_enabled() and bool(runtime.get_rodin_api_key())
    tripo_ready = runtime.is_tripo_tool_enabled() and bool(runtime.get_tripo_api_key())
    hunyuan_ready = runtime.is_hunyuan_tool_enabled()
    objaverse_retrieval_ready = runtime.is_objaverse_retrieval_tool_enabled() and service_status.get(
        "objaverse_retrieval", False
    )
    scenesmith_hssd_ready = runtime.is_scenesmith_hssd_tool_enabled() and service_status.get(
        "scenesmith_hssd", False
    )
    scenesmith_ambientcg_ready = runtime.is_scenesmith_ambientcg_tool_enabled() and service_status.get(
        "scenesmith_ambientcg", False
    )
    sam_reconstruct_ready = runtime.is_sam_reconstruct_tool_enabled() and service_status.get(
        "sam_reconstruct", False
    )
    strategy_text = build_asset_creation_strategy_text(
        polyhaven_ready=runtime.is_polyhaven_tool_enabled(),
        sketchfab_ready=sketchfab_ready,
        infinigen_ready=infinigen_ready,
        trellis2_ready=trellis2_ready,
        rodin_ready=rodin_ready,
        tripo_ready=tripo_ready,
        hunyuan_ready=hunyuan_ready,
        objaverse_retrieval_ready=objaverse_retrieval_ready,
        scenesmith_hssd_ready=scenesmith_hssd_ready,
        scenesmith_ambientcg_ready=scenesmith_ambientcg_ready,
        sam_reconstruct_ready=sam_reconstruct_ready,
        undo_ready=True,
        clear_scene_ready=True,
    )
    return _prepend_global_first_hint(strategy_text)
