# Feature Specification: Scene Auto Fetch & Streaming UI

**Feature Branch**: `001-scene-auto-fetch`  
**Created**: 2026-02-03  
**Status**: Draft  
**Input**: User description: "我需要你帮我实现几个需求：1. 实现前端定期从后端 fetch scene renders & fetch 3d viewport 的功能；一方面，在前端请你添加一个 boolean flag 控制是否自动 fetch。另一方面，后端当agent进行了 @scene_agent/tools/blender_tools.py  相关的工具调用的时候，返回的消息体中包含一个标识场景是否发生变化的标记 `scene_has_change=True` 否则置 False。仅当该标记为 True，且agent消息回复完毕，且前端开启自动 fetch 的时候，你自动地进行 fetch scene renders & gltf 2. 我需要你分析清楚后端在流式请求时的返回结构和返回逻辑，请查阅 langgraph 相关文档弄清楚CoT、工具调用、图片在返回的消息体中的呈现形式，这块给我整理一个简洁的文档放到 @docs 里进行说明。这块你需要非常仔细地 survey 整理，这是后面（3）实现的基础 3. 基于 (2)，我需要你优化前端 agent 智能体回复时的样式和逻辑。需要指出agent的回复里可能是工具调用结果、正常文本、思维链混合的结果 如 @scene_agent/agent/graph.py 中看到的，工具调用结点和vlm输出结点可能交替。 显示样式需要满足以下几点。（a) 首先，整体是流式的（现在已经实现了）。(b) 其次，要支持显示工具调用结果，支持显示图片（例如 scene agent 后台调用 blender mcp 工具对场景进行了渲染）。注意图片一般式嵌入在工具调用中的。(c) 另外，我需要你支持用户对工具调用结果进行折叠。 4. 请你把 @scripts/blender_headless_client.py 设置到 `BLENDER_HEADLESS_CMD` 与`BLENDER_HEADLESS_ARGS` 中。 5. 小的UI优化：前端支持折叠 Scene Tab(场景页签）。"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Auto-refresh scene assets (Priority: P1)

As a user, I can enable automatic scene refresh so that new renders and the 3D viewport update only when the agent actually changes the scene and finishes its reply.

**Why this priority**: This delivers the core workflow value: keeping the scene view synchronized without manual refreshes or unnecessary network usage.

**Independent Test**: Can be fully tested by toggling auto-refresh on/off and sending a request that does or does not change the scene, verifying fetches occur only in the correct cases.

**Acceptance Scenarios**:

1. **Given** auto-refresh is on and the agent reply indicates a scene change, **When** the reply completes, **Then** the scene renders and 3D viewport refresh automatically.
2. **Given** auto-refresh is on and the agent reply indicates no scene change, **When** the reply completes, **Then** no scene refresh occurs.
3. **Given** auto-refresh is off, **When** the reply completes regardless of scene change, **Then** no scene refresh occurs.

---

### User Story 2 - Readable streaming agent responses (Priority: P2)

As a user, I can follow streaming agent replies that interleave normal text, action results, and images, and I can collapse action details when I want a higher-level view.

**Why this priority**: Streaming content is central to the chat experience, and action detail visibility must be useful without overwhelming the user.

**Independent Test**: Can be tested by streaming a mixed response and confirming text, action results, and images appear with collapsible action sections.

**Acceptance Scenarios**:

1. **Given** a streaming reply that includes action results and images, **When** the stream updates, **Then** each content type is displayed in the correct order.
2. **Given** an action result block is visible, **When** the user collapses it, **Then** the action content is hidden without losing it on expand.

---

### User Story 3 - Understand streaming response structure (Priority: P3)

As a developer, I can consult concise documentation that explains the streaming response structure, including how reasoning, action calls, and images appear, so I can maintain or extend the UI with confidence.

**Why this priority**: Clear documentation reduces integration errors and supports future UI/agent changes.

**Independent Test**: Can be tested by checking that the documentation exists in the docs folder and covers each message type with examples or descriptions.

**Acceptance Scenarios**:

1. **Given** the documentation page, **When** a reviewer reads it, **Then** it describes streaming message parts (text, action results, reasoning, images) and their ordering.

---

### User Story 4 - Collapse the Scene tab (Priority: P3)

As a user, I can collapse and expand the Scene tab to focus on the chat when needed.

**Why this priority**: This improves usability for multi-pane layouts without changing core functionality.

**Independent Test**: Can be tested by toggling the Scene tab visibility in the UI.

**Acceptance Scenarios**:

1. **Given** the Scene tab is visible, **When** the user collapses it, **Then** the Scene panel hides and the main chat layout remains usable.
2. **Given** the Scene tab is collapsed, **When** the user expands it, **Then** the Scene panel returns to its previous state.

---

### Edge Cases

- Auto-refresh is toggled off while a streaming reply is in progress.
- A response omits the scene-change indicator or provides an unexpected value.
- Action output arrives without images, or images fail to load.
- A reply completes without any tool calls but still provides mixed content.
- Scene tab is collapsed while a refresh is pending.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a user-visible toggle to enable or disable automatic scene refresh.
- **FR-002**: System MUST include a scene-change indicator in agent responses that use scene-related tools and default it to false otherwise.
- **FR-003**: System MUST trigger scene renders and 3D viewport refresh only when auto-refresh is enabled, the response indicates a scene change, and the agent reply has completed.
- **FR-004**: System MUST display streaming responses that can interleave normal text, action results, and images in the correct order.
- **FR-005**: Users MUST be able to collapse and expand action result sections without losing the content.
- **FR-006**: System MUST provide concise project documentation describing the streaming response structure, including reasoning, action calls, and images.
- **FR-007**: System MUST allow operators to configure the headless renderer invocation (command and arguments) via environment settings without code changes.
- **FR-008**: Users MUST be able to collapse and expand the Scene tab without losing the scene state.

### Non-Functional Requirements *(mandatory)*

- **NFR-001**: Streaming UI updates MUST remain responsive during mixed-content replies, with user actions (scrolling, collapsing) responding within 200 ms in at least 95% of attempts.
- **NFR-002**: Automatic refresh MUST avoid unnecessary requests by only triggering on confirmed scene changes.
- **NFR-003**: Errors in tool output or image loading MUST be visible and actionable to the user.
- **NFR-004**: Testing MUST cover streaming ordering, auto-refresh gating, and UI collapse behavior.

### Key Entities *(include if feature involves data)*

- **Agent Response Segment**: A unit of streamed content, such as text, reasoning, action output, or image.
- **Scene Change Indicator**: A boolean flag representing whether the scene changed in a response.
- **Auto-Refresh Preference**: The user setting that enables or disables automatic refresh.
- **Action Result Block**: A collapsible unit that groups action output, including embedded media.
- **Scene Tab State**: The expanded or collapsed state of the Scene panel.

## Dependencies

- Streaming responses provide a reliable completion signal for each agent reply.
- Scene renders and viewport data can be fetched when requested.
- Agent responses can include a scene-change indicator when actions modify the scene.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: When auto-refresh is enabled, updated scene renders or viewport appear within 5 seconds of response completion in at least 95% of scene-changing replies.
- **SC-002**: Users can toggle auto-refresh and Scene tab visibility in two actions or fewer.
- **SC-003**: At least 90% of action result blocks can be collapsed and re-expanded without content loss during a test session.
- **SC-004**: Documentation review confirms sections exist for text, reasoning, action output, and images in streaming responses.

## Assumptions

- Auto-refresh is off by default unless previously enabled by the user.
- Scene refresh can be initiated immediately after the agent reply completes.
