# Quickstart: Headless Runtime Reliability

## Prerequisites

- Python dependencies installed: `pip install -r requirements.txt`
- Web dependencies installed: `cd web && npm install`
- Blender MCP server available if required by your workflow

## Run in server-only (headless) mode

1. Set environment variables in `.env`:
   - `BLENDER_MODE=headless`
   - (Optional) `BLENDER_HEADLESS_CMD` and `BLENDER_HEADLESS_ARGS` as needed
2. Start services:
   - `./scripts/start_services.sh`
3. Confirm health:
   - `curl http://localhost:8000/health`

## Streaming reliability check

1. Open a streaming request in a terminal:
   - `curl -N -H "Content-Type: application/json" -d '{"message":"Hello","thread_id":"demo"}' http://localhost:8000/chat/stream`
2. Verify the stream emits incremental `data:` lines and ends with:
   - `{"event":"done", ...}` or `{"error":"..."}` (no indefinite loading).

## Concurrency check

1. Start two streaming requests in parallel (two terminals).
2. Confirm both streams progress independently and complete without waiting on each other.

## Shutdown check

1. Press Ctrl+C in the service runner terminal.
2. Verify that the MCP server, API server, and any headless Blender processes exit.

## Markdown rendering check (UI)

1. Start the web UI: `cd web && npm run dev`
2. Send a message containing Markdown (headings, lists, code block).
3. Confirm formatting renders correctly and embedded HTML is not executed.
