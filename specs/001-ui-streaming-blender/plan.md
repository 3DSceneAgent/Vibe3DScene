# Implementation Plan: UI Streaming and Headless Blender

**Branch**: `001-ui-streaming-blender` | **Date**: 2026-02-01 | **Spec**: `specs/001-ui-streaming-blender/spec.md`
**Input**: Feature specification from `specs/001-ui-streaming-blender/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Deliver a unified web workspace layout with scene controls on the left and chat on the right, add an in-message waiting indicator during agent replies, fix backend and frontend streaming to render incremental content, and introduce a headless Blender mode that creates per-session scenes on demand while preserving existing local-client behavior.

## Technical Context

**Language/Version**: Python 3.x (backend), TypeScript 5.9 (frontend)  
**Primary Dependencies**: FastAPI, LangGraph, LangChain, langchain-mcp-adapters, websockets, React 19, Vite 7, Three.js  
**Storage**: In-memory LangGraph checkpointer, browser `localStorage`, temporary filesystem for renders/exports  
**Testing**: pytest (backend), manual UI validation for layout/streaming, targeted unit tests for parsing/utilities  
**Target Platform**: Desktop web browsers (Chromium/Firefox/Safari), local or Linux server for API, Blender runtime available  
**Project Type**: Web app (Python backend at repo root + React frontend in `web/`)  
**Performance Goals**: First streamed content visible within 1s; headless session ready within 10s; UI remains responsive during streaming  
**Constraints**: No blocking on async paths; session isolation to avoid cross-session data leakage; preserve existing API contracts  
**Scale/Scope**: Single-tenant to small-team usage with tens of concurrent sessions

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Code quality: typed interfaces, modular changes, explicit error handling.
- Testing: unit + integration tests for streaming/session routing; manual UI validation steps documented.
- UX consistency: shared message parsing/rendering in chat; stable API response shapes.
- Performance: streaming is async-safe and avoids blocking; background Blender managed per session.
- Error recovery: actionable errors for missing Blender/local-client connectivity and stream failures.

**Post-Design Re-check**: No violations noted after research, data model, contracts, and quickstart artifacts.

## Project Structure

### Documentation (this feature)

```text
specs/001-ui-streaming-blender/
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
├── interfaces/          # CLI + API
├── memory/              # Scene/camera tracking
├── mcp/                 # MCP server
├── tools/               # Blender tool integration
├── vlm/                 # VLM provider abstraction
├── web/                 # React frontend
└── tests/               # (to be added) unit/integration tests
```

**Structure Decision**: Monorepo with Python backend at the root and React frontend under `web/`, plus feature documentation under `specs/001-ui-streaming-blender/`.

## Complexity Tracking

> No constitutional violations expected.
