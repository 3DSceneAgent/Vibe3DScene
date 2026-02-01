<!--
Sync Impact Report
- Version change: 0.0.0 (template) -> 1.0.0
- Modified principles:
  - [PRINCIPLE_1_NAME] -> Code Quality & Maintainability
  - [PRINCIPLE_2_NAME] -> Testing Standards (Non-Negotiable)
  - [PRINCIPLE_3_NAME] -> User Experience Consistency & Interaction Contracts
  - [PRINCIPLE_4_NAME] -> Performance & Resource Discipline
  - [PRINCIPLE_5_NAME] -> Error Transparency & Recovery
- Added sections: None (placeholders filled)
- Removed sections: None
- Templates requiring updates:
  - ✅ .specify/templates/plan-template.md
  - ✅ .specify/templates/spec-template.md
  - ✅ .specify/templates/tasks-template.md
  - ⚠ .specify/templates/commands/*.md (directory not present)
- Follow-up TODOs: None
-->
# 3D Scene Agent Constitution

## Core Principles

### Code Quality & Maintainability
All production code MUST be readable, typed, and modular. New or changed logic
MUST include type hints (Python) or explicit types (TypeScript), keep functions
small with single responsibility, and remove dead code. Errors MUST be handled
explicitly with actionable messages; no silent failures. Rationale: maintainable
agent + UI code reduces regressions and speeds reviews.

### Testing Standards (Non-Negotiable)
Every behavior change MUST include tests proportional to risk: unit tests for
pure logic, integration tests for API/MCP boundaries, and UI validation for the
web app. If automation is impractical, manual steps MUST be documented in the
spec and plan. Bug fixes MUST include regression tests when feasible. Rationale:
guard against regressions in agent behavior and streaming UX.

### User Experience Consistency & Interaction Contracts
UI and API behavior MUST remain consistent across chat, scene, and settings.
Shared components and utilities are the default; no ad-hoc formatting or
duplicated logic. API response shapes and local storage schemas are contracts;
backward-incompatible changes require a migration plan and documented
compatibility notes. Rationale: stable workflows and predictable UI.

### Performance & Resource Discipline
Streaming responses MUST remain responsive; avoid blocking calls on async paths.
Blender render/export operations MUST be user-triggered, rate-limited, and
cached when appropriate. Frontend rendering MUST minimize expensive re-renders
and free heavy resources (e.g., GLTF scenes) when not visible. Rationale:
interactive feel and controlled resource usage.

### Error Transparency & Recovery
User-facing errors MUST be clear, actionable, and non-destructive, with safe
fallbacks for missing backend or Blender availability. Operational failures
MUST be logged with context for debugging. Rationale: avoid silent failure and
preserve UX trust.

## Testing & Quality Standards

- Specs and plans MUST define a test strategy covering unit, integration,
  and UI validation (automated or manual steps when automation is not feasible).
- Changes MUST pass configured linting and type checks; if new tooling is added,
  update docs and plan tasks to run it consistently.
- Integration tests requiring Blender MUST be isolated and provide a mock or
  explicit run instructions to keep CI deterministic.
- Performance and UX acceptance criteria MUST be captured in spec success
  criteria and validated before release.

## User Experience & Performance Requirements

- UI interaction patterns MUST remain consistent across tabs; use shared
  components/utilities for message parsing and rendering.
- Streaming UX MUST handle duplicates, thinking blocks, and partial events
  safely, always producing a final user-facing response.
- Network operations MUST expose loading, empty, and error states; no silent
  failures in the UI.
- Feature specs MUST define performance budgets (latency, render frequency,
  memory use) and the plan MUST include how they are measured.

## Governance

- The constitution supersedes plans, specs, and task lists; conflicts must be
  resolved in favor of this document.
- Amendments require updating this file, a semantic version bump, and a Sync
  Impact Report entry at the top of the document.
- Reviews MUST include a constitution compliance check; any violations require
  explicit justification in the plan's Constitution Check or Complexity section.
- Runtime guidance lives in `README.md` and `.cursor/plans/`; keep them aligned
  with constitutional changes when relevant.

**Version**: 1.0.0 | **Ratified**: 2026-02-01 | **Last Amended**: 2026-02-01
