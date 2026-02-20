from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from mcp.server.fastmcp import Context

logger = logging.getLogger("BlenderMCPServer")


def get_infinigen_available_assets(
    ctx: Context,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Fetch available Infinigen asset types from the API."""
    infinigen_host = os.getenv("INFINIGEN_HOST", "localhost")
    infinigen_port = os.getenv("INFINIGEN_PORT", "8003")
    base_url = f"http://{infinigen_host}:{infinigen_port}/api/v1"
    endpoint = f"{base_url}/infinigen/assets/available"
    try:
        logger.info("Fetching available Infinigen assets from %s", endpoint)
        response = requests.get(endpoint, timeout=timeout)
        if response.status_code != 200:
            return {
                "error": (
                    "Infinigen API request failed with status "
                    f"{response.status_code}: {response.text}"
                )
            }
        result = response.json()
        return {
            "success": True,
            "total_count": result.get("total_count", 0),
            "asset_types": result.get("asset_types", {}),
        }
    except requests.exceptions.Timeout:
        return {"error": f"Infinigen API request timed out after {timeout} seconds"}
    except requests.exceptions.ConnectionError:
        return {
            "error": (
                f"Could not connect to Infinigen API at {base_url}. "
                "Make sure Infinigen service is running."
            )
        }
    except Exception as exc:
        logger.exception("Failed to fetch available Infinigen assets")
        return {"error": f"Failed to fetch available assets: {str(exc)}"}


def generate_infinigen_assets(
    ctx: Context,
    asset_type: str,
    seed: Optional[int] = 42,
    timeout: int = 300,
    output_dir: Optional[str] = None,
    cleanup: bool = False,
) -> Dict[str, Any]:
    """Generate an Infinigen asset and download the .blend output."""
    if not asset_type:
        return {"error": "asset_type is required"}

    infinigen_host = os.getenv("INFINIGEN_HOST", "localhost")
    infinigen_port = os.getenv("INFINIGEN_PORT", "8003")
    base_url = f"http://{infinigen_host}:{infinigen_port}/api/v1"
    endpoint = f"{base_url}/infinigen/assets/generate"

    temp_dir_created = False
    target_dir = Path(output_dir) if output_dir else Path(
        os.path.join("/tmp", f"infinigen_{int(time.time() * 1000)}")
    )
    if not output_dir:
        temp_dir_created = True

    target_dir.mkdir(parents=True, exist_ok=True)
    blend_file_path = target_dir / f"{asset_type}.blend"

    data = {"asset_type": asset_type, "num_assets": 1, "output_folder": str(target_dir)}
    if seed is not None:
        data["seed"] = seed

    try:
        logger.info("Generating Infinigen asset: %s", asset_type)
        response = requests.post(endpoint, json=data, timeout=timeout, stream=True)
        if response.status_code != 200:
            return {
                "error": (
                    "Infinigen API request failed with status "
                    f"{response.status_code}: {response.text}"
                )
            }
        with open(blend_file_path, "wb") as handle:
            handle.write(response.content)
        if not blend_file_path.exists():
            return {"error": f"Blend file not found at: {blend_file_path}"}

        result = {
            "success": True,
            "asset_type": asset_type,
            "blend_file_path": str(blend_file_path),
            "output_dir": str(target_dir),
            "downloaded_bytes": blend_file_path.stat().st_size,
            "cleanup_required": temp_dir_created and not cleanup,
            "recommended_next_tool": "import_blend_contents",
            "recommended_next_action": (
                "Call import_blend_contents(blend_file_path=...) to merge this .blend into the current scene."
            ),
        }
        if cleanup and temp_dir_created:
            try:
                shutil.rmtree(target_dir)
                result["cleanup_performed"] = True
            except Exception as exc:
                logger.warning("Failed to clean up temp dir %s: %s", target_dir, exc)
                result["cleanup_performed"] = False
        return result
    except requests.exceptions.Timeout:
        return {"error": f"Infinigen request timed out after {timeout} seconds"}
    except requests.exceptions.ConnectionError:
        return {
            "error": (
                f"Could not connect to Infinigen API at {base_url}. "
                "Make sure Infinigen service is running."
            )
        }
    except Exception as exc:
        logger.exception("Failed to generate Infinigen assets")
        return {"error": f"Failed to generate Infinigen assets: {str(exc)}"}
