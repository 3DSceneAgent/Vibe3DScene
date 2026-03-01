from __future__ import annotations

import base64
import json
import mimetypes
import os
from typing import Any
from urllib.parse import unquote, urlparse

from langchain_core.messages import HumanMessage

from scene_agent.config import get_settings
from scene_agent.vlm import get_vlm_provider


def _resolve_local_renders_path(reference: str) -> str | None:
    normalized = reference.strip()
    if not normalized:
        return None
    parsed = urlparse(normalized)
    render_path = parsed.path if parsed.scheme and parsed.netloc else normalized
    if not render_path.startswith("/renders/"):
        return None
    filename = unquote(render_path.replace("/renders/", "", 1).strip("/"))
    if not filename:
        return None
    from scene_agent.utils.rendering import RENDERS_DIR

    candidate = os.path.join(str(RENDERS_DIR), filename)
    return candidate if os.path.exists(candidate) else None


def _image_to_data_url(path: str) -> str:
    normalized = path.strip()
    if normalized.startswith("data:"):
        return normalized
    if normalized.startswith("file://"):
        normalized = normalized.replace("file://", "", 1)
    resolved_local_renders_path = _resolve_local_renders_path(normalized)
    if resolved_local_renders_path:
        normalized = resolved_local_renders_path
    elif normalized.startswith("http://") or normalized.startswith("https://"):
        return normalized
    mime, _ = mimetypes.guess_type(normalized)
    mime = mime or "image/png"
    with open(normalized, "rb") as handle:
        payload = handle.read()
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            else:
                parts.append(json.dumps(item, ensure_ascii=False, default=str))
        return "\n".join(parts)
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False, default=str)
    return str(content)


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    snippet = text[start : end + 1]
    try:
        return json.loads(snippet)
    except json.JSONDecodeError:
        return None


def _format_todo_context(todo_context: list[dict[str, str]] | None) -> str:
    if not todo_context:
        return ""
    normalized_items: list[dict[str, str]] = []
    for item in todo_context[:5]:
        if not isinstance(item, dict):
            continue
        todo_id = " ".join(str(item.get("todo_id", "")).strip().split())
        title = " ".join(str(item.get("title", "")).strip().split())
        status = " ".join(str(item.get("status", "")).strip().split())
        if not todo_id or not title:
            continue
        normalized_items.append(
            {
                "todo_id": todo_id,
                "title": title,
                "status": status or "unknown",
            }
        )
    if not normalized_items:
        return ""
    lines = ["Current active todos (primary verification target):"]
    for item in normalized_items:
        lines.append(f"- {item['todo_id']} [{item['status']}] {item['title']}")
    return "\n".join(lines)


def _format_scene_context(scene_context: dict[str, Any] | None) -> str:
    if not scene_context or not isinstance(scene_context, dict):
        return ""
    try:
        serialized = json.dumps(scene_context, ensure_ascii=False, default=str)
    except Exception:
        return ""
    if len(serialized) > 12000:
        serialized = f"{serialized[:12000]}...[truncated]"
    return (
        "Structured scene context from get_scene_info / observation tools "
        "(supplemental to the render):\n"
        f"{serialized}"
    )


_SCENE_LEVEL_VERIFY_PROMPT = """\
Analyze the rendered 3D scene image(s) against the target description (and reference images if provided).
Report on each category:
1) Objects: Are all requested objects present? Any missing, extraneous, or incorrect objects?
2) Layout: Does the spatial arrangement match the description? Suggest concrete transforms if not.
3) Scale: Are proportions realistic? Is any object too large or too small relative to others?
4) Environment: Is the lighting, background, and material/texture treatment correct?
5) Overall: match | partial | mismatch.

Return JSON only:
{
  "status": "match|partial|mismatch",
  "object_feedback": "...",
  "layout_feedback": "...",
  "scale_feedback": "...",
  "environment_feedback": "...",
  "edit_suggestions": ["concrete actionable fix 1", "concrete actionable fix 2"],
  "reason": "short summary"
}"""

