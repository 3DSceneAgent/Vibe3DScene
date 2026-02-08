# Data Model: Image Upload Support

## Entities

### ReferenceImage

- **Fields**:
  - `id` (string, required): Unique image identifier.
  - `thread_id` (string, required): Conversation session ID.
  - `filename` (string, required): Original upload filename.
  - `content_type` (string, required): MIME type (e.g., image/png).
  - `size_bytes` (integer, required): Uploaded file size.
  - `sha256` (string, required): Content hash for deduplication/logging.
  - `stored_path` (string, required): Local path to short-term file storage.
  - `uploaded_at` (timestamp, required): Upload time.
  - `note` (string, optional): User-provided label or UI note.
- **Validation**:
  - `content_type` must be an image type.
  - `size_bytes` must be within configured upload limits.
  - No more than 3 images per `thread_id`.

### ConversationSession

- **Fields**:
  - `thread_id` (string, required): Stable identifier for the conversation.
  - `reference_images` (array of ReferenceImage, optional): Current references.
  - `last_reference_id` (string, optional): Most recently uploaded image ID.
- **Validation**:
  - `thread_id` must be non-empty.

### VerificationResult

- **Fields**:
  - `thread_id` (string, required): Session identifier.
  - `reference_id` (string, optional): Image used for comparison.
  - `status` (enum: `match`, `mismatch`, `text-only`): Verification outcome.
  - `reason` (string, optional): User-facing explanation.
  - `created_at` (timestamp, required): Verification time.
- **Validation**:
  - `status = text-only` only when no reference images are present.

### DiagnosticRecord

- **Fields**:
  - `request_id` (string, required): Unique request identifier.
  - `thread_id` (string, required): Session identifier.
  - `session_id` (string, optional): Headless Blender session ID.
  - `process_id` (integer, optional): Headless Blender PID.
  - `log_path` (string, optional): Request-scoped log file path.
  - `elapsed_ms` (integer, required): Duration of the request.
  - `status` (enum: `ok`, `error`, `timeout`): Outcome.
- **Validation**:
  - `elapsed_ms` must be non-negative.

## Relationships

- A `ConversationSession` owns up to three `ReferenceImage` entries.
- A `VerificationResult` references the `ReferenceImage` used (when present).
- A `DiagnosticRecord` is created per headless request and may link to a spawned process.
