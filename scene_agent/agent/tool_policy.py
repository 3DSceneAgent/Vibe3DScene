"""
Runtime tool-domain policy resolver.
"""
from __future__ import annotations

from typing import Any

from scene_agent.agent.state import TaskMode
from scene_agent.agent.workflow_profiles import AgentRole, ToolProfile

READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "get_scene_info",
        "get_object_info",
        "observe_scene_global",
        "camera_observe",
        "render_from_camera",
        "render_from_objects",
        "camera_act",
        "camera_set_pose",
        "get_viewport_screenshot",
    }
)


def dedupe_tool_names(raw_names: list[str] | None) -> list[str]:
    if not isinstance(raw_names, list):
        return []
    deduped: list[str] = []
    seen: set[str] = set()
    for raw_name in raw_names:
        if not isinstance(raw_name, str):
            continue
        name = raw_name.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        deduped.append(name)
    return deduped


def apply_requested_tool_filter(
    available_tool_names: list[str] | None,
    requested_tool_names: list[str] | None,
) -> list[str] | None:
    if available_tool_names is None:
        return None
    available = dedupe_tool_names(available_tool_names)
    if requested_tool_names is None:
        return available
    requested = set(dedupe_tool_names(requested_tool_names))
    return [name for name in available if name in requested]


def _coerce_non_negative_int(value: Any, *, default: int = 0) -> int:
    if isinstance(value, int) and value >= 0:
        return value
    return default


def _default_profile_for_role(mode: TaskMode, role: AgentRole) -> ToolProfile:
    if role == "verifier":
        return "verifier_default"
    if role == "builder":
        return "builder_default"
    if mode == "conversation_mode":
        return "read_only"
    return "all_tools"


def _filter_for_profile(tool_names: list[str], profile: ToolProfile) -> list[str]:
    if profile in {"read_only", "verifier_default"}:
        return [name for name in tool_names if name in READ_ONLY_TOOLS]
    if profile == "builder_default":
        return list(tool_names)
    return list(tool_names)


def resolve_effective_tool_names(
    *,
    mode: TaskMode,
    role: AgentRole,
    available_tool_names: list[str] | None,
    requested_tool_names: list[str] | None,
    request_tool_batches: int,
    max_request_tool_batches: int,
    explicit_profile: ToolProfile | None = None,
) -> tuple[list[str] | None, str | None]:
    selected = apply_requested_tool_filter(available_tool_names, requested_tool_names)
    if selected is None:
        return None, None

    if max_request_tool_batches >= 0 and request_tool_batches >= max_request_tool_batches:
        return [], "request_tool_budget_exhausted"

    profile = explicit_profile or _default_profile_for_role(mode, role)
    filtered = _filter_for_profile(selected, profile)

    if mode == "conversation_mode":
        filtered = _filter_for_profile(filtered, "read_only")
        return filtered, "conversation_mode_read_only"
    if role == "verifier":
        return filtered, "verifier_role_read_only"
    return filtered, None


def coerce_request_tool_budgets(
    *,
    request_tool_batches: Any,
    max_request_tool_batches: Any,
) -> tuple[int, int]:
    return (
        _coerce_non_negative_int(request_tool_batches),
        _coerce_non_negative_int(max_request_tool_batches, default=-1),
    )
