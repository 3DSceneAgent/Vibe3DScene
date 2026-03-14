"""Verification node: structured objective judgment from render evidence."""

from __future__ import annotations

import hashlib
from typing import Any

from langchain_core.messages import ToolMessage

from scene_agent.agent.state import AgentState
from scene_agent.memory.reference_image_memory import get_reference_image_memory
from scene_agent.verification_result import normalize_verification_payload
from scene_agent.vlm.verification import verify_render_with_references
from scene_agent.utils.verification_helpers import (
    active_todo_context,
    build_verification_guidance_message,
    build_verification_scene_context,
)

from .shared import latest_human_message, resolve_verification_assets


def verify_node(
    state: AgentState,
    *,
    provider_name: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Verify latest render and emit canonical `verification_result` contract."""
    if state.get("fast_mode") is True:
        return {"verification_result": None}

    render_path = state.get("last_render_path")
    if not render_path:
        return {"verification_result": None}

    # Already verified this exact render in current run; skip.
    if state.get("last_verified_path") == render_path:
        return {"verification_result": None}

    render_source = state.get("last_render_source", "agent_camera")
    scene_context = build_verification_scene_context(state)
    todo_context = active_todo_context(state)
    reference_images = resolve_verification_assets(state)
    reference_paths = [
        image["stored_path"]
        for image in reference_images
        if isinstance(image, dict)
        and isinstance(image.get("stored_path"), str)
        and image.get("stored_path")
    ]
    reference_ids = [
        image["asset_id"]
        for image in reference_images
        if isinstance(image, dict)
        and isinstance(image.get("asset_id"), str)
        and image.get("asset_id")
    ]

    try:
        verification_payload = verify_render_with_references(
            render_path=render_path,
            reference_paths=reference_paths,
            user_request=latest_human_message(state),
            render_source=render_source,
            todo_context=todo_context,
            scene_context=scene_context,
            provider_name=provider_name,
            api_key=api_key,
            model=model,
        )
    except Exception as exc:
        verification_payload = {
            "status": "working",
            "reason": f"Verification failed due to render/VLM error: {exc}",
            "edit_suggestions": [],
        }

    verification_result = normalize_verification_payload(verification_payload)
    verification_payload.update(
        {
            "status": verification_result["status"],
            "reason": verification_result["reason"],
            "edit_suggestions": verification_result["edit_suggestions"],
            "reference_count": len(reference_paths),
            "reference_ids": reference_ids,
            "render_path": render_path,
            "render_source": render_source,
            "todo_context": todo_context,
        }
    )
    guidance_text = build_verification_guidance_message(state, verification_payload)
    if guidance_text:
        verification_payload["guidance"] = guidance_text

    verification_tool_call_id = (
        "verification_" + hashlib.sha1(str(render_path).encode("utf-8")).hexdigest()[:12]
    )
    return {
        "messages": [
            ToolMessage(
                name="verification",
                content=verification_payload,
                tool_call_id=verification_tool_call_id,
            )
        ],
        "last_verified_path": render_path,
        "verification_result": verification_result,
    }


__all__ = [
    "verify_node",
    "verify_render_with_references",
    "get_reference_image_memory",
]
