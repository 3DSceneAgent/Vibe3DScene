# Phase 0 Research: Scene UI Fixes

## Decision 1: Consolidate scene actions into a single top bar

**Decision**: Introduce a dedicated top bar that groups Refresh Scene, Fetch Renders, Load 3D Scene, Settings, and the new Download GLTF action, with Scene and Chat tabs placed beneath it.

**Rationale**: A single action cluster reduces visual clutter, makes scene controls easier to find, and aligns with the requirement to place Scene/Chat tabs below the top bar.

**Alternatives considered**:
- Keep actions inside the Scene tab only. Rejected because it conflicts with the desired top bar placement and increases vertical clutter.
- Use floating action buttons. Rejected due to discoverability and overlap risk with the Scene viewer.

## Decision 2: Dedicated scroll containers with wheel suppression for Scene Objects and Camera Renders

**Decision**: Constrain Scene Objects and Camera Renders to max-height containers with visible scrollbars, and suppress mouse wheel scrolling within these containers so scrolling occurs only through explicit scrollbar interaction.

**Rationale**: The requirement calls for two independent scrollbars and explicitly disallows mouse-wheel scrolling in those panels.

**Alternatives considered**:
- Allow mouse wheel scrolling and prevent scroll chaining. Rejected because it does not satisfy the “only scrollbar interaction” constraint.
- Use a single parent scroll container for all scene content. Rejected because it couples scrolling between panels and conflicts with the requirement.

## Decision 3: GLTF download uses the existing export endpoint

**Decision**: Reuse `GET /scene/{thread_id}/gltf`, create a Blob from the response, and trigger a browser download with a scene-specific filename.

**Rationale**: The backend already exports GLB files, so the UI can add a download action without new backend work or breaking contracts.

**Alternatives considered**:
- Introduce a new export endpoint returning a signed URL. Rejected to avoid extra storage and contract changes.
- Persist exported files on the server. Rejected because current workflows rely on ephemeral exports.

## Decision 4: Fix message concatenation by resetting per-message streaming buffers

**Decision**: Ensure each new assistant message has a clean streaming buffer and, when available, associate stream updates with `message_id` to avoid reusing prior content.

**Rationale**: The current behavior suggests raw buffers are being re-used across messages, causing concatenation. A per-message buffer avoids this.

**Alternatives considered**:
- Keep append-only behavior without message scoping. Rejected due to repeated concatenation bugs.
- Replace assistant content on every event. Rejected because it can cause flicker and lose partial updates.
