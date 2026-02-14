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
from typing import Any, Dict
from langchain_core.messages import ToolMessage, AIMessage, SystemMessage, HumanMessage
from scene_agent.agent.state import AgentState, TodoItem, create_todo, update_todo_status
from scene_agent.config import get_settings
from scene_agent.memory.scene_memory import SceneMemory
from scene_agent.memory.reference_image_memory import get_reference_image_memory
from scene_agent.vlm.verification import verify_render_with_references


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
    tool_constraints = _build_available_tools_constraint(available_tool_names)
    if tool_constraints:
        messages.append(SystemMessage(content=tool_constraints))
    messages.extend(state["messages"])
    
    # Invoke the LLM
    response = llm_with_tools.invoke(messages)
    response, dropped_tools = _filter_unavailable_tool_calls(response, available_tool_names)
    if dropped_tools:
        content_text = _message_content_to_text(getattr(response, "content", ""))
        if not content_text.strip():
            skipped = ", ".join(sorted(set(dropped_tools)))
            response.content = (
                "I skipped unavailable tool calls and will continue with enabled tools only. "
                f"Skipped: {skipped}."
            )
    
    return {"messages": [response]}


def _build_available_tools_constraint(available_tool_names: list[str] | None) -> str | None:
    if not available_tool_names:
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
    if not available_tool_names:
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
    Extracts scene_objects from get_scene_info results and todos from messages.
    
    Args:
        state: Current agent state
        
    Returns:
        Partial state update with scene_objects and todos
    """
    last_messages = state["messages"][-10:]  # Look at recent messages
    
    result: Dict[str, Any] = {}
    
    # Parse scene_objects from get_scene_info results
    for msg in last_messages:
        if isinstance(msg, ToolMessage):
            if "get_scene_info" in str(msg.name):
                scene_updates = SceneMemory.parse_scene_info(msg.content)
                if scene_updates:
                    result["scene_objects"] = scene_updates
                    break
    
    # Extract todo updates from assistant messages
    todo_updates = extract_todo_updates(last_messages)
    if todo_updates:
        result["todos"] = todo_updates

    result["agent_decision"] = _extract_agent_decision(last_messages)

    render_message = _find_last_render_message(last_messages)
    last_render_path = _extract_render_path(render_message) if render_message else None
    render_image = _extract_render_image_payload(render_message) if render_message else None
    if not last_render_path and render_image:
        last_render_path = _persist_render_image(render_image)
    if last_render_path:
        result["last_render_path"] = last_render_path

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
    return HumanMessage(content=content), signature


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
        if isinstance(msg, HumanMessage):
            return _message_content_to_text(msg.content)
    return ""


def verify_node(
    state: AgentState,
    *,
    provider_name: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> Dict[str, Any]:
    render_path = state.get("last_render_path")
    if not render_path:
        return {}

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
        provider_name=provider_name,
        api_key=api_key,
        model=model,
    )
    verification.update(
        {
            "reference_count": len(reference_paths),
            "reference_ids": [image.id for image in reference_images],
            "render_path": render_path,
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
