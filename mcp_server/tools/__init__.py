"""Structured MCP tool modules.

Categories:
- base: foundational scene/object/code/import tools
- asset_gen: generative 3D tools
- asset_retrieval: retrieval/download/import tools
- pcg: procedural content generation tools
- multimodal: camera and rendering tools
- memory: session persistence and rollback tools
"""

from mcp_server.tools.asset_gen import (
    generate_hunyuan3d_model,
    generate_hyper3d_model_via_images,
    generate_hyper3d_model_via_text,
    generate_trellis2_model,
    import_generated_asset,
    poll_rodin_job_status,
)
from mcp_server.tools.asset_retrieval import (
    download_polyhaven_asset,
    download_sketchfab_model,
    get_sketchfab_model_preview,
    import_retrieved_asset,
    search_3d_assets_by_text,
    search_polyhaven_assets,
    search_sketchfab_models,
    set_texture,
)
from mcp_server.tools.base import (
    execute_blender_code,
    get_object_info,
    get_scene_info,
    get_viewport_screenshot,
    import_glb_model,
)
from mcp_server.tools.memory import get_session_persistence_status, undo_last_snapshot
from mcp_server.tools.multimodal import (
    camera_act,
    camera_observe,
    camera_set_pose,
    render_from_camera,
    render_from_objects,
)
from mcp_server.tools.pcg import generate_infinigen_assets, get_infinigen_available_assets
from mcp_server.tools.strategy import asset_creation_strategy_text

__all__ = [
    "asset_creation_strategy_text",
    "camera_act",
    "camera_observe",
    "camera_set_pose",
    "download_polyhaven_asset",
    "download_sketchfab_model",
    "execute_blender_code",
    "generate_hunyuan3d_model",
    "generate_hyper3d_model_via_images",
    "generate_hyper3d_model_via_text",
    "generate_infinigen_assets",
    "generate_trellis2_model",
    "get_infinigen_available_assets",
    "get_object_info",
    "get_scene_info",
    "get_session_persistence_status",
    "get_sketchfab_model_preview",
    "get_viewport_screenshot",
    "import_generated_asset",
    "import_glb_model",
    "import_retrieved_asset",
    "poll_rodin_job_status",
    "render_from_camera",
    "render_from_objects",
    "search_3d_assets_by_text",
    "search_polyhaven_assets",
    "search_sketchfab_models",
    "set_texture",
    "undo_last_snapshot",
]
