# 3DSceneAgent Project Reference

This document is a full technical reference for the current repository state.  
It is intended to be the primary onboarding and implementation baseline for future development.

## 1) Project Background

`3DSceneAgent` is a LangGraph-based 3D scene automation system for Blender.

Core idea:
- Users describe scene tasks in natural language (optionally with reference images).
- A LangGraph agent plans and calls tools.
- Tools are exposed via MCP and executed by a Blender addon socket server.
- Results stream back through FastAPI (SSE/WebSocket) and are consumed by a React web app or CLI.

The repository also includes:
- A vendored/linked Blender addon codebase under `addon/`.
- Service/runtime scripts for local and headless workflows.
- Tests across unit/integration/manual layers.
- Specification and planning artifacts used during iterative refactors.

## 2) High-Level Architecture

Logical flow:

1. Client (`web` or CLI) sends request to FastAPI.
2. FastAPI builds or reuses a LangGraph agent graph.
3. Agent uses MCP-discovered tools (`langchain-mcp-adapters`) from `mcp_server/server.py`.
4. MCP server translates tool calls into socket commands for Blender addon server.
5. Blender addon executes commands in Blender main thread and returns results.
6. API streams structured events (`delta`, `messages`, `todos`, `done`) back to clients.

Key runtime modes:
- `local-client`: connect to an already running Blender addon socket.
- `headless`: auto-manage headless Blender process and per-thread MCP process/session on demand (no global MCP process required at API startup).

## 3) Technology Stack

### Backend
- Python 3.10+
- LangGraph + LangChain ecosystem
- FastAPI + Uvicorn
- MCP (`mcp.server.fastmcp`) for tool serving
- Pydantic + `pydantic-settings`
- Pillow for image validation/processing
- Requests/AIOHTTP/WebSockets

### Model Providers
- OpenAI (`ChatOpenAI`)
- Anthropic (`ChatAnthropic`)
- Gemini (`ChatGoogleGenerativeAI`)

### Frontend
- Vite + React + TypeScript
- Three.js + `GLTFLoader` + `OrbitControls`
- `react-markdown` + `remark-gfm`
- IndexedDB + localStorage fallback

### Blender / 3D
- Blender addon socket server (`addon/blender_mcpv_addon/server.py`)
- Asset integrations:
  - PolyHaven
  - Retrieval API
  - TRELLIS2
  - Infinigen

## 4) Runtime Dependencies

### Python dependencies (`requirements.txt`)
- `langgraph`
- `langchain`
- `langchain-core`
- `langchain-mcp-adapters`
- `langchain-openai`
- `langchain-anthropic`
- `langchain-google-genai`
- `fastapi`
- `uvicorn`
- `websockets`
- `pydantic`
- `pydantic-settings`
- `python-dotenv`
- `chromadb`
- `langchain-community`
- `pillow`
- `rich`
- `requests`
- `aiohttp`
- `trio`
- `pytest`

### Frontend dependencies (`web/package.json`)
- Runtime:
  - `react`, `react-dom`
  - `three`
  - `react-markdown`, `remark-gfm`
- Dev:
  - `vite`, `typescript`, ESLint stack
  - React and Node type packages

### External services optionally used
- Blender desktop/headless process
- TRELLIS2 API
- Retrieval API
- Infinigen API
- PolyHaven API

## 5) Configuration and Environment

Primary env keys (from `.env.example` and `scene_agent/config.py`):
- Model: `VLM_PROVIDER`, `VLM_API_KEY`, `VLM_MODEL`
- Blender socket: `BLENDER_MODE`, `BLENDER_HOST`, `BLENDER_PORT`
- MCP server: `MCP_SERVER_HOST`, `MCP_SERVER_PORT`
- API runtime: `API_WORKERS`, `API_STREAM_TIMEOUT_SECONDS`
- Headless process: `BLENDER_HEADLESS_CMD`, `BLENDER_HEADLESS_ARGS`, log/port settings
- Limits: `REFERENCE_IMAGE_MAX_COUNT`, `REFERENCE_IMAGE_MAX_BYTES`, storage directory
- Service endpoints: `RETRIEVAL_API_*`, `TRELLIS2_*`, `INFINIGEN_*`

