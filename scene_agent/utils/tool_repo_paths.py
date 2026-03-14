from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_TOOLS_ROOT_ENV = "AGENT_TOOLS_ROOT"
DEFAULT_AGENT_TOOLS_DIRNAME = "3DAgentTools"
GLB_IMPORT_SCRIPT_RELATIVE_PATH = Path("scripts") / "blender" / "glb_import.py"


def _read_env(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def get_agent_tools_root() -> Path:
    configured_root = _read_env(AGENT_TOOLS_ROOT_ENV)
    if configured_root:
        return Path(configured_root).expanduser().resolve(strict=False)
    return (REPO_ROOT.parent / DEFAULT_AGENT_TOOLS_DIRNAME).resolve(strict=False)


def get_agent_tools_glb_import_script() -> Path:
    return (get_agent_tools_root() / GLB_IMPORT_SCRIPT_RELATIVE_PATH).resolve(strict=False)
