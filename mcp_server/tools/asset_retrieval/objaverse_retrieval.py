from __future__ import annotations

import logging

import requests
from mcp.server.fastmcp import Context

from mcp_server.tools.base import import_glb_model
from scene_agent.utils.tool_service_endpoints import get_retrieval_base_url

logger = logging.getLogger("BlenderMCPServer")


def search_3d_assets_by_text(
    ctx: Context,
    query: str,
    top_k: int = 3,
) -> str:
    """Search for 3D assets in retrieval database using text queries."""
    base_url = get_retrieval_base_url()
    try:
        if top_k < 1 or top_k > 100:
            return f"Error: top_k must be between 1 and 100, got {top_k}"
        payload = {"query": query, "top_k": top_k}
        response = requests.post(f"{base_url}/search/text", json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        results_list = result.get("results", [])
        if not results_list:
            return f"No results found for query: '{query}'"

        output = f"Found {len(results_list)} assets for query: '{query}'\n"
        for i, asset in enumerate(results_list, 1):
            output += f"{i}. Asset ID: {asset.get('asset_id', 'N/A')}\n"
            output += f"   Similarity: {asset.get('similarity', 0):.3f}\n"
            output += f"   Description (EN): {asset.get('caption_en', 'N/A')}\n"
            if asset.get("caption_cn"):
                output += f"   Description (CN): {asset.get('caption_cn', '')}\n"
            output += f"   Model URL: {asset.get('model_url', 'N/A')}\n"
            if asset.get("objaverse_id"):
                output += f"   Objaverse ID: {asset.get('objaverse_id', '')}\n"
            output += "\n"
        output += (
            "\nDescriptions and similarity scores can be noisy. "
            "If a top result is plausibly relevant, import one candidate first instead of rejecting it only from the text.\n"
            "To import one result, use import_retrieved_asset(model_url=..., object_name=...). "
            "asset_id is only a reference label and is not required by the import tool."
        )
        return output
    except requests.exceptions.ConnectionError:
        return f"Cannot connect to retrieval service at {base_url}."
    except requests.exceptions.Timeout:
        return "Request to retrieval service timed out."
    except requests.exceptions.HTTPError as exc:
        return f"HTTP error from retrieval service: {exc.response.status_code} - {exc.response.text}"
    except Exception as exc:
        logger.error("Error searching 3D assets: %s", str(exc))
        return f"Error searching 3D assets: {str(exc)}"


def import_retrieved_asset(
    ctx: Context,
    model_url: str,
    object_name: str = None,
) -> str:
    """Import a 3D asset from retrieval database into Blender."""
    object_name = object_name or "RetrievedAsset"
    return import_glb_model(ctx, model_url, object_name)
