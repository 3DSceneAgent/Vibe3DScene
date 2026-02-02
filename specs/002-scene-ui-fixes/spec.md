# Feature Specification: Scene UI Fixes

**Feature Branch**: `002-scene-ui-fixes`  
**Created**: 2026-02-02  
**Status**: Draft  
**Input**: User description: "(1) ui 中不要显示 todo 和刷新 todo。Scene Objects 列表如果包含太多元素的话，支持通过滚动条查看（限制其高度）。Camera Renders 组件同样应当限制高度（如果有太多相机渲染图的话，通过滚动条向下查看）。注意这里也有两个滚动条，不要监听鼠标滚轮（仅当鼠标点击到对应sroll时生效)
(2) 左侧对话历史支持向左侧折叠
(3) 向下滚动聊天页面的时候场景页签 (Scene Tab) 不要向下滚动，仅向下滚动聊天页签(Chat Tab)
(4) 我建议你把 刷新场景、获取渲染图、加载3D场景、Settings设置按钮合并成一个 TopBar，Scene Tab 和 Chat Tab 分别在TopBar下面并排放置
(5) 在前端加入下载场景 GLTF的按钮功能，相应地实现后端接口（如需增加的话）
(6) 现在消息显示有点问题，似乎后续 agent的消息总会在前一条的基础上显示，例如 前一条是 "abcd", 后一条本应显示为 "xyz"，却显示成了 "abcdxyz"
(7) 最后请你告诉我应当如何测试现在 headless blender mode，需要怎么配置环境变量等等?"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Stable Scene + Chat Layout (Priority: P1)

As a user, I can work in the chat while keeping the scene panel stable, and I can access long scene object lists and camera renders via their own scrollbars without affecting other areas.

**Why this priority**: This is the core workspace layout; instability or unexpected scrolling makes the tool hard to use.

**Independent Test**: Can be fully tested by loading long scene lists and scrolling the chat panel while verifying the scene area and list behavior.

**Acceptance Scenarios**:

1. **Given** a long Scene Objects list, **When** I use the list’s scrollbar, **Then** I can view items beyond the visible area without resizing the page.
2. **Given** a long Camera Renders list, **When** I use the list’s scrollbar, **Then** I can view additional renders without the rest of the page scrolling.
3. **Given** the chat panel has overflow content, **When** I scroll the chat content, **Then** the Scene tab content remains fixed and does not move.
4. **Given** the Scene Objects and Camera Renders lists are focused, **When** I use the mouse wheel, **Then** those lists do not scroll unless I explicitly interact with their scrollbars.

---

### User Story 2 - Consolidated Top Bar Actions (Priority: P1)

As a user, I see a single top bar that contains the scene refresh, render fetch, 3D scene load, and settings controls, with the Scene and Chat tabs below it.

**Why this priority**: Consolidating actions reduces clutter and makes key controls discoverable.

**Independent Test**: Can be fully tested by verifying the location and visibility of the four actions and tab placement.

**Acceptance Scenarios**:

1. **Given** the main workspace, **When** I look for scene actions, **Then** I find all four actions grouped in a single top bar.
2. **Given** the top bar, **When** I look below it, **Then** the Scene tab and Chat tab are displayed side-by-side.
3. **Given** the workspace, **When** I look for todo-related UI, **Then** no todo list or refresh-todo control is visible.

---

### User Story 3 - Download Scene as GLTF (Priority: P2)

As a user, I can download the current scene as a GLTF file from the UI.

**Why this priority**: Exporting the scene enables downstream use and sharing.

**Independent Test**: Can be fully tested by triggering download and verifying the file appears and is valid.

**Acceptance Scenarios**:

1. **Given** a scene is loaded, **When** I click the download action, **Then** a GLTF file is downloaded to my device.
2. **Given** no scene is available, **When** I attempt a download, **Then** I receive a clear error or disabled action indicating export is unavailable.

---

### User Story 4 - Collapsible Conversation History (Priority: P2)

As a user, I can collapse the left conversation history to regain horizontal space and expand it again when needed.

**Why this priority**: The conversation list can crowd the workspace on smaller screens.

**Independent Test**: Can be tested by toggling collapse/expand and verifying layout changes.

**Acceptance Scenarios**:

1. **Given** the history panel is visible, **When** I activate collapse, **Then** the history panel is hidden and the workspace expands.
2. **Given** the history panel is collapsed, **When** I activate expand, **Then** the history panel returns in its previous width.

---

### User Story 5 - Correct Message Rendering (Priority: P2)

As a user, I see each agent message as a distinct entry without it being appended to the previous message.

**Why this priority**: Concatenated messages are confusing and reduce trust in the chat output.

**Independent Test**: Can be tested by sending multiple agent messages and verifying each displays separately.

**Acceptance Scenarios**:

1. **Given** an agent sends consecutive messages "abcd" then "xyz", **When** the chat renders them, **Then** the second message shows only "xyz".

---

### User Story 6 - Headless Mode Testing Guidance (Priority: P3)

As a developer or operator, I can follow documented steps to run headless Blender mode, including required environment settings.

**Why this priority**: Reliable testing requires clear, repeatable instructions.

**Independent Test**: Can be tested by following the documentation from a fresh setup.

**Acceptance Scenarios**:

1. **Given** a new environment, **When** I follow the headless mode guide, **Then** I can run a headless session without missing configuration details.

---

### Edge Cases

- Scene Objects list contains hundreds of items and should remain usable without resizing the page.
- Camera Renders list contains many images and should remain usable without resizing the page.
- Chat scroll is at the bottom when a new message arrives; Scene tab remains fixed.
- Download is attempted with no scene loaded; user receives a clear outcome.
- Conversation history is collapsed on a narrow viewport; no overlapping or hidden controls occur.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The UI MUST hide any todo list or refresh-todo control.
- **FR-002**: The Scene Objects list MUST enforce a maximum height and provide its own scrollbar when content overflows.
- **FR-003**: The Camera Renders list MUST enforce a maximum height and provide its own scrollbar when content overflows.
- **FR-004**: The Scene Objects and Camera Renders lists MUST NOT scroll in response to mouse wheel input; scrolling occurs only via explicit scrollbar interaction.
- **FR-005**: The left conversation history MUST be collapsible and expandable by the user.
- **FR-006**: Scrolling the chat content MUST NOT move or scroll the Scene tab content.
- **FR-007**: The top bar MUST group scene refresh, render fetch, 3D scene load, and settings controls in a single area.
- **FR-008**: The Scene tab and Chat tab MUST appear below the top bar and be displayed side-by-side.
- **FR-009**: Users MUST be able to download the current scene as a GLTF file from the UI.
- **FR-010**: A backend capability MUST exist to provide the GLTF download to the UI.
- **FR-011**: Each agent message MUST render as a distinct chat entry without concatenating with previous messages.
- **FR-012**: The product MUST include documentation that explains how to run headless Blender mode, including required environment settings.

### Non-Functional Requirements *(mandatory)*

- **NFR-001**: The UI MUST remain responsive when Scene Objects exceeds 200 items or Camera Renders exceeds 20 items.
- **NFR-002**: The layout MUST remain usable on common desktop viewports down to 1280x720 without obscuring controls.
- **NFR-003**: Error states for failed downloads MUST be user-visible and actionable.
- **NFR-004**: The headless mode guide MUST be complete enough for a new team member to run a headless session within 15 minutes.

### Key Entities *(include if feature involves data)*

- **Scene Export**: A downloadable representation of the current scene in GLTF format.
- **Chat Message**: A single agent or user message displayed as one entry in the chat history.
- **Layout State**: The current state of the top bar, tab placement, and whether the history panel is collapsed.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can scroll long Scene Objects and Camera Renders lists using their own scrollbars without moving other panels.
- **SC-002**: In a 20-message test conversation, 100% of agent messages appear as distinct entries with no concatenation.
- **SC-003**: Users can download a GLTF file from the UI in two actions or fewer when a scene is available.
- **SC-004**: A new team member can run headless Blender mode within 15 minutes by following the documentation without additional guidance.

## Assumptions

- Collapsing the conversation history is controlled via a visible toggle in the UI.
- The GLTF download is limited to the currently loaded scene and does not include optional assets beyond what is already in the scene.
- Documentation for headless mode is delivered as repository documentation or in-product help content.
- This feature does not change the underlying rendering or scene generation behavior beyond export availability.

## Dependencies

- Headless mode testing depends on having a valid Blender installation and required environment settings available in the runtime environment.
