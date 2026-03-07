# ruff: noqa: F401
"""
LangGraph node implementations.
Nodes follow best practices: return partial state updates only.
"""
import copy
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from typing import Any, Dict, Literal
from uuid import uuid4
from langchain_core.messages import ToolMessage, AIMessage, SystemMessage, HumanMessage
from pydantic import BaseModel, Field
from scene_agent.agent.convergence import CONVERGENCE_GUIDANCE_MESSAGE_ID
from scene_agent.agent.context_manager import build_projected_context
from scene_agent.agent.state import AgentState, ReferenceImageCatalogEntry, TaskMode, TodoItem
from scene_agent.agent.todo_protocol import TODO_UPDATE_TOOL_NAME
from scene_agent.agent.todo_state import project_latest_todos
from scene_agent.agent.tool_policy import (
    coerce_request_tool_budgets,
    resolve_effective_tool_names,
)
from .constants_router import (
    _ACTION_INTENT_MARKERS,
    _IMAGE_QA_MARKERS,
    _PLAN_INTENT_MARKERS,
)
from .constants_runtime import (
    RENDER_VISION_MESSAGE_ID,
    SCENE_MUTATING_TOOLS,
    SCENE_OBSERVE_MESSAGE_ID,
    TODO_BLOCKED_RECOVERY_ACTION_MESSAGE_ID,
    TODO_BLOCKED_RECOVERY_ATTEMPTS,
    TODO_BLOCKED_RECOVERY_MESSAGE_ID,
    TODO_CHECK_INTERVAL_ROUNDS,
    TODO_STAGNATION_LIMIT,
)
from .constants_workflow import (
    CONVERSATION_READ_ONLY_TOOLS,
    DEFAULT_MAX_PLAN_REPLANS,
    MODE_CONVERSATION,
    MODE_PLAN,
    MODE_SINGLE_ACTION,
    REQUEST_BUDGET_DEFAULTS,
    ROLE_BUILDER,
    ROLE_GENERAL,
    ROLE_VERIFIER,
    TOPOLOGY_DUAL,
    TOPOLOGY_SINGLE,
)
from scene_agent.config import get_settings
from scene_agent.memory.reference_image_memory import GLOBAL_TASK_ID, get_image_asset_memory
from scene_agent.utils.agent_messages import (
    collect_latest_tool_batch_names,
    find_last_ai_message,
    message_content_to_text,
)
from scene_agent.utils.render_refs import (
    extract_render_path,
    find_last_render_message,
    infer_render_source,
    normalize_render_reference,
    path_to_data_url as _path_to_data_url,
    payload_to_data_url,
    resolve_render_message_to_data_url,
)
from scene_agent.utils.todo_helpers import (
    align_todo_updates_with_existing,
    coerce_non_negative_int,
    coerce_todos,
    is_milestone_tool_batch,
    latest_todos_by_description,
    normalize_todo_description as _normalize_todo_description,
)
from scene_agent.utils.verification_helpers import (
    _latest_verification_feedback,
    _sanitize_verification_payload,
    active_todo_context,
    build_todo_updates_from_verification,
    build_verification_guidance_message,
    build_verification_scene_context,
    coerce_verification_dict,
    detect_catastrophic_scene_state,
    extract_verifier_fix_instructions,
    latest_verification_payload,
    replan_budget_remaining,
)

class RouterDecision(BaseModel):
    intent: Literal[
        "qa",
        "image_qa",
        "single_scene_action",
        "scene_reconstruction",
        "multi_step_scene_action",
        "continue_existing_plan",
        "clarification_needed",
    ]
    mode: Literal["conversation_mode", "single_action_mode", "plan_mode"]
    confidence: float = Field(ge=0.0, le=1.0)
    need_clarification: bool = False
    clarification_question: str = ""
    requires_scene_mutation: bool = False


class ReferenceImageNameSuggestion(BaseModel):
    name: str = ""
    caption: str = ""


class ReferenceImageSelectionDecision(BaseModel):
    should_attach: bool = False
    selected_name: str | None = None
    reason: str = ""


def coerce_task_mode(raw_mode: Any) -> TaskMode:
    if raw_mode in {MODE_CONVERSATION, MODE_SINGLE_ACTION, MODE_PLAN}:
        return raw_mode
    return MODE_PLAN


def coerce_workflow_topology(raw_topology: Any) -> str:
    if isinstance(raw_topology, str):
        normalized = raw_topology.strip()
        if normalized in {TOPOLOGY_SINGLE, TOPOLOGY_DUAL}:
            return normalized
    return TOPOLOGY_SINGLE


def _coerce_role(raw_role: Any) -> str:
    if isinstance(raw_role, str):
        normalized = raw_role.strip()
        if normalized in {ROLE_GENERAL, ROLE_BUILDER, ROLE_VERIFIER}:
            return normalized
    return ROLE_GENERAL


def request_budget(mode: TaskMode) -> dict[str, int]:
    budget = dict(REQUEST_BUDGET_DEFAULTS.get(mode, REQUEST_BUDGET_DEFAULTS[MODE_PLAN]))
    if mode != MODE_PLAN:
        return budget
    try:
        settings = get_settings()
        budget["max_request_agent_turns"] = max(1, int(settings.plan_mode_max_agent_turns))
        budget["max_request_tool_batches"] = max(1, int(settings.plan_mode_max_tool_batches))
    except Exception:
        pass
    return budget


def _verification_roles_for_mode(mode: TaskMode) -> set[str]:
    if mode == MODE_CONVERSATION:
        return {"question_image", "verification_reference", "style_reference"}
    if mode == MODE_SINGLE_ACTION:
        return {"object_reference", "verification_reference", "style_reference"}
    return {"scene_reference", "object_reference", "verification_reference", "style_reference"}


def _auto_binding_role_for_mode(mode: TaskMode) -> str:
    if mode == MODE_CONVERSATION:
        return "question_image"
    if mode == MODE_SINGLE_ACTION:
        return "object_reference"
    return "scene_reference"


def effective_todo_snapshot(state: AgentState) -> list[TodoItem]:
    return project_latest_todos(
        state.get("todo_versions"),
        fallback_todos_raw=state.get("todos"),
    )


