from __future__ import annotations

import logging
import os

import requests
from mcp.server.fastmcp import Context

from mcp_server.tools.base import import_glb_model

logger = logging.getLogger("BlenderMCPServer")


def generate_trellis2_model(
    ctx: Context,
    text_prompt: str,
    object_name: str,
    pipeline_type: str = "512",
    texture_size: int = 1024,
    timeout: int = 300,
) -> str:
    """Generate a 3D model using TRELLIS2 and auto-import in Blender."""
    try:
        trellis_host = os.getenv("TRELLIS2_HOST", "localhost")
        trellis_port = os.getenv("TRELLIS2_PORT", "8001")
        base_url = f"http://{trellis_host}:{trellis_port}"
        endpoint = f"{base_url}/api/v1/text-to-3d"
        payload = {
            "prompt": text_prompt,
            "generate_model": True,
            "generate_video": False,
            "pipeline_type": pipeline_type.replace('"', "").replace("'", ""),
            "texture_size": texture_size,
            "timeout": timeout,
        }
        response = requests.post(endpoint, data=payload, timeout=timeout)
        if response.status_code != 200:
            return (
                "Error: TRELLIS2 API request failed with status "
                f"{response.status_code}: {response.text}"
            )
        result = response.json()
        model_url = result.get("model_url")
        if not model_url:
            return "Error: No model_url in TRELLIS2 response"
        if not model_url.startswith("http"):
            model_url = f"{base_url}{model_url}"
        import_result = import_glb_model(ctx, model_url, object_name)
        return f"TRELLIS2 generation completed.\n{import_result}"
    except Exception as exc:
        logger.error("Error generating TRELLIS2 model: %s", str(exc))
        return f"Error generating TRELLIS2 model: {str(exc)}"
