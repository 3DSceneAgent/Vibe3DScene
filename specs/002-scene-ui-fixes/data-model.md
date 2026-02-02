# Data Model: Scene UI Fixes

## Entities

### Thread

- **Fields**: `id`, `title`, `created_at`, `messages`, `scene`, `renders`, `gltf_url`
- **Relationships**: one-to-many with `Message`; one-to-one with `SceneInfo`; one-to-many with `RenderImage`
- **Validation**: `id` must be unique; `messages` ordered by `created_at`; `gltf_url` is nullable and cleared when a new export is generated.

### Message

- **Fields**: `id`, `role` (user|assistant), `content`, `raw`, `thinking` (optional), `created_at`, `status` (streaming|final|error)
- **Relationships**: belongs to `Thread`
- **Validation**: assistant messages may be empty while `status=streaming`; `content` must be non-empty when `status=final` unless an error occurred.

### SceneInfo

- **Fields**: `thread_id`, `scene_objects`, `persistent_cameras`, `iteration_count`
- **Relationships**: belongs to `Thread` (by `thread_id`)
- **Validation**: `scene_objects` is a map keyed by object name.

### SceneObject

- **Fields**: `type`, `location`, `dimensions`, `bounding_box`, `visible`, `material_count`
- **Relationships**: belongs to `SceneInfo`
- **Validation**: numeric arrays must contain numeric values.

### RenderImage

- **Fields**: `camera_name`, `image_base64`
- **Relationships**: belongs to `Thread`
- **Validation**: `image_base64` is a valid base64-encoded PNG payload.

### SceneExport

- **Fields**: `thread_id`, `filename`, `blob_url`, `created_at`, `status` (idle|exporting|ready|error)
- **Relationships**: belongs to `Thread`
- **Validation**: `blob_url` is present only when `status=ready`.

### LayoutState

- **Fields**: `history_collapsed` (boolean), `active_tab` (scene|chat)
- **Relationships**: UI-only, associated with the current user session
- **Validation**: `active_tab` must be one of the two panels.

## State Transitions

- **Message.status**: `streaming` → `final` (or `error`)
- **SceneExport.status**: `idle` → `exporting` → `ready` (or `error`)
- **LayoutState.history_collapsed**: `false` ⇄ `true`
