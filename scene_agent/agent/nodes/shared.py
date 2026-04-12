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
from typing import Any, Dict
from uuid import uuid4
from langchain_core.messages import ToolMessage, AIMessage, SystemMessage, HumanMessage
from pydantic import BaseModel
from scene_agent.agent.convergence import CONVERGENCE_GUIDANCE_MESSAGE_ID
from scene_agent.agent.context_manager import build_projected_context
from scene_agent.agent.state import AgentState, ReferenceImageCatalogEntry, TaskMode, TodoItem
from scene_agent.agent.todo_state import project_latest_todos
from scene_agent.agent.tool_policy import (
    coerce_request_tool_budgets,
    resolve_effective_tool_names,
)
from .constants_runtime import (
    RENDER_VISION_MESSAGE_ID,
    SCENE_MUTATING_TOOLS,
    SCENE_OBSERVE_MESSAGE_ID,
    TODO_BLOCKED_RECOVERY_ACTION_MESSAGE_ID,
    TODO_BLOCKED_RECOVERY_ATTEMPTS,
    TODO_BLOCKED_RECOVERY_MESSAGE_ID,
    TODO_STAGNATION_LIMIT,
)
from .constants_workflow import (
    DEFAULT_MAX_PLAN_REPLANS,
    MODE_DIRECT,
    MODE_CONVERSATION,
    MODE_PLAN,
    MODE_SINGLE_ACTION,
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
    extract_verifier_fix_instructions,
    latest_verification_payload,
    replan_budget_remaining,
)
from scene_agent.vlm.metrics import invoke_structured_with_metrics, invoke_with_metrics

class ReferenceImageNameSuggestion(BaseModel):
    name: str = ""
    caption: str = ""


class ReferenceImageSelectionDecision(BaseModel):
    should_attach: bool = False
    selected_name: str | None = None
    reason: str = ""


def coerce_task_mode(raw_mode: Any) -> TaskMode:
    if raw_mode in {MODE_DIRECT, MODE_PLAN}:
        return raw_mode
    if raw_mode in {MODE_CONVERSATION, MODE_SINGLE_ACTION}:
        return MODE_DIRECT
    return MODE_DIRECT


def coerce_budget_limit(raw_value: Any, *, default: int = -1) -> int:
    if isinstance(raw_value, int):
        return raw_value if raw_value >= -1 else default
    return default


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
def _verification_roles_for_mode(mode: TaskMode) -> set[str]:
    if mode == MODE_DIRECT:
        return {"object_reference", "verification_reference", "style_reference"}
    return {"scene_reference", "object_reference", "verification_reference", "style_reference"}


def _auto_binding_role_for_mode(mode: TaskMode) -> str:
    if mode == MODE_DIRECT:
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
        "Todo context (evaluator-managed lifecycle):",
        "- Do not modify todo statuses directly.",
        "- Focus on scene edits and render evidence for the active objective.",
    ]
    active_todo_id = state.get("active_todo_id")
    if isinstance(active_todo_id, str) and active_todo_id:
        lines.append(f"- Current active todo: {active_todo_id}")
    if not todos:
        lines.append("- No current todos are active for this request.")
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


def build_single_agent_active_todo_prompt(state: AgentState) -> str:
    focus = active_todo_context(state, active_only=True)
    open_todo_count = unfinished_todo_count(state)
    lines = [
        "Plan mode execution focus:",
        "- Todo lifecycle is evaluator-managed. Do not modify todo statuses directly.",
        "- Work only on the current active todo in this turn.",
        "- Do not proactively work on later todos, even if they seem related or easy to finish alongside the current one.",
        "- When the active todo looks complete, gather fresh render evidence and stop instead of moving to the next todo yourself.",
    ]
    if not focus:
        lines.append("- No active todo is currently resolved. Focus only on the next unfinished objective if one appears.")
        return "\n".join(lines)

    focus_entry = focus[0]
    focus_id = str(focus_entry.get("todo_id", "")).strip()
    focus_title = str(focus_entry.get("title", "")).strip()
    status = str(focus_entry.get("status", "")).strip()
    label = focus_title
    if focus_id:
        label = f"{focus_id}: {focus_title}" if focus_title else focus_id
    if label:
        lines.append(f"- Active todo only: {label}")
    if status:
        lines.append(f"- Active todo status: {status}")
    if open_todo_count > 1:
        lines.append(
            f"- There are {open_todo_count - 1} later unfinished todos. Ignore them until evaluator advances the active todo."
        )
    return "\n".join(lines)


