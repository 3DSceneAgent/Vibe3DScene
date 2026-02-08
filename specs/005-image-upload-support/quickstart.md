# Quickstart: Image Upload Support

## Prerequisites

- Python dependencies installed: `pip install -r requirements.txt`
- Web dependencies installed: `cd web && npm install`
- Blender MCP server available if required by your workflow

## Start services

1. Start the backend API:
   - `python main.py --mode api --port 8000`
2. Start the web UI:
   - `cd web && npm run dev`
3. Confirm health:
   - `curl http://localhost:8000/health`

## Upload reference images (API)

1. Upload up to three images for a thread:
   - `curl -F "images=@/path/to/ref1.png" -F "images=@/path/to/ref2.jpg" http://localhost:8000/threads/demo/reference-images`
2. Verify the response includes uploaded image metadata and IDs.

## Upload reference images (UI)

1. Open the chat composer.
2. Attach 1–3 reference images and submit.
3. Confirm the UI shows upload success and retains the references for subsequent verification.

## Verification flow check

1. Ask the agent to render or verify the scene.
2. Confirm the verification report indicates which reference image was used.
3. Upload a new reference image and repeat; the most recent image should be selected by default.

## Headless reliability check

1. Set `BLENDER_MODE=headless` in `.env`.
2. Request `/scene/{thread_id}` and `/scene/{thread_id}/renders`.
3. Confirm responses return within 30 seconds or provide actionable errors with diagnostic context.

## Shutdown check

1. Start services using `./scripts/start_services.sh`.
2. Press Ctrl+C once.
3. Verify the MCP and API processes exit within 5 seconds and the terminal returns.
4. If running headless sessions, confirm any headless Blender processes are stopped.
