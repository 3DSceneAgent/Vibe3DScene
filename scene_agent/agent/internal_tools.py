"""Internal non-Blender tools exposed to the agent."""

from __future__ import annotations

from typing import Any


def get_internal_agent_tools() -> list[Any]:
    # Todo lifecycle is evaluator-owned; no internal pseudo tools are exposed.
    return []