def build_single_agent_active_todo_request(state: AgentState) -> str:
    focus = active_todo_context(state, active_only=True)
    lines = [
        "Plan mode execution request:",
        "- Execute only the current active todo in this turn.",
        "- Do not complete later todos or their core deliverables yet.",
        "- Use the current scene state, render evidence, and attached/reference images as guidance.",
    ]
    if not focus:
        lines.append("- No active todo is currently resolved. Do not expand scope on your own.")
        return "\n".join(lines)

    focus_entry = focus[0]
    focus_id = str(focus_entry.get("todo_id", "")).strip()
    focus_title = str(focus_entry.get("title", "")).strip()
    status = str(focus_entry.get("status", "")).strip()
    label = focus_title
    if focus_id:
        label = f"{focus_id}: {focus_title}" if focus_title else focus_id
    if label:
        lines.append(f"- Current active todo: {label}")
    if status:
        lines.append(f"- Active todo status: {status}")
    return "\n".join(lines)


def _project_single_agent_plan_mode_human_message(
    messages: list[Any],
    *,
    state: AgentState,
) -> list[Any]:
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

    projected_text = build_single_agent_active_todo_request(state)
    original = messages[target_index]
    updated = list(messages)
    updated_human = copy.deepcopy(original)
    original_content = getattr(original, "content", "")
    if isinstance(original_content, list):
        preserved_blocks: list[Any] = []
        for item in original_content:
            if isinstance(item, dict) and item.get("type") == "text":
                continue
            if isinstance(item, str):
                continue
            preserved_blocks.append(copy.deepcopy(item))
        updated_human.content = [{"type": "text", "text": projected_text}, *preserved_blocks]
    else:
        updated_human.content = projected_text
    updated[target_index] = updated_human
    return updated

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
    thread_id: str,
    turn_id: str | None,
    llm_call_records: list[dict[str, Any]] | None = None,
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
                thread_id=thread_id,
                turn_id=turn_id,
                llm_call_records=llm_call_records,
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
    thread_id: str,
    turn_id: str | None,
    llm_call_records: list[dict[str, Any]] | None = None,
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
        response, llm_call_record = invoke_structured_with_metrics(
            helper_model,
            ReferenceImageNameSuggestion,
            [HumanMessage(content=content)],
            thread_id=thread_id,
            turn_id=turn_id,
            node_name="sync_reference_catalog",
            call_role="reference_image_name_helper",
            provider_name=provider_name,
        )
        if llm_call_records is not None and isinstance(llm_call_record, dict):
            llm_call_records.append(llm_call_record)
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
    thread_id: str,
    turn_id: str | None,
    llm_call_records: list[dict[str, Any]] | None = None,
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
        response, llm_call_record = invoke_structured_with_metrics(
            helper_model,
            ReferenceImageSelectionDecision,
            [
                SystemMessage(content=selection_prompt),
                HumanMessage(content=selection_input),
            ],
            thread_id=thread_id,
            turn_id=turn_id,
            node_name="prepare_reference_context",
            call_role="reference_image_select_helper",
            provider_name=provider_name,
        )
        if llm_call_records is not None and isinstance(llm_call_record, dict):
            llm_call_records.append(llm_call_record)
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
    thread_id = str(state.get("thread_id") or "default")
    turn_id = latest_human_turn_id(state)
    catalog = _coerce_reference_image_catalog(state.get("reference_image_catalog"))
    attached_image_ids = _coerce_attached_image_ids(state.get("attached_image_ids"))
    assets = _resolve_reference_assets_for_catalog_sync(
        state,
        catalog=catalog,
        attached_image_ids=attached_image_ids,
    )
    llm_call_records: list[dict[str, Any]] = []
    if assets:
        _merge_reference_assets_into_catalog(
            catalog=catalog,
            assets=assets,
            now_iso=now_iso,
            provider_name=provider_name,
            api_key=api_key,
            thread_id=thread_id,
            turn_id=turn_id,
            llm_call_records=llm_call_records,
        )
    result: Dict[str, Any] = {
        "reference_image_catalog": catalog,
        "request_reference_image_keys": [],
        "request_reference_image_source": "none",
        "request_reference_image_reason": None,
    }
    if llm_call_records:
        result["llm_call_records"] = llm_call_records
    return result


