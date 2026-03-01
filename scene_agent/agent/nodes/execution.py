"""Node implementations by category."""
import json
import re
from typing import Any, Dict, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from scene_agent.agent.state import AgentState, TodoItem, create_todo
from scene_agent.agent.todo_protocol import TODO_UPDATE_TOOL_NAME
from scene_agent.agent.todo_state import apply_todo_actions, project_latest_todos
from scene_agent.memory.scene_memory import SceneMemory

from .shared import (
    RENDER_VISION_MESSAGE_ID,
    SCENE_MUTATING_TOOLS,
    SCENE_OBSERVE_MESSAGE_ID,
    TODO_BLOCKED_RECOVERY_ACTION_MESSAGE_ID,
    TODO_BLOCKED_RECOVERY_ATTEMPTS,
    TODO_CHECK_INTERVAL_ROUNDS,
    TODO_STAGNATION_LIMIT,
    TODO_BLOCKED_RECOVERY_MESSAGE_ID,
)
from .shared import (
    coerce_non_negative_int,
    coerce_todos,
    collect_latest_tool_batch_names,
    extract_render_path,
    find_last_render_message,
    get_logger,
    infer_render_source,
    is_milestone_tool_batch,
    normalize_render_reference,
    payload_to_data_url,
    resolve_render_message_to_data_url,
    run_viewport_scene_observe,
    should_use_viewport_scene_observe,
)


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
    latest_tool_batch_names = collect_latest_tool_batch_names(last_messages)
    if latest_tool_batch_names:
        result["last_tool_batch_names"] = latest_tool_batch_names
        result["tool_round_count"] = coerce_non_negative_int(state.get("tool_round_count")) + 1
        next_request_batches = coerce_non_negative_int(state.get("request_tool_batches")) + 1
        result["request_tool_batches"] = next_request_batches
        max_request_batches = coerce_non_negative_int(state.get("max_request_tool_batches"), default=-1)
        if max_request_batches >= 0 and next_request_batches >= max_request_batches:
            result["request_stop_reason"] = "tool_batch_budget_exhausted"

    for msg in last_messages:
        if isinstance(msg, ToolMessage) and "get_scene_info" in str(msg.name):
            scene_updates = SceneMemory.parse_scene_info(msg.content)
            if scene_updates:
                result["scene_objects"] = scene_updates
                break

    render_message = find_last_render_message(last_messages)
    if render_message is not None:
        render_path = extract_render_path(render_message)
        if render_path:
            result["last_render_path"] = render_path
            result["last_render_source"] = infer_render_source(render_message)

        data_url = resolve_render_message_to_data_url(render_message)
        if data_url:
            result["messages"] = [
                HumanMessage(
                    id=RENDER_VISION_MESSAGE_ID,
                    content=[
                        {"type": "text", "text": "Latest render from tool call."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                )
            ]

    return result


def todo_commit_node(state: AgentState) -> Dict[str, Any]:
    """
    Apply internal todo updates without entering the Blender tool pipeline.
    """
    pending_updates = state.get("pending_todo_updates")
    if not isinstance(pending_updates, list) or not pending_updates:
        return {
            "pending_todo_updates": [],
            "todo_protocol_version": 1,
        }

    role_value = state.get("active_role")
    role = role_value if isinstance(role_value, str) and role_value else "general"
    current_versions = state.get("todo_versions")
    current_todos = state.get("todos")
    active_todo_value = state.get("active_todo_id")
    active_todo_id = active_todo_value if isinstance(active_todo_value, str) and active_todo_value else None

    messages: list[ToolMessage] = []
    for entry in pending_updates:
        if not isinstance(entry, dict):
            continue
        request = entry.get("request")
        tool_call_id = entry.get("tool_call_id")
        if not isinstance(tool_call_id, str) or not tool_call_id:
            tool_call_id = "todo_update_commit"
        actions = request.get("actions") if isinstance(request, dict) else None
        if not isinstance(actions, list):
            messages.append(
                ToolMessage(
                    name=TODO_UPDATE_TOOL_NAME,
                    content="todo_update request was missing actions.",
                    tool_call_id=tool_call_id,
                    status="error",
                )
            )
            continue
        try:
            current_versions, current_todos, active_todo_id = apply_todo_actions(
                current_versions,
                actions,
                fallback_todos_raw=current_todos,
                source="agent_commit",
                role=role,
                previous_active_todo_id=active_todo_id,
            )
            messages.append(
                ToolMessage(
                    name=TODO_UPDATE_TOOL_NAME,
                    content={
                        "applied": True,
                        "updated_todo_count": len(actions),
                        "active_todo_id": active_todo_id,
                    },
                    tool_call_id=tool_call_id,
                )
            )
        except Exception as exc:
            messages.append(
                ToolMessage(
                    name=TODO_UPDATE_TOOL_NAME,
                    content=f"todo_update failed: {exc}",
                    tool_call_id=tool_call_id,
                    status="error",
                )
            )

    result: Dict[str, Any] = {
        "pending_todo_updates": [],
        "todo_protocol_version": 1,
        "todo_versions": current_versions if isinstance(current_versions, list) else [],
        "todos": (
            current_todos
            if isinstance(current_todos, list)
            else project_latest_todos(current_versions)
        ),
        "active_todo_id": active_todo_id,
    }
    if messages:
        result["messages"] = messages
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
        logger = get_logger()
        logger.debug(
            "scene_observe_node: API command sender unavailable, "
            "falling back to MCP runtime connection: %s",
            exc,
        )

    if should_use_viewport_scene_observe(state):
        return run_viewport_scene_observe(
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
        logger = get_logger()
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
            vlm_ready_url = payload_to_data_url({"url": url})
            if not vlm_ready_url:
                normalized_url = normalize_render_reference(url)
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
                id=SCENE_OBSERVE_MESSAGE_ID,
                content=content,
            )
        ],
        "last_render_path": first_url,
        "last_render_source": "scene_observe",
        "scene_camera_params": camera_params,
        "persistent_cameras": camera_names,
        "scene_bbox": scene_bbox,
    }