Important note:
- Legacy startup scripts were removed (`scripts/start_services.sh`, `scripts/start_services.py`). Prefer current scripts in `scripts/`.
- `BLENDER_MODE` can be overridden from startup script parameters (`--blender-mode`), which takes precedence over values loaded from `.env`.

## 6) Technical Conventions and Standards

Observed conventions in codebase:
- Typed Python interfaces with `TypedDict`, dataclasses, explicit reducers.
- LangGraph state machine with `ToolNode`, conditional routing, `MemorySaver`.
- Async streaming API with SSE keepalive and timeout controls.
- Structured agent metadata in tagged blocks:
  - `<todos>...</todos>`
  - `<agent_decision>{...}</agent_decision>`
- Headless diagnostics:
  - request IDs
  - elapsed time
  - process/log metadata
- Frontend persistence:
  - IndexedDB-first
  - localStorage fallback
  - storage size mitigation
- Tool outputs in UI:
  - tool blocks collapsed by default
  - markdown/image parsing support

## 7) API and Event Contracts (Current)

Key HTTP endpoints (`scene_agent/interfaces/api.py`):
- `GET /`
- `GET /health`
- `POST /chat`
- `POST /chat/stream` (SSE)
- `GET /scene/{thread_id}`
- `GET /scene/{thread_id}/renders`
- `GET /scene/{thread_id}/gltf`
- `GET /scene/{thread_id}/blend`
- `POST /threads/{thread_id}/reference-images`
- `GET /threads/{thread_id}/reference-images`
- `GET /todos/{thread_id}`
- `GET /threads`
- `WS /ws`

SSE event payload patterns:
- `{ "delta": "...", "message_id": "..." }`
- `{ "messages": [...] }`
- `{ "todos": [...] }`
- `{ "error": "..." }`
- `{ "event": "done", "scene_has_change": bool }`

## 8) Project Structure and File Purposes

The following list is organized by directory and focuses on source, config, scripts, tests, and project docs (excluding `node_modules` and generated cache files).

### Repository root
- `.env.example`: canonical environment template for backend/headless/service settings.
- `.gitignore`: repository ignore rules.
- `.gitmodules`: declares `addon` as a git submodule source.
- `README.md`: top-level overview, setup, architecture, and quickstart.
- `main.py`: backend entrypoint (`cli`/`api` modes).
- `pytest.ini`: pytest discovery configuration.
- `requirements.txt`: Python dependency list.

### `.cursor/commands`
- `speckit.analyze.md`: spec workflow command reference.
- `speckit.checklist.md`: checklist generation command.
- `speckit.clarify.md`: requirement clarification command.
- `speckit.constitution.md`: project constitution command.
- `speckit.implement.md`: implementation command template.
- `speckit.plan.md`: planning command template.
- `speckit.specify.md`: specification command template.
- `speckit.tasks.md`: task generation command.
- `speckit.taskstoissues.md`: convert tasks to issues command.

### `.cursor/plans`
- `001_3d_scene_agent_framework_646ae945.plan.md`: initial LangGraph framework design.
- `002_sceneagent_refactor_6a0ca674.plan.md`: package refactor into `scene_agent`.
- `003_headless_blender_纯同步重构_2d3928f6.plan.md`: headless synchronous architecture refactor notes.
- `004_blender_连接架构重构_4825a7a7.plan.md`: connection architecture and multi-client redesign.
- `005_修复前端解析与插件问题_50725b99.plan.md`: frontend parsing + addon registration fixes.
- `006_修复消息显示与存储问题_7a319dad.plan.md`: message rendering/storage/export fixes.
- `007_web-chat-ui_1f293d8f.plan.md`: web UI implementation plan.

### `.cursor/rules`
- `specify-rules.mdc`: Cursor rule file for spec workflow behavior.

### `.specify/memory`
- `constitution.md`: specification framework constitution/principles.

### `.specify/scripts/bash`
- `check-prerequisites.sh`: validates environment prerequisites.
- `common.sh`: shared helper shell functions for specify scripts.
- `create-new-feature.sh`: scaffold a new feature spec package.
- `setup-plan.sh`: initialize plan artifacts.
- `update-agent-context.sh`: refresh agent context metadata.