def prepare_reference_context_node(
    state: AgentState,
    *,
    provider_name: str | None = None,
    api_key: str | None = None,
) -> Dict[str, Any]:
    now_iso = datetime.now().isoformat()
    thread_id = str(state.get("thread_id") or "default")
    turn_id = latest_human_turn_id(state)
    catalog = _coerce_reference_image_catalog(state.get("reference_image_catalog"))
    attached_image_ids = _coerce_attached_image_ids(state.get("attached_image_ids"))
    llm_call_records: list[dict[str, Any]] = []

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
        thread_id=thread_id,
        turn_id=turn_id,
        llm_call_records=llm_call_records,
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
        result: Dict[str, Any] = {
            "reference_image_catalog": catalog,
            "request_reference_image_keys": [selected_name],
            "request_reference_image_source": "memory_retrieved",
            "request_reference_image_reason": reason or "selected_from_reference_catalog",
        }
        if llm_call_records:
            result["llm_call_records"] = llm_call_records
        return result
    result = {
        "reference_image_catalog": catalog,
        "request_reference_image_keys": [],
        "request_reference_image_source": "none",
        "request_reference_image_reason": reason or "no_reference_image_selected",
    }
    if llm_call_records:
        result["llm_call_records"] = llm_call_records
    return result


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

    request_reference_image_keys = _coerce_request_reference_image_keys(
        state.get("request_reference_image_keys")
    )
    if len(request_reference_image_keys) > 0:
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


def _iter_exception_chain(error: BaseException) -> list[BaseException]:
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    chain: list[BaseException] = []
    while pending:
        current = pending.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)
        chain.append(current)
        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        if isinstance(cause, BaseException):
            pending.append(cause)
        if isinstance(context, BaseException) and context is not cause:
            pending.append(context)
    return chain


def _extract_masked_google_genai_error(error: BaseException) -> BaseException | None:
    if not isinstance(error, TypeError):
        return None
    if "object is not subscriptable" not in str(error):
        return None
    for candidate in _iter_exception_chain(error):
        if candidate is error:
            continue
        module_name = type(candidate).__module__
        if module_name == "google.genai.errors" or module_name.startswith("google.genai.errors."):
            return candidate
    return None




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

    messages = [
        SystemMessage(
            content=get_full_system_prompt(
                effective_tool_names,
                fast_mode=state.get("fast_mode") is True,
            )
        )
    ]
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
    if tool_policy_reason == "verifier_role_camera_tools":
        messages.append(
            SystemMessage(
                content=(
                    "Current role is verifier. "
                    "Before making any judgment, render/inspect the scene with camera tools. "
                    "Only provide completion judgment after visual inspection."
                )
            )
        )
    if role == ROLE_GENERAL:
        if mode == MODE_PLAN and coerce_workflow_topology(state.get("workflow_topology")) != TOPOLOGY_DUAL:
            messages.append(SystemMessage(content=build_single_agent_active_todo_prompt(state)))
        elif effective_todo_snapshot(state):
            messages.append(SystemMessage(content=build_todo_runtime_prompt(state)))
    state_messages = list(state["messages"])
    if role in {ROLE_GENERAL, ROLE_BUILDER, ROLE_VERIFIER}:
        state_messages = _inject_reference_images_into_latest_human_message(
            state_messages,
            state=state,
        )
    if (
        role == ROLE_GENERAL
        and mode == MODE_PLAN
        and coerce_workflow_topology(state.get("workflow_topology")) == TOPOLOGY_SINGLE
    ):
        state_messages = _project_single_agent_plan_mode_human_message(
            state_messages,
            state=state,
        )
    turn_id = latest_human_turn_id(state)
    thread_id = str(state.get("thread_id") or "default")
    messages, summary_text, omitted_count, llm_call_records = build_projected_context(
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
        thread_id=thread_id,
        turn_id=turn_id,
    )
    
    # Invoke the LLM
    try:
        response, llm_call_record = invoke_with_metrics(
            llm_with_tools,
            messages,
            thread_id=thread_id,
            turn_id=turn_id,
            node_name=role,
            call_role=role,
        )
        if isinstance(llm_call_record, dict):
            llm_call_records.append(llm_call_record)
    except Exception as exc:
        original_provider_error = _extract_masked_google_genai_error(exc)
        if original_provider_error is not None:
            raise original_provider_error from None
        raise
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
    if llm_call_records:
        result["llm_call_records"] = llm_call_records
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
) -> tuple[str, list[dict[str, Any]]]:
    generated, llm_call_records = _build_finalize_summary_with_model(
        state,
        workflow,
        finalizer_model=finalizer_model,
    )
    if generated:
        return generated, llm_call_records
    return _build_finalize_summary(state, workflow), llm_call_records


