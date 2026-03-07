"""Request initialization node (router-free workflow entrypoint)."""

from __future__ import annotations

from typing import Any, Dict

from scene_agent.agent.memory_scope import resolve_memory_profile
from scene_agent.agent.state import AgentState
from scene_agent.agent.workflow_profiles import (
    normalize_workflow_topology_request,
    resolve_workflow_topology,
)
from scene_agent.config import get_settings
from scene_agent.utils.todo_helpers import coerce_non_negative_int

from .constants_workflow import (
    DEFAULT_MAX_PLAN_REPLANS,
    MODE_PLAN,
    ROLE_BUILDER,
    ROLE_GENERAL,
    TOPOLOGY_DUAL,
)
from .shared import request_budget, unfinished_todo_count


def initialize_request_node(state: AgentState) -> Dict[str, Any]:
    """
    Initialize per-request workflow state without a dedicated router LLM call.

    Design notes:
    - Always start in plan_mode with generous default budgets.
    - Let the main agent decide whether to answer directly or mutate scene/tools.
    - Keep topology/memory requests honored from explicit API hints.
    """
    unfinished_todos = unfinished_todo_count(state)
    mode = MODE_PLAN

    raw_topology_request = state.get("workflow_topology_request")
    if raw_topology_request is None:
        raw_topology_request = state.get("workflow_topology")
    requested_topology = normalize_workflow_topology_request(raw_topology_request)
    workflow_topology = resolve_workflow_topology(
        task_mode=mode,
        requested_topology=requested_topology,
    )

    raw_memory_profile_request = state.get("memory_profile_request")
    if raw_memory_profile_request is None:
        raw_memory_profile_request = state.get("memory_profile")
    memory_profile_request = "auto"
    if isinstance(raw_memory_profile_request, str):
        normalized_memory_request = raw_memory_profile_request.strip().lower().replace("-", "_")
        if normalized_memory_request in {
            "auto",
            "thread_shared_only",
            "shared_plus_role_private",
        }:
            memory_profile_request = normalized_memory_request
    if memory_profile_request == "auto" and workflow_topology == TOPOLOGY_DUAL:
        memory_profile = "shared_plus_role_private"
    else:
        memory_profile = resolve_memory_profile(memory_profile_request)

    current_task_id = state.get("task_id")
    if isinstance(current_task_id, str) and current_task_id.strip():
        normalized_task_id = current_task_id.strip()[:128]
    else:
        normalized_task_id = "plan"

    budget = request_budget(mode)

    active_role = ROLE_GENERAL
    if workflow_topology == TOPOLOGY_DUAL:
        active_role = ROLE_BUILDER

    active_todo_id = state.get("active_todo_id")
    if not isinstance(active_todo_id, str):
        active_todo_id = None

    max_plan_replans_default = DEFAULT_MAX_PLAN_REPLANS
    try:
        max_plan_replans_default = max(0, int(get_settings().plan_mode_max_replans))
    except Exception:
        pass
    max_plan_replans = coerce_non_negative_int(
        state.get("max_plan_replans"),
        default=max_plan_replans_default,
    )

    task_intent = "continue_existing_plan" if unfinished_todos > 0 else "direct_request"

    return {
        "task_mode": mode,
        "task_intent": task_intent,
        "task_id": normalized_task_id,
        "tool_policy": "allow_mutation",
        "workflow_topology_request": requested_topology,
        "memory_profile_request": memory_profile_request,
        "workflow_topology": workflow_topology,
        "memory_profile": memory_profile,
        "active_role": active_role,
        "request_agent_turns": 0,
        "request_tool_batches": 0,
        "builder_turn_count": 0,
        "verifier_turn_count": 0,
        "builder_stall_count": 0,
        "verification_mismatch_streak": 0,
        "quality_eval": {"status": "unknown", "reason": "not_evaluated"},
        "convergence_eval": {"status": "stable", "pattern": "none", "reason": "not_evaluated"},
        "recent_verification_signatures": [],
        "convergence_intervention_count": 0,
        "progress_eval": {"status": "continue", "reason": "not_evaluated"},
        "budget_eval": {"budget_ok": True, "stop_reason": None},
        "plan_replan_count": 0,
        "max_plan_replans": max_plan_replans,
        "todo_protocol_version": 1,
        "pending_todo_updates": [],
        "assistant_turn_kind": "no_calls",
        "active_todo_id": active_todo_id,
        "context_summary": "",
        "context_summary_message_count": 0,
        "context_compaction_count": 0,
        "transition_next": None,
        "transition_reason": "workflow_initialized_without_router",
        "max_request_agent_turns": budget["max_request_agent_turns"],
        "max_request_tool_batches": budget["max_request_tool_batches"],
        "request_stop_reason": None,
    }


__all__ = ["initialize_request_node"]
