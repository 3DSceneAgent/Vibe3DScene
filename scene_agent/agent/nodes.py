"""
LangGraph node implementations.
Nodes follow best practices: return partial state updates only.
"""
import base64
import ast
import hashlib
import json
import mimetypes
import os
import re
import tempfile
from datetime import datetime
from typing import Any, Dict, Literal
from urllib.parse import unquote, urlparse
from langchain_core.messages import ToolMessage, AIMessage, SystemMessage, HumanMessage
from pydantic import BaseModel, Field, ValidationError
from scene_agent.agent.memory_scope import merge_role_private_memory, resolve_memory_profile
from scene_agent.agent.state import AgentState, TaskMode, TodoItem, create_todo
from scene_agent.agent.tool_policy import (
    READ_ONLY_TOOLS,
    coerce_request_tool_budgets,
    resolve_effective_tool_names,
)
from scene_agent.agent.workflow_profiles import (
    normalize_workflow_topology_request,
    resolve_workflow_topology,
)
from scene_agent.config import get_settings
from scene_agent.memory.scene_memory import SceneMemory
from scene_agent.memory.reference_image_memory import GLOBAL_TASK_ID, get_image_asset_memory
from scene_agent.vlm.verification import verify_render_with_references

TODO_CHECK_INTERVAL_ROUNDS = 3
TODO_STAGNATION_LIMIT = 2
TODO_BLOCKED_RECOVERY_ATTEMPTS = 2
TODO_MILESTONE_TOOL_MARKERS = (
    "render_from_camera",
    "render_from_objects",
    "camera_observe",
    "camera_act",
    "observe_scene_global",
)

SCENE_MUTATING_TOOLS: frozenset[str] = frozenset({
    "clear_scene",
    "execute_blender_code",
    "delete_objects",
    "import_glb_model",
    "import_blend_contents",
    "download_polyhaven_asset",
    "set_texture",
    "generate_trellis2_model",
    "import_retrieved_asset",
    "download_sketchfab_model",
    "generate_infinigen_assets",
    "import_generated_asset",
    "generate_hunyuan3d_model",
    "generate_hyper3d_model_via_text",
    "generate_hyper3d_model_via_images",
    "undo_last_snapshot",
})

# Fixed message IDs for internal visual context messages.
# add_messages replaces by ID, so these slots hold at most one message each —
# no unbounded accumulation across turns.
_RENDER_VISION_MESSAGE_ID = "render_vision_current"
_SCENE_OBSERVE_MESSAGE_ID = "scene_observe_current"
_TODO_BLOCKED_RECOVERY_MESSAGE_ID = "todo_blocked_recovery_current"
_TODO_BLOCKED_RECOVERY_ACTION_MESSAGE_ID = "todo_blocked_recovery_action_current"

_CATASTROPHIC_SCENE_DIMENSION_THRESHOLD = 5000.0
_CATASTROPHIC_OBJECT_COORD_THRESHOLD = 5000.0
_CATASTROPHIC_OBJECT_DIMENSION_THRESHOLD = 2000.0
_CATASTROPHIC_RENDER_STDDEV_THRESHOLD = 2.0
_CATASTROPHIC_RENDER_GRAY_DRIFT_THRESHOLD = 3.0
_CATASTROPHIC_RENDER_BLACK_MEAN_THRESHOLD = 4.0
_CATASTROPHIC_RENDER_WHITE_MEAN_THRESHOLD = 251.0

MODE_CONVERSATION: TaskMode = "conversation_mode"
MODE_SINGLE_ACTION: TaskMode = "single_action_mode"
MODE_PLAN: TaskMode = "plan_mode"
TOPOLOGY_SINGLE = "single_agent"
TOPOLOGY_DUAL = "dual_agent"
ROLE_GENERAL = "general"
ROLE_BUILDER = "builder"
ROLE_VERIFIER = "verifier"
DEFAULT_MAX_PLAN_REPLANS = 2

REQUEST_BUDGET_DEFAULTS: dict[TaskMode, dict[str, int]] = {
    MODE_CONVERSATION: {"max_request_agent_turns": 2, "max_request_tool_batches": 0},
    MODE_SINGLE_ACTION: {"max_request_agent_turns": 3, "max_request_tool_batches": 1},
    MODE_PLAN: {"max_request_agent_turns": 8, "max_request_tool_batches": 6},
}

CONVERSATION_READ_ONLY_TOOLS: frozenset[str] = READ_ONLY_TOOLS

_PLAN_INTENT_MARKERS: tuple[str, ...] = (
    "recreate",
    "replicate",
    "match this scene",
    "rebuild scene",
    "entire scene",
    "full scene",
    "from reference",
    "according to reference",
    "layout",
    "composition",
    "lighting and materials",
)

_ACTION_INTENT_MARKERS: tuple[str, ...] = (
    "add ",
    "create ",
    "generate ",
    "import ",
    "place ",
    "move ",
    "rotate ",
    "scale ",
    "delete ",
    "remove ",
    "arrange ",
    "set texture",
)

_IMAGE_QA_MARKERS: tuple[str, ...] = (
    "this image",
    "the image",
    "in the image",
    "what is in",
    "what's in",
)
_ROUTER_MIN_CONFIDENCE = 0.65


class RouterDecision(BaseModel):
    intent: Literal[
        "qa",
        "image_qa",
        "single_scene_action",
        "scene_reconstruction",
        "multi_step_scene_action",
        "continue_existing_plan",
        "clarification_needed",
    ]
    mode: Literal["conversation_mode", "single_action_mode", "plan_mode"]
    confidence: float = Field(ge=0.0, le=1.0)
    need_clarification: bool = False
    clarification_question: str = ""
    requires_scene_mutation: bool = False


def _coerce_task_mode(raw_mode: Any) -> TaskMode:
    if raw_mode in {MODE_CONVERSATION, MODE_SINGLE_ACTION, MODE_PLAN}:
        return raw_mode
    return MODE_PLAN


def _coerce_workflow_topology(raw_topology: Any) -> str:
    if isinstance(raw_topology, str):
        normalized = raw_topology.strip()
        if normalized in {TOPOLOGY_SINGLE, TOPOLOGY_DUAL}:
            return normalized
    return TOPOLOGY_SINGLE


def _coerce_role(raw_role: Any) -> str:
    if isinstance(raw_role, str):
        normalized = raw_role.strip()
        if normalized in {ROLE_GENERAL, ROLE_BUILDER, ROLE_VERIFIER}:
            return normalized
    return ROLE_GENERAL


def _request_budget(mode: TaskMode) -> dict[str, int]:
    return dict(REQUEST_BUDGET_DEFAULTS.get(mode, REQUEST_BUDGET_DEFAULTS[MODE_PLAN]))


def _verification_roles_for_mode(mode: TaskMode) -> set[str]:
    if mode == MODE_CONVERSATION:
        return {"question_image", "verification_reference", "style_reference"}
    if mode == MODE_SINGLE_ACTION:
        return {"object_reference", "verification_reference", "style_reference"}
    return {"scene_reference", "object_reference", "verification_reference", "style_reference"}


def _auto_binding_role_for_mode(mode: TaskMode) -> str:
    if mode == MODE_CONVERSATION:
        return "question_image"
    if mode == MODE_SINGLE_ACTION:
        return "object_reference"
    return "scene_reference"


def _unfinished_todo_count(state: AgentState) -> int:
    todos = _coerce_todos(state.get("todos"))
    latest = _latest_todos_by_description(todos)
    effective = list(latest.values()) if latest else todos
    return sum(1 for todo in effective if todo.get("status") in {"pending", "in_progress"})


def _classify_task_mode_from_text(text: str) -> tuple[TaskMode, str]:
    normalized = " ".join((text or "").strip().lower().split())
    if not normalized:
        return MODE_CONVERSATION, "qa"

    if any(marker in normalized for marker in _PLAN_INTENT_MARKERS):
        return MODE_PLAN, "scene_reconstruction"

    action_hits = sum(1 for marker in _ACTION_INTENT_MARKERS if marker in normalized)
    if action_hits == 0:
        if any(marker in normalized for marker in _IMAGE_QA_MARKERS):
            return MODE_CONVERSATION, "image_qa"
        return MODE_CONVERSATION, "qa"

    # Multi-action phrasing usually indicates a plan-level workflow.
    if action_hits >= 2 or " and " in normalized or " then " in normalized:
        return MODE_PLAN, "multi_step_scene_action"

    return MODE_SINGLE_ACTION, "single_scene_action"


def _build_router_clarification_question(text: str) -> str:
    mode_guess, _ = _classify_task_mode_from_text(text)
    if mode_guess == MODE_PLAN:
        return (
            "我需要先确认目标：你是要完整多步重建场景（plan_mode），"
            "还是只做一个单步修改（single_action_mode）？请明确最终目标和成功标准。"
        )
    if mode_guess == MODE_SINGLE_ACTION:
        return (
            "请补充这个单步动作的关键约束：目标对象、位置/尺寸、材质或参考图用途，"
            "以便我准确执行。"
        )
    return (
        "请确认你的意图：是只做问答解释，还是要我对 3D 场景执行修改？"
        "如果要修改，请描述具体动作。"
    )


def _router_has_images(thread_id: str) -> bool:
    try:
        memory = get_reference_image_memory()
        if hasattr(memory, "list_assets"):
            return len(memory.list_assets(thread_id)) > 0
        if hasattr(memory, "list_images"):
            return len(memory.list_images(thread_id)) > 0
    except Exception:
        return False
    return False


def _invoke_router_decision(
    *,
    state: AgentState,
    router_model: Any,
    latest_user_request: str,
) -> RouterDecision:
    thread_id = state.get("thread_id", "default")
    has_images = _router_has_images(thread_id)
    unfinished_todos = _unfinished_todo_count(state)
    topology_hint = state.get("workflow_topology_request") or state.get("workflow_topology") or "auto"

    router_prompt = (
        "You are a strict workflow router for a 3D scene agent.\n"
        "Output only structured fields.\n"
        "Routing modes:\n"
        "- conversation_mode: QA / explanation / image understanding only.\n"
        "- single_action_mode: one scene edit expected to finish quickly.\n"
        "- plan_mode: multi-step scene construction/reconstruction.\n"
        "If user intent is ambiguous, set need_clarification=true and provide a concise clarification_question.\n"
        "Return confidence in [0,1]."
    )
    router_input = (
        f"latest_user_request: {latest_user_request}\n"
        f"thread_id: {thread_id}\n"
        f"has_uploaded_images: {has_images}\n"
        f"unfinished_todos_count: {unfinished_todos}\n"
        f"requested_workflow_topology: {topology_hint}\n"
    )

    try:
        llm = router_model.with_config(tags=["nostream"], run_name="route_mode_internal")
        structured_llm = llm.with_structured_output(RouterDecision)
        decision_raw = structured_llm.invoke(
            [
                SystemMessage(content=router_prompt),
                HumanMessage(content=router_input),
            ]
        )
        if isinstance(decision_raw, RouterDecision):
            return decision_raw
        return RouterDecision.model_validate(decision_raw)
    except (ValidationError, Exception):
        return RouterDecision(
            intent="clarification_needed",
            mode="conversation_mode",
            confidence=0.0,
            need_clarification=True,
            clarification_question=_build_router_clarification_question(latest_user_request),
            requires_scene_mutation=False,
        )


