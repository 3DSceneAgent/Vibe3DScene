"""Node implementations by category."""
import hashlib
from typing import Any, Dict
from langchain_core.messages import ToolMessage
from scene_agent.agent.state import AgentState
from scene_agent.agent.todo_state import apply_todo_actions
from scene_agent.config import get_settings
from scene_agent.memory.reference_image_memory import get_reference_image_memory
from scene_agent.vlm.verification import verify_render_with_references
from scene_agent.utils.verification_helpers import (
    active_todo_context,
    build_todo_updates_from_verification,
    build_verification_guidance_message,
    build_verification_scene_context,
    detect_catastrophic_scene_state,
)

from .shared import (
    latest_human_message,
    resolve_verification_assets,
)


def verify_node(
    state: AgentState,
    *,
    provider_name: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> Dict[str, Any]:
    """Verify the latest render against references and current todo focus.

    As a fixed sequential node (scene_observe -> verify -> checkpoint_loop),
    this skips silently when there is no new unverified render.
    """
    render_path = state.get("last_render_path")
    if not render_path:
        return {"verify_forced_recovery": False}

    # Already verified this exact render — skip
    if state.get("last_verified_path") == render_path:
        return {"verify_forced_recovery": False}

    render_source = state.get("last_render_source", "agent_camera")
    scene_context = build_verification_scene_context(state)

    # Phase 1: catastrophic scene-state gate (hard recovery before todo/consistency checks).
    catastrophic_report = detect_catastrophic_scene_state(
        state,
        render_reference=render_path,
    )
    if catastrophic_report.get("is_catastrophic"):
        verification = {
            "status": "catastrophic",
            "reason": (
                "Catastrophic scene-state signal detected. "
                "Automatic hard-recovery tool injection is disabled; use verifier-guided remediation."
            ),
            "render_path": render_path,
            "render_source": render_source,
            "catastrophic_signals": catastrophic_report.get("signals", []),
            "catastrophic_metrics": catastrophic_report.get("metrics", {}),
            "hard_recovery": {
                "forced": False,
                "action": "disabled",
                "tool_calls": [],
            },
        }

        guidance_text = build_verification_guidance_message(state, verification)
        if guidance_text:
            verification["guidance"] = guidance_text

        verification_tool_call_id = (
            "verification_"
            + hashlib.sha1(str(render_path).encode("utf-8")).hexdigest()[:12]
        )
        result: Dict[str, Any] = {
            "messages": [
                ToolMessage(
                    name="verification",
                    content=verification,
                    tool_call_id=verification_tool_call_id,
                )
            ],
            "last_verified_path": render_path,
            "verify_forced_recovery": False,
            "catastrophic_recovery_attempts": 0,
        }
        return result

    # Phase 2: normal verification against active todos and user request.
    # Verify should follow active todo objectives in both scene-level and
    # object-level paths whenever todos exist.
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
        verification = verify_render_with_references(
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
        verification = {
            "status": "error",
            "reason": f"Verification failed due to render/VLM error: {exc}",
        }
    verification.update(
        {
            "reference_count": len(reference_paths),
            "reference_ids": reference_ids,
            "render_path": render_path,
            "render_source": render_source,
            "todo_context": todo_context,
        }
    )
    verification_tool_call_id = (
        "verification_"
        + hashlib.sha1(str(render_path).encode("utf-8")).hexdigest()[:12]
    )
    guidance_text = build_verification_guidance_message(state, verification)
    if guidance_text:
        verification["guidance"] = guidance_text

    todo_actions, todo_update_records = build_todo_updates_from_verification(
        state,
        verification,
    )
    if todo_update_records:
        verification["todo_status_updates"] = todo_update_records

    result: Dict[str, Any] = {
        "messages": [
            ToolMessage(
                name="verification",
                content=verification,
                tool_call_id=verification_tool_call_id,
            )
        ],
        "last_verified_path": render_path,
        "verify_forced_recovery": False,
        "catastrophic_recovery_attempts": 0,
    }
    if todo_actions:
        active_todo_value = state.get("active_todo_id")
        active_todo_id = active_todo_value if isinstance(active_todo_value, str) and active_todo_value else None
        todo_versions, todos, next_active_todo_id = apply_todo_actions(
            state.get("todo_versions"),
            todo_actions,
            fallback_todos_raw=state.get("todos"),
            source="verification",
            role="system",
            render_path=render_path,
            previous_active_todo_id=active_todo_id,
        )
        result["todo_versions"] = todo_versions
        result["todos"] = todos
        result["active_todo_id"] = next_active_todo_id

    return result


__all__ = [
    "verify_node",
    "verify_render_with_references",
    "get_reference_image_memory",
    "get_settings",
]
