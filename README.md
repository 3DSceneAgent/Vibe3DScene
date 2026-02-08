# 3D Scene Agent

LangGraph-based backend that builds 3D scenes in Blender using natural language
and optional reference images. It integrates with Blender through MCP and
supports streaming responses over API and CLI.

## Overview and Architecture

The system combines a LangGraph agent with MCP-based Blender control and a
FastAPI service layer.

```
User / CLI / Web UI
        |
        v
   FastAPI API
        |
        v
 LangGraph Agent
        |
        v
   MCP Client
        |
        v
   MCP Server
        |
        v
Blender Addon (Socket)
        |
        v
Renders, Scene State, Assets
```

## Demo

TBD.

## Installation

1. Create a virtual environment and install Python dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. Install Blender (required for local-client or headless mode).
3. For the web UI, install Node.js and npm (optional):
   ```bash
   cd web
   npm install
   ```

## Quickstart

### Environment Variable Setup

1. Copy an environment template:
   ```bash
   cp .env.example.dev .env
   ```
2. Set required values:
   - `VLM_PROVIDER`
   - `VLM_API_KEY`
   - `BLENDER_MODE` (`local-client` or `headless`)

### Start Services with `scripts/start_services.sh`

This script starts the MCP server and the API server:
```bash
bash scripts/start_services.sh
```

### Local-client Mode

1. Set `BLENDER_MODE=local-client`.
2. Install and enable the Blender addon from `addon/`.
3. Start the Blender addon socket server.
4. Start the MCP server:
   ```bash
   python mcp_server/server.py
   ```
5. Start the API server:
   ```bash
   python main.py --mode api --port 8000
   ```

### Headless Mode

1. Set `BLENDER_MODE=headless`.
2. Provide headless startup configuration:
   - `BLENDER_HEADLESS_CMD`
   - `BLENDER_HEADLESS_ARGS`
3. Start the API server:
   ```bash
   python main.py --mode api --port 8000
   ```
4. Send a request with a new `thread_id` to initialize a headless session.

## Usage

### CLI Usage

```bash
python main.py --mode cli
```

### API Usage

Common endpoints:
- `POST /chat`
- `POST /chat/stream`
- `GET /scene/{thread_id}`
- `GET /scene/{thread_id}/renders`
- `GET /threads/{thread_id}/reference-images`
- `POST /threads/{thread_id}/reference-images`
- `GET /todos/{thread_id}`
- `WS /ws`

Example request:
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Create a studio lighting setup", "thread_id": "demo"}'
```

## Web UI

1. Start the API server.
2. Run the web UI:
   ```bash
   cd web
   npm run dev
   ```
3. Open the UI and set the backend URL in the settings panel.

## Codebase Structure

```
3DSceneAgent/
├── addon/               # Blender addon
├── docs/                # Documentation
├── mcp_server/          # MCP server implementation
├── scene_agent/         # Agent, API, memory, config
├── scripts/             # Service and headless helpers
├── tests/               # Test suites
└── web/                 # Web UI
```

## Acknowledgement

This project builds on:
- LangGraph
- Model Context Protocol (MCP)
- Blender and the Blender Python API
- langchain-mcp-adapters
- PolyHaven assets
- TRELLIS2