def _resolve_verification_assets(state: AgentState) -> list[Any]:
    thread_id = state.get("thread_id", "default")
    mode = _coerce_task_mode(state.get("task_mode"))
    task_id = state.get("task_id")
    normalized_task_id = task_id.strip() if isinstance(task_id, str) and task_id.strip() else None
    role_filter = _verification_roles_for_mode(mode)
    settings = get_settings()

    memory = get_reference_image_memory()
    if hasattr(memory, "resolve_assets"):
        try:
            if hasattr(memory, "ensure_auto_bindings"):
                memory.ensure_auto_bindings(
                    thread_id=thread_id,
                    task_id=normalized_task_id or GLOBAL_TASK_ID,
                    preferred_role=_auto_binding_role_for_mode(mode),
                )
            return memory.resolve_assets(
                thread_id=thread_id,
                task_id=normalized_task_id or GLOBAL_TASK_ID,
                roles=role_filter,
                limit=settings.reference_image_max_count,
            )
        except Exception:
            return []
    if hasattr(memory, "list_images"):
        try:
            images = memory.list_images(thread_id)
            return images[-settings.reference_image_max_count :]
        except Exception:
            return []
    return []


# Legacy alias kept for test monkeypatching and extension compatibility.
get_reference_image_memory = get_image_asset_memory


def route_mode_node(
    state: AgentState,
    *,
    router_model: Any | None = None,
) -> Dict[str, Any]:
    """
    LLM-based router for task mode / intent classification.
    Low-confidence decisions require strict clarification before execution.
    """
    latest_user_request = _latest_human_message(state)
    unfinished_todos = _unfinished_todo_count(state)

    if unfinished_todos > 0:
        decision = RouterDecision(
            intent="continue_existing_plan",
            mode=MODE_PLAN,
            confidence=1.0,
            need_clarification=False,
            clarification_question="",
            requires_scene_mutation=True,
        )
    elif router_model is None:
        decision = RouterDecision(
            intent="clarification_needed",
            mode=MODE_CONVERSATION,
            confidence=0.0,
            need_clarification=True,
            clarification_question=_build_router_clarification_question(latest_user_request),
            requires_scene_mutation=False,
        )
    else:
        decision = _invoke_router_decision(
            state=state,
            router_model=router_model,
            latest_user_request=latest_user_request,
        )

    mode = _coerce_task_mode(decision.mode)
    intent = decision.intent

    raw_topology_request = state.get("workflow_topology_request")
    if raw_topology_request is None:
        raw_topology_request = state.get("workflow_topology")
    requested_topology = normalize_workflow_topology_request(raw_topology_request)
    workflow_topology = resolve_workflow_topology(
        task_mode=mode,
        requested_topology=requested_topology,
    )

    raw_memory_profile_request = state.get("memory_profile_request")
    if raw_memory_profile_request is None:
        raw_memory_profile_request = state.get("memory_profile")
    memory_profile_request = "auto"
    if isinstance(raw_memory_profile_request, str):
        normalized_memory_request = raw_memory_profile_request.strip().lower().replace("-", "_")
        if normalized_memory_request in {
            "auto",
            "thread_shared_only",
            "shared_plus_role_private",
        }:
            memory_profile_request = normalized_memory_request
    memory_profile = resolve_memory_profile(memory_profile_request)

    current_task_id = state.get("task_id")
    if isinstance(current_task_id, str) and current_task_id.strip():
        normalized_task_id = current_task_id.strip()[:128]
    else:
        if mode == MODE_CONVERSATION:
            normalized_task_id = "conversation"
        elif mode == MODE_SINGLE_ACTION:
            normalized_task_id = "single_action"
        else:
            normalized_task_id = "plan"

    budget = _request_budget(mode)
    tool_policy = "allow_mutation"
    if mode == MODE_CONVERSATION:
        tool_policy = "forbid_mutation"
    elif mode == MODE_SINGLE_ACTION:
        tool_policy = "allow_mutation_limited"

    active_role = ROLE_GENERAL
    if mode == MODE_PLAN and workflow_topology == TOPOLOGY_DUAL:
        active_role = ROLE_BUILDER

    clarification_question = decision.clarification_question.strip()
    if not clarification_question:
        clarification_question = _build_router_clarification_question(latest_user_request)
    need_clarification = bool(decision.need_clarification) or decision.confidence < _ROUTER_MIN_CONFIDENCE

    max_plan_replans = _coerce_non_negative_int(
        state.get("max_plan_replans"),
        default=DEFAULT_MAX_PLAN_REPLANS,
    )

    return {
        "task_mode": mode,
        "task_intent": intent,
        "task_id": normalized_task_id,
        "router_decision": decision.model_dump(mode="json"),
        "router_confidence": float(decision.confidence),
        "router_need_clarification": need_clarification,
        "router_clarification_question": clarification_question,
        "tool_policy": tool_policy,
        "workflow_topology_request": requested_topology,
        "memory_profile_request": memory_profile_request,
        "workflow_topology": workflow_topology,
        "memory_profile": memory_profile,
        "active_role": active_role,
        "request_agent_turns": 0,
        "request_tool_batches": 0,
        "builder_turn_count": 0,
        "verifier_turn_count": 0,
        "builder_stall_count": 0,
        "verification_mismatch_streak": 0,
        "quality_eval": {"status": "unknown", "reason": "not_evaluated"},
        "progress_eval": {"status": "continue", "reason": "not_evaluated"},
        "budget_eval": {"budget_ok": True, "stop_reason": None},
        "plan_replan_count": 0,
        "max_plan_replans": max_plan_replans,
        "transition_next": None,
        "transition_reason": "router_initialized",
        "max_request_agent_turns": budget["max_request_agent_turns"],
        "max_request_tool_batches": budget["max_request_tool_batches"],
        "request_stop_reason": None,
    }


def route_mode_llm_node(state: AgentState, router_model: Any) -> Dict[str, Any]:
    """Explicit LLM router entrypoint used by graph wiring."""
    return route_mode_node(state, router_model=router_model)


def clarification_node(state: AgentState) -> Dict[str, Any]:
    question_raw = state.get("router_clarification_question")
    question = question_raw.strip() if isinstance(question_raw, str) and question_raw.strip() else (
        "我需要你补充更具体的目标：是问答解释、单步修改，还是多步场景重建？"
    )
    return {
        "messages": [AIMessage(content=question)],
        "request_stop_reason": "clarification_required",
        "transition_next": "finalize",
        "transition_reason": "router_low_confidence_clarification_required",
    }


def _effective_tool_names_for_state(
    state: AgentState,
    tool_names: list[str] | None,
    *,
    role: str = ROLE_GENERAL,
) -> tuple[list[str] | None, str | None]:
    mode = _coerce_task_mode(state.get("task_mode"))
    safe_role = _coerce_role(role)
    request_tool_batches, max_request_tool_batches = coerce_request_tool_budgets(
        request_tool_batches=state.get("request_tool_batches"),
        max_request_tool_batches=state.get("max_request_tool_batches"),
    )
    return resolve_effective_tool_names(
        mode=mode,
        role=safe_role,
        available_tool_names=tool_names,
        requested_tool_names=None,
        request_tool_batches=request_tool_batches,
        max_request_tool_batches=max_request_tool_batches,
    )


def agent_node(
    state: AgentState,
    llm_with_tools,
    available_tool_names: list[str] | None = None,
) -> Dict[str, Any]:
    """
    Agent node: VLM reasoning with all tools bound.
    The agent decides when to perceive, render, and manipulate the scene.
    
    Args:
        state: Current agent state
        llm_with_tools: LLM with tools bound via bind_tools()
        
    Returns:
        Partial state update with new messages
    """
    return _invoke_role_agent(
        state=state,
        llm_with_tools=llm_with_tools,
        available_tool_names=available_tool_names,
        role=ROLE_GENERAL,
    )


def _invoke_role_agent(
    *,
    state: AgentState,
    llm_with_tools: Any,
    available_tool_names: list[str] | None,
    role: str,
) -> Dict[str, Any]:
    # Build messages including system prompt
    from scene_agent.agent.prompts import get_full_system_prompt

    requested_tool_names = _resolve_effective_available_tools(state, available_tool_names)
    effective_tool_names, tool_policy_reason = _effective_tool_names_for_state(
        state,
        requested_tool_names,
        role=role,
    )

    messages = [SystemMessage(content=get_full_system_prompt(effective_tool_names))]
    if role == ROLE_BUILDER:
        messages.append(
            SystemMessage(
                content=(
                    "You are the Builder agent for plan_mode execution. "
                    "Focus on concrete scene edits and tool calls. "
                    "Use verifier feedback and todo context to perform the next highest-impact fix."
                )
            )
        )
    tool_constraints = _build_available_tools_constraint(effective_tool_names)
    if tool_constraints:
        messages.append(SystemMessage(content=tool_constraints))
    if tool_policy_reason == "conversation_mode_read_only":
        messages.append(
            SystemMessage(
                content=(
                    "Current mode is conversation_mode. "
                    "Do not call scene-mutation tools; answer directly unless a read-only check is essential."
                )
            )
        )
    elif tool_policy_reason == "verifier_role_camera_tools":
        messages.append(
            SystemMessage(
                content=(
                    "Current role is verifier. "
                    "Use camera and render tools for verification; do not use scene-asset mutation tools."
                )
            )
        )
    elif tool_policy_reason == "request_tool_budget_exhausted":
        messages.append(
            SystemMessage(
                content=(
                    "Tool budget for this request is exhausted. "
                    "Do not call more tools; provide a concise completion summary."
                )
            )
        )
    messages.extend(state["messages"])
    
    # Invoke the LLM
    response = llm_with_tools.invoke(messages)
    response, dropped_tools = _filter_unavailable_tool_calls(response, effective_tool_names)
    if dropped_tools:
        content_text = _message_content_to_text(getattr(response, "content", ""))
        if not content_text.strip():
            skipped = ", ".join(sorted(set(dropped_tools)))
            response.content = (
                "I skipped unavailable tool calls and will continue with enabled tools only. "
                f"Skipped: {skipped}."
            )
    
    result: Dict[str, Any] = {"messages": [response]}
    if _coerce_role(state.get("active_role")) != role:
        result["active_role"] = role
    return result


