# Agentic Workflow and Deployment Topologies

This document extracts the runtime agentic workflow from `docs/architecture/current-agent-workflow.md` and adds explicit deployment topologies for both single-worker and multi-worker operation.

## 1. Agentic Workflow (Runtime Graph)

The graph uses a **visual-first** architecture where verification is a fixed sequential step (not a conditional branch), and scene-level cameras auto-render after every scene mutation.

### Two Runtime Paths

| Path | Trigger | scene_observe | verify |
|------|---------|---------------|--------|
| **Scene mutation** | import, generate, execute_blender_code, set_texture | Runs (4 scene cameras re-render) | Structured multi-view feedback |
| **Object-level inspection** | camera_act, render_from_objects, camera_observe | Skips (no scene mutation) | Focused object-level feedback + todo context |

```mermaid
flowchart TD
    A["Client (Web/CLI)"] --> B["FastAPI /chat or /chat/stream"]
    B --> C["Resolve thread VLM selection"]
    C --> D["get_agent(thread_id)"]

    D --> E{"Graph exists and VLM match?"}
    E -- "No" --> F["create_agent_graph(session_id, provider, model, api_key)"]
    F --> G["get_blender_tools(session_id)"]
    G --> H["model.bind_tools(tools)"]
    H --> I["compile LangGraph with checkpointer"]
    I --> J["cache graph by thread_id"]
    E -- "Yes" --> J

    J --> K["agent.ainvoke / agent.astream"]
    K --> L["Node: agent"]
    L --> M["Node: post_agent (decision/todo extract)"]
    M --> N{"has tool calls?"}
    N -- "Yes" --> O["Node: tools (ToolNode)"]
    O --> P["Node: update_memory"]
    P --> PA["Node: scene_observe (conditional)"]
    PA --> PB["Node: verify (conditional)"]
    PB --> Q["Node: checkpoint_loop"]
    Q --> R{"run todo_check?"}
    R -- "Yes" --> S["Node: todo_check"]
    R -- "No" --> L

    N -- "No" --> V["Node: checkpoint_finalize"]
    V --> W{"run todo_check?"}
    W -- "Yes" --> S
    W -- "No" --> X["Node: finalize"]
    S --> Y{"completed or blocked?"}
    Y -- "Yes" --> X
    Y -- "No" --> L
    X --> Z["END / return response"]

    O --> BA["MCP tools"]
    BA --> BB["Blender addon socket server"]
    BB --> BC["Scene mutate / render / export"]

    CA["Idle sweeper / shutdown"] --> CB["persist .blend"]
    CB --> CC["terminate headless Blender + MCP process"]
```

### Key Design Decisions

- **verify is sequential, not a routing branch.** It runs between `scene_observe` and `checkpoint_loop` as a fixed step, skipping internally when there is no new unverified render. This ensures visual verification always precedes `todo_check`, preventing premature "completed" claims.
- **scene_observe is conditional.** It only fires when the latest tool batch contains scene-mutating tools. Object-level camera work (camera_act, render_from_objects) skips scene_observe entirely — the agent's own render flows directly to verify.
- **checkpoint_loop routing is simplified** to `todo_check | agent` (two targets instead of three). Verify no longer competes with todo_check for routing priority.

### Local Camera Persistence Policy (Object-Level)

- `scene_observe` remains **ephemeral** and bbox-driven for global diagnostics. It does not maintain a persistent 4-camera pool.
- Object-level tools (`camera_observe`, `render_from_objects`, `camera_act`) use a **persistent local work-camera pool** in the Blender addon (`Camera_Work_*`).
- Reuse path:
  - `render_from_objects(..., reuse_cameras=True)` and `camera_observe(..., reuse_cameras=True)` first query `CameraManager.find_matching_camera(...)`.
  - If no suitable camera is found, a new local work camera is created and registered.
- Eviction path (to avoid camera explosion):
  - Invalid local cameras are removed first.
  - Idle local cameras are removed after TTL (`LOCAL_CAMERA_IDLE_TTL_SECONDS`, default `900`).
  - Remaining overflow is trimmed by LRU up to `LOCAL_CAMERA_POOL_MAX_SIZE` (default `24`).
  - Active/focused cameras are protected from eviction.

### Verification Input Policy

- **Global verification (`render_source=scene_observe`)**
  - Input render: scene-level auto observation output.
  - Targets: full user request + optional uploaded reference images.
- **Local verification (`render_source=agent_camera`)**
  - Input render: latest object-level render from `camera_act`/`render_from_*`/`camera_observe`.
  - Targets: user request + optional references + current in-progress/pending todo context.
  - Goal: make local refinement checks align with current todo objectives rather than only coarse global intent.

## 2. Single-Worker Architecture

Single-worker means one API process serving requests directly.

```mermaid
flowchart LR
    U[Web UI / CLI] --> API[FastAPI process\nworkers=1]
    API --> COORD[Session coordinator]
    COORD --> AG[LangGraph Agent]
    AG --> MCP[MCP tools]
    MCP --> BL[Blender addon socket]
    BL --> OUT[Scene / Render / Assets]
    COORD --> REDIS[(Redis)]
    API --> FS[(Shared/local session storage)]
```

Reference startup command (current baseline):

```bash
cd /Users/fishwowater/projects/3DSceneAgent
python main.py --mode api --host 0.0.0.0 --port 8000 --workers 1
```

## 3. Multi-Worker Architecture (Docker + Gateway + Owner Proxy)

In multi-worker mode, every API instance still runs with `--workers 1`. Horizontal scaling is achieved by running multiple API processes/containers behind a gateway.

```mermaid
flowchart LR
    C[Client] --> GW[Nginx Gateway :8000]
    GW --> W1[API Worker 1\nworkers=1]
    GW --> W2[API Worker 2\nworkers=1]

    W1 --> R[(Redis Control Plane)]
    W2 --> R

    W1 --> D1{"Is owner for thread_id?"}
    W2 --> D2{"Is owner for thread_id?"}

    D1 -- Yes --> E1[Execute request locally]
    D1 -- No --> P1[Owner proxy forward to owner]
    D2 -- Yes --> E2[Execute request locally]
    D2 -- No --> P2[Owner proxy forward to owner]

    E1 --> HS1[Headless session manager\nBlender + per-session MCP]
    E2 --> HS2[Headless session manager\nBlender + per-session MCP]

    HS1 --> S[(Session storage / reference images)]
    HS2 --> S
```

Request routing behavior:
- Any worker can receive the request from gateway.
- Worker checks Redis ownership/lease for `thread_id`.
- Owner executes locally.
- Non-owner forwards request/stream to owner through `owner_proxy`.

## 4. Multi-Worker Docker Quickstart

```bash
cd /Users/fishwowater/projects/3DSceneAgent
cp docker/.env.multiprocess.example docker/.env.multiprocess
# edit docker/.env.multiprocess (provider and API keys)
docker compose -f docker/docker-compose.multiprocess.yml up --build
```

Gateway endpoint:
- `http://localhost:8000`

Related files:
- `docker/docker-compose.multiprocess.yml`
- `docker/nginx/multiprocess.conf`
- `scene_agent/session/owner_proxy.py`
- `docs/architecture/multiprocess-migration-plan.md`
