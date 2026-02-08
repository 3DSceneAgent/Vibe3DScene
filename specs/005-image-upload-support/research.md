# Research: Image Upload Support

## Image upload transport

- **Decision**: Add a dedicated multipart upload endpoint for reference images, separate from `/chat`, returning image metadata and IDs for the current thread.
- **Rationale**: The existing chat endpoints accept JSON bodies; multipart uploads keep large binaries out of chat payloads, simplify validation, and allow independent retries.
- **Alternatives considered**: Embed base64 images in `/chat` requests; use an external object store with signed URLs.

## Short-term memory storage

- **Decision**: Store uploaded images on the local filesystem with in-memory metadata keyed by thread ID; retain only for the active conversation.
- **Rationale**: No persistent datastore is present; filesystem + memory keeps implementation small while matching the short-term requirement.
- **Alternatives considered**: Persist to a database; store blobs in memory only (risking memory pressure).

## Reference selection logic

- **Decision**: Default to the most recently uploaded image unless the user explicitly references an earlier image; always record which image was used for verification.
- **Rationale**: Recency is a deterministic default that matches user expectation in the absence of explicit reference.
- **Alternatives considered**: Always pick the first image; add a required image selector in the UI.

## Headless request diagnostics

- **Decision**: Attach request-scoped diagnostics for headless requests (session ID, PID, log path, elapsed time) and enforce timeouts on Blender socket operations.
- **Rationale**: Hangs are hard to debug without process/log attribution; timeouts prevent indefinite waits in the API.
- **Alternatives considered**: Rely on global logs without per-request context; leave socket operations unbounded.

## Service shutdown behavior

- **Decision**: Update the service runner to trap SIGINT/SIGTERM and terminate all child processes (API, MCP, headless Blender) with a single interrupt.
- **Rationale**: The current runner reports "Stopping services" but leaves child processes running, causing blocked terminals.
- **Alternatives considered**: Manual process cleanup; per-service stop scripts.
