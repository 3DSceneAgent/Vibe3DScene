from __future__ import annotations

import ast
import json
import os
import re
from typing import Any
from urllib.parse import unquote, urlparse

from langchain_core.messages import ToolMessage
from scene_agent.agent.nodes.constants_workflow import DEFAULT_MAX_PLAN_REPLANS
from scene_agent.agent.state import AgentState, TodoItem
from scene_agent.agent.todo_state import project_latest_todos
from scene_agent.utils.todo_helpers import (
    coerce_non_negative_int,
    normalize_todo_description as _normalize_todo_description,
)

_CATASTROPHIC_SCENE_DIMENSION_THRESHOLD = 5000.0
_CATASTROPHIC_OBJECT_COORD_THRESHOLD = 5000.0
_CATASTROPHIC_OBJECT_DIMENSION_THRESHOLD = 2000.0
_CATASTROPHIC_RENDER_STDDEV_THRESHOLD = 2.0
_CATASTROPHIC_RENDER_GRAY_DRIFT_THRESHOLD = 3.0
_CATASTROPHIC_RENDER_BLACK_MEAN_THRESHOLD = 4.0
_CATASTROPHIC_RENDER_WHITE_MEAN_THRESHOLD = 251.0


def coerce_verification_dict(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        parsed = _coerce_verification_payload_from_text(payload)
        if isinstance(parsed, dict):
            return parsed
    return {}


def extract_verifier_fix_instructions(verification: dict[str, Any]) -> list[str]:
    instructions: list[str] = []
    seen: set[str] = set()

    raw_suggestions = verification.get("edit_suggestions")
    if isinstance(raw_suggestions, list):
        for raw_item in raw_suggestions:
            if not isinstance(raw_item, str):
                continue
            text = " ".join(raw_item.strip().split())
            if not text or text in seen:
                continue
            seen.add(text)
            instructions.append(text)

    for key in (
        "reason",
        "guidance",
        "object_feedback",
        "layout_feedback",
        "placement_feedback",
        "material_feedback",
        "scale_feedback",
        "environment_feedback",
    ):
        raw_value = verification.get(key)
        if not isinstance(raw_value, str):
            continue
        text = " ".join(raw_value.strip().split())
        if not text or text in seen:
            continue
        seen.add(text)
        instructions.append(text)

    return instructions[:8]


def replan_budget_remaining(state: AgentState) -> bool:
    current_replans = coerce_non_negative_int(state.get("plan_replan_count"))
    max_replans = coerce_non_negative_int(
        state.get("max_plan_replans"),
        default=DEFAULT_MAX_PLAN_REPLANS,
    )
    if max_replans < 0:
        return True
    return current_replans < max_replans


def _sanitize_verification_payload(payload: Any) -> Any:
    if isinstance(payload, str):
        text = payload.strip()
        parsed = _coerce_verification_payload_from_text(text)
        if isinstance(parsed, dict):
            return _sanitize_verification_payload(parsed)
        return text[:600] if len(text) > 600 else text
    if not isinstance(payload, dict):
        return payload
    allowed_keys = (
        "status",
        "reason",
        "object_feedback",
        "layout_feedback",
        "placement_feedback",
        "material_feedback",
        "scale_feedback",
        "environment_feedback",
        "edit_suggestions",
        "render_source",
        "verification_mode",
    )
    sanitized: dict[str, Any] = {}
    for key in allowed_keys:
        if key not in payload:
            continue
        value = payload.get(key)
        if key == "edit_suggestions" and isinstance(value, list):
            sanitized[key] = [str(item) for item in value[:4]]
            continue
        if isinstance(value, str):
            sanitized[key] = value[:600] if len(value) > 600 else value
            continue
        sanitized[key] = value
    return sanitized


def latest_verification_payload(state: AgentState) -> dict[str, Any] | str | None:
    messages = state.get("messages")
    if not isinstance(messages, list):
        return None
    for msg in reversed(messages):
        if not isinstance(msg, ToolMessage):
            continue
        name = getattr(msg, "name", None)
        if not isinstance(name, str) or "verification" not in name:
            continue
        content = msg.content
        if isinstance(content, dict):
            return content
        if isinstance(content, str) and content.strip():
            return content.strip()
        return None
    return None


def _latest_verification_feedback(state: AgentState) -> tuple[str | None, str | None]:
    payload = latest_verification_payload(state)
    if isinstance(payload, dict):
        status = payload.get("status")
        reason = payload.get("reason")
        status_value = status if isinstance(status, str) and status else None
        reason_value = reason if isinstance(reason, str) and reason else None
        return status_value, reason_value
    if isinstance(payload, str):
        parsed = _coerce_verification_payload_from_text(payload)
        if isinstance(parsed, dict):
            status = parsed.get("status")
            reason = parsed.get("reason")
            status_value = status if isinstance(status, str) and status else None
            if isinstance(reason, str) and reason:
                return status_value, reason

            # Fallback to a concise synthesized reason from feedback fields.
            for key in (
                "object_feedback",
                "layout_feedback",
                "placement_feedback",
                "material_feedback",
                "scale_feedback",
                "environment_feedback",
            ):
                value = parsed.get(key)
                if isinstance(value, str) and value.strip():
                    return status_value, value.strip()
            return status_value, None
        normalized = " ".join(payload.strip().split())
        if not normalized:
            return None, None
        if len(normalized) > 300:
            normalized = f"{normalized[:300]}..."
        return None, normalized
    return None, None


def _coerce_verification_payload_from_text(text: str) -> dict[str, Any] | None:
    if not isinstance(text, str):
        return None
    normalized = text.strip()
    if not normalized:
        return None

    candidates: list[str] = [normalized]
    first_brace = normalized.find("{")
    last_brace = normalized.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        snippet = normalized[first_brace : last_brace + 1].strip()
        if snippet and snippet not in candidates:
            candidates.append(snippet)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        try:
            parsed_literal = ast.literal_eval(candidate)
            if isinstance(parsed_literal, dict):
                return parsed_literal
        except Exception:
            pass
    return None


def _effective_todo_snapshot(state: AgentState) -> list[TodoItem]:
    return project_latest_todos(
        state.get("todo_versions"),
        fallback_todos_raw=state.get("todos"),
    )


def active_todo_context(state: AgentState) -> list[dict[str, str]]:
    todos = _effective_todo_snapshot(state)
    if not todos:
        return []
    in_progress: list[dict[str, str]] = []
    pending: list[dict[str, str]] = []
    active_todo_id = state.get("active_todo_id")
    for todo in todos:
        description = str(todo.get("description", "")).strip()
        status = str(todo.get("status", "")).strip()
        todo_id = str(todo.get("id", "")).strip()
        if not description or not todo_id:
            continue
        payload = {
            "todo_id": todo_id,
            "title": description,
            "status": status,
        }
        if todo_id == active_todo_id and status in {"pending", "in_progress"}:
            in_progress.insert(0, payload)
            continue
        if status == "in_progress":
            in_progress.append(payload)
        elif status == "pending":
            pending.append(payload)
    return (in_progress + pending)[:5]


def _normalize_verification_todo_status(raw_status: Any) -> str | None:
    if not isinstance(raw_status, str):
        return None
    normalized = raw_status.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"done", "completed", "complete"}:
        return "completed"
    if normalized in {"not_done", "pending", "in_progress", "uncertain", "unknown"}:
        return "not_completed"
    return None


