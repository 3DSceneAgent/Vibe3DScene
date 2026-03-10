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
            "undo_last_snapshot",
        ]
    )

    required_phrases = [
        "0. Scene grounding first (NEVER skip):",
        "Global-to-local order is mandatory",
        "avoid fully sealed rooms/containers",
        "stage as an open shell first",
        "If scene-level views cannot see primary objects because of enclosure",
        "0.5. Automatic scene observation (system-managed):",
        "1. Visual evidence before claims (anti-hallucination rule):",
        "2. Available asset workflows (no status-check tools needed):",
        "3. After every import/generation (REQUIRED):",
        "Ensure at least one scene-level view has clear line-of-sight to main target objects.",
        "5. Multimodal feedback loop (use throughout construction):",
        "6. Only fall back to execute_blender_code() when:",
        "After local refinement, ALWAYS re-render the target object before moving on.",
        "Do not generate ground/floor/entire-scene; create scene parts separately",
        "do NOT build a fully sealed shell",
        "The runtime strategy prompt lists all currently enabled sources and their priority.",
    ]
    for phrase in required_phrases:
        assert phrase in prompt


def test_strategy_includes_undo_guidance_when_tool_available():
    prompt = get_full_system_prompt(
        [
            "get_scene_info",
            "observe_scene_global",
            "undo_last_snapshot",
        ]
    )
    assert "call undo_last_snapshot() immediately." in prompt


def test_strategy_omits_undo_guidance_when_tool_unavailable():
    prompt = get_full_system_prompt(
        [
            "get_scene_info",
            "observe_scene_global",
        ]
    )
    assert "call undo_last_snapshot() immediately." not in prompt


def test_strategy_prefers_clear_scene_reset_when_available():
    prompt = get_full_system_prompt(
        [
            "get_scene_info",
            "observe_scene_global",
            "undo_last_snapshot",
            "clear_scene",
        ]
    )
    assert "clear_scene() -> get_scene_info() -> observe_scene_global()" in prompt


def test_strategy_falls_back_to_delete_objects_reset_when_clear_scene_unavailable():
    prompt = get_full_system_prompt(
        [
            "get_scene_info",
            "observe_scene_global",
            "undo_last_snapshot",
        ]
    )
    assert "delete_objects(all object names, mode=\"cascade\"" in prompt
    assert "clear_scene() -> get_scene_info() -> observe_scene_global()" not in prompt


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
    assert "Global-first modeling principle" in prompt


def test_strategy_includes_blend_import_guidance_for_infinigen():
    prompt = get_full_system_prompt(
        [
            "get_infinigen_available_assets",
            "generate_infinigen_assets",
            "import_blend_contents",
        ]
    )

    assert "import_blend_contents(blend_file_path=\"...\")" in prompt
    assert "Do NOT assume collection name equals asset_type" in prompt


def test_strategy_includes_sam_reconstruct_guidance_when_workflow_is_complete():
    prompt = get_full_system_prompt(
        [
            "reconstruct_full_scene",
            "import_blend_contents",
        ]
    )

    assert "SAM3D full-scene reconstruction (image-only)" in prompt
    assert "reconstruct_full_scene(input_image_path=...)" in prompt
    assert "Do NOT use this tool for text-only requests or single-object generation" in prompt
    assert "fall back to retrieval/generation workflows" in prompt


def test_strategy_requires_blend_import_for_sam_reconstruct_workflow():
    prompt = get_full_system_prompt(
        [
            "reconstruct_full_scene",
        ]
    )

    assert "SAM3D full-scene reconstruction (image-only)" not in prompt


def test_strategy_includes_imported_hierarchy_guidance_without_exposing_extra_tool():
    prompt = get_full_system_prompt(
        [
            "get_scene_info",
            "get_object_info",
            "delete_objects",
        ]
    )

    assert "compare world_scale vs local scale" in prompt
    assert "detach_keep_world" in prompt
    assert "flatten_hierarchy(" not in prompt


def test_mcp_strategy_includes_sam_reconstruct_when_runtime_ready(monkeypatch):
    monkeypatch.setattr(
        strategy_module.runtime,
        "probe_conditional_services",
        lambda _logger: {
            "trellis2": False,
            "retrieval": False,
            "pcg_integrator": False,
            "sam_reconstruct": True,
        },
    )
    monkeypatch.setattr(strategy_module.runtime, "is_sketchfab_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "get_sketchfab_api_key", lambda: "")
    monkeypatch.setattr(strategy_module.runtime, "is_infinigen_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "is_trellis2_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "is_rodin_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "get_rodin_api_key", lambda: "")
    monkeypatch.setattr(strategy_module.runtime, "is_hunyuan_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "is_retrieval_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "is_sam_reconstruct_tool_enabled", lambda: True)

    prompt = strategy_module.asset_creation_strategy_text()

    assert "SAM3D full-scene reconstruction (image-only)" in prompt


def test_mcp_strategy_omits_sketchfab_when_api_unreachable(monkeypatch):
    monkeypatch.setattr(
        strategy_module.runtime,
        "probe_conditional_services",
        lambda _logger: {
            "trellis2": False,
            "retrieval": False,
            "pcg_integrator": False,
            "sam_reconstruct": False,
        },
    )
    monkeypatch.setattr(strategy_module.runtime, "is_sketchfab_tool_enabled", lambda: True)
    monkeypatch.setattr(strategy_module.runtime, "get_sketchfab_api_key", lambda: "configured")
    monkeypatch.setattr(strategy_module.runtime, "probe_sketchfab_api", lambda _logger: False)
    monkeypatch.setattr(strategy_module.runtime, "is_infinigen_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "is_trellis2_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "is_rodin_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "get_rodin_api_key", lambda: "")
    monkeypatch.setattr(strategy_module.runtime, "is_hunyuan_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "is_retrieval_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "is_sam_reconstruct_tool_enabled", lambda: False)

    prompt = strategy_module.asset_creation_strategy_text()

    assert "Sketchfab (server-side)" not in prompt
