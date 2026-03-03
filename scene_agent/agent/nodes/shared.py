# ruff: noqa: F401
"""
LangGraph node implementations.
Nodes follow best practices: return partial state updates only.
"""
import base64
import ast
import copy
import hashlib
import json
import mimetypes
import os
import re
import tempfile
from datetime import datetime
from typing import Any, Dict, Literal
from urllib.parse import unquote, urlparse
from uuid import uuid4
from langchain_core.messages import ToolMessage, AIMessage, SystemMessage, HumanMessage
from pydantic import BaseModel, Field, ValidationError
from scene_agent.agent.convergence import CONVERGENCE_GUIDANCE_MESSAGE_ID
from scene_agent.agent.context_manager import build_projected_context
from scene_agent.agent.state import AgentState, ReferenceImageCatalogEntry, TaskMode, TodoItem
from scene_agent.agent.todo_protocol import TODO_UPDATE_TOOL_NAME
from scene_agent.agent.todo_state import project_latest_todos
from scene_agent.agent.tool_policy import (
    READ_ONLY_TOOLS,
    coerce_request_tool_budgets,
    resolve_effective_tool_names,
)
from scene_agent.config import get_settings
from scene_agent.memory.reference_image_memory import GLOBAL_TASK_ID, get_image_asset_memory

TODO_CHECK_INTERVAL_ROUNDS = 3
TODO_STAGNATION_LIMIT = 2
TODO_BLOCKED_RECOVERY_ATTEMPTS = 2
TODO_MILESTONE_TOOL_MARKERS = (
    "render_from_camera",
    "render_from_objects",
    "camera_observe",
    "camera_act",
    "observe_scene_global",
)

SCENE_MUTATING_TOOLS: frozenset[str] = frozenset({
    "clear_scene",
    "execute_blender_code",
    "delete_objects",
    "import_glb_model",
    "import_blend_contents",
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
    "undo_last_snapshot",
})

# Fixed message IDs for internal visual context messages.
# add_messages replaces by ID, so these slots hold at most one message each —
# no unbounded accumulation across turns.
RENDER_VISION_MESSAGE_ID = "render_vision_current"
SCENE_OBSERVE_MESSAGE_ID = "scene_observe_current"
TODO_BLOCKED_RECOVERY_MESSAGE_ID = "todo_blocked_recovery_current"
TODO_BLOCKED_RECOVERY_ACTION_MESSAGE_ID = "todo_blocked_recovery_action_current"

_CATASTROPHIC_SCENE_DIMENSION_THRESHOLD = 5000.0
_CATASTROPHIC_OBJECT_COORD_THRESHOLD = 5000.0
_CATASTROPHIC_OBJECT_DIMENSION_THRESHOLD = 2000.0
_CATASTROPHIC_RENDER_STDDEV_THRESHOLD = 2.0
_CATASTROPHIC_RENDER_GRAY_DRIFT_THRESHOLD = 3.0
_CATASTROPHIC_RENDER_BLACK_MEAN_THRESHOLD = 4.0
_CATASTROPHIC_RENDER_WHITE_MEAN_THRESHOLD = 251.0

MODE_CONVERSATION: TaskMode = "conversation_mode"
MODE_SINGLE_ACTION: TaskMode = "single_action_mode"
MODE_PLAN: TaskMode = "plan_mode"
TOPOLOGY_SINGLE = "single_agent"
TOPOLOGY_DUAL = "dual_agent"
ROLE_GENERAL = "general"
ROLE_BUILDER = "builder"
ROLE_VERIFIER = "verifier"
DEFAULT_MAX_PLAN_REPLANS = 3

REQUEST_BUDGET_DEFAULTS: dict[TaskMode, dict[str, int]] = {
    MODE_CONVERSATION: {"max_request_agent_turns": 2, "max_request_tool_batches": 0},
    MODE_SINGLE_ACTION: {"max_request_agent_turns": 3, "max_request_tool_batches": 1},
    MODE_PLAN: {"max_request_agent_turns": 50, "max_request_tool_batches": 40},
}