def unfinished_todo_count(state: AgentState) -> int:
    todos = effective_todo_snapshot(state)
    return sum(1 for todo in todos if todo.get("status") in {"pending", "in_progress"})


def build_todo_runtime_prompt(state: AgentState) -> str:
    todos = effective_todo_snapshot(state)
    lines = [
        "Todo protocol:",
        f"- Use `{TODO_UPDATE_TOOL_NAME}` for all todo changes.",
        "- Never emit textual <todos> blocks.",
    ]
    active_todo_id = state.get("active_todo_id")
    if isinstance(active_todo_id, str) and active_todo_id:
        lines.append(f"- Current active todo: {active_todo_id}")
    if not todos:
        lines.append("- No current todos. For complex tasks, create them with todo_update(create).")
        return "\n".join(lines)

    lines.append("Current todo state (latest version per todo_id):")
    for todo in todos[:12]:
        todo_id = str(todo.get("id", "")).strip()
        status = str(todo.get("status", "")).strip()
        description = str(todo.get("description", "")).strip()
        if not todo_id or not description:
            continue
        marker = " (active)" if todo_id == active_todo_id else ""
        lines.append(f"- {todo_id} [{status}] {description}{marker}")
    return "\n".join(lines)

def _normalize_reference_image_key(raw_value: Any) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", str(raw_value or "").strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized[:48].rstrip("_")


def _coerce_attached_image_ids(raw_value: Any) -> list[str]:
    if not isinstance(raw_value, list):
        return []
    attached_ids: list[str] = []
    seen: set[str] = set()
    for item in raw_value:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        attached_ids.append(value)
    return attached_ids


def _coerce_reference_image_catalog(
    raw_value: Any,
) -> dict[str, ReferenceImageCatalogEntry]:
    if not isinstance(raw_value, dict):
        return {}
    catalog: dict[str, ReferenceImageCatalogEntry] = {}
    for raw_key, raw_entry in raw_value.items():
        key = _normalize_reference_image_key(raw_key)
        if not key or not isinstance(raw_entry, dict):
            continue
        asset_id = str(raw_entry.get("asset_id", "")).strip()
        stored_path = str(raw_entry.get("stored_path", "")).strip()
        if not asset_id or not stored_path:
            continue
        caption = raw_entry.get("caption", "")
        source_turn_at = raw_entry.get("source_turn_at", "")
        created_at = raw_entry.get("created_at", "")
        last_used_at = raw_entry.get("last_used_at")
        use_count_raw = raw_entry.get("use_count", 0)
        use_count = use_count_raw if isinstance(use_count_raw, int) and use_count_raw >= 0 else 0
        catalog[key] = ReferenceImageCatalogEntry(
            asset_id=asset_id,
            stored_path=stored_path,
            caption=caption.strip() if isinstance(caption, str) else "",
            source_turn_at=source_turn_at.strip() if isinstance(source_turn_at, str) else "",
            created_at=created_at.strip() if isinstance(created_at, str) else "",
            last_used_at=last_used_at.strip() if isinstance(last_used_at, str) and last_used_at.strip() else None,
            use_count=use_count,
        )
    return catalog


def _coerce_request_reference_image_keys(raw_value: Any) -> list[str]:
    if not isinstance(raw_value, list):
        return []
    keys: list[str] = []
    seen: set[str] = set()
    for item in raw_value:
        key = _normalize_reference_image_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


def _current_turn_has_images(state: AgentState) -> bool:
    return len(_coerce_attached_image_ids(state.get("attached_image_ids"))) > 0


def _catalog_has_images(state: AgentState) -> bool:
    return len(_coerce_reference_image_catalog(state.get("reference_image_catalog"))) > 0


def _resolve_reference_image_helper_model(
    *,
    provider_name: str | None,
    api_key: str | None,
    run_name: str,
) -> Any | None:
    settings = get_settings()
    resolved_provider = (provider_name or settings.vlm_provider).strip().lower()
    if not resolved_provider:
        return None
    resolved_api_key = api_key or settings.get_vlm_api_key(resolved_provider)
    if not resolved_api_key:
        return None
    try:
        from scene_agent.vlm import get_vlm_provider

        helper_model_name = settings.get_reference_image_helper_model(resolved_provider)
        helper_provider = get_vlm_provider(
            provider_name=resolved_provider,
            api_key=resolved_api_key,
            model=helper_model_name,
        )
        helper_model = helper_provider.get_chat_model()
        if hasattr(helper_model, "with_config"):
            try:
                return helper_model.with_config(tags=["nostream"], run_name=run_name)
            except Exception:
                return helper_model
        return helper_model
    except Exception:
        return None


def _fallback_reference_image_name(asset: Any) -> str:
    filename = getattr(asset, "filename", "")
    if isinstance(filename, str):
        stem, _ext = os.path.splitext(filename)
        normalized = _normalize_reference_image_key(stem)
        if normalized:
            return normalized
    asset_id = str(getattr(asset, "id", "")).strip()
    if asset_id:
        return f"image_{asset_id[:8]}"
    return "image_asset"


def _existing_reference_image_key_for_asset(
    catalog: dict[str, ReferenceImageCatalogEntry],
    asset_id: str,
) -> str | None:
    for key, entry in catalog.items():
        if entry["asset_id"] == asset_id:
            return key
    return None


def _ensure_unique_reference_image_key(
    desired_key: str,
    *,
    catalog: dict[str, ReferenceImageCatalogEntry],
    asset_id: str,
) -> str:
    existing_key = _existing_reference_image_key_for_asset(catalog, asset_id)
    if existing_key:
        return existing_key
    base_key = _normalize_reference_image_key(desired_key)
    if not base_key:
        base_key = f"image_{asset_id[:8]}" if asset_id else "image_asset"
    if base_key not in catalog:
        return base_key
    if catalog[base_key]["asset_id"] == asset_id:
        return base_key

    suffix = 2
    while True:
        suffix_token = f"_{suffix}"
        candidate = f"{base_key[: max(1, 48 - len(suffix_token))]}{suffix_token}"
        if candidate not in catalog or catalog[candidate]["asset_id"] == asset_id:
            return candidate
        suffix += 1


