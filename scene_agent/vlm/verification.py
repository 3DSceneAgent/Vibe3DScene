from __future__ import annotations

import base64
import json
import mimetypes
import os
from typing import Any
from urllib.parse import unquote, urlparse

from langchain_core.messages import HumanMessage, SystemMessage

from scene_agent.config import get_settings
from scene_agent.utils.logging import log_event
from scene_agent.verification_result import (
    VerificationResult,
    normalize_verification_payload,
)
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
        parts: list[str] = []
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
        parsed = json.loads(snippet)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _format_objective_context(
    *,
    user_request: str,
    todo_context: list[dict[str, str]] | None,
) -> str:
    if isinstance(todo_context, list):
        for todo in todo_context:
            if not isinstance(todo, dict):
                continue
            todo_title = str(todo.get("title", "")).strip()
            todo_status = str(todo.get("status", "")).strip()
            todo_id = str(todo.get("todo_id", "")).strip()
            if todo_title:
                return (
                    "Active objective (primary): "
                    f"[{todo_id or 'todo'}|{todo_status or 'unknown'}] {todo_title}\n"
                    f"Full request context: {user_request}"
                )
    return f"Objective: {user_request}"


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
            f"Set {selected_provider.upper()}_API_KEY. "
            "VLM_API_KEY is no longer used."
        )

    provider = get_vlm_provider(
        provider_name=selected_provider,
        api_key=selected_api_key,
        model=selected_model,
    )
    chat_model = provider.get_chat_model()
    invoke_model = chat_model
    if hasattr(chat_model, "with_config"):
        try:
            invoke_model = chat_model.with_config(
                tags=["nostream"],
                run_name="verification_internal",
            )
        except Exception:
            invoke_model = chat_model

    objective_text = _format_objective_context(
        user_request=user_request,
        todo_context=todo_context,
    )
    has_references = bool(reference_paths)
    scene_context_text = ""
    if isinstance(scene_context, dict) and scene_context:
        try:
            scene_context_text = json.dumps(scene_context, ensure_ascii=False, default=str)
        except Exception:
            scene_context_text = ""
    if len(scene_context_text) > 12000:
        scene_context_text = scene_context_text[:12000] + "...[truncated]"

    prompt = (
        "You verify whether a 3D render satisfies the current objective.\n"
        "Output only structured fields.\n"
        "Judgment rules:\n"
        "- status=done only when the objective is visually satisfied.\n"
        "- status=working when objective is partially met, unmet, or uncertain.\n"
        "- Keep reason concise and concrete.\n"
        "- Provide 0-4 actionable edit suggestions when status=working."
    )

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    content.append({"type": "text", "text": objective_text})
    content.append(
        {
            "type": "text",
            "text": (
                f"render_source={render_source}; reference_images_present={has_references}; "
                f"reference_count={len(reference_paths)}"
            ),
        }
    )
    if scene_context_text:
        content.append(
            {
                "type": "text",
                "text": f"scene_context:\n{scene_context_text}",
            }
        )

    content.append(
        {
            "type": "image_url",
            "image_url": {"url": _image_to_data_url(render_path)},
        }
    )
    for path in reference_paths:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": _image_to_data_url(path)},
            }
        )

    messages = [
        SystemMessage(content="Structured visual verification for 3D scene workflow."),
        HumanMessage(content=content),
    ]

    # Primary path: provider-supported structured output.
    structured_output_error: Exception | None = None
    try:
        structured_model = invoke_model.with_structured_output(VerificationResult)
        structured_raw = structured_model.invoke(messages)
        structured = (
            structured_raw
            if isinstance(structured_raw, VerificationResult)
            else VerificationResult.model_validate(structured_raw)
        )
        normalized = normalize_verification_payload(structured.model_dump(mode="json"))
        normalized["verification_mode"] = "reference_comparison" if has_references else "text_only"
        normalized["render_source"] = render_source
        normalized["structured_output_fallback"] = False
        return normalized
    except Exception as exc:
        structured_output_error = exc

    log_event(
        "warning",
        "verification_structured_output_fallback",
        {
            "provider": selected_provider,
            "model": selected_model,
            "render_source": render_source,
            "has_references": has_references,
            "error_type": type(structured_output_error).__name__ if structured_output_error else "unknown",
            "error": str(structured_output_error or ""),
        },
    )

    # Fallback path: raw output + JSON extraction.
    response = invoke_model.invoke(messages)
    response_text = _content_to_text(getattr(response, "content", response))
    parsed = _extract_json(response_text) or {}
    normalized = normalize_verification_payload(
        parsed,
        fallback_reason=response_text.strip() or "Verification response could not be parsed.",
    )
    normalized["verification_mode"] = "reference_comparison" if has_references else "text_only"
    normalized["render_source"] = render_source
    normalized["structured_output_fallback"] = True
    return normalized
