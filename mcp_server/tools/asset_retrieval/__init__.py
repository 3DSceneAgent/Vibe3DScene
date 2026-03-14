"""Retrieval-oriented MCP tools."""

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

__all__ = [
    "download_polyhaven_asset",
    "download_sketchfab_model",
    "get_sketchfab_model_preview",
    "apply_ambientcg_material",
    "import_hssd_asset",
    "import_retrieved_asset",
    "search_ambientcg_materials",
    "search_3d_assets_by_text",
    "search_hssd_assets",
    "search_polyhaven_assets",
    "search_sketchfab_models",
    "set_texture",
]
