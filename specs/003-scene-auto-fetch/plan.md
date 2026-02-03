# Implementation Plan: Scene Auto Fetch & Streaming UI

**Branch**: `001-scene-auto-fetch` | **Date**: 2026-02-03 | **Spec**: `specs/001-scene-auto-fetch/spec.md`
**Input**: Feature specification from `specs/001-scene-auto-fetch/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Add auto-refresh controls that fetch scene renders and GLTF only after a scene-changing reply completes, enrich streaming responses with action output and images (plus collapsible sections), document streaming response structure, and ensure headless Blender startup is configurable via environment variables while supporting a collapsible Scene tab UI.

## Technical Context

**Language/Version**: Python 3.x (backend), TypeScript 5.9 (frontend)  
**Primary Dependencies**: FastAPI, LangGraph, LangChain, langchain-mcp-adapters, React 19, Vite 7, Three.js  
**Storage**: In-memory LangGraph checkpointer, browser `localStorage`, temporary filesystem for renders/exports  
**Testing**: pytest (backend), manual UI validation for streaming/collapse, targeted unit tests for message parsing and auto-refresh gating  
**Target Platform**: Desktop web browsers (Chromium/Firefox/Safari), local or Linux server for API, Blender runtime available  
**Project Type**: Web app (Python backend at repo root + React frontend in `web/`)  
**Performance Goals**: Stream updates visible within 1s; UI interactions <200ms for 95% of actions; scene refresh visible within 5s of completion  
**Constraints**: Avoid redundant scene fetches; preserve SSE stream contract; handle tool/image payloads without blocking UI; keep scene resources managed when hidden  
**Scale/Scope**: Single-tenant to small-team usage with tens of concurrent sessions

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Code quality: typed interfaces, modular changes, explicit error handling.
- Testing: unit coverage for parsing/gating; integration checks for SSE payloads; manual UI validation for auto-refresh and collapsible UI.
- UX consistency: shared message parsing/rendering for action output, stable stream contract updates.
- Performance: avoid blocking in streaming handlers; gate expensive render/export calls.
- Error recovery: user-visible errors for failed fetches, missing Blender connectivity, or invalid stream payloads.

**Post-Design Re-check**: No constitutional violations noted after research, data model, contracts, and quickstart artifacts.

## Project Structure

### Documentation (this feature)

```text
specs/001-scene-auto-fetch/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
3DSceneAgent/
├── scene_agent/         # LangGraph agent, API, tools, memory
├── mcp_server/          # MCP server launcher
├── scripts/             # Headless client + service helpers
├── web/                 # React frontend
├── docs/                # Project docs
└── tests/               # Unit/integration tests
```

**Structure Decision**: Monorepo with Python backend at the root and React frontend under `web/`, plus feature documentation under `specs/001-scene-auto-fetch/`.

## Complexity Tracking

> No constitutional violations expected.
