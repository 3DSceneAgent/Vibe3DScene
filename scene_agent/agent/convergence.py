"""Convergence and oscillation detection for long-running plan execution."""

from __future__ import annotations

from typing import Any


CONVERGENCE_GUIDANCE_MESSAGE_ID = "convergence_guidance_current"


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
    if quality_status == "catastrophic":
        return "catastrophic"

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


def _pattern_from_history(history: list[dict[str, Any]]) -> tuple[str, str]:
    if len(history) >= 2:
        last_two = history[-2:]
        if all(str(item.get("status")) == "catastrophic" for item in last_two):
            return "hard_stop", "consecutive_catastrophic_verification"

    if len(history) >= 3:
        last_three = history[-3:]
        keys = [_signature_key(item) for item in last_three]
        if keys[0] == keys[1] == keys[2] and keys[0][0]:
            return "repeat_loop", "repeated_same_failure_signature"

    if len(history) >= 4:
        a, b, c, d = history[-4:]
        if (
            str(a.get("todo_id") or "")
            and str(a.get("todo_id")) == str(b.get("todo_id")) == str(c.get("todo_id")) == str(d.get("todo_id"))
            and str(a.get("failure_bucket") or "")
            in {"scale", "placement", "layout"}
            and str(a.get("failure_bucket")) == str(c.get("failure_bucket"))
            and str(b.get("failure_bucket")) == str(d.get("failure_bucket"))
            and str(a.get("failure_bucket")) != str(b.get("failure_bucket"))
        ):
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

    if quality_status not in {"mismatch", "catastrophic"}:
        return {
            "recent_verification_signatures": [],
            "convergence_intervention_count": 0,
            "convergence_eval": {
                "status": "stable",
                "pattern": "none",
                "reason": "verification_not_in_failure_state",
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

    if pattern == "hard_stop":
        return {
            "recent_verification_signatures": history,
            "convergence_intervention_count": prior_interventions,
            "convergence_eval": {
                "status": "hard_stop",
                "pattern": pattern,
                "reason": pattern_reason,
                "todo_id": signature["todo_id"],
                "failure_bucket": signature["failure_bucket"],
            },
            "guidance_text": None,
        }

    if pattern in {"repeat_loop", "oscillation_loop"}:
        if prior_interventions >= 1:
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
