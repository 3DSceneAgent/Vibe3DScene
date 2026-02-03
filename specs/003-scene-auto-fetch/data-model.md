# Data Model: Scene Auto Fetch & Streaming UI

## Entities

### Message

- **Fields**: `id`, `thread_id`, `role` (user|assistant), `content`, `created_at`, `status` (streaming|final|error), `thinking` (optional), `stream_id` (optional)
- **Relationships**: belongs to `Thread`
- **Validation**: assistant messages may be empty while `status=streaming`, must be non-empty on `final` unless an error occurred.

### Thread

- **Fields**: `id`, `title`, `created_at`, `messages`, `todos`, `scene`, `renders`, `gltf_url`
- **Relationships**: owns `Message`, `TodoItem`, `SceneInfo`, `RenderImage`
- **Validation**: `id` unique; `gltf_url` revoked when replaced or deleted.

### StreamEvent

- **Fields**: `delta` (optional), `message_id` (optional), `messages` (optional list), `todos` (optional), `scene_has_change` (optional), `event` (optional: done), `error` (optional)
- **Relationships**: updates `Message`, `TodoItem`, and auto-refresh gating
- **Validation**: at least one of `delta`, `messages`, `todos`, `event`, or `error` must be present.

### ActionResultBlock

- **Fields**: `id`, `tool_name`, `payload`, `media` (optional list), `collapsed` (boolean)
- **Relationships**: derived from tool/action messages in the stream
- **Validation**: `payload` must be serializable; `collapsed` defaults to `false`.

### SceneInfo

- **Fields**: `thread_id`, `scene_objects`, `persistent_cameras`, `iteration_count`
- **Relationships**: belongs to `Thread`
- **Validation**: `scene_objects` is a map keyed by object name.

### RenderImage

- **Fields**: `camera_name`, `image_base64`
- **Relationships**: belongs to `Thread`
- **Validation**: `image_base64` must decode to a valid image.

### AutoRefreshPreference

- **Fields**: `thread_id`, `enabled`
- **Relationships**: belongs to `Thread`
- **Validation**: defaults to `false` unless explicitly enabled.

## State Transitions

- **Message.status**: `streaming` → `final` (or `error`)
- **ActionResultBlock.collapsed**: `false` ↔ `true` (user toggles)
- **AutoRefreshPreference.enabled**: `false` ↔ `true` (user toggles)