CONVERSATION_READ_ONLY_TOOLS: frozenset[str] = READ_ONLY_TOOLS

_PLAN_INTENT_MARKERS: tuple[str, ...] = (
    "recreate",
    "replicate",
    "match this scene",
    "rebuild scene",
    "entire scene",
    "full scene",
    "from reference",
    "according to reference",
    "layout",
    "composition",
    "lighting and materials",
)

_ACTION_INTENT_MARKERS: tuple[str, ...] = (
    "add ",
    "create ",
    "generate ",
    "import ",
    "place ",
    "move ",
    "rotate ",
    "scale ",
    "delete ",
    "remove ",
    "arrange ",
    "set texture",
)

_IMAGE_QA_MARKERS: tuple[str, ...] = (
    "this image",
    "the image",
    "in the image",
    "what is in",
    "what's in",
)
ROUTER_MIN_CONFIDENCE = 0.65


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


def _classify_task_mode_from_text(text: str) -> tuple[TaskMode, str]:
    normalized = " ".join((text or "").strip().lower().split())
    if not normalized:
        return MODE_CONVERSATION, "qa"

    if any(marker in normalized for marker in _PLAN_INTENT_MARKERS):
        return MODE_PLAN, "scene_reconstruction"

    action_hits = sum(1 for marker in _ACTION_INTENT_MARKERS if marker in normalized)
    if action_hits == 0:
        if any(marker in normalized for marker in _IMAGE_QA_MARKERS):
            return MODE_CONVERSATION, "image_qa"
        return MODE_CONVERSATION, "qa"

    # Multi-action phrasing usually indicates a plan-level workflow.
    if action_hits >= 2 or " and " in normalized or " then " in normalized:
        return MODE_PLAN, "multi_step_scene_action"

    return MODE_SINGLE_ACTION, "single_scene_action"