def checkpoint_gate_node(
    state: AgentState,
    *,
    stage: Literal["loop", "finalize"],
) -> Dict[str, Any]:
    """
    Decide whether a checkpoint should keep iterating or allow finalization.

    Strategy:
    - In loop stage, retain the legacy sparse-check metadata only.
    - In loop stage, run sparsely (interval or milestone tool batch).
    - In finalize stage, run the finalize guard inline and emit the latest
      finalize-guard snapshot directly, without a separate graph node.
    """
    todos = coerce_todos(state.get("todos"))
    has_todos = len(todos) > 0
    tool_round_count = coerce_non_negative_int(state.get("tool_round_count"))
    last_check_round = coerce_non_negative_int(state.get("last_finalize_guard_round"), default=-1)
    latest_tool_batch_names = state.get("last_tool_batch_names")
    milestone_hit = is_milestone_tool_batch(latest_tool_batch_names)
    stop_reason = state.get("request_stop_reason")
    if not isinstance(stop_reason, str):
        stop_reason = ""
    transition_reason = state.get("transition_reason")
    if not isinstance(transition_reason, str):
        transition_reason = ""
    convergence_eval = state.get("convergence_eval")
    convergence_status = ""
    if isinstance(convergence_eval, dict):
        convergence_status = str(convergence_eval.get("status", "")).strip().lower()

    should_run = False
    reason = "no_todos"

    if stage == "finalize":
        pending_count = sum(1 for todo in todos if todo.get("status") == "pending")
        in_progress_count = sum(1 for todo in todos if todo.get("status") == "in_progress")
        completed_count = sum(1 for todo in todos if todo.get("status") == "completed")
        failed_count = sum(1 for todo in todos if todo.get("status") == "failed")
        snapshot = {
            str(todo.get("id", "")): str(todo.get("status", "pending"))
            for todo in todos
            if isinstance(todo.get("id"), str) and str(todo.get("id"))
        }
        current_verified_path = state.get("last_verified_path")
        if not isinstance(current_verified_path, str):
            current_verified_path = None

        todo_status = "not_applicable"
        if stop_reason:
            reason = "terminal_stop_skip_guard"
            todo_status = "skipped"
        elif convergence_status == "hard_stop":
            reason = "convergence_hard_stop_skip_guard"
            todo_status = "skipped"
        elif transition_reason and transition_reason not in {"progress_done", "default_finalize"}:
            reason = "non_success_finalize_skip_guard"
            todo_status = "skipped"
        elif not has_todos:
            reason = "no_todos"
            todo_status = "not_applicable"
        elif pending_count == 0 and in_progress_count == 0:
            reason = "all_todos_terminal"
            todo_status = "completed"
        else:
            reason = "unfinished_todos_remaining"
            todo_status = "continue"

        result: Dict[str, Any] = {
            "finalize_guard_gate": {
                "stage": stage,
                "should_run": False,
                "reason": reason,
                "has_todos": has_todos,
                "tool_round_count": tool_round_count,
                "last_finalize_guard_round": last_check_round,
            },
            "finalize_guard": {
                "status": todo_status,
                "reason": reason,
                "stage": stage,
                "pending_count": pending_count,
                "in_progress_count": in_progress_count,
                "completed_count": completed_count,
                "failed_count": failed_count,
                "tool_round_count": tool_round_count,
                "stagnation_count": 0,
            },
            "last_finalize_guard_round": tool_round_count,
            "last_finalize_guard_verified_path": current_verified_path,
            "last_finalize_guard_todo_snapshot": snapshot,
            "stagnation_count": 0,
        }
        return result

    if has_todos:
        if milestone_hit:
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
        "finalize_guard_gate": {
            "stage": stage,
            "should_run": should_run,
            "reason": reason,
            "has_todos": has_todos,
            "tool_round_count": tool_round_count,
            "last_finalize_guard_round": last_check_round,
        }
    }

