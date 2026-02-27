"""Node implementations by category."""
import json
import re
from typing import Any, Dict, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from scene_agent.agent.state import AgentState, TodoItem, create_todo
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
    latest_todos_by_description,
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
    Decide whether todo_check should run at the current checkpoint.

    Strategy:
    - Run only when todos exist.
    - In loop stage, run sparsely (interval or milestone tool batch).
    - In finalize stage, run once as a pre-final guard.
    """
    todos = coerce_todos(state.get("todos"))
    has_todos = len(todos) > 0
    tool_round_count = coerce_non_negative_int(state.get("tool_round_count"))
    last_check_round = coerce_non_negative_int(state.get("last_todo_check_round"), default=-1)
    latest_tool_batch_names = state.get("last_tool_batch_names")
    milestone_hit = is_milestone_tool_batch(latest_tool_batch_names)

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

    todos = coerce_todos(state.get("todos"))
    tool_round_count = coerce_non_negative_int(state.get("tool_round_count"))
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

    latest_by_description = latest_todos_by_description(todos)
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
    previous_stagnation = coerce_non_negative_int(state.get("stagnation_count"))
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

    stagnation_count = coerce_non_negative_int(todo_check.get("stagnation_count"))
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
                id=TODO_BLOCKED_RECOVERY_MESSAGE_ID,
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

    stagnation_count = coerce_non_negative_int(todo_check.get("stagnation_count"))
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
