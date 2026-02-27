"""Node implementations by category."""
from typing import Any, Dict
from scene_agent.agent.memory_scope import merge_role_private_memory
from scene_agent.agent.state import AgentState, TodoItem, create_todo
from .shared import (
    DEFAULT_MAX_PLAN_REPLANS,
    MODE_CONVERSATION,
    MODE_PLAN,
    ROLE_BUILDER,
    ROLE_VERIFIER,
    TOPOLOGY_DUAL,
)
from .shared import (
    coerce_non_negative_int,
    coerce_task_mode,
    coerce_verification_dict,
    coerce_workflow_topology,
    extract_verifier_fix_instructions,
    latest_verification_payload,
    replan_budget_remaining,
    unfinished_todo_count,
)


def verifier_agent_node(state: AgentState) -> Dict[str, Any]:
    """
    Build compact structured verifier feedback from latest verification evidence.
    """
    verification_payload = latest_verification_payload(state)
    verification = coerce_verification_dict(verification_payload)
    raw_status = verification.get("status")
    normalized_status = raw_status.strip().lower() if isinstance(raw_status, str) else ""
    unfinished_todos = unfinished_todo_count(state)

    feedback_status = "needs_fix"
    if normalized_status in {"match", "pass", "passed"}:
        feedback_status = "pass"
    elif normalized_status == "catastrophic":
        feedback_status = "catastrophic"

    fix_instructions = extract_verifier_fix_instructions(verification)
    should_replan = False
    if feedback_status == "needs_fix":
        stall_count = coerce_non_negative_int(state.get("builder_stall_count"))
        should_replan = replan_budget_remaining(state) and (stall_count >= 2 or len(fix_instructions) == 0)

    ready_to_finalize = feedback_status == "pass" and unfinished_todos == 0
    confidence = 0.55
    if feedback_status == "pass":
        confidence = 0.9
    elif feedback_status == "catastrophic":
        confidence = 0.4

    verifier_feedback = {
        "status": feedback_status,
        "source_verification_status": normalized_status or "unknown",
        "ready_to_finalize": ready_to_finalize,
        "should_replan": should_replan,
        "focus_objects": [],
        "fix_instructions": fix_instructions,
        "confidence": confidence,
    }

    if isinstance(verification.get("reason"), str) and verification["reason"].strip():
        verifier_feedback["reason"] = verification["reason"].strip()
    elif fix_instructions:
        verifier_feedback["reason"] = fix_instructions[0]
    else:
        verifier_feedback["reason"] = "No explicit verification guidance was available."

    next_verifier_turns = coerce_non_negative_int(state.get("verifier_turn_count"))
    role_private_memory = merge_role_private_memory(
        state.get("role_private_memory"),
        role=ROLE_VERIFIER,
        patch={
            "last_feedback_status": verifier_feedback["status"],
            "last_feedback_reason": verifier_feedback["reason"],
            "last_feedback_confidence": verifier_feedback["confidence"],
        },
    )

    return {
        "verifier_feedback": verifier_feedback,
        "verifier_turn_count": next_verifier_turns,
        "active_role": ROLE_VERIFIER,
        "role_private_memory": role_private_memory,
    }

def verifier_feedback_node(state: AgentState) -> Dict[str, Any]:
    """Alias node for readability in graph composition."""
    return verifier_agent_node(state)

def quality_evaluator_node(state: AgentState) -> Dict[str, Any]:
    verification_payload = latest_verification_payload(state)
    verification = coerce_verification_dict(verification_payload)
    raw_status = verification.get("status")
    normalized_status = raw_status.strip().lower() if isinstance(raw_status, str) else ""

    status = "skipped"
    reason = "No fresh verification evidence."
    if normalized_status in {"match", "pass", "passed"}:
        status = "match"
        reason = str(verification.get("reason") or "Verification passed.")
    elif normalized_status in {"mismatch", "partial", "needs_fix", "fail", "failed"}:
        status = "mismatch"
        reason = str(verification.get("reason") or "Verification reported mismatches.")
    elif normalized_status == "catastrophic":
        status = "catastrophic"
        reason = str(verification.get("reason") or "Catastrophic scene signal detected.")

    streak = coerce_non_negative_int(state.get("verification_mismatch_streak"))
    if status in {"mismatch", "catastrophic"}:
        streak += 1
    else:
        streak = 0

    return {
        "quality_eval": {
            "status": status,
            "reason": reason,
            "source_verification_status": normalized_status or "none",
        },
        "verification_mismatch_streak": streak,
    }

