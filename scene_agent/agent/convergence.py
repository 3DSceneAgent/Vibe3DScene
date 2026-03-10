"""Convergence and oscillation detection for long-running plan execution."""

from __future__ import annotations

from typing import Any


CONVERGENCE_GUIDANCE_MESSAGE_ID = "convergence_guidance_current"
_MAX_GUIDED_RETRIES_BEFORE_HARD_STOP = 2


def _coerce_signature_history(raw_value: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_value, list):
        return []
    history: list[dict[str, Any]] = []
    for item in raw_value:
        if not isinstance(item, dict):
            continue
        history.append(dict(item))
    return history


def _clean_text(raw_value: Any) -> str:
    if not isinstance(raw_value, str):
        return ""
    return " ".join(raw_value.strip().lower().split())


def infer_failure_bucket(verification: dict[str, Any], quality_status: str) -> str:
    for key, bucket in (
        ("scale_feedback", "scale"),
        ("placement_feedback", "placement"),
        ("layout_feedback", "layout"),
        ("material_feedback", "material"),
    ):
        if _clean_text(verification.get(key)):
            return bucket

    object_feedback = _clean_text(verification.get("object_feedback"))
    reason = _clean_text(verification.get("reason"))
    if any(marker in object_feedback or marker in reason for marker in ("missing", "absent", "not present")):
        return "missing_object"
    if object_feedback:
        return "layout"
    if reason:
        if "scale" in reason:
            return "scale"
        if "material" in reason or "texture" in reason:
            return "material"
        if "place" in reason or "position" in reason:
            return "placement"
        if "layout" in reason or "arrangement" in reason:
            return "layout"
    return "unknown"


def _signature_key(signature: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(signature.get("todo_id") or ""),
        str(signature.get("failure_bucket") or ""),
        str(signature.get("status") or ""),
    )


def _is_alternating_bucket_sequence(buckets: list[str]) -> bool:
    if len(buckets) < 4:
        return False
    if any(not bucket or bucket == "unknown" for bucket in buckets):
        return False
    unique_buckets = set(buckets)
    if len(unique_buckets) != 2:
        return False
    if any(buckets[idx] == buckets[idx - 1] for idx in range(1, len(buckets))):
        return False
    return (
        len({buckets[idx] for idx in range(0, len(buckets), 2)}) == 1
        and len({buckets[idx] for idx in range(1, len(buckets), 2)}) == 1
    )


def _has_oscillation_pattern(history: list[dict[str, Any]]) -> bool:
    if len(history) < 4:
        return False

    recent_window = history[-6:]
    signatures_by_todo: dict[str, list[dict[str, Any]]] = {}
    for item in recent_window:
        todo_id = str(item.get("todo_id") or "")
        if not todo_id:
            continue
        signatures_by_todo.setdefault(todo_id, []).append(item)

    for todo_history in signatures_by_todo.values():
        if len(todo_history) < 4:
            continue
        max_window = min(6, len(todo_history))
        for window_size in range(max_window, 3, -1):
            candidate = todo_history[-window_size:]
            buckets = [str(entry.get("failure_bucket") or "") for entry in candidate]
            if _is_alternating_bucket_sequence(buckets):
                return True
    return False


def _pattern_from_history(history: list[dict[str, Any]]) -> tuple[str, str]:
    if len(history) >= 3:
        last_three = history[-3:]
        keys = [_signature_key(item) for item in last_three]
        if keys[0] == keys[1] == keys[2] and keys[0][0]:
            return "repeat_loop", "repeated_same_failure_signature"

    if _has_oscillation_pattern(history):
        return "oscillation_loop", "alternating_failure_buckets"

    return "none", "no_convergence_pattern"


def build_convergence_guidance(pattern: str, signature: dict[str, Any]) -> str:
    todo_id = str(signature.get("todo_id") or "current_todo")
    bucket = str(signature.get("failure_bucket") or "unknown issue")
    if pattern == "repeat_loop":
        return (
            f"Convergence guard: repeated {bucket} mismatch on {todo_id}. "
            "Do exactly one targeted fix on this single issue, then render once for fresh evidence. "
            "Do not rework unrelated objects in the same turn."
        )
    if pattern == "oscillation_loop":
        return (
            f"Convergence guard: {todo_id} is oscillating between failure modes. "
            "Freeze the current successful dimensions, change only one variable, and re-render before any other edit."
        )
    return (
        f"Convergence guard: unstable verification detected on {todo_id}. "
        "Perform one bounded corrective action, then verify again."
    )


def evaluate_convergence(
    *,
    state: dict[str, Any],
    verification: dict[str, Any],
    quality_status: str,
    quality_reason: str,
    active_todo_id: str | None,
    last_verified_path: str | None,
) -> dict[str, Any]:
    previous_history = _coerce_signature_history(state.get("recent_verification_signatures"))
    prior_interventions = 0
    raw_interventions = state.get("convergence_intervention_count")
    if isinstance(raw_interventions, int) and raw_interventions >= 0:
        prior_interventions = raw_interventions

    if quality_status != "mismatch":
        should_reset = quality_status == "match"
        return {
            "recent_verification_signatures": [] if should_reset else previous_history,
            "convergence_intervention_count": 0 if should_reset else prior_interventions,
            "convergence_eval": {
                "status": "stable",
                "pattern": "none",
                "reason": (
                    "verification_match_resets_failure_history"
                    if should_reset
                    else "verification_not_in_failure_state"
                ),
            },
            "guidance_text": None,
        }

    signature = {
        "todo_id": active_todo_id or "",
        "status": quality_status,
        "failure_bucket": infer_failure_bucket(verification, quality_status),
        "verified_path": last_verified_path,
        "reason": quality_reason,
    }
    history = (previous_history + [signature])[-6:]
    pattern, pattern_reason = _pattern_from_history(history)

    if pattern in {"repeat_loop", "oscillation_loop"}:
        if prior_interventions >= _MAX_GUIDED_RETRIES_BEFORE_HARD_STOP:
            return {
                "recent_verification_signatures": history,
                "convergence_intervention_count": prior_interventions,
                "convergence_eval": {
                    "status": "hard_stop",
                    "pattern": pattern,
                    "reason": f"{pattern_reason}_after_guidance",
                    "todo_id": signature["todo_id"],
                    "failure_bucket": signature["failure_bucket"],
                },
                "guidance_text": None,
            }
        return {
            "recent_verification_signatures": history,
            "convergence_intervention_count": prior_interventions + 1,
            "convergence_eval": {
                "status": "guided_retry",
                "pattern": pattern,
                "reason": pattern_reason,
                "todo_id": signature["todo_id"],
                "failure_bucket": signature["failure_bucket"],
            },
            "guidance_text": build_convergence_guidance(pattern, signature),
        }

    return {
        "recent_verification_signatures": history,
        "convergence_intervention_count": 0,
        "convergence_eval": {
            "status": "stable",
            "pattern": "none",
            "reason": "failure_observed_without_repeat_pattern",
            "todo_id": signature["todo_id"],
            "failure_bucket": signature["failure_bucket"],
        },
        "guidance_text": None,
    }