_OBJECT_LEVEL_VERIFY_PROMPT = """\
Analyze the rendered image of specific object(s) against the target description (and reference images if provided).
Focus on fine details since this is a close-up / object-level view:
1) Geometry: Is the object shape correct? Any visible mesh artifacts, holes, or distortion?
2) Placement: Is the object at the correct position? Any clipping with other objects or floating?
3) Material: Are textures, colors, and material properties correct? Any UV issues?
4) Scale: Is the object sized correctly relative to nearby objects and real-world expectations?
5) Overall: match | partial | mismatch.

Return JSON only:
{
  "status": "match|partial|mismatch",
  "object_feedback": "...",
  "placement_feedback": "...",
  "material_feedback": "...",
  "scale_feedback": "...",
  "edit_suggestions": ["concrete actionable fix 1", "concrete actionable fix 2"],
  "reason": "short summary"
}"""


def verify_render_with_references(
    *,
    render_path: str,
    reference_paths: list[str],
    user_request: str,
    render_source: str = "agent_camera",
    todo_context: list[dict[str, str]] | None = None,
    scene_context: dict[str, Any] | None = None,
    provider_name: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    selected_provider = (provider_name or settings.vlm_provider).lower()
    selected_model = model or settings.get_vlm_default_model(selected_provider)
    selected_api_key = api_key or settings.get_vlm_api_key(selected_provider)
    if not selected_api_key:
        raise ValueError(
            f"No API key configured for provider '{selected_provider}'. "
            "Set provider-specific API key or VLM_API_KEY."
        )
    provider = get_vlm_provider(
        provider_name=selected_provider,
        api_key=selected_api_key,
        model=selected_model,
    )
    model = provider.get_chat_model()

    has_references = bool(reference_paths)

    if render_source == "scene_observe":
        base_prompt = _SCENE_LEVEL_VERIFY_PROMPT
    else:
        base_prompt = _OBJECT_LEVEL_VERIFY_PROMPT

    todo_text = _format_todo_context(todo_context)
    has_todo_focus = bool(todo_text)
    scene_context_text = _format_scene_context(scene_context)
    has_scene_context = bool(scene_context_text)

    if has_references:
        prompt = (
            base_prompt
            + "\n\nReference images are provided — also check style/appearance consistency."
        )
    else:
        prompt = (
            base_prompt
            + "\n\nNo reference images provided — judge solely based on the text description."
        )

    if has_todo_focus:
        prompt += (
            "\n\nIf current todo objectives are provided, treat them as the primary verification target. "
            "Use the full user request only as background context."
            "\nYou MUST include a `todo_assessment` JSON array with exactly one item per provided todo."
            "\nEach item format: {\"todo_id\": \"...\", \"status\": \"done|not_done|uncertain\", \"reason\": \"...\"}."
            "\nOnly mark status as `done` when there is clear visual evidence in the current render."
        )

    if has_scene_context:
        prompt += (
            "\n\nStructured scene context is provided below (objects/cameras/bounds from Blender tools). "
            "Use it to improve object/layout/scale checks; if it conflicts with direct visual evidence, "
            "prioritize what is visible in the render."
        )

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    if has_todo_focus:
        content.append({"type": "text", "text": todo_text})
        if user_request.strip():
            content.append(
                {
                    "type": "text",
                    "text": f"Original full user request (background context only): {user_request}",
                }
            )
    else:
        content.append({"type": "text", "text": f"User request: {user_request}"})

    if has_scene_context:
        content.append({"type": "text", "text": scene_context_text})

    render_data_url = _image_to_data_url(render_path)
    content.append(
        {
            "type": "image_url",
            "image_url": {"url": render_data_url},
        }
    )
    for path in reference_paths:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": _image_to_data_url(path)},
            }
        )

    # Prevent internal verification-model tokens from leaking into the
    # outer agent stream (`stream_mode=["messages"]`).
    invoke_model = model
    if hasattr(model, "with_config"):
        try:
            invoke_model = model.with_config(
                tags=["nostream"],
                run_name="verification_internal",
            )
        except Exception:
            invoke_model = model
    response = invoke_model.invoke([HumanMessage(content=content)])
    response_text = _content_to_text(getattr(response, "content", response))
    parsed = _extract_json(response_text)
    if not isinstance(parsed, dict) or "status" not in parsed:
        return {
            "status": "mismatch",
            "reason": response_text.strip() or "Verification response could not be parsed.",
        }
    parsed["verification_mode"] = "reference_comparison" if has_references else "text_only"
    parsed["render_source"] = render_source
    return parsed
