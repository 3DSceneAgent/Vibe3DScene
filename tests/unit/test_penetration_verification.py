from __future__ import annotations

import pytest
from pydantic import ValidationError

from scene_agent.config import reload_settings
from scene_agent.penetration_verification import (
    build_penetration_reason,
    merge_verification_with_penetration,
    normalize_penetration_check_payload,
)


def test_penetration_settings_parse_env_values(monkeypatch):
    monkeypatch.setenv("SCENE_AGENT_ENABLE_PENETRATION_VERIFY", "true")
    monkeypatch.setenv("SCENE_AGENT_PENETRATION_THRESHOLD_M", "0.01")
    monkeypatch.setenv("SCENE_AGENT_PENETRATION_MAX_CANDIDATE_PAIRS", "24")
    monkeypatch.setenv("SCENE_AGENT_PENETRATION_MAX_REPORTED_PAIRS", "6")

    settings = reload_settings()

    assert settings.enable_penetration_verify is True
    assert settings.penetration_threshold_m == 0.01
    assert settings.penetration_max_candidate_pairs == 24
    assert settings.penetration_max_reported_pairs == 6

    monkeypatch.delenv("SCENE_AGENT_ENABLE_PENETRATION_VERIFY", raising=False)
    monkeypatch.delenv("SCENE_AGENT_PENETRATION_THRESHOLD_M", raising=False)
    monkeypatch.delenv("SCENE_AGENT_PENETRATION_MAX_CANDIDATE_PAIRS", raising=False)
    monkeypatch.delenv("SCENE_AGENT_PENETRATION_MAX_REPORTED_PAIRS", raising=False)
    reload_settings()


def test_penetration_settings_fail_fast_on_invalid_env_values(monkeypatch):
    monkeypatch.setenv("SCENE_AGENT_ENABLE_PENETRATION_VERIFY", "maybe")
    monkeypatch.setenv("SCENE_AGENT_PENETRATION_THRESHOLD_M", "-1")
    monkeypatch.setenv("SCENE_AGENT_PENETRATION_MAX_CANDIDATE_PAIRS", "0")
    monkeypatch.setenv("SCENE_AGENT_PENETRATION_MAX_REPORTED_PAIRS", "invalid")

    with pytest.raises(ValidationError):
        reload_settings()

    monkeypatch.delenv("SCENE_AGENT_ENABLE_PENETRATION_VERIFY", raising=False)
    monkeypatch.delenv("SCENE_AGENT_PENETRATION_THRESHOLD_M", raising=False)
    monkeypatch.delenv("SCENE_AGENT_PENETRATION_MAX_CANDIDATE_PAIRS", raising=False)
    monkeypatch.delenv("SCENE_AGENT_PENETRATION_MAX_REPORTED_PAIRS", raising=False)
    reload_settings()


def test_normalize_penetration_check_payload_builds_default_summary():
    payload = normalize_penetration_check_payload(
        {
            "enabled": True,
            "has_penetration": True,
            "pairs": [
                {
                    "object_a": "Chair",
                    "object_b": "Table",
                    "depth_m": 0.014,
                    "stage": "narrow_phase",
                    "category": "mesh_intersection",
                }
            ],
            "pair_count": 1,
        },
        enabled=True,
    )

    assert payload["has_penetration"] is True
    assert payload["pair_count"] == 1
    assert payload["summary"] == "Detected 1 penetration pair(s): Chair vs Table (1.4cm)."


def test_merge_verification_with_penetration_downgrades_done_status():
    verification_result, normalized_payload = merge_verification_with_penetration(
        {
            "status": "done",
            "reason": "Looks good.",
            "edit_suggestions": ["Adjust lamp rotation."],
        },
        {
            "enabled": True,
            "has_penetration": True,
            "pair_count": 1,
            "pairs": [
                {
                    "object_a": "Chair",
                    "object_b": "Table",
                    "depth_m": 0.014,
                    "stage": "narrow_phase",
                    "category": "mesh_intersection",
                }
            ],
        },
    )

    assert verification_result["status"] == "working"
    assert normalized_payload["reason"] == (
        "Looks good. Geometry check: "
        + build_penetration_reason(
            {
                "pair_count": 1,
                "pairs": [
                    {
                        "object_a": "Chair",
                        "object_b": "Table",
                        "depth_m": 0.014,
                    }
                ],
            }
        )
    )
    assert normalized_payload["edit_suggestions"] == [
        "Adjust lamp rotation.",
        "Separate Chair and Table to remove the mesh intersection."
    ]