def build_router_clarification_question(text: str) -> str:
    mode_guess, _ = _classify_task_mode_from_text(text)
    if mode_guess == MODE_PLAN:
        return (
            "我需要先确认目标：你是要完整多步重建场景（plan_mode），"
            "还是只做一个单步修改（single_action_mode）？请明确最终目标和成功标准。"
        )
    if mode_guess == MODE_SINGLE_ACTION:
        return (
            "请补充这个单步动作的关键约束：目标对象、位置/尺寸、材质或参考图用途，"
            "以便我准确执行。"
        )
    return (
        "请确认你的意图：是只做问答解释，还是要我对 3D 场景执行修改？"
        "如果要修改，请描述具体动作。"
    )


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
        selected_name, reason = _fallback_select_reference_image(
            latest_user_request=latest_user_request,
            catalog=catalog,
        )
        return (selected_name is not None), selected_name, reason

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
    except Exception:
        pass

    selected_name, reason = _fallback_select_reference_image(
        latest_user_request=latest_user_request,
        catalog=catalog,
    )
    return (selected_name is not None), selected_name, reason


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
    except (ValidationError, Exception):
        return RouterDecision(
            intent="clarification_needed",
            mode="conversation_mode",
            confidence=0.0,
            need_clarification=True,
            clarification_question=build_router_clarification_question(latest_user_request),
            requires_scene_mutation=False,
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






def coerce_verification_dict(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        parsed = _coerce_verification_payload_from_text(payload)
        if isinstance(parsed, dict):
            return parsed
    return {}


def extract_verifier_fix_instructions(verification: dict[str, Any]) -> list[str]:
    instructions: list[str] = []
    seen: set[str] = set()

    raw_suggestions = verification.get("edit_suggestions")
    if isinstance(raw_suggestions, list):
        for raw_item in raw_suggestions:
            if not isinstance(raw_item, str):
                continue
            text = " ".join(raw_item.strip().split())
            if not text or text in seen:
                continue
            seen.add(text)
            instructions.append(text)

    for key in (
        "reason",
        "guidance",
        "object_feedback",
        "layout_feedback",
        "placement_feedback",
        "material_feedback",
        "scale_feedback",
        "environment_feedback",
    ):
        raw_value = verification.get(key)
        if not isinstance(raw_value, str):
            continue
        text = " ".join(raw_value.strip().split())
        if not text or text in seen:
            continue
        seen.add(text)
        instructions.append(text)

    return instructions[:8]


def replan_budget_remaining(state: AgentState) -> bool:
    current_replans = coerce_non_negative_int(state.get("plan_replan_count"))
    max_replans = coerce_non_negative_int(
        state.get("max_plan_replans"),
        default=DEFAULT_MAX_PLAN_REPLANS,
    )
    if max_replans < 0:
        return True
    return current_replans < max_replans


















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


def _sanitize_verification_payload(payload: Any) -> Any:
    if isinstance(payload, str):
        text = payload.strip()
        parsed = _coerce_verification_payload_from_text(text)
        if isinstance(parsed, dict):
            return _sanitize_verification_payload(parsed)
        return text[:600] if len(text) > 600 else text
    if not isinstance(payload, dict):
        return payload
    allowed_keys = (
        "status",
        "reason",
        "object_feedback",
        "layout_feedback",
        "placement_feedback",
        "material_feedback",
        "scale_feedback",
        "environment_feedback",
        "edit_suggestions",
        "render_source",
        "verification_mode",
    )
    sanitized: dict[str, Any] = {}
    for key in allowed_keys:
        if key not in payload:
            continue
        value = payload.get(key)
        if key == "edit_suggestions" and isinstance(value, list):
            sanitized[key] = [str(item) for item in value[:4]]
            continue
        if isinstance(value, str):
            sanitized[key] = value[:600] if len(value) > 600 else value
            continue
        sanitized[key] = value
    return sanitized


def latest_verification_payload(state: AgentState) -> dict[str, Any] | str | None:
    messages = state.get("messages")
    if not isinstance(messages, list):
        return None
    for msg in reversed(messages):
        if not isinstance(msg, ToolMessage):
            continue
        name = getattr(msg, "name", None)
        if not isinstance(name, str) or "verification" not in name:
            continue
        content = msg.content
        if isinstance(content, dict):
            return content
        if isinstance(content, str) and content.strip():
            return content.strip()
        return None
    return None


def _latest_verification_feedback(state: AgentState) -> tuple[str | None, str | None]:
    payload = latest_verification_payload(state)
    if isinstance(payload, dict):
        status = payload.get("status")
        reason = payload.get("reason")
        status_value = status if isinstance(status, str) and status else None
        reason_value = reason if isinstance(reason, str) and reason else None
        return status_value, reason_value
    if isinstance(payload, str):
        parsed = _coerce_verification_payload_from_text(payload)
        if isinstance(parsed, dict):
            status = parsed.get("status")
            reason = parsed.get("reason")
            status_value = status if isinstance(status, str) and status else None
            if isinstance(reason, str) and reason:
                return status_value, reason

            # Fallback to a concise synthesized reason from feedback fields.
            for key in (
                "object_feedback",
                "layout_feedback",
                "placement_feedback",
                "material_feedback",
                "scale_feedback",
                "environment_feedback",
            ):
                value = parsed.get(key)
                if isinstance(value, str) and value.strip():
                    return status_value, value.strip()
            return status_value, None
        normalized = " ".join(payload.strip().split())
        if not normalized:
            return None, None
        if len(normalized) > 300:
            normalized = f"{normalized[:300]}..."
        return None, normalized
    return None, None


def _coerce_verification_payload_from_text(text: str) -> dict[str, Any] | None:
    if not isinstance(text, str):
        return None
    normalized = text.strip()
    if not normalized:
        return None

    candidates: list[str] = [normalized]
    first_brace = normalized.find("{")
    last_brace = normalized.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        snippet = normalized[first_brace : last_brace + 1].strip()
        if snippet and snippet not in candidates:
            candidates.append(snippet)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        try:
            parsed_literal = ast.literal_eval(candidate)
            if isinstance(parsed_literal, dict):
                return parsed_literal
        except Exception:
            pass
    return None


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










def message_content_to_text(content: Any) -> str:
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


def collect_latest_tool_batch_names(messages: list) -> list[str]:
    names_reversed: list[str] = []
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            if isinstance(msg.name, str) and msg.name and msg.name != TODO_UPDATE_TOOL_NAME:
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


def align_todo_updates_with_existing(
    existing_todos_raw: Any,
    todo_updates: list[TodoItem],
) -> list[TodoItem]:
    if not todo_updates:
        return []

    existing_todos = coerce_todos(existing_todos_raw)
    if not existing_todos:
        return todo_updates

    existing_by_description: dict[str, TodoItem] = latest_todos_by_description(existing_todos)
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


_TODO_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "to",
        "of",
        "for",
        "with",
        "and",
        "on",
        "in",
        "at",
        "by",
        "from",
    }
)


