 # Project Questions and Startup Notes
 
 This document collects the answers to the recent design questions and the
 recommended resolution for the backend MCP connection error when running
 in headless mode.
 
 ## Q1. `start_services.sh` vs `blender_tools.py` MCP startup
 
 **Short answer:** `scripts/start_services.sh` starts a standalone MCP server.
 `scene_agent/tools/blender_tools.py` starts a per-session MCP process only in
 headless mode when a `session_id` is provided.
 
 - `start_services.sh` launches `mcp_server/server.py` and the API server as
   independent processes.
 - `get_blender_tools()` in `blender_tools.py` starts the headless Blender
   process and the per-session MCP server only when:
   - `BLENDER_MODE=headless`, and
   - `get_blender_tools(session_id=...)` is called.
 - The call chain that triggers per-session MCP in headless mode:
   - API request -> `get_agent(thread_id)` -> `create_agent_graph(session_id)`
   -> `get_blender_tools(session_id)` -> `start_mcp_process(...)`.
 
 **Meaningfulness:** The per-session MCP startup path is useful for headless,
 per-thread sessions and is separate from the standalone MCP server started
 by `start_services.sh`.
 
 ## Q2. Is a single MCP server enough in headless mode?
 
 **Not if you run multiple Blender clients on the same host.**
 
 The current MCP server (`mcp_server/server.py`) connects to a single Blender
 socket host/port. If you run multiple headless Blender instances (different
 ports), a single MCP server cannot multiplex them.
 
 Available patterns in this repo:
 
 - **Standalone MCP server:** one MCP server connects to one Blender instance.
 - **Per-session MCP servers (headless):** `blender_tools.py` can spawn one MCP
   server per session with `BLENDER_MCP_BASE_PORT` + `BLENDER_MCP_PORT_RANGE`.
 
 If you need multiple headless clients on the same host, use per-session MCP
 servers (or extend MCP server to support multi-connection routing).
 
## Q3. `BACKEND_BASE_URL` in MCP server

`mcp_server/server.py` now returns a **relative render path** (e.g.
`/renders/{filename}`) and no longer uses `BACKEND_BASE_URL`. URL composition
is handled by the API/UI layer.
 
 ## Q4. Architecture summary and potential issues
 
 ### Architecture summary
 
 - **Agent Graph**: LangGraph state machine in `scene_agent/agent/graph.py`
 - **API**: FastAPI server in `scene_agent/interfaces/api.py`
 - **MCP Server**: `mcp_server/server.py`, exposes Blender tools
 - **Headless Process Mgmt**: `scene_agent/blender/session_manager.py`
 - **Memory**: scene and reference image memory in `scene_agent/memory/`
 - **VLM Providers**: `scene_agent/vlm/`
 
### Potential issues (current state)

- **Single MCP connection**: MCP server uses one Blender socket connection,
  which does not support multi-session headless routing.
- **Port collisions**: default `*_PORT_RANGE` is now 16 and allocation avoids
  collisions across active sessions.
- **Duplicate logic**: render post-processing and Blender socket connection
  are shared via utilities instead of duplicated.
- **Startup coupling**: API startup skips agent initialization in headless mode.
- **Config drift**: defaults now align (`BLENDER_PORT=9876`,
  `MCP_SERVER_PORT=9877`).
 
 ### Environment variables used in code (and missing from `.env.example.dev`)
 
 **Core settings (`scene_agent/config.py`):**
 - `VLM_MODEL`
 - `BLENDER_HOST`, `BLENDER_PORT`
 - `BLENDER_HEADLESS_STARTUP_TIMEOUT`
 - `HEADLESS_REQUEST_TIMEOUT_SECONDS`
 - `REFERENCE_IMAGE_MAX_COUNT`
 - `REFERENCE_IMAGE_MAX_BYTES`
 - `REFERENCE_IMAGE_STORAGE_DIR`
 - `RAG_ENABLED`
 
 **Headless + MCP process management:**
 - `BLENDER_HEADLESS_CMD`, `BLENDER_HEADLESS_ARGS`, `BLENDER_HEADLESS_LOG_DIR`
 - `BLENDER_HEADLESS_HOST`, `BLENDER_HEADLESS_BASE_PORT`, `BLENDER_HEADLESS_PORT_RANGE`
 - `BLENDER_MCP_HOST`, `BLENDER_MCP_BASE_PORT`, `BLENDER_MCP_PORT_RANGE`
 - `BLENDER_MCP_CMD`, `BLENDER_MCP_ARGS`
 
**MCP server configuration:**
- `MCP_SERVER_HOST`, `MCP_SERVER_PORT`
 
 **Blender headless client script:**
 - `BLENDER_ADDON_MODULE`, `BLENDER_ADDON_START_OP`, `BLENDER_HEADLESS_PORT`
 
 **Service integrations in MCP server:**
 - `TRELLIS2_HOST`, `TRELLIS2_PORT`
 - `RETRIEVAL_API_HOST`, `RETRIEVAL_API_PORT`
 
 ## Backend MCP error in headless mode (Solution C)
 
 **Observed error:** running
 `uvicorn scene_agent.interfaces.api:app --reload` with `BLENDER_MODE=headless`
 fails during startup because the API initializes the agent immediately, and
 the agent tries to connect to an MCP server that is not running.
 
 **Root cause:**
 - On startup, `startup_event()` calls `get_agent()` without a `thread_id`.
 - In headless mode, `get_agent()` creates a global agent without starting
   headless Blender or a per-session MCP server.
 - `get_blender_tools()` then attempts to connect to the default MCP URL
   (`http://localhost:9877/mcp`) and fails.
 
 **Solution C (preferred):**
 - Skip agent initialization on startup when `BLENDER_MODE=headless`.
 - Initialize per-thread agents lazily on the first request that includes
   a `thread_id`, which will correctly trigger headless + per-session MCP.
 
 **Implementation idea (no code changes here):**
 - In `startup_event()`, check the mode:
   - if `headless`, do not call `get_agent()`
   - else (local-client), call `get_agent()` as before
 
 This keeps headless mode lazy-initialized and avoids MCP connection failures
 when no standalone MCP server is running.
