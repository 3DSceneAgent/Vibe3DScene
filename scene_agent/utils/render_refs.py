from __future__ import annotations

import base64
import mimetypes
import os
import re
from typing import Any
from urllib.parse import unquote, urlparse

from langchain_core.messages import ToolMessage


def extract_markdown_image_url(text: str) -> str | None:
    if not isinstance(text, str) or not text:
        return None
    pattern = r"!\[[^\]]*\]\(([^)]+)\)"
    match = re.search(pattern, text)
    if not match:
        return None
    url = match.group(1).strip()
    return url if url else None


def normalize_render_reference(raw_value: str | None) -> str | None:
    if not isinstance(raw_value, str):
        return None
    normalized = raw_value.strip()
    if not normalized:
        return None
    markdown_url = extract_markdown_image_url(normalized)
    if markdown_url:
        normalized = markdown_url
    if normalized.startswith("file://"):
        normalized = normalized.replace("file://", "", 1)
    return normalized


def is_probable_render_reference(value: str) -> bool:
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


def resolve_renders_url_to_path(url: str) -> str | None:
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


def path_to_data_url(path: str) -> str | None:
    normalized = normalize_render_reference(path)
    if not normalized:
        return None
    resolved_renders_path = resolve_renders_url_to_path(normalized)
    if resolved_renders_path:
        normalized = resolved_renders_path
    # If it's already a URL/data URL, return as-is
    if normalized.startswith("data:"):
        return normalized
    if normalized.startswith("http://") or normalized.startswith("https://"):
        return normalized
    resolved_path = normalized
    if normalized.startswith("/renders/"):
        resolved_path = resolve_renders_url_to_path(normalized) or normalized
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


def payload_to_data_url(payload: dict[str, str] | None) -> str | None:
    if not payload:
        return None

    base64_data = payload.get("base64")
    if base64_data:
        mime_type = payload.get("mime_type", "image/png")
        return f"data:{mime_type};base64,{base64_data}"
    url = payload.get("url")
    if isinstance(url, str):
        normalized = normalize_render_reference(url)
        if not normalized:
            return None
        # Data URL - return as-is
        if normalized.startswith("data:"):
            return normalized
        # Resolve /renders references (including absolute local URLs) to local files first.
        resolved = resolve_renders_url_to_path(normalized)
        if resolved:
            return path_to_data_url(resolved)
        if normalized.startswith("/renders/"):
            return path_to_data_url(normalized)
        # HTTP URL - return as-is when it is externally reachable.
        if normalized.startswith("http://") or normalized.startswith("https://"):
            return normalized
        return path_to_data_url(normalized)
    return None


def extract_render_path(message: ToolMessage | None) -> str | None:
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
        normalized = normalize_render_reference(content)
        if normalized and is_probable_render_reference(normalized):
            return normalized
        return None
    return None


def find_last_render_message(messages: list[Any]) -> ToolMessage | None:
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


def infer_render_source(message: ToolMessage | None) -> str:
    if message is None:
        return "agent_camera"
    name = str(getattr(message, "name", "") or "")
    if "observe_scene_global" in name:
        return "scene_observe"
    return "agent_camera"


def resolve_render_message_to_data_url(message: ToolMessage) -> str | None:
    """Convert a render tool message's image reference to a VLM-ready data URL."""
    render_path = extract_render_path(message)
    if render_path:
        return path_to_data_url(render_path)

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