def _tokenize_todo_text(text: str) -> set[str]:
    if not isinstance(text, str):
        return set()
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) >= 3
    }


def _match_todo_by_objective(
    latest_todos: dict[str, TodoItem],
    objective: str,
) -> TodoItem | None:
    objective_key = _normalize_todo_description(objective)
    if not objective_key:
        return None

    exact = latest_todos.get(objective_key)
    if exact:
        return exact

    if len(objective_key) >= 8:
        for key, todo in latest_todos.items():
            if objective_key in key or key in objective_key:
                return todo

    objective_tokens = _tokenize_todo_text(objective_key)
    if not objective_tokens:
        return None

    best_todo: TodoItem | None = None
    best_score = 0.0
    for key, todo in latest_todos.items():
        todo_tokens = _tokenize_todo_text(key)
        if not todo_tokens:
            continue
        overlap = objective_tokens & todo_tokens
        if not overlap:
            continue
        score = len(overlap) / max(len(objective_tokens), len(todo_tokens))
        if score > best_score:
            best_score = score
            best_todo = todo

    if best_score >= 0.5:
        return best_todo
    return None


def _extract_verification_todo_assessments(
    verification: dict[str, Any],
) -> list[dict[str, str]]:
    raw_assessment = verification.get("todo_assessment")
    if not isinstance(raw_assessment, list):
        return []

    assessments: list[dict[str, str]] = []
    for item in raw_assessment:
        if not isinstance(item, dict):
            continue
        todo_id = item.get("todo_id")
        status = item.get("status")
        reason = item.get("reason")
        if not isinstance(todo_id, str) or not todo_id.strip():
            continue
        normalized_status = _normalize_verification_todo_status(status)
        if normalized_status is None:
            continue
        assessments.append(
            {
                "todo_id": todo_id.strip(),
                "status": normalized_status,
                "reason": reason.strip() if isinstance(reason, str) else "",
            }
        )
    return assessments