def finalize_guard_node(state: AgentState) -> Dict[str, Any]:
    """
    Thin finalize-stage guard snapshot helper.

    This node is no longer responsible for stagnation detection. Loop
    stability now belongs to budget + convergence; finalize_guard only guards
    against premature finalization when unfinished todos still exist.
    """
    gate = state.get("finalize_guard_gate")
    stage = "loop"
    if isinstance(gate, dict):
        stage_value = gate.get("stage")
        if stage_value in {"loop", "finalize"}:
            stage = stage_value

    todos = coerce_todos(state.get("todos"))
    tool_round_count = coerce_non_negative_int(state.get("tool_round_count"))
    current_verified_path = state.get("last_verified_path")
    if not isinstance(current_verified_path, str):
        current_verified_path = None

    if not todos:
        return {
            "finalize_guard": {
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
            "last_finalize_guard_round": tool_round_count,
            "last_finalize_guard_verified_path": current_verified_path,
            "last_finalize_guard_todo_snapshot": {},
            "stagnation_count": 0,
        }

    effective_todos = list(todos)
    pending_count = sum(1 for todo in effective_todos if todo.get("status") == "pending")
    in_progress_count = sum(1 for todo in effective_todos if todo.get("status") == "in_progress")
    completed_count = sum(1 for todo in effective_todos if todo.get("status") == "completed")
    failed_count = sum(1 for todo in effective_todos if todo.get("status") == "failed")

    snapshot = {
        str(todo.get("id", "")): str(todo.get("status", "pending"))
        for todo in effective_todos
        if isinstance(todo.get("id"), str) and str(todo.get("id"))
    }
    convergence_eval = state.get("convergence_eval")
    convergence_status = ""
    convergence_reason = ""
    if isinstance(convergence_eval, dict):
        convergence_status = str(convergence_eval.get("status", "")).strip().lower()
        convergence_reason = str(convergence_eval.get("reason", "")).strip()

    status = "continue"
    reason = "unfinished_todos_remaining"
    stagnation_count = 0
    if pending_count == 0 and in_progress_count == 0:
        status = "completed"
        reason = "all_todos_terminal"
    elif convergence_status == "hard_stop":
        status = "blocked"
        reason = convergence_reason or "convergence_guard_triggered"

    return {
        "finalize_guard": {
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
        "last_finalize_guard_round": tool_round_count,
        "last_finalize_guard_verified_path": current_verified_path,
        "last_finalize_guard_todo_snapshot": snapshot,
        "stagnation_count": stagnation_count,
    }

def blocked_recovery_node(state: AgentState) -> Dict[str, Any]:
    """
    Inject a one-shot internal recovery instruction when the finalize guard is blocked.
    """
    finalize_guard = state.get("finalize_guard")
    if not isinstance(finalize_guard, dict):
        return {}
    if finalize_guard.get("status") != "blocked":
        return {}

    stagnation_count = coerce_non_negative_int(finalize_guard.get("stagnation_count"))
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
            "Recovery mode: the finalize guard flagged the run as blocked.",
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
                id=TODO_BLOCKED_RECOVERY_MESSAGE_ID,
                content=guidance,
            )
        ]
    }

def blocked_recovery_action_node(state: AgentState) -> Dict[str, Any]:
    """
    Dispatch deterministic recovery tool calls to reduce LLM hesitation.
    """
    finalize_guard = state.get("finalize_guard")
    if not isinstance(finalize_guard, dict):
        return {}
    if finalize_guard.get("status") != "blocked":
        return {}

    stagnation_count = coerce_non_negative_int(finalize_guard.get("stagnation_count"))
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
                id=TODO_BLOCKED_RECOVERY_ACTION_MESSAGE_ID,
                content="",
                tool_calls=tool_calls,
            )
        ]
    }

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
