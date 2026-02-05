# Implementation Plan: Headless Runtime Reliability

**Branch**: `004-fix-headless-blender` | **Date**: 2026-02-04 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `/specs/004-fix-headless-blender/spec.md`

## Summary

Improve server-only streaming reliability, enable concurrent request handling in the API runtime, ensure clean shutdown of headless Blender subprocesses, and render Markdown in chat messages with HTML treated as inert text. The plan adds explicit stream completion/error signaling, configurable multi-worker execution, lifecycle cleanup for spawned processes, and safe Markdown rendering in the web UI.

## Technical Context

**Language/Version**: Python >=3.10 for backend; TypeScript 5.9 for web  
**Primary Dependencies**: FastAPI, uvicorn, websockets, langgraph/langchain; React 19, Vite  
**Storage**: N/A (no persistent datastore; in-memory session state)  
**Testing**: pytest (unit/integration), manual UI validation for streaming/Markdown  
**Target Platform**: macOS/Linux server for backend; modern Chromium-based browsers for web UI  
**Project Type**: web application (Python backend + React frontend)  
**Performance Goals**: stream start <=5s and completion <=60s; handle 5 concurrent requests within 2x baseline; shutdown <=3s; UI remains responsive during streaming  
**Constraints**: headless Blender subprocess management; avoid blocking async paths; SSE must emit explicit completion or error events  
**Scale/Scope**: single service deployment, small-team usage, limited concurrent users

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Code quality: typed interfaces, modular changes, explicit error handling.  
- Testing: unit, integration, and UI validation steps defined; manual steps allowed for Blender-dependent flows.  
- UX consistency: shared message parsing and rendering utilities reused; API stream event shapes remain stable.  
- Performance: budgets aligned with spec success criteria; streaming avoids blocking operations.  
- Error recovery: user-facing errors and logging added for stream failures and shutdown issues.  
- Post-design re-check: no violations found.

## Project Structure

### Documentation (this feature)

```text
specs/004-fix-headless-blender/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
scene_agent/
├── agent/
├── blender/
├── interfaces/
└── memory/

mcp_server/
scripts/
web/
tests/
```

**Structure Decision**: Hybrid monorepo with Python backend in `scene_agent/` plus service scripts in `scripts/`, and React frontend in `web/`.

## Complexity Tracking

No constitution violations requiring justification.