def _normalize_todo_description(description: str) -> str:
    if not isinstance(description, str):
        return ""
    lowered = description.strip().lower()
    if not lowered:
        return ""
    alnum = re.sub(r"[^a-z0-9]+", " ", lowered)
    tokens = [token for token in alnum.split() if token and token not in _TODO_STOPWORDS]
    if not tokens:
        return re.sub(r"\s+", " ", lowered).strip()
    return " ".join(tokens)


def latest_todos_by_description(todos: list[TodoItem]) -> dict[str, TodoItem]:
    latest: dict[str, TodoItem] = {}
    for todo in todos:
        description = str(todo.get("description", ""))
        key = _normalize_todo_description(description)
        if not key:
            continue
        latest[key] = todo
    return latest


def coerce_todos(raw: Any) -> list[TodoItem]:
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


def coerce_non_negative_int(value: Any, *, default: int = 0) -> int:
    if isinstance(value, int) and value >= 0:
        return value
    return default


def is_milestone_tool_batch(names: Any) -> bool:
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
        if normalized and _is_probable_render_reference(normalized):
            return normalized
        return None
    return None


def find_last_render_message(messages: list) -> ToolMessage | None:
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


def find_last_ai_message(messages: list) -> AIMessage | None:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            return msg
    return None


def resolve_render_message_to_data_url(message: ToolMessage) -> str | None:
    """Convert a render tool message's image reference to a VLM-ready data URL.

    Uses extract_render_path for URL/path extraction (handles all content
    formats including markdown), then converts to data: via _path_to_data_url.
    Legacy base64 image blocks are handled as a fallback.
    """
    render_path = extract_render_path(message)
    if render_path:
        return _path_to_data_url(render_path)

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
        resolved = _resolve_renders_url_to_path(normalized)
        if resolved:
            return _path_to_data_url(resolved)
        if normalized.startswith("/renders/"):
            return _path_to_data_url(normalized)
        # HTTP URL - return as-is when it is externally reachable.
        if normalized.startswith("http://") or normalized.startswith("https://"):
            return normalized
        return _path_to_data_url(normalized)
    return None


def _path_to_data_url(path: str) -> str | None:
    normalized = normalize_render_reference(path)
    if not normalized:
        return None
    resolved_renders_path = _resolve_renders_url_to_path(normalized)
    if resolved_renders_path:
        normalized = resolved_renders_path
    # If it's already a URL/data URL, return as-is
    if normalized.startswith("data:"):
        return normalized
    if normalized.startswith("http://") or normalized.startswith("https://"):
        return normalized
    resolved_path = normalized
    if normalized.startswith("/renders/"):
        resolved_path = _resolve_renders_url_to_path(normalized) or normalized
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



