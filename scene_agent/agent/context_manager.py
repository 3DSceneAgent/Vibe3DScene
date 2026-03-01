"""Prompt projection helpers for long-running workflows."""

from __future__ import annotations

import copy
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage


CONTEXT_SUMMARY_MESSAGE_ID = "context_summary_current"


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


def build_projected_context(
    *,
    base_messages: list[Any],
    state_messages: list[Any],
    pinned_message_ids: set[str],
    max_recent_messages: int = 12,
) -> tuple[list[Any], str, int]:
    total = len(state_messages)
    if total <= max_recent_messages:
        return base_messages + list(state_messages), "", 0

    selected_indices: set[int] = set()

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
    if latest_user_idx is not None:
        selected_indices.add(latest_user_idx)
    if latest_verification_idx is not None:
        selected_indices.add(latest_verification_idx)
    if latest_tool_request_idx is not None:
        selected_indices.add(latest_tool_request_idx)
    for idx in latest_tool_batch_indices:
        selected_indices.add(idx)

    for idx in range(total - 1, -1, -1):
        if len(selected_indices) >= max_recent_messages:
            break
        if idx in selected_indices:
            continue
        selected_indices.add(idx)

    selected_list = sorted(selected_indices)
    omitted_indices = [idx for idx in range(total) if idx not in selected_indices]
    summary_text = ""
    if omitted_indices:
        summary_lines = [
            f"Historical context summary: {len(omitted_indices)} earlier messages were compacted.",
            "Recent highlights from omitted history:",
        ]
        for idx in omitted_indices[-4:]:
            summary_lines.append(_compact_message_line(state_messages[idx]))
        summary_text = "\n".join(summary_lines)

    projected = list(base_messages)
    if summary_text:
        projected.append(SystemMessage(id=CONTEXT_SUMMARY_MESSAGE_ID, content=summary_text))
    for idx in selected_list:
        projected.append(copy.deepcopy(state_messages[idx]))
    return projected, summary_text, len(omitted_indices)
