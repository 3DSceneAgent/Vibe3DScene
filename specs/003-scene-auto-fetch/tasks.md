---

description: "Task list for Scene Auto Fetch & Streaming UI"
---

# Tasks: Scene Auto Fetch & Streaming UI

**Input**: Design documents from `/specs/001-scene-auto-fetch/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/, quickstart.md

**Tests**: Tests are REQUIRED for code changes unless the spec explicitly documents a waiver.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish shared types, contracts, and UI hooks used across stories

- [x] T001 Update stream contract with scene change flag and tool payloads in `specs/001-scene-auto-fetch/contracts/openapi.yaml`
- [x] T002 Extend settings defaults for auto-refresh + scene tab collapse in `web/src/state/types.ts` and `web/src/state/storage.ts`
- [x] T003 [P] Add base CSS hooks for tool blocks and scene collapse UI in `web/src/App.css`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Backend stream contract + shared parsing utilities required by all stories

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T004 Add scene-change detection helper for tool calls in `scene_agent/interfaces/api.py`
- [x] T005 Emit tool messages + `scene_has_change` in SSE payloads in `scene_agent/interfaces/api.py`
- [x] T006 Update serialization tests for tool messages and scene change flags in `tests/unit/test_streaming_events.py`
- [x] T007 [P] Update SSE integration stub to include tool messages + scene flag in `tests/integration/test_chat_stream.py`
- [x] T008 Update stream event typings with `scene_has_change` and tool payloads in `web/src/api/types.ts`
- [x] T009 Update stream parsing helpers for tool payloads/media in `web/src/utils/message.ts`

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Auto-refresh scene assets (Priority: P1) 🎯 MVP

**Goal**: Auto-fetch renders + glTF only after a scene-changing reply completes and the user enables auto-refresh.

**Independent Test**: Toggle auto-refresh on/off, stream a scene-changing reply, and verify refresh triggers only on completion.

### Tests for User Story 1 (REQUIRED unless waived) ⚠️

- [x] T010 [US1] Add manual auto-refresh validation steps in `specs/001-scene-auto-fetch/quickstart.md`

### Implementation for User Story 1

- [x] T011 [US1] Add auto-refresh toggle UI in `web/src/components/SettingsPanel.tsx`
- [x] T012 [US1] Persist auto-refresh preference and apply defaults in `web/src/state/storage.ts` and `web/src/App.tsx`
- [x] T013 [US1] Track per-thread `scene_has_change` during streaming in `web/src/App.tsx` and `web/src/state/types.ts`
- [x] T014 [US1] Trigger `getSceneRenders` + `getSceneGltf` only on `event: done` when auto-refresh is enabled in `web/src/App.tsx`

**Checkpoint**: User Story 1 is fully functional and testable independently

---

## Phase 4: User Story 3 - Understand streaming response structure (Priority: P3)

**Goal**: Provide concise documentation describing streaming response structure (text, reasoning, tool calls, images).

**Independent Test**: Verify the documentation exists and covers message parts and ordering.

### Tests for User Story 3 (REQUIRED unless waived) ⚠️

- [x] T015 [US3] Add documentation validation steps in `specs/001-scene-auto-fetch/quickstart.md`

### Implementation for User Story 3

- [x] T016 [US3] Author streaming response doc in `docs/streaming-response.md`
- [x] T017 [US3] Link streaming doc from `README.md`
- [x] T018 [US3] Add backend stream payload harness in `scripts/stream_payload_harness.py`

**Checkpoint**: Documentation complete and discoverable

---

## Phase 5: User Story 2 - Readable streaming agent responses (Priority: P2)

**Goal**: Render tool/action outputs and images inline during streaming, with collapsible action blocks.

**Independent Test**: Stream a mixed response and confirm ordering, media rendering, and collapsible action blocks.

### Tests for User Story 2 (REQUIRED unless waived) ⚠️

- [x] T019 [US2] Add manual streaming + tool block validation steps in `specs/001-scene-auto-fetch/quickstart.md`

### Implementation for User Story 2

- [x] T020 [US2] Extend message model to include action/tool entries in `web/src/state/types.ts`
- [x] T021 [US2] Preserve tool messages in stream handling order in `web/src/App.tsx`
- [x] T022 [US2] Create collapsible tool output component in `web/src/components/ToolResultBlock.tsx`
- [x] T023 [US2] Render tool blocks alongside assistant messages in `web/src/components/MessageList.tsx`
- [x] T024 [US2] Parse tool payloads + media references in `web/src/utils/message.ts`
- [x] T025 [US2] Add tool block styling + collapse affordances in `web/src/App.css`

**Checkpoint**: User Stories 1–3 work independently and together

---

## Phase 6: User Story 4 - Collapse the Scene tab (Priority: P3)

**Goal**: Allow users to collapse/expand the Scene tab without losing state.

**Independent Test**: Collapse and expand the Scene tab and confirm scene content persists.

### Tests for User Story 4 (REQUIRED unless waived) ⚠️

- [x] T026 [US4] Add Scene tab collapse validation steps in `specs/001-scene-auto-fetch/quickstart.md`

### Implementation for User Story 4

- [x] T027 [US4] Add Scene tab collapse state in `web/src/App.tsx` and `web/src/state/storage.ts`
- [x] T028 [US4] Add collapse control + conditional rendering in `web/src/components/SceneTab.tsx`
- [x] T029 [US4] Add collapse styling for Scene tab in `web/src/App.css`

**Checkpoint**: All user stories now independently functional

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final configuration polish, documentation, and validation

- [x] T030 [P] Set `BLENDER_HEADLESS_CMD` + `BLENDER_HEADLESS_ARGS` defaults in `scripts/start_services.sh` and `scripts/start_services.py`
- [x] T031 [P] Update headless configuration docs in `README.md` and `scripts/README.md`
- [x] T032 Run quickstart validation log update in `specs/001-scene-auto-fetch/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational phase completion
- **Polish (Phase 7)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) - no dependency on other stories
- **User Story 3 (P3)**: Can start after Foundational (Phase 2) - documentation only
- **User Story 2 (P2)**: Starts after User Story 3 to inform UI parsing/rendering decisions
- **User Story 4 (P3)**: Can start after Foundational (Phase 2) - UI-only changes

### Parallel Opportunities

- T003, T007, T029, T030 can run in parallel with other non-dependent tasks
- User Stories 3–4 can proceed in parallel once Foundational is complete
- Documentation tasks (US3) should complete before US2 to guide UI work

---

## Parallel Example: User Story 2

```bash
Task: "Create collapsible tool output component in web/src/components/ToolResultBlock.tsx"
Task: "Parse tool payloads + media references in web/src/utils/message.ts"
Task: "Add tool block styling + collapse affordances in web/src/App.css"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: Test User Story 1 independently

### Incremental Delivery

1. Complete Setup + Foundational → Foundation ready
2. Add User Story 1 → Test independently → Demo (MVP)
3. Add User Story 3 → Test independently → Demo
4. Add User Story 2 → Test independently → Demo
5. Add User Story 4 → Test independently → Demo

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Manual UI validation steps are documented in quickstart when automation is impractical
