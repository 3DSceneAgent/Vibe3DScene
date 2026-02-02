# Implementation Plan: Scene UI Fixes

**Branch**: `002-scene-ui-fixes` | **Date**: 2026-02-02 | **Spec**: `specs/002-scene-ui-fixes/spec.md`
**Input**: Feature specification from `specs/002-scene-ui-fixes/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Revise the web UI layout and behavior to consolidate scene actions into a top bar, enable collapsible conversation history, ensure chat scrolling does not move the scene panel, add a GLTF download action, constrain long scene/renders lists to scrollable containers, and correct streaming message rendering so assistant messages do not concatenate.

## Technical Context

**Language/Version**: Python 3.x (backend), TypeScript 5.9 (frontend)  
**Primary Dependencies**: FastAPI, LangGraph/LangChain, websockets, React 19, Vite 7, Three.js  
**Storage**: In-memory agent state, temporary filesystem exports, browser `localStorage` for threads/settings  
**Testing**: pytest for backend, manual UI validation for layout/scrolling, unit tests for message parsing where feasible  
**Target Platform**: Desktop web browsers (Chromium/Firefox/Safari), local or Linux server for API, Blender runtime available  
**Project Type**: Web app (Python backend at repo root + React frontend in `web/`)  
**Performance Goals**: Chat streaming remains responsive (<300ms UI update cadence); scene lists remain smooth with 200+ items; GLTF export completes within 10s for typical scenes  
**Constraints**: No blocking calls on async paths; preserve existing API response shapes; scroll behavior must remain isolated per panel  
**Scale/Scope**: Single-tenant or small-team usage, tens of threads stored in browser state

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Code quality: typed interfaces, modular changes, explicit error handling.
- Testing: unit + integration tests for streaming and API responses; manual UI validation for layout/scroll behavior.
- UX consistency: shared UI patterns for chat + scene panels; stable API and storage contracts.
- Performance: explicit scroll/streaming budgets; avoid blocking on render/export calls.
- Error recovery: clear UI errors for missing scene/GLTF download failures and missing Blender connectivity.

**Post-Design Re-check**: No violations noted after research, data model, contracts, and quickstart artifacts.

## Project Structure

### Documentation (this feature)

```text
specs/002-scene-ui-fixes/
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
├── agent/               # LangGraph state machine
├── blender/             # Blender session manager
├── interfaces/          # CLI + FastAPI API
├── memory/              # Scene/camera tracking
├── mcp/                 # MCP server
├── tools/               # Blender tool integration
├── vlm/                 # VLM provider abstraction
├── web/                 # React frontend
└── tests/               # unit/integration tests
```

**Structure Decision**: Monorepo with Python backend at the root and React frontend under `web/`, plus feature documentation under `specs/002-scene-ui-fixes/`.

## Complexity Tracking

> No constitutional violations expected.
