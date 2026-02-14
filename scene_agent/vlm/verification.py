from __future__ import annotations

import base64
import json
import mimetypes
from typing import Any

from langchain_core.messages import HumanMessage

from scene_agent.config import get_settings
from scene_agent.vlm import get_vlm_provider


def _image_to_data_url(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    mime = mime or "image/png"
    with open(path, "rb") as handle:
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


def verify_render_with_references(
    *,
    render_path: str,
    reference_paths: list[str],
    user_request: str,
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

    prompt = (
        "You are verifying whether the rendered image matches the user request "
        "and any provided reference images. Respond with JSON only using:\n"
        '{"status":"match|mismatch","reason":"short explanation"}'
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    content.append({"type": "text", "text": f"User request: {user_request}"})
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

    response = model.invoke([HumanMessage(content=content)])
    response_text = _content_to_text(getattr(response, "content", response))
    parsed = _extract_json(response_text)
    if not isinstance(parsed, dict) or "status" not in parsed:
        return {
            "status": "mismatch",
            "reason": response_text.strip() or "Verification response could not be parsed.",
        }
    return parsed
