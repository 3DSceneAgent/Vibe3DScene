# Startup and Deployment Guide

Last updated: 2026-03-31

This guide collects the practical startup paths that were previously spread across the README: local setup, single-worker and multi-worker startup, and Docker entry points.

## 1. Prerequisites

Required baseline:

- Python 3.11+
- Blender 3.6+
- Node.js 18+ for the frontend
- Redis for recommended persistence and multi-worker coordination

Optional:

- Nginx for local multi-worker gateway flows
- Docker for containerized deployment and external tool services

## 2. Install the Main Repository

```bash
git clone --recurse-submodules https://github.com/3DSceneAgent/Vibe3DScene
cd Vibe3DScene

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env
```

## 3. Install Platform Dependencies

### macOS

For local multi-worker setup:

```bash
brew install redis nginx
```

### Linux

For local multi-worker setup:

```bash
sudo apt install -y redis-server nginx
```

## 4. Single-Worker Startup

### Headless mode

Recommended for the web frontend and most automated scene-building flows.

```bash
./scripts/run_headless.sh
```

Equivalent manual entry:

```bash
python main.py --mode api --port 8000
```

Typical `.env` requirements:

- `BLENDER_MODE=headless`
- one provider API key
- headless Blender command variables if your Blender binary is not on the default path
- optional: `SCENE_AGENT_ENABLE_PENETRATION_VERIFY=true` to add geometry penetration checks inside the single-agent `verify` step

### Local-client mode

Use this when Blender is already running locally with the addon socket server available.

```bash
./scripts/run_local_client.sh
```

Typical `.env` requirements:

- `BLENDER_MODE=local-client`
- `BLENDER_HOST`
- `BLENDER_PORT`
- optional: `SCENE_AGENT_ENABLE_PENETRATION_VERIFY=true` if you want verify-time geometry penetration checks in addition to VLM verification

## 5. Multi-Worker Startup on a Local Machine

This path is useful for testing owner-proxy behavior, Redis-backed thread ownership, and gateway routing.

Start:

```bash
./scripts/run_multiprocess_local.sh
```

Stop:

```bash
./scripts/stop_multiprocess_local.sh
```

Notes:

- the local multi-worker flow expects Redis and Nginx to be available
- the script prefers `docker/.env.multiprocess` when present and falls back to `.env`
- each API worker still behaves as a single logical owner worker for the threads it owns

## 6. Docker Startup

### Single-process headless deployment

```bash
cp docker/.env.singleprocess.example docker/.env.singleprocess
# edit docker/.env.singleprocess
docker compose -f docker/docker-compose.singleprocess.yml up
```

### Multi-worker deployment

```bash
cp docker/.env.multiprocess.example docker/.env.multiprocess
# edit docker/.env.multiprocess
docker compose -f docker/docker-compose.multiprocess.yml up
```

The multi-worker compose stack uses:

- Nginx gateway
- multiple API workers
- Redis-backed coordination

## 7. Frontend Startup

```bash
cd web
npm install
npm run dev
```

Then point the frontend to the backend URL, typically `http://localhost:8000`.

## 8. Validation and Smoke Testing

Run the automated Python test suite:

```bash
pytest
```

Optional local multi-worker smoke test:

```bash
python scripts/smoke_multiprocess_local.py \
  --gateway-url http://127.0.0.1:8000 \
  --worker1-url http://127.0.0.1:18001 \
  --worker2-url http://127.0.0.1:18002
```

Optional topology validation:

```bash
python scripts/validate_prompt.py \
  --mode api \
  --base-url http://127.0.0.1:8000 \
  --prompt "Build a living room scene from the reference style." \
  --topology-run compare
```

## 9. External Tool Servers

If you want retrieval, reconstruction, or generation services beyond the core Blender runtime, install and start the sibling `3DAgentTools` stack:

```bash
git clone --recurse-submodules https://github.com/3DSceneAgent/3DAgentTools ../3DAgentTools
cd ../3DAgentTools
cp .env.example .env
# edit .env
./start_tool_servers.sh
```

See [Tool Servers](./tool-servers.md) for more detail.

## 10. Related Docs

- [System Architecture](../architecture/system-architecture.md)
- [Runtime Workflow](../architecture/runtime-workflow.md)
- [Tool Servers](./tool-servers.md)
- [MCP Server and Tools](../integrations/mcp-server-and-tools.md)
- [Configuration Reference](../reference/configuration.md)
