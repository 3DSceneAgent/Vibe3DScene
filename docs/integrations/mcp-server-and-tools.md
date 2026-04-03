# MCP Server and Tools

Last updated: 2026-03-30

This document summarizes the role of the in-repo MCP server, how tools are registered and gated, and which tool families are currently supported.

For environment variables, endpoint overrides, and service-related settings, see the central [Configuration Reference](../reference/configuration.md).

Primary code references:

- `mcp_server/runtime.py`
- `mcp_server/tool_registry.py`
- `mcp_server/tools/`
- `scene_agent/utils/tool_service_endpoints.py`

## 1. MCP Server Role

The MCP server is the tool execution layer used by the agent runtime.

Its responsibilities are:

- registering available tools at server startup
- enforcing environment- and mode-based tool gating
- exposing Blender-facing tools
- exposing optional external-service-backed tools
- preventing invalid tool combinations from booting silently

In practice, the graph runtime does not talk directly to every external service. It talks to the MCP tool surface, and the MCP server decides what is currently valid and reachable.

## 2. Registration and Gating Model

Tool registration happens centrally in `register_mcp_tools()` in `mcp_server/tool_registry.py`.

The registry evaluates:

- `BLENDER_MODE`
- `ENABLE_*` flags
- provider-specific credentials such as `RODIN_API_KEY`, `TRIPO_API_KEY`, or `SKETCHFAB_API_KEY`
- retrieval backend selection via `ASSET_RETRIEVAL_BACKEND`
- service reachability checks for optional dependencies

The registry can also reject invalid combinations. Important examples include:

- generator switches are mutually exclusive:
  - `ENABLE_RODIN`
  - `ENABLE_TRIPO`
  - `ENABLE_TRELLIS2`
  - `ENABLE_HUNYUAN`
- retrieval backends and Sketchfab are not allowed in conflicting configurations

## 3. Runtime Mode Constraints

Some tools are always available, while others depend on runtime mode.

| Runtime mode | Characteristics | Typical mode-sensitive tools |
| --- | --- | --- |
| `local-client` | Connects to an already running Blender addon and supports viewport-centric observation. | `get_viewport_screenshot`, local observation flows |
| `headless` | Backend launches and manages Blender/MCP runtime processes, with better fit for persisted sessions and automation. | TRELLIS2, headless generation or reconstruction workflows |

Examples:

- `get_viewport_screenshot` is only valid in `local-client`
- TRELLIS2 is gated to `headless`
- some generators are exposed in both `local-client` and `headless`

## 4. Tool Families

| Category | Provider / Source | Representative tools | Notes |
| --- | --- | --- | --- |
| Scene control and inspection | Blender core | `get_scene_info`, `get_object_info`, `clear_scene`, `delete_objects`, `execute_blender_code`, `import_glb_model`, `import_blend_contents` | Core Blender-facing manipulation and inspection. |
| Camera, observation, and rendering | Blender core | `observe_scene_global`, `render_from_camera`, `render_from_objects`, `camera_observe`, `camera_act`, `camera_set_pose`, `get_viewport_screenshot` | Evidence collection and visual inspection. |
| Session memory and rollback | Blender core | `undo_last_snapshot` | Recovery-oriented tool path. |
| Retrieval and materials | PolyHaven | `search_polyhaven_assets`, `download_polyhaven_asset`, `set_texture` | Controlled by `ENABLE_POLYHAVEN`. |
| Retrieval | Objaverse-compatible backend | `search_3d_assets_by_text`, `import_retrieved_asset` | Active when `ASSET_RETRIEVAL_BACKEND=objaverse`. |
| Retrieval and materials | SceneSmith compatibility API | `search_hssd_assets`, `import_hssd_asset`, `search_ambientcg_materials`, `apply_ambientcg_material` | Active for SceneSmith retrieval; AmbientCG is independently gated. |
| Retrieval | Sketchfab | `search_sketchfab_models`, `get_sketchfab_model_preview`, `download_sketchfab_model` | Requires `ENABLE_SKETCHFAB`, API key, and reachable API. |
| 3D generation | Rodin / Hyper3D | `generate_hyper3d_model_via_text`, `generate_hyper3d_model_via_images`, `poll_rodin_job_status`, `import_generated_asset` | Mutually exclusive with other generator families. |
| 3D generation | TRELLIS2 | `generate_trellis2_model` | Headless-only in current gating. |
| 3D generation | Hunyuan3D | `generate_hunyuan3d_model` | Headless-only in current gating. |
| 3D generation | Tripo3D | `generate_tripo3d_model` | Available in `local-client` or `headless` when enabled. |
| Reconstruction | SAM / SAM3D service | `reconstruct_full_scene` | SAM-based full-scene reconstruction. |
| PCG | Infinigen / PCG service | `get_infinigen_available_assets`, `generate_infinigen_assets` | Depends on the PCG service stack. |

## 5. Retrieval Backend Selection

`ASSET_RETRIEVAL_BACKEND` chooses which retrieval family is active:

- `disabled`
- `objaverse`
- `scenesmith`

Behavior:

- `objaverse` enables text retrieval/import flow through the Objaverse-compatible stack
- `scenesmith` enables SceneSmith-compatible HSSD retrieval
- AmbientCG material support is controlled independently via `ENABLE_AMBIENTCG`

## 6. Service Endpoint Resolution

External service adapters resolve endpoints through environment variables and shared host defaults.

Common patterns:

- `TOOL_SERVICE_HOST` as the shared host override
- service-specific host variables such as `TRELLIS2_HOST`, `SCENESMITH_COMPAT_HOST`, or `SAM_HOST`
- service-specific ports such as `TRELLIS2_PORT`, `OBJAVERSE_PORT`, `SCENESMITH_COMPAT_PORT`, `INFINIGEN_PORT`, and `SAM_PORT`

This allows a single deployment to colocate multiple tool services while still supporting per-service overrides.

For the complete variable list and defaults, see [Configuration Reference](../reference/configuration.md).

## 7. Operational Notes

- The MCP server belongs to this repository.
- Heavy external services are expected to run in the sibling `../3DAgentTools` checkout.
- Health probes and gating are meant to fail early rather than letting the agent discover broken tool combinations at runtime.
- The agent runtime may still apply request-level allow-lists on top of the MCP-available tool set.

## 8. Related Docs

- [System Architecture](../architecture/system-architecture.md)
- [Runtime Workflow](../architecture/runtime-workflow.md)
- [Tool Servers](../deployment/tool-servers.md)
- [Configuration Reference](../reference/configuration.md)
