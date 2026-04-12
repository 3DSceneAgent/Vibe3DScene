"""Agent-side nodes: role agents, plan decomposition, and dispatch helpers."""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from scene_agent.agent.memory_scope import merge_role_private_memory
from scene_agent.agent.state import AgentState
from scene_agent.agent.todo_state import apply_todo_actions, project_latest_todos
from scene_agent.utils.agent_messages import find_last_ai_message, message_content_to_text
from scene_agent.utils.todo_helpers import coerce_non_negative_int
from scene_agent.vlm.metrics import invoke_structured_with_metrics, invoke_with_metrics

from .constants_workflow import MODE_PLAN, ROLE_BUILDER, ROLE_GENERAL, ROLE_VERIFIER
from .shared import (
    ai_message_has_tool_calls,
    coerce_budget_limit,
    invoke_role_agent,
    latest_human_message,
    latest_human_turn_id,
    resolve_verification_assets,
    unfinished_todo_count,
)


class PlannedTodo(BaseModel):
    title: str
    description: str


class PlanOutput(BaseModel):
    todos: list[PlannedTodo] = Field(default_factory=list)


_FENCED_BLOCK_RE = re.compile(r"^\s*```(?:[a-zA-Z0-9_-]+)?\s*(.*?)\s*```\s*$", re.DOTALL)
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|(?:\d+|[A-Za-z])[\.\)])\s+(.*\S)\s*$")
_INLINE_NUMBERED_LIST_RE = re.compile(
    r"(?:^|\s)(?:\d+|[A-Za-z])[\.\)]\s+(.*?)(?=(?:\s+(?:\d+|[A-Za-z])[\.\)]\s+)|$)",
    re.DOTALL,
)


def _apply_agent_turn_budget(
    state: AgentState,
    *,
    next_turns: int,
    update: dict[str, Any],
) -> dict[str, Any]:
    max_turns = coerce_budget_limit(state.get("max_request_agent_turns"))
    if max_turns >= 0 and next_turns >= max_turns:
        update["request_stop_reason"] = "max_request_agent_turns_reached"
        update["transition_reason"] = "max_request_agent_turns_reached"
        update["transition_next"] = "finalize"
    return update


def _normalize_freeform_text(value: str) -> str:
    return " ".join(value.strip().split())


def _coerce_planned_todo(raw: Any) -> PlannedTodo | None:
    if isinstance(raw, PlannedTodo):
        return raw
    if isinstance(raw, str):
        normalized = _normalize_freeform_text(raw)
        if not normalized:
            return None
        return PlannedTodo(title=normalized[:180], description=normalized[:500])
    if not isinstance(raw, dict):
        return None

    raw_title = raw.get("title")
    raw_description = raw.get("description")
    title = _normalize_freeform_text(raw_title) if isinstance(raw_title, str) else ""
    description = _normalize_freeform_text(raw_description) if isinstance(raw_description, str) else ""
    if not title and not description:
        return None
    if not title:
        title = description[:180]
    if not description:
        description = title
    return PlannedTodo(title=title[:180], description=description[:500])


def _coerce_planned_todos_payload(raw: Any) -> list[PlannedTodo]:
    if isinstance(raw, PlanOutput):
        return list(raw.todos)
    if isinstance(raw, dict):
        if "todos" in raw:
            return _coerce_planned_todos_payload(raw.get("todos"))
        todo = _coerce_planned_todo(raw)
        return [todo] if todo is not None else []
    if not isinstance(raw, list):
        return []

    todos: list[PlannedTodo] = []
    for item in raw:
        todo = _coerce_planned_todo(item)
        if todo is not None:
            todos.append(todo)
    return todos


def _strip_markdown_fence(raw_text: str) -> str:
    match = _FENCED_BLOCK_RE.fullmatch(raw_text)
    if match:
        return match.group(1).strip()
    return raw_text.strip()


def _planned_todos_from_list_text(raw_text: str) -> list[PlannedTodo]:
    items: list[str] = []
    current_parts: list[str] = []
    for raw_line in raw_text.splitlines():
        line = _normalize_freeform_text(raw_line)
        if not line:
            continue
        match = _LIST_ITEM_RE.match(line)
        if match:
            if current_parts:
                items.append(" ".join(current_parts))
            current_parts = [match.group(1).strip()]
            continue
        if current_parts:
            current_parts.append(line)
    if current_parts:
        items.append(" ".join(current_parts))
    if items:
        return [PlannedTodo(title=item[:180], description=item[:500]) for item in items if item]

    collapsed = _normalize_freeform_text(raw_text)
    inline_matches = [
        _normalize_freeform_text(match.group(1))
        for match in _INLINE_NUMBERED_LIST_RE.finditer(collapsed)
        if _normalize_freeform_text(match.group(1))
    ]
    if len(inline_matches) >= 2:
        return [
            PlannedTodo(title=item[:180], description=item[:500])
            for item in inline_matches
        ]
    return []


