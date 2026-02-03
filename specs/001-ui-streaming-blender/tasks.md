# Tasks: UI Streaming and Headless Blender

**Input**: Design documents from `specs/001-ui-streaming-blender/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/openapi.yaml`, `quickstart.md`

**Tests**: Tests are REQUIRED for code changes unless the spec explicitly documents a waiver with rationale and alternative validation steps.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish baseline test scaffolding and shared docs required for all changes.

- [x] T001 Create test scaffold directories with placeholders in `tests/contract/__init__.py`, `tests/integration/__init__.py`, `tests/unit/__init__.py`
- [x] T002 Add pytest configuration and common fixtures in `pytest.ini` and `tests/conftest.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared configuration and session primitives used across streaming and headless support.

- [x] T003 Add blender mode and headless settings to `scene_agent/config.py`
- [x] T004 Create session registry primitives in `scene_agent/blender/session_manager.py`

**Checkpoint**: Foundation ready - user story implementation can now begin.

---

## Phase 3: User Story 1 - Unified Workspace Layout (Priority: P1) 🎯 MVP

**Goal**: Present a single large workspace with scene controls on the left and chat on the right, without topbar tabs.

**Independent Test**: Open the web app and verify the split layout renders with both panels visible; resizing keeps both panels usable.

### Tests for User Story 1 (REQUIRED unless waived) ⚠️

- [x] T005 [US1] Add manual UI validation steps for split layout in `specs/001-ui-streaming-blender/quickstart.md`

### Implementation for User Story 1

- [x] T006 [US1] Remove topbar tabs and render split workspace in `web/src/App.tsx`
- [x] T007 [US1] Add split-view layout styles in `web/src/App.css`
- [x] T008 [P] [US1] Adjust chat pane sizing and scroll behavior in `web/src/components/ChatTab.tsx`
- [x] T009 [P] [US1] Adjust scene pane sizing and scroll behavior in `web/src/components/SceneTab.tsx`

**Checkpoint**: User Story 1 is fully functional and testable independently.

---

## Phase 4: User Story 2 - Streaming Agent Replies with Waiting Indicator (Priority: P2)

**Goal**: Stream agent replies incrementally and show a loading spinner inside the assistant message while streaming.

**Independent Test**: Send a message and see the assistant bubble appear with a spinner, then incremental content until completion.

### Tests for User Story 2 (REQUIRED unless waived) ⚠️

- [x] T010 [P] [US2] Add unit tests for streaming event serialization in `tests/unit/test_streaming_events.py`
- [x] T011 [P] [US2] Add integration test for SSE streaming in `tests/integration/test_chat_stream.py`
- [x] T012 [US2] Expand manual streaming UX validation steps in `specs/001-ui-streaming-blender/quickstart.md`

### Implementation for User Story 2

- [x] T013 [US2] Stream message deltas via SSE in `scene_agent/interfaces/api.py`
- [x] T014 [US2] Extend stream event typings in `web/src/api/types.ts`
- [x] T015 [US2] Handle incremental SSE events in `web/src/api/client.ts`
- [x] T016 [US2] Add message streaming state fields in `web/src/state/types.ts`
- [x] T017 [US2] Implement streaming accumulator helper in `web/src/utils/message.ts`
- [x] T018 [US2] Update streaming merge logic to append deltas in `web/src/App.tsx`
- [x] T019 [P] [US2] Add loading spinner component in `web/src/components/LoadingSpinner.tsx`
- [x] T020 [US2] Render spinner for in-progress messages in `web/src/components/MessageList.tsx`
- [x] T021 [US2] Add spinner animation styles in `web/src/App.css`
- [x] T022 [US2] Ensure stream errors mark message as error in `web/src/App.tsx` and `web/src/components/MessageList.tsx`

**Checkpoint**: User Story 2 streams content incrementally and shows a clear waiting indicator.

---

## Phase 5: User Story 3 - Headless Background Blender Sessions (Priority: P3)

**Goal**: Support headless Blender sessions created on demand per session ID while preserving local-client behavior.

**Independent Test**: Send a request with a new session ID in headless mode and confirm a new session is created and reused.

### Tests for User Story 3 (REQUIRED unless waived) ⚠️

- [x] T023 [P] [US3] Add unit tests for session lifecycle in `tests/unit/test_blender_sessions.py`
- [x] T024 [P] [US3] Add integration test for headless session creation in `tests/integration/test_headless_session.py`
- [x] T025 [US3] Expand headless/local-client validation steps in `specs/001-ui-streaming-blender/quickstart.md`

### Implementation for User Story 3

- [x] T026 [US3] Add headless session manager implementation in `scene_agent/blender/session_manager.py`
- [x] T027 [US3] Route Blender commands by session ID in `scene_agent/interfaces/api.py`
- [x] T028 [US3] Add session-aware tool loading in `scene_agent/tools/blender_tools.py`
- [x] T029 [US3] Use session-aware tools in `scene_agent/agent/graph.py`
- [x] T030 [US3] Document headless mode environment variables in `README.md`

**Checkpoint**: User Story 3 provisions headless sessions on demand and keeps local-client mode intact.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Cross-story validation, cleanup, and documentation.

- [ ] T031 [P] Validate manual steps in `specs/001-ui-streaming-blender/quickstart.md`
- [x] T032 [P] Run lint checks for frontend and backend and record results in `specs/001-ui-streaming-blender/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately.
- **Foundational (Phase 2)**: Depends on Setup completion - blocks all user stories.
- **User Stories (Phase 3+)**: Depend on Foundational completion.
- **Polish (Phase 6)**: Depends on all desired user stories being complete.

### User Story Dependencies

- **User Story 1 (P1)**: No dependencies on other stories.
- **User Story 2 (P2)**: Can proceed after Foundational; no functional dependency on US1.
- **User Story 3 (P3)**: Can proceed after Foundational; no functional dependency on US1/US2.

### Within Each User Story

- Tests (if included) must be written and fail before implementation.
- Types/helpers before UI/endpoint integration.
- Core implementation before integration and manual validation.

### Parallel Opportunities

- T001 and T002 can run in parallel.
- Within each user story, tasks marked [P] can run in parallel.
- User stories can be implemented in parallel after Phase 2 if staffing allows.

---

## Parallel Example: User Story 2

```text
Task: "Add unit tests for streaming event serialization in tests/unit/test_streaming_events.py"
Task: "Add integration test for SSE streaming in tests/integration/test_chat_stream.py"
Task: "Add loading spinner component in web/src/components/LoadingSpinner.tsx"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational
3. Complete Phase 3: User Story 1
4. Validate US1 using `specs/001-ui-streaming-blender/quickstart.md`

### Incremental Delivery

1. Finish Setup + Foundational.
2. Deliver US1 (layout) and validate.
3. Deliver US2 (streaming + spinner) and validate.
4. Deliver US3 (headless sessions) and validate.

---

## Notes

- [P] tasks can run in parallel because they touch different files or have no dependencies.
- Each user story should remain independently testable.
- Maintain stable API response shapes and document any compatibility notes.
