# Frontend Stream Refresh Recovery Plan

Date: 2026-04-03

## Summary

Current behavior breaks in two ways when the user refreshes the frontend while an agent response is still streaming:

1. The user can no longer see the in-progress assistant reply after refresh.
2. If the user sends another message after refresh, the old run and the new run can be merged into the same UI turn, which corrupts the conversation display.

The root cause is that execution state and presentation state are still too tightly coupled to one live SSE connection. The backend can continue producing events after the client disconnects, but those events are only buffered in process memory and the frontend does not persist the identifiers needed to resume the stream after a full page reload.

This can be fixed without introducing a full external job queue first. The recommended direction is to turn the current ephemeral stream session into a persisted run model backed by Redis, then let the frontend restore and re-subscribe to the active run after refresh.

This revised plan also closes the gaps identified during review:

- `single-active-run` must be created atomically across workers
- lost-owner runs must become terminal instead of blocking the thread forever
- replay must be bounded and idempotent
- checkpoint history and pending run snapshots must have explicit replacement rules

Difficulty assessment: medium-high.

## Current Architecture Findings

### Backend stream behavior

The chat streaming route in [scene_agent/interfaces/api/routes_chat.py](/Users/fishwowater/projects/3DSceneAgent/scene_agent/interfaces/api/routes_chat.py) already has a resumable shape:

- each stream gets a `stream_request_id`
- SSE events are published with monotonic `seq`
- the backend supports resume via `X-Stream-Request-Id` and `Last-Event-ID`
- on disconnect, the producer is not immediately stopped
- `_ActiveStreamSession.history` keeps recent events for replay

However, this history is only stored in the Python process:

- active sessions live in `_ACTIVE_STREAM_SESSIONS`
- replay history is stored in `_ActiveStreamSession.history`
- completed sessions are retained for only `_STREAM_SESSION_RETAIN_SECONDS = 120.0`

That means recovery only works if:

- the same backend worker is still alive
- the frontend still has the `stream_request_id`
- the frontend still has the last consumed event sequence
- reconnection happens within the short retention window

The first two requirements already fail on a browser refresh today.

### Frontend stream behavior

The SSE client in [web/src/api/client.ts](/Users/fishwowater/projects/3DSceneAgent/web/src/api/client.ts) already supports transient reconnect inside one page lifecycle:

- it remembers `resumeStreamId`
- it remembers `lastEventId`
- it retries a dropped stream connection a small number of times

But those values are function-local and disappear on full reload. There is no persisted thread-level run state in storage.

The React app in [web/src/App.tsx](/Users/fishwowater/projects/3DSceneAgent/web/src/App.tsx) reconstructs thread history after reload from:

- IndexedDB / localStorage thread snapshots
- `/threads/{thread_id}/history`

That history endpoint is built from graph checkpoint state in [scene_agent/interfaces/api/shared.py](/Users/fishwowater/projects/3DSceneAgent/scene_agent/interfaces/api/shared.py), not from active stream buffers. In practice this means:

- completed assistant messages are visible after reload
- in-progress assistant deltas are usually not visible after reload
- pending tool state also cannot be reliably reconstructed from the live run

### Existing ownership and client scoping

The repo already has two useful coordination layers:

- thread ownership and proxy routing across workers
- `frontend_client_id` binding for thread visibility and management

Relevant behavior in [scene_agent/interfaces/api/shared.py](/Users/fishwowater/projects/3DSceneAgent/scene_agent/interfaces/api/shared.py):

- thread visibility is already scoped by `frontend_client_id`
- a thread is associated with one frontend client for management purposes
- worker ownership and leases are already coordinated through Redis

This means the new run model should be scoped as:

- `active run` is unique per `thread_id`
- thread visibility stays governed by existing `frontend_client_id` rules
- multiple tabs from the same browser may resume the same active run

### Why replies get mixed together

The current UI turn assembly is mainly based on:

- `turnId`
- one local assistant placeholder
- `messageIdMapRef`
- current in-memory stream refs

After refresh, those refs are rebuilt from incomplete history, not from the live run. If the previous run is still executing in the backend and the user sends a new message, the UI no longer has a durable notion of "this thread already has an active run". As a result:

- the old run is invisible
- the user can start a new run
- placeholders and streamed assistant chunks can attach to the wrong turn

## Root Cause

The system currently has three separate representations of a conversation turn:

1. graph checkpoint state
2. in-memory active stream session
3. frontend local UI placeholders

Only the graph checkpoint is durable enough to survive refresh, but it typically represents finalized messages rather than a live streaming snapshot. The live stream buffer is the missing persistence layer.

