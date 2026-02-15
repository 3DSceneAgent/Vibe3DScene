# Agentic Workflow and Deployment Topologies

This document extracts the runtime agentic workflow from `docs/architecture/current-agent-workflow.md` and adds explicit deployment topologies for both single-worker and multi-worker operation.

## 1. Agentic Workflow (Runtime Graph)

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
    P --> Q["Node: checkpoint_loop"]
    Q --> R{"run todo_check?"}
    R -- "Yes" --> S["Node: todo_check"]
    R -- "No" --> T{"need verify?"}
    T -- "Yes" --> U["Node: verify"]
    U --> L
    T -- "No" --> L

    N -- "No" --> V["Node: checkpoint_finalize"]
    V --> W{"run todo_check?"}
    W -- "Yes" --> S
    W -- "No" --> X["Node: finalize"]
    S --> Y{"stage == finalize?"}
    Y -- "Yes" --> X
    Y -- "No" --> T
    X --> Z["END / return response"]

    O --> BA["MCP tools"]
    BA --> BB["Blender addon socket server"]
    BB --> BC["Scene mutate / render / export"]

    CA["Idle sweeper / shutdown"] --> CB["persist .blend"]
    CB --> CC["terminate headless Blender + MCP process"]
```

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
docker compose -f docker-compose.multiprocess.yml up --build
```

Gateway endpoint:
- `http://localhost:8000`

Related files:
- `docker-compose.multiprocess.yml`
- `docker/nginx/multiprocess.conf`
- `scene_agent/session/owner_proxy.py`
- `docs/architecture/multiprocess-migration-plan.md`
