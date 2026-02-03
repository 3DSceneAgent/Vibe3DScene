# Quickstart: Scene Auto Fetch & Streaming UI

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

## Validate Auto-Refresh (Manual)

1. Open the web app and start a new conversation.
2. Enable the auto-refresh toggle in the top bar.
3. Send a prompt that triggers a scene change (e.g., create or modify an object).
4. Confirm:
   - The assistant finishes streaming.
   - Scene renders and the 3D viewport refresh automatically within 5 seconds.
5. Send a prompt that does **not** change the scene and confirm no automatic refresh occurs.
6. Toggle auto-refresh off and confirm no `/scene/{thread_id}/renders` or `/scene/{thread_id}/gltf` requests are fired.

## Validate Streaming Action Output (Manual)

1. Send a prompt that triggers tool/action execution.
2. Confirm action/tool output appears in the chat stream in-order with assistant text.
3. If images are returned, confirm they render inline within the action block.
4. Confirm tool output shows a collapse/expand control.

## Validate Streaming Response Documentation (Manual)

1. Open `docs/streaming-response.md`.
2. Confirm it describes streaming message parts (text, reasoning, tool output, images) and their ordering.

## Validate Collapsible Action Blocks (Manual)

1. Collapse an action/tool block in the chat.
2. Confirm the content is hidden.
3. Expand the block and confirm the content is restored without loss.

## Validate Scene Tab Collapse (Manual)

1. Collapse the Scene tab using the Hide/Show control in the top bar.
2. Confirm the scene UI hides without breaking the chat UI.
3. Expand the Scene tab and confirm prior scene content is retained.

## Validate Headless Command Configuration (Manual)

1. Set `BLENDER_MODE=headless`.
2. Set `BLENDER_HEADLESS_CMD` and `BLENDER_HEADLESS_ARGS` to run `scripts/blender_headless_client.py`.
3. Send a chat request with a new `thread_id`.
4. Confirm the headless Blender session starts and services the request.

## Validation Log

- 2026-02-03: Manual validation not run (local server not started).
- 2026-02-03: Manual validation pending after auto-refresh/tool UI updates.
