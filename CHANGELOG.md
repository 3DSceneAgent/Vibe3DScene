# CHANGELOG

This changelog tracks major project updates at a coarse version level rather than as a commit-by-commit ledger.

## v0.2.0

- Router-driven runtime with `direct_mode` and `plan_mode`.
- `fast_mode` support for lower-latency direct execution paths.
- Stronger todo lifecycle and unified evaluator-driven convergence.
- Experimental dual-agent builder/verifier topology.
- Qwen provider support alongside OpenAI, Anthropic, and Gemini.
- Expanded MCP tool surface, including SceneSmith retrieval/material flows, SAM reconstruction, and Tripo3D integration.
- Improved reference-image handling with persisted assets and request-scoped image routing.
- Retry support and stronger persistence around graph state, `.blend` sessions, and image artifacts.
- Decoupled external tool servers into the sibling `3DAgentTools` repository.
- Cleaner frontend UX with improved image attachments, render previews, scene previews, and viewport behavior.
- Documentation reorganized into focused architecture, deployment, MCP/tooling, and configuration references.

## v0.1.0

- Single-agent Blender scene runtime with MCP-based tool integration.
- Web/API/CLI interfaces for scene editing and generation.
- Headless and local-client Blender runtime modes.
- Early retrieval, rendering, verification, and scene manipulation flows.