def build_workflow_metadata(state: AgentState) -> dict[str, Any]:
    task_mode = coerce_task_mode(state.get("task_mode"))
    finish_reason = "completed"
    transition_reason = state.get("transition_reason")
    if isinstance(transition_reason, str) and transition_reason.strip():
        finish_reason = transition_reason.strip()

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
    skipped = todo_counts["skipped"]

    sentence_1 = (
        f"The workflow finished ({finish_reason}). "
        f"Todo progress: {completed}/{total_todos} completed."
    )

    status_fragments = [
        f"{pending} pending",
        f"{in_progress} in progress",
        f"{failed} failed",
    ]
    if skipped > 0:
        status_fragments.append(f"{skipped} skipped")
    sentence_2 = "Current todo status: " + ", ".join(status_fragments) + "."

    verification_status, verification_reason = _latest_verification_feedback(state)
    sentence_3 = _build_finalize_next_action(state, finish_reason, pending, in_progress)
    if verification_status or verification_reason:
        verification_bits: list[str] = []
        if verification_status:
            verification_bits.append(f"Latest verification is {verification_status}")
        if verification_reason:
            verification_bits.append(verification_reason)
        sentence_3 = sentence_3 + " " + ". ".join(verification_bits) + "."
    return " ".join([sentence_1, sentence_2, sentence_3]).strip()


def _collect_current_todo_counts(state: AgentState) -> dict[str, int]:
    effective_todos = effective_todo_snapshot(state)
    return {
        "total": len(effective_todos),
        "pending": sum(1 for todo in effective_todos if todo.get("status") == "pending"),
        "in_progress": sum(1 for todo in effective_todos if todo.get("status") == "in_progress"),
        "completed": sum(1 for todo in effective_todos if todo.get("status") == "completed"),
        "failed": sum(1 for todo in effective_todos if todo.get("status") == "failed"),
        "skipped": sum(1 for todo in effective_todos if todo.get("status") == "skipped"),
        "superseded": sum(1 for todo in effective_todos if todo.get("status") == "superseded"),
    }


def _build_finalize_next_action(
    state: AgentState,
    finish_reason: str,
    pending_count: int,
    in_progress_count: int,
) -> str:
    if pending_count == 0 and in_progress_count == 0:
        return "If the result looks correct, you can export render/GLB/BLEND artifacts."

    focus = active_todo_context(state)
    if focus:
        focus_entry = focus[0]
        focus_text = str(focus_entry.get("title", "")).strip()
        focus_id = str(focus_entry.get("todo_id", "")).strip()
        if focus_id:
            focus_text = f"{focus_id} {focus_text}".strip()
        return (
            "Continue with the next unfinished objective: "
            + focus_text
            + ". Apply edits, then render and verify again."
        )
    if finish_reason == "pure_qa":
        return "No further scene edits are required for this request."
    return "Continue iterating on remaining objectives and verify with a fresh render before finishing."


