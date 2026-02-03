---

description: "Task list for Scene UI Fixes implementation"
---

# Tasks: Scene UI Fixes

**Input**: Design documents from `/Users/fishwowater/projects/blender-mcp-vision/thirdparty/3DSceneAgent/specs/002-scene-ui-fixes/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md  
**Tests**: Not explicitly requested in the spec; rely on manual validation steps documented in `specs/002-scene-ui-fixes/quickstart.md`.

**Organization**: Tasks are grouped by user story to enable independent implementation and validation of each story.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Shared UI scaffolding used by multiple stories.

- [x] T001 [P] Create reusable top bar component shell in `web/src/components/TopBar.tsx`
- [x] T002 [P] Add base top bar + layout utility styles in `web/src/App.css`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish layout scaffolding before story-specific changes.

- [x] T003 Update main layout to insert the top bar region and workspace sections in `web/src/App.tsx`

**Checkpoint**: Layout scaffolding is in place; user story implementation can begin.

---

## Phase 3: User Story 1 - Stable Scene + Chat Layout (Priority: P1) 🎯 MVP

**Goal**: Ensure scene lists are independently scrollable with fixed heights and chat scrolling does not move the scene panel.

**Independent Test**: Load long scene + render lists, scroll each list via its scrollbar, and confirm chat scroll does not move the scene panel.

### Implementation for User Story 1

- [x] T004 [P] [US1] Constrain Scene Objects list height + scrollbar styles in `web/src/components/SceneInfoPanel.tsx` and `web/src/App.css`
- [x] T005 [P] [US1] Constrain Camera Renders list height + scrollbar styles in `web/src/components/RenderGallery.tsx` and `web/src/App.css`
- [x] T006 [US1] Suppress mouse wheel scrolling inside list containers in `web/src/components/SceneInfoPanel.tsx` and `web/src/components/RenderGallery.tsx`
- [x] T007 [US1] Remove scene-wide scrolling so chat scroll does not move scene panel in `web/src/components/SceneTab.tsx` and `web/src/App.css`

**Checkpoint**: Scene lists scroll independently and chat scroll does not move the scene panel.

---

## Phase 4: User Story 2 - Consolidated Top Bar Actions (Priority: P1)

**Goal**: Move scene actions into a single top bar and remove todo UI from the workspace.

**Independent Test**: Verify the top bar shows all actions and no todo list/refresh-todo control is visible.

### Implementation for User Story 2

- [x] T008 [US2] Remove todo panel and refresh-todo wiring in `web/src/components/SceneTab.tsx` and `web/src/App.tsx`
- [x] T009 [US2] Move scene action buttons (refresh scene, fetch renders, load 3D scene) into `web/src/components/TopBar.tsx` and wire in `web/src/App.tsx`
- [x] T010 [US2] Relocate Settings button to the top bar and remove sidebar footer control in `web/src/App.tsx` and `web/src/App.css`

**Checkpoint**: Scene actions are grouped in the top bar and todos are no longer shown.

---

## Phase 5: User Story 3 - Download Scene as GLTF (Priority: P2)

**Goal**: Provide a Download GLTF action that saves the current scene to the user’s machine.

**Independent Test**: Click Download GLTF and confirm a `.glb` file is saved; confirm error/disabled state when no scene is loaded.

### Implementation for User Story 3

- [x] T011 [P] [US3] Add download helper to trigger browser saves in `web/src/utils/download.ts`
- [x] T012 [US3] Add Download GLTF button + loading state in `web/src/components/TopBar.tsx`
- [x] T013 [US3] Implement GLTF download flow using `getSceneGltf` + download helper in `web/src/App.tsx`
- [x] T014 [P] [US3] Add `Content-Disposition` filename header to GLB export response in `scene_agent/interfaces/api.py`

**Checkpoint**: Download action works end-to-end for an existing scene.

---

## Phase 6: User Story 4 - Collapsible Conversation History (Priority: P2)

**Goal**: Allow the left conversation history to collapse and expand to free space.

**Independent Test**: Toggle collapse/expand and confirm layout adjusts without overlap.

### Implementation for User Story 4

- [x] T015 [US4] Add sidebar collapsed state + toggle control in `web/src/App.tsx`
- [x] T016 [P] [US4] Add collapsed sidebar styles (width/visibility) in `web/src/App.css`
- [x] T017 [P] [US4] Adjust thread list layout for collapsed mode in `web/src/components/ThreadList.tsx`

**Checkpoint**: Conversation history collapses and expands smoothly.

---

## Phase 7: User Story 5 - Correct Message Rendering (Priority: P2)

**Goal**: Ensure assistant messages do not concatenate with previous messages during streaming.

**Independent Test**: Send sequential prompts and confirm each assistant message renders independently.

### Implementation for User Story 5

- [x] T018 [US5] Reset per-message streaming buffers and honor `message_id` updates in `web/src/App.tsx`
- [x] T019 [US5] Extend streaming helpers for buffer reset/message-id tracking in `web/src/utils/message.ts`

**Checkpoint**: Streaming no longer appends previous assistant content.

---

## Phase 8: User Story 6 - Headless Mode Testing Guidance (Priority: P3)

**Goal**: Provide clear headless Blender testing instructions and environment variable guidance.

**Independent Test**: Follow the README headless instructions to start a headless session.

### Implementation for User Story 6

- [x] T020 [US6] Add headless testing steps + env var checklist in `README.md`
- [x] T021 [US6] Add a short headless testing note or link in `web/README.md`

**Checkpoint**: Headless testing guidance is discoverable in repo docs.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Final documentation and validation updates.

- [ ] T022 [P] Update validation log after manual checks in `specs/002-scene-ui-fixes/quickstart.md`
- [x] T023 [P] Update contracts if GLTF response headers or fields changed in `specs/002-scene-ui-fixes/contracts/openapi.yaml`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational phase completion
- **Polish (Final Phase)**: Depends on all desired user stories being complete

### User Story Dependencies

- **US1 (P1)**: Can start after Foundational (Phase 2) - no dependency on other stories
- **US2 (P1)**: Can start after Foundational (Phase 2) - independent of US1
- **US3 (P2)**: Can start after Foundational (Phase 2) - independent of US1/US2
- **US4 (P2)**: Can start after Foundational (Phase 2) - independent of US1/US2
- **US5 (P2)**: Can start after Foundational (Phase 2) - independent of US1/US2
- **US6 (P3)**: Can start after Foundational (Phase 2) - independent of UI stories

### Parallel Opportunities

- Setup tasks marked [P] can run in parallel (T001, T002).
- US1 list constraints (T004, T005) can be parallelized across components.
- US3 backend header update (T014) can run in parallel with frontend download tasks.
- US4 styling and thread list updates (T016, T017) can be parallelized.

---

## Parallel Example: User Story 1

```bash
Task: "Constrain Scene Objects list height + scrollbar styles in web/src/components/SceneInfoPanel.tsx and web/src/App.css"
Task: "Constrain Camera Renders list height + scrollbar styles in web/src/components/RenderGallery.tsx and web/src/App.css"
```

## Parallel Example: User Story 3

```bash
Task: "Add download helper to trigger browser saves in web/src/utils/download.ts"
Task: "Add Content-Disposition filename header to GLB export response in scene_agent/interfaces/api.py"
```

## Parallel Example: User Story 4

```bash
Task: "Add collapsed sidebar styles (width/visibility) in web/src/App.css"
Task: "Adjust thread list layout for collapsed mode in web/src/components/ThreadList.tsx"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational
3. Complete Phase 3: User Story 1
4. Validate scroll behavior and chat/scene isolation

### Incremental Delivery

1. Deliver US1 (stable layout + isolated scrolling)
2. Deliver US2 (top bar actions + remove todos)
3. Deliver US3 (GLTF download)
4. Deliver US4 (collapsible history)
5. Deliver US5 (message rendering fix)
6. Deliver US6 (headless testing guidance)