## Recommended Direction

Introduce a persisted run abstraction for each active streamed agent execution.

The key design change is:

- agent execution is identified by `run_id`
- stream subscription is resumable independently of the original HTTP request
- active run state and event history are stored in Redis, not only in process memory

This keeps the current architecture mostly intact:

- the agent still runs inside the current backend worker
- the existing SSE format can largely stay the same
- the existing session ownership / proxy model can stay in place

But it removes the fragile assumption that one browser tab must stay open for the run to remain observable.

## Proposed Backend Changes

### 1. Add a persisted run model

For each streaming execution, persist a run record in Redis with at least:

- `run_id`
- `thread_id`
- `turn_id`
- `request_id`
- `status`: `running | done | error | cancelled | stale`
- `started_at_ms`
- `updated_at_ms`
- `finished_at_ms`
- `owner_worker_id`
- `lease_token`
- `lease_epoch`
- `last_seq`
- `snapshot_seq`
- `current_message_id`
- `pending_message_id`
- `scene_has_change`

Also persist run progress fields:

- `task_mode`
- `graph_steps`
- `last_node`
- `tool_events`
- `assistant_chunks`
- `todo_total`
- `todo_completed`

And persist a per-run replay structure:

- replayable events with strictly increasing `seq`
- optional compacted snapshot state when old events are truncated
- enough information to rebuild assistant text, reasoning, pending tool state, and todos

### 2. Create runs atomically

`single-active-run` must not rely on a read-then-create pattern.

Implement one Redis atomic operation, preferably in the same Lua-oriented style already used by [scene_agent/session/redis_registry.py](/Users/fishwowater/projects/3DSceneAgent/scene_agent/session/redis_registry.py), that does all of the following in one step:

- checks the current `thread_active_run:{thread_id}` pointer
- loads the pointed run status if present
- if the pointed run is non-terminal, returns conflict with that run
- if absent or terminal, creates a new run record
- initializes run metadata and `last_seq = 0`
- stores `thread_active_run:{thread_id} = run_id`

This is the only accepted creation path for `POST /chat/stream`.

### 3. Add lease/watchdog terminalization

V1 does not attempt cross-worker execution migration. Therefore owner loss must explicitly terminate the run instead of leaving it forever active.

Add a watchdog rule:

- if a run is `running` and its owner lease can no longer be refreshed or is observed expired beyond a grace window, atomically mark it `stale`
- emit one terminal error event for the run
- clear the thread active-run pointer if it still points to this run

This ensures:

- the frontend can show the run as failed/stale
- the thread becomes sendable again
- users are not permanently blocked by an orphaned run

### 4. Add explicit cancel support

Add `POST /threads/{thread_id}/runs/{run_id}/cancel` as the unblock escape hatch.

Behavior:

- if the run is `running` or `stale`, mark it `cancelled`
- emit a terminal event
- clear the thread active-run pointer if still current
- future sends are allowed immediately after successful cancel

This endpoint is required because frontend send blocking becomes unsafe without an explicit way to break bad states.

### 5. Add terminal-state fencing for run writers

Terminal transitions (`done`, `error`, `stale`, `cancelled`) must fence out stale producers.

Add a write-ownership guard in run metadata, for example:

- `writer_epoch` (or reuse `lease_epoch` as the fencing token)
- `terminal_at_seq`
- `terminal_reason`

Rules:

- every event append must atomically verify writer token still matches run ownership
- once a terminal status is written, reject any later non-terminal event append
- only one terminal event is allowed per `run_id`
- if a producer loses fencing, it must stop publishing and exit quickly

Without fencing, a cancelled/stale run can still receive late chunks from an old producer and corrupt replay/history consistency.

## Replay, Consistency, and Storage Bounds

### Replay semantics

Replay is at-least-once, not exactly-once.

To make this safe:

- backend guarantees `seq` is strictly increasing per `run_id`
- frontend persists `lastAppliedSeq` per `run_id`
- frontend ignores any event with `seq <= lastAppliedSeq`

All streaming mutations must be applied under the tuple:

- `(run_id, seq)`

This applies to:

- assistant text deltas
- reasoning deltas
- pending tool placeholders
- tool result messages
- terminal `done/error/stale/cancelled` events

### Event bounds and compaction

The replay log must have hard bounds. Use explicit caps such as:

- max event count per run
- max serialized byte size per run
- TTL for finished terminal runs

When the active log exceeds bounds:

- compact older replay state into a snapshot record
- keep latest assistant content, reasoning, pending tool state, todos, and `snapshot_seq`
- delete or trim replay events older than the snapshot boundary

