"""Agent-side nodes: role agents, plan decomposition, and dispatch helpers."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from scene_agent.agent.memory_scope import merge_role_private_memory
from scene_agent.agent.state import AgentState
from scene_agent.agent.todo_state import apply_todo_actions, project_latest_todos
from scene_agent.utils.agent_messages import find_last_ai_message, message_content_to_text
from scene_agent.utils.todo_helpers import coerce_non_negative_int

from .constants_workflow import MODE_PLAN, ROLE_BUILDER, ROLE_GENERAL, ROLE_VERIFIER
from .shared import (
    ai_message_has_tool_calls,
    invoke_role_agent,
    latest_human_message,
    resolve_verification_assets,
    unfinished_todo_count,
)


class PlannedTodo(BaseModel):
    title: str
    description: str


class PlanOutput(BaseModel):
    todos: list[PlannedTodo] = Field(default_factory=list)


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
            "- Each todo should be independently verifiable by render inspection."
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
            if hasattr(llm, "with_structured_output"):
                llm = llm.with_structured_output(PlanOutput)
            decision_raw = llm.invoke(planner_messages)
            raw_text = message_content_to_text(getattr(decision_raw, "content", decision_raw)).strip()
            if raw_text and not isinstance(decision_raw, (dict, PlanOutput)):
                fallback_todo_text = raw_text
            if isinstance(decision_raw, PlanOutput):
                planned_todos = decision_raw.todos
            else:
                planned_todos = PlanOutput.model_validate(decision_raw).todos
        except Exception:
            try:
                raw_llm = planner_model
                if hasattr(planner_model, "with_config"):
                    raw_llm = planner_model.with_config(
                        tags=["nostream"],
                        run_name="plan_node_fallback_internal",
                    )
                raw_response = raw_llm.invoke(planner_messages)
                raw_text = message_content_to_text(getattr(raw_response, "content", raw_response)).strip()
                if raw_text and not isinstance(raw_response, dict):
                    fallback_todo_text = raw_text
            except Exception:
                pass
            planned_todos = []

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
    return {
        "task_mode": MODE_PLAN,
        "routed_to_plan": True,
        "todo_versions": todo_versions,
        "todos": todos,
        "active_todo_id": next_active_todo_id,
        "current_todo_stall_count": 0,
    }


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
    return {
        "assistant_turn_kind": "has_calls" if has_calls else "no_calls",
        "request_agent_turns": current_turns + 1,
        "verification_result": None,
    }


def post_agent_node(state: AgentState) -> dict[str, Any]:
    return turn_dispatch_node(state)


def post_builder_node(state: AgentState) -> dict[str, Any]:
    """Dual-agent builder post-processing (counts + stall tracking only)."""
    latest_ai_message = find_last_ai_message(list(state.get("messages") or [])[-10:])
    has_calls = ai_message_has_tool_calls(latest_ai_message)

    result: dict[str, Any] = {
        "active_role": ROLE_BUILDER,
        "request_agent_turns": coerce_non_negative_int(state.get("request_agent_turns")) + 1,
        "builder_turn_count": coerce_non_negative_int(state.get("builder_turn_count")) + 1,
        "builder_stall_count": 0 if has_calls else coerce_non_negative_int(state.get("builder_stall_count")) + 1,
        "assistant_turn_kind": "has_calls" if has_calls else "no_calls",
        "verification_result": None,
    }
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
