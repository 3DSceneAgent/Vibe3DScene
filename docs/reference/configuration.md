# Configuration Reference

Last updated: 2026-04-04

This document summarizes the main environment variables used by the current runtime. For defaults and validation logic, the source of truth is `scene_agent/config.py` plus MCP/runtime-specific modules such as `mcp_server/runtime.py`.

## 1. Model Providers

### Core provider selection

| Variable | Description |
| --- | --- |
| `VLM_PROVIDER` | Default provider for new threads. Supported values include `openai`, `anthropic`, `gemini`, and `qwen`. |
| `VLM_MODEL` | Optional default model override for the selected provider. |
| `DUAL_AGENT_VERIFIER_VLM_PROVIDER` | Optional dedicated verifier provider for experimental dual-agent plan mode. |
| `DUAL_AGENT_VERIFIER_VLM_MODEL` | Optional dedicated verifier model override. |

### Provider credentials

| Variable | Description |
| --- | --- |
| `OPENAI_API_KEY` | OpenAI API key. |
| `ANTHROPIC_API_KEY` | Anthropic API key. |
| `GEMINI_API_KEY` | Gemini API key. |
| `QWEN_API_KEY` | Qwen (DashScope) API key. |

### Provider model catalogs

| Variable | Description |
| --- | --- |
| `VLM_OPENAI_MODELS` | Comma-separated models exposed to clients for OpenAI. |
| `VLM_ANTHROPIC_MODELS` | Comma-separated models exposed to clients for Anthropic. |
| `VLM_GEMINI_MODELS` | Comma-separated models exposed to clients for Gemini. |
| `VLM_QWEN_MODELS` | Comma-separated models exposed to clients for Qwen. |

### Provider-specific behavior

| Variable | Description |
| --- | --- |
| `GEMINI_INCLUDE_THOUGHTS` | Whether Gemini responses may include provider thinking summaries. |
| `GEMINI_THINKING_BUDGET` | Optional Gemini thinking budget override. |
| `QWEN_ENABLE_THINKING` | Enables Qwen reasoning-content behavior. |
| `QWEN_THINKING_BUDGET` | Optional Qwen thinking budget override. |

## 2. API and Workflow Runtime

| Variable | Description |
| --- | --- |
| `API_PORT` | API port. |
| `API_WORKERS` | Number of API worker processes to launch. |
| `API_WORKER_ID` | Logical worker ID used in ownership and diagnostics. |
| `API_WORKER_ADVERTISE_URL` | Base URL advertised to peer workers for owner-proxy routing. |
| `API_STREAM_TIMEOUT_SECONDS` | Idle timeout for normal streaming responses. |
| `API_PLAN_STREAM_TIMEOUT_SECONDS` | Longer idle timeout for planning-heavy streams. |
| `FAST_MODE_DEFAULT` | Default `fast_mode` value when a request omits it. |
| `SCENE_AGENT_ENABLE_PENETRATION_VERIFY` | Enables the internal Blender command `check_scene_penetration` during single-agent `verify`. This is a runtime-only verification feature, not a public MCP tool. |
| `SCENE_AGENT_PENETRATION_THRESHOLD_M` | Minimum penetration depth in meters before geometry verification marks the merged verify result as `working` (default `0.02`). This is intentionally conservative so only more obvious intersections override a `done` status. |
| `SCENE_AGENT_PENETRATION_MAX_CANDIDATE_PAIRS` | Broad-phase candidate limit for internal geometry verification before narrow-phase BVH checks run. |
| `SCENE_AGENT_PENETRATION_MAX_REPORTED_PAIRS` | Maximum confirmed penetration pairs included in the `verification` payload under `penetration_check`. |
| `PLAN_MODE_MAX_AGENT_TURNS` | Agent-turn budget for plan mode. |
| `PLAN_MODE_MAX_TOOL_BATCHES` | Tool-batch budget for plan mode. |
| `PLAN_MODE_MAX_REPLANS` | Replan budget for plan mode. |
| `ENABLE_PLANMODE_DUAL_AGENT` | Enables dual-agent topology by default for plan mode when request routing allows it. |

