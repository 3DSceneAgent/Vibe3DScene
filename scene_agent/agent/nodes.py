"""
LangGraph node implementations.
Nodes follow best practices: return partial state updates only.
"""
import base64
import hashlib
import json
import mimetypes
import os
import re
from datetime import datetime
from typing import Any, Dict, Literal
from urllib.parse import unquote, urlparse
from langchain_core.messages import ToolMessage, AIMessage, SystemMessage, HumanMessage
from scene_agent.agent.state import AgentState, TodoItem, create_todo
from scene_agent.config import get_settings
from scene_agent.memory.scene_memory import SceneMemory
from scene_agent.memory.reference_image_memory import get_reference_image_memory
from scene_agent.vlm.verification import verify_render_with_references

TODO_CHECK_INTERVAL_ROUNDS = 3
TODO_STAGNATION_LIMIT = 2
TODO_MILESTONE_TOOL_MARKERS = (
    "render_from_camera",
    "render_from_objects",
    "camera_observe",
    "camera_act",
    "observe_scene_global",
)

SCENE_MUTATING_TOOLS: frozenset[str] = frozenset({
    "execute_blender_code",
    "delete_objects",
    "import_glb_model",
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
})

OBJECT_LEVEL_TOOLS: frozenset[str] = frozenset({
    "camera_act",
    "camera_observe",
    "camera_set_pose",
    "render_from_camera",
    "render_from_objects",
})

# Fixed message IDs for internal visual context messages.
# add_messages replaces by ID, so these slots hold at most one message each —
# no unbounded accumulation across turns.
_RENDER_VISION_MESSAGE_ID = "render_vision_current"
_SCENE_OBSERVE_MESSAGE_ID = "scene_observe_current"


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
    # Build messages including system prompt
    from scene_agent.agent.prompts import get_full_system_prompt
    effective_tool_names = _resolve_effective_available_tools(state, available_tool_names)

    messages = [SystemMessage(content=get_full_system_prompt(effective_tool_names))]
    tool_constraints = _build_available_tools_constraint(effective_tool_names)
    if tool_constraints:
        messages.append(SystemMessage(content=tool_constraints))
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
    
    return {"messages": [response]}


def post_agent_node(state: AgentState) -> Dict[str, Any]:
    """
    Post-agent node: persist structured control signals after every assistant turn.

    This node runs after each agent response so that decision/todo state is captured
    even when the response does not include tool calls.
    """
    last_messages = state["messages"][-10:]
    latest_ai_message = _find_last_ai_message(last_messages)
    result: Dict[str, Any] = {}

    if latest_ai_message is not None:
        todo_updates = extract_todo_updates([latest_ai_message])
        aligned_todos = _align_todo_updates_with_existing(state.get("todos"), todo_updates)
        if aligned_todos:
            result["todos"] = aligned_todos
        result["agent_decision"] = _extract_agent_decision([latest_ai_message])
    else:
        result["agent_decision"] = {}

    current_iteration = state.get("iteration_count")
    if isinstance(current_iteration, int) and current_iteration >= 0:
        result["iteration_count"] = current_iteration + 1
    else:
        result["iteration_count"] = 1

    return result


def finalize_node(
    state: AgentState,
    *,
    finalizer_model: Any | None = None,
) -> Dict[str, Any]:
    """
    Finalize node: mark workflow-level finish metadata before END.
    """
    decision = state.get("agent_decision")
    normalized = dict(decision) if isinstance(decision, dict) else {}
    normalized["workflow_status"] = "finished"

    todo_check = state.get("todo_check")
    if isinstance(todo_check, dict):
        todo_status = todo_check.get("status")
        if todo_status == "completed":
            normalized["finish_reason"] = "todos_completed"
            summary = _compose_finalize_summary(
                state,
                normalized,
                finalizer_model=finalizer_model,
            )
            return {"agent_decision": normalized, "messages": [AIMessage(content=summary)]}
        if todo_status == "blocked":
            normalized["finish_reason"] = "todo_check_blocked"
            summary = _compose_finalize_summary(
                state,
                normalized,
                finalizer_model=finalizer_model,
            )
            return {"agent_decision": normalized, "messages": [AIMessage(content=summary)]}

    should_call_tools = normalized.get("should_call_tools")
    if isinstance(should_call_tools, bool):
        normalized["finish_reason"] = (
            "tool_calls_exhausted"
            if not should_call_tools
            else "model_requested_tools_but_none_emitted"
        )
    else:
        normalized["finish_reason"] = "no_tool_calls"

    summary = _compose_finalize_summary(
        state,
        normalized,
        finalizer_model=finalizer_model,
    )
    return {"agent_decision": normalized, "messages": [AIMessage(content=summary)]}


def _compose_finalize_summary(
    state: AgentState,
    decision: dict[str, Any],
    *,
    finalizer_model: Any | None = None,
) -> str:
    fallback = _build_finalize_summary(state, decision)
    generated = _build_finalize_summary_with_model(
        state,
        decision,
        finalizer_model=finalizer_model,
    )
    return generated or fallback


def _build_finalize_summary(state: AgentState, decision: dict[str, Any]) -> str:
    finish_reason = str(decision.get("finish_reason", "unknown"))
    lines: list[str] = [f"Scene workflow finished ({finish_reason})."]

    todo_check = state.get("todo_check")
    if isinstance(todo_check, dict):
        status = todo_check.get("status")
        reason = todo_check.get("reason")
        pending = todo_check.get("pending_count")
        in_progress = todo_check.get("in_progress_count")
        completed = todo_check.get("completed_count")
        failed = todo_check.get("failed_count")
        if isinstance(status, str) and status:
            if isinstance(reason, str) and reason:
                lines.append(f"Todo check: {status} ({reason}).")
            else:
                lines.append(f"Todo check: {status}.")
        counts = [pending, in_progress, completed, failed]
        if all(isinstance(value, int) for value in counts):
            lines.append(
                "Todo summary: "
                f"completed={completed}, in_progress={in_progress}, pending={pending}, failed={failed}."
            )

    verification_status, verification_reason = _latest_verification_feedback(state)
    if verification_status:
        lines.append(f"Latest verification: {verification_status}.")
    if verification_reason:
        lines.append(f"Verification note: {verification_reason}.")

    return "\n".join(lines)


