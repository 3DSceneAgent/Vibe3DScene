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

    lines: list[str] = [
        "When creating 3D content in Blender:",
        "",
        "0. Before anything, call get_scene_info().",
        "1. Use only these currently available asset workflows (no status-check tools needed):",
        "   - PolyHaven",
        "     - Objects/models: download_polyhaven_asset(asset_type=\"models\")",
        "     - Materials/textures: download_polyhaven_asset(asset_type=\"textures\")",
        "     - Environment lighting: download_polyhaven_asset(asset_type=\"hdris\")",
        "     - Best for physically plausible materials and HDRI lighting setup",
    ]

    if sketchfab_ready:
        lines.extend(
            [
                "   - Sketchfab (server-side)",
                "     - Search: search_sketchfab_models(query=...)",
                "     - Compare previews: get_sketchfab_model_preview(uid)",
                "     - Import: download_sketchfab_model(uid=..., target_size=...)",
                "     - Best for authored realistic assets",
            ]
        )

    if infinigen_ready:
        lines.extend(
            [
                "   - Infinigen (Procedural Content Generation)",
                "     - Inspect supported asset types: get_infinigen_available_assets()",
                "     - Generate: generate_infinigen_assets(asset_type=\"...\")",
                "     - Best for natural assets and procedural indoor/architectural variations",
            ]
        )

    if trellis2_ready:
        lines.extend(
            [
                "   - TRELLIS2 (headless)",
                "     - Generate custom single object: generate_trellis2_model(text_prompt=..., object_name=...)",
                "     - Use when retrieval/libraries cannot satisfy a unique object request",
            ]
        )

    if rodin_ready:
        lines.extend(
            [
                "   - Hyper3D Rodin (local-client/headless)",
                "     - Create task: generate_hyper3d_model_via_text(...) or generate_hyper3d_model_via_images(...)",
                "     - Poll task: poll_rodin_job_status(...)",
                "     - Import generated model: import_generated_asset(...)",
                "     - Best for single-item custom generation, especially from reference images",
            ]
        )

    if hunyuan_ready:
        lines.extend(
            [
                "   - Hunyuan3D (headless)",
                "     - Generate with built-in polling: generate_hunyuan3d_model(text_prompt=... or input_image_url=...)",
                "     - Best for single custom object generation in headless mode",
            ]
        )

    if retrieval_ready:
        lines.extend(
            [
                "   - 3D Asset Retrieval Database",
                "     - Search: search_3d_assets_by_text(query=..., top_k=...)",
                "     - Import: import_retrieved_asset(model_url=..., object_name=...)",
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
        priority_rules.append("For headless custom generation fallback: Retrieval first, then TRELLIS2")
    elif trellis2_ready:
        priority_rules.append("For headless custom generation fallback: TRELLIS2")

    if hunyuan_ready and retrieval_ready:
        priority_rules.append("For headless unique-object fallback: Retrieval first, then Hunyuan3D")
    elif hunyuan_ready:
        priority_rules.append("For headless unique-object fallback: Hunyuan3D")

    lines.extend(
        [
            "",
            "2. Always verify placement and scale after each import/generation:",
            "   - Inspect world/object bounding boxes to avoid clipping and floating assets",
            "   - Ensure spatial relationships and target size are consistent across objects",
            "",
            "3. Recommended source priority among available workflows:",
        ]
    )

    if priority_rules:
        for rule in priority_rules:
            lines.append(f"   - {rule}")
    else:
        lines.append("   - Use PolyHaven for materials/textures/HDRIs; rely on scripting for custom geometry.")

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
            "",
            "4. Only fall back to scripting when:",
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
    lines.append("   - The task specifically requires basic procedural geometry/material edits")

    return "\n".join(lines)