def builder_agent_node(
    state: AgentState,
    llm_with_tools,
    available_tool_names: list[str] | None = None,
) -> Dict[str, Any]:
    """
    Builder agent node for dual-agent plan_mode execution.
    """
    return _invoke_role_agent(
        state=state,
        llm_with_tools=llm_with_tools,
        available_tool_names=available_tool_names,
        role=ROLE_BUILDER,
    )


def verifier_camera_agent_node(
    state: AgentState,
    llm_with_tools,
    available_tool_names: list[str] | None = None,
) -> Dict[str, Any]:
    """
    Tool-capable verifier agent.

    This role can operate camera/render inspection tools (including camera
    adjustments) but is blocked from scene asset mutation tools.
    """
    return _invoke_role_agent(
        state=state,
        llm_with_tools=llm_with_tools,
        available_tool_names=available_tool_names,
        role=ROLE_VERIFIER,
    )


def post_agent_node(state: AgentState) -> Dict[str, Any]:
    """
    Post-agent node: persist todo updates and per-request counters after each assistant turn.
    """
    last_messages = state["messages"][-10:]
    latest_ai_message = _find_last_ai_message(last_messages)
    result: Dict[str, Any] = {}

    if latest_ai_message is not None:
        todo_updates = extract_todo_updates([latest_ai_message])
        aligned_todos = _align_todo_updates_with_existing(state.get("todos"), todo_updates)
        if aligned_todos:
            result["todos"] = aligned_todos

    current_turns = _coerce_non_negative_int(state.get("request_agent_turns"))
    next_turns = current_turns + 1
    result["request_agent_turns"] = next_turns

    max_turns = _coerce_non_negative_int(state.get("max_request_agent_turns"), default=-1)
    if max_turns >= 0 and next_turns >= max_turns:
        result["request_stop_reason"] = "agent_turn_budget_exhausted"

    return result