def _build_finalize_summary_with_model(
    state: AgentState,
    workflow: dict[str, Any],
    *,
    finalizer_model: Any | None,
) -> tuple[str | None, list[dict[str, Any]]]:
    if finalizer_model is None:
        return None, []

    summary_payload = _build_finalize_summary_context(state, workflow)
    summarize_prompt = (
        "You summarize the final state of a 3D scene-editing workflow.\n"
        "Write 2-3 natural sentences in plain text (no markdown tables/code blocks).\n"
        "Requirements:\n"
        "- Mention what was accomplished.\n"
        "- Mention skipped todos when skipped_count > 0.\n"
        "- Mention what remains if pending/in_progress > 0.\n"
        "- Match the user's language inferred from latest_user_request.\n"
        "- Never dump raw dict/JSON keys."
    )
    context_json = json.dumps(summary_payload, ensure_ascii=False, default=str)
    summarize_messages = [
        SystemMessage(content=summarize_prompt),
        HumanMessage(content=f"workflow_state:\n{context_json}"),
    ]
    thread_id = str(state.get("thread_id") or "default")
    turn_id = latest_human_turn_id(state)
    llm_call_records: list[dict[str, Any]] = []
    try:
        if hasattr(finalizer_model, "with_config"):
            invoke_model = finalizer_model.with_config(
                tags=["nostream"],
                run_name="finalize_summary_internal",
            )
            response, llm_call_record = invoke_with_metrics(
                invoke_model,
                summarize_messages,
                thread_id=thread_id,
                turn_id=turn_id,
                node_name="finalize",
                call_role="finalize_summary",
            )
        else:
            try:
                response, llm_call_record = invoke_with_metrics(
                    finalizer_model,
                    summarize_messages,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    node_name="finalize",
                    call_role="finalize_summary",
                )
            except TypeError:
                response, llm_call_record = invoke_with_metrics(
                    finalizer_model,
                    summarize_messages,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    node_name="finalize",
                    call_role="finalize_summary",
                )
        if isinstance(llm_call_record, dict):
            llm_call_records.append(llm_call_record)
    except Exception:
        return None, []

    content_text = message_content_to_text(getattr(response, "content", response))
    if not isinstance(content_text, str):
        return None, llm_call_records
    normalized = re.sub(r"<agent_decision>.*?</agent_decision>", "", content_text, flags=re.DOTALL).strip()
    return normalized or None, llm_call_records


def _build_finalize_summary_context(
    state: AgentState,
    workflow: dict[str, Any],
) -> dict[str, Any]:
    todo_counts = _collect_current_todo_counts(state)

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
        "todo_counts": {
            "total": todo_counts["total"],
            "pending_count": todo_counts["pending"],
            "in_progress_count": todo_counts["in_progress"],
            "completed_count": todo_counts["completed"],
            "failed_count": todo_counts["failed"],
            "skipped_count": todo_counts["skipped"],
            "superseded_count": todo_counts["superseded"],
        },
        "active_todos": active_todo_context(state),
        "latest_verification": (
            state.get("verification_result")
            if isinstance(state.get("verification_result"), dict)
            else _sanitize_verification_payload(latest_verification_payload(state))
        ),
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


def latest_human_turn_id(state: AgentState) -> str | None:
    _skip_ids = {
        RENDER_VISION_MESSAGE_ID,
        SCENE_OBSERVE_MESSAGE_ID,
    }
    for msg in reversed(state["messages"]):
        if not isinstance(msg, HumanMessage):
            continue
        message_id = getattr(msg, "id", None)
        if message_id in _skip_ids:
            continue
        if isinstance(message_id, str) and message_id.strip():
            return message_id.strip()
    return None


# Export all non-dunder names for package-level compatibility.


__all__ = [name for name in globals() if not (name.startswith("__") and name.endswith("__"))]
