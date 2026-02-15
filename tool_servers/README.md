# Tool Servers Docker Compose Guide

This folder provides one-click Docker Compose workflow for the three tool servers used by `mcp_server/server.py`:

- TRELLIS2
- AssetRetrieval3D
- PCGIntegrator3D

## 1) Configure

```bash
cd tool_servers
cp .env.example .env
```

Set ports/hosts and service env vars in `.env`.

Per-service toggles:

- `ENABLE_TRELLIS2=true|false`
- `ENABLE_RETRIEVAL=true|false`
- `ENABLE_PCG=true|false`

Default host bindings:

- TRELLIS2: `0.0.0.0:8001`
- AssetRetrieval3D: `0.0.0.0:8002`
- PCGIntegrator3D: `0.0.0.0:8003`

By default, startup pulls images from Docker Hub (`TOOL_PULL_IMAGES=true`).

Default image refs are configured for Docker Hub user `fishwowater`:

- `fishwowater/trellis.2:latest`
- `fishwowater/assetretrieval3d:latest`
- `fishwowater/pcgintegrator3d:latest`

If TRELLIS2 model pull requires Hugging Face auth, set:

- `HUGGINGFACE_TOKEN=<your_token>`
- `HUGGINGFACE_CACHE_DIR=./cache/huggingface/hub` (host cache mapped into containers to avoid repeated model downloads)

Compose files:

- `docker-compose.tools.yml`
- `docker-compose.tools.gpu.yml` (optional TRELLIS2 GPU override)

## 2) Start all three services

```bash
cd tool_servers
./start_tool_servers.sh
```

## 3) Stop all three services

```bash
cd tool_servers
./stop_tool_servers.sh
```

## 4) Build & push images (for the two newly dockerized services)

### PCGIntegrator3D

```bash
cd tool_servers/PCGIntegrator3D
DOCKERHUB_NAMESPACE=<your_dockerhub_user> IMAGE_TAG=latest ./scripts/docker_build_push.sh
```

### AssetRetrieval3D

```bash
cd tool_servers/AssetRetrieval3D
DOCKERHUB_NAMESPACE=<your_dockerhub_user> IMAGE_TAG=latest ./scripts/docker_build_push.sh
```

If you are using OSS bootstrap for retrieval DB, set these in `tool_servers/.env`:

- `QWEN_DB_OSS_URL`
- `QWEN_DB_DUMP_FORMAT`
- `QWEN_DB_AUTO_BOOTSTRAP=true`
- `QWEN_DB_DUMP_LOCAL_PATH=/cache/asset-retrieval/qwen_embeddings.dump.gz`
- `QWEN_DB_REUSE_LOCAL_DUMP=true`
- `RETRIEVAL_CACHE_DIR=./cache/asset-retrieval`
- `HUGGINGFACE_CACHE_DIR=./cache/huggingface/hub`

Notes:

- Docker login is required before push: `docker login`.
- Startup/stop scripts support both `docker compose` (v2) and `docker-compose`.
- TRELLIS2 Docker support already exists in `tool_servers/TRELLIS.2`.
- If you change ports in `tool_servers/.env`, update your root project `.env` (`TRELLIS2_PORT`, `RETRIEVAL_API_PORT`, `INFINIGEN_PORT`) to match.
- Retrieval compose service now injects `host.docker.internal` via `host-gateway`, so Linux hosts can resolve it without extra manual DNS setup.
- TRELLIS2 and retrieval services mount `HUGGINGFACE_CACHE_DIR` into container HF cache path to reuse downloaded model artifacts.
