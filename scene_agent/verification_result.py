"""Canonical verification result contract and normalization helpers."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

VerificationStatus = Literal["working", "done"]

_DONE_STATUS_MARKERS = frozenset(
    {
        "done",
        "match",
        "pass",
        "passes",
        "passed",
        "complete",
        "completed",
        "success",
        "successful",
        "satisfied",
        "correct",
    }
)
_NORMALIZED_DONE_STATUS_MARKERS = frozenset(
    marker.replace("-", "").replace("_", "").replace(" ", "")
    for marker in _DONE_STATUS_MARKERS
)


class VerificationResult(BaseModel):
    status: VerificationStatus = "working"
    reason: str = ""
    edit_suggestions: list[str] = Field(default_factory=list)


def normalize_verification_status(raw_status: Any) -> VerificationStatus:
    if not isinstance(raw_status, str):
        return "working"
    normalized = raw_status.strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    if normalized in _NORMALIZED_DONE_STATUS_MARKERS:
        return "done"
    return "working"


def normalize_edit_suggestions(raw_value: Any, *, limit: int = 8) -> list[str]:
    if not isinstance(raw_value, list):
        return []
    suggestions: list[str] = []
    for item in raw_value:
        if not isinstance(item, str):
            continue
        text = " ".join(item.strip().split())
        if text:
            suggestions.append(text)
        if len(suggestions) >= limit:
            break
    return suggestions


def normalize_verification_payload(
    raw_payload: Any,
    *,
    fallback_reason: str = "",
) -> dict[str, Any]:
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        reason = " ".join(fallback_reason.strip().split())
    return {
        "status": normalize_verification_status(payload.get("status")),
        "reason": reason[:1000],
        "edit_suggestions": normalize_edit_suggestions(payload.get("edit_suggestions")),
    }


def coerce_verification_result(
    raw_value: Any,
    *,
    fallback_reason: str = "",
) -> VerificationResult | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, VerificationResult):
        return raw_value
    if isinstance(raw_value, dict):
        return VerificationResult.model_validate(
            normalize_verification_payload(raw_value, fallback_reason=fallback_reason)
        )
    try:
        return VerificationResult.model_validate(raw_value)
    except Exception:
        return None