def progress_evaluator_node(state: AgentState) -> Dict[str, Any]:
    mode = coerce_task_mode(state.get("task_mode"))
    unfinished_todos = unfinished_todo_count(state)
    quality = state.get("quality_eval")
    quality_status = ""
    quality_reason = ""
    if isinstance(quality, dict):
        quality_status = str(quality.get("status", "")).strip().lower()
        quality_reason = str(quality.get("reason", "")).strip()

    should_replan = False
    verifier_feedback = state.get("verifier_feedback")
    if isinstance(verifier_feedback, dict) and bool(verifier_feedback.get("should_replan")):
        should_replan = True
    mismatch_streak = coerce_non_negative_int(state.get("verification_mismatch_streak"))
    builder_stall_count = coerce_non_negative_int(state.get("builder_stall_count"))
    if mode == MODE_PLAN and (mismatch_streak >= 2 or builder_stall_count >= 2):
        should_replan = True

    if mode == MODE_CONVERSATION:
        status = "done"
        reason = "conversation_mode_response_ready"
    elif quality_status == "match" and unfinished_todos == 0:
        status = "done"
        reason = "verification_match_and_no_open_todos"
    elif quality_status == "catastrophic" and mode == MODE_PLAN and unfinished_todos == 0:
        status = "blocked"
        reason = quality_reason or "catastrophic_without_open_todo"
    elif unfinished_todos > 0:
        status = "continue"
        reason = "open_todos_remaining"
    elif quality_status in {"mismatch", "catastrophic"}:
        status = "continue"
        reason = quality_reason or f"quality_{quality_status}"
    else:
        status = "done"
        reason = "no_additional_progress_needed"

    return {
        "progress_eval": {
            "status": status,
            "reason": reason,
            "unfinished_todos": unfinished_todos,
            "should_replan": should_replan,
        }
    }

def budget_evaluator_node(state: AgentState) -> Dict[str, Any]:
    stop_reason_raw = state.get("request_stop_reason")
    if isinstance(stop_reason_raw, str) and stop_reason_raw:
        return {
            "budget_eval": {
                "budget_ok": False,
                "stop_reason": stop_reason_raw,
            }
        }

    turns = coerce_non_negative_int(state.get("request_agent_turns"))
    max_turns = coerce_non_negative_int(state.get("max_request_agent_turns"), default=-1)
    if max_turns >= 0 and turns >= max_turns:
        return {
            "budget_eval": {
                "budget_ok": False,
                "stop_reason": "agent_turn_budget_exhausted",
            },
            "request_stop_reason": "agent_turn_budget_exhausted",
        }

    tool_batches = coerce_non_negative_int(state.get("request_tool_batches"))
    max_tool_batches = coerce_non_negative_int(state.get("max_request_tool_batches"), default=-1)
    if max_tool_batches >= 0 and tool_batches >= max_tool_batches:
        return {
            "budget_eval": {
                "budget_ok": False,
                "stop_reason": "tool_batch_budget_exhausted",
            },
            "request_stop_reason": "tool_batch_budget_exhausted",
        }

    replans = coerce_non_negative_int(state.get("plan_replan_count"))
    max_replans = coerce_non_negative_int(state.get("max_plan_replans"), default=-1)
    if max_replans >= 0 and replans > max_replans:
        return {
            "budget_eval": {
                "budget_ok": False,
                "stop_reason": "plan_replan_budget_exhausted",
            },
            "request_stop_reason": "plan_replan_budget_exhausted",
        }

    return {"budget_eval": {"budget_ok": True, "stop_reason": None}}

