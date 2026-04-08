from __future__ import annotations

import copy
import time
from typing import Any
from uuid import uuid4

from langchain_core.callbacks.base import BaseCallbackHandler


def _coerce_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        candidate = int(value)
        return candidate if candidate >= 0 else None
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.isdigit():
            return int(normalized)
    return None


def _resolve_provider_name(model: Any) -> str | None:
    module_name = str(getattr(type(model), "__module__", "") or "")
    if "langchain_google_genai" in module_name:
        return "gemini"
    if "langchain_qwq" in module_name:
        return "qwen"
    if "langchain_openai" in module_name:
        return "openai"
    if "langchain_anthropic" in module_name:
        return "anthropic"
    bound = getattr(model, "bound", None)
    if bound is not None and bound is not model:
        return _resolve_provider_name(bound)
    return None


def resolve_model_name(model: Any) -> str | None:
    for attr_name in ("model_name", "model"):
        raw_value = getattr(model, attr_name, None)
        if isinstance(raw_value, str) and raw_value.strip():
            return raw_value.strip()
    bound = getattr(model, "bound", None)
    if bound is not None and bound is not model:
        return resolve_model_name(bound)
    return None


def _unwrap_bound_model(model: Any) -> Any:
    bound = getattr(model, "bound", None)
    if bound is not None and bound is not model:
        return _unwrap_bound_model(bound)
    return model


def resolve_context_limit_tokens(model: Any) -> int | None:
    profile = getattr(model, "profile", None)
    if isinstance(profile, dict):
        profile_tokens = _coerce_positive_int(profile.get("max_input_tokens"))
        if profile_tokens is not None:
            return profile_tokens

    for attr_name in ("max_context_size", "max_input_tokens", "context_window"):
        raw_value = getattr(model, attr_name, None)
        if callable(raw_value):
            try:
                raw_value = raw_value()
            except Exception:
                raw_value = None
        resolved = _coerce_positive_int(raw_value)
        if resolved is not None:
            return resolved

    bound = getattr(model, "bound", None)
    if bound is not None and bound is not model:
        return resolve_context_limit_tokens(bound)
    return None


def _recursive_find_int(value: Any, keys: set[str], *, depth: int = 0) -> int | None:
    if depth > 6:
        return None
    if isinstance(value, dict):
        for key in keys:
            resolved = _coerce_positive_int(value.get(key))
            if resolved is not None:
                return resolved
        for nested in value.values():
            resolved = _recursive_find_int(nested, keys, depth=depth + 1)
            if resolved is not None:
                return resolved
    elif isinstance(value, list):
        for item in value:
            resolved = _recursive_find_int(item, keys, depth=depth + 1)
            if resolved is not None:
                return resolved
    return None


def _extract_usage_metadata_from_value(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, list):
        for item in value:
            resolved = _extract_usage_metadata_from_value(item)
            if resolved is not None:
                return resolved
        return None
    if isinstance(value, dict):
        if any(key in value for key in ("input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens")):
            return value
        for key in ("usage_metadata", "token_usage", "usage", "llm_output", "response_metadata"):
            nested = value.get(key)
            if isinstance(nested, dict):
                resolved = _extract_usage_metadata_from_value(nested)
                if resolved is not None:
                    return resolved
        for nested in value.values():
            resolved = _extract_usage_metadata_from_value(nested)
            if resolved is not None:
                return resolved
        return None

    usage_metadata = getattr(value, "usage_metadata", None)
    if isinstance(usage_metadata, dict):
        return usage_metadata
    message = getattr(value, "message", None)
    if message is not None:
        resolved = _extract_usage_metadata_from_value(message)
        if resolved is not None:
            return resolved
    generations = getattr(value, "generations", None)
    if isinstance(generations, list):
        resolved = _extract_usage_metadata_from_value(generations)
        if resolved is not None:
            return resolved
    response_metadata = getattr(value, "response_metadata", None)
    if isinstance(response_metadata, dict):
        resolved = _extract_usage_metadata_from_value(response_metadata)
        if resolved is not None:
            return resolved
    llm_output = getattr(value, "llm_output", None)
    if isinstance(llm_output, dict):
        resolved = _extract_usage_metadata_from_value(llm_output)
        if resolved is not None:
            return resolved
    return None