def _ai_message_has_tool_calls(message: AIMessage | None) -> bool:
    if not isinstance(message, AIMessage):
        return False
    tool_calls = getattr(message, "tool_calls", None)
    if isinstance(tool_calls, list) and len(tool_calls) > 0:
        return True
    additional_kwargs = getattr(message, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        raw_calls = additional_kwargs.get("tool_calls")
        return isinstance(raw_calls, list) and len(raw_calls) > 0
    return False


def post_builder_node(state: AgentState) -> Dict[str, Any]:
    """
    Post-builder node used in plan_mode dual-agent execution.
    """
    last_messages = state["messages"][-10:]
    latest_ai_message = _find_last_ai_message(last_messages)
    result: Dict[str, Any] = {"active_role": ROLE_BUILDER}

    if latest_ai_message is not None:
        todo_updates = extract_todo_updates([latest_ai_message])
        aligned_todos = _align_todo_updates_with_existing(state.get("todos"), todo_updates)
        if aligned_todos:
            result["todos"] = aligned_todos

    current_turns = _coerce_non_negative_int(state.get("request_agent_turns"))
    next_turns = current_turns + 1
    result["request_agent_turns"] = next_turns

    current_builder_turns = _coerce_non_negative_int(state.get("builder_turn_count"))
    result["builder_turn_count"] = current_builder_turns + 1

    if _ai_message_has_tool_calls(latest_ai_message):
        result["builder_stall_count"] = 0
    else:
        stall = _coerce_non_negative_int(state.get("builder_stall_count"))
        result["builder_stall_count"] = stall + 1

    max_turns = _coerce_non_negative_int(state.get("max_request_agent_turns"), default=-1)
    if max_turns >= 0 and next_turns >= max_turns:
        result["request_stop_reason"] = "agent_turn_budget_exhausted"

    if latest_ai_message is not None:
        builder_note = _message_content_to_text(latest_ai_message.content).strip()
        if builder_note:
            result["role_private_memory"] = merge_role_private_memory(
                state.get("role_private_memory"),
                role=ROLE_BUILDER,
                patch={
                    "last_action_summary": builder_note[:1200],
                },
            )

    return result


def post_verifier_node(state: AgentState) -> Dict[str, Any]:
    """
    Post-verifier node used in plan_mode dual-agent execution.
    """
    last_messages = state["messages"][-10:]
    latest_ai_message = _find_last_ai_message(last_messages)
    result: Dict[str, Any] = {"active_role": ROLE_VERIFIER}

    current_turns = _coerce_non_negative_int(state.get("request_agent_turns"))
    next_turns = current_turns + 1
    result["request_agent_turns"] = next_turns

    current_verifier_turns = _coerce_non_negative_int(state.get("verifier_turn_count"))
    result["verifier_turn_count"] = current_verifier_turns + 1

    max_turns = _coerce_non_negative_int(state.get("max_request_agent_turns"), default=-1)
    if max_turns >= 0 and next_turns >= max_turns:
        result["request_stop_reason"] = "agent_turn_budget_exhausted"

    if latest_ai_message is not None:
        verifier_note = _message_content_to_text(latest_ai_message.content).strip()
        if verifier_note:
            result["role_private_memory"] = merge_role_private_memory(
                state.get("role_private_memory"),
                role=ROLE_VERIFIER,
                patch={
                    "last_verifier_action_summary": verifier_note[:1200],
                },
            )

    return result


def _coerce_verification_dict(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        parsed = _coerce_verification_payload_from_text(payload)
        if isinstance(parsed, dict):
            return parsed
    return {}


def _extract_verifier_fix_instructions(verification: dict[str, Any]) -> list[str]:
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


def _replan_budget_remaining(state: AgentState) -> bool:
    current_replans = _coerce_non_negative_int(state.get("plan_replan_count"))
    max_replans = _coerce_non_negative_int(
        state.get("max_plan_replans"),
        default=DEFAULT_MAX_PLAN_REPLANS,
    )
    if max_replans < 0:
        return True
    return current_replans < max_replans


def verifier_agent_node(state: AgentState) -> Dict[str, Any]:
    """
    Build compact structured verifier feedback from latest verification evidence.
    """
    verification_payload = _latest_verification_payload(state)
    verification = _coerce_verification_dict(verification_payload)
    raw_status = verification.get("status")
    normalized_status = raw_status.strip().lower() if isinstance(raw_status, str) else ""
    unfinished_todos = _unfinished_todo_count(state)

    feedback_status = "needs_fix"
    if normalized_status in {"match", "pass", "passed"}:
        feedback_status = "pass"
    elif normalized_status == "catastrophic":
        feedback_status = "catastrophic"

    fix_instructions = _extract_verifier_fix_instructions(verification)
    should_replan = False
    if feedback_status == "needs_fix":
        stall_count = _coerce_non_negative_int(state.get("builder_stall_count"))
        should_replan = _replan_budget_remaining(state) and (stall_count >= 2 or len(fix_instructions) == 0)

    ready_to_finalize = feedback_status == "pass" and unfinished_todos == 0
    confidence = 0.55
    if feedback_status == "pass":
        confidence = 0.9
    elif feedback_status == "catastrophic":
        confidence = 0.4

    verifier_feedback = {
        "status": feedback_status,
        "source_verification_status": normalized_status or "unknown",
        "ready_to_finalize": ready_to_finalize,
        "should_replan": should_replan,
        "focus_objects": [],
        "fix_instructions": fix_instructions,
        "confidence": confidence,
    }

    if isinstance(verification.get("reason"), str) and verification["reason"].strip():
        verifier_feedback["reason"] = verification["reason"].strip()
    elif fix_instructions:
        verifier_feedback["reason"] = fix_instructions[0]
    else:
        verifier_feedback["reason"] = "No explicit verification guidance was available."

    next_verifier_turns = _coerce_non_negative_int(state.get("verifier_turn_count"))
    role_private_memory = merge_role_private_memory(
        state.get("role_private_memory"),
        role=ROLE_VERIFIER,
        patch={
            "last_feedback_status": verifier_feedback["status"],
            "last_feedback_reason": verifier_feedback["reason"],
            "last_feedback_confidence": verifier_feedback["confidence"],
        },
    )

    return {
        "verifier_feedback": verifier_feedback,
        "verifier_turn_count": next_verifier_turns,
        "active_role": ROLE_VERIFIER,
        "role_private_memory": role_private_memory,
    }


def verifier_feedback_node(state: AgentState) -> Dict[str, Any]:
    """Alias node for readability in graph composition."""
    return verifier_agent_node(state)


def quality_evaluator_node(state: AgentState) -> Dict[str, Any]:
    verification_payload = _latest_verification_payload(state)
    verification = _coerce_verification_dict(verification_payload)
    raw_status = verification.get("status")
    normalized_status = raw_status.strip().lower() if isinstance(raw_status, str) else ""

    status = "skipped"
    reason = "No fresh verification evidence."
    if normalized_status in {"match", "pass", "passed"}:
        status = "match"
        reason = str(verification.get("reason") or "Verification passed.")
    elif normalized_status in {"mismatch", "partial", "needs_fix", "fail", "failed"}:
        status = "mismatch"
        reason = str(verification.get("reason") or "Verification reported mismatches.")
    elif normalized_status == "catastrophic":
        status = "catastrophic"
        reason = str(verification.get("reason") or "Catastrophic scene signal detected.")

    streak = _coerce_non_negative_int(state.get("verification_mismatch_streak"))
    if status in {"mismatch", "catastrophic"}:
        streak += 1
    else:
        streak = 0

    return {
        "quality_eval": {
            "status": status,
            "reason": reason,
            "source_verification_status": normalized_status or "none",
        },
        "verification_mismatch_streak": streak,
    }


def progress_evaluator_node(state: AgentState) -> Dict[str, Any]:
    mode = _coerce_task_mode(state.get("task_mode"))
    unfinished_todos = _unfinished_todo_count(state)
    quality = state.get("quality_eval")
    quality_status = ""
    quality_reason = ""
    if isinstance(quality, dict):
        quality_status = str(quality.get("status", "")).strip().lower()
        quality_reason = str(quality.get("reason", "")).strip()

    should_replan = False
    verifier_feedback = state.get("verifier_feedback")
    if isinstance(verifier_feedback, dict) and bool(verifier_feedback.get("should_replan")):
        should_replan = True
    mismatch_streak = _coerce_non_negative_int(state.get("verification_mismatch_streak"))
    builder_stall_count = _coerce_non_negative_int(state.get("builder_stall_count"))
    if mode == MODE_PLAN and (mismatch_streak >= 2 or builder_stall_count >= 2):
        should_replan = True

    if mode == MODE_CONVERSATION:
        status = "done"
        reason = "conversation_mode_response_ready"
    elif quality_status == "match" and unfinished_todos == 0:
        status = "done"
        reason = "verification_match_and_no_open_todos"
    elif quality_status == "catastrophic" and mode == MODE_PLAN and unfinished_todos == 0:
        status = "blocked"
        reason = quality_reason or "catastrophic_without_open_todo"
    elif unfinished_todos > 0:
        status = "continue"
        reason = "open_todos_remaining"
    elif quality_status in {"mismatch", "catastrophic"}:
        status = "continue"
        reason = quality_reason or f"quality_{quality_status}"
    else:
        status = "done"
        reason = "no_additional_progress_needed"

    return {
        "progress_eval": {
            "status": status,
            "reason": reason,
            "unfinished_todos": unfinished_todos,
            "should_replan": should_replan,
        }
    }


def budget_evaluator_node(state: AgentState) -> Dict[str, Any]:
    stop_reason_raw = state.get("request_stop_reason")
    if isinstance(stop_reason_raw, str) and stop_reason_raw:
        return {
            "budget_eval": {
                "budget_ok": False,
                "stop_reason": stop_reason_raw,
            }
        }

    turns = _coerce_non_negative_int(state.get("request_agent_turns"))
    max_turns = _coerce_non_negative_int(state.get("max_request_agent_turns"), default=-1)
    if max_turns >= 0 and turns >= max_turns:
        return {
            "budget_eval": {
                "budget_ok": False,
                "stop_reason": "agent_turn_budget_exhausted",
            },
            "request_stop_reason": "agent_turn_budget_exhausted",
        }

    tool_batches = _coerce_non_negative_int(state.get("request_tool_batches"))
    max_tool_batches = _coerce_non_negative_int(state.get("max_request_tool_batches"), default=-1)
    if max_tool_batches >= 0 and tool_batches >= max_tool_batches:
        return {
            "budget_eval": {
                "budget_ok": False,
                "stop_reason": "tool_batch_budget_exhausted",
            },
            "request_stop_reason": "tool_batch_budget_exhausted",
        }

    replans = _coerce_non_negative_int(state.get("plan_replan_count"))
    max_replans = _coerce_non_negative_int(state.get("max_plan_replans"), default=-1)
    if max_replans >= 0 and replans > max_replans:
        return {
            "budget_eval": {
                "budget_ok": False,
                "stop_reason": "plan_replan_budget_exhausted",
            },
            "request_stop_reason": "plan_replan_budget_exhausted",
        }

    return {"budget_eval": {"budget_ok": True, "stop_reason": None}}


def transition_resolver_node(state: AgentState) -> Dict[str, Any]:
    """
    Deterministic transition resolver shared by single-agent and dual-agent paths.
    """
    mode = _coerce_task_mode(state.get("task_mode"))
    topology = _coerce_workflow_topology(state.get("workflow_topology"))
    is_dual_plan = mode == MODE_PLAN and topology == TOPOLOGY_DUAL

    budget_eval = state.get("budget_eval")
    if isinstance(budget_eval, dict) and not bool(budget_eval.get("budget_ok", True)):
        reason = str(budget_eval.get("stop_reason") or "budget_exhausted")
        return {
            "transition_next": "checkpoint_finalize",
            "transition_reason": reason,
        }

    progress_eval = state.get("progress_eval")
    progress_status = ""
    should_replan = False
    if isinstance(progress_eval, dict):
        progress_status = str(progress_eval.get("status", "")).strip().lower()
        should_replan = bool(progress_eval.get("should_replan"))

    if progress_status == "done":
        return {
            "transition_next": "checkpoint_finalize",
            "transition_reason": "progress_done",
        }

    quality_eval = state.get("quality_eval")
    quality_status = ""
    if isinstance(quality_eval, dict):
        quality_status = str(quality_eval.get("status", "")).strip().lower()

    # Priority: budget_exhausted > done > catastrophic > replan > continue
    if quality_status == "catastrophic":
        return {
            "transition_next": "builder_agent" if is_dual_plan else "agent",
            "transition_reason": "catastrophic_manual_remediation",
        }

    if is_dual_plan and should_replan and _replan_budget_remaining(state):
        return {
            "transition_next": "planner_refresh",
            "transition_reason": "replan_requested_by_evaluators",
        }

    if progress_status in {"continue", "blocked"}:
        return {
            "transition_next": "builder_agent" if is_dual_plan else "agent",
            "transition_reason": "continue_execution",
        }

    return {
        "transition_next": "checkpoint_finalize",
        "transition_reason": "default_finalize",
    }


def planner_refresh_node(state: AgentState) -> Dict[str, Any]:
    """
    Lightweight plan refresh from verifier feedback.
    """
    current_replans = _coerce_non_negative_int(state.get("plan_replan_count"))
    max_replans = _coerce_non_negative_int(
        state.get("max_plan_replans"),
        default=DEFAULT_MAX_PLAN_REPLANS,
    )
    next_replans = current_replans + 1

    result: Dict[str, Any] = {
        "plan_replan_count": next_replans,
        "active_role": ROLE_BUILDER,
        "builder_stall_count": 0,
    }
    if max_replans >= 0 and next_replans > max_replans:
        result["request_stop_reason"] = "plan_replan_budget_exhausted"
        return result

    feedback = state.get("verifier_feedback")
    reason = ""
    instructions: list[str] = []
    if isinstance(feedback, dict):
        reason_value = feedback.get("reason")
        if isinstance(reason_value, str):
            reason = reason_value.strip()
        raw_instructions = feedback.get("fix_instructions")
        if isinstance(raw_instructions, list):
            for item in raw_instructions:
                if isinstance(item, str):
                    text = " ".join(item.strip().split())
                    if text:
                        instructions.append(text)

    new_todos: list[TodoItem] = []
    for instruction in instructions[:2]:
        new_todos.append(create_todo(description=f"Replan fix: {instruction}", status="pending"))

    if not new_todos:
        fallback_description = reason or "Re-evaluate scene plan and continue fixing unresolved mismatches"
        new_todos.append(create_todo(description=f"Replan: {fallback_description}", status="pending"))

    result["todos"] = new_todos
    result["role_private_memory"] = merge_role_private_memory(
        state.get("role_private_memory"),
        role=ROLE_BUILDER,
        patch={
            "last_replan_reason": reason or "verifier_requested_replan",
            "replan_count": next_replans,
        },
    )
    return result


def finalize_node(
    state: AgentState,
    *,
    finalizer_model: Any | None = None,
) -> Dict[str, Any]:
    """
    Finalize node: mark workflow-level finish metadata before END.
    """
    workflow = _build_workflow_metadata(state)
    summary = _compose_finalize_summary(
        state,
        workflow,
        finalizer_model=finalizer_model,
    )
    return {"workflow": workflow, "messages": [AIMessage(content=summary)]}


def _compose_finalize_summary(
    state: AgentState,
    workflow: dict[str, Any],
    *,
    finalizer_model: Any | None = None,
) -> str:
    generated = _build_finalize_summary_with_model(
        state,
        workflow,
        finalizer_model=finalizer_model,
    )
    if generated:
        return generated
    return _build_finalize_summary(state, workflow)


def _build_workflow_metadata(state: AgentState) -> dict[str, Any]:
    task_mode = _coerce_task_mode(state.get("task_mode"))
    finish_reason = "no_tool_calls"

    stop_reason = state.get("request_stop_reason")
    if isinstance(stop_reason, str) and stop_reason:
        finish_reason = stop_reason

    todo_check = state.get("todo_check")
    if isinstance(todo_check, dict):
        todo_status = todo_check.get("status")
        if todo_status == "completed":
            finish_reason = "todos_completed"
        elif todo_status == "blocked":
            finish_reason = "todo_check_blocked"

    if finish_reason == "no_tool_calls" and task_mode == MODE_CONVERSATION:
        finish_reason = "conversation_completed"

    return {
        "workflow_status": "finished",
        "finish_reason": finish_reason,
        "task_mode": task_mode,
        "task_intent": state.get("task_intent"),
        "workflow_topology": _coerce_workflow_topology(state.get("workflow_topology")),
        "memory_profile": state.get("memory_profile"),
        "request_agent_turns": _coerce_non_negative_int(state.get("request_agent_turns")),
        "request_tool_batches": _coerce_non_negative_int(state.get("request_tool_batches")),
    }


def _build_finalize_summary(state: AgentState, workflow: dict[str, Any]) -> str:
    finish_reason = str(workflow.get("finish_reason", "unknown"))
    todo_counts = _collect_current_todo_counts(state)
    total_todos = todo_counts["total"]
    pending = todo_counts["pending"]
    in_progress = todo_counts["in_progress"]
    completed = todo_counts["completed"]
    failed = todo_counts["failed"]

    lines: list[str] = [
        "Result",
        f"Scene workflow finished ({finish_reason}).",
    ]

    todo_check = state.get("todo_check")
    todo_check_status: str | None = None
    todo_check_reason: str | None = None
    if isinstance(todo_check, dict):
        status = todo_check.get("status")
        reason = todo_check.get("reason")
        if isinstance(status, str) and status:
            todo_check_status = status
        if isinstance(reason, str) and reason:
            todo_check_reason = reason

    lines.extend(
        [
            "",
            "Todo Progress",
            (
                f"Total todos: {total_todos}. "
                f"Completed: {completed}, in progress: {in_progress}, pending: {pending}, failed: {failed}."
            ),
        ]
    )
    if todo_check_status:
        if todo_check_reason:
            lines.append(f"Todo check status: {todo_check_status} ({todo_check_reason}).")
        else:
            lines.append(f"Todo check status: {todo_check_status}.")

    verification_status, verification_reason = _latest_verification_feedback(state)
    lines.extend(["", "Verification Highlights"])
    if verification_status:
        lines.append(f"Latest verification: {verification_status}.")
    if verification_reason:
        lines.append(f"Verification note: {verification_reason}.")
    if not verification_status and not verification_reason:
        lines.append("No verification payload was captured in the final state.")

    next_action = _build_finalize_next_action(state, finish_reason, pending, in_progress)
    lines.extend(["", "Suggested Next Action", next_action])

    return "\n".join(lines)


def _collect_current_todo_counts(state: AgentState) -> dict[str, int]:
    todo_check = state.get("todo_check")
    if isinstance(todo_check, dict):
        pending = todo_check.get("pending_count")
        in_progress = todo_check.get("in_progress_count")
        completed = todo_check.get("completed_count")
        failed = todo_check.get("failed_count")
        if all(
            isinstance(value, int) and value >= 0
            for value in (pending, in_progress, completed, failed)
        ):
            total = pending + in_progress + completed + failed
            return {
                "total": total,
                "pending": pending,
                "in_progress": in_progress,
                "completed": completed,
                "failed": failed,
            }

    todos = _coerce_todos(state.get("todos"))
    latest_by_description = _latest_todos_by_description(todos)
    effective_todos = list(latest_by_description.values()) if latest_by_description else todos
    return {
        "total": len(effective_todos),
        "pending": sum(1 for todo in effective_todos if todo.get("status") == "pending"),
        "in_progress": sum(1 for todo in effective_todos if todo.get("status") == "in_progress"),
        "completed": sum(1 for todo in effective_todos if todo.get("status") == "completed"),
        "failed": sum(1 for todo in effective_todos if todo.get("status") == "failed"),
    }


def _build_finalize_next_action(
    state: AgentState,
    finish_reason: str,
    pending_count: int,
    in_progress_count: int,
) -> str:
    if pending_count == 0 and in_progress_count == 0:
        return "If the result looks correct, export the scene artifacts (render/GLB/BLEND)."

    focus = _active_todo_context(state)
    if focus:
        return (
            "Continue from the next unfinished todo: "
            + focus[0]
            + ". Apply edits, then render and verify again."
        )
    if finish_reason == "todo_check_blocked":
        return "Resolve the highest-impact scene mismatch first, then re-run render + verification."
    return "Continue iterating on unfinished todos, then render and verify before finalizing."


def _build_finalize_summary_with_model(
    state: AgentState,
    workflow: dict[str, Any],
    *,
    finalizer_model: Any | None,
) -> str | None:
    if finalizer_model is None:
        return None

    summary_payload = _build_finalize_summary_context(state, workflow)
    summarize_prompt = (
        "You summarize the final state of a 3D scene-editing workflow.\n"
        "Write concise plain text (no markdown table/code block) using 4 short sections:\n"
        "1) Result\n"
        "2) Todo Progress\n"
        "3) Verification Highlights\n"
        "4) Suggested Next Action\n"
        "Requirements:\n"
        "- Never dump raw dict/JSON.\n"
        "- Never copy machine field names (e.g., status/object_feedback/layout_feedback/todo_assessment).\n"
        "- Convert structured inputs into natural-language summary sentences.\n"
        "- Keep concrete and readable for end users.\n"
        "- Match the user's language inferred from latest_user_request."
    )
    context_json = json.dumps(summary_payload, ensure_ascii=False, default=str)
    summarize_messages = [
        SystemMessage(content=summarize_prompt),
        HumanMessage(content=f"workflow_state:\n{context_json}"),
    ]
    try:
        if hasattr(finalizer_model, "with_config"):
            invoke_model = finalizer_model.with_config(
                tags=["nostream"],
                run_name="finalize_summary_internal",
            )
            response = invoke_model.invoke(summarize_messages)
        else:
            try:
                response = finalizer_model.invoke(
                    summarize_messages,
                    config={"tags": ["nostream"], "run_name": "finalize_summary_internal"},
                )
            except TypeError:
                response = finalizer_model.invoke(summarize_messages)
    except Exception:
        return None

    content_text = _message_content_to_text(getattr(response, "content", response))
    if not isinstance(content_text, str):
        return None
    normalized = re.sub(r"<agent_decision>.*?</agent_decision>", "", content_text, flags=re.DOTALL).strip()
    return normalized or None


def _build_finalize_summary_context(
    state: AgentState,
    workflow: dict[str, Any],
) -> dict[str, Any]:
    todo_check = state.get("todo_check")
    todo_summary: dict[str, Any] = {}
    if isinstance(todo_check, dict):
        for key in (
            "status",
            "reason",
            "pending_count",
            "in_progress_count",
            "completed_count",
            "failed_count",
            "stagnation_count",
            "tool_round_count",
        ):
            todo_summary[key] = todo_check.get(key)

    return {
        "finish_reason": workflow.get("finish_reason"),
        "workflow_status": workflow.get("workflow_status"),
        "task_mode": workflow.get("task_mode"),
        "task_intent": workflow.get("task_intent"),
        "workflow_topology": workflow.get("workflow_topology"),
        "memory_profile": workflow.get("memory_profile"),
        "request_agent_turns": workflow.get("request_agent_turns"),
        "request_tool_batches": workflow.get("request_tool_batches"),
        "builder_turn_count": _coerce_non_negative_int(state.get("builder_turn_count")),
        "verifier_turn_count": _coerce_non_negative_int(state.get("verifier_turn_count")),
        "plan_replan_count": _coerce_non_negative_int(state.get("plan_replan_count")),
        "verifier_feedback": state.get("verifier_feedback") if isinstance(state.get("verifier_feedback"), dict) else {},
        "latest_user_request": _latest_human_message(state),
        "todo_check": todo_summary,
        "active_todos": _active_todo_context(state),
        "latest_verification": _sanitize_verification_payload(_latest_verification_payload(state)),
    }


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


def _latest_verification_payload(state: AgentState) -> dict[str, Any] | str | None:
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
    payload = _latest_verification_payload(state)
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


def _resolve_effective_available_tools(
    state: AgentState,
    available_tool_names: list[str] | None,
) -> list[str] | None:
    if available_tool_names is None:
        return None
    deduped_available: list[str] = []
    seen_available: set[str] = set()
    for name in available_tool_names:
        if not isinstance(name, str) or not name or name in seen_available:
            continue
        seen_available.add(name)
        deduped_available.append(name)

    requested_tools = state.get("enabled_tool_names")
    if requested_tools is None:
        return deduped_available
    if not isinstance(requested_tools, list):
        return deduped_available

    requested_set = {
        tool_name
        for tool_name in requested_tools
        if isinstance(tool_name, str) and tool_name
    }
    return [name for name in deduped_available if name in requested_set]


def _build_available_tools_constraint(available_tool_names: list[str] | None) -> str | None:
    if available_tool_names is None:
        return None
    deduped = sorted(set(available_tool_names))
    tools_csv = ", ".join(deduped)
    return (
        "Runtime tool constraints:\n"
        "- Only call tools listed in CURRENT_AVAILABLE_TOOLS.\n"
        f"- CURRENT_AVAILABLE_TOOLS: [{tools_csv}]\n"
        "- If a needed tool is missing, explain and use available alternatives."
    )


def _extract_tool_call_name(tool_call: Any) -> str | None:
    if isinstance(tool_call, dict):
        name = tool_call.get("name")
        return name if isinstance(name, str) and name else None
    name = getattr(tool_call, "name", None)
    return name if isinstance(name, str) and name else None


def _filter_unavailable_tool_calls(
    response: Any,
    available_tool_names: list[str] | None,
) -> tuple[Any, list[str]]:
    if available_tool_names is None:
        return response, []
    tool_calls = getattr(response, "tool_calls", None)
    if not isinstance(tool_calls, list) or len(tool_calls) == 0:
        return response, []

    allowed_names = set(available_tool_names)
    filtered_calls: list[Any] = []
    dropped_calls: list[str] = []
    for tool_call in tool_calls:
        tool_name = _extract_tool_call_name(tool_call)
        if tool_name and tool_name in allowed_names:
            filtered_calls.append(tool_call)
            continue
        dropped_calls.append(tool_name or "<unknown>")

    if len(filtered_calls) == len(tool_calls):
        return response, []

    response.tool_calls = filtered_calls
    additional_kwargs = getattr(response, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        if filtered_calls:
            additional_kwargs["tool_calls"] = filtered_calls
        else:
            additional_kwargs.pop("tool_calls", None)
    return response, dropped_calls


def update_memory_node(state: AgentState) -> Dict[str, Any]:
    """
    Update memory node: parse tool results and update scene state.

    Extracts scene objects from get_scene_info and injects a single
    VLM-ready visual message (fixed ID) for the latest render.
    Using a fixed ID means add_messages replaces the previous visual
    message rather than appending, keeping context lean.
    """
    last_messages = state["messages"][-10:]

    result: Dict[str, Any] = {}
    latest_tool_batch_names = _collect_latest_tool_batch_names(last_messages)
    if latest_tool_batch_names:
        result["last_tool_batch_names"] = latest_tool_batch_names
        result["tool_round_count"] = _coerce_non_negative_int(state.get("tool_round_count")) + 1
        next_request_batches = _coerce_non_negative_int(state.get("request_tool_batches")) + 1
        result["request_tool_batches"] = next_request_batches
        max_request_batches = _coerce_non_negative_int(state.get("max_request_tool_batches"), default=-1)
        if max_request_batches >= 0 and next_request_batches >= max_request_batches:
            result["request_stop_reason"] = "tool_batch_budget_exhausted"

    for msg in last_messages:
        if isinstance(msg, ToolMessage) and "get_scene_info" in str(msg.name):
            scene_updates = SceneMemory.parse_scene_info(msg.content)
            if scene_updates:
                result["scene_objects"] = scene_updates
                break

    render_message = _find_last_render_message(last_messages)
    if render_message is not None:
        render_path = _extract_render_path(render_message)
        if render_path:
            result["last_render_path"] = render_path
            result["last_render_source"] = _infer_render_source(render_message)

        data_url = _resolve_render_message_to_data_url(render_message)
        if data_url:
            result["messages"] = [
                HumanMessage(
                    id=_RENDER_VISION_MESSAGE_ID,
                    content=[
                        {"type": "text", "text": "Latest render from tool call."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                )
            ]

    return result


def scene_observe_node(state: AgentState) -> Dict[str, Any]:
    """Auto-render 3 scene-level cameras after scene-mutating tool calls.

    This node fires only when the latest tool batch contains a scene-mutating
    tool (import, generate, execute_blender_code, etc.).  For object-level
    camera work the node is a no-op so that the agent's own render flows
    directly to verify.
    """
    latest_tools = state.get("last_tool_batch_names")
    if not isinstance(latest_tools, list):
        return {}

    has_scene_mutation = any(name in SCENE_MUTATING_TOOLS for name in latest_tools)
    if not has_scene_mutation:
        return {}

    thread_id = state.get("thread_id", "default")
    send_blender_command = None

    # In headless deployments, agent graph execution runs in the API process.
    # Use API-side per-thread command routing so scene_observe does not depend
    # on MCP runtime globals from another process.
    try:
        from scene_agent.interfaces.api import send_blender_command_sync

        def _send_blender_command(
            command_type: str,
            params: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            return send_blender_command_sync(command_type, params, thread_id=thread_id)

        send_blender_command = _send_blender_command
    except Exception as exc:
        logger = _get_logger()
        logger.debug(
            "scene_observe_node: API command sender unavailable, "
            "falling back to MCP runtime connection: %s",
            exc,
        )

    if _should_use_viewport_scene_observe(state):
        return _run_viewport_scene_observe(
            state=state,
            thread_id=thread_id,
            send_blender_command=send_blender_command,
        )

    try:
        from mcp_server.tools.multimodal.camera_tools import update_scene_cameras

        try:
            result = update_scene_cameras(
                thread_id=thread_id,
                send_blender_command=send_blender_command,
                use_direct_pose=True,
            )
        except TypeError as exc:
            if "use_direct_pose" not in str(exc):
                raise
            # Backward-compatible fallback for older test doubles.
            result = update_scene_cameras(
                thread_id=thread_id,
                send_blender_command=send_blender_command,
            )
    except Exception as exc:
        logger = _get_logger()
        logger.warning("scene_observe_node: update_scene_cameras failed: %s", exc)
        # Scene mutated but render failed — invalidate stale render path
        return {"last_render_path": None}

    if not result.get("success"):
        # Scene mutated but render failed — invalidate stale render path
        return {"last_render_path": None}

    cameras = result.get("cameras", [])
    image_urls = result.get("image_urls", [])
    scene_bbox = result.get("scene_bbox", {})

    if not image_urls:
        # Scene mutated but render failed — invalidate stale render path
        return {"last_render_path": None}

    content: list[dict] = [
        {
            "type": "text",
            "text": (
                "Auto scene observation — 3-view render after scene mutation (2 diagonal views + top-down bird view). "
                "Review these views to assess overall composition, scale, and layout."
            ),
        },
    ]
    for cam_info in cameras:
        url = cam_info.get("image_url", "")
        if url:
            vlm_ready_url = _payload_to_data_url({"url": url})
            if not vlm_ready_url:
                normalized_url = _normalize_render_reference(url)
                if (
                    isinstance(normalized_url, str)
                    and (
                        normalized_url.startswith("http://")
                        or normalized_url.startswith("https://")
                        or normalized_url.startswith("data:")
                    )
                ):
                    vlm_ready_url = normalized_url
            if not vlm_ready_url:
                continue
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": vlm_ready_url},
                }
            )

    camera_params: dict = {}
    camera_names: list[str] = []
    for cam_info in cameras:
        name = cam_info.get("camera_name", "")
        camera_params[name] = {
            "location": cam_info.get("location"),
            "focal_mm": cam_info.get("focal_mm"),
            "azimuth": cam_info.get("azimuth"),
            "elevation": cam_info.get("elevation"),
        }
        camera_names.append(name)

    first_url = image_urls[0] if image_urls else None

    return {
        "messages": [
            HumanMessage(
                id=_SCENE_OBSERVE_MESSAGE_ID,
                content=content,
            )
        ],
        "last_render_path": first_url,
        "last_render_source": "scene_observe",
        "scene_camera_params": camera_params,
        "persistent_cameras": camera_names,
        "scene_bbox": scene_bbox,
    }


def _should_use_viewport_scene_observe(state: AgentState) -> bool:
    enabled_tool_set = _resolve_enabled_tool_set(state)
    if enabled_tool_set:
        return "get_viewport_screenshot" in enabled_tool_set

    try:
        return get_settings().blender_mode == "local-client"
    except Exception:
        return False


def _run_viewport_scene_observe(
    *,
    state: AgentState,
    thread_id: str,
    send_blender_command,
) -> Dict[str, Any]:
    logger = _get_logger()
    command_sender = send_blender_command
    if command_sender is None:
        try:
            from mcp_server import runtime

            blender = runtime.get_blender_connection(logger)
            command_sender = blender.send_command
        except Exception as exc:
            logger.warning(
                "scene_observe_node: local viewport command sender unavailable: %s",
                exc,
            )
            return {"last_render_path": None}

    temp_path = ""
    screenshot_path = ""
    try:
        fd, temp_path = tempfile.mkstemp(
            prefix="scene_observe_viewport_",
            suffix=".png",
        )
        os.close(fd)
        result = command_sender(
            "get_viewport_screenshot",
            {
                "max_size": 800,
                "filepath": temp_path,
                "format": "png",
            },
        )
        if not isinstance(result, dict):
            logger.warning(
                "scene_observe_node: local viewport capture returned non-dict result: %r",
                result,
            )
            return {"last_render_path": None}
        error = result.get("error")
        if isinstance(error, str) and error.strip():
            logger.warning("scene_observe_node: local viewport capture failed: %s", error)
            return {"last_render_path": None}

        reported_path = result.get("filepath")
        screenshot_path = (
            reported_path.strip()
            if isinstance(reported_path, str) and reported_path.strip()
            else temp_path
        )
        if not os.path.exists(screenshot_path):
            logger.warning(
                "scene_observe_node: local viewport screenshot file missing: %s",
                screenshot_path,
            )
            return {"last_render_path": None}

        from scene_agent.utils.rendering import process_and_save_render

        render_url = process_and_save_render(
            screenshot_path,
            thread_id,
            "SceneObserveViewport",
            logger=logger,
        )
        vlm_ready_url = _payload_to_data_url({"url": render_url})
        if not vlm_ready_url:
            vlm_ready_url = _path_to_data_url(screenshot_path)

        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "Auto scene observation — viewport screenshot after scene mutation (local-client mode). "
                    "Review this image to assess global composition, scale, and layout."
                ),
            }
        ]
        if vlm_ready_url:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": vlm_ready_url},
                }
            )

        return {
            "messages": [
                HumanMessage(
                    id=_SCENE_OBSERVE_MESSAGE_ID,
                    content=content,
                )
            ],
            "last_render_path": render_url,
            "last_render_source": "scene_observe",
        }
    except Exception as exc:
        logger.warning("scene_observe_node: get_viewport_screenshot failed: %s", exc)
        return {"last_render_path": None}
    finally:
        for path in {temp_path, screenshot_path}:
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass


def _get_logger():
    import logging
    return logging.getLogger("scene_agent.nodes")


def checkpoint_gate_node(
    state: AgentState,
    *,
    stage: Literal["loop", "finalize"],
) -> Dict[str, Any]:
    """
    Decide whether todo_check should run at the current checkpoint.

    Strategy:
    - Run only when todos exist.
    - In loop stage, run sparsely (interval or milestone tool batch).
    - In finalize stage, run once as a pre-final guard.
    """
    todos = _coerce_todos(state.get("todos"))
    has_todos = len(todos) > 0
    tool_round_count = _coerce_non_negative_int(state.get("tool_round_count"))
    last_check_round = _coerce_non_negative_int(state.get("last_todo_check_round"), default=-1)
    latest_tool_batch_names = state.get("last_tool_batch_names")
    milestone_hit = _is_milestone_tool_batch(latest_tool_batch_names)

    should_run = False
    reason = "no_todos"

    if has_todos:
        if stage == "finalize":
            should_run = True
            reason = "pre_finalize_guard"
        elif milestone_hit:
            should_run = True
            reason = "milestone_tool_batch"
        elif last_check_round < 0:
            should_run = True
            reason = "first_check"
        elif tool_round_count - last_check_round >= TODO_CHECK_INTERVAL_ROUNDS:
            should_run = True
            reason = "interval_reached"
        else:
            should_run = False
            reason = "interval_not_reached"

    return {
        "todo_check_gate": {
            "stage": stage,
            "should_run": should_run,
            "reason": reason,
            "has_todos": has_todos,
            "tool_round_count": tool_round_count,
            "last_todo_check_round": last_check_round,
        }
    }