## 3. Blender Runtime and Session Management

| Variable | Description |
| --- | --- |
| `BLENDER_MODE` | `local-client` or `headless`. |
| `BLENDER_HOST` | Blender addon socket host. |
| `BLENDER_PORT` | Blender addon socket port. |
| `BLENDER_HEADLESS_CMD` | Blender binary used in headless mode. |
| `BLENDER_HEADLESS_ARGS` | Headless Blender startup arguments. |
| `BLENDER_HEADLESS_STARTUP_TIMEOUT` | Startup timeout for headless Blender. |
| `BLENDER_HEADLESS_LOG_DIR` | Log directory for headless runtime processes. |
| `BLENDER_HEADLESS_BASE_PORT` | Base port for headless Blender sessions. |
| `BLENDER_MCP_BASE_PORT` | Base port for per-session MCP processes. |
| `BLENDER_MCP_CMD` | Command used to start per-session MCP runtime. |
| `BLENDER_MCP_ARGS` | Arguments used to start per-session MCP runtime. |
| `HEADLESS_REQUEST_TIMEOUT_SECONDS` | Timeout for scene/render/export operations in headless mode. |

## 4. Redis, Ownership, and Persistence

| Variable | Description |
| --- | --- |
| `REDIS_URL` | Redis URL used for session coordination and checkpointing. |
| `REDIS_KEY_PREFIX` | Redis key prefix. |
| `SESSION_LEASE_TTL_SECONDS` | Ownership lease TTL. |
| `SESSION_HEARTBEAT_INTERVAL_SECONDS` | Lease/activity heartbeat interval. |
| `SESSION_OWNER_UNREACHABLE_GRACE_SECONDS` | Grace period before ownership takeover. |
| `SESSION_SHARED_STORAGE_ROOT` | Shared filesystem root for persisted per-thread scene state. |
| `SESSION_IDLE_TIMEOUT_SECONDS` | Idle timeout for headless session shutdown. |
| `SESSION_SWEEP_INTERVAL_SECONDS` | Sweeper interval for idle-session cleanup. |
| `SESSION_MAX_SNAPSHOTS` | Snapshot retention limit per session. |

## 5. Reference Images and Short-Term Memory

| Variable | Description |
| --- | --- |
| `REFERENCE_IMAGE_MAX_COUNT` | Maximum uploaded images per conversation/thread. |
| `REFERENCE_IMAGE_MAX_BYTES` | Maximum bytes per uploaded image. |
| `REFERENCE_IMAGE_STORAGE_DIR` | Storage directory for persisted reference images. |
| `REFERENCE_IMAGE_HELPER_OPENAI_MODEL` | Helper model for naming/routing images under OpenAI. |
| `REFERENCE_IMAGE_HELPER_GEMINI_MODEL` | Helper model for naming/routing images under Gemini. |
| `REFERENCE_IMAGE_HELPER_ANTHROPIC_MODEL` | Helper model for naming/routing images under Anthropic. |
| `REFERENCE_IMAGE_HELPER_QWEN_MODEL` | Helper model for naming/routing images under Qwen. |
| `CONTEXT_SUMMARY_HELPER_OPENAI_MODEL` | Helper model for prompt-compaction summaries under OpenAI. |
| `CONTEXT_SUMMARY_HELPER_GEMINI_MODEL` | Helper model for prompt-compaction summaries under Gemini. |
| `CONTEXT_SUMMARY_HELPER_ANTHROPIC_MODEL` | Helper model for prompt-compaction summaries under Anthropic. |
| `CONTEXT_SUMMARY_HELPER_QWEN_MODEL` | Helper model for prompt-compaction summaries under Qwen. |

## 6. Tool Gating and External Services

### 6.1 Shared service routing

