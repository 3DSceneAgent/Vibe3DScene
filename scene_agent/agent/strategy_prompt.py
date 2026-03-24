"""
Shared strategy prompt builder for scene creation workflows.

This module is pure and tool-list driven so both the agent runtime and MCP
prompt endpoint can build consistent, non-duplicated strategy text.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypedDict


class AssetWorkflowAvailability(TypedDict):
    polyhaven_ready: bool
    sketchfab_ready: bool
    infinigen_ready: bool
    trellis2_ready: bool
    rodin_ready: bool
    hunyuan_ready: bool
    objaverse_retrieval_ready: bool
    scenesmith_hssd_ready: bool
    scenesmith_ambientcg_ready: bool
    sam_reconstruct_ready: bool
    undo_ready: bool
    clear_scene_ready: bool


POLYHAVEN_WORKFLOW_TOOLS: frozenset[str] = frozenset(
    {
        "search_polyhaven_assets",
        "download_polyhaven_asset",
        "set_texture",
    }
)
SKETCHFAB_WORKFLOW_TOOLS: frozenset[str] = frozenset(
    {
        "search_sketchfab_models",
        "get_sketchfab_model_preview",
        "download_sketchfab_model",
    }
)
INFINIGEN_WORKFLOW_TOOLS: frozenset[str] = frozenset(
    {
        "get_infinigen_available_assets",
        "generate_infinigen_assets",
        "import_blend_contents",
    }
)
TRELLIS2_WORKFLOW_TOOLS: frozenset[str] = frozenset({"generate_trellis2_model"})
RODIN_WORKFLOW_TOOLS: frozenset[str] = frozenset(
    {
        "generate_hyper3d_model_via_text",
        "generate_hyper3d_model_via_images",
        "poll_rodin_job_status",
        "import_generated_asset",
    }
)
HUNYUAN_WORKFLOW_TOOLS: frozenset[str] = frozenset({"generate_hunyuan3d_model"})
OBJAVERSE_RETRIEVAL_WORKFLOW_TOOLS: frozenset[str] = frozenset(
    {
        "search_3d_assets_by_text",
        "import_retrieved_asset",
    }
)
SCENESMITH_HSSD_WORKFLOW_TOOLS: frozenset[str] = frozenset(
    {
        "search_hssd_assets",
        "import_hssd_asset",
    }
)
SCENESMITH_AMBIENTCG_WORKFLOW_TOOLS: frozenset[str] = frozenset(
    {
        "search_ambientcg_materials",
        "apply_ambientcg_material",
    }
)
SAM_RECONSTRUCT_WORKFLOW_TOOLS: frozenset[str] = frozenset(
    {
        "reconstruct_full_scene",
        "import_blend_contents",
    }
)
UNDO_WORKFLOW_TOOLS: frozenset[str] = frozenset({"undo_last_snapshot"})
CLEAR_SCENE_WORKFLOW_TOOLS: frozenset[str] = frozenset({"clear_scene"})


def _normalize_tool_names(available_tool_names: Iterable[str] | None) -> set[str]:
    if not available_tool_names:
        return set()
    normalized: set[str] = set()
    for name in available_tool_names:
        if isinstance(name, str):
            cleaned = name.strip()
            if cleaned:
                normalized.add(cleaned)
    return normalized


def infer_asset_workflow_availability(
    available_tool_names: Iterable[str] | None,
) -> AssetWorkflowAvailability:
    tool_names = _normalize_tool_names(available_tool_names)
    return {
        "polyhaven_ready": POLYHAVEN_WORKFLOW_TOOLS.issubset(tool_names),
        "sketchfab_ready": SKETCHFAB_WORKFLOW_TOOLS.issubset(tool_names),
        "infinigen_ready": INFINIGEN_WORKFLOW_TOOLS.issubset(tool_names),
        "trellis2_ready": TRELLIS2_WORKFLOW_TOOLS.issubset(tool_names),
        "rodin_ready": RODIN_WORKFLOW_TOOLS.issubset(tool_names),
        "hunyuan_ready": HUNYUAN_WORKFLOW_TOOLS.issubset(tool_names),
        "objaverse_retrieval_ready": OBJAVERSE_RETRIEVAL_WORKFLOW_TOOLS.issubset(tool_names),
        "scenesmith_hssd_ready": SCENESMITH_HSSD_WORKFLOW_TOOLS.issubset(tool_names),
        "scenesmith_ambientcg_ready": SCENESMITH_AMBIENTCG_WORKFLOW_TOOLS.issubset(tool_names),
        "sam_reconstruct_ready": SAM_RECONSTRUCT_WORKFLOW_TOOLS.issubset(tool_names),
        "undo_ready": UNDO_WORKFLOW_TOOLS.issubset(tool_names),
        "clear_scene_ready": CLEAR_SCENE_WORKFLOW_TOOLS.issubset(tool_names),
    }


def build_asset_creation_strategy_text(
    *,
    polyhaven_ready: bool,
    sketchfab_ready: bool,
    infinigen_ready: bool,
    trellis2_ready: bool,
    rodin_ready: bool,
    hunyuan_ready: bool,
    objaverse_retrieval_ready: bool,
    scenesmith_hssd_ready: bool,
    scenesmith_ambientcg_ready: bool,
    sam_reconstruct_ready: bool,
    undo_ready: bool,
    clear_scene_ready: bool,
    fast_mode: bool = False,
) -> str:
    # Phase 0: Scene grounding
    lines: list[str] = [
        "When creating or editing a 3D scene, follow this execution playbook:",
        "",
        "0. Scene grounding first (NEVER skip):",
        "   - Global-to-local order is mandatory: block out global layout and major masses first,",
        "     then refine object-level placement, then tune local details/materials.",
        "   - Do NOT spend cycles on fine local tweaks before global composition and scale are stable.",
        "   - Run get_scene_info() to understand existing objects and scene scale.",
        "   - If scene is visually complex, run observe_scene_global() for a 5-view global snapshot.",
        "   - In early layout passes, prefer open composition: keep at least one major side open",
        "     or keep ceiling off, and avoid fully sealed rooms/containers.",
        "   - Even for indoor requests, stage as an open shell first; close enclosure only near finalization,",
        "     after verification confirms object/layout/scale correctness.",
        "   - If scene-level views cannot see primary objects because of enclosure, remove or open blockers first.",
        "",
    ]

    # Phase 0.5: Automatic scene observation
    lines.extend(
        [
            "0.5. Automatic scene observation (system-managed):",
            *(
                [
                    "   - Fast mode is ON for this request: automatic scene observation is skipped.",
                    "   - You must proactively call get_scene_info() after meaningful edits to keep scene grounding fresh.",
                    "   - For scene-wide visual checks, manually call observe_scene_global() when layout or scale may have shifted.",
                    "   - For object-level inspection, use camera_act(), camera_observe(), and manual render tools more aggressively.",
                    "   - Do not assume the system has fresh visual evidence unless you explicitly produced it.",
                    "   - If a major scene edit lands, render before and after the next high-risk change when feasible.",
                    "   - After local refinement, ALWAYS re-render the target object before moving on.",
                ]
                if fast_mode
                else [
                    "   - After every scene mutation (import, generate, execute_blender_code, set_texture),",
                    "     5 scene-level cameras auto-update and render (4 corners + top-down bird view).",
                    "   - These cameras track the full scene bounding box - do NOT modify them manually.",
                    "   - If the scene has no explicit lights or HDRI, render tools may add temporary neutral",
                    "     verification lighting so geometry stays readable; those lights are not persisted/exported.",
                    "   - For object-level inspection, use camera_act() and camera_observe().",
                    "   - If object-level renders look unreliable (blank/black/repeatedly inconclusive),",
                    "     run observe_scene_global() to re-ground with scene-wide context.",
                    "   - After local refinement, ALWAYS re-render the target object before moving on.",
                ]
            ),
            "",
        ]
    )

    # Phase 1: Visual evidence & camera workflow
    lines.extend(
        [
            "1. Visual evidence before claims (anti-hallucination rule):",
            (
                "   - Review the latest scene-wide renders for global composition issues."
                if fast_mode
                else "   - Review the automatic multi-view renders for global composition issues."
            ),
            "   - For targeted inspection around one object:",
            '       camera_act(action="focus", object_names=[...])',
            '       camera_act(action="move", direction="left/right/up/down")',
            '       camera_act(action="zoom", direction="in/out")',
            "   - Use render_from_objects() or render_from_camera() for deterministic single-shot verification.",
            "   - When debugging object placement/details, prefer render_from_objects(..., mode=\"annotated\") first.",
            "   - If visibility is incomplete or occluded, explicitly state uncertainty instead of guessing.",
            "   - camera_set_pose() for precise absolute camera placement when exact viewpoints matter.",
            "",
        ]
    )

    # Phase 2: Available asset workflows
    lines.append("2. Available asset workflows (no status-check tools needed):")

    if polyhaven_ready:
        lines.extend(
            [
                "   - PolyHaven",
                "     - Flow: search_polyhaven_assets() -> download_polyhaven_asset()",
                '     - Objects/models: download_polyhaven_asset(asset_type="models")',
                '     - Materials/textures: download_polyhaven_asset(asset_type="textures")',
                "       then set_texture() to apply downloaded textures to existing meshes",
                '     - Environment lighting: download_polyhaven_asset(asset_type="hdris")',
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
                "     - Flow: get_infinigen_available_assets() -> generate_infinigen_assets(asset_type=\"...\")"
                " -> import_blend_contents(blend_file_path=\"...\")",
                "     - Do NOT assume collection name equals asset_type when importing .blend outputs",
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
                "     - Do not generate ground/floor/entire-scene; create scene parts separately",
            ]
        )

    if rodin_ready:
        lines.extend(
            [
                "   - Hyper3D Rodin",
                "     - Flow: generate_hyper3d_model_via_text(...) or generate_hyper3d_model_via_images(...)"
                " -> poll_rodin_job_status(subscription_key=...)"
                " -> import_generated_asset(...)",
                "     - Best for single-item custom generation, especially from reference images",
                "     - For remembered thread images, use input_image_name=... or input_image_id=... instead of filesystem paths",
            ]
        )

    if hunyuan_ready:
        lines.extend(
            [
                "   - Hunyuan3D",
                "     - Flow: generate_hunyuan3d_model(text_prompt=... or input_image_url=...)",
                "     - Built-in polling, best for single custom object generation",
                "     - When exactly one image is attached to the current request, the runtime can auto-resolve it for image-to-3D",
                "     - For remembered thread images, use input_image_name=... or input_image_id=...",
                "     - Hunyuan often returns the model as Type=OBJ with a .zip bundle plus preview GIFs",
                "     - Import flow after generation: prefer preferred_model_asset.url when present, "
                "otherwise select the Type=OBJ ResultFile3Ds URL, then call "
                "import_glb_model(model_url=..., object_name=...)",
            ]
        )

    if objaverse_retrieval_ready:
        lines.extend(
            [
                "   - 3D Asset Retrieval Database",
                "     - Flow: search_3d_assets_by_text(query=..., top_k=...)"
                " -> import_retrieved_asset(model_url=..., object_name=...)",
                "     - Best for common real-world objects and fast scene assembly",
                "     - Retrieval captions and similarity scores can be noisy; do not reject a candidate solely from its text label.",
                "     - If a top result is even plausibly relevant, import the best candidate first.",
                "     - Only abandon retrieval after imported candidates are visually wrong or the top results are clearly unrelated.",
            ]
        )

    if scenesmith_hssd_ready:
        lines.extend(
            [
                "   - SceneSmith HSSD Retrieval",
                "     - Flow: search_hssd_assets(query=..., object_type=..., top_k=..., desired_dimensions_m=...)"
                " -> import_hssd_asset(download_url=..., object_name=...)",
                "     - Best for indoor scene objects when category and target dimensions matter",
                "     - Prefer importing a plausible candidate instead of judging only from returned names/categories.",
            ]
        )

    if scenesmith_ambientcg_ready:
        lines.extend(
            [
                "   - SceneSmith AmbientCG Materials",
                "     - Flow: search_ambientcg_materials(query=..., top_k=...)"
                " -> apply_ambientcg_material(object_name=..., color_url=..., normal_url=..., roughness_url=...)",
                "     - Use the texture URLs returned by search_ambientcg_materials() directly.",
                "     - apply_ambientcg_material() downloads the maps, creates a Blender PBR material,"
                " and assigns it to the target existing mesh object.",
                "     - Do NOT manually download the package zip or route AmbientCG maps through set_texture().",
                "     - After applying the material, re-render the target object to verify the result.",
                "     - Best for fast PBR material search and assignment inside Blender",
            ]
        )

    if sam_reconstruct_ready:
        lines.extend(
            [
                "   - SAM3D full-scene reconstruction (image-only)",
                "     - Flow: reconstruct_full_scene(input_image_path=...)"
                " -> import_blend_contents(blend_file_path=\"...\")",
                "     - Use only when the user provides a reference image and wants a fast whole-scene"
                " layout bootstrap from that image",
                "     - When exactly one image is attached to the current request, the runtime can auto-resolve"
                " it for reconstruction",
                "     - For remembered thread images, use input_image_name=... or input_image_id=..."
                " instead of filesystem paths",
                "     - Do NOT use this tool for text-only requests or single-object generation",
                "     - The generated .blend is an external scene asset pack; import it first, then refine,"
                " replace, delete, retarget materials, and verify with existing tools",
                "     - If reconstruction fails or times out, fall back to retrieval/generation workflows"
                " and assemble the scene incrementally",
            ]
        )

    if not any(
        [
            polyhaven_ready,
            sketchfab_ready,
            infinigen_ready,
            trellis2_ready,
            rodin_ready,
            hunyuan_ready,
            objaverse_retrieval_ready,
            scenesmith_hssd_ready,
            scenesmith_ambientcg_ready,
            sam_reconstruct_ready,
        ]
    ):
        lines.append(
            "   - Note: No asset-library or generator workflow is currently enabled; rely on direct Blender edits and imports."
        )
    elif polyhaven_ready and not any(
        [
            sketchfab_ready,
            infinigen_ready,
            trellis2_ready,
            rodin_ready,
            hunyuan_ready,
            objaverse_retrieval_ready,
            scenesmith_hssd_ready,
            scenesmith_ambientcg_ready,
            sam_reconstruct_ready,
        ]
    ):
        lines.append("   - Note: No extra generator/retrieval workflow is currently available beyond PolyHaven.")
    lines.append("   (The runtime strategy prompt lists all currently enabled sources and their priority.)")

    # Phase 3: Post-import verification
    lines.extend(
        [
            "",
            "3. After every import/generation (REQUIRED):",
            "   a. Use get_object_info() to confirm world_bounding_box, dimensions, and transform.",
            "   a.1 Scale safety: before changing object scale, inspect current dimensions/transform first.",
            "       Prefer incremental scaling from current value; avoid blind absolute scale overrides.",
            "   a.2 Imported assets may arrive under nested Empty wrappers; compare world_scale vs local scale.",
            "   a.3 Do NOT delete imported parent Empty wrappers directly; if wrappers still remain,",
            "       prefer delete_objects(..., mode=\"detach_keep_world\" / \"reparent_to_parent_keep_world\").",
            "   b. Check for clipping/intersection/floating: compare bounding boxes of nearby objects.",
            "   c. Review the automatic scene-level renders for overall fit.",
            "   c.1 Ensure at least one scene-level view has clear line-of-sight to main target objects.",
            '   d. Re-check with camera_observe(object_names, mode="multi_view") before asserting final placement.',
            "   e. Fix scale mismatch, clipping, or intersection immediately using Blender edits.",
            "   f. Ensure spatial relationships and target sizes are consistent across all objects.",
            "",
        ]
    )

    # Phase 4: Source priority
    priority_rules: list[str] = []
    if sketchfab_ready and objaverse_retrieval_ready:
        priority_rules.append("For realistic authored objects: Sketchfab -> Retrieval")
    elif sketchfab_ready:
        priority_rules.append("For realistic authored objects: Sketchfab")
    elif objaverse_retrieval_ready:
        priority_rules.append("For realistic authored objects: Retrieval")

    if sam_reconstruct_ready:
        priority_rules.append(
            "For full-scene reference-image bootstrapping: SAM3D reconstruct first, then refine with import/edit tools"
        )

    if infinigen_ready:
        if sketchfab_ready and objaverse_retrieval_ready:
            priority_rules.append(
                "For natural or indoor procedural assets: Infinigen first, then Sketchfab, then Retrieval"
            )
        elif sketchfab_ready:
            priority_rules.append("For natural or indoor procedural assets: Infinigen first, then Sketchfab")
        elif objaverse_retrieval_ready:
            priority_rules.append("For natural or indoor procedural assets: Infinigen first, then Retrieval")
        else:
            priority_rules.append("For natural or indoor procedural assets: Infinigen")

    if rodin_ready and objaverse_retrieval_ready:
        priority_rules.append("For unique custom objects: Retrieval first, then Rodin")
    elif rodin_ready:
        priority_rules.append("For unique custom objects: Rodin")
    elif objaverse_retrieval_ready:
        priority_rules.append("For unique custom objects: Retrieval")

    if trellis2_ready and objaverse_retrieval_ready:
        priority_rules.append("For custom generation fallback: Retrieval first, then TRELLIS2")
    elif trellis2_ready:
        priority_rules.append("For custom generation fallback: TRELLIS2")

    if hunyuan_ready and objaverse_retrieval_ready:
        priority_rules.append("For unique-object fallback: Retrieval first, then Hunyuan3D")
    elif hunyuan_ready:
        priority_rules.append("For unique-object fallback: Hunyuan3D")

    if scenesmith_hssd_ready:
        priority_rules.append(
            "For indoor furniture or size-sensitive library objects: SceneSmith HSSD before generic generation"
        )

    if scenesmith_ambientcg_ready and polyhaven_ready:
        priority_rules.append("For PBR materials/textures on existing meshes: SceneSmith AmbientCG or PolyHaven")
    elif scenesmith_ambientcg_ready:
        priority_rules.append("For PBR materials/textures on existing meshes: SceneSmith AmbientCG")

    lines.append("4. Recommended source priority among available workflows:")
    if priority_rules:
        for rule in priority_rules:
            lines.append(f"   - {rule}")
    elif polyhaven_ready:
        lines.append("   - Use PolyHaven for materials/textures/HDRIs; rely on scripting for custom geometry.")
    else:
        lines.append(
            "   - No asset-library workflow is enabled; rely on direct scripting/import tools for geometry and materials."
        )

    if polyhaven_ready:
        lines.append("   - Environment lighting and PBR textures: PolyHaven first.")
    elif scenesmith_ambientcg_ready:
        lines.append("   - PBR textures on existing meshes: SceneSmith AmbientCG first.")
    else:
        lines.append(
            "   - Environment lighting and PBR textures: configure them explicitly via scripting or imported assets."
        )
    if objaverse_retrieval_ready and (rodin_ready or hunyuan_ready):
        lines.append(
            "   - If the user explicitly asks to compare retrieval vs image-conditioned generation from one reference image, do both branches, import both results, then place them side by side for inspection."
        )
    lines.append("   - Simple primitives (cube/sphere/plane): create directly via scripting.")

    # Phase 5: Multimodal feedback loop
    lines.extend(
        [
            "",
            "5. Multimodal feedback loop (use throughout construction):",
            (
                "   - Fast mode is ON for this request: automatic verification is skipped."
                if fast_mode
                else "   - Scene-level verification runs automatically after every scene mutation."
            ),
            "   - Keep global-first cadence: global render/verification first, then object-level refinement.",
            "   - For object-level detail work, use camera_act/render_from_objects to inspect.",
            "   - If mismatch persists, call render_from_objects(..., mode=\"annotated\") to pinpoint bad objects/regions.",
            *(
                [
                    "   - You must judge progress from get_scene_info(), manual renders, and explicit visual inspection.",
                    "   - Before claiming completion, produce a fresh render yourself and reason from that evidence.",
                ]
                if fast_mode
                else []
            ),
            "   - If verification reports problems (wrong scale, bad placement, missing objects):",
            "       -> fix immediately, then re-render the affected object to confirm the fix.",
        ]
    )
    if undo_ready:
        undo_recovery_lines = [
            "   - If a single edit badly breaks the scene (blank scene-level renders,"
            " key objects disappear, or scale explodes), call undo_last_snapshot() immediately.",
            "   - After undo, re-run get_scene_info() plus observe_scene_global() before continuing.",
        ]
        if clear_scene_ready:
            undo_recovery_lines.append(
                "   - If undo cannot recover the scene, run reset flow:"
                " clear_scene() -> get_scene_info() -> observe_scene_global(), then rebuild from the first pending todo."
            )
        else:
            undo_recovery_lines.append(
                "   - If undo cannot recover the scene, run reset flow:"
                " get_scene_info() -> delete_objects(all object names, mode=\"cascade\", strict=False,"
                " ignore_missing=True), then rebuild from the first pending todo."
            )
        undo_recovery_lines.append(
            "   - Do NOT use execute_blender_code for scene reset/deletion; raw delete code is blocked."
        )
        lines.extend(undo_recovery_lines)
    lines.extend(
        [
            "   - After local refinement, ALWAYS call a render tool so the system can verify.",
            "   - Do NOT claim the scene is complete without verification showing 'match' status.",
            "   - Do NOT mark a todo as completed without visual confirmation.",
            "",
        ]
    )

    # Phase 6: Scripting fallback
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
            "   - The change is not object deletion (for deletion, use delete_objects())",
            '   - If names are ambiguous, use delete_objects(name_match_mode="contains")',
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


def asset_creation_strategy_text_from_tools(
    available_tool_names: Iterable[str] | None,
    *,
    fast_mode: bool = False,
) -> str:
    return build_asset_creation_strategy_text(
        **infer_asset_workflow_availability(available_tool_names),
        fast_mode=fast_mode,
    )
