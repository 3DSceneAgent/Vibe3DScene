# CHANGELOG

This changelog tracks major project updates at a coarse version level rather than as a commit-by-commit ledger.

## CHANGELOG(v0.2.0, 20260412)

1. Optimized the agentic harness by introducing intelligent routing and a budgeting mechanism to decide whether planning and verification are needed. Improved how images are managed in memory and auto-referenced. At the topology level, the system now supports both single-agent and dual-agent modes, with the dual-agent setup still under active debugging.
2. Added support for Qwen. Our primary models are now Gemini and Qwen, both of which offer strong cost-performance tradeoffs. Also added end-to-end token usage display across the full workflow.
3. Added support for manual editing of vibe-generated objects, including basic transforms and deletion. Users can also add basic primitives into the scene, and those changes are propagated through to the Blender side. In addition, object-specific edits can now be made via `@` references.
4. Added support for several new tools, including SAM3D, Tripo3D generation, HSSD object retrieval, and AmbientCG material retrieval. Improved the robustness of the code execution tool through a validation-and-rollback mechanism.
5. Significantly improved the frontend, including the chat UI, lighting/rendering, and interaction logic. Image and scene persistence are now handled independently from the runtime.

## v0.1.0

1. Single-agent Blender scene runtime with MCP-based tool integration.
2. Web/API/CLI interfaces for scene editing and generation.
3. Headless and local-client Blender runtime modes.
4. Early retrieval, rendering, verification, and scene manipulation flows.
