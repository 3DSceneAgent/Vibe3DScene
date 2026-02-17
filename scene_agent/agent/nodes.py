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
import tempfile
import time
from datetime import datetime
from typing import Any, Dict, Literal
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
)

SCENE_MUTATING_TOOLS: frozenset[str] = frozenset({
    "execute_blender_code",
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

_INTERNAL_HUMAN_MESSAGE_SOURCE_KEY = "internal_source"
_INTERNAL_HUMAN_MESSAGE_SOURCES: frozenset[str] = frozenset({
    "scene_observe",
    "tool_render_observe",
})

_LEGACY_INTERNAL_HUMAN_PREFIXES: tuple[str, ...] = (
    "auto scene observation",
    "latest render from tool call",
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
    # Build messages including system prompt
    from scene_agent.agent.prompts import get_full_system_prompt
    
    messages = [SystemMessage(content=get_full_system_prompt())]
    effective_tool_names = _resolve_effective_available_tools(state, available_tool_names)
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


def finalize_node(state: AgentState) -> Dict[str, Any]:
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
            return {"agent_decision": normalized}
        if todo_status == "blocked":
            normalized["finish_reason"] = "todo_check_blocked"
            return {"agent_decision": normalized}

    should_call_tools = normalized.get("should_call_tools")
    if isinstance(should_call_tools, bool):
        normalized["finish_reason"] = (
            "tool_calls_exhausted"
            if not should_call_tools
            else "model_requested_tools_but_none_emitted"
        )
    else:
        normalized["finish_reason"] = "no_tool_calls"

    return {"agent_decision": normalized}


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
    Update memory node: Parse tool results and update scene state.
    Extracts scene_objects/render artifacts from tool results.
    
    Args:
        state: Current agent state
        
    Returns:
        Partial state update with scene_objects and render metadata
    """
    last_messages = state["messages"][-10:]  # Look at recent messages
    
    result: Dict[str, Any] = {}
    latest_tool_batch_names = _collect_latest_tool_batch_names(last_messages)
    if latest_tool_batch_names:
        result["last_tool_batch_names"] = latest_tool_batch_names
        current_tool_round = state.get("tool_round_count")
        if isinstance(current_tool_round, int) and current_tool_round >= 0:
            result["tool_round_count"] = current_tool_round + 1
        else:
            result["tool_round_count"] = 1
    
    # Parse scene_objects from get_scene_info results
    for msg in last_messages:
        if isinstance(msg, ToolMessage):
            if "get_scene_info" in str(msg.name):
                scene_updates = SceneMemory.parse_scene_info(msg.content)
                if scene_updates:
                    result["scene_objects"] = scene_updates
                    break
    
    render_message = _find_last_render_message(last_messages)
    last_render_path = _extract_render_path(render_message) if render_message else None
    render_image = _extract_render_image_payload(render_message) if render_message else None
    if not last_render_path and render_image:
        last_render_path = _persist_render_image(render_image)
    if last_render_path:
        result["last_render_path"] = last_render_path
        result["last_render_source"] = "agent_camera"

    render_message_update = _build_render_vlm_message(
        last_render_path,
        render_image,
        state.get("last_render_signature"),
    )
    if render_message_update:
        message, signature = render_message_update
        result["messages"] = [message]
        result["last_render_signature"] = signature
    
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
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": url},
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
        "messages": [_build_internal_human_message(content, source="scene_observe")],
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
    previous_stagnation = _coerce_non_negative_int(state.get("stagnation_count"))
    stagnation_count = 0

    status = "continue"
    reason = "pending_todos"
    if pending_count == 0 and in_progress_count == 0:
        status = "completed"
        reason = "all_todos_terminal"
    elif isinstance(previous_snapshot, dict) and previous_snapshot == snapshot:
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
        return content if content else None
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
            ):
                return msg
    return None


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


def _extract_render_image_payload(message: ToolMessage | None) -> dict[str, str] | None:
    if message is None:
        return None
    content = message.content
    
    # First check if content is a string with markdown image
    if isinstance(content, str):
        # Extract URL from markdown: ![alt](url)
        import re
        pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
        match = re.search(pattern, content)
        if match:
            url = match.group(2)
            result = {"url": url, "mime_type": "image/jpeg"}
            return result
    
    # Then check list format (legacy)
    if isinstance(content, list):
        # First check for markdown strings in list
        for item in content:
            if isinstance(item, str):
                import re
                pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
                match = re.search(pattern, item)
                if match:
                    url = match.group(2)
                    return {"url": url, "mime_type": "image/jpeg"}
            if isinstance(item, dict):
                # Text content dict may contain markdown
                text_value = item.get("text")
                if isinstance(text_value, str):
                    import re
                    pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
                    match = re.search(pattern, text_value)
                    if match:
                        url = match.group(2)
                        return {"url": url, "mime_type": "image/jpeg"}
                # Legacy: check for image objects
                if item.get("type") == "image":
                    base64_data = item.get("base64")
                    mime_type = item.get("mime_type") or item.get("mimeType")
                    url = item.get("url")
                    if isinstance(base64_data, str) and base64_data:
                        return {"base64": base64_data, "mime_type": mime_type or "image/png"}
                    if isinstance(url, str) and url:
                        return {"url": url, "mime_type": mime_type or "image/png"}
    return None


def _persist_render_image(payload: dict[str, str]) -> str | None:
    base64_data = payload.get("base64")
    if not base64_data:
        url = payload.get("url")
        if isinstance(url, str):
            # If it's a file:// URL, convert to local path
            if url.startswith("file://"):
                return url.replace("file://", "", 1)
            # If it's an http/https URL, return as-is (already hosted)
            if url.startswith("http://") or url.startswith("https://"):
                return url
        return None
    mime_type = payload.get("mime_type", "image/png")
    extension = mimetypes.guess_extension(mime_type) or ".png"
    filename = f"agent_render_{int(time.time() * 1000)}{extension}"
    path = os.path.join(tempfile.gettempdir(), filename)
    try:
        with open(path, "wb") as handle:
            handle.write(base64.b64decode(base64_data))
    except Exception:
        return None
    return path


def _build_render_vlm_message(
    render_path: str | None,
    payload: dict[str, str] | None,
    last_signature: str | None,
) -> tuple[HumanMessage, str] | None:
    data_url = _payload_to_data_url(payload)
    if not data_url and render_path:
        data_url = _path_to_data_url(render_path)
    if not data_url:
        return None
    signature = _render_signature(render_path, payload)
    if not signature or signature == last_signature:
        return None
    content = [
        {"type": "text", "text": "Latest render from tool call."},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]
    return _build_internal_human_message(content, source="tool_render_observe"), signature


def _build_internal_human_message(content: Any, *, source: str) -> HumanMessage:
    return HumanMessage(
        content=content,
        additional_kwargs={_INTERNAL_HUMAN_MESSAGE_SOURCE_KEY: source},
    )


def _is_internal_human_message(msg: HumanMessage) -> bool:
    additional_kwargs = getattr(msg, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        source = additional_kwargs.get(_INTERNAL_HUMAN_MESSAGE_SOURCE_KEY)
        if isinstance(source, str) and source in _INTERNAL_HUMAN_MESSAGE_SOURCES:
            return True

    # Backward compatibility for messages produced before internal_source tagging.
    text = _message_content_to_text(msg.content).strip().lower()
    return any(text.startswith(prefix) for prefix in _LEGACY_INTERNAL_HUMAN_PREFIXES)


def _payload_to_data_url(payload: dict[str, str] | None) -> str | None:
    if not payload:
        return None
    
    base64_data = payload.get("base64")
    if base64_data:
        mime_type = payload.get("mime_type", "image/png")
        return f"data:{mime_type};base64,{base64_data}"
    url = payload.get("url")
    if isinstance(url, str):
        # Data URL - return as-is
        if url.startswith("data:"):
            return url
        # HTTP URL - return as-is (OpenAI API supports direct URLs)
        if url.startswith("http://") or url.startswith("https://"):
            return url
        # File URL - convert to data URL
        if url.startswith("file://"):
            return _path_to_data_url(url.replace("file://", "", 1))
    return None


def _path_to_data_url(path: str) -> str | None:
    # If it's already an HTTP URL, return as-is
    if path.startswith("http://") or path.startswith("https://"):
        return path
    # If it's a local file, convert to data URL
    mime, _ = mimetypes.guess_type(path)
    mime = mime or "image/png"
    try:
        with open(path, "rb") as handle:
            payload = handle.read()
    except OSError:
        return None
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _render_signature(render_path: str | None, payload: dict[str, str] | None) -> str | None:
    if render_path:
        return f"path:{render_path}"
    if payload and payload.get("base64"):
        digest = hashlib.sha256(payload["base64"].encode("utf-8")).hexdigest()
        return f"b64:{digest}"
    if payload and payload.get("url"):
        return f"url:{payload['url']}"
    return None


def _latest_human_message(state: AgentState) -> str:
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage) and not _is_internal_human_message(msg):
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
    verification.update(
        {
            "reference_count": len(reference_paths),
            "reference_ids": [image.id for image in reference_images],
            "render_path": render_path,
            "render_source": render_source,
            "todo_context": todo_context,
        }
    )
    return {
        "messages": [ToolMessage(name="verification", content=verification)],
        "last_verified_path": render_path,
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
