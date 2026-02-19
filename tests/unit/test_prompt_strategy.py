import mcp_server.tools.strategy as strategy_module

from scene_agent.agent.prompts import get_full_system_prompt


def test_get_full_system_prompt_reflects_runtime_available_workflows():
    prompt = get_full_system_prompt(
        [
            "get_scene_info",
            "search_3d_assets_by_text",
            "import_retrieved_asset",
        ]
    )

    assert "3D Asset Retrieval Database" in prompt
    assert "Sketchfab (server-side)" not in prompt
    assert "TRELLIS2 (headless)" not in prompt


def test_get_full_system_prompt_reports_when_only_polyhaven_available():
    prompt = get_full_system_prompt(
        [
            "get_scene_info",
            "search_polyhaven_assets",
            "download_polyhaven_asset",
            "set_texture",
        ]
    )

    assert "No extra generator/retrieval workflow is currently available beyond PolyHaven." in prompt


def test_get_full_system_prompt_preserves_legacy_strategy_guidance():
    prompt = get_full_system_prompt(
        [
            "get_scene_info",
            "observe_scene_global",
            "render_from_camera",
            "render_from_objects",
            "camera_act",
            "camera_observe",
            "camera_set_pose",
            "get_object_info",
            "execute_blender_code",
            "delete_objects",
            "search_polyhaven_assets",
            "download_polyhaven_asset",
            "set_texture",
            "generate_trellis2_model",
            "search_3d_assets_by_text",
            "import_retrieved_asset",
        ]
    )

    required_phrases = [
        "0. Scene grounding first (NEVER skip):",
        "0.5. Automatic scene observation (system-managed):",
        "1. Visual evidence before claims (anti-hallucination rule):",
        "2. Available asset workflows (no status-check tools needed):",
        "3. After every import/generation (REQUIRED):",
        "5. Multimodal feedback loop (use throughout construction):",
        "6. Only fall back to execute_blender_code() when:",
        "After local refinement, ALWAYS re-render the target object before moving on.",
        "Do not generate ground/floor/entire-scene; create scene parts separately",
        "The runtime strategy prompt lists all currently enabled sources and their priority.",
    ]
    for phrase in required_phrases:
        assert phrase in prompt


def test_mcp_strategy_skips_runtime_probe_with_explicit_tool_list(monkeypatch):
    def fail_if_called(_logger):
        raise AssertionError("runtime probe should not be called with explicit tool list")

    monkeypatch.setattr(strategy_module.runtime, "probe_conditional_services", fail_if_called)

    prompt = strategy_module.asset_creation_strategy_text(
        [
            "search_3d_assets_by_text",
            "import_retrieved_asset",
        ]
    )

    assert "3D Asset Retrieval Database" in prompt
