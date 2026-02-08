# Feature Specification: Image Upload Support

**Feature Branch**: `001-image-upload-support`  
**Created**: 2026-02-05  
**Status**: Draft  
**Input**: User description: "现在 BLENDER_MODE=headless时，前端获取场景或者获取renders都会卡死，请你分析原因。你可以在适当的地方增加日志的打印输出，或者把每个blender进程的pid、日志输出都进行重定向。 (2) ./scripts/start_services.sh 开启服务后还是没法关停，ctrl+c连按以后也无法关闭，只显示Stopping services (3) 请你在前后端加入对用户上传图片的支持。用户可能在对话的任意节点上传K张图片（不超过3张）。上传图片后你需要把图片保存到 agent 的 short term memory 里作为后面 verify and compare 的参照（即验证当前场景的渲染图是否和参考图一致，如果没有参考图的话就是目前的逻辑，只能确认是否和输入文本的请求一致）。假如用户在对话中多次上传了参考图，memory中就会存在多张，agent需要智能地判断以哪张为参考图。 注意，（3）的优先级很高，重点实现。"

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.
  
  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.
  Think of each story as a standalone slice of functionality that can be:
  - Developed independently
  - Tested independently
  - Deployed independently
  - Demonstrated to users independently
-->

### User Story 1 - Upload reference images for verification (Priority: P1)

As a user, I can upload up to three reference images at any point in a conversation so the system can verify renders against what I expect.

**Why this priority**: Image uploads directly affect verification accuracy and are the highest-priority new capability.

**Independent Test**: Can be fully tested by uploading images during a conversation and confirming subsequent verification uses the references.

**Acceptance Scenarios**:

1. **Given** an active conversation, **When** I upload 1–3 images, **Then** the system confirms the uploads and stores them for later verification.
2. **Given** stored reference images, **When** a render is verified, **Then** the system compares the render against the reference images and reports the verification outcome.

---

### User Story 2 - Headless requests return reliably (Priority: P2)

As a user, I can request the scene or renders in headless mode and receive a result or a clear, actionable error without the request hanging.

**Why this priority**: Reliability in headless mode is required for unattended operation and debugging.

**Independent Test**: Can be fully tested by issuing scene/render requests in headless mode and confirming responses complete or fail with actionable information.

**Acceptance Scenarios**:

1. **Given** headless mode is enabled, **When** I request the scene or renders, **Then** the system returns a result or an actionable error within a defined time limit.
2. **Given** a headless request fails, **When** I review diagnostics, **Then** I can identify the relevant process and logs for that request.

---

### User Story 3 - Stop services predictably (Priority: P3)

As a user, I can stop the running services with a single interrupt and see a clear stop result.

**Why this priority**: Being unable to stop services blocks development and operations, but is less critical than verification accuracy.

**Independent Test**: Can be fully tested by starting services and stopping them with one interrupt while verifying all processes exit.

**Acceptance Scenarios**:

1. **Given** services are running, **When** I send a single interrupt, **Then** all services stop and the command exits promptly with a clear status.

---

[Add more user stories as needed, each with an assigned priority]

### Edge Cases

- User uploads more than three images in one conversation.
- User uploads a non-image file or a corrupted image.
- User uploads images while a render request is already in progress.
- Multiple reference images conflict or are equally relevant to a verification request.
- Headless render process becomes unresponsive during a request.
- Stop command is issued while some services are already stopped or stalled.

## Requirements *(mandatory)*

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right functional requirements.
-->

### Functional Requirements

- **FR-001**: Users MUST be able to upload 1–3 reference images at any point in a conversation.
- **FR-002**: System MUST validate uploads are images and reject invalid files or uploads exceeding the limit with a clear message.
- **FR-003**: UI MUST provide a clear add-image entry point near the message composer with click/drag-drop support and image previews.
- **FR-004**: System MUST store uploaded images in short-term memory tied to the conversation for later verification.
- **FR-005**: When reference images exist, system MUST send the render plus all reference images to the vision-language model for verification and record which reference(s) were considered.
- **FR-006**: When no reference images exist, system MUST use the same verification flow with the vision-language model based on the user request text.
- **FR-007**: Headless scene/render requests MUST complete successfully or return an actionable error within 30 seconds, with no indefinite hangs.
- **FR-008**: System MUST capture diagnostic information for each headless request, including process identifiers and request-specific logs.
- **FR-009**: Service stop actions MUST terminate all started services on a single interrupt and report final stop status.

### Non-Functional Requirements *(mandatory)*

- **NFR-001**: Upload and verification messaging MUST be clear, consistent, and user-friendly.
- **NFR-002**: Response-time targets for headless requests MUST be defined and measured.
- **NFR-003**: Errors MUST be user-visible, actionable, and logged with sufficient context for troubleshooting.
- **NFR-004**: Testing MUST cover image upload flows, headless request reliability, and service stop behavior.

### Key Entities *(include if feature involves data)*

- **Reference Image**: User-provided image used for verification; attributes include upload time and conversation association.
- **Conversation Session**: The active chat context where images, requests, and verification results are associated.
- **Verification Result**: Outcome of comparing a render against a reference; includes pass/fail and the reference used.
- **Diagnostic Record**: Troubleshooting data tied to a headless request; includes process identifier and log reference.

## Success Criteria *(mandatory)*

<!--
  ACTION REQUIRED: Define measurable success criteria.
  These must be technology-agnostic and measurable.
-->

### Measurable Outcomes

- **SC-001**: 95% of valid image uploads (up to 3) are acknowledged within 2 seconds.
- **SC-002**: 99% of headless scene/render requests return a result or actionable error within 30 seconds.
- **SC-003**: In verification tests with reference images, 90% of runs correctly classify match vs. mismatch using the vision-language model.
- **SC-004**: 95% of service stop actions complete within 5 seconds and terminate all managed services.

## Assumptions

- Uploaded images are retained only for the duration of the active conversation (short-term memory).
- The vision-language model supports image inputs and can compare a render against multiple references.

## Dependencies

- None identified at this time.