def todo_check_node(state: AgentState) -> Dict[str, Any]:
    """
    Check todo progress and detect stagnation.
    """
    gate = state.get("todo_check_gate")
    stage = "loop"
    if isinstance(gate, dict):
        stage_value = gate.get("stage")
        if stage_value in {"loop", "finalize"}:
            stage = stage_value

    todos = _coerce_todos(state.get("todos"))
    tool_round_count = _coerce_non_negative_int(state.get("tool_round_count"))
    current_verified_path = state.get("last_verified_path")
    if not isinstance(current_verified_path, str):
        current_verified_path = None

    if not todos:
        return {
            "todo_check": {
                "status": "not_applicable",
                "reason": "no_todos",
                "stage": stage,
                "pending_count": 0,
                "in_progress_count": 0,
                "completed_count": 0,
                "failed_count": 0,
                "tool_round_count": tool_round_count,
                "stagnation_count": 0,
            },
            "last_todo_check_round": tool_round_count,
            "last_todo_check_verified_path": current_verified_path,
            "last_todo_snapshot": {},
            "stagnation_count": 0,
        }

    latest_by_description = _latest_todos_by_description(todos)
    effective_todos = list(latest_by_description.values())
    pending_count = sum(1 for todo in effective_todos if todo.get("status") == "pending")
    in_progress_count = sum(1 for todo in effective_todos if todo.get("status") == "in_progress")
    completed_count = sum(1 for todo in effective_todos if todo.get("status") == "completed")
    failed_count = sum(1 for todo in effective_todos if todo.get("status") == "failed")

    snapshot = {
        key: str(todo.get("status", "pending"))
        for key, todo in latest_by_description.items()
    }
    previous_snapshot = state.get("last_todo_snapshot")
    previous_verified_path = state.get("last_todo_check_verified_path")
    if not isinstance(previous_verified_path, str):
        previous_verified_path = None
    previous_stagnation = _coerce_non_negative_int(state.get("stagnation_count"))
    stagnation_count = 0

    status = "continue"
    reason = "pending_todos"
    if pending_count == 0 and in_progress_count == 0:
        status = "completed"
        reason = "all_todos_terminal"
    elif isinstance(previous_snapshot, dict) and previous_snapshot == snapshot:
        has_new_visual_evidence = (
            isinstance(current_verified_path, str)
            and current_verified_path
            and current_verified_path != previous_verified_path
        )
        if has_new_visual_evidence:
            stagnation_count = 0
            reason = "pending_todos_with_new_visual_evidence"
        else:
            stagnation_count = previous_stagnation + 1
            if stagnation_count >= TODO_STAGNATION_LIMIT:
                status = "blocked"
                reason = "todo_progress_stagnant"
    else:
        stagnation_count = 0

    return {
        "todo_check": {
            "status": status,
            "reason": reason,
            "stage": stage,
            "pending_count": pending_count,
            "in_progress_count": in_progress_count,
            "completed_count": completed_count,
            "failed_count": failed_count,
            "tool_round_count": tool_round_count,
            "stagnation_count": stagnation_count,
        },
        "last_todo_check_round": tool_round_count,
        "last_todo_check_verified_path": current_verified_path,
        "last_todo_snapshot": snapshot,
        "stagnation_count": stagnation_count,
    }


