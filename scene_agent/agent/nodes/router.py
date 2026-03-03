"""Node implementations by category."""
from typing import Any, Dict
from langchain_core.messages import AIMessage
from scene_agent.agent.memory_scope import resolve_memory_profile
from scene_agent.agent.state import AgentState
from scene_agent.config import get_settings
from .shared import (
    DEFAULT_MAX_PLAN_REPLANS,
    MODE_CONVERSATION,
    MODE_PLAN,
    MODE_SINGLE_ACTION,
    ROLE_BUILDER,
    ROLE_GENERAL,
    ROUTER_MIN_CONFIDENCE,
    TOPOLOGY_DUAL,
)
from .shared import (
    RouterDecision,
)
from .shared import (
    build_router_clarification_question,
    coerce_non_negative_int,
    coerce_task_mode,
    invoke_router_decision,
    latest_human_message,
    request_budget,
    unfinished_todo_count,
)

from scene_agent.agent.workflow_profiles import (
    normalize_workflow_topology_request,
    resolve_workflow_topology,
)


def route_mode_node(
    state: AgentState,
    *,
    router_model: Any | None = None,
) -> Dict[str, Any]:
    """
    LLM-based router for task mode / intent classification.
    Low-confidence decisions require strict clarification before execution.
    """
    latest_user_request = latest_human_message(state)
    unfinished_todos = unfinished_todo_count(state)

    if unfinished_todos > 0:
        decision = RouterDecision(
            intent="continue_existing_plan",
            mode=MODE_PLAN,
            confidence=1.0,
            need_clarification=False,
            clarification_question="",
            requires_scene_mutation=True,
        )
    elif router_model is None:
        decision = RouterDecision(
            intent="clarification_needed",
            mode=MODE_CONVERSATION,
            confidence=0.0,
            need_clarification=True,
            clarification_question=build_router_clarification_question(latest_user_request),
            requires_scene_mutation=False,
        )
    else:
        decision = invoke_router_decision(
            state=state,
            router_model=router_model,
            latest_user_request=latest_user_request,
        )

    mode = coerce_task_mode(decision.mode)
    intent = decision.intent

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
    if memory_profile_request == "auto" and mode == MODE_PLAN and workflow_topology == TOPOLOGY_DUAL:
        memory_profile = "shared_plus_role_private"
    else:
        memory_profile = resolve_memory_profile(memory_profile_request)

    current_task_id = state.get("task_id")
    if isinstance(current_task_id, str) and current_task_id.strip():
        normalized_task_id = current_task_id.strip()[:128]
    else:
        if mode == MODE_CONVERSATION:
            normalized_task_id = "conversation"
        elif mode == MODE_SINGLE_ACTION:
            normalized_task_id = "single_action"
        else:
            normalized_task_id = "plan"

    budget = request_budget(mode)
    tool_policy = "allow_mutation"
    if mode == MODE_CONVERSATION:
        tool_policy = "forbid_mutation"
    elif mode == MODE_SINGLE_ACTION:
        tool_policy = "allow_mutation_limited"

    active_role = ROLE_GENERAL
    if mode == MODE_PLAN and workflow_topology == TOPOLOGY_DUAL:
        active_role = ROLE_BUILDER

    clarification_question = decision.clarification_question.strip()
    if not clarification_question:
        clarification_question = build_router_clarification_question(latest_user_request)
    # Keep action workflows flowing unless router explicitly asks for clarification.
    # Low-confidence auto-clarification is only enforced for conversation intents.
    need_clarification = bool(decision.need_clarification)
    if not need_clarification and mode == MODE_CONVERSATION and decision.confidence < ROUTER_MIN_CONFIDENCE:
        need_clarification = True
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

    return {
        "task_mode": mode,
        "task_intent": intent,
        "task_id": normalized_task_id,
        "router_decision": decision.model_dump(mode="json"),
        "router_confidence": float(decision.confidence),
        "router_need_clarification": need_clarification,
        "router_clarification_question": clarification_question,
        "tool_policy": tool_policy,
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
        "transition_reason": "router_initialized",
        "max_request_agent_turns": budget["max_request_agent_turns"],
        "max_request_tool_batches": budget["max_request_tool_batches"],
        "request_stop_reason": None,
    }

def route_mode_llm_node(state: AgentState, router_model: Any) -> Dict[str, Any]:
    """Explicit LLM router entrypoint used by graph wiring."""
    return route_mode_node(state, router_model=router_model)

def clarification_node(state: AgentState) -> Dict[str, Any]:
    question_raw = state.get("router_clarification_question")
    question = question_raw.strip() if isinstance(question_raw, str) and question_raw.strip() else (
        "Please clarify your goal: are you asking for explanation only, a single edit, "
        "or a multi-step scene build?"
    )
    return {
        "messages": [AIMessage(content=question)],
        "request_stop_reason": "clarification_required",
        "transition_next": "finalize",
        "transition_reason": "router_low_confidence_clarification_required",
    }