If a client resumes from before the truncation boundary:

- respond with `replay_truncated = true`
- provide `snapshot_seq`
- replay from snapshot plus subsequent events

Do not keep an unbounded append-only event log for active runs.

### History/pending snapshot replacement rules

When extending thread history with live run state, define one synthetic pending assistant snapshot with:

- deterministic `message_id`, derived from `run_id`
- `status = streaming`
- `run_id`
- `turn_id`

Replacement rules:

- if checkpoint history already contains the final assistant message for that run/turn, do not append the pending snapshot
- if the pending snapshot is present and the final assistant message later appears, the frontend must replace the pending snapshot rather than render both
- tool placeholders should also use deterministic identities based on `run_id` and tool call identity

This avoids the double-render window where checkpoint history and run snapshot overlap during finalization.

## Proposed APIs and Types

### Backend APIs

Keep:

- `POST /chat/stream` as "create or conflict and subscribe immediately"

Add:

- `GET /threads/{thread_id}/active-run`
- `GET /threads/{thread_id}/runs/{run_id}`
- `GET /threads/{thread_id}/runs/{run_id}/stream`
- `POST /threads/{thread_id}/runs/{run_id}/cancel`

Conflict behavior must be standardized:

- return `409`
- include `run_id`
- include `status`
- include `resumable`
- include `last_seq`

Authorization and scoping rules must also be explicit:

- all run APIs must enforce existing thread visibility constraints based on `frontend_client_id`
- `run_id` must belong to the `thread_id` in path; mismatch returns `404` (not cross-thread leakage)
- cancel requests must verify caller can manage the target thread under current ownership/proxy rules
- cancel and terminal transitions should emit audit logs with `thread_id`, `run_id`, caller client id, and reason

### Shared types

Extend stream events to include:

- `run_id`
- `seq`
- optional `snapshot_seq`
- optional `replay_truncated`
- terminal `run_status`

Extend history response to include:

- `active_run`
- message `status`
- optional `run_id`

Add run summary types such as:

- `ActiveRunInfo`
- `ThreadRunInfo`

## Proposed Frontend Changes

### 1. Persist active run state per thread

Extend the thread state model in [web/src/state/types.ts](/Users/fishwowater/projects/3DSceneAgent/web/src/state/types.ts) and persisted storage so each thread can remember:

- `activeRunId`
- `activeTurnId`
- `activeRunStatus`
- `lastAppliedSeq`
- `replayTruncated`
- `pendingAssistantMessageId`
- `resumeStateUpdatedAtMs`

These values should be saved to IndexedDB / localStorage together with the thread.

### 2. Restore active runs on reload

On app startup or when a thread becomes active:

1. load persisted local thread state
2. fetch `/threads/{thread_id}/active-run`
3. if the backend confirms an active run, re-subscribe to that run using `lastAppliedSeq`
4. if the backend says there is no active run, clear stale local run state
5. if the backend reports truncation, rebuild from the provided snapshot before consuming later events

This means refresh recovery is deterministic and no longer depends on in-memory browser refs.

### 3. Bind UI placeholders to `run_id`

Current UI placeholder logic needs one more stable dimension:

- every streaming assistant placeholder belongs to a `run_id`
- pending tool placeholders belong to a `run_id`
- finalization only touches messages associated with that `run_id`

This prevents the old hidden run from being merged with a new send action after reload.

### 4. Block sending while a thread has an active run

When `activeRunId` exists and is still non-terminal:

- default send action is blocked
- the frontend follows one unified conflict path
- the user can either resume the current run or cancel it first

This keeps one-thread-one-run semantics and avoids silent corruption.

### 5. Support multi-tab idempotent recovery

Multiple tabs from the same frontend client may resume the same run. The UI must therefore:

- treat backend replay as authoritative
- apply events idempotently by `(run_id, seq)`
- avoid generating duplicate placeholders if the same event arrives in two tabs

No tab should assume it is the sole subscriber to a run.

### 6. Standardize send/retry/resume/cancel state machine

`retry` must follow the same active-run guard as `send`; do not allow side paths.

Required behavior matrix:

- `idle`: `send` allowed, `retry` allowed, `resume` no-op, `cancel` no-op
- `running`: `send` blocked with conflict payload, `retry` blocked with same conflict payload, `resume` allowed, `cancel` allowed
- `stale`: `send` blocked until user `cancel`s or backend auto-clears pointer, `retry` blocked, `resume` returns terminal state, `cancel` allowed
- `done | error | cancelled`: `send` allowed, `retry` allowed per existing turn rules, `resume` returns terminal/no-active, `cancel` no-op