def _build_reference_image_catalog_entry(
    *,
    asset: Any,
    caption: str,
    now_iso: str,
    existing: ReferenceImageCatalogEntry | None = None,
) -> ReferenceImageCatalogEntry:
    stored_path = str(getattr(asset, "stored_path", "")).strip()
    asset_id = str(getattr(asset, "id", "")).strip()
    if existing is not None:
        created_at = existing["created_at"]
        source_turn_at = existing["source_turn_at"]
        effective_caption = existing["caption"] or caption
        use_count = existing["use_count"] + 1
    else:
        created_at = now_iso
        source_turn_at = now_iso
        effective_caption = caption
        use_count = 1
    return ReferenceImageCatalogEntry(
        asset_id=asset_id,
        stored_path=stored_path,
        caption=effective_caption[:240],
        source_turn_at=source_turn_at,
        created_at=created_at,
        last_used_at=now_iso,
        use_count=max(1, use_count),
    )


def _merge_reference_assets_into_catalog(
    *,
    catalog: dict[str, ReferenceImageCatalogEntry],
    assets: list[Any],
    now_iso: str,
    provider_name: str | None,
    api_key: str | None,
) -> list[str]:
    synced_keys: list[str] = []
    for asset in assets:
        asset_id = str(getattr(asset, "id", "")).strip()
        if not asset_id:
            continue
        existing_key = _existing_reference_image_key_for_asset(catalog, asset_id)
        existing_entry = catalog.get(existing_key) if existing_key else None
        if existing_key is None:
            suggested_name, caption = _describe_reference_image_with_helper(
                asset=asset,
                provider_name=provider_name,
                api_key=api_key,
            )
            entry_key = _ensure_unique_reference_image_key(
                suggested_name,
                catalog=catalog,
                asset_id=asset_id,
            )
        else:
            entry_key = existing_key
            caption = existing_entry["caption"] if existing_entry else ""
        catalog[entry_key] = _build_reference_image_catalog_entry(
            asset=asset,
            caption=caption,
            now_iso=now_iso,
            existing=existing_entry,
        )
        synced_keys.append(entry_key)
    return synced_keys


def _resolve_reference_assets_for_catalog_sync(
    state: AgentState,
    *,
    catalog: dict[str, ReferenceImageCatalogEntry],
    attached_image_ids: list[str],
) -> list[Any]:
    try:
        memory = get_reference_image_memory()
    except Exception:
        return []

    thread_id = state.get("thread_id", "default")
    if attached_image_ids:
        if hasattr(memory, "get_assets_by_ids"):
            try:
                return memory.get_assets_by_ids(thread_id, attached_image_ids)
            except Exception:
                return []
        return []

    if catalog:
        return []

    task_id = state.get("task_id")
    if hasattr(memory, "resolve_assets"):
        try:
            return memory.resolve_assets(thread_id=thread_id, task_id=task_id)
        except TypeError:
            try:
                return memory.resolve_assets(thread_id, task_id)
            except Exception:
                pass
        except Exception:
            pass
    if hasattr(memory, "list_assets"):
        try:
            return memory.list_assets(thread_id)
        except TypeError:
            try:
                return memory.list_assets(thread_id=thread_id)
            except Exception:
                pass
        except Exception:
            pass
    return []