def _recover_planned_todos_from_text(raw_text: str) -> list[PlannedTodo]:
    sanitized = _strip_markdown_fence(raw_text)
    if not sanitized:
        return []

    try:
        parsed = json.loads(sanitized)
    except json.JSONDecodeError:
        parsed = None
    if parsed is not None:
        recovered = _coerce_planned_todos_payload(parsed)
        if recovered:
            return recovered

    return _planned_todos_from_list_text(sanitized)


def _extract_planned_todos_from_model_output(decision_raw: Any, raw_text: str) -> list[PlannedTodo]:
    recovered = _coerce_planned_todos_payload(decision_raw)
    if recovered:
        return recovered

    if raw_text:
        return _recover_planned_todos_from_text(raw_text)
    return []


def _normalize_planned_todos(raw_todos: list[PlannedTodo], fallback_text: str) -> list[PlannedTodo]:
    normalized: list[PlannedTodo] = []
    for raw in raw_todos:
        title = " ".join(str(raw.title).strip().split())
        description = " ".join(str(raw.description).strip().split())
        if not title and not description:
            continue
        if not title:
            title = description[:120]
        if not description:
            description = title
        normalized.append(PlannedTodo(title=title[:180], description=description[:500]))

    if normalized:
        return normalized

    fallback = " ".join(fallback_text.strip().split()) or "Complete the user's request"
    return [PlannedTodo(title=fallback, description=fallback)]


def plan_node(
    state: AgentState,
    *,
    planner_model: Any | None = None,
) -> dict[str, Any]:
    """Decompose user request into ordered todos via structured VLM output."""
    if unfinished_todo_count(state) > 0:
        todos = project_latest_todos(
            state.get("todo_versions"),
            fallback_todos_raw=state.get("todos"),
        )
        active_todo_id = state.get("active_todo_id")
        if not isinstance(active_todo_id, str) or not active_todo_id.strip():
            active_todo_id = None
            for todo in todos:
                todo_id = str(todo.get("id", "")).strip()
                status = str(todo.get("status", "")).strip()
                if todo_id and status in {"pending", "in_progress"}:
                    active_todo_id = todo_id
                    break
        return {
            "task_mode": MODE_PLAN,
            "routed_to_plan": True,
            "active_todo_id": active_todo_id,
        }

    latest_request = latest_human_message(state)
    planned_todos: list[PlannedTodo] = []
    fallback_todo_text = latest_request
    llm_call_records: list[dict[str, Any]] = []
    thread_id = str(state.get("thread_id") or "default")
    turn_id = latest_human_turn_id(state)
    if planner_model is not None:
        reference_entries = resolve_verification_assets(state)
        scene_objects = state.get("scene_objects")
        scene_object_names: list[str] = []
        if isinstance(scene_objects, dict):
            scene_object_names = [
                name for name in scene_objects.keys() if isinstance(name, str) and name
            ][:40]

        references_text = []
        for entry in reference_entries[:5]:
            if not isinstance(entry, dict):
                continue
            caption = str(entry.get("caption", "")).strip()
            asset_id = str(entry.get("asset_id", "")).strip()
            stored_path = str(entry.get("stored_path", "")).strip()
            if not (caption or asset_id or stored_path):
                continue
            references_text.append(
                f"- asset_id={asset_id or 'unknown'}, caption={caption or 'n/a'}, path={stored_path or 'n/a'}"
            )
        references_block = "\n".join(references_text) if references_text else "- none"
        objects_block = ", ".join(scene_object_names) if scene_object_names else "none"

        prompt = (
            "You are a task planner for a 3D scene editing agent.\n"
            "Break the request into ordered, executable todos.\n"
            "Return structured output only."
        )
        planner_input = (
            f"user_request: {latest_request}\n"
            f"scene_objects: {objects_block}\n"
            f"reference_images:\n{references_block}\n"
            "Rules:\n"
            "- Keep todos concrete and action-oriented.\n"
            "- 1 to 6 todos.\n"
            "- Each todo should be independently verifiable by render inspection.\n"
            "- Todos must be mutually exclusive and minimally overlapping.\n"
            "- Each todo should own a distinct scene change, object group, or spatial area.\n"
            "- Earlier todos may prepare prerequisites, but must not already complete the core deliverable of later todos.\n"
            "- If a later todo is about placing or refining a hero object, keep that hero object out of earlier setup/blockout todos.\n"
            '- Example: if one todo is "Set up the basic beach environment" and a later todo is "Place the central camper van", the beach setup todo must NOT place the camper van.'
        )
        planner_messages = [
            SystemMessage(content=prompt),
            HumanMessage(content=planner_input),
        ]
        try:
            llm = planner_model
            if hasattr(planner_model, "with_config"):
                llm = planner_model.with_config(
                    tags=["nostream"],
                    run_name="plan_node_internal",
                )
            decision_raw, llm_call_record = invoke_structured_with_metrics(
                llm,
                PlanOutput,
                planner_messages,
                thread_id=thread_id,
                turn_id=turn_id,
                node_name="plan",
                call_role="planner",
            )
            if isinstance(llm_call_record, dict):
                llm_call_records.append(llm_call_record)
            raw_text = message_content_to_text(getattr(decision_raw, "content", decision_raw)).strip()
            planned_todos = _extract_planned_todos_from_model_output(decision_raw, raw_text)
        except Exception:
            try:
                raw_llm = planner_model
                if hasattr(planner_model, "with_config"):
                    raw_llm = planner_model.with_config(
                        tags=["nostream"],
                        run_name="plan_node_fallback_internal",
                    )
                raw_response, llm_call_record = invoke_with_metrics(
                    raw_llm,
                    planner_messages,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    node_name="plan",
                    call_role="planner_fallback",
                )
                if isinstance(llm_call_record, dict):
                    llm_call_records.append(llm_call_record)
                raw_text = message_content_to_text(getattr(raw_response, "content", raw_response)).strip()
                planned_todos = _extract_planned_todos_from_model_output(raw_response, raw_text)
            except Exception:
                pass

    normalized_todos = _normalize_planned_todos(planned_todos, fallback_todo_text)
    actions: list[dict[str, Any]] = []
    for index, todo in enumerate(normalized_todos):
        actions.append(
            {
                "action": "create",
                "title": todo.title,
                "status": "pending",
                "reason": "Generated by plan_node",
                "set_active": index == 0,
            }
        )

    role = str(state.get("active_role") or ROLE_GENERAL)
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
        role=role,
        previous_active_todo_id=previous_active_todo_id,
    )
    result = {
        "task_mode": MODE_PLAN,
        "routed_to_plan": True,
        "todo_versions": todo_versions,
        "todos": todos,
        "active_todo_id": next_active_todo_id,
        "current_todo_stall_count": 0,
    }
    if llm_call_records:
        result["llm_call_records"] = llm_call_records
    return result


