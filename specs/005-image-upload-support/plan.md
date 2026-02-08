# Implementation Plan: Image Upload Support

**Branch**: `001-image-upload-support` | **Date**: 2026-02-05 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `/specs/001-image-upload-support/spec.md`

## Summary

Add reference image uploads in the web UI and API, store them in short-term memory, and use the vision-language model to verify renders against all reference images (text-only verification when no images). Improve headless scene/render reliability with per-request diagnostics and timeouts, and fix service shutdown to terminate all processes on a single interrupt.

## Technical Context

**Language/Version**: Python >=3.10 for backend; TypeScript 5.9 for web  
**Primary Dependencies**: FastAPI, uvicorn, langgraph/langchain, pillow; React 19, Vite, Three.js  
**Storage**: local filesystem for short-term image files + in-memory metadata (no persistent datastore)  
**Testing**: pytest (unit/integration) for backend; manual UI validation for upload/verify flows  
**Target Platform**: macOS/Linux server for backend; modern Chromium-based browsers for web UI  
**Project Type**: web application (Python backend + React frontend)  
**Performance Goals**: upload ack <=2s; headless scene/render completes or errors <=30s; shutdown <=5s (measured via diagnostics timing logs and quickstart validation)  
**Constraints**: avoid blocking async paths; headless subprocesses must be cleaned up; uploads limited to 3 images per conversation  
**Scale/Scope**: single service deployment, small-team usage, limited concurrent users

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Code quality: typed interfaces, modular changes, explicit error handling.  
- Testing: unit + integration coverage for backend plus manual UI validation for uploads/verification.  
- UX consistency: shared UI patterns, consistent error handling, and stable API response shapes.  
- Performance: explicit budgets for uploads/headless renders; avoid blocking async paths.  
- Error recovery: user-facing fallbacks with request-scoped logs and diagnostics.  
- Post-design re-check: no violations found.

## Project Structure

### Documentation (this feature)

```text
specs/001-image-upload-support/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)
<!--
  ACTION REQUIRED: Replace the placeholder tree below with the concrete layout
  for this feature. Delete unused options and expand the chosen structure with
  real paths (e.g., apps/admin, packages/something). The delivered plan must
  not include Option labels.
-->

```text
scene_agent/
├── agent/
├── blender/
├── interfaces/
├── memory/
└── utils/

scripts/
mcp_server/
web/
tests/
```

**Structure Decision**: Hybrid monorepo with Python backend in `scene_agent/`, service scripts in `scripts/`, and React frontend in `web/`.

## Complexity Tracking

No constitution violations requiring justification.
