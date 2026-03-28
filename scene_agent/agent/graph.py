"""
LangGraph state machine construction.
Creates the agent graph following LangGraph best practices.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Literal

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt.tool_node import ToolCallRequest

from scene_agent.agent.graph_factory import build_agent_state_graph
from scene_agent.agent.internal_tools import get_internal_agent_tools
from scene_agent.agent.nodes import (
    agent_node,
    builder_agent_node,
    evaluator_node,
    finalize_node,
    initialize_request_node,
    plan_node,
    planner_refresh_node,
    post_builder_node,
    prepare_reference_context_node,
    router_node,
    scene_observe_node,
    sync_reference_catalog_node,
    turn_dispatch_node,
    update_memory_node,
    verifier_agent_node,
    verifier_feedback_node,
    verify_node,
)
from scene_agent.agent.redis_checkpointer import get_graph_checkpointer
from scene_agent.agent.state import AgentState
from scene_agent.config import get_settings
from scene_agent.memory.reference_image_memory import get_image_asset_memory
from scene_agent.tools import get_blender_tools
from scene_agent.vlm import get_vlm_provider

_TOOL_RETRY_MAX_ATTEMPTS = 2
_TOOL_RETRY_BASE_DELAY_SECONDS = 0.2
_RETRYABLE_TOOL_ERROR_MARKERS = (
    "validation error",
    "input should",
    "type=list_type",
    "timeout",
    "timed out",
    "connection",
    "transport",
    "temporarily unavailable",
    "service unavailable",
    "rate limit",
    "try again",
    "429",
    "503",
)
_SUPPORTED_VLM_PROVIDERS = frozenset({"openai", "anthropic", "gemini", "qwen"})


def _extract_tool_hint(tool: object) -> str | None:
    description = getattr(tool, "description", None)
    if not isinstance(description, str):
        return None
    normalized = re.sub(r"\s+", " ", description).strip()
    if not normalized:
        return None
    return normalized


def _message_has_tool_calls(message: AIMessage) -> bool:
    tool_calls = getattr(message, "tool_calls", None)
    if isinstance(tool_calls, list) and len(tool_calls) > 0:
        return True

    additional_kwargs = getattr(message, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        additional_tool_calls = additional_kwargs.get("tool_calls")
        if isinstance(additional_tool_calls, list) and len(additional_tool_calls) > 0:
            return True
    return False


def _build_context_summary_helper_model(
    *,
    provider_name: str,
    api_key: str,
    settings: Any,
) -> Any | None:
    try:
        if hasattr(settings, "get_context_summary_helper_model"):
            helper_model_name = settings.get_context_summary_helper_model(provider_name)
        else:
            helper_model_name = settings.get_reference_image_helper_model(provider_name)
        helper_provider = get_vlm_provider(
            provider_name=provider_name,
            api_key=api_key,
            model=helper_model_name,
        )
        helper_model = helper_provider.get_chat_model()
    except Exception:
        return None

    if hasattr(helper_model, "with_config"):
        try:
            return helper_model.with_config(
                tags=["nostream"],
                run_name="context_summary_helper",
            )
        except Exception:
            return helper_model
    return helper_model


def _resolve_dual_agent_verifier_runtime(
    *,
    settings: Any,
    default_provider: str,
    default_model: str,
    default_api_key: str,
) -> tuple[str, str, str]:
    raw_provider = getattr(settings, "dual_agent_verifier_vlm_provider", None)
    verifier_provider = default_provider
    if isinstance(raw_provider, str):
        candidate = raw_provider.strip().lower()
        if candidate in _SUPPORTED_VLM_PROVIDERS:
            verifier_provider = candidate

    raw_model = getattr(settings, "dual_agent_verifier_vlm_model", None)
    if isinstance(raw_model, str) and raw_model.strip():
        verifier_model = raw_model.strip()
    elif verifier_provider == default_provider:
        verifier_model = default_model
    else:
        verifier_model = settings.get_vlm_default_model(verifier_provider)
    verifier_api_key = settings.get_vlm_api_key(verifier_provider)
    if not verifier_api_key:
        return default_provider, default_model, default_api_key
    return verifier_provider, verifier_model, verifier_api_key


def _workflow_topology(state: AgentState) -> str:
    raw_topology = state.get("workflow_topology")
    if isinstance(raw_topology, str) and raw_topology.strip():
        return raw_topology.strip()
    return "single_agent"


def _is_dual_topology(state: AgentState) -> bool:
    return _workflow_topology(state) == "dual_agent"


def _route_after_initialize_request(_state: AgentState) -> Literal["sync_reference_catalog"]:
    return "sync_reference_catalog"


def _route_after_sync_reference_catalog(
    _state: AgentState,
) -> Literal["prepare_reference_context"]:
    return "prepare_reference_context"


def _route_after_prepare_reference_context(_state: AgentState) -> Literal["router"]:
    return "router"


def _route_after_router(
    state: AgentState,
) -> Literal["plan_node", "agent", "builder_agent"]:
    if bool(state.get("routed_to_plan")):
        return "plan_node"
    return "builder_agent" if _is_dual_topology(state) else "agent"


def _route_after_plan_node(state: AgentState) -> Literal["agent", "builder_agent"]:
    return "builder_agent" if _is_dual_topology(state) else "agent"


def _route_after_turn_dispatch(state: AgentState) -> Literal["tools", "evaluator"]:
    return "tools" if state.get("assistant_turn_kind") == "has_calls" else "evaluator"


def _route_after_post_builder(state: AgentState) -> Literal["tools", "evaluator"]:
    return "tools" if state.get("assistant_turn_kind") == "has_calls" else "evaluator"


def _route_after_verifier_feedback(state: AgentState) -> Literal["tools", "evaluator"]:
    return "tools" if state.get("assistant_turn_kind") == "has_calls" else "evaluator"


def _route_after_update_memory(state: AgentState) -> Literal["scene_observe", "verifier_agent"]:
    if _is_dual_topology(state):
        return "verifier_agent"
    return "scene_observe"


def _route_after_evaluator(state: AgentState) -> str:
    transition_next = state.get("transition_next")
    if transition_next in {"agent", "builder_agent", "planner_refresh", "finalize"}:
        return str(transition_next)
    if transition_next in {"__end__", END}:
        return END
    return "builder_agent" if _is_dual_topology(state) else "agent"


def _coerce_object_name_list(raw_value: Any) -> list[str] | None:
    if isinstance(raw_value, list):
        normalized: list[str] = []
        for item in raw_value:
            if not isinstance(item, str):
                continue
            value = item.strip()
            if value:
                normalized.append(value)
        return normalized

    if not isinstance(raw_value, str):
        return None

    text = raw_value.strip()
    if not text:
        return []

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, list):
        return _coerce_object_name_list(parsed)

    if any(separator in text for separator in (",", "\n", ";")):
        chunks = text.replace("\n", ",").replace(";", ",").split(",")
    else:
        chunks = [text]

    normalized: list[str] = []
    for chunk in chunks:
        value = chunk.strip().strip("\"'`")
        if value:
            normalized.append(value)
    return normalized


def _normalize_tool_call_args(tool_call: dict[str, Any]) -> dict[str, Any]:
    tool_name = tool_call.get("name")
    if tool_name != "delete_objects":
        return tool_call

    raw_args = tool_call.get("args")
    if not isinstance(raw_args, dict):
        return tool_call

    normalized_names = _coerce_object_name_list(raw_args.get("object_names"))
    if normalized_names is None:
        return tool_call

    updated_args = dict(raw_args)
    updated_args["object_names"] = normalized_names
    return {**tool_call, "args": updated_args}


def _tool_validation_message(tool_call: dict[str, Any], message: str) -> ToolMessage:
    tool_name = tool_call.get("name") if isinstance(tool_call.get("name"), str) else "unknown_tool"
    return ToolMessage(
        content=message,
        name=tool_name,
        tool_call_id=_safe_tool_call_id(tool_call),
        status="error",
    )


def _coerce_attached_image_ids_from_state(state: AgentState | Any) -> list[str]:
    if not isinstance(state, dict):
        return []
    raw_value = state.get("attached_image_ids")
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


def _normalize_image_reference_token(raw_value: Any) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", str(raw_value or "").strip().lower())
    return re.sub(r"_+", "_", normalized).strip("_")


def _coerce_request_reference_image_keys_from_state(state: AgentState | Any) -> list[str]:
    if not isinstance(state, dict):
        return []
    raw_value = state.get("request_reference_image_keys")
    if not isinstance(raw_value, list):
        return []
    keys: list[str] = []
    seen: set[str] = set()
    for item in raw_value:
        key = _normalize_image_reference_token(item)
        if not key or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


def _coerce_reference_image_catalog_from_state(state: AgentState | Any) -> dict[str, dict[str, str]]:
    if not isinstance(state, dict):
        return {}
    raw_value = state.get("reference_image_catalog")
    if not isinstance(raw_value, dict):
        return {}
    catalog: dict[str, dict[str, str]] = {}
    for raw_key, raw_entry in raw_value.items():
        if not isinstance(raw_entry, dict):
            continue
        key = _normalize_image_reference_token(raw_key)
        asset_id = str(raw_entry.get("asset_id", "")).strip()
        stored_path = str(raw_entry.get("stored_path", "")).strip()
        if not key or not asset_id or not stored_path:
            continue
        catalog[key] = {
            "asset_id": asset_id,
            "stored_path": stored_path,
        }
    return catalog


def _thread_id_from_state(state: AgentState | Any) -> str:
    if isinstance(state, dict):
        raw_thread_id = state.get("thread_id")
        if isinstance(raw_thread_id, str) and raw_thread_id.strip():
            return raw_thread_id.strip()
    return "default"


def _normalize_optional_string_arg(raw_value: Any) -> str | None:
    if not isinstance(raw_value, str):
        return None
    normalized = raw_value.strip()
    return normalized or None


def _resolved_image_asset_from_memory_asset(
    asset: Any,
    *,
    reference_name: str | None = None,
) -> dict[str, str] | None:
    asset_id = str(getattr(asset, "id", "")).strip()
    stored_path = str(getattr(asset, "stored_path", "")).strip()
    filename = str(getattr(asset, "filename", "")).strip()
    if not asset_id and not stored_path:
        return None
    return {
        "asset_id": asset_id,
        "stored_path": stored_path,
        "filename": filename,
        "reference_name": reference_name or "",
    }


def _resolved_image_asset_from_catalog_entry(
    *,
    reference_name: str,
    entry: dict[str, str],
) -> dict[str, str]:
    return {
        "asset_id": entry["asset_id"],
        "stored_path": entry["stored_path"],
        "filename": "",
        "reference_name": reference_name,
    }


def _resolve_single_attached_image_asset(state: AgentState | Any) -> tuple[dict[str, str] | None, str | None]:
    attached_image_ids = _coerce_attached_image_ids_from_state(state)
    if len(attached_image_ids) != 1:
        return None, None

    thread_id = _thread_id_from_state(state)

    try:
        assets = get_image_asset_memory().get_assets_by_ids(thread_id, attached_image_ids)
    except Exception as exc:
        return None, (
            "Could not resolve the attached request image: "
            f"{str(exc)}"
        )

    if len(assets) != 1:
        return None, "Could not resolve exactly one attached request image."

    resolved = _resolved_image_asset_from_memory_asset(assets[0])
    if resolved is None:
        return None, "Resolved the attached request image, but it had no usable metadata."
    return resolved, None


def _resolve_request_selected_image_assets(state: AgentState | Any) -> list[dict[str, str]]:
    catalog = _coerce_reference_image_catalog_from_state(state)
    selected_keys = _coerce_request_reference_image_keys_from_state(state)
    resolved: list[dict[str, str]] = []
    seen_asset_ids: set[str] = set()
    for key in selected_keys:
        entry = catalog.get(key)
        if entry is None:
            continue
        asset_id = entry["asset_id"]
        if asset_id in seen_asset_ids:
            continue
        seen_asset_ids.add(asset_id)
        resolved.append(_resolved_image_asset_from_catalog_entry(reference_name=key, entry=entry))
    return resolved


def _resolve_explicit_thread_image_asset(
    state: AgentState | Any,
    *,
    input_image_name: str | None,
    input_image_id: str | None,
) -> tuple[dict[str, str] | None, str | None]:
    thread_id = _thread_id_from_state(state)
    catalog = _coerce_reference_image_catalog_from_state(state)
    selected_assets = _resolve_request_selected_image_assets(state)

    if input_image_id:
        target_asset_id = input_image_id.strip()
        if not target_asset_id:
            return None, None
        for asset in selected_assets:
            if asset.get("asset_id") == target_asset_id:
                return asset, None
        for key, entry in catalog.items():
            if entry["asset_id"] == target_asset_id:
                return _resolved_image_asset_from_catalog_entry(reference_name=key, entry=entry), None
        try:
            assets = get_image_asset_memory().get_assets_by_ids(thread_id, [target_asset_id])
        except Exception as exc:
            return None, f"Could not resolve input_image_id '{target_asset_id}': {exc}"
        if len(assets) == 1:
            resolved = _resolved_image_asset_from_memory_asset(assets[0])
            if resolved is not None:
                return resolved, None
        return None, f"Could not resolve input_image_id '{target_asset_id}' to a stored thread image."

    if input_image_name:
        target_name = _normalize_image_reference_token(input_image_name)
        if not target_name:
            return None, None
        for asset in selected_assets:
            if _normalize_image_reference_token(asset.get("reference_name")) == target_name:
                return asset, None
        entry = catalog.get(target_name)
        if entry is not None:
            return _resolved_image_asset_from_catalog_entry(reference_name=target_name, entry=entry), None
        try:
            assets = get_image_asset_memory().list_assets(thread_id)
        except Exception as exc:
            return None, f"Could not resolve input_image_name '{input_image_name}': {exc}"
        matches: list[dict[str, str]] = []
        for asset in assets:
            resolved = _resolved_image_asset_from_memory_asset(asset)
            if resolved is None:
                continue
            filename = resolved.get("filename", "")
            stem = os.path.splitext(filename)[0] if filename else ""
            candidate_tokens = {
                _normalize_image_reference_token(filename),
                _normalize_image_reference_token(stem),
            }
            if target_name in candidate_tokens:
                matches.append(resolved)
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            return None, (
                f"input_image_name '{input_image_name}' matched multiple stored thread images. "
                "Use input_image_id instead."
            )
        return None, f"Could not resolve input_image_name '{input_image_name}' to a stored thread image."

    return None, None


def _resolve_default_request_image_asset(
    state: AgentState | Any,
) -> tuple[dict[str, str] | None, str | None]:
    attached_asset, attached_error = _resolve_single_attached_image_asset(state)
    if attached_asset is not None or attached_error:
        return attached_asset, attached_error

    selected_assets = _resolve_request_selected_image_assets(state)
    if len(selected_assets) == 1:
        return selected_assets[0], None
    if len(selected_assets) > 1:
        return None, (
            "Multiple remembered reference images are active for this request. "
            "Set input_image_name or input_image_id to choose one."
        )
    return None, None


def _resolved_image_asset_to_local_path(
    *,
    tool_name: str,
    asset: dict[str, str],
    arg_name: str,
) -> tuple[str | None, str | None]:
    stored_path = str(asset.get("stored_path", "")).strip()
    if not stored_path:
        return None, f"{tool_name} resolved an image reference, but found no stored path for {arg_name}."
    if not os.path.isfile(stored_path):
        return None, f"{tool_name} resolved an image reference, but the file is missing: {stored_path}"
    return stored_path, None


def _resolve_tool_image_asset(
    state: AgentState | Any,
    *,
    input_image_name: str | None,
    input_image_id: str | None,
) -> tuple[dict[str, str] | None, str | None]:
    explicit_asset, explicit_error = _resolve_explicit_thread_image_asset(
        state,
        input_image_name=input_image_name,
        input_image_id=input_image_id,
    )
    if explicit_asset is not None:
        return explicit_asset, None

    default_asset, default_error = _resolve_default_request_image_asset(state)
    if default_asset is not None:
        return default_asset, None
    if explicit_error:
        return None, explicit_error
    return None, default_error


def _resolve_reconstruct_input_image_path(state: AgentState | Any, raw_args: dict[str, Any]) -> tuple[str | None, str | None]:
    resolved_asset, resolution_error = _resolve_tool_image_asset(
        state,
        input_image_name=_normalize_optional_string_arg(raw_args.get("input_image_name")),
        input_image_id=_normalize_optional_string_arg(raw_args.get("input_image_id")),
    )
    if resolved_asset is None:
        if resolution_error:
            return None, resolution_error
        return None, (
            "reconstruct_full_scene requires exactly one attached image or one request-selected "
            "reference image. When multiple remembered images are active, set input_image_name "
            "or input_image_id."
        )

    return _resolved_image_asset_to_local_path(
        tool_name="reconstruct_full_scene",
        asset=resolved_asset,
        arg_name="input_image_path",
    )


def _normalize_reconstruct_full_scene_request(
    request: ToolCallRequest,
) -> ToolCallRequest | ToolMessage:
    tool_call = request.tool_call
    if not isinstance(tool_call, dict):
        return request
    if tool_call.get("name") != "reconstruct_full_scene":
        return request

    raw_args = tool_call.get("args")
    updated_args = dict(raw_args) if isinstance(raw_args, dict) else {}
    input_image_path, error_message = _resolve_reconstruct_input_image_path(
        request.state,
        updated_args,
    )
    if error_message:
        return _tool_validation_message(tool_call, error_message)

    updated_args["input_image_path"] = input_image_path
    updated_args.pop("input_image_name", None)
    updated_args.pop("input_image_id", None)
    normalized_call = {**tool_call, "args": updated_args}
    return request.override(tool_call=normalized_call)


def _normalize_generate_hunyuan3d_request(
    request: ToolCallRequest,
) -> ToolCallRequest | ToolMessage:
    tool_call = request.tool_call
    if not isinstance(tool_call, dict):
        return request
    if tool_call.get("name") != "generate_hunyuan3d_model":
        return request

    raw_args = tool_call.get("args")
    updated_args = dict(raw_args) if isinstance(raw_args, dict) else {}
    text_prompt = _normalize_optional_string_arg(updated_args.get("text_prompt"))
    if text_prompt:
        return request
    explicit_input_image_url = _normalize_optional_string_arg(updated_args.get("input_image_url"))
    if explicit_input_image_url:
        return request

    resolved_asset, resolution_error = _resolve_tool_image_asset(
        request.state,
        input_image_name=_normalize_optional_string_arg(updated_args.get("input_image_name")),
        input_image_id=_normalize_optional_string_arg(updated_args.get("input_image_id")),
    )
    if resolved_asset is not None:
        input_image_url, path_error = _resolved_image_asset_to_local_path(
            tool_name="generate_hunyuan3d_model",
            asset=resolved_asset,
            arg_name="input_image_url",
        )
        if path_error:
            return _tool_validation_message(tool_call, path_error)
        updated_args["input_image_url"] = input_image_url
        updated_args.pop("input_image_name", None)
        updated_args.pop("input_image_id", None)
        normalized_call = {**tool_call, "args": updated_args}
        return request.override(tool_call=normalized_call)

    if resolution_error:
        return _tool_validation_message(tool_call, resolution_error)
    return _tool_validation_message(
        tool_call,
        "generate_hunyuan3d_model requires text_prompt or a resolvable request image. "
        "Attach one image, use a selected remembered reference image, or provide a valid "
        "input_image_name/input_image_id/input_image_url.",
    )


def _normalize_generate_tripo3d_request(
    request: ToolCallRequest,
) -> ToolCallRequest | ToolMessage:
    tool_call = request.tool_call
    if not isinstance(tool_call, dict):
        return request
    if tool_call.get("name") != "generate_tripo3d_model":
        return request

    raw_args = tool_call.get("args")
    updated_args = dict(raw_args) if isinstance(raw_args, dict) else {}
    text_prompt = _normalize_optional_string_arg(updated_args.get("text_prompt"))
    if text_prompt:
        return request
    explicit_input_image_url = _normalize_optional_string_arg(updated_args.get("input_image_url"))
    if explicit_input_image_url:
        return request

    resolved_asset, resolution_error = _resolve_tool_image_asset(
        request.state,
        input_image_name=_normalize_optional_string_arg(updated_args.get("input_image_name")),
        input_image_id=_normalize_optional_string_arg(updated_args.get("input_image_id")),
    )
    if resolved_asset is not None:
        input_image_url, path_error = _resolved_image_asset_to_local_path(
            tool_name="generate_tripo3d_model",
            asset=resolved_asset,
            arg_name="input_image_url",
        )
        if path_error:
            return _tool_validation_message(tool_call, path_error)
        updated_args["input_image_url"] = input_image_url
        updated_args.pop("input_image_name", None)
        updated_args.pop("input_image_id", None)
        normalized_call = {**tool_call, "args": updated_args}
        return request.override(tool_call=normalized_call)

    if resolution_error:
        return _tool_validation_message(tool_call, resolution_error)
    return _tool_validation_message(
        tool_call,
        "generate_tripo3d_model requires text_prompt or a resolvable request image. "
        "Attach one image, use a selected remembered reference image, or provide a valid "
        "input_image_name/input_image_id/input_image_url.",
    )


def _normalize_generate_hyper3d_via_images_request(
    request: ToolCallRequest,
) -> ToolCallRequest | ToolMessage:
    tool_call = request.tool_call
    if not isinstance(tool_call, dict):
        return request
    if tool_call.get("name") != "generate_hyper3d_model_via_images":
        return request

    raw_args = tool_call.get("args")
    updated_args = dict(raw_args) if isinstance(raw_args, dict) else {}
    raw_paths = updated_args.get("input_image_paths")
    if isinstance(raw_paths, list) and len(raw_paths) > 0:
        return request
    raw_urls = updated_args.get("input_image_urls")
    if isinstance(raw_urls, list) and len(raw_urls) > 0:
        return request

    resolved_asset, resolution_error = _resolve_tool_image_asset(
        request.state,
        input_image_name=_normalize_optional_string_arg(updated_args.get("input_image_name")),
        input_image_id=_normalize_optional_string_arg(updated_args.get("input_image_id")),
    )
    if resolved_asset is not None:
        input_image_path, path_error = _resolved_image_asset_to_local_path(
            tool_name="generate_hyper3d_model_via_images",
            asset=resolved_asset,
            arg_name="input_image_paths",
        )
        if path_error:
            return _tool_validation_message(tool_call, path_error)
        updated_args["input_image_paths"] = [input_image_path]
        updated_args.pop("input_image_urls", None)
        updated_args.pop("input_image_name", None)
        updated_args.pop("input_image_id", None)
        normalized_call = {**tool_call, "args": updated_args}
        return request.override(tool_call=normalized_call)

    if resolution_error:
        return _tool_validation_message(tool_call, resolution_error)
    return _tool_validation_message(
        tool_call,
        "generate_hyper3d_model_via_images requires a resolvable request image. "
        "Attach one image, use a selected remembered reference image, or provide a valid "
        "input_image_name/input_image_id/input_image_paths.",
    )


def _normalize_tool_request(request: ToolCallRequest) -> ToolCallRequest | ToolMessage:
    tool_call = request.tool_call
    if not isinstance(tool_call, dict):
        return request

    reconstruct_request = _normalize_reconstruct_full_scene_request(request)
    if isinstance(reconstruct_request, ToolMessage):
        return reconstruct_request
    request = reconstruct_request
    tool_call = request.tool_call if isinstance(request.tool_call, dict) else tool_call

    hunyuan_request = _normalize_generate_hunyuan3d_request(request)
    if isinstance(hunyuan_request, ToolMessage):
        return hunyuan_request
    request = hunyuan_request
    tool_call = request.tool_call if isinstance(request.tool_call, dict) else tool_call

    tripo_request = _normalize_generate_tripo3d_request(request)
    if isinstance(tripo_request, ToolMessage):
        return tripo_request
    request = tripo_request
    tool_call = request.tool_call if isinstance(request.tool_call, dict) else tool_call

    hyper3d_request = _normalize_generate_hyper3d_via_images_request(request)
    if isinstance(hyper3d_request, ToolMessage):
        return hyper3d_request
    request = hyper3d_request
    tool_call = request.tool_call if isinstance(request.tool_call, dict) else tool_call

    normalized_call = _normalize_tool_call_args(tool_call)
    if normalized_call == tool_call:
        return request
    return request.override(tool_call=normalized_call)


def _tool_error_is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    error_text = str(exc).lower()
    if not error_text:
        return False
    return any(marker in error_text for marker in _RETRYABLE_TOOL_ERROR_MARKERS)


def _safe_tool_call_id(tool_call: dict[str, Any]) -> str:
    tool_call_id = tool_call.get("id")
    if isinstance(tool_call_id, str) and tool_call_id:
        return tool_call_id
    return f"tool_call_{int(time.time() * 1000)}"


def _tool_error_message(tool_name: str, exc: Exception, attempts: int) -> str:
    attempt_word = "attempt" if attempts == 1 else "attempts"
    return f"Error executing tool {tool_name} after {attempts} {attempt_word}: {exc}"


async def _awrap_tool_call_with_retry(
    request: ToolCallRequest,
    execute,
):
    working_request = _normalize_tool_request(request)
    if isinstance(working_request, ToolMessage):
        return working_request
    for attempt in range(1, _TOOL_RETRY_MAX_ATTEMPTS + 1):
        try:
            return await execute(working_request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            raw_tool_call = (
                working_request.tool_call
                if isinstance(working_request.tool_call, dict)
                else request.tool_call
            )
            tool_call = raw_tool_call if isinstance(raw_tool_call, dict) else {}
            tool_name = tool_call.get("name")
            if not isinstance(tool_name, str) or not tool_name:
                tool_name = "unknown_tool"

            should_retry = attempt < _TOOL_RETRY_MAX_ATTEMPTS and _tool_error_is_retryable(exc)
            if not should_retry:
                return ToolMessage(
                    content=_tool_error_message(tool_name, exc, attempt),
                    name=tool_name,
                    tool_call_id=_safe_tool_call_id(tool_call),
                    status="error",
                )

            working_request = _normalize_tool_request(working_request)
            await asyncio.sleep(_TOOL_RETRY_BASE_DELAY_SECONDS * attempt)

    tool_call = request.tool_call if isinstance(request.tool_call, dict) else {}
    tool_name = tool_call.get("name") if isinstance(tool_call.get("name"), str) else "unknown_tool"
    return ToolMessage(
        content=f"Error executing tool {tool_name}: retry budget exhausted.",
        name=tool_name,
        tool_call_id=_safe_tool_call_id(tool_call),
        status="error",
    )


def _wrap_tool_call_with_retry(
    request: ToolCallRequest,
    execute,
):
    import time as _time

    working_request = _normalize_tool_request(request)
    if isinstance(working_request, ToolMessage):
        return working_request
    for attempt in range(1, _TOOL_RETRY_MAX_ATTEMPTS + 1):
        try:
            return execute(working_request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            raw_tool_call = (
                working_request.tool_call
                if isinstance(working_request.tool_call, dict)
                else request.tool_call
            )
            tool_call = raw_tool_call if isinstance(raw_tool_call, dict) else {}
            tool_name = tool_call.get("name")
            if not isinstance(tool_name, str) or not tool_name:
                tool_name = "unknown_tool"

            should_retry = attempt < _TOOL_RETRY_MAX_ATTEMPTS and _tool_error_is_retryable(exc)
            if not should_retry:
                return ToolMessage(
                    content=_tool_error_message(tool_name, exc, attempt),
                    name=tool_name,
                    tool_call_id=_safe_tool_call_id(tool_call),
                    status="error",
                )

            working_request = _normalize_tool_request(working_request)
            _time.sleep(_TOOL_RETRY_BASE_DELAY_SECONDS * attempt)

    tool_call = request.tool_call if isinstance(request.tool_call, dict) else {}
    tool_name = tool_call.get("name") if isinstance(tool_call.get("name"), str) else "unknown_tool"
    return ToolMessage(
        content=f"Error executing tool {tool_name}: retry budget exhausted.",
        name=tool_name,
        tool_call_id=_safe_tool_call_id(tool_call),
        status="error",
    )


async def create_agent_graph(
    session_id: str | None = None,
    *,
    provider_name: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
):
    """
    Create and compile the LangGraph agent.
    """
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
    vlm_provider = get_vlm_provider(
        provider_name=selected_provider,
        api_key=selected_api_key,
        model=selected_model,
    )
    primary_chat_model = vlm_provider.get_chat_model()
    context_summary_model = _build_context_summary_helper_model(
        provider_name=selected_provider,
        api_key=selected_api_key,
        settings=settings,
    )

    verifier_provider_name, verifier_model_name, verifier_api_key = _resolve_dual_agent_verifier_runtime(
        settings=settings,
        default_provider=selected_provider,
        default_model=selected_model,
        default_api_key=selected_api_key,
    )
    verifier_chat_model = primary_chat_model
    verifier_context_summary_model = context_summary_model
    if (
        verifier_provider_name != selected_provider
        or verifier_model_name != selected_model
        or verifier_api_key != selected_api_key
    ):
        try:
            verifier_provider = get_vlm_provider(
                provider_name=verifier_provider_name,
                api_key=verifier_api_key,
                model=verifier_model_name,
            )
            verifier_chat_model = verifier_provider.get_chat_model()
            verifier_context_summary_model = _build_context_summary_helper_model(
                provider_name=verifier_provider_name,
                api_key=verifier_api_key,
                settings=settings,
            )
        except Exception:
            verifier_provider_name = selected_provider
            verifier_model_name = selected_model
            verifier_api_key = selected_api_key
            verifier_chat_model = primary_chat_model
            verifier_context_summary_model = context_summary_model

    tools = await get_blender_tools(session_id=session_id)
    tools.extend(get_internal_agent_tools())
    available_tool_names = [
        tool.name
        for tool in tools
        if hasattr(tool, "name") and isinstance(tool.name, str) and tool.name
    ]
    available_tool_hints: dict[str, str] = {}
    for tool in tools:
        tool_name = getattr(tool, "name", None)
        if not isinstance(tool_name, str) or not tool_name or tool_name in available_tool_hints:
            continue
        hint = _extract_tool_hint(tool)
        if hint:
            available_tool_hints[tool_name] = hint
    public_tool_names = list(available_tool_names)
    public_tool_hints = dict(available_tool_hints)

    llm_with_tools = primary_chat_model.bind_tools(tools)
    verifier_llm_with_tools = verifier_chat_model.bind_tools(tools)

    def call_model(state: AgentState) -> dict:
        return agent_node(
            state,
            llm_with_tools,
            available_tool_names,
            summary_model=context_summary_model,
        )

    def call_builder_model(state: AgentState) -> dict:
        return builder_agent_node(
            state,
            llm_with_tools,
            available_tool_names,
            summary_model=context_summary_model,
        )

    def call_verifier_model(state: AgentState) -> dict:
        return verifier_agent_node(
            state,
            verifier_llm_with_tools,
            available_tool_names,
            summary_model=verifier_context_summary_model,
        )

    def call_router(state: AgentState) -> dict:
        return router_node(state, router_model=primary_chat_model)

    def call_plan(state: AgentState) -> dict:
        return plan_node(state, planner_model=primary_chat_model)

    def call_verifier_feedback(state: AgentState) -> dict:
        return verifier_feedback_node(state, parser_model=verifier_chat_model)

    def call_prepare_reference_context(state: AgentState) -> dict:
        return prepare_reference_context_node(
            state,
            provider_name=selected_provider,
            api_key=selected_api_key,
        )

    def call_sync_reference_catalog(state: AgentState) -> dict:
        return sync_reference_catalog_node(
            state,
            provider_name=selected_provider,
            api_key=selected_api_key,
        )

    builder = build_agent_state_graph(
        initialize_request_node=initialize_request_node,
        sync_reference_catalog_node=call_sync_reference_catalog,
        prepare_reference_context_node=call_prepare_reference_context,
        router_node=call_router,
        plan_node=call_plan,
        agent_node=call_model,
        turn_dispatch_node=turn_dispatch_node,
        builder_agent_node=call_builder_model,
        post_builder_node=post_builder_node,
        verifier_agent_node=call_verifier_model,
        verifier_feedback_node=call_verifier_feedback,
        evaluator_node=evaluator_node,
        tools_node=ToolNode(
            tools,
            handle_tool_errors=False,
            wrap_tool_call=_wrap_tool_call_with_retry,
            awrap_tool_call=_awrap_tool_call_with_retry,
        ),
        update_memory_node=update_memory_node,
        scene_observe_node=scene_observe_node,
        verify_node=lambda state: verify_node(
            state,
            provider_name=selected_provider,
            api_key=selected_api_key,
            model=selected_model,
        ),
        planner_refresh_node=planner_refresh_node,
        finalize_node=lambda state: finalize_node(state, finalizer_model=primary_chat_model),
        route_after_initialize_request=_route_after_initialize_request,
        route_after_sync_reference_catalog=_route_after_sync_reference_catalog,
        route_after_prepare_reference_context=_route_after_prepare_reference_context,
        route_after_router=_route_after_router,
        route_after_plan_node=_route_after_plan_node,
        route_after_turn_dispatch=_route_after_turn_dispatch,
        route_after_post_builder=_route_after_post_builder,
        route_after_verifier_feedback=_route_after_verifier_feedback,
        route_after_update_memory=_route_after_update_memory,
        route_after_evaluator=_route_after_evaluator,
    )

    checkpointer = get_graph_checkpointer()
    app = builder.compile(checkpointer=checkpointer)
    setattr(app, "_available_tool_names", available_tool_names)
    setattr(app, "_available_tool_hints", available_tool_hints)
    setattr(app, "_public_tool_names", public_tool_names)
    setattr(app, "_public_tool_hints", public_tool_hints)
    setattr(app, "_vlm_provider", selected_provider)
    setattr(app, "_vlm_model", selected_model)
    setattr(app, "_verifier_vlm_provider", verifier_provider_name)
    setattr(app, "_verifier_vlm_model", verifier_model_name)
    return app


def create_agent_graph_sync(
    session_id: str | None = None,
    *,
    provider_name: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
):
    """Synchronous wrapper for create_agent_graph."""
    return asyncio.run(
        create_agent_graph(
            session_id=session_id,
            provider_name=provider_name,
            api_key=api_key,
            model=model,
        )
    )