def build_todo_updates_from_verification(
    state: AgentState,
    verification: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    todos = _effective_todo_snapshot(state)
    if not todos:
        return [], []

    latest_todos = {
        str(todo.get("id", "")).strip(): todo
        for todo in todos
        if isinstance(todo.get("id"), str) and str(todo.get("id"))
    }
    if not latest_todos:
        return [], []

    assessments = _extract_verification_todo_assessments(verification)
    if not assessments:
        return [], []

    todo_actions: list[dict[str, Any]] = []
    update_records: list[dict[str, str]] = []

    for assessment in assessments:
        if assessment["status"] != "completed":
            continue
        matched = latest_todos.get(assessment["todo_id"])
        if not matched:
            continue
        if matched.get("status") == "completed":
            continue
        todo_actions.append(
            {
                "action": "set_status",
                "todo_id": assessment["todo_id"],
                "status": "completed",
                "reason": assessment.get("reason", ""),
            }
        )
        update_records.append(
            {
                "todo_id": assessment["todo_id"],
                "matched_todo": str(matched.get("description", "")),
                "status": "completed",
                "reason": assessment.get("reason", ""),
            }
        )

    return todo_actions, update_records


def build_verification_scene_context(state: AgentState) -> dict[str, Any] | None:
    context: dict[str, Any] = {}

    scene_objects = state.get("scene_objects")
    if isinstance(scene_objects, dict) and scene_objects:
        object_names = sorted(name for name in scene_objects.keys() if isinstance(name, str))
        selected_names = object_names[:60]
        compact_objects: dict[str, Any] = {}
        for name in selected_names:
            raw_object = scene_objects.get(name)
            if isinstance(raw_object, dict):
                compact: dict[str, Any] = {}
                for key in ("type", "location", "dimensions", "bounding_box", "visible", "material_count"):
                    if key in raw_object:
                        compact[key] = raw_object.get(key)
                compact_objects[name] = compact or raw_object
            else:
                compact_objects[name] = raw_object
        context["scene_objects"] = compact_objects
        context["scene_object_count"] = len(object_names)
        if len(object_names) > len(selected_names):
            context["scene_objects_truncated"] = True

    scene_bbox = state.get("scene_bbox")
    if isinstance(scene_bbox, dict) and scene_bbox:
        context["scene_bbox"] = scene_bbox

    scene_camera_params = state.get("scene_camera_params")
    if isinstance(scene_camera_params, dict) and scene_camera_params:
        context["scene_camera_params"] = scene_camera_params

    persistent_cameras = state.get("persistent_cameras")
    if isinstance(persistent_cameras, list) and persistent_cameras:
        cameras = [name for name in persistent_cameras if isinstance(name, str)]
        if cameras:
            context["persistent_cameras"] = cameras[:12]

    active_todos = active_todo_context(state)
    if active_todos:
        context["active_todos"] = active_todos

    return context or None


def _coerce_numeric_triplet(raw_value: Any) -> list[float]:
    if not isinstance(raw_value, (list, tuple)):
        return []
    values: list[float] = []
    for item in raw_value[:3]:
        if isinstance(item, (int, float)):
            values.append(float(item))
    return values


def _bbox_dimensions_from_bounds(raw_bbox: Any) -> list[float]:
    if (
        not isinstance(raw_bbox, (list, tuple))
        or len(raw_bbox) != 2
        or not isinstance(raw_bbox[0], (list, tuple))
        or not isinstance(raw_bbox[1], (list, tuple))
    ):
        return []
    min_corner = _coerce_numeric_triplet(raw_bbox[0])
    max_corner = _coerce_numeric_triplet(raw_bbox[1])
    if len(min_corner) != 3 or len(max_corner) != 3:
        return []
    return [
        abs(max_corner[0] - min_corner[0]),
        abs(max_corner[1] - min_corner[1]),
        abs(max_corner[2] - min_corner[2]),
    ]


def _resolve_render_path_for_analysis(render_reference: Any) -> str | None:
    if not isinstance(render_reference, str):
        return None
    normalized = render_reference.strip()
    if not normalized or normalized.startswith("data:"):
        return None
    if normalized.startswith("file://"):
        normalized = normalized.replace("file://", "", 1)

    parsed = urlparse(normalized)
    candidate_path = parsed.path if parsed.scheme and parsed.netloc else normalized
    if candidate_path.startswith("/renders/"):
        filename = unquote(candidate_path.replace("/renders/", "", 1).strip("/"))
        if not filename:
            return None
        try:
            from scene_agent.utils.rendering import RENDERS_DIR

            local_path = os.path.join(str(RENDERS_DIR), filename)
            if os.path.exists(local_path):
                return local_path
        except Exception:
            return None
    if os.path.exists(candidate_path):
        return candidate_path
    return None


def _analyze_render_flatness(render_reference: Any) -> dict[str, Any] | None:
    local_path = _resolve_render_path_for_analysis(render_reference)
    if not local_path:
        return None

    try:
        from PIL import Image, ImageStat

        with Image.open(local_path) as image:
            rgb = image.convert("RGB")
            stat = ImageStat.Stat(rgb)
    except Exception:
        return None

    means = [float(value) for value in stat.mean[:3]]
    stddevs = [float(value) for value in stat.stddev[:3]]
    if not means or not stddevs:
        return None

    mean_intensity = sum(means) / len(means)
    stddev_intensity = sum(stddevs) / len(stddevs)
    max_channel_drift = max(abs(channel - mean_intensity) for channel in means)
    is_flat = stddev_intensity <= _CATASTROPHIC_RENDER_STDDEV_THRESHOLD
    is_grayish = max_channel_drift <= _CATASTROPHIC_RENDER_GRAY_DRIFT_THRESHOLD
    is_black_or_white = (
        mean_intensity <= _CATASTROPHIC_RENDER_BLACK_MEAN_THRESHOLD
        or mean_intensity >= _CATASTROPHIC_RENDER_WHITE_MEAN_THRESHOLD
    )
    return {
        "path": local_path,
        "mean_intensity": round(mean_intensity, 3),
        "stddev_intensity": round(stddev_intensity, 3),
        "max_channel_drift": round(max_channel_drift, 3),
        "is_flat": bool(is_flat),
        "is_grayish": bool(is_grayish),
        "is_black_or_white": bool(is_black_or_white),
    }


def detect_catastrophic_scene_state(
    state: AgentState,
    *,
    render_reference: Any,
) -> dict[str, Any]:
    signals: list[str] = []
    metrics: dict[str, Any] = {}

    scene_bbox = state.get("scene_bbox")
    if isinstance(scene_bbox, dict):
        dims = _coerce_numeric_triplet(scene_bbox.get("dimensions"))
        if len(dims) == 3:
            max_dim = max(abs(value) for value in dims)
            metrics["scene_bbox_dimensions"] = [round(value, 4) for value in dims]
            metrics["scene_bbox_max_dimension"] = round(max_dim, 4)
            if max_dim > _CATASTROPHIC_SCENE_DIMENSION_THRESHOLD:
                signals.append("scene_bbox_dimension_exploded")
            positive_dims = [abs(value) for value in dims if abs(value) > 1e-6]
            if positive_dims:
                span_ratio = max(positive_dims) / min(positive_dims)
                metrics["scene_bbox_span_ratio"] = round(span_ratio, 4)
                if max_dim > 100.0 and span_ratio > 10000.0:
                    signals.append("scene_bbox_span_ratio_extreme")

    scene_objects = state.get("scene_objects")
    if isinstance(scene_objects, dict) and scene_objects:
        far_objects: list[str] = []
        huge_objects: list[str] = []
        for name, payload in list(scene_objects.items())[:300]:
            if not isinstance(name, str):
                continue
            if not isinstance(payload, dict):
                continue

            location = _coerce_numeric_triplet(payload.get("location"))
            if location and max(abs(value) for value in location) > _CATASTROPHIC_OBJECT_COORD_THRESHOLD:
                far_objects.append(name)

            dimensions = _coerce_numeric_triplet(payload.get("dimensions"))
            if not dimensions:
                dimensions = _bbox_dimensions_from_bounds(payload.get("bounding_box"))
            if not dimensions and isinstance(payload.get("bbox"), dict):
                dimensions = _coerce_numeric_triplet(payload["bbox"].get("dimensions"))
            if dimensions and max(abs(value) for value in dimensions) > _CATASTROPHIC_OBJECT_DIMENSION_THRESHOLD:
                huge_objects.append(name)

            if len(far_objects) >= 5 and len(huge_objects) >= 5:
                break

        if far_objects:
            signals.append("object_location_outlier")
            metrics["object_location_outliers"] = far_objects[:5]
        if huge_objects:
            signals.append("object_dimension_outlier")
            metrics["object_dimension_outliers"] = huge_objects[:5]

    render_stats = _analyze_render_flatness(render_reference)
    if isinstance(render_stats, dict):
        metrics["render_flatness"] = render_stats
        if render_stats.get("is_flat") and (
            render_stats.get("is_grayish") or render_stats.get("is_black_or_white")
        ):
            signals.append("render_flat_gray_or_blank")

    return {
        "is_catastrophic": bool(signals),
        "signals": sorted(set(signals)),
        "metrics": metrics,
    }


def _resolve_enabled_tool_set(state: AgentState) -> set[str]:
    enabled_tool_names = state.get("enabled_tool_names")
    if not isinstance(enabled_tool_names, list):
        return set()
    return {
        name.strip()
        for name in enabled_tool_names
        if isinstance(name, str) and name.strip()
    }


def build_verification_guidance_message(
    state: AgentState,
    verification: dict[str, Any],
) -> str:
    status_value = verification.get("status")
    status = status_value.strip().lower() if isinstance(status_value, str) else ""
    if status == "catastrophic":
        hard_recovery = verification.get("hard_recovery")
        if isinstance(hard_recovery, dict):
            action = hard_recovery.get("action")
            attempt = hard_recovery.get("attempt")
            forced = bool(hard_recovery.get("forced"))
            if action == "undo_last_snapshot" and forced:
                return (
                    f"Catastrophic state detected (attempt {attempt}). "
                    "Hard recovery is forcing undo_last_snapshot, then scene re-grounding."
                )
            if action == "clear_scene" and forced:
                return (
                    f"Catastrophic state detected (attempt {attempt}). "
                    "Hard recovery is forcing clear_scene; rebuild todos if they assume deleted assets."
                )
            if action in {"undo_last_snapshot", "clear_scene"} and not forced:
                return (
                    f"Catastrophic state detected (attempt {attempt}), but automatic recovery budget is exhausted. "
                    "Continue with manual remediation and consider rebuilding todos if scene state was reset."
                )
        return "Catastrophic state detected; no automatic recovery tool is currently available."
    if status == "match":
        return "Latest verification is match. Continue with the next pending todo."

    render_source = verification.get("render_source")
    is_scene_level = isinstance(render_source, str) and render_source == "scene_observe"
    focus_candidates: list[str] = []

    for line in active_todo_context(state):
        todo_id = str(line.get("todo_id", "")).strip()
        title = str(line.get("title", "")).strip()
        label = f"{todo_id} {title}".strip()
        if not label:
            continue
        if label not in focus_candidates:
            focus_candidates.append(label)
        if len(focus_candidates) >= 4:
            break

    focus_text = ""
    if focus_candidates:
        focus_text = " Focus first on: " + ", ".join(focus_candidates[:4]) + "."

    if is_scene_level:
        return (
            "Global verification still reports mismatches. "
            "Before editing, run object-level inspection with "
            "render_from_objects(object_names=[...], mode=\"annotated\") "
            "to localize exact problem objects and positions."
            + focus_text
        )
    return (
        "Object-level verification is not yet match. "
        "Run render_from_objects(object_names=[...], mode=\"annotated\") "
        "before the next edit so you can locate and fix issues precisely."
        + focus_text
    )
