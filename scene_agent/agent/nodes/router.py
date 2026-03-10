"""Workflow initialization and lightweight routing nodes."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

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
    MODE_DIRECT,
    MODE_PLAN,
    ROLE_BUILDER,
    ROLE_GENERAL,
    TOPOLOGY_DUAL,
)
from .shared import latest_human_message, unfinished_todo_count


class RouterDecision(BaseModel):
    needs_plan: bool
    reasoning: str


def _normalize_memory_profile_request(raw_value: Any) -> str:
    if not isinstance(raw_value, str):
        return "auto"
    normalized = raw_value.strip().lower().replace("-", "_")
    if normalized in {"auto", "thread_shared_only", "shared_plus_role_private"}:
        return normalized
    return "auto"


def _sanitize_task_id(raw_task_id: Any) -> str:
    if isinstance(raw_task_id, str) and raw_task_id.strip():
        return raw_task_id.strip()[:128]
    return "request"


def initialize_request_node(state: AgentState) -> dict[str, Any]:
    """Initialize request-scoped workflow counters and topology preferences."""
    unfinished_todos = unfinished_todo_count(state)

    raw_topology_request = state.get("workflow_topology_request")
    if raw_topology_request is None:
        raw_topology_request = state.get("workflow_topology")
    requested_topology = normalize_workflow_topology_request(raw_topology_request)
    workflow_topology = resolve_workflow_topology(
        # Keep topology planning-capable before router decides direct vs plan.
        task_mode=MODE_PLAN,
        requested_topology=requested_topology,
    )

    raw_memory_profile_request = state.get("memory_profile_request")
    if raw_memory_profile_request is None:
        raw_memory_profile_request = state.get("memory_profile")
    memory_profile_request = _normalize_memory_profile_request(raw_memory_profile_request)
    if memory_profile_request == "auto" and workflow_topology == TOPOLOGY_DUAL:
        memory_profile = "shared_plus_role_private"
    else:
        memory_profile = resolve_memory_profile(memory_profile_request)

    max_plan_replans_default = DEFAULT_MAX_PLAN_REPLANS
    try:
        max_plan_replans_default = max(0, int(get_settings().plan_mode_max_replans))
    except Exception:
        pass
    max_plan_replans = coerce_non_negative_int(
        state.get("max_plan_replans"),
        default=max_plan_replans_default,
    )

    active_todo_id = state.get("active_todo_id")
    if not isinstance(active_todo_id, str) or not active_todo_id.strip():
        active_todo_id = None

    return {
        "task_mode": MODE_DIRECT,
        "task_intent": "continue_existing_plan" if unfinished_todos > 0 else "direct_request",
        "task_id": _sanitize_task_id(state.get("task_id")),
        "tool_policy": "allow_mutation",
        "workflow_topology_request": requested_topology,
        "memory_profile_request": memory_profile_request,
        "workflow_topology": workflow_topology,
        "memory_profile": memory_profile,
        "active_role": ROLE_BUILDER if workflow_topology == TOPOLOGY_DUAL else ROLE_GENERAL,
        "request_agent_turns": 0,
        "request_tool_batches": 0,
        "builder_turn_count": 0,
        "verifier_turn_count": 0,
        "builder_stall_count": 0,
        "plan_replan_count": 0,
        "max_plan_replans": max_plan_replans,
        "todo_protocol_version": 1,
        "active_todo_id": active_todo_id,
        "context_summary": "",
        "context_summary_message_count": 0,
        "context_compaction_count": 0,
        "transition_next": None,
        "transition_reason": "workflow_initialized",
        "routed_to_plan": False,
        "current_todo_stall_count": 0,
        "overall_stall_count": 0,
        "verification_result": None,
        "evaluator_result": {"status": "initialized", "reason": "not_evaluated"},
    }


def router_node(
    state: AgentState,
    *,
    router_model: Any | None = None,
) -> dict[str, Any]:
    """Lightweight router selecting direct execution or plan decomposition."""
    unfinished_todos = unfinished_todo_count(state)
    if unfinished_todos > 0:
        decision = RouterDecision(
            needs_plan=True,
            reasoning=f"continue_existing_plan_with_{unfinished_todos}_unfinished_todos",
        )
    else:
        latest_user_request = latest_human_message(state)
        decision: RouterDecision | None = None
        router_fallback_reason = "router_model_unavailable_default_plan_mode"
        if router_model is not None:
            prompt = (
                "You are a lightweight workflow router for a 3D scene editing agent.\n"
                "Decide whether this request needs a multi-step todo plan.\n"
                "Return structured output only."
            )
            reference_count = len(state.get("request_reference_image_keys") or [])
            if reference_count == 0:
                reference_count = len(state.get("attached_image_ids") or [])
            router_input = (
                f"user_request: {latest_user_request}\n"
                f"reference_images_attached: {reference_count > 0}\n"
                f"unfinished_todos_count: {unfinished_todos}\n"
                "needs_plan=true when decomposition into ordered steps is required."
            )
            try:
                llm = router_model
                if hasattr(router_model, "with_config"):
                    llm = router_model.with_config(
                        tags=["nostream"],
                        run_name="router_internal",
                    )
                if hasattr(llm, "with_structured_output"):
                    llm = llm.with_structured_output(RouterDecision)
                decision_raw = llm.invoke(
                    [
                        SystemMessage(content=prompt),
                        HumanMessage(content=router_input),
                    ]
                )
                if isinstance(decision_raw, RouterDecision):
                    decision = decision_raw
                else:
                    decision = RouterDecision.model_validate(decision_raw)
            except Exception:
                decision = None
                router_fallback_reason = "router_model_error_default_plan_mode"
        if decision is None:
            decision = RouterDecision(
                needs_plan=True,
                reasoning=router_fallback_reason,
            )

    workflow_topology = str(state.get("workflow_topology") or "").strip()
    next_role = ROLE_BUILDER if workflow_topology == TOPOLOGY_DUAL else ROLE_GENERAL
    task_mode = MODE_PLAN if decision.needs_plan else MODE_DIRECT
    task_intent = "planned_request" if decision.needs_plan else "direct_request"
    if unfinished_todos > 0:
        task_intent = "continue_existing_plan"

    return {
        "task_mode": task_mode,
        "task_intent": task_intent,
        "routed_to_plan": bool(decision.needs_plan),
        "router_decision": decision.model_dump(mode="json"),
        "transition_reason": "router_needs_plan" if decision.needs_plan else "router_direct_mode",
        "active_role": next_role,
    }


__all__ = ["RouterDecision", "initialize_request_node", "router_node"]
