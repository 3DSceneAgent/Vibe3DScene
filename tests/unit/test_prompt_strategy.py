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
    assert "do not reject a candidate solely from its text label" in prompt
    assert "import the best candidate first." in prompt
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


def test_get_full_system_prompt_omits_polyhaven_guidance_when_unavailable():
    prompt = get_full_system_prompt(
        [
            "get_scene_info",
            "execute_blender_code",
        ]
    )

    assert "PolyHaven" not in prompt
    assert "No asset-library or generator workflow is currently enabled" in prompt


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


def test_get_full_system_prompt_includes_fast_mode_override():
    prompt = get_full_system_prompt(
        [
            "get_scene_info",
            "observe_scene_global",
            "render_from_objects",
        ],
        fast_mode=True,
    )

    assert "Current request override:" in prompt
    assert "Fast mode is enabled for this request." in prompt
    assert "Planner decomposition is disabled for this request" in prompt
    assert "automatic verification is skipped" in prompt
    assert "automatic scene observation is skipped" in prompt
    assert "collect at least one fresh piece of evidence before you stop" in prompt


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


def test_strategy_includes_scenesmith_specific_workflows_when_tools_are_available():
    prompt = get_full_system_prompt(
        [
            "search_hssd_assets",
            "import_hssd_asset",
            "search_ambientcg_materials",
            "apply_ambientcg_material",
        ]
    )

    assert "SceneSmith HSSD Retrieval" in prompt
    assert "search_hssd_assets(query=..., object_type=..., top_k=..., desired_dimensions_m=...)" in prompt
    assert "Prefer importing a plausible candidate instead of judging only from returned names/categories." in prompt
    assert "SceneSmith AmbientCG Materials" in prompt
    assert "apply_ambientcg_material(object_name=..., color_url=..., normal_url=..., roughness_url=...)" in prompt
    assert "Use the texture URLs returned by search_ambientcg_materials() directly." in prompt
    assert "Do NOT manually download the package zip or route AmbientCG maps through set_texture()." in prompt


def test_strategy_includes_sam_reconstruct_guidance_when_workflow_is_complete():
    prompt = get_full_system_prompt(
        [
            "reconstruct_full_scene",
            "import_blend_contents",
        ]
    )

    assert "SAM3D full-scene reconstruction (image-only)" in prompt
    assert "reconstruct_full_scene(input_image_path=...)" in prompt
    assert "auto-resolve it for reconstruction" in prompt
    assert "input_image_name=... or input_image_id=..." in prompt
    assert "Do NOT use this tool for text-only requests or single-object generation" in prompt
    assert "fall back to retrieval/generation workflows" in prompt


def test_strategy_includes_image_referenced_hunyuan_comparison_guidance():
    prompt = get_full_system_prompt(
        [
            "get_scene_info",
            "search_3d_assets_by_text",
            "import_retrieved_asset",
            "generate_hunyuan3d_model",
            "import_glb_model",
            "execute_blender_code",
        ]
    )

    assert "input_image_name=... or input_image_id=..." in prompt
    assert "auto-resolve it for image-to-3D" in prompt
    assert "Hunyuan often returns the model as Type=OBJ with a .zip bundle" in prompt
    assert "prefer preferred_model_asset.url when present" in prompt
    assert "select the Type=OBJ ResultFile3Ds URL" in prompt
    assert "import_glb_model(model_url=..., object_name=...)" in prompt
    assert "compare retrieval vs image-conditioned generation from one reference image" in prompt
    assert "place them side by side" in prompt