Frontend should implement one unified conflict handler for both `send` and `retry`.

## Recommended Rollout

### Phase 1

- Redis-backed run metadata
- atomic create-or-conflict operation
- active-run pointer per thread
- run replay endpoint with bounded event retention
- writer fencing on terminal transitions
- frontend persisted `activeRunId` and `lastAppliedSeq`
- send blocking plus unified `409` conflict handling
- stale/watchdog terminalization
- cancel endpoint
- run API authorization checks aligned with existing `frontend_client_id` visibility
- feature flag to switch between legacy in-memory stream recovery and Redis run recovery
- baseline metrics and dashboards for rollout safety

This phase should already fix the two user-visible bugs:

- refresh no longer hides the active run
- a new send cannot silently mix with the old run

### Phase 2

- history hydration with pending assistant snapshots
- replay compaction / snapshot restore
- deterministic pending tool restoration
- polish for multi-tab recovery and truncated replay UX

## Difficulty and Risk Assessment

### Feasibility

High. The existing codebase already contains:

- resumable SSE event IDs
- short-term stream session replay
- Redis session coordination
- Redis graph checkpointing
- thread-bound ownership semantics
- existing Lua-based Redis coordination patterns

The required work is architectural but incremental, not a greenfield rewrite.

### Main difficulty

The hard part is not replaying text deltas. The hard part is making all three state views agree:

- active run store
- checkpoint history
- frontend placeholder state

If those are not reconciled carefully, duplicate assistant messages, stale placeholders, and blocked threads will appear.

### Complexity estimate

- short-term reconnect-only fix: medium
- durable refresh recovery with persisted runs: medium-high
- full process-restart-safe execution continuation: high

## Test Plan

### Backend

- two concurrent `POST /chat/stream` calls on the same thread: exactly one run is created, the other gets `409`
- start a streamed run, disconnect client, confirm run continues and replay data grows
- reconnect using stored `run_id` and `after_seq`, confirm only missing events are applied
- owner dies mid-stream, lease expires, run becomes `stale`, active-run pointer clears, and the thread becomes sendable again
- cancel a `running` or `stale` run and confirm it reaches `cancelled` and unblocks the thread
- hit replay compaction boundary and verify resume before `snapshot_seq` still restores correctly
- force cancel while producer is still emitting and verify fencing blocks post-terminal appends
- verify run APIs reject cross-thread `run_id` probing and unauthorized cancel attempts

### Frontend

- refresh while assistant is streaming, confirm prior reply reappears and continues
- refresh during tool execution, confirm pending tool UI survives and resolves correctly
- refresh after run finished but before user interacts again, confirm final message is shown once
- try to send a new message while an active run exists, confirm the unified conflict branch is used
- try `retry` while an active run exists, confirm it uses the same unified conflict branch as `send`
- multiple tabs resume the same run, confirm `(run_id, seq)` dedupe prevents duplicate chunks and placeholders

### Integration and failure injection

- owner worker crash in the middle of streaming
- Redis TTL / retention boundary on finished runs
- replay truncation boundary with delayed resume
- history endpoint on the exact boundary where final assistant lands in checkpoint while a pending snapshot still exists
- rollout flag off: legacy flow still works without run APIs
- rollout flag on: conflict/cancel/resume metrics remain within expected thresholds

## Observability and Rollback

Track and alert on:

- `run_create_conflict_rate`
- `run_resume_success_rate`
- `run_cancel_success_rate`
- `run_stale_terminalization_count`
- `replay_truncated_rate`
- `post_terminal_append_rejected_count` (fencing effectiveness)

Rollout strategy:

- guard new run recovery path behind a backend feature flag and matching frontend capability gate
- start with internal/canary clients, then ramp gradually
- keep fast rollback by disabling the flag and falling back to legacy in-memory stream behavior
- preserve schema backward compatibility during rollout so active sessions survive toggles

## Assumptions

- `active run` is unique per `thread_id`, not per `frontend_client_id`
- thread visibility and management continue to be governed by existing `frontend_client_id` semantics
- v1 does not support cross-worker execution migration after owner loss
- Redis is the required persistence layer for run state, replay metadata, and atomic lifecycle transitions

## Expected Outcome

After these changes:

- refreshing the page during agent streaming should continue to show the same in-progress reply
- the frontend should know that the thread already has an active run
- a second user send should no longer be allowed to silently merge with the unfinished previous run
- owner-loss scenarios should terminate cleanly instead of leaving the thread blocked
- replay should remain bounded, resumable, and idempotent across refresh, reconnect, and multi-tab recovery