### `.specify/templates`
- `agent-file-template.md`: template for agent context files.
- `checklist-template.md`: generic checklist template.
- `plan-template.md`: implementation plan template.
- `spec-template.md`: feature specification template.
- `tasks-template.md`: task list template.

### `assets`
- `polyhaven_meta.json`: cached metadata/categories for PolyHaven assets.

### `docs/cursor_created`
- `refactor_test_summary.md`: generated summary of refactor/testing efforts.

### `docs/issues`
- `headless-blender-daemon-design.md`: design discussion for headless daemon behavior.
- `headless-blender-known-issues.md`: known issues catalog for headless workflows.
- `proj_issues_0208.md`: issue tracking snapshot.

### `legacy`
- `graph-workflow.md`: legacy graph-flow explanation with verify routing.
- `streaming-response.md`: legacy streaming response notes.
- `start_services.py`: deprecated legacy dual-service launcher.

### `mcp_server`
- `server.py`: MCP server implementation; exposes Blender/asset/camera/render tools and prompt.
- MCP tools are registered at startup with conditional availability checks:
  - `generate_trellis2_model` only when TRELLIS2 health check passes.
  - `get_infinigen_available_assets` / `generate_infinigen_assets` only when PCGIntegrator health check passes.
  - `search_3d_assets_by_text` only when Retrieval health check passes.
  - Startup logs print both service health probe results and the final enabled tool list.

### `scene_agent` (backend package)
- `__init__.py`: package marker.
- `config.py`: typed settings model and singleton settings loader.

#### `scene_agent/agent`
- `__init__.py`: subpackage marker.
- `graph.py`: LangGraph state machine composition and routing.
- `nodes.py`: agent/update-memory/verify node logic and parsing helpers.
- `prompts.py`: system prompt and asset strategy prompt.
- `state.py`: `AgentState`, reducers, todo/reference/diagnostic types.

#### `scene_agent/blender`
- `__init__.py`: subpackage marker.
- `connection.py`: TCP socket client for Blender command protocol.
- `session_manager.py`: headless session lifecycle, port allocation, process startup/shutdown.

#### `scene_agent/interfaces`
- `__init__.py`: subpackage marker.
- `api.py`: FastAPI app, streaming endpoints, scene exports, reference image APIs.
- `cli.py`: interactive terminal client with streaming output and commands.

#### `scene_agent/memory`
- `__init__.py`: subpackage marker.
- `camera_memory.py`: camera rendering history container.
- `reference_image_memory.py`: reference image validation/storage/metadata memory.
- `scene_memory.py`: scene info parsing and geometry utility helpers.

#### `scene_agent/rag`
- `__init__.py`: subpackage marker.
- `retriever.py`: placeholder BPY retrieval wrapper.
- `vector_store.py`: placeholder vector store abstraction for future RAG.

#### `scene_agent/tools`
- `__init__.py`: tool exports.
- `base.py`: common tool utility functions.
- `blender_tools.py`: MCP tool bootstrap and headless MCP process management.

#### `scene_agent/utils`
- `__init__.py`: subpackage marker.
- `diagnostics.py`: request timing and diagnostic record utilities.
- `logging.py`: structured JSON-style log helper.
- `rendering.py`: post-process/save render images and URL path generation.

#### `scene_agent/vlm`
- `__init__.py`: VLM exports.
- `base.py`: abstract provider interface.
- `providers.py`: OpenAI/Anthropic/Gemini provider factories.
- `verification.py`: VLM-based render-vs-reference verification routine.

### `scripts`
- `README.md`: operational notes for service scripts (partly legacy references).
- `blender_headless_client.py`: Blender-side bootstrap script for enabling addon and starting blocking server.
- `run_headless.sh`: startup script defaulting to `headless` mode; skips global MCP startup and relies on on-demand MCP per session.
- `run_local_client.sh`: startup script defaulting to `local-client` mode; starts MCP + API, while still allowing `--blender-mode` override.
- `stream_payload_harness.py`: SSE debug harness for `/chat/stream`.
- `helpers/install_headless_addon.sh`: helper to install addon into Blender addon directory.