def _extract_markdown_image_url(text: str) -> str | None:
    if not isinstance(text, str) or not text:
        return None
    pattern = r'!\[[^\]]*\]\(([^)]+)\)'
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
    markdown_url = _extract_markdown_image_url(normalized)
    if markdown_url:
        normalized = markdown_url
    if normalized.startswith("file://"):
        normalized = normalized.replace("file://", "", 1)
    return normalized


def _is_probable_render_reference(value: str) -> bool:
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


def _resolve_renders_url_to_path(url: str) -> str | None:
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


def active_todo_context(state: AgentState) -> list[dict[str, str]]:
    todos = effective_todo_snapshot(state)
    if not todos:
        return []
    in_progress: list[dict[str, str]] = []
    pending: list[dict[str, str]] = []
    active_todo_id = state.get("active_todo_id")
    for todo in todos:
        description = str(todo.get("description", "")).strip()
        status = str(todo.get("status", "")).strip()
        todo_id = str(todo.get("id", "")).strip()
        if not description or not todo_id:
            continue
        payload = {
            "todo_id": todo_id,
            "title": description,
            "status": status,
        }
        if todo_id == active_todo_id and status in {"pending", "in_progress"}:
            in_progress.insert(0, payload)
            continue
        if status == "in_progress":
            in_progress.append(payload)
        elif status == "pending":
            pending.append(payload)
    return (in_progress + pending)[:5]


def _normalize_verification_todo_status(raw_status: Any) -> str | None:
    if not isinstance(raw_status, str):
        return None
    normalized = raw_status.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"done", "completed", "complete"}:
        return "completed"
    if normalized in {"not_done", "pending", "in_progress", "uncertain", "unknown"}:
        return "not_completed"
    return None


def _tokenize_todo_text(text: str) -> set[str]:
    if not isinstance(text, str):
        return set()
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) >= 3
    }


def _match_todo_by_objective(
    latest_todos: dict[str, TodoItem],
    objective: str,
) -> TodoItem | None:
    objective_key = _normalize_todo_description(objective)
    if not objective_key:
        return None

    exact = latest_todos.get(objective_key)
    if exact:
        return exact

    if len(objective_key) >= 8:
        for key, todo in latest_todos.items():
            if objective_key in key or key in objective_key:
                return todo

    objective_tokens = _tokenize_todo_text(objective_key)
    if not objective_tokens:
        return None

    best_todo: TodoItem | None = None
    best_score = 0.0
    for key, todo in latest_todos.items():
        todo_tokens = _tokenize_todo_text(key)
        if not todo_tokens:
            continue
        overlap = objective_tokens & todo_tokens
        if not overlap:
            continue
        score = len(overlap) / max(len(objective_tokens), len(todo_tokens))
        if score > best_score:
            best_score = score
            best_todo = todo

    if best_score >= 0.5:
        return best_todo
    return None


def _extract_verification_todo_assessments(
    verification: dict[str, Any],
) -> list[dict[str, str]]:
    raw_assessment = verification.get("todo_assessment")
    if not isinstance(raw_assessment, list):
        return []

    assessments: list[dict[str, str]] = []
    for item in raw_assessment:
        if not isinstance(item, dict):
            continue
        todo_id = item.get("todo_id")
        status = item.get("status")
        reason = item.get("reason")
        if not isinstance(todo_id, str) or not todo_id.strip():
            continue
        normalized_status = _normalize_verification_todo_status(status)
        if normalized_status is None:
            continue
        assessments.append(
            {
                "todo_id": todo_id.strip(),
                "status": normalized_status,
                "reason": reason.strip() if isinstance(reason, str) else "",
            }
        )
    return assessments