def _build_finalize_summary_with_model(
    state: AgentState,
    decision: dict[str, Any],
    *,
    finalizer_model: Any | None,
) -> str | None:
    if finalizer_model is None:
        return None

    summary_payload = _build_finalize_summary_context(state, decision)
    summarize_prompt = (
        "You summarize the final state of a 3D scene-editing workflow.\n"
        "Write concise plain text (no markdown table/code block) using 4 short sections:\n"
        "1) Result\n"
        "2) Todo Progress\n"
        "3) Verification Highlights\n"
        "4) Suggested Next Action\n"
        "Requirements:\n"
        "- Never dump raw dict/JSON.\n"
        "- Keep concrete and readable for end users.\n"
        "- Match the user's language inferred from latest_user_request."
    )
    context_json = json.dumps(summary_payload, ensure_ascii=False, default=str)
    try:
        response = finalizer_model.invoke(
            [
                SystemMessage(content=summarize_prompt),
                HumanMessage(content=f"workflow_state:\n{context_json}"),
            ]
        )
    except Exception:
        return None

    content_text = _message_content_to_text(getattr(response, "content", response))
    if not isinstance(content_text, str):
        return None
    normalized = re.sub(r"<agent_decision>.*?</agent_decision>", "", content_text, flags=re.DOTALL).strip()
    return normalized or None


def _build_finalize_summary_context(
    state: AgentState,
    decision: dict[str, Any],
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
        "finish_reason": decision.get("finish_reason"),
        "workflow_status": decision.get("workflow_status"),
        "latest_user_request": _latest_human_message(state),
        "todo_check": todo_summary,
        "active_todos": _active_todo_context(state),
        "latest_verification": _sanitize_verification_payload(_latest_verification_payload(state)),
    }


def _sanitize_verification_payload(payload: Any) -> Any:
    if isinstance(payload, str):
        text = payload.strip()
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
        return None, payload
    return None, None


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
    """Auto-render 4 scene-level cameras after scene-mutating tool calls.

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

    try:
        from mcp_server.tools.multimodal.camera_tools import update_scene_cameras

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
                "Auto scene observation — 4-view render after scene mutation. "
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

    tool_round = _coerce_non_negative_int(state.get("tool_round_count"))

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
        "last_scene_observe_round": tool_round,
    }


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


def _normalize_todo_description(description: str) -> str:
    return re.sub(r"\s+", " ", description).strip().lower()


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


def _extract_agent_decision(messages: list) -> dict[str, Any]:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            content = _message_content_to_text(msg.content)
            decision = _extract_tagged_json(content, "agent_decision")
            if isinstance(decision, dict):
                return decision
    return {}


def _find_last_ai_message(messages: list) -> AIMessage | None:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            return msg
    return None


def _extract_tagged_json(text: str, tag: str) -> dict[str, Any] | None:
    if not text:
        return None
    pattern = rf"<{tag}>(.*?)</{tag}>"
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        return None
    snippet = match.group(1).strip()
    if not snippet:
        return None
    try:
        parsed = json.loads(snippet)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None




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


def verify_node(
    state: AgentState,
    *,
    provider_name: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> Dict[str, Any]:
    """Verify the latest render against references / user request.

    As a fixed sequential node (scene_observe -> verify -> checkpoint_loop),
    this skips silently when there is no new unverified render.
    """
    render_path = state.get("last_render_path")
    if not render_path:
        return {}

    # Already verified this exact render — skip
    if state.get("last_verified_path") == render_path:
        return {}

    render_source = state.get("last_render_source", "agent_camera")
    todo_context = _active_todo_context(state) if render_source != "scene_observe" else []

    thread_id = state.get("thread_id", "default")
    memory = get_reference_image_memory()
    reference_images = memory.list_images(thread_id)
    settings = get_settings()
    reference_images = reference_images[-settings.reference_image_max_count :]
    reference_paths = [image.stored_path for image in reference_images]

    try:
        verification = verify_render_with_references(
            render_path=render_path,
            reference_paths=reference_paths,
            user_request=_latest_human_message(state),
            render_source=render_source,
            todo_context=todo_context,
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

    return {
        "messages": [
            ToolMessage(
                name="verification",
                content=verification,
                tool_call_id=verification_tool_call_id,
            )
        ],
        "last_verified_path": render_path,
    }


def _build_verification_guidance_message(
    state: AgentState,
    verification: dict[str, Any],
) -> str:
    status_value = verification.get("status")
    status = status_value.strip().lower() if isinstance(status_value, str) else ""
    if status == "match":
        return "Latest verification is match. Continue with the next pending todo."

    render_source = verification.get("render_source")
    is_scene_level = isinstance(render_source, str) and render_source == "scene_observe"
    focus_candidates: list[str] = []

    decision = state.get("agent_decision")
    if isinstance(decision, dict):
        next_focus = decision.get("next_focus_objects")
        if isinstance(next_focus, list):
            for item in next_focus:
                if isinstance(item, str):
                    cleaned = item.strip()
                    if cleaned:
                        focus_candidates.append(cleaned)

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