### `scene_agent`
- `utils/health_check_services.py`: reusable Python health-check utility class + CLI for TRELLIS2 / Retrieval / PCGIntegrator.

### `specs` (feature specifications)

Each feature folder follows a common structure:
- `spec.md`: feature requirements/specification.
- `plan.md`: implementation planning.
- `tasks.md`: execution task list.
- `research.md`: notes/research findings.
- `data-model.md`: data model definitions.
- `quickstart.md`: dev/test quickstart for that feature.
- `contracts/*.yaml`: API contract/OpenAPI fragment.
- `checklists/requirements.md`: requirement completion checklist.

Feature folders:
- `001-ui-streaming-blender/` (streaming UI + Blender integration specs)
- `001-image-upload-support/` (reference image upload specs)
- `002-scene-ui-fixes/` (scene UI bug-fix specs)
- `003-scene-auto-fetch/` (auto-fetch scene/renders/glTF behavior specs)
- `004-fix-headless-blender/` (headless reliability specs)
- `005-image-upload-support/` (follow-up/iteration of image upload specs)

### `tests`
- `conftest.py`: test fixtures and baseline env setup.
- `test_refactor_blender.py`: ad-hoc/manual refactor verification script.

#### `tests/contract`
- `__init__.py`: package marker.
- `test_reference_images.py`: API contract checks for reference image endpoints.

#### `tests/integration`
- `__init__.py`: package marker.
- `streaming_helpers.py`: SSE payload parsing helper utilities.
- `test_chat_stream.py`: streaming endpoint behavior tests.
- `test_concurrent_chat.py`: concurrent stream request behavior tests.
- `test_headless_diagnostics.py`: headless diagnostics payload/timeout tests.
- `test_headless_session.py`: headless session creation behavior tests.
- `test_reference_images.py`: integration tests for image upload/list.

#### `tests/manual`
- `__init__.py`: package marker.
- `README.md`: how to run manual Blender concurrency/headless tests.
- `test_direct_socket.py`: raw socket command verification.
- `test_headless_automanaged.py`: auto-managed headless flow checks.
- `test_multi_client_concurrent.py`: concurrent client performance checks.
- `test_web_agent_concurrent.py`: mixed workload concurrency test.

#### `tests/unit`
- `__init__.py`: package marker.
- `test_blender_sessions.py`: session manager lifecycle/unit checks.
- `test_reference_image_memory.py`: reference image memory limits and behavior.
- `test_session_manager_cleanup.py`: process cleanup/shutdown tests.
- `test_session_manager_logging.py`: startup logging redirection behavior.
- `test_streaming_events.py`: stream serialization helper tests.

### `web` (frontend app)
- `.gitignore`: frontend ignore rules.
- `eslint.config.js`: TS/React lint config.
- `index.html`: Vite app shell.
- `package.json`: frontend scripts and dependencies.
- `package-lock.json`: npm lockfile.
- `README.md`: web app usage notes.
- `tsconfig.json`: base TS config.
- `tsconfig.app.json`: app-specific TS config.
- `tsconfig.node.json`: node tooling TS config.
- `vite.config.ts`: Vite + React plugin setup.
- `public/vite.svg`: static asset.
- `src/assets/react.svg`: sample static asset.
- `src/main.tsx`: React bootstrap.
- `src/index.css`: global theme and markdown baseline styles.
- `src/App.css`: full app layout/component styles.
- `src/App.tsx`: main application state, streaming orchestration, scene actions.

#### `web/src/api`
- `client.ts`: HTTP/SSE client functions for backend endpoints.
- `types.ts`: API payload type definitions.

#### `web/src/components`
- `ChatComposer.tsx`: prompt input, drag/drop image upload, send/stop actions.
- `ChatTab.tsx`: chat pane composition.
- `GltfViewer.tsx`: Three.js GLB renderer + lighting presets.
- `LoadingSpinner.tsx`: inline streaming indicator.
- `MarkdownMessage.tsx`: markdown renderer with safe links/images.
- `MessageList.tsx`: message feed rendering (assistant/tool/todos/thinking).
- `ReferenceImageStrip.tsx`: uploaded reference image preview strip.
- `RenderGallery.tsx`: camera render thumbnails.
- `SceneInfoPanel.tsx`: scene object summary panel.
- `SceneTab.tsx`: scene page composition.
- `SettingsPanel.tsx`: backend URL/theme settings UI.
- `ThreadList.tsx`: thread navigation/create/delete.
- `TodosPanel.tsx`: todo status panel component.
- `ToolResultBlock.tsx`: collapsible tool-output block with media display.
- `TopBar.tsx`: action toolbar (refresh/render/load/export/settings/status).

