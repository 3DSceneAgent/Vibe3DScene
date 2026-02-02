# Data Model: UI Streaming and Headless Blender

## Entities

### Session

- **Fields**: `id`, `created_at`, `mode` (local-client|headless), `status` (starting|ready|error|closed)
- **Relationships**: one-to-one with `BlenderScene`; one-to-many with `Message`
- **Validation**: `id` must be non-empty and unique; `mode` determined by configuration; `status` transitions are monotonic.

### BlenderScene

- **Fields**: `session_id`, `connection_ref`, `last_active_at`, `error`
- **Relationships**: belongs to `Session`
- **Validation**: `connection_ref` must exist when status is `ready`.

### Message

- **Fields**: `id`, `session_id`, `role` (user|assistant), `content`, `created_at`, `status` (streaming|final|error), `thinking` (optional)
- **Relationships**: belongs to `Session`
- **Validation**: assistant messages may be empty while `status=streaming`, must be non-empty when `status=final` unless an error occurred.

### StreamEvent

- **Fields**: `messages` (list), `todos` (list), `error` (optional)
- **Relationships**: used to update `Message` and `TodoItem` during streaming
- **Validation**: at least one of `messages`, `todos`, or `error` must be present.

### TodoItem

- **Fields**: `id`, `description`, `status`, `created_at`, `completed_at`
- **Relationships**: belongs to `Session`
- **Validation**: `status` is one of `pending|in_progress|completed|failed`.

### SceneInfo

- **Fields**: `thread_id`, `scene_objects`, `persistent_cameras`, `iteration_count`
- **Relationships**: belongs to `Session` (by `thread_id`)
- **Validation**: `scene_objects` is a map keyed by object name.

## State Transitions

- **Session.status**: `starting` → `ready` → `closed` (or `error`)
- **Message.status**: `streaming` → `final` (or `error`)
- **TodoItem.status**: `pending` → `in_progress` → `completed|failed`
