"""Prompt projection helpers for long-running workflows."""

from __future__ import annotations

import copy
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from scene_agent.vlm.metrics import invoke_with_metrics

CONTEXT_SUMMARY_MESSAGE_ID = "context_summary_current"
_DEFAULT_CONTEXT_TOKEN_BUDGET_RATIO = 0.8
_FALLBACK_SUMMARY_MAX_ITEMS = 10
_SUMMARY_MODEL_SOURCE_MAX_ITEMS = 24
_SUMMARY_MAX_CHARS = 1200


def _message_tool_calls(message: Any) -> list[dict[str, Any]]:
    raw_tool_calls = getattr(message, "tool_calls", None)
    if isinstance(raw_tool_calls, list):
        return [call for call in raw_tool_calls if isinstance(call, dict)]
    additional_kwargs = getattr(message, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        nested_calls = additional_kwargs.get("tool_calls")
        if isinstance(nested_calls, list):
            return [call for call in nested_calls if isinstance(call, dict)]
    return []


def _latest_tool_batch_indices(state_messages: list[Any]) -> tuple[int | None, list[int]]:
    for idx in range(len(state_messages) - 1, -1, -1):
        message = state_messages[idx]
        if not isinstance(message, AIMessage):
            continue
        if not _message_tool_calls(message):
            continue

        batch_indices: list[int] = []
        cursor = idx + 1
        while cursor < len(state_messages) and isinstance(state_messages[cursor], ToolMessage):
            batch_indices.append(cursor)
            cursor += 1
        return idx, batch_indices
    return None, []


def _message_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return " ".join(part.strip() for part in parts if isinstance(part, str) and part.strip())
    if isinstance(content, dict):
        return str(content)
    return str(content).strip()


def _compact_message_line(message: Any) -> str:
    role = "message"
    if isinstance(message, HumanMessage):
        role = "user"
    elif isinstance(message, AIMessage):
        role = "assistant"
    elif isinstance(message, ToolMessage):
        tool_name = getattr(message, "name", None)
        role = f"tool:{tool_name}" if isinstance(tool_name, str) and tool_name else "tool"
    elif isinstance(message, SystemMessage):
        role = "system"

    text = " ".join(_message_text(message).split())
    if len(text) > 180:
        text = f"{text[:180]}..."
    return f"- {role}: {text}" if text else f"- {role}"


def _coerce_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        candidate = int(value)
        return candidate if candidate > 0 else None
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.isdigit():
            candidate = int(normalized)
            return candidate if candidate > 0 else None
    return None


def _resolve_max_input_tokens(model: Any) -> int | None:
    if model is None:
        return None

    profile = getattr(model, "profile", None)
    if isinstance(profile, dict):
        profile_tokens = _coerce_positive_int(profile.get("max_input_tokens"))
        if profile_tokens is not None:
            return profile_tokens

    for attr_name in ("max_context_size", "max_input_tokens", "context_window"):
        raw_value = getattr(model, attr_name, None)
        if callable(raw_value):
            try:
                raw_value = raw_value()
            except Exception:
                raw_value = None
        resolved = _coerce_positive_int(raw_value)
        if resolved is not None:
            return resolved

    bound = getattr(model, "bound", None)
    if bound is not None and bound is not model:
        return _resolve_max_input_tokens(bound)
    return None


def _estimate_token_usage(model: Any, messages: list[Any]) -> int | None:
    if model is None:
        return None
    counter = getattr(model, "get_num_tokens_from_messages", None)
    if not callable(counter):
        bound = getattr(model, "bound", None)
        if bound is not None and bound is not model:
            return _estimate_token_usage(bound, messages)
        return None
    try:
        raw_value = counter(messages)
    except Exception:
        return None
    if isinstance(raw_value, bool):
        return None
    if isinstance(raw_value, (int, float)):
        return max(0, int(raw_value))
    if isinstance(raw_value, str) and raw_value.strip().isdigit():
        return int(raw_value.strip())
    return None


def _target_token_budget(model: Any, *, token_budget_ratio: float) -> int | None:
    max_input_tokens = _resolve_max_input_tokens(model)
    if max_input_tokens is None:
        return None
    clamped_ratio = min(0.95, max(0.25, float(token_budget_ratio)))
    budget = int(max_input_tokens * clamped_ratio)
    return budget if budget > 0 else None


def _is_milestone_message(message: Any) -> bool:
    if isinstance(message, AIMessage):
        return len(_message_tool_calls(message)) > 0
    if isinstance(message, ToolMessage):
        tool_name = str(getattr(message, "name", "") or "").lower()
        return any(
            marker in tool_name
            for marker in ("verification", "render", "observe", "camera", "todo")
        )
    if isinstance(message, SystemMessage):
        return bool(_message_text(message))
    return False


def _sample_summary_source_indices(
    omitted_indices: list[int],
    state_messages: list[Any],
    *,
    max_items: int,
) -> list[int]:
    if len(omitted_indices) <= max_items:
        return list(omitted_indices)

    selected: set[int] = set()
    for idx in omitted_indices[:2]:
        selected.add(idx)
    for idx in omitted_indices[-6:]:
        selected.add(idx)

    for idx in omitted_indices:
        if len(selected) >= max_items:
            break
        if idx in selected:
            continue
        if _is_milestone_message(state_messages[idx]):
            selected.add(idx)

    if len(selected) < max_items:
        remaining = [idx for idx in omitted_indices if idx not in selected]
        slots = max_items - len(selected)
        if remaining and slots > 0:
            for offset in range(slots):
                position = int((offset + 1) * len(remaining) / (slots + 1))
                selected.add(remaining[min(position, len(remaining) - 1)])

    return sorted(selected)


def _build_fallback_summary_text(
    state_messages: list[Any],
    omitted_indices: list[int],
) -> str:
    if not omitted_indices:
        return ""

    sampled_indices = _sample_summary_source_indices(
        omitted_indices,
        state_messages,
        max_items=_FALLBACK_SUMMARY_MAX_ITEMS,
    )
    summary_lines = [
        f"Historical context summary: {len(omitted_indices)} earlier messages were compacted.",
        "Key points from omitted history:",
    ]
    for idx in sampled_indices:
        summary_lines.append(_compact_message_line(state_messages[idx]))
    extra_count = len(omitted_indices) - len(sampled_indices)
    if extra_count > 0:
        summary_lines.append(f"- ... {extra_count} additional compacted messages not shown")
    return "\n".join(summary_lines)


def _coerce_response_text(response: Any) -> str:
    if response is None:
        return ""
    if isinstance(response, str):
        return response.strip()
    text = _message_text(response)
    if text:
        return text
    return str(response).strip()


def _summarize_omitted_history_with_model(
    *,
    summary_model: Any | None,
    state_messages: list[Any],
    omitted_indices: list[int],
    thread_id: str | None = None,
    turn_id: str | None = None,
) -> tuple[str | None, list[dict[str, Any]]]:
    if summary_model is None or not omitted_indices:
        return None, []

    source_indices = _sample_summary_source_indices(
        omitted_indices,
        state_messages,
        max_items=_SUMMARY_MODEL_SOURCE_MAX_ITEMS,
    )
    compact_history = "\n".join(_compact_message_line(state_messages[idx]) for idx in source_indices)
    prompt_messages = [
        SystemMessage(
            content=(
                "You summarize omitted 3D-scene agent history for prompt compression.\n"
                "Write plain text with at most 6 short bullet lines.\n"
                "Preserve: user intent changes, tool actions, verification outcomes, failures, and active constraints.\n"
                "Do not invent details and do not emit markdown code fences."
            )
        ),
        HumanMessage(
            content=(
                f"omitted_message_count: {len(omitted_indices)}\n"
                "sampled_omitted_history:\n"
                f"{compact_history}"
            )
        ),
    ]

    invoke_model = summary_model
    if hasattr(summary_model, "with_config"):
        try:
            invoke_model = summary_model.with_config(
                tags=["nostream"],
                run_name="context_summary_internal",
            )
        except Exception:
            invoke_model = summary_model

    llm_call_records: list[dict[str, Any]] = []
    try:
        response, llm_call_record = invoke_with_metrics(
            invoke_model,
            prompt_messages,
            thread_id=thread_id or "default",
            turn_id=turn_id,
            node_name="context_summary",
            call_role="context_summary",
        )
        if isinstance(llm_call_record, dict):
            llm_call_records.append(llm_call_record)
    except TypeError:
        try:
            response, llm_call_record = invoke_with_metrics(
                summary_model,
                prompt_messages,
                thread_id=thread_id or "default",
                turn_id=turn_id,
                node_name="context_summary",
                call_role="context_summary",
            )
            if isinstance(llm_call_record, dict):
                llm_call_records.append(llm_call_record)
        except Exception:
            return None, []
    except Exception:
        return None, []

    response_text = _coerce_response_text(response)
    if not response_text:
        return None, llm_call_records

    normalized = "\n".join(
        line.strip()
        for line in response_text.splitlines()
        if isinstance(line, str) and line.strip()
    )
    if not normalized:
        return None, llm_call_records
    if len(normalized) > _SUMMARY_MAX_CHARS:
        normalized = f"{normalized[:_SUMMARY_MAX_CHARS - 3].rstrip()}..."
    return (
        f"Historical context summary: {len(omitted_indices)} earlier messages were compacted.\n"
        f"{normalized}",
        llm_call_records,
    )


def _shrink_summary_text(summary_text: str) -> str:
    lines = [line.strip() for line in summary_text.splitlines() if line.strip()]
    if len(lines) <= 1:
        return ""

    header = lines[0]
    body = lines[1:]
    if len(body) > 4:
        condensed = body[:1] + body[-2:]
        return "\n".join([header, *condensed])

    shortened_body: list[str] = []
    changed = False
    for line in body:
        if len(line) > 160:
            shortened_body.append(f"{line[:157].rstrip()}...")
            changed = True
        else:
            shortened_body.append(line)
    if changed:
        return "\n".join([header, *shortened_body])
    return ""


def _build_projected_messages(
    *,
    base_messages: list[Any],
    state_messages: list[Any],
    selected_indices: list[int],
    summary_text: str,
) -> list[Any]:
    projected = list(base_messages)
    if summary_text:
        projected.append(SystemMessage(id=CONTEXT_SUMMARY_MESSAGE_ID, content=summary_text))
    for idx in selected_indices:
        projected.append(copy.deepcopy(state_messages[idx]))
    return projected


def build_projected_context(
    *,
    base_messages: list[Any],
    state_messages: list[Any],
    pinned_message_ids: set[str],
    max_recent_messages: int = 12,
    token_counter: Any | None = None,
    summary_model: Any | None = None,
    token_budget_ratio: float = _DEFAULT_CONTEXT_TOKEN_BUDGET_RATIO,
    thread_id: str | None = None,
    turn_id: str | None = None,
) -> tuple[list[Any], str, int, list[dict[str, Any]]]:
    total = len(state_messages)
    if total <= 0:
        return list(base_messages), "", 0, []

    selected_indices: set[int] = set()
    protected_indices: set[int] = set()

    latest_user_idx: int | None = None
    latest_verification_idx: int | None = None
    latest_tool_request_idx, latest_tool_batch_indices = _latest_tool_batch_indices(state_messages)
    latest_pinned_by_id: dict[str, int] = {}

    for idx in range(total - 1, -1, -1):
        message = state_messages[idx]
        message_id = getattr(message, "id", None)
        if isinstance(message_id, str) and message_id in pinned_message_ids and message_id not in latest_pinned_by_id:
            latest_pinned_by_id[message_id] = idx
        if latest_user_idx is None and isinstance(message, HumanMessage):
            if not (isinstance(message_id, str) and message_id in pinned_message_ids):
                latest_user_idx = idx
        if latest_verification_idx is None and isinstance(message, ToolMessage):
            name = getattr(message, "name", None)
            if isinstance(name, str) and "verification" in name:
                latest_verification_idx = idx

    for idx in latest_pinned_by_id.values():
        selected_indices.add(idx)
        protected_indices.add(idx)
    if latest_user_idx is not None:
        selected_indices.add(latest_user_idx)
        protected_indices.add(latest_user_idx)
    if latest_verification_idx is not None:
        selected_indices.add(latest_verification_idx)
        protected_indices.add(latest_verification_idx)
    if latest_tool_request_idx is not None:
        selected_indices.add(latest_tool_request_idx)
        protected_indices.add(latest_tool_request_idx)
    for idx in latest_tool_batch_indices:
        selected_indices.add(idx)
        protected_indices.add(idx)

    for idx in range(total - 1, -1, -1):
        if len(selected_indices) >= max_recent_messages:
            break
        if idx in selected_indices:
            continue
        selected_indices.add(idx)

    token_budget = _target_token_budget(
        token_counter,
        token_budget_ratio=token_budget_ratio,
    )

    while token_budget is not None:
        selected_list = sorted(selected_indices)
        omitted_indices = [idx for idx in range(total) if idx not in selected_indices]
        fallback_summary_text = _build_fallback_summary_text(state_messages, omitted_indices)
        projected = _build_projected_messages(
            base_messages=base_messages,
            state_messages=state_messages,
            selected_indices=selected_list,
            summary_text=fallback_summary_text,
        )
        estimated_tokens = _estimate_token_usage(token_counter, projected)
        if estimated_tokens is None or estimated_tokens <= token_budget:
            break
        removable = [idx for idx in selected_list if idx not in protected_indices]
        if not removable:
            break
        selected_indices.remove(removable[0])

    selected_list = sorted(selected_indices)
    omitted_indices = [idx for idx in range(total) if idx not in selected_indices]
    omitted_count = len(omitted_indices)
    fallback_summary_text = _build_fallback_summary_text(state_messages, omitted_indices)
    summary_text = fallback_summary_text

    model_summary_text, llm_call_records = _summarize_omitted_history_with_model(
        summary_model=summary_model,
        state_messages=state_messages,
        omitted_indices=omitted_indices,
        thread_id=thread_id,
        turn_id=turn_id,
    )
    if model_summary_text:
        summary_text = model_summary_text

    if token_budget is not None and summary_text:
        if model_summary_text:
            candidate = _build_projected_messages(
                base_messages=base_messages,
                state_messages=state_messages,
                selected_indices=selected_list,
                summary_text=summary_text,
            )
            estimated_tokens = _estimate_token_usage(token_counter, candidate)
            if estimated_tokens is not None and estimated_tokens > token_budget:
                summary_text = fallback_summary_text

        while summary_text:
            candidate = _build_projected_messages(
                base_messages=base_messages,
                state_messages=state_messages,
                selected_indices=selected_list,
                summary_text=summary_text,
            )
            estimated_tokens = _estimate_token_usage(token_counter, candidate)
            if estimated_tokens is None or estimated_tokens <= token_budget:
                break
            smaller_summary = _shrink_summary_text(summary_text)
            if not smaller_summary or smaller_summary == summary_text:
                summary_text = ""
                break
            summary_text = smaller_summary

    projected = _build_projected_messages(
        base_messages=base_messages,
        state_messages=state_messages,
        selected_indices=selected_list,
        summary_text=summary_text,
    )
    return projected, summary_text, omitted_count, llm_call_records