#### `web/src/state`
- `indexeddb.ts`: IndexedDB persistence primitives.
- `storage.ts`: storage abstraction with migration/fallback logic.
- `types.ts`: frontend domain types (`Thread`, `Message`, `Settings`).

#### `web/src/utils`
- `download.ts`: browser blob download helper.
- `message.ts`: stream/message parsing, thinking/todo extraction, tool media extraction.

### `addon` (external addon submodule + local integration files)

`addon/` is maintained as a separate project and linked via submodule (`.gitmodules` root).  
It is used as the Blender-side execution engine for MCP commands.

Top-level addon files:
- `README.md`: upstream addon project docs and integration instructions.
- `LICENSE`: addon project license.
- `pyproject.toml`: addon package metadata and dependencies.
- `uv.lock`: addon lockfile.
- `main.py`: addon package entrypoint.
- `.python-version`: Python version pin for addon project.
- `.gitignore`, `.gitmodules`, `.git`: addon-internal repo metadata files.

Addon source:
- `src/blender_mcpv/__init__.py`: package marker.
- `src/blender_mcpv/server.py`: upstream MCP server entry implementation.
- `blender_mcpv_addon/__init__.py`: Blender addon register/unregister and server entrypoints.
- `blender_mcpv_addon/server.py`: socket command server, multi-client queue, render/camera ops.
- `blender_mcpv_addon/asset_handlers.py`: PolyHaven/TRELLIS2/Infinigen/retrieval import logic.
- `blender_mcpv_addon/camera_manager.py`: camera reuse and frustum-based visibility tracking.
- `blender_mcpv_addon/chat.py`: custom backend SSE chat bridge for Blender UI.
- `blender_mcpv_addon/operators.py`: Blender operators (start/stop server, send chat).
- `blender_mcpv_addon/ui.py`: Blender panel and chat UI list.
- `blender_mcpv_addon/properties.py`: Blender property group definitions.
- `blender_mcpv_addon/README.md`: addon module structure/install doc.

Addon scripts:
- `scripts/package_addon.sh`: zip packaging for Blender install.
- `scripts/update_addon.sh`: copy addon into Blender addons directory.
- `scripts/test_addon_imports.py`: addon import sanity check script.
- `scripts/healthcheck_services.sh`: external service health check for addon ecosystem.

Addon docs/assets/plans:
- `docs/retreival_api_doc.md`: retrieval API documentation.
- `docs/trellis2_api_doc.md`: TRELLIS2 API documentation.
- `docs/vibe_coding/custom_backend.md`: custom backend notes.
- `docs/vibe_coding/followup1.md`: follow-up implementation notes.
- `docs/vibe_coding/prompt.md`: prompt strategy notes.
- `docs/vibe_coding/trellis2.md`: TRELLIS2 notes.
- `assets/polyhaven_meta.json`: PolyHaven metadata copy for addon context.
- `assets/example_prompts.md`: prompt examples.
- `.cursor/plans/*.plan.md`: addon-side planning artifacts.

## 9) Known Gaps / Maintenance Notes

- `README.md` and some script docs still mention removed startup scripts.
- `scene_agent/interfaces/api.py` still contains both shared and per-thread headless connection logic; maintainers should keep this aligned with current architecture decisions.
- RAG components are placeholders (`scene_agent/rag/*`) and not production-enabled by default.
- `addon/` evolves independently; sync policy should be explicit during upgrades.

## 10) Recommended Next Documentation Steps

- Keep this file as the canonical "engineering map", update it in every major refactor.
- Add an architecture decision log (`docs/adr/`) for mode/session/routing decisions.
- Add endpoint schema snapshots (OpenAPI export) for stable client integration.
- Add a small "operational runbook" for local-client vs headless troubleshooting.

