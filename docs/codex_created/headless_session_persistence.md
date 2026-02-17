# Headless Session Continuation Validation

This guide validates that headless sessions persist `.blend` state per `thread_id`,
auto-stop after inactivity, and resume correctly on the next request.

Persistence is intentionally enabled only for `BLENDER_MODE=headless`.
In `local-client` mode, session persistence/snapshot workflow is disabled.

## Prerequisites

- `BLENDER_MODE=headless`
- Blender executable available in `BLENDER_HEADLESS_CMD`
- API started with:
  - `./scripts/run_headless.sh`

## Suggested Environment

- `SESSION_IDLE_TIMEOUT_SECONDS=600`
- `SESSION_SWEEP_INTERVAL_SECONDS=30`
- `SESSION_BLEND_ROOT=/tmp/scene_agent_sessions`
- `SESSION_MAX_SNAPSHOTS=20`

For fast local verification you can temporarily set:

- `SESSION_IDLE_TIMEOUT_SECONDS=30`
- `SESSION_SWEEP_INTERVAL_SECONDS=5`

## Validation Steps

1. **Create session and mutate scene**
   - Send a chat request with `thread_id=session-a`.
   - Trigger any mutating operation (for example, import an asset, execute code, or camera action).
2. **Confirm persisted files**
   - Check `SESSION_BLEND_ROOT/session-a/scene.blend` exists.
   - Check `SESSION_BLEND_ROOT/session-a/snapshots/` contains pre-mutation snapshots.
3. **Wait for idle auto-stop**
   - Stop sending requests for longer than `SESSION_IDLE_TIMEOUT_SECONDS`.
   - Confirm logs contain `headless_session_idle_stopped`.
4. **Resume with same thread**
   - Send another chat request using `thread_id=session-a`.
   - Confirm scene state is continued from previous `.blend`.
5. **Undo snapshot path**
   - Call MCP tool `undo_last_snapshot` after a mutating step.
   - Verify scene rolls back to previous saved snapshot.

## Expected Behavior

- One `thread_id` maps to one persisted `.blend` path.
- Blender and session MCP processes are terminated after idle timeout.
- Next request with the same `thread_id` lazily restarts processes and reloads persisted scene.
- Conversation graph memory remains in-process while runtime processes are recycled.