def _extract_usage_fields(value: Any) -> dict[str, int | None]:
    usage = _extract_usage_metadata_from_value(value)
    if not isinstance(usage, dict):
        return {
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "image_input_tokens": None,
        }

    input_tokens = _recursive_find_int(
        usage,
        {"input_tokens", "prompt_tokens", "prompt_token_count", "input_token_count"},
    )
    output_tokens = _recursive_find_int(
        usage,
        {"output_tokens", "completion_tokens", "completion_token_count", "candidates_token_count"},
    )
    total_tokens = _recursive_find_int(
        usage,
        {"total_tokens", "total_token_count"},
    )
    image_input_tokens = _recursive_find_int(
        usage,
        {"image_input_tokens", "image_tokens", "image_token_count"},
    )
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "image_input_tokens": image_input_tokens,
    }


def _usage_fields_have_known_tokens(usage_fields: dict[str, int | None] | None) -> bool:
    if not isinstance(usage_fields, dict):
        return False
    return any(
        usage_fields.get(key) is not None
        for key in ("input_tokens", "output_tokens", "total_tokens", "image_input_tokens")
    )


def _content_has_image_inputs(content: Any) -> bool:
    if isinstance(content, list):
        return any(_content_has_image_inputs(item) for item in content)
    if not isinstance(content, dict):
        return False
    block_type = str(content.get("type") or "").strip().lower()
    if "image" in block_type:
        return True
    if "image_url" in content:
        return True
    nested = content.get("value")
    if nested is not None:
        return _content_has_image_inputs(nested)
    return False


def messages_have_image_inputs(messages: Any) -> bool:
    if not isinstance(messages, list):
        return False
    for message in messages:
        if _content_has_image_inputs(getattr(message, "content", None)):
            return True
    return False


def _strip_image_blocks_from_content(content: Any) -> Any:
    if isinstance(content, list):
        stripped: list[Any] = []
        for item in content:
            if isinstance(item, dict):
                item_type = str(item.get("type") or "").strip().lower()
                if "image" in item_type or "image_url" in item:
                    continue
                nested = item.get("value")
                if nested is not None:
                    next_item = dict(item)
                    next_item["value"] = _strip_image_blocks_from_content(nested)
                    stripped.append(next_item)
                    continue
            stripped.append(copy.deepcopy(item))
        return stripped
    return copy.deepcopy(content)


def strip_image_inputs_from_messages(messages: list[Any]) -> list[Any]:
    stripped_messages: list[Any] = []
    for message in messages:
        cloned = copy.deepcopy(message)
        try:
            cloned.content = _strip_image_blocks_from_content(getattr(cloned, "content", None))
        except Exception:
            pass
        stripped_messages.append(cloned)
    return stripped_messages


def _get_num_tokens_from_messages(model: Any, messages: list[Any]) -> int | None:
    counter = getattr(model, "get_num_tokens_from_messages", None)
    if callable(counter):
        try:
            raw_value = counter(messages)
        except Exception:
            raw_value = None
        resolved = _coerce_positive_int(raw_value)
        if resolved is not None:
            return resolved
    bound = getattr(model, "bound", None)
    if bound is not None and bound is not model:
        return _get_num_tokens_from_messages(bound, messages)
    return None


def _count_gemini_tokens_from_messages(model: Any, messages: list[Any]) -> int | None:
    if not isinstance(messages, list):
        return None

    resolved_model = _unwrap_bound_model(model)
    model_name = resolve_model_name(resolved_model)
    client = getattr(resolved_model, "client", None)
    if not isinstance(model_name, str) or not model_name.strip() or client is None:
        return None

    try:
        from langchain_google_genai.chat_models import _parse_chat_history
    except Exception:
        return None

    try:
        system_instruction, contents = _parse_chat_history(messages, model=model_name)
    except Exception:
        return None

    config: dict[str, Any] | None = None
    if system_instruction is not None:
        config = {"system_instruction": system_instruction}

    try:
        response = client.models.count_tokens(
            model=model_name,
            contents=contents,
            config=config,
        )
    except Exception:
        return None

    for key in ("total_tokens", "totalTokens", "token_count", "tokenCount"):
        resolved = _coerce_positive_int(getattr(response, key, None))
        if resolved is not None:
            return resolved
    if isinstance(response, dict):
        for key in ("total_tokens", "totalTokens", "token_count", "tokenCount"):
            resolved = _coerce_positive_int(response.get(key))
            if resolved is not None:
                return resolved
    return None