def transition_resolver_node(state: AgentState) -> Dict[str, Any]:
    """
    Deterministic transition resolver shared by single-agent and dual-agent paths.
    """
    mode = coerce_task_mode(state.get("task_mode"))
    topology = coerce_workflow_topology(state.get("workflow_topology"))
    is_dual_plan = mode == MODE_PLAN and topology == TOPOLOGY_DUAL

    budget_eval = state.get("budget_eval")
    if isinstance(budget_eval, dict) and not bool(budget_eval.get("budget_ok", True)):
        reason = str(budget_eval.get("stop_reason") or "budget_exhausted")
        return {
            "transition_next": "checkpoint_finalize",
            "transition_reason": reason,
        }

    progress_eval = state.get("progress_eval")
    progress_status = ""
    should_replan = False
    if isinstance(progress_eval, dict):
        progress_status = str(progress_eval.get("status", "")).strip().lower()
        should_replan = bool(progress_eval.get("should_replan"))

    if progress_status == "done":
        return {
            "transition_next": "checkpoint_finalize",
            "transition_reason": "progress_done",
        }

    quality_eval = state.get("quality_eval")
    quality_status = ""
    if isinstance(quality_eval, dict):
        quality_status = str(quality_eval.get("status", "")).strip().lower()

    # Priority: budget_exhausted > done > catastrophic > replan > continue
    if quality_status == "catastrophic":
        return {
            "transition_next": "builder_agent" if is_dual_plan else "agent",
            "transition_reason": "catastrophic_manual_remediation",
        }

    if is_dual_plan and should_replan and replan_budget_remaining(state):
        return {
            "transition_next": "planner_refresh",
            "transition_reason": "replan_requested_by_evaluators",
        }

    if progress_status in {"continue", "blocked"}:
        return {
            "transition_next": "builder_agent" if is_dual_plan else "agent",
            "transition_reason": "continue_execution",
        }

    return {
        "transition_next": "checkpoint_finalize",
        "transition_reason": "default_finalize",
    }

def planner_refresh_node(state: AgentState) -> Dict[str, Any]:
    """
    Lightweight plan refresh from verifier feedback.
    """
    current_replans = coerce_non_negative_int(state.get("plan_replan_count"))
    max_replans = coerce_non_negative_int(
        state.get("max_plan_replans"),
        default=DEFAULT_MAX_PLAN_REPLANS,
    )
    next_replans = current_replans + 1

    result: Dict[str, Any] = {
        "plan_replan_count": next_replans,
        "active_role": ROLE_BUILDER,
        "builder_stall_count": 0,
    }
    if max_replans >= 0 and next_replans > max_replans:
        result["request_stop_reason"] = "plan_replan_budget_exhausted"
        return result

    feedback = state.get("verifier_feedback")
    reason = ""
    instructions: list[str] = []
    if isinstance(feedback, dict):
        reason_value = feedback.get("reason")
        if isinstance(reason_value, str):
            reason = reason_value.strip()
        raw_instructions = feedback.get("fix_instructions")
        if isinstance(raw_instructions, list):
            for item in raw_instructions:
                if isinstance(item, str):
                    text = " ".join(item.strip().split())
                    if text:
                        instructions.append(text)

    new_todos: list[TodoItem] = []
    for instruction in instructions[:2]:
        new_todos.append(create_todo(description=f"Replan fix: {instruction}", status="pending"))

    if not new_todos:
        fallback_description = reason or "Re-evaluate scene plan and continue fixing unresolved mismatches"
        new_todos.append(create_todo(description=f"Replan: {fallback_description}", status="pending"))

    result["todos"] = new_todos
    result["role_private_memory"] = merge_role_private_memory(
        state.get("role_private_memory"),
        role=ROLE_BUILDER,
        patch={
            "last_replan_reason": reason or "verifier_requested_replan",
            "replan_count": next_replans,
        },
    )
    return result