def blocked_recovery_node(state: AgentState) -> Dict[str, Any]:
    """
    Inject a one-shot internal recovery instruction when finalize-stage todo_check is blocked.
    """
    todo_check = state.get("todo_check")
    if not isinstance(todo_check, dict):
        return {}
    if todo_check.get("status") != "blocked":
        return {}

    stagnation_count = _coerce_non_negative_int(todo_check.get("stagnation_count"))
    recovery_attempt = max(1, stagnation_count - TODO_STAGNATION_LIMIT + 1)

    enabled_tool_names = state.get("enabled_tool_names")
    enabled_tool_set: set[str] = set()
    if isinstance(enabled_tool_names, list):
        enabled_tool_set = {
            name.strip()
            for name in enabled_tool_names
            if isinstance(name, str) and name.strip()
        }

    undo_known_available = not enabled_tool_set or "undo_last_snapshot" in enabled_tool_set
    clear_scene_known_available = not enabled_tool_set or "clear_scene" in enabled_tool_set
    if clear_scene_known_available:
        reset_line = (
            "- Full reset flow: call `clear_scene()`, then call `get_scene_info()` and "
            "`observe_scene_global()` to confirm an empty baseline before rebuilding from the first pending todo."
        )
    else:
        reset_line = (
            "- Full reset flow: call `get_scene_info()`, collect all current object names, then call "
            "`delete_objects(object_names=[...], mode=\"cascade\", strict=False, ignore_missing=True)` "
            "to clear the scene before rebuilding from the first pending todo."
        )

    if undo_known_available and recovery_attempt <= 1:
        recovery_lines = [
            "- First recovery action: call `undo_last_snapshot()` once.",
            "- Validate rollback with `get_scene_info()` and `observe_scene_global()`.",
            "- If undo fails or the scene is still broken, immediately run full reset:",
            reset_line,
        ]
    elif undo_known_available:
        recovery_lines = [
            "- Previous recovery did not restore progress. Skip undo and run full reset now.",
            reset_line,
        ]
    else:
        recovery_lines = [
            "- `undo_last_snapshot` is unavailable. Run full reset now.",
            reset_line,
        ]

    guidance = "\n".join(
        [
            "Recovery mode: todo progress was flagged as blocked in finalize checkpoint.",
            f"Recovery attempt {recovery_attempt}/{TODO_BLOCKED_RECOVERY_ATTEMPTS}.",
            "Do not finalize now. You must call tools in this turn.",
            "- Do NOT use `execute_blender_code` for scene deletion/reset; addon enforces hierarchy-safe deletion via `delete_objects`.",
            *recovery_lines,
            "- After recovery edits, call a render tool so verification receives fresh visual evidence.",
        ]
    )

    return {
        "messages": [
            SystemMessage(
                id=_TODO_BLOCKED_RECOVERY_MESSAGE_ID,
                content=guidance,
            )
        ]
    }


def blocked_recovery_action_node(state: AgentState) -> Dict[str, Any]:
    """
    Dispatch deterministic recovery tool calls to reduce LLM hesitation.
    """
    todo_check = state.get("todo_check")
    if not isinstance(todo_check, dict):
        return {}
    if todo_check.get("status") != "blocked":
        return {}

    stagnation_count = _coerce_non_negative_int(todo_check.get("stagnation_count"))
    recovery_attempt = max(1, stagnation_count - TODO_STAGNATION_LIMIT + 1)

    enabled_tool_names = state.get("enabled_tool_names")
    enabled_tool_set: set[str] = set()
    if isinstance(enabled_tool_names, list):
        enabled_tool_set = {
            name.strip()
            for name in enabled_tool_names
            if isinstance(name, str) and name.strip()
        }

    def _tool_available(name: str) -> bool:
        if not enabled_tool_set:
            return True
        return name in enabled_tool_set

    tool_calls: list[dict[str, Any]] = []
    if recovery_attempt <= 1 and _tool_available("undo_last_snapshot"):
        tool_calls.append(
            {
                "name": "undo_last_snapshot",
                "args": {},
                "id": "recovery-undo-1",
                "type": "tool_call",
            }
        )
    elif _tool_available("clear_scene"):
        tool_calls.append(
            {
                "name": "clear_scene",
                "args": {},
                "id": "recovery-clear-1",
                "type": "tool_call",
            }
        )

    # Always request fresh grounding evidence when available.
    if _tool_available("get_scene_info"):
        tool_calls.append(
            {
                "name": "get_scene_info",
                "args": {},
                "id": "recovery-scene-info-1",
                "type": "tool_call",
            }
        )
    if _tool_available("observe_scene_global"):
        tool_calls.append(
            {
                "name": "observe_scene_global",
                "args": {},
                "id": "recovery-observe-1",
                "type": "tool_call",
            }
        )

    if not tool_calls:
        return {}

    return {
        "messages": [
            AIMessage(
                id=_TODO_BLOCKED_RECOVERY_ACTION_MESSAGE_ID,
                content="",
                tool_calls=tool_calls,
            )
        ]
    }


def _message_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False, default=str)
    return str(content)


def _collect_latest_tool_batch_names(messages: list) -> list[str]:
    names_reversed: list[str] = []
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            if isinstance(msg.name, str) and msg.name:
                names_reversed.append(msg.name)
            continue
        if names_reversed:
            break
    if not names_reversed:
        return []
    names = list(reversed(names_reversed))
    deduped: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        deduped.append(name)
    return deduped


def _align_todo_updates_with_existing(
    existing_todos_raw: Any,
    todo_updates: list[TodoItem],
) -> list[TodoItem]:
    if not todo_updates:
        return []

    existing_todos = _coerce_todos(existing_todos_raw)
    if not existing_todos:
        return todo_updates

    existing_by_description: dict[str, TodoItem] = _latest_todos_by_description(existing_todos)
    aligned: list[TodoItem] = []
    for todo in todo_updates:
        description = str(todo.get("description", ""))
        key = _normalize_todo_description(description)
        existing = existing_by_description.get(key)
        if not existing:
            aligned.append(todo)
            continue

        merged = dict(todo)
        merged["id"] = existing["id"]
        merged["created_at"] = existing["created_at"]
        if merged.get("status") == "completed":
            previous_completed_at = existing.get("completed_at")
            merged["completed_at"] = (
                previous_completed_at
                if isinstance(previous_completed_at, str) and previous_completed_at
                else datetime.now().isoformat()
            )
        else:
            merged["completed_at"] = None
        aligned.append(TodoItem(**merged))
    return aligned


_TODO_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "to",
        "of",
        "for",
        "with",
        "and",
        "on",
        "in",
        "at",
        "by",
        "from",
    }
)


def _normalize_todo_description(description: str) -> str:
    if not isinstance(description, str):
        return ""
    lowered = description.strip().lower()
    if not lowered:
        return ""
    alnum = re.sub(r"[^a-z0-9]+", " ", lowered)
    tokens = [token for token in alnum.split() if token and token not in _TODO_STOPWORDS]
    if not tokens:
        return re.sub(r"\s+", " ", lowered).strip()
    return " ".join(tokens)


def _latest_todos_by_description(todos: list[TodoItem]) -> dict[str, TodoItem]:
    latest: dict[str, TodoItem] = {}
    for todo in todos:
        description = str(todo.get("description", ""))
        key = _normalize_todo_description(description)
        if not key:
            continue
        latest[key] = todo
    return latest


def _coerce_todos(raw: Any) -> list[TodoItem]:
    if not isinstance(raw, list):
        return []
    todos: list[TodoItem] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        description = item.get("description")
        status = item.get("status")
        todo_id = item.get("id")
        created_at = item.get("created_at")
        completed_at = item.get("completed_at")
        if not (
            isinstance(description, str)
            and description
            and isinstance(status, str)
            and isinstance(todo_id, str)
            and todo_id
            and isinstance(created_at, str)
            and created_at
            and (isinstance(completed_at, str) or completed_at is None)
        ):
            continue
        todos.append(
            TodoItem(
                id=todo_id,
                description=description,
                status=status,
                created_at=created_at,
                completed_at=completed_at,
            )
        )
    return todos


def _coerce_non_negative_int(value: Any, *, default: int = 0) -> int:
    if isinstance(value, int) and value >= 0:
        return value
    return default


def _is_milestone_tool_batch(names: Any) -> bool:
    if not isinstance(names, list):
        return False
    for raw_name in names:
        if not isinstance(raw_name, str):
            continue
        name = raw_name.strip()
        if not name:
            continue
        for marker in TODO_MILESTONE_TOOL_MARKERS:
            if marker in name:
                return True
    return False


def _extract_render_path(message: ToolMessage | None) -> str | None:
    if message is None:
        return None
    artifact = getattr(message, "artifact", None)
    if isinstance(artifact, dict):
        structured = artifact.get("structured_content") or artifact.get("structuredContent")
        if isinstance(structured, dict):
            for key in ("filepath", "file_path", "path"):
                value = structured.get(key)
                if isinstance(value, str) and value:
                    return value
    content = message.content
    if isinstance(content, dict):
        for key in ("filepath", "file_path", "path"):
            value = content.get(key)
            if isinstance(value, str) and value:
                return value
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "image":
                url = item.get("url")
                if isinstance(url, str) and url.startswith("file://"):
                    return url.replace("file://", "", 1)
    if isinstance(content, str):
        normalized = _normalize_render_reference(content)
        if normalized and _is_probable_render_reference(normalized):
            return normalized
        return None
    return None


def _find_last_render_message(messages: list) -> ToolMessage | None:
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage) and msg.name:
            name = msg.name
            if (
                "render_from_camera" in name
                or "render_from_objects" in name
                or "camera_observe" in name
                or "camera_act" in name
                or "observe_scene_global" in name
            ):
                return msg
    return None


def _infer_render_source(message: ToolMessage | None) -> str:
    if message is None:
        return "agent_camera"
    name = str(getattr(message, "name", "") or "")
    if "observe_scene_global" in name:
        return "scene_observe"
    return "agent_camera"


def _find_last_ai_message(messages: list) -> AIMessage | None:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            return msg
    return None


def _resolve_render_message_to_data_url(message: ToolMessage) -> str | None:
    """Convert a render tool message's image reference to a VLM-ready data URL.

    Uses _extract_render_path for URL/path extraction (handles all content
    formats including markdown), then converts to data: via _path_to_data_url.
    Legacy base64 image blocks are handled as a fallback.
    """
    render_path = _extract_render_path(message)
    if render_path:
        return _path_to_data_url(render_path)

    # Fallback: legacy base64 image block
    content = message.content
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "image":
                b64 = item.get("base64")
                mime = item.get("mime_type") or item.get("mimeType") or "image/png"
                if isinstance(b64, str) and b64:
                    return f"data:{mime};base64,{b64}"
    return None


def _payload_to_data_url(payload: dict[str, str] | None) -> str | None:
    if not payload:
        return None
    
    base64_data = payload.get("base64")
    if base64_data:
        mime_type = payload.get("mime_type", "image/png")
        return f"data:{mime_type};base64,{base64_data}"
    url = payload.get("url")
    if isinstance(url, str):
        normalized = _normalize_render_reference(url)
        if not normalized:
            return None
        # Data URL - return as-is
        if normalized.startswith("data:"):
            return normalized
        # Resolve /renders references (including absolute local URLs) to local files first.
        resolved = _resolve_renders_url_to_path(normalized)
        if resolved:
            return _path_to_data_url(resolved)
        if normalized.startswith("/renders/"):
            return _path_to_data_url(normalized)
        # HTTP URL - return as-is when it is externally reachable.
        if normalized.startswith("http://") or normalized.startswith("https://"):
            return normalized
        return _path_to_data_url(normalized)
    return None


