# Data Model: Headless Runtime Reliability

## Entities

### ChatRequest

- **Fields**:
  - `thread_id` (string, required): Stable identifier for the conversation session.
  - `message` (string, required): User input to the agent.
  - `created_at` (timestamp, required): Request creation time.
- **Validation**:
  - `thread_id` must be non-empty.
  - `message` must be non-empty and within reasonable size limits for the UI.

### StreamEvent

- **Fields**:
  - `event` (string, optional): Terminal event marker (e.g., `done`).
  - `delta` (string, optional): Incremental assistant text.
  - `message_id` (string, optional): Identifier for the assistant message stream.
  - `messages` (array, optional): Serialized message objects (assistant/tool/system).
  - `todos` (array, optional): Todo items with id/description/status.
  - `scene_has_change` (boolean, optional): Indicates scene updates during the stream.
  - `error` (string, optional): Error text when streaming fails.
- **Validation**:
  - `event = done` must include `scene_has_change` when available.
  - `error` must be user-displayable text.

### LiveResponse

- **Fields**:
  - `request_id` (string, required): Links to the initiating chat request.
  - `status` (enum: `streaming`, `final`, `error`): Client-visible stream state.
  - `content` (string, optional): Final assistant content.
  - `thinking` (string, optional): Extracted reasoning block for UI display.
- **Validation**:
  - `status = error` must include an error message surfaced to the user.

### ServiceProcess

- **Fields**:
  - `process_id` (integer, required): Operating system PID.
  - `role` (enum: `api`, `mcp`, `headless-blender`): Process purpose.
  - `started_at` (timestamp, required): Launch time.
  - `status` (enum: `running`, `stopped`, `failed`): Current lifecycle state.
  - `session_id` (string, optional): Session association for headless Blender.
  - `exit_code` (integer, optional): Exit code if the process stopped.
  - `last_signal` (string, optional): Last termination signal sent.
- **Validation**:
  - `process_id` must be positive.
  - `role` must be one of the supported values.

## Relationships

- A `ChatRequest` produces zero or more `StreamEvent` records.
- A `LiveResponse` aggregates `StreamEvent` data for a single assistant reply.
- A `ServiceProcess` may be linked to a `session_id` when spawned per headless session.
