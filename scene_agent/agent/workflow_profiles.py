"""
Workflow profile and topology helpers.
"""
from __future__ import annotations

import os
from typing import Any, Literal, TypedDict

from scene_agent.agent.state import TaskMode

WorkflowTopology = Literal["single_agent", "dual_agent"]
WorkflowTopologyRequest = Literal["auto", "single_agent", "dual_agent"]
MemoryProfile = Literal["thread_shared_only", "shared_plus_role_private"]
MemoryProfileRequest = Literal["auto", "thread_shared_only", "shared_plus_role_private"]
AgentRole = Literal["general", "builder", "verifier"]
ToolProfile = Literal["all_tools", "read_only", "builder_default", "verifier_default"]


class WorkflowProfile(TypedDict):
    name: str
    topology: WorkflowTopology
    default_tool_profile: dict[AgentRole, ToolProfile]
    memory_profile: MemoryProfile
    budgets: dict[str, int]
    verify_policy: dict[str, Any]


def _parse_env_bool(raw_value: str | None, *, default: bool = False) -> bool:
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "on", "enabled"}


def normalize_workflow_topology_request(raw_value: Any) -> WorkflowTopologyRequest:
    if not isinstance(raw_value, str):
        return "auto"
    normalized = raw_value.strip().lower().replace("-", "_")
    if normalized in {"single", "single_agent"}:
        return "single_agent"
    if normalized in {"dual", "dual_agent"}:
        return "dual_agent"
    if normalized == "auto":
        return "auto"
    return "auto"


def normalize_memory_profile_request(raw_value: Any) -> MemoryProfileRequest:
    if not isinstance(raw_value, str):
        return "auto"
    normalized = raw_value.strip().lower().replace("-", "_")
    if normalized == "thread_shared_only":
        return "thread_shared_only"
    if normalized == "shared_plus_role_private":
        return "shared_plus_role_private"
    if normalized == "auto":
        return "auto"
    return "auto"


def resolve_memory_profile(
    requested_profile: MemoryProfileRequest,
) -> MemoryProfile:
    if requested_profile == "shared_plus_role_private":
        return "shared_plus_role_private"
    return "thread_shared_only"


def _planmode_dual_agent_default_enabled() -> bool:
    return _parse_env_bool(os.getenv("ENABLE_PLANMODE_DUAL_AGENT"), default=False)


def resolve_workflow_topology(
    *,
    task_mode: TaskMode,
    requested_topology: WorkflowTopologyRequest,
) -> WorkflowTopology:
    if task_mode != "plan_mode":
        return "single_agent"
    if requested_topology == "single_agent":
        return "single_agent"
    if requested_topology == "dual_agent":
        return "dual_agent"
    return "dual_agent" if _planmode_dual_agent_default_enabled() else "single_agent"


DEFAULT_WORKFLOW_PROFILE: WorkflowProfile = {
    "name": "default_single",
    "topology": "single_agent",
    "default_tool_profile": {
        "general": "all_tools",
        "builder": "builder_default",
        "verifier": "verifier_default",
    },
    "memory_profile": "thread_shared_only",
    "budgets": {
        "max_request_agent_turns": 50,
        "max_request_tool_batches": 40,
        "max_plan_replans": 3,
    },
    "verify_policy": {
        "use_verifier_agent": False,
    },
}