def _path_to_data_url(path: str) -> str | None:
    normalized = _normalize_render_reference(path)
    if not normalized:
        return None
    resolved_renders_path = _resolve_renders_url_to_path(normalized)
    if resolved_renders_path:
        normalized = resolved_renders_path
    # If it's already a URL/data URL, return as-is
    if normalized.startswith("data:"):
        return normalized
    if normalized.startswith("http://") or normalized.startswith("https://"):
        return normalized
    resolved_path = normalized
    if normalized.startswith("/renders/"):
        resolved_path = _resolve_renders_url_to_path(normalized) or normalized
    if not os.path.exists(resolved_path):
        return None
    mime, _ = mimetypes.guess_type(resolved_path)
    mime = mime or "image/png"
    try:
        with open(resolved_path, "rb") as handle:
            payload = handle.read()
    except OSError:
        return None
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime};base64,{encoded}"



def _extract_markdown_image_url(text: str) -> str | None:
    if not isinstance(text, str) or not text:
        return None
    pattern = r'!\[[^\]]*\]\(([^)]+)\)'
    match = re.search(pattern, text)
    if not match:
        return None
    url = match.group(1).strip()
    return url if url else None


def _normalize_render_reference(raw_value: str | None) -> str | None:
    if not isinstance(raw_value, str):
        return None
    normalized = raw_value.strip()
    if not normalized:
        return None
    markdown_url = _extract_markdown_image_url(normalized)
    if markdown_url:
        normalized = markdown_url
    if normalized.startswith("file://"):
        normalized = normalized.replace("file://", "", 1)
    return normalized


def _is_probable_render_reference(value: str) -> bool:
    if not isinstance(value, str):
        return False
    lowered = value.lower().strip()
    if not lowered:
        return False
    if lowered.startswith(("http://", "https://", "data:image/", "/renders/")):
        return True
    if os.path.exists(value):
        return True
    return lowered.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))


def _resolve_renders_url_to_path(url: str) -> str | None:
    if not isinstance(url, str):
        return None
    normalized = url.strip()
    if not normalized:
        return None
    parsed = urlparse(normalized)
    renders_path = normalized
    if parsed.scheme and parsed.netloc:
        renders_path = parsed.path
    if not renders_path.startswith("/renders/"):
        return None
    filename = unquote(renders_path.replace("/renders/", "", 1).strip("/"))
    if not filename:
        return None
    try:
        from scene_agent.utils.rendering import RENDERS_DIR
    except Exception:
        return None
    candidate = os.path.join(str(RENDERS_DIR), filename)
    return candidate if os.path.exists(candidate) else None


def _latest_human_message(state: AgentState) -> str:
    _skip_ids = {
        _RENDER_VISION_MESSAGE_ID,
        _SCENE_OBSERVE_MESSAGE_ID,
    }
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage) and getattr(msg, "id", None) not in _skip_ids:
            return _message_content_to_text(msg.content)
    return ""


def _active_todo_context(state: AgentState) -> list[str]:
    todos = _coerce_todos(state.get("todos"))
    if not todos:
        return []
    latest = _latest_todos_by_description(todos)
    in_progress: list[str] = []
    pending: list[str] = []
    for todo in latest.values():
        description = str(todo.get("description", "")).strip()
        status = str(todo.get("status", "")).strip()
        if not description:
            continue
        if status == "in_progress":
            in_progress.append(description)
        elif status == "pending":
            pending.append(description)
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
        objective = item.get("objective")
        status = item.get("status")
        reason = item.get("reason")
        if not isinstance(objective, str) or not objective.strip():
            continue
        normalized_status = _normalize_verification_todo_status(status)
        if normalized_status is None:
            continue
        assessments.append(
            {
                "objective": objective.strip(),
                "status": normalized_status,
                "reason": reason.strip() if isinstance(reason, str) else "",
            }
        )
    return assessments


def _build_todo_updates_from_verification(
    state: AgentState,
    verification: dict[str, Any],
) -> tuple[list[TodoItem], list[dict[str, str]]]:
    todos = _coerce_todos(state.get("todos"))
    if not todos:
        return [], []

    latest_todos = _latest_todos_by_description(todos)
    if not latest_todos:
        return [], []

    assessments = _extract_verification_todo_assessments(verification)
    if not assessments:
        return [], []

    updates_by_id: dict[str, TodoItem] = {}
    update_records: list[dict[str, str]] = []
    now_iso = datetime.now().isoformat()

    for assessment in assessments:
        if assessment["status"] != "completed":
            continue
        matched = _match_todo_by_objective(latest_todos, assessment["objective"])
        if not matched:
            continue
        if matched.get("status") == "completed":
            continue

        updated = dict(matched)
        updated["status"] = "completed"
        updated["completed_at"] = now_iso
        todo_item = TodoItem(**updated)
        updates_by_id[todo_item["id"]] = todo_item
        update_records.append(
            {
                "objective": assessment["objective"],
                "matched_todo": str(matched.get("description", "")),
                "status": "completed",
                "reason": assessment.get("reason", ""),
            }
        )

    return list(updates_by_id.values()), update_records


def _build_verification_scene_context(state: AgentState) -> dict[str, Any] | None:
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


def _detect_catastrophic_scene_state(
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


def verify_node(
    state: AgentState,
    *,
    provider_name: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> Dict[str, Any]:
    """Verify the latest render against references and current todo focus.

    As a fixed sequential node (scene_observe -> verify -> checkpoint_loop),
    this skips silently when there is no new unverified render.
    """
    render_path = state.get("last_render_path")
    if not render_path:
        return {"verify_forced_recovery": False}

    # Already verified this exact render — skip
    if state.get("last_verified_path") == render_path:
        return {"verify_forced_recovery": False}

    render_source = state.get("last_render_source", "agent_camera")
    scene_context = _build_verification_scene_context(state)

    # Phase 1: catastrophic scene-state gate (hard recovery before todo/consistency checks).
    catastrophic_report = _detect_catastrophic_scene_state(
        state,
        render_reference=render_path,
    )
    if catastrophic_report.get("is_catastrophic"):
        verification = {
            "status": "catastrophic",
            "reason": (
                "Catastrophic scene-state signal detected. "
                "Automatic hard-recovery tool injection is disabled; use verifier-guided remediation."
            ),
            "render_path": render_path,
            "render_source": render_source,
            "catastrophic_signals": catastrophic_report.get("signals", []),
            "catastrophic_metrics": catastrophic_report.get("metrics", {}),
            "hard_recovery": {
                "forced": False,
                "action": "disabled",
                "tool_calls": [],
            },
        }

        guidance_text = _build_verification_guidance_message(state, verification)
        if guidance_text:
            verification["guidance"] = guidance_text

        verification_tool_call_id = (
            "verification_"
            + hashlib.sha1(str(render_path).encode("utf-8")).hexdigest()[:12]
        )
        result: Dict[str, Any] = {
            "messages": [
                ToolMessage(
                    name="verification",
                    content=verification,
                    tool_call_id=verification_tool_call_id,
                )
            ],
            "last_verified_path": render_path,
            "verify_forced_recovery": False,
            "catastrophic_recovery_attempts": 0,
        }
        return result

    # Phase 2: normal verification against active todos and user request.
    # Verify should follow active todo objectives in both scene-level and
    # object-level paths whenever todos exist.
    todo_context = _active_todo_context(state)

    reference_images = _resolve_verification_assets(state)
    reference_paths = [image.stored_path for image in reference_images if isinstance(image.stored_path, str)]

    try:
        verification = verify_render_with_references(
            render_path=render_path,
            reference_paths=reference_paths,
            user_request=_latest_human_message(state),
            render_source=render_source,
            todo_context=todo_context,
            scene_context=scene_context,
            provider_name=provider_name,
            api_key=api_key,
            model=model,
        )
    except Exception as exc:
        verification = {
            "status": "mismatch",
            "reason": f"Verification skipped due to render access error: {exc}",
        }
    verification.update(
        {
            "reference_count": len(reference_paths),
            "reference_ids": [image.id for image in reference_images],
            "render_path": render_path,
            "render_source": render_source,
            "todo_context": todo_context,
        }
    )
    verification_tool_call_id = (
        "verification_"
        + hashlib.sha1(str(render_path).encode("utf-8")).hexdigest()[:12]
    )
    guidance_text = _build_verification_guidance_message(state, verification)
    if guidance_text:
        verification["guidance"] = guidance_text

    todo_updates, todo_update_records = _build_todo_updates_from_verification(
        state,
        verification,
    )
    if todo_update_records:
        verification["todo_status_updates"] = todo_update_records

    result: Dict[str, Any] = {
        "messages": [
            ToolMessage(
                name="verification",
                content=verification,
                tool_call_id=verification_tool_call_id,
            )
        ],
        "last_verified_path": render_path,
        "verify_forced_recovery": False,
        "catastrophic_recovery_attempts": 0,
    }
    if todo_updates:
        result["todos"] = todo_updates

    return result


def _build_verification_guidance_message(
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

    for line in _active_todo_context(state):
        if line not in focus_candidates:
            focus_candidates.append(line)
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


def extract_todo_updates(messages: list) -> list[TodoItem]:
    """
    Extract todo items from messages that contain <todos> tags.
    
    Args:
        messages: List of recent messages
        
    Returns:
        List of TodoItem objects parsed from messages
    """
    todos = []
    
    for msg in messages:
        if isinstance(msg, AIMessage):
            content = msg.content
            if not isinstance(content, str):
                if isinstance(content, list):
                    parts = []
                    for item in content:
                        if isinstance(item, dict):
                            if "text" in item and isinstance(item["text"], str):
                                parts.append(item["text"])
                            elif "content" in item and isinstance(item["content"], str):
                                parts.append(item["content"])
                        elif isinstance(item, str):
                            parts.append(item)
                    content = "\n".join(parts) if parts else json.dumps(content, ensure_ascii=False)
                else:
                    content = str(content)
            
            # Look for <todos> blocks in the message
            todo_pattern = r'<todos>(.*?)</todos>'
            matches = re.findall(todo_pattern, content, re.DOTALL)
            
            for match in matches:
                # Parse each line in the todos block
                lines = match.strip().split('\n')
                for line in lines:
                    line = line.strip()
                    if not line or line.startswith('-'):
                        # Parse format: - [status] description
                        status_match = re.match(r'-?\s*\[(.*?)\]\s*(.*)', line)
                        if status_match:
                            status = status_match.group(1).strip()
                            description = status_match.group(2).strip()
                            
                            # Map status variations
                            status_map = {
                                'pending': 'pending',
                                'in_progress': 'in_progress',
                                'in progress': 'in_progress',
                                'completed': 'completed',
                                'done': 'completed',
                                'failed': 'failed',
                                'error': 'failed'
                            }
                            
                            status = status_map.get(status.lower(), 'pending')
                            
                            if description:
                                todo = create_todo(description, status)
                                todos.append(todo)
    
    return todos
