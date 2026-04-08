"""Merged workflow evaluator and dual-agent verifier feedback nodes."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from langchain_core.messages import AIMessage, SystemMessage

from scene_agent.agent.convergence import (
    CONVERGENCE_GUIDANCE_MESSAGE_ID,
    evaluate_convergence,
)
from scene_agent.agent.memory_scope import merge_role_private_memory
from scene_agent.agent.state import AgentState
from scene_agent.agent.todo_state import apply_todo_actions
from scene_agent.utils.agent_messages import find_last_ai_message, message_content_to_text
from scene_agent.utils.todo_helpers import coerce_non_negative_int
from scene_agent.utils.verification_helpers import (
    replan_budget_remaining,
)
from scene_agent.vlm.metrics import invoke_structured_with_metrics
from scene_agent.verification_result import (
    VerificationResult,
    coerce_verification_result,
)

from .constants_workflow import (
    DEFAULT_MAX_PLAN_REPLANS,
    DEFAULT_REPLAN_THRESHOLD,
    DEFAULT_SKIP_THRESHOLD,
    ROLE_BUILDER,
    ROLE_VERIFIER,
    TOPOLOGY_DUAL,
)
from .constants_runtime import FAST_MODE_EVIDENCE_REQUIRED_MESSAGE_ID
from .shared import (
    ai_message_has_tool_calls,
    coerce_workflow_topology,
    effective_todo_snapshot,
    latest_human_turn_id,
    unfinished_todo_count,
)


def _safe_active_todo_id(state: AgentState) -> str | None:
    current = state.get("active_todo_id")
    if isinstance(current, str) and current.strip():
        return current
    for todo in effective_todo_snapshot(state):
        todo_id = str(todo.get("id", "")).strip()
        status = str(todo.get("status", "")).strip()
        if todo_id and status in {"pending", "in_progress"}:
            return todo_id
    return None


def _quality_status_from_verification(
    verification: VerificationResult | None,
) -> tuple[str, str]:
    if verification is None:
        return "skipped", "No fresh verification evidence."
    reason = verification.reason.strip() or "Verification did not provide detailed reasoning."
    if verification.status == "done":
        return "match", reason
    return "mismatch", reason


def _mark_todo_status(
    state: AgentState,
    *,
    todo_id: str,
    status: Literal["completed", "skipped"],
    reason: str,
) -> dict[str, Any]:
    active_todo_value = state.get("active_todo_id")
    previous_active_todo_id = (
        active_todo_value
        if isinstance(active_todo_value, str) and active_todo_value.strip()
        else None
    )
    todo_versions, todos, next_active_todo_id = apply_todo_actions(
        state.get("todo_versions"),
        [
            {
                "action": "set_status",
                "todo_id": todo_id,
                "status": status,
                "reason": reason,
            }
        ],
        fallback_todos_raw=state.get("todos"),
        source="verification",
        role="system",
        previous_active_todo_id=previous_active_todo_id,
    )
    return {
        "todo_versions": todo_versions,
        "todos": todos,
        "active_todo_id": next_active_todo_id,
    }


def _fast_mode_evidence_required_message() -> SystemMessage:
    return SystemMessage(
        id=FAST_MODE_EVIDENCE_REQUIRED_MESSAGE_ID,
        content=(
            "Fast mode requires fresh evidence before you stop after scene edits. "
            "If your last tool batch changed the scene, call get_scene_info(), "
            "observe_scene_global(), camera_observe(), render_from_camera(), "
            "render_from_objects(), or get_viewport_screenshot() before finishing."
        ),
    )


def _fast_mode_direct_resolution(state: AgentState, *, agent_target: str) -> dict[str, Any] | None:
    if state.get("fast_mode") is not True:
        return None
    if state.get("assistant_turn_kind") != "no_calls":
        return None
    if unfinished_todo_count(state) > 0:
        return None
    if coerce_non_negative_int(state.get("request_tool_batches")) <= 0:
        return None

    last_mutation_batch = coerce_non_negative_int(state.get("fast_mode_last_mutation_batch"))
    last_evidence_batch = coerce_non_negative_int(state.get("fast_mode_last_evidence_batch"))
    if last_mutation_batch == 0 or last_evidence_batch >= last_mutation_batch:
        return {
            "overall_stall_count": 0,
            "transition_next": "finalize",
            "transition_reason": "fast_mode_direct_complete",
            "evaluator_result": {
                "status": "finalize",
                "reason": (
                    "fast_mode_evidence_covers_latest_mutation"
                    if last_mutation_batch > 0
                    else "fast_mode_no_mutation"
                ),
                "transition_next": "finalize",
            },
        }

    return {
        "transition_next": agent_target,
        "transition_reason": "fast_mode_evidence_required",
        "evaluator_result": {
            "status": "continue",
            "reason": "fast_mode_missing_fresh_evidence",
            "transition_next": agent_target,
        },
        "messages": [_fast_mode_evidence_required_message()],
    }


def _extract_json_candidate(text: str) -> dict[str, Any] | None:
    normalized = text.strip()
    if not normalized:
        return None
    candidates = [normalized]
    start = normalized.find("{")
    end = normalized.rfind("}")
    if start >= 0 and end > start:
        candidates.append(normalized[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _heuristic_verifier_assessment(text: str) -> VerificationResult:
    normalized = " ".join(text.strip().split())
    lowered = normalized.lower()
    negative_phrases = (
        "not done",
        "not completed",
        "not complete",
        "not correct",
        "not satisfied",
        "incorrect",
        "wrong",
        "unfinished",
        "incomplete",
        "not finished",
        "still working",
        "still needs",
        "needs more",
        "missing",
        "not yet",
    )
    positive_phrases = (
        "looks good",
        "looks correct",
        "objective satisfied",
        "request satisfied",
        "task completed",
        "task complete",
        "completed successfully",
        "passes verification",
        "verified successfully",
    )

    status = "working"
    if not any(phrase in lowered for phrase in negative_phrases):
        if any(phrase in lowered for phrase in positive_phrases):
            status = "done"
        else:
            tokens = re.findall(r"[a-z]+(?:'[a-z]+)?", lowered)
            positive_tokens = {"done", "complete", "completed", "correct", "satisfied", "pass", "passes", "passed"}
            negation_tokens = {
                "not",
                "no",
                "never",
                "incorrect",
                "wrong",
                "unfinished",
                "incomplete",
                "without",
                "isnt",
                "isn't",
                "arent",
                "aren't",
                "wasnt",
                "wasn't",
            }
            soft_negative_tokens = {"still", "almost", "partially", "partly", "yet"}
            for index, token in enumerate(tokens):
                if token not in positive_tokens:
                    continue
                window_before = tokens[max(0, index - 3) : index]
                window_after = tokens[index + 1 : index + 3]
                if any(item in negation_tokens for item in window_before):
                    continue
                if any(item in soft_negative_tokens for item in [*window_before, *window_after]):
                    continue
                status = "done"
                break
    reason = normalized or "Verifier returned no explicit judgment."
    return VerificationResult(
        status=status,
        reason=reason[:1000],
        edit_suggestions=[],
    )


def verifier_feedback_node(
    state: AgentState,
    *,
    parser_model: Any | None = None,
) -> dict[str, Any]:
    """Dual-agent verifier dispatch + verification_result extraction."""
    latest_ai = find_last_ai_message(list(state.get("messages") or [])[-10:])
    has_calls = ai_message_has_tool_calls(latest_ai)
    base_update: dict[str, Any] = {
        "active_role": ROLE_VERIFIER,
        "request_agent_turns": coerce_non_negative_int(state.get("request_agent_turns")) + 1,
        "verifier_turn_count": coerce_non_negative_int(state.get("verifier_turn_count")) + 1,
        "assistant_turn_kind": "has_calls" if has_calls else "no_calls",
    }
    if has_calls:
        base_update["verification_result"] = None
        base_update["verifier_feedback"] = {
            "status": "observation_in_progress",
            "reason": "verifier_called_camera_tools",
        }
        return base_update

    content_text = ""
    if latest_ai is not None:
        content_text = message_content_to_text(latest_ai.content).strip()

    parsed_result: VerificationResult | None = None
    llm_call_records: list[dict[str, Any]] = []
    if parser_model is not None and content_text:
        prompt = (
            "Extract a structured verification result from verifier text.\n"
            "Map success/completion to status=done; otherwise status=working.\n"
            "Keep the reason concise and include only concrete edit suggestions."
        )
        parser_input = f"verifier_text:\n{content_text}"
        try:
            llm = parser_model
            if hasattr(parser_model, "with_config"):
                llm = parser_model.with_config(
                    tags=["nostream"],
                    run_name="verifier_feedback_internal",
                )
            parsed_raw, llm_call_record = invoke_structured_with_metrics(
                llm,
                VerificationResult,
                [
                    SystemMessage(content=prompt),
                    SystemMessage(content=parser_input),
                ],
                thread_id=str(state.get("thread_id") or "default"),
                turn_id=latest_human_turn_id(state),
                node_name="verifier_feedback",
                call_role="verifier_feedback_parser",
            )
            if isinstance(llm_call_record, dict):
                llm_call_records.append(llm_call_record)
            parsed_result = coerce_verification_result(parsed_raw)
        except Exception:
            parsed_result = None

    if parsed_result is None and content_text:
        payload = _extract_json_candidate(content_text)
        if isinstance(payload, dict):
            parsed_result = coerce_verification_result(payload)
    if parsed_result is None:
        parsed_result = _heuristic_verifier_assessment(content_text)

    role_private_memory = merge_role_private_memory(
        state.get("role_private_memory"),
        role=ROLE_VERIFIER,
        patch={
            "last_feedback_status": parsed_result.status,
            "last_feedback_reason": parsed_result.reason,
        },
    )
    base_update["role_private_memory"] = role_private_memory
    base_update["verification_result"] = parsed_result.model_dump(mode="json")
    base_update["verifier_feedback"] = {
        "status": "done" if parsed_result.status == "done" else "working",
        "reason": parsed_result.reason,
        "edit_suggestions": list(parsed_result.edit_suggestions),
    }
    if llm_call_records:
        base_update["llm_call_records"] = llm_call_records
    return base_update


def evaluator_node(state: AgentState) -> dict[str, Any]:
    """Single merged evaluator for both single-agent and dual-agent workflows."""
    verification = coerce_verification_result(state.get("verification_result"))
    has_todos = unfinished_todo_count(state) > 0
    current_todo_id = _safe_active_todo_id(state)
    has_tool_calls = coerce_non_negative_int(state.get("request_tool_batches")) > 0
    routed_to_plan = bool(state.get("routed_to_plan"))
    topology = coerce_workflow_topology(state.get("workflow_topology"))
    is_dual = topology == TOPOLOGY_DUAL
    agent_target = "builder_agent" if is_dual else "agent"

    quality_status, quality_reason = _quality_status_from_verification(verification)
    active_todo_id = current_todo_id
    last_verified_path = state.get("last_verified_path")
    if not isinstance(last_verified_path, str) or not last_verified_path:
        last_verified_path = None
    convergence = evaluate_convergence(
        state=state,
        verification=verification.model_dump(mode="json") if verification is not None else {},
        quality_status=quality_status,
        quality_reason=quality_reason,
        active_todo_id=active_todo_id,
        last_verified_path=last_verified_path,
    )

    result: dict[str, Any] = {
        "recent_verification_signatures": convergence["recent_verification_signatures"],
        "convergence_intervention_count": convergence["convergence_intervention_count"],
        "convergence_eval": convergence["convergence_eval"],
        "verification_result": None,
    }
    guidance_text = convergence.get("guidance_text")
    if isinstance(guidance_text, str) and guidance_text.strip():
        result["messages"] = [
            SystemMessage(
                id=CONVERGENCE_GUIDANCE_MESSAGE_ID,
                content=guidance_text.strip(),
            )
        ]

    # Path A: Pure Q&A (no plan, no tool calls ever made).
    if not has_todos and not has_tool_calls and not routed_to_plan:
        result["transition_next"] = "__end__"
        result["transition_reason"] = "pure_qa"
        result["evaluator_result"] = {
            "status": "pure_qa",
            "reason": "no_plan_and_no_tool_calls",
            "transition_next": "__end__",
        }
        return result

    convergence_status = str(convergence["convergence_eval"].get("status", "")).strip().lower()
    if convergence_status == "hard_stop":
        hard_stop_reason = str(
            convergence["convergence_eval"].get("reason")
            or "repeated_same_failure_signature_after_guidance"
        )
        result["transition_next"] = "finalize"
        result["transition_reason"] = hard_stop_reason
        result["evaluator_result"] = {
            "status": "hard_stop",
            "reason": hard_stop_reason,
            "transition_next": "finalize",
        }
        return result

    fast_mode_resolution = _fast_mode_direct_resolution(state, agent_target=agent_target)
    if fast_mode_resolution is not None:
        result.update(fast_mode_resolution)
        return result

    # Path B: planned workflow with pending todos.
    if has_todos and current_todo_id:
        if verification is not None and verification.status == "done":
            result.update(
                _mark_todo_status(
                    state,
                    todo_id=current_todo_id,
                    status="completed",
                    reason=verification.reason or "Marked done by evaluator after verification.",
                )
            )
            result["current_todo_stall_count"] = 0
            remaining_open = unfinished_todo_count({**state, **result}) > 0
            if remaining_open:
                result["transition_next"] = agent_target
                result["transition_reason"] = "todo_completed_continue"
                result["evaluator_result"] = {
                    "status": "continue",
                    "reason": "completed_active_todo",
                    "transition_next": agent_target,
                }
            else:
                result["transition_next"] = "finalize"
                result["transition_reason"] = "all_todos_terminal"
                result["evaluator_result"] = {
                    "status": "finalize",
                    "reason": "all_todos_terminal",
                    "transition_next": "finalize",
                }
            return result

        if verification is None:
            result["current_todo_stall_count"] = coerce_non_negative_int(
                state.get("current_todo_stall_count")
            )
            result["transition_next"] = agent_target
            result["transition_reason"] = "todo_waiting_for_fresh_verification"
            result["evaluator_result"] = {
                "status": "continue",
                "reason": "no_fresh_verification_evidence",
                "transition_next": agent_target,
            }
            return result

        next_stall = coerce_non_negative_int(state.get("current_todo_stall_count")) + 1
        result["current_todo_stall_count"] = next_stall
        if is_dual and next_stall >= DEFAULT_REPLAN_THRESHOLD and replan_budget_remaining(state):
            result["transition_next"] = "planner_refresh"
            result["transition_reason"] = "todo_stall_replan"
            result["evaluator_result"] = {
                "status": "replan",
                "reason": "todo_stall_threshold_reached",
                "transition_next": "planner_refresh",
            }
            return result

        if next_stall >= DEFAULT_SKIP_THRESHOLD:
            result.update(
                _mark_todo_status(
                    state,
                    todo_id=current_todo_id,
                    status="skipped",
                    reason="Skipped by evaluator after repeated stalls.",
                )
            )
            result["current_todo_stall_count"] = 0
            remaining_open = unfinished_todo_count({**state, **result}) > 0
            if remaining_open:
                result["transition_next"] = agent_target
                result["transition_reason"] = "todo_skipped_continue"
                result["evaluator_result"] = {
                    "status": "continue",
                    "reason": "skipped_stalled_todo",
                    "transition_next": agent_target,
                }
            else:
                result["transition_next"] = "finalize"
                result["transition_reason"] = "all_todos_terminal_after_skip"
                result["evaluator_result"] = {
                    "status": "finalize",
                    "reason": "all_todos_terminal_after_skip",
                    "transition_next": "finalize",
                }
            return result

        result["transition_next"] = agent_target
        result["transition_reason"] = "todo_still_working"
        result["evaluator_result"] = {
            "status": "continue",
            "reason": "active_todo_still_working",
            "transition_next": agent_target,
        }
        return result

    # Path C: direct-mode task (no todos).
    if verification is not None and verification.status == "done":
        result["overall_stall_count"] = 0
        result["transition_next"] = "finalize"
        result["transition_reason"] = "direct_done"
        result["evaluator_result"] = {
            "status": "finalize",
            "reason": "verification_done_direct_mode",
            "transition_next": "finalize",
        }
        return result

    if verification is None:
        result["overall_stall_count"] = coerce_non_negative_int(state.get("overall_stall_count"))
        result["transition_next"] = agent_target
        result["transition_reason"] = "direct_waiting_for_fresh_verification"
        result["evaluator_result"] = {
            "status": "continue",
            "reason": "no_fresh_verification_evidence",
            "transition_next": agent_target,
        }
        return result

    overall_stall = coerce_non_negative_int(state.get("overall_stall_count")) + 1
    result["overall_stall_count"] = overall_stall
    if overall_stall >= DEFAULT_SKIP_THRESHOLD:
        result["transition_next"] = "finalize"
        result["transition_reason"] = "direct_stall_finalize"
        result["evaluator_result"] = {
            "status": "finalize",
            "reason": "direct_mode_stall_threshold_reached",
            "transition_next": "finalize",
        }
        return result

    result["transition_next"] = agent_target
    result["transition_reason"] = "direct_continue"
    result["evaluator_result"] = {
        "status": "continue",
        "reason": "direct_mode_still_working",
        "transition_next": agent_target,
    }
    return result


def planner_refresh_node(state: AgentState) -> dict[str, Any]:
    """Refresh todo plan from latest verification_result guidance."""
    current_replans = coerce_non_negative_int(state.get("plan_replan_count"))
    max_replans = coerce_non_negative_int(
        state.get("max_plan_replans"),
        default=DEFAULT_MAX_PLAN_REPLANS,
    )
    next_replans = current_replans + 1
    if max_replans >= 0 and next_replans > max_replans:
        return {
            "plan_replan_count": current_replans,
            "transition_next": "builder_agent",
            "transition_reason": "planner_refresh_budget_exhausted",
        }

    verification = coerce_verification_result(state.get("verification_result"))
    fallback_feedback = state.get("verifier_feedback")
    reason = ""
    instructions: list[str] = []

    if verification is not None:
        reason = verification.reason.strip()
        instructions.extend(
            [
                " ".join(str(item).strip().split())
                for item in verification.edit_suggestions
                if isinstance(item, str) and item.strip()
            ]
        )

    if isinstance(fallback_feedback, dict):
        if not reason:
            reason_value = fallback_feedback.get("reason")
            if isinstance(reason_value, str):
                reason = reason_value.strip()
        raw_instructions = fallback_feedback.get("edit_suggestions") or fallback_feedback.get("fix_instructions")
        if isinstance(raw_instructions, list):
            for item in raw_instructions:
                if isinstance(item, str) and item.strip():
                    instructions.append(" ".join(item.strip().split()))

    deduped_instructions: list[str] = []
    seen: set[str] = set()
    for instruction in instructions:
        compact = " ".join(instruction.strip().split())
        if not compact or compact in seen:
            continue
        seen.add(compact)
        deduped_instructions.append(compact)
        if len(deduped_instructions) >= 3:
            break
    if not deduped_instructions:
        fallback_description = reason or "Re-evaluate unresolved scene mismatches and continue fixing."
        deduped_instructions = [fallback_description]

    open_todos = [
        todo
        for todo in effective_todo_snapshot(state)
        if str(todo.get("status", "")).strip() in {"pending", "in_progress"}
    ]
    actions: list[dict[str, Any]] = []
    normalized_reason = reason or "verifier_requested_replan"
    for todo in open_todos:
        todo_id = str(todo.get("id", "")).strip()
        if not todo_id:
            continue
        actions.append(
            {
                "action": "supersede",
                "todo_id": todo_id,
                "reason": f"Superseded by planner_refresh #{next_replans}: {normalized_reason}",
            }
        )
    for index, instruction in enumerate(deduped_instructions):
        actions.append(
            {
                "action": "create",
                "title": f"Replan fix: {instruction}",
                "status": "pending",
                "reason": f"Generated by planner_refresh #{next_replans}",
                "set_active": index == 0,
            }
        )

    active_todo_value = state.get("active_todo_id")
    previous_active_todo_id = (
        active_todo_value
        if isinstance(active_todo_value, str) and active_todo_value.strip()
        else None
    )
    todo_versions, todos, next_active_todo_id = apply_todo_actions(
        state.get("todo_versions"),
        actions,
        fallback_todos_raw=state.get("todos"),
        source="system",
        role=ROLE_BUILDER,
        previous_active_todo_id=previous_active_todo_id,
    )
    return {
        "plan_replan_count": next_replans,
        "active_role": ROLE_BUILDER,
        "builder_stall_count": 0,
        "current_todo_stall_count": 0,
        "todo_versions": todo_versions,
        "todos": todos,
        "active_todo_id": next_active_todo_id,
        "role_private_memory": merge_role_private_memory(
            state.get("role_private_memory"),
            role=ROLE_BUILDER,
            patch={
                "last_replan_reason": normalized_reason,
                "replan_count": next_replans,
                "last_replan_superseded": len(open_todos),
                "last_replan_new_tasks": len(deduped_instructions),
            },
        ),
    }