def _describe_reference_image_with_helper(
    *,
    asset: Any,
    provider_name: str | None,
    api_key: str | None,
) -> tuple[str, str]:
    fallback_name = _fallback_reference_image_name(asset)
    stored_path = str(getattr(asset, "stored_path", "")).strip()
    data_url = _path_to_data_url(stored_path)
    if not data_url:
        return fallback_name, ""

    helper_model = _resolve_reference_image_helper_model(
        provider_name=provider_name,
        api_key=api_key,
        run_name="reference_image_name_helper",
    )
    if helper_model is None or not hasattr(helper_model, "with_structured_output"):
        return fallback_name, ""

    filename = getattr(asset, "filename", "")
    prompt = (
        "You name uploaded reference images for a 3D scene agent.\n"
        "Return a short snake_case-ish object/scene identifier and one concise caption.\n"
        "Keep the name stable, specific, and under 6 words."
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    if isinstance(filename, str) and filename.strip():
        content.append({"type": "text", "text": f"Original filename: {filename.strip()}"})
    content.append({"type": "image_url", "image_url": {"url": data_url}})

    try:
        structured = helper_model.with_structured_output(ReferenceImageNameSuggestion)
        response = structured.invoke([HumanMessage(content=content)])
        if not isinstance(response, ReferenceImageNameSuggestion):
            response = ReferenceImageNameSuggestion.model_validate(response)
        suggested_key = _normalize_reference_image_key(response.name)
        caption = response.caption.strip() if isinstance(response.caption, str) else ""
        return suggested_key or fallback_name, caption[:240]
    except Exception:
        return fallback_name, ""


def _tokenize_reference_selector_text(text: str) -> set[str]:
    if not isinstance(text, str):
        return set()
    return set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", text.lower()))


def _fallback_select_reference_image(
    *,
    latest_user_request: str,
    catalog: dict[str, ReferenceImageCatalogEntry],
) -> tuple[str | None, str]:
    request_tokens = _tokenize_reference_selector_text(latest_user_request)
    if not request_tokens:
        return None, "fallback_no_request_tokens"

    best_name: str | None = None
    best_score = 0
    best_use_count = -1
    for name, entry in catalog.items():
        candidate_tokens = _tokenize_reference_selector_text(f"{name} {entry['caption']}")
        if not candidate_tokens:
            continue
        score = len(request_tokens & candidate_tokens)
        if score <= 0:
            continue
        if score > best_score or (score == best_score and entry["use_count"] > best_use_count):
            best_name = name
            best_score = score
            best_use_count = entry["use_count"]
    if best_name is None:
        return None, "fallback_no_match"
    return best_name, f"fallback_token_overlap:{best_score}"


def _select_reference_image_with_helper(
    *,
    latest_user_request: str,
    catalog: dict[str, ReferenceImageCatalogEntry],
    provider_name: str | None,
    api_key: str | None,
) -> tuple[bool, str | None, str]:
    if not catalog:
        return False, None, "no_catalog_images"

    helper_model = _resolve_reference_image_helper_model(
        provider_name=provider_name,
        api_key=api_key,
        run_name="reference_image_select_helper",
    )
    if helper_model is None or not hasattr(helper_model, "with_structured_output"):
        return False, None, "helper_unavailable_no_auto_attach"

    catalog_lines: list[str] = []
    for name, entry in catalog.items():
        usage_note = f"use_count={entry['use_count']}"
        if entry["last_used_at"]:
            usage_note += f", last_used_at={entry['last_used_at']}"
        caption = entry["caption"] or "(no caption)"
        catalog_lines.append(f"- {name}: {caption} [{usage_note}]")

    selection_prompt = (
        "You decide whether a stored reference image should be reattached to the current user request.\n"
        "Attach at most one image.\n"
        "Return should_attach=false when the user request can be handled without a prior image.\n"
        "If should_attach=true, selected_name must exactly match one catalog name."
    )
    selection_input = (
        f"latest_user_request: {latest_user_request}\n"
        "reference_image_catalog:\n"
        + ("\n".join(catalog_lines) if catalog_lines else "(empty)")
    )

    try:
        structured = helper_model.with_structured_output(ReferenceImageSelectionDecision)
        response = structured.invoke(
            [
                SystemMessage(content=selection_prompt),
                HumanMessage(content=selection_input),
            ]
        )
        if not isinstance(response, ReferenceImageSelectionDecision):
            response = ReferenceImageSelectionDecision.model_validate(response)
        selected_name = _normalize_reference_image_key(response.selected_name)
        reason = response.reason.strip() if isinstance(response.reason, str) else ""
        if response.should_attach and selected_name in catalog:
            return True, selected_name, reason or "helper_selected_reference_image"
        if not response.should_attach:
            return False, None, reason or "helper_declined_reference_image"
        return False, None, reason or "helper_invalid_selection_no_auto_attach"
    except Exception:
        return False, None, "helper_error_no_auto_attach"


def _request_reference_image_entries(state: AgentState) -> list[dict[str, str]]:
    catalog = _coerce_reference_image_catalog(state.get("reference_image_catalog"))
    selected_keys = _coerce_request_reference_image_keys(state.get("request_reference_image_keys"))
    entries: list[dict[str, str]] = []
    seen_asset_ids: set[str] = set()
    for key in selected_keys:
        entry = catalog.get(key)
        if entry is None:
            continue
        asset_id = entry["asset_id"]
        stored_path = entry["stored_path"]
        if not asset_id or not stored_path or asset_id in seen_asset_ids:
            continue
        seen_asset_ids.add(asset_id)
        entries.append(
            {
                "key": key,
                "asset_id": asset_id,
                "stored_path": stored_path,
                "caption": entry["caption"],
            }
        )
    return entries


def _request_reference_keys_for_attached_images(
    *,
    catalog: dict[str, ReferenceImageCatalogEntry],
    attached_image_ids: list[str],
) -> list[str]:
    request_keys: list[str] = []
    seen_keys: set[str] = set()
    for asset_id in attached_image_ids:
        entry_key = _existing_reference_image_key_for_asset(catalog, asset_id)
        if not entry_key or entry_key in seen_keys:
            continue
        seen_keys.add(entry_key)
        request_keys.append(entry_key)
    return request_keys


def sync_reference_catalog_node(
    state: AgentState,
    *,
    provider_name: str | None = None,
    api_key: str | None = None,
) -> Dict[str, Any]:
    now_iso = datetime.now().isoformat()
    catalog = _coerce_reference_image_catalog(state.get("reference_image_catalog"))
    attached_image_ids = _coerce_attached_image_ids(state.get("attached_image_ids"))
    assets = _resolve_reference_assets_for_catalog_sync(
        state,
        catalog=catalog,
        attached_image_ids=attached_image_ids,
    )
    if assets:
        _merge_reference_assets_into_catalog(
            catalog=catalog,
            assets=assets,
            now_iso=now_iso,
            provider_name=provider_name,
            api_key=api_key,
        )
    return {
        "reference_image_catalog": catalog,
        "request_reference_image_keys": [],
        "request_reference_image_source": "none",
        "request_reference_image_reason": None,
    }


def prepare_reference_context_node(
    state: AgentState,
    *,
    provider_name: str | None = None,
    api_key: str | None = None,
) -> Dict[str, Any]:
    now_iso = datetime.now().isoformat()
    catalog = _coerce_reference_image_catalog(state.get("reference_image_catalog"))
    attached_image_ids = _coerce_attached_image_ids(state.get("attached_image_ids"))

    if attached_image_ids:
        request_keys = _request_reference_keys_for_attached_images(
            catalog=catalog,
            attached_image_ids=attached_image_ids,
        )
        if request_keys:
            return {
                "reference_image_catalog": catalog,
                "request_reference_image_keys": request_keys,
                "request_reference_image_source": "attached",
                "request_reference_image_reason": "current_turn_attached_images",
            }
        return {
            "reference_image_catalog": catalog,
            "request_reference_image_keys": [],
            "request_reference_image_source": "none",
            "request_reference_image_reason": "attached_images_unresolved",
        }

    latest_user_request = latest_human_message(state)
    should_attach, selected_name, reason = _select_reference_image_with_helper(
        latest_user_request=latest_user_request,
        catalog=catalog,
        provider_name=provider_name,
        api_key=api_key,
    )
    if should_attach and selected_name and selected_name in catalog:
        selected_entry = catalog[selected_name]
        catalog[selected_name] = ReferenceImageCatalogEntry(
            asset_id=selected_entry["asset_id"],
            stored_path=selected_entry["stored_path"],
            caption=selected_entry["caption"],
            source_turn_at=selected_entry["source_turn_at"],
            created_at=selected_entry["created_at"],
            last_used_at=now_iso,
            use_count=max(1, selected_entry["use_count"] + 1),
        )
        return {
            "reference_image_catalog": catalog,
            "request_reference_image_keys": [selected_name],
            "request_reference_image_source": "memory_retrieved",
            "request_reference_image_reason": reason or "selected_from_reference_catalog",
        }
    return {
        "reference_image_catalog": catalog,
        "request_reference_image_keys": [],
        "request_reference_image_source": "none",
        "request_reference_image_reason": reason or "no_reference_image_selected",
    }


def invoke_router_decision(
    *,
    state: AgentState,
    router_model: Any,
    latest_user_request: str,
) -> RouterDecision:
    thread_id = state.get("thread_id", "default")
    has_current_turn_images = _current_turn_has_images(state)
    has_catalog_images = _catalog_has_images(state)
    unfinished_todos = unfinished_todo_count(state)
    topology_hint = state.get("workflow_topology_request") or state.get("workflow_topology") or "auto"

    router_prompt = (
        "You are a strict workflow router for a 3D scene agent.\n"
        "Output only structured fields.\n"
        "Routing modes:\n"
        "- conversation_mode: QA / explanation / image understanding only.\n"
        "- single_action_mode: one scene edit expected to finish quickly.\n"
        "- plan_mode: multi-step scene construction/reconstruction.\n"
        "If user intent is ambiguous, set need_clarification=true and provide a concise clarification_question.\n"
        "Return confidence in [0,1]."
    )
    router_input = (
        f"latest_user_request: {latest_user_request}\n"
        f"thread_id: {thread_id}\n"
        f"has_current_turn_images: {has_current_turn_images}\n"
        f"has_catalog_images: {has_catalog_images}\n"
        f"unfinished_todos_count: {unfinished_todos}\n"
        f"requested_workflow_topology: {topology_hint}\n"
    )

    try:
        llm = router_model.with_config(tags=["nostream"], run_name="route_mode_internal")
        structured_llm = llm.with_structured_output(RouterDecision)
        decision_raw = structured_llm.invoke(
            [
                SystemMessage(content=router_prompt),
                HumanMessage(content=router_input),
            ]
        )
        if isinstance(decision_raw, RouterDecision):
            return decision_raw
        return RouterDecision.model_validate(decision_raw)
    except Exception as exc:
        get_logger().warning(
            "invoke_router_decision: router decision failed, falling back to safe plan_mode: %s",
            exc,
        )
        return RouterDecision(
            intent="multi_step_scene_action",
            mode="plan_mode",
            confidence=0.0,
            need_clarification=False,
            clarification_question="",
            requires_scene_mutation=True,
        )


def resolve_verification_assets(state: AgentState) -> list[Any]:
    return _request_reference_image_entries(state)


# Legacy alias kept for test monkeypatching and extension compatibility.
get_reference_image_memory = get_image_asset_memory








def _effective_tool_names_for_state(
    state: AgentState,
    tool_names: list[str] | None,
    *,
    role: str = ROLE_GENERAL,
) -> tuple[list[str] | None, str | None]:
    mode = coerce_task_mode(state.get("task_mode"))
    safe_role = _coerce_role(role)
    request_tool_batches, max_request_tool_batches = coerce_request_tool_budgets(
        request_tool_batches=state.get("request_tool_batches"),
        max_request_tool_batches=state.get("max_request_tool_batches"),
    )
    return resolve_effective_tool_names(
        mode=mode,
        role=safe_role,
        available_tool_names=tool_names,
        requested_tool_names=None,
        request_tool_batches=request_tool_batches,
        max_request_tool_batches=max_request_tool_batches,
    )


def _apply_request_scoped_tool_constraints(
    state: AgentState,
    tool_names: list[str] | None,
) -> list[str] | None:
    if tool_names is None:
        return None

    attached_image_ids = _coerce_attached_image_ids(state.get("attached_image_ids"))
    if len(attached_image_ids) == 1:
        return tool_names

    return [name for name in tool_names if name != "reconstruct_full_scene"]


def _normalize_role_memory_value(raw_value: Any) -> str:
    if raw_value is None:
        return ""
    if isinstance(raw_value, str):
        text = " ".join(raw_value.strip().split())
    elif isinstance(raw_value, (int, float, bool)):
        text = str(raw_value)
    elif isinstance(raw_value, list):
        compact = [str(item).strip() for item in raw_value if str(item).strip()]
        text = ", ".join(compact[:4])
    elif isinstance(raw_value, dict):
        text = json.dumps(raw_value, ensure_ascii=False, default=str)
    else:
        text = str(raw_value).strip()
    if len(text) > 240:
        return f"{text[:240].rstrip()}..."
    return text


def _build_role_private_memory_prompt(
    state: AgentState,
    *,
    role: str,
) -> str | None:
    memory_profile = str(state.get("memory_profile") or "").strip().lower()
    if memory_profile != "shared_plus_role_private":
        return None

    role_private_memory = state.get("role_private_memory")
    if not isinstance(role_private_memory, dict):
        return None
    role_bucket = role_private_memory.get(role)
    if not isinstance(role_bucket, dict) or not role_bucket:
        return None

    role_key_priority: dict[str, tuple[str, ...]] = {
        ROLE_BUILDER: (
            "last_action_summary",
            "last_replan_reason",
            "replan_count",
        ),
        ROLE_VERIFIER: (
            "last_verifier_action_summary",
            "last_feedback_status",
            "last_feedback_reason",
            "last_feedback_confidence",
        ),
        ROLE_GENERAL: ("last_action_summary",),
    }
    prioritized_keys = list(role_key_priority.get(role, ()))
    for key in role_bucket.keys():
        if isinstance(key, str) and key not in prioritized_keys:
            prioritized_keys.append(key)

    lines: list[str] = []
    for key in prioritized_keys:
        if not isinstance(key, str):
            continue
        value_text = _normalize_role_memory_value(role_bucket.get(key))
        if not value_text:
            continue
        pretty_key = key.replace("_", " ").strip()
        lines.append(f"- {pretty_key}: {value_text}")
        if len(lines) >= 5:
            break
    if not lines:
        return None

    return "\n".join(
        [
            "Role-private memory (historical hint for this role; prioritize current request and latest evidence):",
            *lines,
        ]
    )




def invoke_role_agent(
    *,
    state: AgentState,
    llm_with_tools: Any,
    available_tool_names: list[str] | None,
    role: str,
    summary_model: Any | None = None,
) -> Dict[str, Any]:
    # Build messages including system prompt
    from scene_agent.agent.prompts import get_full_system_prompt

    mode = coerce_task_mode(state.get("task_mode"))
    requested_tool_names = _resolve_effective_available_tools(state, available_tool_names)
    requested_tool_names = _apply_request_scoped_tool_constraints(
        state,
        requested_tool_names,
    )
    effective_tool_names, tool_policy_reason = _effective_tool_names_for_state(
        state,
        requested_tool_names,
        role=role,
    )

    messages = [SystemMessage(content=get_full_system_prompt(effective_tool_names))]
    if role == ROLE_BUILDER:
        messages.append(
            SystemMessage(
                content=(
                    "You are the Builder agent for plan_mode execution. "
                    "Focus on concrete scene edits and tool calls. "
                    "Use verifier feedback and todo context to perform the next highest-impact fix."
                )
            )
        )
    role_private_memory_prompt = _build_role_private_memory_prompt(state, role=role)
    if role_private_memory_prompt:
        messages.append(SystemMessage(content=role_private_memory_prompt))
    tool_constraints = _build_available_tools_constraint(effective_tool_names)
    if tool_constraints:
        messages.append(SystemMessage(content=tool_constraints))
    if tool_policy_reason == "conversation_mode_read_only":
        messages.append(
            SystemMessage(
                content=(
                    "Current mode is conversation_mode. "
                    "Do not call scene-mutation tools; answer directly unless a read-only check is essential."
                )
            )
        )
    elif tool_policy_reason == "verifier_role_camera_tools":
        messages.append(
            SystemMessage(
                content=(
                    "Current role is verifier. "
                    "Use camera and render tools for verification; do not use scene-asset mutation tools."
                )
            )
        )
    elif tool_policy_reason == "request_tool_budget_exhausted":
        messages.append(
            SystemMessage(
                content=(
                    "Tool budget for this request is exhausted. "
                    "Do not call more tools; provide a concise completion summary."
                )
            )
        )
    if role == ROLE_GENERAL and (mode == MODE_PLAN or effective_todo_snapshot(state)):
        messages.append(SystemMessage(content=build_todo_runtime_prompt(state)))
    state_messages = list(state["messages"])
    if role in {ROLE_GENERAL, ROLE_BUILDER, ROLE_VERIFIER}:
        state_messages = _inject_reference_images_into_latest_human_message(
            state_messages,
            state=state,
        )
    messages, summary_text, omitted_count = build_projected_context(
        base_messages=messages,
        state_messages=state_messages,
        pinned_message_ids={
            RENDER_VISION_MESSAGE_ID,
            SCENE_OBSERVE_MESSAGE_ID,
            CONVERGENCE_GUIDANCE_MESSAGE_ID,
        },
        max_recent_messages=12,
        token_counter=llm_with_tools,
        summary_model=summary_model,
    )
    
    # Invoke the LLM
    response = llm_with_tools.invoke(messages)
    response_id = getattr(response, "id", None)
    if not isinstance(response_id, str) or not response_id:
        response.id = f"assistant_turn_{uuid4().hex}"
    response, dropped_tools = _filter_unavailable_tool_calls(response, effective_tool_names)
    if dropped_tools:
        content_text = message_content_to_text(getattr(response, "content", ""))
        if not content_text.strip():
            skipped = ", ".join(sorted(set(dropped_tools)))
            response.content = (
                "I skipped unavailable tool calls and will continue with enabled tools only. "
                f"Skipped: {skipped}."
            )
    
    result: Dict[str, Any] = {"messages": [response]}
    if omitted_count > 0:
        current_compactions = coerce_non_negative_int(state.get("context_compaction_count"))
        result["context_summary"] = summary_text
        result["context_summary_message_count"] = omitted_count
        result["context_compaction_count"] = current_compactions + 1
    else:
        result["context_summary"] = ""
        result["context_summary_message_count"] = 0
    if _coerce_role(state.get("active_role")) != role:
        result["active_role"] = role
    return result








def ai_message_has_tool_calls(message: AIMessage | None) -> bool:
    if not isinstance(message, AIMessage):
        return False
    tool_calls = getattr(message, "tool_calls", None)
    if isinstance(tool_calls, list) and len(tool_calls) > 0:
        return True
    additional_kwargs = getattr(message, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        raw_calls = additional_kwargs.get("tool_calls")
        return isinstance(raw_calls, list) and len(raw_calls) > 0
    return False






















def compose_finalize_summary(
    state: AgentState,
    workflow: dict[str, Any],
    *,
    finalizer_model: Any | None = None,
) -> str:
    generated = _build_finalize_summary_with_model(
        state,
        workflow,
        finalizer_model=finalizer_model,
    )
    if generated:
        return generated
    return _build_finalize_summary(state, workflow)


def build_workflow_metadata(state: AgentState) -> dict[str, Any]:
    task_mode = coerce_task_mode(state.get("task_mode"))
    finish_reason = "no_tool_calls"

    stop_reason = state.get("request_stop_reason")
    if isinstance(stop_reason, str) and stop_reason:
        finish_reason = stop_reason
    elif isinstance(state.get("transition_reason"), str) and str(state.get("transition_reason")).strip():
        transition_reason = str(state.get("transition_reason")).strip()
        if transition_reason not in {"continue_execution", "router_initialized"}:
            finish_reason = transition_reason

    finalize_guard = state.get("finalize_guard")
    if isinstance(finalize_guard, dict):
        guard_status = finalize_guard.get("status")
        if guard_status == "completed":
            finish_reason = "todos_completed"
        elif guard_status == "blocked":
            finish_reason = "finalize_guard_blocked"

    if finish_reason == "no_tool_calls" and task_mode == MODE_CONVERSATION:
        finish_reason = "conversation_completed"

    return {
        "workflow_status": "finished",
        "finish_reason": finish_reason,
        "task_mode": task_mode,
        "task_intent": state.get("task_intent"),
        "workflow_topology": coerce_workflow_topology(state.get("workflow_topology")),
        "memory_profile": state.get("memory_profile"),
        "request_agent_turns": coerce_non_negative_int(state.get("request_agent_turns")),
        "request_tool_batches": coerce_non_negative_int(state.get("request_tool_batches")),
    }


def _build_finalize_summary(state: AgentState, workflow: dict[str, Any]) -> str:
    finish_reason = str(workflow.get("finish_reason", "unknown"))
    todo_counts = _collect_current_todo_counts(state)
    total_todos = todo_counts["total"]
    pending = todo_counts["pending"]
    in_progress = todo_counts["in_progress"]
    completed = todo_counts["completed"]
    failed = todo_counts["failed"]

    lines: list[str] = [
        "Result",
        f"Scene workflow finished ({finish_reason}).",
    ]

    finalize_guard = state.get("finalize_guard")
    finalize_guard_status: str | None = None
    finalize_guard_reason: str | None = None
    if isinstance(finalize_guard, dict):
        status = finalize_guard.get("status")
        reason = finalize_guard.get("reason")
        if isinstance(status, str) and status:
            finalize_guard_status = status
        if isinstance(reason, str) and reason:
            finalize_guard_reason = reason

    lines.extend(
        [
            "",
            "Todo Progress",
            (
                f"Total todos: {total_todos}. "
                f"Completed: {completed}, in progress: {in_progress}, pending: {pending}, failed: {failed}."
            ),
        ]
    )
    if finalize_guard_status:
        if finalize_guard_reason:
            lines.append(f"Finalize guard status: {finalize_guard_status} ({finalize_guard_reason}).")
        else:
            lines.append(f"Finalize guard status: {finalize_guard_status}.")

    verification_status, verification_reason = _latest_verification_feedback(state)
    lines.extend(["", "Verification Highlights"])
    if verification_status:
        lines.append(f"Latest verification: {verification_status}.")
    if verification_reason:
        lines.append(f"Verification note: {verification_reason}.")
    if not verification_status and not verification_reason:
        lines.append("No verification payload was captured in the final state.")

    next_action = _build_finalize_next_action(state, finish_reason, pending, in_progress)
    lines.extend(["", "Suggested Next Action", next_action])

    return "\n".join(lines)


def _collect_current_todo_counts(state: AgentState) -> dict[str, int]:
    finalize_guard = state.get("finalize_guard")
    if isinstance(finalize_guard, dict):
        pending = finalize_guard.get("pending_count")
        in_progress = finalize_guard.get("in_progress_count")
        completed = finalize_guard.get("completed_count")
        failed = finalize_guard.get("failed_count")
        if all(
            isinstance(value, int) and value >= 0
            for value in (pending, in_progress, completed, failed)
        ):
            total = pending + in_progress + completed + failed
            return {
                "total": total,
                "pending": pending,
                "in_progress": in_progress,
                "completed": completed,
                "failed": failed,
            }

    effective_todos = effective_todo_snapshot(state)
    return {
        "total": len(effective_todos),
        "pending": sum(1 for todo in effective_todos if todo.get("status") == "pending"),
        "in_progress": sum(1 for todo in effective_todos if todo.get("status") == "in_progress"),
        "completed": sum(1 for todo in effective_todos if todo.get("status") == "completed"),
        "failed": sum(1 for todo in effective_todos if todo.get("status") == "failed"),
    }


def _build_finalize_next_action(
    state: AgentState,
    finish_reason: str,
    pending_count: int,
    in_progress_count: int,
) -> str:
    if pending_count == 0 and in_progress_count == 0:
        return "If the result looks correct, export the scene artifacts (render/GLB/BLEND)."

    focus = active_todo_context(state)
    if focus:
        focus_entry = focus[0]
        focus_text = str(focus_entry.get("title", "")).strip()
        focus_id = str(focus_entry.get("todo_id", "")).strip()
        if focus_id:
            focus_text = f"{focus_id} {focus_text}".strip()
        return (
            "Continue from the next unfinished todo: "
            + focus_text
            + ". Apply edits, then render and verify again."
        )
    if finish_reason == "finalize_guard_blocked":
        return "Resolve the highest-impact scene mismatch first, then re-run render + verification."
    return "Continue iterating on unfinished todos, then render and verify before finalizing."


def _build_finalize_summary_with_model(
    state: AgentState,
    workflow: dict[str, Any],
    *,
    finalizer_model: Any | None,
) -> str | None:
    if finalizer_model is None:
        return None

    summary_payload = _build_finalize_summary_context(state, workflow)
    summarize_prompt = (
        "You summarize the final state of a 3D scene-editing workflow.\n"
        "Write concise plain text (no markdown table/code block) using 4 short sections:\n"
        "1) Result\n"
        "2) Todo Progress\n"
        "3) Verification Highlights\n"
        "4) Suggested Next Action\n"
        "Requirements:\n"
        "- Never dump raw dict/JSON.\n"
        "- Never copy machine field names (e.g., status/object_feedback/layout_feedback/todo_assessment).\n"
        "- Convert structured inputs into natural-language summary sentences.\n"
        "- Keep concrete and readable for end users.\n"
        "- Match the user's language inferred from latest_user_request."
    )
    context_json = json.dumps(summary_payload, ensure_ascii=False, default=str)
    summarize_messages = [
        SystemMessage(content=summarize_prompt),
        HumanMessage(content=f"workflow_state:\n{context_json}"),
    ]
    try:
        if hasattr(finalizer_model, "with_config"):
            invoke_model = finalizer_model.with_config(
                tags=["nostream"],
                run_name="finalize_summary_internal",
            )
            response = invoke_model.invoke(summarize_messages)
        else:
            try:
                response = finalizer_model.invoke(
                    summarize_messages,
                    config={"tags": ["nostream"], "run_name": "finalize_summary_internal"},
                )
            except TypeError:
                response = finalizer_model.invoke(summarize_messages)
    except Exception:
        return None

    content_text = message_content_to_text(getattr(response, "content", response))
    if not isinstance(content_text, str):
        return None
    normalized = re.sub(r"<agent_decision>.*?</agent_decision>", "", content_text, flags=re.DOTALL).strip()
    return normalized or None


def _build_finalize_summary_context(
    state: AgentState,
    workflow: dict[str, Any],
) -> dict[str, Any]:
    finalize_guard = state.get("finalize_guard")
    guard_summary: dict[str, Any] = {}
    if isinstance(finalize_guard, dict):
        for key in (
            "status",
            "reason",
            "pending_count",
            "in_progress_count",
            "completed_count",
            "failed_count",
            "stagnation_count",
            "tool_round_count",
        ):
            guard_summary[key] = finalize_guard.get(key)

    return {
        "finish_reason": workflow.get("finish_reason"),
        "workflow_status": workflow.get("workflow_status"),
        "task_mode": workflow.get("task_mode"),
        "task_intent": workflow.get("task_intent"),
        "workflow_topology": workflow.get("workflow_topology"),
        "memory_profile": workflow.get("memory_profile"),
        "request_agent_turns": workflow.get("request_agent_turns"),
        "request_tool_batches": workflow.get("request_tool_batches"),
        "builder_turn_count": coerce_non_negative_int(state.get("builder_turn_count")),
        "verifier_turn_count": coerce_non_negative_int(state.get("verifier_turn_count")),
        "plan_replan_count": coerce_non_negative_int(state.get("plan_replan_count")),
        "verifier_feedback": state.get("verifier_feedback") if isinstance(state.get("verifier_feedback"), dict) else {},
        "latest_user_request": latest_human_message(state),
        "finalize_guard": guard_summary,
        "active_todos": active_todo_context(state),
        "latest_verification": _sanitize_verification_payload(latest_verification_payload(state)),
    }


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
    if TODO_UPDATE_TOOL_NAME in deduped_available:
        requested_set.add(TODO_UPDATE_TOOL_NAME)
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


def _resolve_enabled_tool_set(state: AgentState) -> set[str]:
    enabled_tool_names = state.get("enabled_tool_names")
    if not isinstance(enabled_tool_names, list):
        return set()
    return {
        name.strip()
        for name in enabled_tool_names
        if isinstance(name, str) and name.strip()
    }


def should_use_viewport_scene_observe(state: AgentState) -> bool:
    enabled_tool_set = _resolve_enabled_tool_set(state)
    if enabled_tool_set:
        return "get_viewport_screenshot" in enabled_tool_set

    try:
        return get_settings().blender_mode == "local-client"
    except Exception:
        return False


def run_viewport_scene_observe(
    *,
    state: AgentState,
    thread_id: str,
    send_blender_command,
) -> Dict[str, Any]:
    logger = get_logger()
    command_sender = send_blender_command
    if command_sender is None:
        try:
            from mcp_server import runtime

            blender = runtime.get_blender_connection(logger)
            command_sender = blender.send_command
        except Exception as exc:
            logger.warning(
                "scene_observe_node: local viewport command sender unavailable: %s",
                exc,
            )
            return {"last_render_path": None}

    temp_path = ""
    screenshot_path = ""
    try:
        fd, temp_path = tempfile.mkstemp(
            prefix="scene_observe_viewport_",
            suffix=".png",
        )
        os.close(fd)
        result = command_sender(
            "get_viewport_screenshot",
            {
                "max_size": 800,
                "filepath": temp_path,
                "format": "png",
            },
        )
        if not isinstance(result, dict):
            logger.warning(
                "scene_observe_node: local viewport capture returned non-dict result: %r",
                result,
            )
            return {"last_render_path": None}
        error = result.get("error")
        if isinstance(error, str) and error.strip():
            logger.warning("scene_observe_node: local viewport capture failed: %s", error)
            return {"last_render_path": None}

        reported_path = result.get("filepath")
        screenshot_path = (
            reported_path.strip()
            if isinstance(reported_path, str) and reported_path.strip()
            else temp_path
        )
        if not os.path.exists(screenshot_path):
            logger.warning(
                "scene_observe_node: local viewport screenshot file missing: %s",
                screenshot_path,
            )
            return {"last_render_path": None}

        from scene_agent.utils.rendering import process_and_save_render

        render_url = process_and_save_render(
            screenshot_path,
            thread_id,
            "SceneObserveViewport",
            logger=logger,
        )
        vlm_ready_url = payload_to_data_url({"url": render_url})
        if not vlm_ready_url:
            vlm_ready_url = _path_to_data_url(screenshot_path)

        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "Auto scene observation — viewport screenshot after scene mutation (local-client mode). "
                    "Review this image to assess global composition, scale, and layout."
                ),
            }
        ]
        if vlm_ready_url:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": vlm_ready_url},
                }
            )

        return {
            "messages": [
                HumanMessage(
                    id=SCENE_OBSERVE_MESSAGE_ID,
                    content=content,
                )
            ],
            "last_render_path": render_url,
            "last_render_source": "scene_observe",
        }
    except Exception as exc:
        logger.warning("scene_observe_node: get_viewport_screenshot failed: %s", exc)
        return {"last_render_path": None}
    finally:
        for path in {temp_path, screenshot_path}:
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass


def get_logger():
    import logging
    return logging.getLogger("scene_agent.nodes")










def _prompt_reference_image_urls(state: AgentState) -> list[str]:
    assets = _request_reference_image_entries(state)
    urls: list[str] = []
    seen: set[str] = set()
    for asset in assets:
        stored_path = asset.get("stored_path")
        if not isinstance(stored_path, str) or not stored_path.strip():
            continue
        data_url = _path_to_data_url(stored_path)
        if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
            continue
        if data_url in seen:
            continue
        seen.add(data_url)
        urls.append(data_url)
    return urls


def _inject_reference_images_into_latest_human_message(
    messages: list[Any],
    *,
    state: AgentState,
) -> list[Any]:
    image_urls = _prompt_reference_image_urls(state)
    if not image_urls:
        return messages

    skip_ids = {RENDER_VISION_MESSAGE_ID, SCENE_OBSERVE_MESSAGE_ID}
    target_index: int | None = None
    for idx in range(len(messages) - 1, -1, -1):
        message = messages[idx]
        if not isinstance(message, HumanMessage):
            continue
        if getattr(message, "id", None) in skip_ids:
            continue
        target_index = idx
        break
    if target_index is None:
        return messages

    original = messages[target_index]
    text = message_content_to_text(getattr(original, "content", "")).strip()
    multimodal_content: list[dict[str, Any]] = []
    if text:
        multimodal_content.append({"type": "text", "text": text})
    else:
        multimodal_content.append({"type": "text", "text": "User request with reference images."})
    for url in image_urls:
        multimodal_content.append({"type": "image_url", "image_url": {"url": url}})

    updated = list(messages)
    updated_human = copy.deepcopy(original)
    updated_human.content = multimodal_content
    updated[target_index] = updated_human
    return updated


def latest_human_message(state: AgentState) -> str:
    _skip_ids = {
        RENDER_VISION_MESSAGE_ID,
        SCENE_OBSERVE_MESSAGE_ID,
    }
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage) and getattr(msg, "id", None) not in _skip_ids:
            return message_content_to_text(msg.content)
    return ""


# Export all non-dunder names for package-level compatibility.


__all__ = [name for name in globals() if not (name.startswith("__") and name.endswith("__"))]
