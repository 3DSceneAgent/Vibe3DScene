"""
Helpers for scoped workflow memory updates.
"""
from __future__ import annotations

from typing import Any

from scene_agent.agent.workflow_profiles import MemoryProfile, MemoryProfileRequest


def normalize_memory_profile_request(raw_value: Any) -> MemoryProfileRequest:
    if not isinstance(raw_value, str):
        return "auto"
    normalized = raw_value.strip().lower().replace("-", "_")
    if normalized == "thread_shared_only":
        return "thread_shared_only"
    if normalized == "shared_plus_role_private":
        return "shared_plus_role_private"
    return "auto"


def resolve_memory_profile(raw_request: Any) -> MemoryProfile:
    request = normalize_memory_profile_request(raw_request)
    if request == "shared_plus_role_private":
        return "shared_plus_role_private"
    return "thread_shared_only"


def merge_role_private_memory(
    existing: dict[str, dict[str, Any]] | None,
    *,
    role: str,
    patch: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    current: dict[str, dict[str, Any]] = {}
    if isinstance(existing, dict):
        for raw_role, raw_payload in existing.items():
            if not isinstance(raw_role, str):
                continue
            if isinstance(raw_payload, dict):
                current[raw_role] = dict(raw_payload)
            else:
                current[raw_role] = {}

    role_key = role.strip() if isinstance(role, str) else ""
    if not role_key:
        return current

    role_bucket = dict(current.get(role_key, {}))
    for key, value in patch.items():
        if not isinstance(key, str) or not key:
            continue
        role_bucket[key] = value
    current[role_key] = role_bucket
    return current