def test_strategy_includes_tripo_guidance_for_text_and_image_generation():
    prompt = get_full_system_prompt(
        [
            "get_scene_info",
            "search_3d_assets_by_text",
            "import_retrieved_asset",
            "generate_tripo3d_model",
            "import_glb_model",
            "execute_blender_code",
        ]
    )

    assert "Tripo" in prompt
    assert "P1-20260311" in prompt
    assert "input_image_name=... or input_image_id=..." in prompt
    assert "auto-resolve it for image-to-3D" in prompt
    assert "preferred_model_asset.url" in prompt
    assert "import_glb_model(model_url=..., object_name=...)" in prompt
    assert "compare retrieval vs image-conditioned generation from one reference image" in prompt


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
            "objaverse_retrieval": False,
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
    monkeypatch.setattr(strategy_module.runtime, "is_tripo_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "get_tripo_api_key", lambda: "")
    monkeypatch.setattr(strategy_module.runtime, "is_hunyuan_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "is_retrieval_tool_enabled", lambda: False)
    monkeypatch.setattr(
        strategy_module.runtime, "is_objaverse_retrieval_tool_enabled", lambda: False
    )
    monkeypatch.setattr(strategy_module.runtime, "is_sam_reconstruct_tool_enabled", lambda: True)

    prompt = strategy_module.asset_creation_strategy_text()

    assert "SAM3D full-scene reconstruction (image-only)" in prompt


def test_mcp_strategy_omits_sketchfab_when_api_unreachable(monkeypatch):
    monkeypatch.setattr(
        strategy_module.runtime,
        "probe_conditional_services",
        lambda _logger: {
            "trellis2": False,
            "objaverse_retrieval": False,
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
    monkeypatch.setattr(strategy_module.runtime, "is_tripo_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "get_tripo_api_key", lambda: "")
    monkeypatch.setattr(strategy_module.runtime, "is_hunyuan_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "is_retrieval_tool_enabled", lambda: False)
    monkeypatch.setattr(
        strategy_module.runtime, "is_objaverse_retrieval_tool_enabled", lambda: False
    )
    monkeypatch.setattr(strategy_module.runtime, "is_sam_reconstruct_tool_enabled", lambda: False)

    prompt = strategy_module.asset_creation_strategy_text()

    assert "Sketchfab (server-side)" not in prompt


def test_mcp_strategy_omits_polyhaven_when_runtime_disabled(monkeypatch):
    monkeypatch.setattr(
        strategy_module.runtime,
        "probe_conditional_services",
        lambda _logger: {
            "trellis2": False,
            "objaverse_retrieval": False,
            "pcg_integrator": False,
            "sam_reconstruct": False,
        },
    )
    monkeypatch.setattr(strategy_module.runtime, "is_polyhaven_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "is_sketchfab_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "get_sketchfab_api_key", lambda: "")
    monkeypatch.setattr(strategy_module.runtime, "is_infinigen_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "is_trellis2_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "is_rodin_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "get_rodin_api_key", lambda: "")
    monkeypatch.setattr(strategy_module.runtime, "is_tripo_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "get_tripo_api_key", lambda: "")
    monkeypatch.setattr(strategy_module.runtime, "is_hunyuan_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "is_retrieval_tool_enabled", lambda: False)
    monkeypatch.setattr(
        strategy_module.runtime, "is_objaverse_retrieval_tool_enabled", lambda: False
    )
    monkeypatch.setattr(strategy_module.runtime, "is_sam_reconstruct_tool_enabled", lambda: False)

    prompt = strategy_module.asset_creation_strategy_text()

    assert "PolyHaven" not in prompt
    assert "No asset-library or generator workflow is currently enabled" in prompt


def test_mcp_strategy_respects_independent_scenesmith_subservice_readiness(monkeypatch):
    monkeypatch.setattr(
        strategy_module.runtime,
        "probe_conditional_services",
        lambda _logger: {
            "trellis2": False,
            "objaverse_retrieval": False,
            "scenesmith_hssd": True,
            "scenesmith_ambientcg": False,
            "pcg_integrator": False,
            "sam_reconstruct": False,
        },
    )
    monkeypatch.setattr(strategy_module.runtime, "is_sketchfab_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "get_sketchfab_api_key", lambda: "")
    monkeypatch.setattr(strategy_module.runtime, "is_infinigen_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "is_trellis2_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "is_rodin_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "get_rodin_api_key", lambda: "")
    monkeypatch.setattr(strategy_module.runtime, "is_tripo_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "get_tripo_api_key", lambda: "")
    monkeypatch.setattr(strategy_module.runtime, "is_hunyuan_tool_enabled", lambda: False)
    monkeypatch.setattr(strategy_module.runtime, "is_retrieval_tool_enabled", lambda: True)
    monkeypatch.setattr(
        strategy_module.runtime, "is_objaverse_retrieval_tool_enabled", lambda: False
    )
    monkeypatch.setattr(strategy_module.runtime, "is_scenesmith_hssd_tool_enabled", lambda: True)
    monkeypatch.setattr(
        strategy_module.runtime, "is_scenesmith_ambientcg_tool_enabled", lambda: True
    )
    monkeypatch.setattr(strategy_module.runtime, "is_sam_reconstruct_tool_enabled", lambda: False)

    prompt = strategy_module.asset_creation_strategy_text()

    assert "3D Asset Retrieval Database" not in prompt
    assert "SceneSmith HSSD Retrieval" in prompt
    assert "SceneSmith AmbientCG Materials" not in prompt
