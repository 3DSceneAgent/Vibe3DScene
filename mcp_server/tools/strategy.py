from __future__ import annotations

import logging

from mcp_server import runtime

logger = logging.getLogger("BlenderMCPServer")


def asset_creation_strategy_text() -> str:
    service_status = runtime.probe_conditional_services(logger)

    sketchfab_ready = runtime.is_sketchfab_tool_enabled() and bool(runtime.get_sketchfab_api_key())
    infinigen_ready = runtime.is_infinigen_tool_enabled() and service_status.get("pcg_integrator", False)
    trellis2_ready = runtime.is_trellis2_tool_enabled() and service_status.get("trellis2", False)
    rodin_ready = runtime.is_rodin_tool_enabled() and bool(runtime.get_rodin_api_key())
    hunyuan_ready = runtime.is_hunyuan_tool_enabled()
    retrieval_ready = runtime.is_retrieval_tool_enabled() and service_status.get("retrieval", False)

    # ── Phase 0: Scene grounding ──────────────────────────────────────
    lines: list[str] = [
        "When creating or editing a 3D scene, follow this execution playbook:",
        "",
        "0. Scene grounding first (NEVER skip):",
        "   - Run get_scene_info() to understand existing objects and scene scale.",
        "   - If scene is visually complex, run get_viewport_screenshot() for a quick global snapshot.",
        "",
    ]

    # ── Phase 0.5: Automatic scene observation ────────────────────────
    lines.extend(
        [
            "0.5. Automatic scene observation (system-managed):",
            "   - After every scene mutation (import, generate, execute_blender_code, set_texture),",
            "     4 scene-level cameras auto-update and render. You will see a multi-view composite.",
            "   - These cameras track the full scene bounding box — do NOT modify them manually.",
            "   - For object-level inspection, use camera_act() and camera_observe().",
            "   - After local refinement, ALWAYS re-render the target object before moving on.",
            "",
        ]
    )

    # ── Phase 1: Visual evidence & camera workflow ────────────────────
    lines.extend(
        [
            "1. Visual evidence before claims (anti-hallucination rule):",
            "   - Review the automatic multi-view renders for global composition issues.",
            "   - For targeted inspection around one object:",
            "       camera_act(action=\"focus\", object_names=[...])",
            "       camera_act(action=\"move\", direction=\"left/right/up/down\")",
            "       camera_act(action=\"zoom\", direction=\"in/out\")",
            "   - Use render_from_objects() or render_from_camera() for deterministic single-shot verification.",
            "   - If visibility is incomplete or occluded, explicitly state uncertainty instead of guessing.",
            "   - camera_set_pose() for precise absolute camera placement when exact viewpoints matter.",
            "",
        ]
    )

    # ── Phase 2: Available asset workflows ────────────────────────────
    lines.extend(
        [
            "2. Available asset workflows (no status-check tools needed):",
            "   - PolyHaven (always available)",
            "     - Flow: search_polyhaven_assets() -> download_polyhaven_asset()",
            "     - Objects/models: download_polyhaven_asset(asset_type=\"models\")",
            "     - Materials/textures: download_polyhaven_asset(asset_type=\"textures\")",
            "       then set_texture() to apply downloaded textures to existing meshes",
            "     - Environment lighting: download_polyhaven_asset(asset_type=\"hdris\")",
            "     - Best for physically plausible materials and HDRI lighting setup",
        ]
    )

    if sketchfab_ready:
        lines.extend(
            [
                "   - Sketchfab (server-side)",
                "     - Flow: search_sketchfab_models(query=...) -> get_sketchfab_model_preview(uid)"
                " -> download_sketchfab_model(uid=..., target_size=...)",
                "     - Always compare previews before importing to avoid wasted downloads",
                "     - Best for authored realistic assets",
            ]
        )

    if infinigen_ready:
        lines.extend(
            [
                "   - Infinigen (Procedural Content Generation)",
                "     - Flow: get_infinigen_available_assets() -> generate_infinigen_assets(asset_type=\"...\")",
                "     - Best for natural assets and procedural indoor/architectural variations",
            ]
        )

    if trellis2_ready:
        lines.extend(
            [
                "   - TRELLIS2 (headless)",
                "     - Flow: generate_trellis2_model(text_prompt=..., object_name=...)",
                "     - Synchronous (~30-60s), best for single custom objects",
                "     - Use when retrieval/libraries cannot satisfy a unique object request",
            ]
        )

    if rodin_ready:
        lines.extend(
            [
                "   - Hyper3D Rodin",
                "     - Flow: generate_hyper3d_model_via_text(...) or generate_hyper3d_model_via_images(...)"
                " -> poll_rodin_job_status(subscription_key=... or request_id=...)"
                " -> import_generated_asset(...)",
                "     - Best for single-item custom generation, especially from reference images",
            ]
        )

    if hunyuan_ready:
        lines.extend(
            [
                "   - Hunyuan3D",
                "     - Flow: generate_hunyuan3d_model(text_prompt=... or input_image_url=...)",
                "     - Built-in polling, best for single custom object generation",
            ]
        )

    if retrieval_ready:
        lines.extend(
            [
                "   - 3D Asset Retrieval Database",
                "     - Flow: search_3d_assets_by_text(query=..., top_k=...)"
                " -> import_retrieved_asset(model_url=..., object_name=...)",
                "     - Best for common real-world objects and fast scene assembly",
            ]
        )

    if not any(
        [sketchfab_ready, infinigen_ready, trellis2_ready, rodin_ready, hunyuan_ready, retrieval_ready]
    ):
        lines.extend(
            [
                "   - Note: No extra generator/retrieval workflow is currently available beyond PolyHaven.",
            ]
        )

    # ── Phase 3: Post-import verification ─────────────────────────────
    lines.extend(
        [
            "",
            "3. After every import/generation (REQUIRED):",
            "   a. Use get_object_info() to confirm world_bounding_box, dimensions, and transform.",
            "   b. Check for clipping/intersection/floating: compare bounding boxes of nearby objects.",
            "   c. Re-check with camera_observe(object_names, mode=\"multi_view\") before asserting"
            " final placement.",
            "   d. Fix scale mismatch, clipping, or intersection immediately using Blender edits.",
            "   e. Ensure spatial relationships and target sizes are consistent across all objects.",
            "",
        ]
    )

    # ── Phase 4: Source priority ──────────────────────────────────────
    priority_rules: list[str] = []
    if sketchfab_ready and retrieval_ready:
        priority_rules.append("For realistic authored objects: Sketchfab -> Retrieval")
    elif sketchfab_ready:
        priority_rules.append("For realistic authored objects: Sketchfab")
    elif retrieval_ready:
        priority_rules.append("For realistic authored objects: Retrieval")

    if infinigen_ready:
        if sketchfab_ready and retrieval_ready:
            priority_rules.append(
                "For natural or indoor procedural assets: Infinigen first, then Sketchfab, then Retrieval"
            )
        elif sketchfab_ready:
            priority_rules.append("For natural or indoor procedural assets: Infinigen first, then Sketchfab")
        elif retrieval_ready:
            priority_rules.append("For natural or indoor procedural assets: Infinigen first, then Retrieval")
        else:
            priority_rules.append("For natural or indoor procedural assets: Infinigen")

    if rodin_ready and retrieval_ready:
        priority_rules.append("For unique custom objects: Retrieval first, then Rodin")
    elif rodin_ready:
        priority_rules.append("For unique custom objects: Rodin")
    elif retrieval_ready:
        priority_rules.append("For unique custom objects: Retrieval")

    if trellis2_ready and retrieval_ready:
        priority_rules.append("For custom generation fallback: Retrieval first, then TRELLIS2")
    elif trellis2_ready:
        priority_rules.append("For custom generation fallback: TRELLIS2")

    if hunyuan_ready and retrieval_ready:
        priority_rules.append("For unique-object fallback: Retrieval first, then Hunyuan3D")
    elif hunyuan_ready:
        priority_rules.append("For unique-object fallback: Hunyuan3D")

    lines.append("4. Recommended source priority among available workflows:")

    if priority_rules:
        for rule in priority_rules:
            lines.append(f"   - {rule}")
    else:
        lines.append("   - Use PolyHaven for materials/textures/HDRIs; rely on scripting for custom geometry.")

    lines.append("   - Environment lighting and PBR textures: PolyHaven first.")
    lines.append("   - Simple primitives (cube/sphere/plane): create directly via scripting.")

    # ── Phase 5: Multimodal feedback loop ─────────────────────────────
    lines.extend(
        [
            "",
            "5. Multimodal feedback loop (use throughout construction):",
            "   - Scene-level verification runs automatically after every scene mutation.",
            "   - For object-level detail work, use camera_act/render_from_objects to inspect.",
            "   - If verification reports problems (wrong scale, bad placement, missing objects):",
            "       -> fix immediately, then re-render the affected object to confirm the fix.",
            "   - After local refinement, ALWAYS call a render tool so the system can verify.",
            "   - Do NOT claim the scene is complete without verification showing 'match' status.",
            "   - Do NOT mark a todo as completed without visual confirmation.",
            "",
        ]
    )

    # ── Phase 6: Scripting fallback ───────────────────────────────────
    generator_names = [
        name
        for enabled, name in [
            (trellis2_ready, "TRELLIS2"),
            (rodin_ready, "Rodin"),
            (hunyuan_ready, "Hunyuan3D"),
        ]
        if enabled
    ]

    lines.extend(
        [
            "6. Only fall back to execute_blender_code() when:",
            "   - A simple primitive is explicitly requested",
            "   - No suitable asset exists after searching/generating with available workflows",
        ]
    )
    if generator_names:
        lines.append(
            "   - "
            + ", ".join(generator_names)
            + " generation failed, timed out, or returned unusable geometry"
        )
    lines.extend(
        [
            "   - The task specifically requires basic procedural geometry/material edits",
            "   - Required capability is not available in existing tools",
        ]
    )

    return "\n".join(lines)