def agent_node(
    state: AgentState,
    llm_with_tools: Any,
    available_tool_names: list[str] | None = None,
    summary_model: Any | None = None,
) -> dict[str, Any]:
    return invoke_role_agent(
        state=state,
        llm_with_tools=llm_with_tools,
        available_tool_names=available_tool_names,
        role=ROLE_GENERAL,
        summary_model=summary_model,
    )


def builder_agent_node(
    state: AgentState,
    llm_with_tools: Any,
    available_tool_names: list[str] | None = None,
    summary_model: Any | None = None,
) -> dict[str, Any]:
    return invoke_role_agent(
        state=state,
        llm_with_tools=llm_with_tools,
        available_tool_names=available_tool_names,
        role=ROLE_BUILDER,
        summary_model=summary_model,
    )


def verifier_agent_node(
    state: AgentState,
    llm_with_tools: Any,
    available_tool_names: list[str] | None = None,
    summary_model: Any | None = None,
) -> dict[str, Any]:
    return invoke_role_agent(
        state=state,
        llm_with_tools=llm_with_tools,
        available_tool_names=available_tool_names,
        role=ROLE_VERIFIER,
        summary_model=summary_model,
    )


# Backward-compatible alias used in a few tests/import sites.
verifier_camera_agent_node = verifier_agent_node


def turn_dispatch_node(state: AgentState) -> dict[str, Any]:
    """Single-agent dispatch: binary split into has_calls or no_calls."""
    latest_ai_message = find_last_ai_message(list(state.get("messages") or [])[-10:])
    has_calls = ai_message_has_tool_calls(latest_ai_message)
    current_turns = coerce_non_negative_int(state.get("request_agent_turns"))
    next_turns = current_turns + 1
    return _apply_agent_turn_budget(
        state,
        next_turns=next_turns,
        update={
        "assistant_turn_kind": "has_calls" if has_calls else "no_calls",
        "request_agent_turns": next_turns,
        "verification_result": None,
        },
    )


def post_agent_node(state: AgentState) -> dict[str, Any]:
    return turn_dispatch_node(state)


def post_builder_node(state: AgentState) -> dict[str, Any]:
    """Dual-agent builder post-processing (counts + stall tracking only)."""
    latest_ai_message = find_last_ai_message(list(state.get("messages") or [])[-10:])
    has_calls = ai_message_has_tool_calls(latest_ai_message)
    next_turns = coerce_non_negative_int(state.get("request_agent_turns")) + 1

    result: dict[str, Any] = _apply_agent_turn_budget(
        state,
        next_turns=next_turns,
        update={
        "active_role": ROLE_BUILDER,
        "request_agent_turns": next_turns,
        "builder_turn_count": coerce_non_negative_int(state.get("builder_turn_count")) + 1,
        "builder_stall_count": 0 if has_calls else coerce_non_negative_int(state.get("builder_stall_count")) + 1,
        "assistant_turn_kind": "has_calls" if has_calls else "no_calls",
        "verification_result": None,
        },
    )
    if latest_ai_message is not None:
        builder_note = message_content_to_text(latest_ai_message.content).strip()
        if builder_note:
            result["role_private_memory"] = merge_role_private_memory(
                state.get("role_private_memory"),
                role=ROLE_BUILDER,
                patch={"last_action_summary": builder_note[:1200]},
            )
    return result


def post_verifier_node(state: AgentState) -> dict[str, Any]:
    """Backward-compatible no-op; verifier routing now lives in verifier_feedback."""
    _ = state
    return {}
