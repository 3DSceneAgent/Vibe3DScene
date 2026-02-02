# Quickstart: Scene UI Fixes

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

## Validate Layout & Controls (Manual)

1. Open the web app and confirm a single top bar contains Refresh Scene, Fetch Renders, Load 3D Scene, Settings, and Download GLTF.
2. Confirm the Scene tab and Chat tab appear below the top bar and are visible side-by-side.
3. Collapse the left conversation history and verify the workspace expands; expand it again.

## Validate Scroll Behavior (Manual)

1. Populate Scene Objects with a long list and confirm it is constrained by height and scrolls via its own scrollbar.
2. Populate Camera Renders with multiple images and confirm it is constrained by height and scrolls via its own scrollbar.
3. Use the mouse wheel over Scene Objects/Camera Renders and confirm those lists do not scroll unless the scrollbar is explicitly dragged.
4. Scroll the chat history and confirm the Scene panel remains fixed in place.

## Validate GLTF Download (Manual)

1. Load a scene and click Download GLTF.
2. Confirm a `.glb` file is downloaded and can be opened by a GLTF viewer.
3. Attempt download with no scene loaded and confirm a clear disabled state or error message appears.

## Validate Message Rendering (Manual)

1. Send two prompts in sequence that produce short responses (e.g., "abcd" then "xyz").
2. Confirm the second assistant message renders only "xyz" and does not append to the previous message.

## Validate Headless Mode (Manual)

1. Set `BLENDER_MODE=headless` in `.env`.
2. (Optional) Configure headless Blender startup if not already set:
   - `BLENDER_HEADLESS_HOST` (default: `localhost`)
   - `BLENDER_HEADLESS_BASE_PORT` (default: `9876`)
   - `BLENDER_HEADLESS_PORT_RANGE` (default: `1`)
   - `BLENDER_HEADLESS_CMD` (e.g., `/Applications/Blender.app/Contents/MacOS/Blender`)
   - `BLENDER_HEADLESS_ARGS` (supports `{session_id}`, `{host}`, `{port}`)
3. (Optional) Configure MCP server commands if needed:
   - `BLENDER_MCP_HOST`, `BLENDER_MCP_BASE_PORT`, `BLENDER_MCP_PORT_RANGE`
   - `BLENDER_MCP_CMD` (default: `python`)
   - `BLENDER_MCP_ARGS` (default: `mcp/server.py`)
4. Start the API and send a chat request with a new `thread_id`.
5. Confirm a headless session is created and subsequent requests reuse it.

## Validation Log

- 2026-02-02: Manual validation not run (local server not started).
