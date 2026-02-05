# Tasks: Headless Runtime Reliability

**Input**: Design documents from `/specs/004-fix-headless-blender/`  
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/  
**Tests**: Tests are REQUIRED for code changes unless the spec explicitly documents a waiver. Manual validation steps are included where automation is impractical.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and shared configuration

- [x] T001 Add API worker and stream timeout settings in `scene_agent/config.py`
- [x] T002 [P] Document new runtime env vars in `.env.example`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared utilities used across multiple stories

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T003 [P] Add structured logging helper in `scene_agent/utils/logging.py`
- [x] T004 [P] Add SSE stream test helpers in `tests/integration/streaming_helpers.py`

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Reliable live responses in server-only mode (Priority: P1) 🎯 MVP

**Goal**: Ensure streaming replies complete reliably with explicit done/error events and user-visible failures.

**Independent Test**: Send a streaming request in headless mode and confirm the stream starts, emits data, and ends with `event: done` or `error` without indefinite loading.

### Tests for User Story 1 (REQUIRED) ⚠️

- [x] T005 [P] [US1] Add integration test for terminal `event: done` in `tests/integration/test_chat_stream.py`
- [x] T006 [P] [US1] Add integration test for error event on failure in `tests/integration/test_chat_stream.py`

### Implementation for User Story 1

- [x] T007 [US1] Add SSE response headers and keepalive behavior in `scene_agent/interfaces/api.py`
- [x] T008 [US1] Ensure stream generator always emits error + done terminal events in `scene_agent/interfaces/api.py`
- [x] T009 [US1] Log stream failures with timestamp and request_id in `scene_agent/interfaces/api.py` (use `scene_agent/utils/logging.py`)
- [x] T010 [US1] Emit client-side error on unexpected stream close in `web/src/api/client.ts`

**Checkpoint**: User Story 1 should be fully functional and testable independently

---

## Phase 4: User Story 2 - Concurrent request handling (Priority: P2)

**Goal**: Allow multiple chat requests to process concurrently without blocking.

**Independent Test**: Run two streaming requests in parallel and confirm both progress and complete.

### Tests for User Story 2 (REQUIRED) ⚠️

- [x] T011 [P] [US2] Add integration test for concurrent chat requests in `tests/integration/test_concurrent_chat.py`

### Implementation for User Story 2

- [x] T012 [US2] Add API worker CLI arg and wiring in `main.py`
- [x] T013 [US2] Update `run_api` to accept worker count in `scene_agent/interfaces/api.py`
- [x] T014 [US2] Pass worker settings through service runners in `scripts/start_services.sh` and `scripts/start_services.py`

**Checkpoint**: User Story 2 should be fully functional and testable independently

---

## Phase 5: User Story 3 - Clean shutdown on stop command (Priority: P3)

**Goal**: Terminate all spawned processes on a single stop action.

**Independent Test**: Start services and issue one stop command; verify API, MCP, and headless Blender processes exit within 3 seconds.

### Tests for User Story 3 (REQUIRED) ⚠️

- [x] T015 [P] [US3] Add unit test for session cleanup in `tests/unit/test_session_manager_cleanup.py`

### Implementation for User Story 3

- [x] T016 [US3] Add `shutdown_all()` to terminate headless/MCP processes in `scene_agent/blender/session_manager.py`
- [x] T017 [US3] Register FastAPI shutdown hook to call cleanup in `scene_agent/interfaces/api.py`
- [x] T018 [US3] Ensure shell runner kills full process tree in `scripts/start_services.sh`
- [x] T019 [US3] Ensure python runner kills process groups in `scripts/start_services.py`

**Checkpoint**: User Story 3 should be fully functional and testable independently

---

## Phase 6: User Story 4 - Markdown message rendering (Priority: P4)

**Goal**: Render Markdown in chat messages while treating HTML/scripts as inert text.

**Independent Test**: Send a message with headings, lists, emphasis, code blocks, and links; confirm correct rendering and no HTML execution.

### Tests for User Story 4 (REQUIRED) ⚠️

- [ ] T020 [P] [US4] Manual UI validation per `specs/004-fix-headless-blender/quickstart.md`

### Implementation for User Story 4

- [x] T021 [P] [US4] Add Markdown renderer deps in `web/package.json`
- [x] T022 [US4] Create Markdown renderer in `web/src/components/MarkdownMessage.tsx`
- [x] T023 [US4] Render Markdown for message content in `web/src/components/MessageList.tsx`
- [x] T024 [US4] Add Markdown styling in `web/src/index.css`

**Checkpoint**: User Story 4 should be fully functional and testable independently

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, regression coverage, and final validation

- [x] T025 [P] Update streaming docs in `docs/streaming-response.md`
- [x] T026 [P] Update runtime configuration docs in `README.md`
- [x] T027 [P] Add concurrency/worker notes to `scripts/README.md`
- [ ] T028 [P] Run quickstart validation steps in `specs/004-fix-headless-blender/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational phase completion
  - Stories can proceed in parallel or in priority order (P1 → P2 → P3 → P4)
- **Polish (Phase 7)**: Depends on all desired user stories being complete

### User Story Dependencies

- **US1 (P1)**: No dependencies after Foundational
- **US2 (P2)**: No dependencies after Foundational
- **US3 (P3)**: No dependencies after Foundational
- **US4 (P4)**: No dependencies after Foundational

### Within Each User Story

- Tests (if included) MUST be written and FAIL before implementation
- Shared utilities before story-specific logic
- Core backend changes before UI updates
- Story complete before moving to next priority if working sequentially

### Parallel Opportunities

- All Setup tasks marked [P] can run in parallel
- Foundational tasks marked [P] can run in parallel
- After Foundational, US1–US4 can run in parallel
- Tests within a story marked [P] can run in parallel

---

## Parallel Example: User Story 1

```bash
Task: "Add integration test for terminal event done in tests/integration/test_chat_stream.py"
Task: "Add integration test for error event on failure in tests/integration/test_chat_stream.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup  
2. Complete Phase 2: Foundational  
3. Complete Phase 3: User Story 1  
4. **STOP and VALIDATE**: Test User Story 1 independently  
5. Deploy/demo if ready

### Incremental Delivery

1. Setup + Foundational  
2. Add US1 → Test independently → Deploy/Demo  
3. Add US2 → Test independently → Deploy/Demo  
4. Add US3 → Test independently → Deploy/Demo  
5. Add US4 → Test independently → Deploy/Demo

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to a specific user story
- Each user story should be independently completable and testable
- Manual UI validation is acceptable where automated UI testing is impractical
