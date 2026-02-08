# Headless Blender Daemon Design

This document outlines a daemon-based approach for keeping Blender headless
alive and stable, avoiding per-request process startup and premature exit.

## Goals

- Keep a long-lived Blender headless process running.
- Provide a reliable socket server for API requests.
- Auto-restart on crash or exit.
- Simple local development workflow.

## High-Level Architecture

Components:
- **Daemon**: owns the Blender process lifecycle.
- **Blender headless**: runs the addon server and listens on a fixed port.
- **API server**: sends commands to the daemon-managed Blender socket.

Data flow:
1. API sends command to Blender socket.
2. Blender addon executes and responds.
3. API returns result to client.

## Daemon Responsibilities

- Start Blender with the correct command and args.
- Ensure the addon server is running.
- Keep the process alive (no one-shot script exit).
- Detect process exit and restart.
- Expose health checks (optional HTTP or simple socket probe).

## Recommended Implementation

### Process Manager (Python)

- A small Python service that:
  - Spawns Blender with `--background --python scripts/blender_headless_client.py`.
  - Verifies port availability (e.g., connect to `host:port`).
  - Restarts Blender if health checks fail.

### Health Check Strategy

- Primary: TCP connect to Blender socket port.
- Secondary: send a lightweight command (e.g., `get_scene_info`).
- If unhealthy, restart Blender.

### API Integration

- Replace per-request `ensure()` startup with:
  - A connection reuse layer.
  - On failure: request the daemon to restart.

## Configuration

Suggested environment variables:
- `BLENDER_DAEMON_HOST` (default: `localhost`)
- `BLENDER_DAEMON_PORT` (default: `9876`)
- `BLENDER_DAEMON_HEALTH_INTERVAL` (seconds)
- `BLENDER_DAEMON_RESTART_BACKOFF` (seconds)

## Development Workflow

1. Start the daemon.
2. Start the API server.
3. Use the web UI as usual.

## Rollout Plan

1. Add daemon service (new script/module).
2. Update API to reuse the daemon-managed socket.
3. Add health checks and restart logic.
4. Document how to start/stop the daemon.

## Notes

- This is not a large refactor: it is lifecycle management and process reuse.
- It avoids the current failure mode where Blender exits after the script ends.