def build_todo_updates_from_verification(
    state: AgentState,
    verification: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    todos = effective_todo_snapshot(state)
    if not todos:
        return [], []

    latest_todos = {
        str(todo.get("id", "")).strip(): todo
        for todo in todos
        if isinstance(todo.get("id"), str) and str(todo.get("id"))
    }
    if not latest_todos:
        return [], []

    assessments = _extract_verification_todo_assessments(verification)
    if not assessments:
        return [], []

    todo_actions: list[dict[str, Any]] = []
    update_records: list[dict[str, str]] = []

    for assessment in assessments:
        if assessment["status"] != "completed":
            continue
        matched = latest_todos.get(assessment["todo_id"])
        if not matched:
            continue
        if matched.get("status") == "completed":
            continue
        todo_actions.append(
            {
                "action": "set_status",
                "todo_id": assessment["todo_id"],
                "status": "completed",
                "reason": assessment.get("reason", ""),
            }
        )
        update_records.append(
            {
                "todo_id": assessment["todo_id"],
                "matched_todo": str(matched.get("description", "")),
                "status": "completed",
                "reason": assessment.get("reason", ""),
            }
        )

    return todo_actions, update_records


def build_verification_scene_context(state: AgentState) -> dict[str, Any] | None:
    context: dict[str, Any] = {}

    scene_objects = state.get("scene_objects")
    if isinstance(scene_objects, dict) and scene_objects:
        object_names = sorted(name for name in scene_objects.keys() if isinstance(name, str))
        selected_names = object_names[:60]
        compact_objects: dict[str, Any] = {}
        for name in selected_names:
            raw_object = scene_objects.get(name)
            if isinstance(raw_object, dict):
                compact: dict[str, Any] = {}
                for key in ("type", "location", "dimensions", "bounding_box", "visible", "material_count"):
                    if key in raw_object:
                        compact[key] = raw_object.get(key)
                compact_objects[name] = compact or raw_object
            else:
                compact_objects[name] = raw_object
        context["scene_objects"] = compact_objects
        context["scene_object_count"] = len(object_names)
        if len(object_names) > len(selected_names):
            context["scene_objects_truncated"] = True

    scene_bbox = state.get("scene_bbox")
    if isinstance(scene_bbox, dict) and scene_bbox:
        context["scene_bbox"] = scene_bbox

    scene_camera_params = state.get("scene_camera_params")
    if isinstance(scene_camera_params, dict) and scene_camera_params:
        context["scene_camera_params"] = scene_camera_params

    persistent_cameras = state.get("persistent_cameras")
    if isinstance(persistent_cameras, list) and persistent_cameras:
        cameras = [name for name in persistent_cameras if isinstance(name, str)]
        if cameras:
            context["persistent_cameras"] = cameras[:12]

    active_todos = active_todo_context(state)
    if active_todos:
        context["active_todos"] = active_todos

    return context or None


def _coerce_numeric_triplet(raw_value: Any) -> list[float]:
    if not isinstance(raw_value, (list, tuple)):
        return []
    values: list[float] = []
    for item in raw_value[:3]:
        if isinstance(item, (int, float)):
            values.append(float(item))
    return values


def _bbox_dimensions_from_bounds(raw_bbox: Any) -> list[float]:
    if (
        not isinstance(raw_bbox, (list, tuple))
        or len(raw_bbox) != 2
        or not isinstance(raw_bbox[0], (list, tuple))
        or not isinstance(raw_bbox[1], (list, tuple))
    ):
        return []
    min_corner = _coerce_numeric_triplet(raw_bbox[0])
    max_corner = _coerce_numeric_triplet(raw_bbox[1])
    if len(min_corner) != 3 or len(max_corner) != 3:
        return []
    return [
        abs(max_corner[0] - min_corner[0]),
        abs(max_corner[1] - min_corner[1]),
        abs(max_corner[2] - min_corner[2]),
    ]


def _resolve_render_path_for_analysis(render_reference: Any) -> str | None:
    if not isinstance(render_reference, str):
        return None
    normalized = render_reference.strip()
    if not normalized or normalized.startswith("data:"):
        return None
    if normalized.startswith("file://"):
        normalized = normalized.replace("file://", "", 1)

    parsed = urlparse(normalized)
    candidate_path = parsed.path if parsed.scheme and parsed.netloc else normalized
    if candidate_path.startswith("/renders/"):
        filename = unquote(candidate_path.replace("/renders/", "", 1).strip("/"))
        if not filename:
            return None
        try:
            from scene_agent.utils.rendering import RENDERS_DIR

            local_path = os.path.join(str(RENDERS_DIR), filename)
            if os.path.exists(local_path):
                return local_path
        except Exception:
            return None
    if os.path.exists(candidate_path):
        return candidate_path
    return None


def _analyze_render_flatness(render_reference: Any) -> dict[str, Any] | None:
    local_path = _resolve_render_path_for_analysis(render_reference)
    if not local_path:
        return None

    try:
        from PIL import Image, ImageStat

        with Image.open(local_path) as image:
            rgb = image.convert("RGB")
            stat = ImageStat.Stat(rgb)
    except Exception:
        return None

    means = [float(value) for value in stat.mean[:3]]
    stddevs = [float(value) for value in stat.stddev[:3]]
    if not means or not stddevs:
        return None

    mean_intensity = sum(means) / len(means)
    stddev_intensity = sum(stddevs) / len(stddevs)
    max_channel_drift = max(abs(channel - mean_intensity) for channel in means)
    is_flat = stddev_intensity <= _CATASTROPHIC_RENDER_STDDEV_THRESHOLD
    is_grayish = max_channel_drift <= _CATASTROPHIC_RENDER_GRAY_DRIFT_THRESHOLD
    is_black_or_white = (
        mean_intensity <= _CATASTROPHIC_RENDER_BLACK_MEAN_THRESHOLD
        or mean_intensity >= _CATASTROPHIC_RENDER_WHITE_MEAN_THRESHOLD
    )
    return {
        "path": local_path,
        "mean_intensity": round(mean_intensity, 3),
        "stddev_intensity": round(stddev_intensity, 3),
        "max_channel_drift": round(max_channel_drift, 3),
        "is_flat": bool(is_flat),
        "is_grayish": bool(is_grayish),
        "is_black_or_white": bool(is_black_or_white),
    }


def detect_catastrophic_scene_state(
    state: AgentState,
    *,
    render_reference: Any,
) -> dict[str, Any]:
    signals: list[str] = []
    metrics: dict[str, Any] = {}

    scene_bbox = state.get("scene_bbox")
    if isinstance(scene_bbox, dict):
        dims = _coerce_numeric_triplet(scene_bbox.get("dimensions"))
        if len(dims) == 3:
            max_dim = max(abs(value) for value in dims)
            metrics["scene_bbox_dimensions"] = [round(value, 4) for value in dims]
            metrics["scene_bbox_max_dimension"] = round(max_dim, 4)
            if max_dim > _CATASTROPHIC_SCENE_DIMENSION_THRESHOLD:
                signals.append("scene_bbox_dimension_exploded")
            positive_dims = [abs(value) for value in dims if abs(value) > 1e-6]
            if positive_dims:
                span_ratio = max(positive_dims) / min(positive_dims)
                metrics["scene_bbox_span_ratio"] = round(span_ratio, 4)
                if max_dim > 100.0 and span_ratio > 10000.0:
                    signals.append("scene_bbox_span_ratio_extreme")

    scene_objects = state.get("scene_objects")
    if isinstance(scene_objects, dict) and scene_objects:
        far_objects: list[str] = []
        huge_objects: list[str] = []
        for name, payload in list(scene_objects.items())[:300]:
            if not isinstance(name, str):
                continue
            if not isinstance(payload, dict):
                continue

            location = _coerce_numeric_triplet(payload.get("location"))
            if location and max(abs(value) for value in location) > _CATASTROPHIC_OBJECT_COORD_THRESHOLD:
                far_objects.append(name)

            dimensions = _coerce_numeric_triplet(payload.get("dimensions"))
            if not dimensions:
                dimensions = _bbox_dimensions_from_bounds(payload.get("bounding_box"))
            if not dimensions and isinstance(payload.get("bbox"), dict):
                dimensions = _coerce_numeric_triplet(payload["bbox"].get("dimensions"))
            if dimensions and max(abs(value) for value in dimensions) > _CATASTROPHIC_OBJECT_DIMENSION_THRESHOLD:
                huge_objects.append(name)

            if len(far_objects) >= 5 and len(huge_objects) >= 5:
                break

        if far_objects:
            signals.append("object_location_outlier")
            metrics["object_location_outliers"] = far_objects[:5]
        if huge_objects:
            signals.append("object_dimension_outlier")
            metrics["object_dimension_outliers"] = huge_objects[:5]

    render_stats = _analyze_render_flatness(render_reference)
    if isinstance(render_stats, dict):
        metrics["render_flatness"] = render_stats
        if render_stats.get("is_flat") and (
            render_stats.get("is_grayish") or render_stats.get("is_black_or_white")
        ):
            signals.append("render_flat_gray_or_blank")

    return {
        "is_catastrophic": bool(signals),
        "signals": sorted(set(signals)),
        "metrics": metrics,
    }


def _resolve_enabled_tool_set(state: AgentState) -> set[str]:
    enabled_tool_names = state.get("enabled_tool_names")
    if not isinstance(enabled_tool_names, list):
        return set()
    return {
        name.strip()
        for name in enabled_tool_names
        if isinstance(name, str) and name.strip()
    }




def build_verification_guidance_message(
    state: AgentState,
    verification: dict[str, Any],
) -> str:
    status_value = verification.get("status")
    status = status_value.strip().lower() if isinstance(status_value, str) else ""
    if status == "catastrophic":
        hard_recovery = verification.get("hard_recovery")
        if isinstance(hard_recovery, dict):
            action = hard_recovery.get("action")
            attempt = hard_recovery.get("attempt")
            forced = bool(hard_recovery.get("forced"))
            if action == "undo_last_snapshot" and forced:
                return (
                    f"Catastrophic state detected (attempt {attempt}). "
                    "Hard recovery is forcing undo_last_snapshot, then scene re-grounding."
                )
            if action == "clear_scene" and forced:
                return (
                    f"Catastrophic state detected (attempt {attempt}). "
                    "Hard recovery is forcing clear_scene; rebuild todos if they assume deleted assets."
                )
            if action in {"undo_last_snapshot", "clear_scene"} and not forced:
                return (
                    f"Catastrophic state detected (attempt {attempt}), but automatic recovery budget is exhausted. "
                    "Continue with manual remediation and consider rebuilding todos if scene state was reset."
                )
        return "Catastrophic state detected; no automatic recovery tool is currently available."
    if status == "match":
        return "Latest verification is match. Continue with the next pending todo."

    render_source = verification.get("render_source")
    is_scene_level = isinstance(render_source, str) and render_source == "scene_observe"
    focus_candidates: list[str] = []

    for line in active_todo_context(state):
        todo_id = str(line.get("todo_id", "")).strip()
        title = str(line.get("title", "")).strip()
        label = f"{todo_id} {title}".strip()
        if not label:
            continue
        if label not in focus_candidates:
            focus_candidates.append(label)
        if len(focus_candidates) >= 4:
            break

    focus_text = ""
    if focus_candidates:
        focus_text = " Focus first on: " + ", ".join(focus_candidates[:4]) + "."

    if is_scene_level:
        return (
            "Global verification still reports mismatches. "
            "Before editing, run object-level inspection with "
            "render_from_objects(object_names=[...], mode=\"annotated\") "
            "to localize exact problem objects and positions."
            + focus_text
        )
    return (
        "Object-level verification is not yet match. "
        "Run render_from_objects(object_names=[...], mode=\"annotated\") "
        "before the next edit so you can locate and fix issues precisely."
        + focus_text
    )


# Export all non-dunder names for package-level compatibility.


__all__ = [name for name in globals() if not (name.startswith("__") and name.endswith("__"))]
