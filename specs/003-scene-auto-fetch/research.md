# Phase 0 Research: Scene Auto Fetch & Streaming UI

## Decision 1: Treat SSE stream as the canonical response timeline

**Decision**: Use the existing `/chat/stream` SSE flow as the single source of truth for message progression, emitting completion only when the `event: done` payload arrives, and layering scene-change flags into the same stream payloads.

**Rationale**: The backend already streams `delta` and `messages` payloads via SSE. Aligning auto-refresh triggers to the same stream ensures refresh happens only after the assistant reply completes and prevents race conditions with partial updates.

**Alternatives considered**:
- Poll `/chat` after streaming ends. Rejected because it duplicates API traffic and can diverge from the SSE stream content.
- Trigger refresh on any tool result immediately. Rejected because it may fetch mid-response and show inconsistent scene state.

## Decision 2: Surface action/tool output as distinct stream segments

**Decision**: Include tool/action messages in the streaming payload (rather than filtering them out) so the frontend can render them as collapsible blocks, preserving order alongside assistant text.

**Rationale**: The current backend stream filters out tool messages and only emits assistant text. To show action results and embedded images, the UI needs access to the tool payloads as discrete items.

**Alternatives considered**:
- Fetch tool output from a separate endpoint. Rejected due to missing identifiers and extra coordination.
- Embed tool output into assistant text. Rejected because it loses structure and makes collapsible UI unreliable.

## Decision 3: Detect scene changes by tracking Blender tool calls

**Decision**: Mark `scene_has_change=true` for any response cycle that includes a Blender MCP tool call; default to `false` otherwise.

**Rationale**: Blender MCP tools are the authoritative source of scene mutations. Associating the change flag with tool activity provides a predictable and low-cost signal for the frontend.

**Alternatives considered**:
- Diff scene state after every reply. Rejected due to extra render/scene fetch cost and potential latency.
- Assume all assistant replies change the scene. Rejected because it would cause unnecessary renders and GLTF exports.

## Decision 4: Parse tool payloads for media references in the UI

**Decision**: Standardize frontend parsing to treat action/tool content as structured JSON that may include embedded image data or URLs, rendering any detected media inline within the collapsible action block.

**Rationale**: Tool outputs from Blender MCP can include render output or other assets. UI-level detection enables a consistent experience without backend hardcoding for each tool type.

**Alternatives considered**:
- Require each tool to emit a dedicated image event type. Rejected to avoid tool API churn and keep the contract flexible.
