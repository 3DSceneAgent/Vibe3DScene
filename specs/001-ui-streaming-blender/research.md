# Phase 0 Research: UI Streaming and Headless Blender

## Decision 1: Stream agent replies from LangGraph using message-level events

**Decision**: Update the backend streaming endpoint to emit incremental message updates by using LangGraph streaming modes that surface message chunks (instead of only full state snapshots).

**Rationale**: The current API streams `stream_mode="values"` events that often include complete `messages` objects after the model finishes, which prevents true incremental rendering. Switching to message/chunk events enables the frontend to append partial content as it arrives.

**Alternatives considered**:
- Keep `stream_mode="values"` and attempt to diff content on the frontend. Rejected because the server often only emits completed messages.
- Use WebSocket-only streaming for the web app. Rejected because SSE is already integrated and simpler for request/response streaming.

## Decision 2: Treat streaming responses as append-only deltas in the UI

**Decision**: Modify the chat UI to accumulate streaming content per assistant message ID, appending deltas in order, and show a loading spinner until a final event indicates completion.

**Rationale**: The current UI de-duplicates by hashing whole content strings, which collapses incremental updates into a single final render. Append-only handling preserves streaming UX and is compatible with partial message events.

**Alternatives considered**:
- Replace the assistant message on every event. Rejected because it flickers and may drop incremental chunks.
- Render tool/thinking blocks as separate messages. Rejected because it complicates the primary chat flow.

## Decision 3: Add a headless Blender session manager keyed by session ID

**Decision**: Introduce a headless mode that lazily provisions Blender scenes per session ID, maintaining an in-memory session registry and per-session connection handle.

**Rationale**: The system currently assumes a single local Blender client. On-demand headless sessions enable API usage without a pre-connected client while keeping session isolation and routing intact.

**Alternatives considered**:
- Reuse a single headless Blender instance for all sessions. Rejected because it risks state contamination between sessions.
- Require clients to pre-register sessions via a new API. Rejected to keep the workflow simple and backward compatible.

## Decision 4: Keep existing API surface; add mode-specific errors

**Decision**: Preserve the current API endpoints and introduce clear error responses when local-client mode is active without a connected Blender client.

**Rationale**: Minimizes integration churn and preserves existing frontend behavior while enabling headless mode via configuration.

**Alternatives considered**:
- Add a separate `/session` provisioning endpoint. Rejected to avoid breaking existing flows and to keep session creation implicit.
