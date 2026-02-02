# Quickstart: UI Streaming and Headless Blender

## Prerequisites

- Python dependencies installed: `pip install -r requirements.txt`
- Node dependencies installed: `cd web && npm install`
- `.env` configured with `VLM_API_KEY`

## Run the API

```bash
python main.py --mode api --port 8000
```

## Run the Web App

```bash
cd web
npm run dev
```

## Validate Split Layout (Manual)

1. Open the web app and confirm the main workspace is a split view.
2. Verify the left panel shows scene controls and the right panel shows chat.
3. Resize the browser window and confirm both panels remain visible and usable.

## Validate Streaming UX (Manual)

1. Open the web app and start a new conversation.
2. Send a message and confirm:
   - An assistant bubble appears immediately with a loading spinner.
   - Partial content appears incrementally until completion.

## Validate Headless Mode (Manual)

1. Set `BLENDER_MODE=headless` and (optionally) configure `BLENDER_HEADLESS_CMD`/`BLENDER_HEADLESS_ARGS`.
2. Send a chat request with a new `thread_id`.
3. Confirm a headless Blender session is created and reused for subsequent requests.

## Validate Local-Client Mode (Manual)

1. Set `BLENDER_MODE=local-client` and ensure the Blender client is not running.
2. Without a running Blender client, request `/scene/{thread_id}`.
3. Confirm the API returns a clear, actionable error about missing local-client connectivity.

## Validation Log

- 2026-02-01: Manual UX validation not run (local server not started).
- 2026-02-01: Frontend lint `npm --prefix web run lint` → 2 warnings
  - `web/src/App.tsx`: missing `useEffect` dependencies (`refreshScene`, `refreshTodos`)
  - `web/src/components/GltfViewer.tsx`: missing `useEffect` dependencies
- 2026-02-01: Backend lint → no linter configured (skipped).