def resolve_image_input_tokens(
    *,
    provider_name: str | None,
    model: Any,
    messages: Any,
    image_input_tokens: int | None,
) -> tuple[int | None, bool]:
    has_images = messages_have_image_inputs(messages)
    if not has_images:
        return 0, False
    if image_input_tokens is not None:
        return image_input_tokens, True
    if str(provider_name or "").strip().lower() != "gemini" or not isinstance(messages, list):
        return None, True

    full_count = _count_gemini_tokens_from_messages(model, messages)
    if full_count is None:
        return None, True
    text_only_messages = strip_image_inputs_from_messages(messages)
    text_only_count = _count_gemini_tokens_from_messages(model, text_only_messages)
    if text_only_count is None:
        return None, True
    return max(0, full_count - text_only_count), True


class UsageCaptureHandler(BaseCallbackHandler):
    def __init__(self) -> None:
        super().__init__()
        self.response: Any = None
        self.usage_fields: dict[str, int | None] | None = None

    def on_llm_end(self, response: Any, **_kwargs: Any) -> Any:
        self.response = response
        self.usage_fields = _extract_usage_fields(response)
        return None


def _invoke_with_callback(model: Any, invoke_input: Any, handler: UsageCaptureHandler) -> Any:
    if hasattr(model, "with_config"):
        try:
            configured = model.with_config(callbacks=[handler])
            return configured.invoke(invoke_input)
        except Exception:
            pass
    try:
        return model.invoke(invoke_input, config={"callbacks": [handler]})
    except TypeError:
        return model.invoke(invoke_input)


def build_llm_call_record(
    *,
    model: Any,
    provider_name: str | None,
    model_name: str | None,
    thread_id: str,
    turn_id: str | None,
    node_name: str,
    call_role: str,
    usage_fields: dict[str, int | None] | None,
    invoke_input: Any,
) -> dict[str, Any]:
    resolved_provider = (provider_name or _resolve_provider_name(model) or "").strip().lower() or None
    resolved_model = model_name or resolve_model_name(model)
    usage = usage_fields or {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "image_input_tokens": None,
    }
    image_input_tokens, has_image_inputs = resolve_image_input_tokens(
        provider_name=resolved_provider,
        model=model,
        messages=invoke_input,
        image_input_tokens=usage.get("image_input_tokens"),
    )
    return {
        "call_id": f"llm_call_{uuid4().hex}",
        "thread_id": thread_id,
        "turn_id": turn_id,
        "node_name": node_name,
        "call_role": call_role,
        "provider": resolved_provider,
        "model": resolved_model,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "image_input_tokens": image_input_tokens,
        "has_image_inputs": has_image_inputs,
        "context_limit_tokens": resolve_context_limit_tokens(model),
        "created_at_ms": int(time.time() * 1000),
    }


def invoke_with_metrics(
    model: Any,
    invoke_input: Any,
    *,
    thread_id: str,
    turn_id: str | None,
    node_name: str,
    call_role: str,
    provider_name: str | None = None,
    model_name: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    handler = UsageCaptureHandler()
    response = _invoke_with_callback(model, invoke_input, handler)
    if _usage_fields_have_known_tokens(handler.usage_fields):
        usage_fields = handler.usage_fields
    else:
        usage_fields = _extract_usage_fields(response)
    record = build_llm_call_record(
        model=model,
        provider_name=provider_name,
        model_name=model_name,
        thread_id=thread_id,
        turn_id=turn_id,
        node_name=node_name,
        call_role=call_role,
        usage_fields=usage_fields,
        invoke_input=invoke_input,
    )
    return response, record


def invoke_structured_with_metrics(
    model: Any,
    schema: Any,
    invoke_input: Any,
    *,
    thread_id: str,
    turn_id: str | None,
    node_name: str,
    call_role: str,
    provider_name: str | None = None,
    model_name: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    structured_model = model
    if hasattr(model, "with_structured_output"):
        structured_model = model.with_structured_output(schema)
    response, record = invoke_with_metrics(
        structured_model,
        invoke_input,
        thread_id=thread_id,
        turn_id=turn_id,
        node_name=node_name,
        call_role=call_role,
        provider_name=provider_name or _resolve_provider_name(model),
        model_name=model_name or resolve_model_name(model),
    )
    if record.get("context_limit_tokens") is None:
        record["context_limit_tokens"] = resolve_context_limit_tokens(model)
    if isinstance(response, schema):
        parsed = response
    else:
        parsed = schema.model_validate(response)
    return parsed, record