| Variable | Description |
| --- | --- |
| `TOOL_SERVICE_HOST` | Shared host/IP for colocated tool services. |
| `AGENT_TOOLS_ROOT` | Optional override for the `../3DAgentTools` checkout path. |

### 6.2 Retrieval and asset sources

| Variable | Description |
| --- | --- |
| `ENABLE_POLYHAVEN` | Enables PolyHaven search/download/material tools. |
| `ASSET_RETRIEVAL_BACKEND` | Selects `disabled`, `objaverse`, or `scenesmith`. |
| `OBJAVERSE_HOST` / `OBJAVERSE_PORT` | Objaverse-compatible retrieval service endpoint. |
| `SCENESMITH_COMPAT_HOST` / `SCENESMITH_COMPAT_PORT` | SceneSmith compatibility endpoint. |
| `ENABLE_AMBIENTCG` | Enables SceneSmith/AmbientCG material tooling independently. |
| `ENABLE_SKETCHFAB` | Enables Sketchfab tooling. |
| `SKETCHFAB_API_KEY` | Sketchfab API key. |
| `SKETCHFAB_API_BASE_URL` | Optional Sketchfab API base URL override. |

### 6.3 3D generation and reconstruction

| Variable | Description |
| --- | --- |
| `ENABLE_RODIN` | Enables Rodin tooling. |
| `RODIN_API_KEY` | Rodin API key. |
| `RODIN_MODE` | Rodin mode selection. |
| `ENABLE_TRIPO` | Enables Tripo3D tooling. |
| `TRIPO_API_KEY` | Tripo3D API key. |
| `ENABLE_TRELLIS2` | Enables TRELLIS2 tooling. |
| `TRELLIS2_HOST` / `TRELLIS2_PORT` | TRELLIS2 endpoint. |
| `ENABLE_HUNYUAN` | Enables Hunyuan3D tooling. |
| `HUNYUAN3D_SECRET_ID` | Hunyuan credential. |
| `HUNYUAN3D_SECRET_KEY` | Hunyuan credential. |
| `ENABLE_SAM_RECONSTRUCT` | Enables SAM-based reconstruction tooling. |
| `SAM_HOST` / `SAM_PORT` | SAM reconstruction service endpoint. |

### 6.4 PCG

| Variable | Description |
| --- | --- |
| `ENABLE_INFINIGEN` | Enables PCG / Infinigen tools. |
| `INFINIGEN_HOST` / `INFINIGEN_PORT` | PCG service endpoint. |

## 7. Frontend-Related Runtime Settings

| Variable | Description |
| --- | --- |
| `FRONTEND_SESSION_QUOTA_DEFAULT` | Default headless runtime quota per frontend client. |
| `FRONTEND_SESSION_QUOTA_OVERRIDES` | Per-client quota overrides. |
| `VITE_BACKEND_URL` | Shared frontend backend URL fallback. |
| `VITE_BACKEND_URL_DEV` | Development backend URL for Vite. |
| `VITE_BACKEND_URL_PROD` | Production backend URL for Vite. |

## 8. Practical Notes

- Prefer provider-specific credentials over assuming one global credential will cover every provider.
- Treat generator-family flags as mutually exclusive unless the code explicitly documents otherwise.
- In colocated external deployments, set `TOOL_SERVICE_HOST` once and override only the exceptions.
- For multi-worker and persistent operation, Redis plus shared storage should be treated as part of the baseline deployment, not as optional polish.
- Penetration verification settings are fail-fast: invalid values should be corrected in `.env` rather than relying on silent fallback.
- The penetration check supplements VLM-based verification. It is meant to catch more obvious mesh intersections, not to replace visual verification as the primary evidence source.

## 9. Related Docs

- [System Architecture](../architecture/system-architecture.md)
- [Runtime Workflow](../architecture/runtime-workflow.md)
- [MCP Server and Tools](../integrations/mcp-server-and-tools.md)
- [Tool Servers](../deployment/tool-servers.md)
