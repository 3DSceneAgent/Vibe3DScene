# Feature Specification: UI Streaming and Headless Blender

**Feature Branch**: `001-ui-streaming-blender`  
**Created**: 2026-02-01  
**Status**: Draft  
**Input**: User description: "我有几个需求
(1) 优化前端页面 @web 。(i) 优化 ui, 现在 topbar 上有三个 tab (chat/scene/settings)，这有点多余。请你在一个大的 viewport里，左侧显示 scene tab的内容，右侧显示聊天接口，移除掉 topbar。(ii) 在等待 Agent 消息回复的时候，请你在对话框消息体中使用简单的动态效果（例如经典的等待中 loading spinner)
(2) 现在前端的 Agent消息回复并不是流式的（而是一次性，请你结合前后端代码，分析其原因并修复。必要的时候请你在网上检索最新的 langgraph, langchain文档，查看流式消息是如何接收和通信的
(3) 现在后端 scene agent 默认与本地的 blender client进行通信，这其实假定了我已经在本地开启了 blender 客户端并启用了插件。请你支持另一种运行模式：纯后台Blender。这种模式平时并不维护任何 blender 连接，cli/api 请求传入某个新的 session id 的时候，如果id不存在，你就在后台创建与之对应的 blender 场景并启用插件，后续api在这个session id 上就与这个blender 场景进行通信，注意这种模式是按需取用+纯后台Blender的。注意请你架构如何同时支持现有的本地client和这种纯后台模式。"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Unified Workspace Layout (Priority: P1)

As a user, I want to see the scene controls and chat in a single, large workspace so I can work without switching tabs.

**Why this priority**: It directly improves the primary workflow and removes unnecessary navigation.

**Independent Test**: Can be fully tested by opening the app and verifying the layout without using any backend features.

**Acceptance Scenarios**:

1. **Given** the user opens the web app, **When** the main workspace loads, **Then** the topbar tabs are absent and the viewport shows the scene panel on the left and chat on the right.
2. **Given** the user resizes the browser window, **When** the layout adjusts, **Then** both the scene panel and chat remain visible and usable without overlapping.

---

### User Story 2 - Streaming Agent Replies with Waiting Indicator (Priority: P2)

As a user, I want to see agent replies arrive progressively with a clear waiting indicator so I understand the system is responding.

**Why this priority**: It improves clarity and responsiveness during conversations.

**Independent Test**: Can be tested by sending a message and observing the reply behavior and loading indicator.

**Acceptance Scenarios**:

1. **Given** the user sends a message, **When** the agent starts responding, **Then** the chat shows a message bubble with a visible waiting indicator in the body.
2. **Given** an agent response is in progress, **When** partial content arrives, **Then** the message bubble updates incrementally until completion and the waiting indicator is removed.

---

### User Story 3 - Headless Background Blender Sessions (Priority: P3)

As a system operator or API caller, I want a headless background mode that creates Blender sessions on demand by session ID so I do not need a local client running.

**Why this priority**: It enables unattended operation and removes the need for a pre-connected local client.

**Independent Test**: Can be tested by sending requests with a new session ID and verifying a new session is created and reused.

**Acceptance Scenarios**:

1. **Given** the system is running in headless background mode, **When** a request arrives with a new session ID, **Then** a new Blender scene is created and subsequent requests with that ID are routed to the same scene.
2. **Given** the system is running in local-client mode without a connected client, **When** a request is made, **Then** the caller receives a clear error indicating that a local client connection is required.

---

### Edge Cases

- What happens when an agent response stalls or fails mid-stream?
- How does the system handle multiple concurrent requests for the same new session ID?
- What happens when a headless session cannot be created due to missing resources?
- How does the system behave when local-client mode is enabled but no client is connected?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The UI MUST present a single large workspace with a left-side scene panel and a right-side chat panel, and the topbar tabs MUST be removed.
- **FR-002**: The chat UI MUST display an in-progress agent message with a visible waiting indicator in the message body until the response completes.
- **FR-003**: Agent responses MUST be delivered and rendered incrementally to users as content becomes available.
- **FR-004**: The system MUST support a headless background mode that creates a new session-scoped Blender scene when a request arrives with an unknown session ID.
- **FR-005**: The system MUST continue to support the existing local-client mode without changing its expected behavior.
- **FR-006**: Requests MUST be routed to the correct session based on session ID, and callers MUST receive a clear error if local-client mode is active without a connected client.
- **FR-007**: Users MUST be able to use scene controls and chat without switching tabs or opening additional pages.

### Non-Functional Requirements *(mandatory)*

- **NFR-001**: The workspace layout MUST remain usable on common desktop screen sizes with both panels visible at the same time.
- **NFR-002**: During streaming responses, users MUST see new content within 1 second of it becoming available under normal operating conditions.
- **NFR-003**: Errors in messaging or scene connectivity MUST be visible to users and provide actionable guidance.
- **NFR-004**: The system MUST prevent cross-session data leakage between different session IDs.

### Key Entities *(include if feature involves data)*

- **Session**: A unique conversation or scene context keyed by session ID, used to route requests.
- **Blender Scene**: The session-scoped 3D environment associated with a session ID.
- **Agent Message**: The agent's response content, including partial and final states.

### Assumptions

- The primary usage is desktop web; no mobile-specific layout changes are required.
- Session lifetime is limited to the runtime of the service and does not need to persist across restarts.
- Authentication and authorization requirements are unchanged for this feature.

### Dependencies

- Headless mode requires access to a Blender runtime suitable for background operation.
- Local-client mode depends on a locally running client when that mode is enabled.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In usability testing, at least 90% of users complete a combined scene-and-chat task without switching views or tabs.
- **SC-002**: Users see the first partial agent response within 1 second of the response starting, and a waiting indicator remains visible until completion.
- **SC-003**: In headless mode, a new session becomes ready for subsequent requests within 10 seconds of the first request for that session.
- **SC-004**: Local-client mode regression testing shows zero critical failures across existing workflows.
