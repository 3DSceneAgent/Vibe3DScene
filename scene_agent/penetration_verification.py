"""Helpers for internal geometry penetration verification."""

from __future__ import annotations

from typing import Any

from scene_agent.verification_result import (
    normalize_edit_suggestions,
    normalize_verification_payload,
)


def default_penetration_check_payload(
    *,
    enabled: bool,
    summary: str = "",
    degraded: bool = False,
    error: str = "",
) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "has_penetration": False,
        "pair_count": 0,
        "filtered_pair_count": 0,
        "pairs": [],
        "summary": summary,
        "degraded": degraded,
        "error": error,
    }


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _coerce_non_negative_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _coerce_non_negative_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if parsed < 0:
        return 0.0
    return parsed


def _normalize_penetration_pairs(raw_pairs: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_pairs, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in raw_pairs:
        if not isinstance(item, dict):
            continue
        object_a = str(item.get("object_a") or "").strip()
        object_b = str(item.get("object_b") or "").strip()
        if not object_a or not object_b:
            continue
        normalized.append(
            {
                "object_a": object_a,
                "object_b": object_b,
                "depth_m": round(_coerce_non_negative_float(item.get("depth_m")), 6),
                "stage": str(item.get("stage") or "narrow_phase").strip() or "narrow_phase",
                "category": str(item.get("category") or "mesh_intersection").strip() or "mesh_intersection",
            }
        )
    return normalized


def normalize_penetration_check_payload(
    raw_payload: Any,
    *,
    enabled: bool,
    fallback_error: str = "",
) -> dict[str, Any]:
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    pairs = _normalize_penetration_pairs(payload.get("pairs"))
    has_penetration = _coerce_bool(payload.get("has_penetration")) and bool(pairs)
    error = str(payload.get("error") or fallback_error or "").strip()
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        if not enabled:
            summary = "Penetration verification disabled."
        elif error:
            summary = f"Penetration verification unavailable: {error}"
        elif has_penetration:
            summary = build_penetration_reason({"pair_count": len(pairs), "pairs": pairs})
        else:
            summary = "No penetration detected."
    return {
        "enabled": enabled,
        "has_penetration": has_penetration,
        "pair_count": max(_coerce_non_negative_int(payload.get("pair_count")), len(pairs)),
        "filtered_pair_count": _coerce_non_negative_int(payload.get("filtered_pair_count")),
        "pairs": pairs,
        "summary": summary,
        "degraded": _coerce_bool(payload.get("degraded")),
        "error": error,
    }


def _format_depth_m(depth_m: float) -> str:
    centimeters = depth_m * 100.0
    if centimeters >= 1:
        return f"{centimeters:.1f}cm"
    millimeters = depth_m * 1000.0
    return f"{millimeters:.1f}mm"


def build_penetration_reason(report: dict[str, Any], *, limit: int = 2) -> str:
    pairs = report.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        pair_count = _coerce_non_negative_int(report.get("pair_count"))
        if pair_count > 0:
            return f"Detected {pair_count} penetration pair(s)."
        return "No penetration detected."

    snippets: list[str] = []
    for pair in pairs[:limit]:
        if not isinstance(pair, dict):
            continue
        object_a = str(pair.get("object_a") or "").strip()
        object_b = str(pair.get("object_b") or "").strip()
        if not object_a or not object_b:
            continue
        depth_text = _format_depth_m(_coerce_non_negative_float(pair.get("depth_m")))
        snippets.append(f"{object_a} vs {object_b} ({depth_text})")
    pair_count = max(_coerce_non_negative_int(report.get("pair_count")), len(snippets))
    if not snippets:
        return f"Detected {pair_count} penetration pair(s)."
    return f"Detected {pair_count} penetration pair(s): {', '.join(snippets)}."


def build_penetration_edit_suggestions(
    report: dict[str, Any],
    *,
    limit: int = 4,
) -> list[str]:
    pairs = report.get("pairs")
    if not isinstance(pairs, list):
        return []

    suggestions: list[str] = []
    for pair in pairs[:limit]:
        if not isinstance(pair, dict):
            continue
        object_a = str(pair.get("object_a") or "").strip()
        object_b = str(pair.get("object_b") or "").strip()
        if not object_a or not object_b:
            continue
        suggestions.append(
            f"Separate {object_a} and {object_b} to remove the mesh intersection."
        )
    return normalize_edit_suggestions(suggestions, limit=limit)


def _merge_reason_text(visual_reason: str, geometry_reason: str) -> str:
    visual = " ".join(visual_reason.strip().split())
    geometry = " ".join(geometry_reason.strip().split())
    if not visual:
        return geometry[:1000]
    if not geometry:
        return visual[:1000]
    if geometry in visual:
        return visual[:1000]
    return f"{visual} Geometry check: {geometry}"[:1000]


def merge_verification_with_penetration(
    verification_payload: Any,
    penetration_check: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized_result = normalize_verification_payload(verification_payload)
    if not penetration_check.get("enabled"):
        return normalized_result, normalized_result
    if not penetration_check.get("has_penetration"):
        return normalized_result, normalized_result

    penetration_reason = build_penetration_reason(penetration_check)
    geometry_suggestions = build_penetration_edit_suggestions(penetration_check)
    merged_suggestions = normalize_edit_suggestions(
        [
            *normalized_result.get("edit_suggestions", []),
            *geometry_suggestions,
        ]
    )
    merged_result = {
        "status": "working",
        "reason": _merge_reason_text(normalized_result.get("reason", ""), penetration_reason),
        "edit_suggestions": merged_suggestions,
    }
    return merged_result, merged_result
