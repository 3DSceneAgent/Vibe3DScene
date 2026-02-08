# Tasks: Image Upload Support

**Input**: Design documents from `/specs/001-image-upload-support/`  
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/, quickstart.md  

**Tests**: Tests are REQUIRED for code changes unless the spec explicitly documents a waiver with rationale and alternative validation steps.  

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and shared configuration

- [x] T001 Update shared settings for upload limits, storage dir, and headless timeouts in `scene_agent/config.py`
- [x] T002 [P] Add diagnostics helper utilities in `scene_agent/utils/diagnostics.py` and export in `scene_agent/utils/__init__.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T003 [P] Add reference image metadata + storage manager in `scene_agent/memory/reference_image_memory.py` and export in `scene_agent/memory/__init__.py`
- [x] T004 [P] Update agent state for reference images + diagnostics in `scene_agent/agent/state.py`

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Upload reference images for verification (Priority: P1) 🎯 MVP

**Goal**: Users can upload up to three reference images at any time and the agent uses them for verification.

**Independent Test**: Upload 1–3 images, confirm they are stored, and verify a render uses the selected reference image.

### Tests for User Story 1 (REQUIRED unless waived) ⚠️

- [x] T005 [P] [US1] Contract test for reference image upload/list in `tests/contract/test_reference_images.py`
- [x] T006 [P] [US1] Unit test for reference image selection/limits in `tests/unit/test_reference_image_memory.py`
- [x] T007 [P] [US1] Integration test for upload + list flow in `tests/integration/test_reference_images.py`

### Implementation for User Story 1

- [x] T008 [US1] Implement upload/list endpoints in `scene_agent/interfaces/api.py` for `/threads/{thread_id}/reference-images`
- [x] T009 [US1] Add response/request models for reference images in `scene_agent/interfaces/api.py`
- [x] T010 [US1] Add VLM verification helper for render + reference images (and text-only fallback) in `scene_agent/vlm/verification.py` and export in `scene_agent/vlm/__init__.py`
- [x] T011 [US1] Wire verification helper into agent flow in `scene_agent/agent/nodes.py`
- [x] T012 [US1] Update prompt guidance for reference image verification in `scene_agent/agent/prompts.py`
- [x] T013 [P] [US1] Add API types + client methods in `web/src/api/types.ts` and `web/src/api/client.ts`
- [x] T014 [US1] Extend thread state for reference images in `web/src/state/types.ts` and `web/src/state/storage.ts`
- [x] T015 [US1] Add image upload UI (drop/click placeholder above composer, right-aligned) with previews in `web/src/components/ChatComposer.tsx` and styles in `web/src/App.css`
- [x] T016 [US1] Wire upload flow + error handling in `web/src/App.tsx` and `web/src/components/ChatTab.tsx`
- [x] T017 [US1] Add reference image display component in `web/src/components/ReferenceImageStrip.tsx` and render in `web/src/components/ChatTab.tsx`

**Checkpoint**: User Story 1 fully functional and independently testable

---

## Phase 4: User Story 2 - Headless requests return reliably (Priority: P2)

**Goal**: Headless scene/render requests return or error within 30 seconds with diagnostics and logs.

**Independent Test**: Issue `/scene/{thread_id}` and `/scene/{thread_id}/renders` in headless mode and verify responses include actionable diagnostics and no hangs.

### Tests for User Story 2 (REQUIRED unless waived) ⚠️

- [x] T018 [P] [US2] Integration test for headless timeout + diagnostics in `tests/integration/test_headless_diagnostics.py`
- [x] T019 [P] [US2] Unit test for log redirection + PID tracking in `tests/unit/test_session_manager_logging.py`

### Implementation for User Story 2

- [x] T020 [US2] Implement diagnostic record builder in `scene_agent/utils/diagnostics.py`
- [x] T021 [US2] Redirect headless stdout/stderr to per-session logs in `scene_agent/blender/session_manager.py`
- [x] T022 [US2] Add request timeout + diagnostics to `/scene` and `/renders` handlers in `scene_agent/interfaces/api.py`
- [x] T023 [US2] Log request IDs, PIDs, and log paths via `scene_agent/utils/logging.py` in `scene_agent/interfaces/api.py`
- [x] T024 [US2] Include diagnostic references in headless error responses in `scene_agent/interfaces/api.py`
- [x] T025 [US2] Record elapsed timing metrics and compare against targets in `scene_agent/utils/diagnostics.py` and `scene_agent/interfaces/api.py`

**Checkpoint**: User Stories 1 and 2 work independently with headless reliability validated

---

## Phase 5: User Story 3 - Stop services predictably (Priority: P3)

**Goal**: A single Ctrl+C stops all services and exits promptly.

**Independent Test**: Start services with `./scripts/start_services.sh`, press Ctrl+C once, verify all processes exit.

### Tests for User Story 3 (REQUIRED unless waived) ⚠️

- [x] T026 [P] [US3] Add manual shutdown validation checklist in `specs/001-image-upload-support/quickstart.md`

### Implementation for User Story 3

- [x] T027 [US3] Fix cleanup trap and child process termination in `scripts/start_services.sh`
- [x] T028 [US3] Align Python runner shutdown behavior in `scripts/start_services.py`
- [x] T029 [US3] Log stop status + exit codes in `scripts/start_services.sh` and `scripts/start_services.py`

**Checkpoint**: All user stories functional and independently testable

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [x] T030 [P] Update API docs for reference images in `README.md`
- [x] T031 [P] Add UI copy/help text for upload limits in `web/src/components/ChatComposer.tsx`
- [ ] T032 [P] Run quickstart validation steps in `specs/001-image-upload-support/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational phase completion  
  - Can proceed in parallel (if staffed) or sequentially (P1 → P2 → P3)
- **Polish (Final Phase)**: Depends on desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: No dependencies on other stories after Foundational
- **User Story 2 (P2)**: No dependencies on other stories after Foundational
- **User Story 3 (P3)**: No dependencies on other stories after Foundational

### Within Each User Story

- Tests (if included) should be written and FAIL before implementation
- Data models/helpers before API endpoints or UI integration
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- T002 can run in parallel with other Setup tasks
- T003 and T004 can run in parallel within Foundational
- T005–T007 can run in parallel within US1
- T013–T015 can run in parallel within US1 (different files)
- T018–T019 can run in parallel within US2
- T030–T032 can run in parallel in Polish phase

---

## Parallel Example: User Story 1

```bash
# Tests can run in parallel
Task: "Contract test for reference image upload/list in tests/contract/test_reference_images.py"
Task: "Unit test for reference image selection/limits in tests/unit/test_reference_image_memory.py"
Task: "Integration test for upload + list flow in tests/integration/test_reference_images.py"

# Frontend + backend can proceed in parallel after API contract is stable
Task: "Add API types + client methods in web/src/api/types.ts and web/src/api/client.ts"
Task: "Add image upload UI in web/src/components/ChatComposer.tsx"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: Run US1 tests and manual UI verification

### Incremental Delivery

1. Setup + Foundational
2. User Story 1 → validate
3. User Story 2 → validate headless diagnostics
4. User Story 3 → validate shutdown behavior
5. Polish tasks

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story is independently completable and testable
