# Research: Headless Runtime Reliability

## Streaming reliability

- **Decision**: Always emit a terminal stream event (`event: done`) and a structured error payload when streaming fails, and add SSE headers that prevent buffering.
- **Rationale**: The frontend depends on explicit completion to stop loading; buffering or abrupt connection close leads to stuck states and EOF errors.
- **Alternatives considered**: Rely on implicit connection close; switch to WebSockets for all streaming; ignore terminal events and infer completion from network close.

## Concurrent request handling

- **Decision**: Add configurable multi-worker execution for the API runtime and keep blocking Blender calls isolated via `to_thread` or locks.
- **Rationale**: Multiple workers allow true concurrency across requests without redesigning the agent, while thread offloading prevents blocking the event loop.
- **Alternatives considered**: Run a single worker with async-only concurrency; migrate to a separate process manager (gunicorn/supervisor) without in-app support.

## Process shutdown and cleanup

- **Decision**: Track headless Blender subprocesses in the session manager and terminate them on API shutdown; update the service runner to send a single stop signal that cascades to all child processes.
- **Rationale**: Headless Blender processes are spawned by the API and can survive parent termination unless explicitly cleaned up.
- **Alternatives considered**: Rely on OS process reaping; only kill the API process and leave Blender sessions running.

## Markdown rendering

- **Decision**: Render chat message Markdown in the web UI using a Markdown renderer with HTML disabled or sanitized (e.g., `react-markdown` with sanitation).
- **Rationale**: Markdown improves readability while HTML sanitization ensures embedded HTML/scripts remain inert.
- **Alternatives considered**: Render raw text only; use a non-React parser plus DOM sanitization.
