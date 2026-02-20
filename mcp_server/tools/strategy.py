from __future__ import annotations

import logging
from collections.abc import Iterable

from mcp_server import runtime
from scene_agent.agent.strategy_prompt import (
    asset_creation_strategy_text_from_tools,
    build_asset_creation_strategy_text,
)

logger = logging.getLogger("BlenderMCPServer")


def asset_creation_strategy_text(
    available_tool_names: Iterable[str] | None = None,
) -> str:
    if available_tool_names is not None:
        return asset_creation_strategy_text_from_tools(available_tool_names)

    service_status = runtime.probe_conditional_services(logger)

    sketchfab_ready = runtime.is_sketchfab_tool_enabled() and bool(runtime.get_sketchfab_api_key())
    infinigen_ready = runtime.is_infinigen_tool_enabled() and service_status.get("pcg_integrator", False)
    trellis2_ready = runtime.is_trellis2_tool_enabled() and service_status.get("trellis2", False)
    rodin_ready = runtime.is_rodin_tool_enabled() and bool(runtime.get_rodin_api_key())
    hunyuan_ready = runtime.is_hunyuan_tool_enabled()
    retrieval_ready = runtime.is_retrieval_tool_enabled() and service_status.get("retrieval", False)
    return build_asset_creation_strategy_text(
        sketchfab_ready=sketchfab_ready,
        infinigen_ready=infinigen_ready,
        trellis2_ready=trellis2_ready,
        rodin_ready=rodin_ready,
        hunyuan_ready=hunyuan_ready,
        retrieval_ready=retrieval_ready,
        undo_ready=True,
        clear_scene_ready=True,
    )
