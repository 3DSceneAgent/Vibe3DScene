# Tool Servers Docker Compose Guide

This folder provides one-click Docker Compose workflow for the three tool servers used by `mcp_server/server.py`:

- TRELLIS2
- AssetRetrieval3D
- PCGIntegrator3D
- PostgreSQL (for AssetRetrieval3D)

## 1) Configure

```bash
cd tool_servers
cp .env.example .env
```

Set ports/hosts and service env vars in `.env`.
`start_tool_servers.sh` waits for HTTP health endpoints before returning.

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
- If host env has `HTTP_PROXY`/`HTTPS_PROXY`, `start_tool_servers.sh` injects TRELLIS2 and retrieval proxy as `http://host.docker.internal:7890`; if host vars are empty, proxy vars are not set in containers.

Compose files:

- `docker-compose.tools.yml`
- `docker-compose.tools.gpu.yml` (optional TRELLIS2 GPU override)

GPU selection notes for TRELLIS2:

- Keep `TRELLIS2_GPU=all` (required by current compose schema validation).
- Use `TRELLIS2_VISIBLE_DEVICES` to choose cards:
- `TRELLIS2_VISIBLE_DEVICES=1` means only the second GPU is visible in container.

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

- `DB_HOST=postgres`
- `QWEN_DB_OSS_URL`
- `QWEN_DB_DUMP_FORMAT`
- `QWEN_DB_AUTO_BOOTSTRAP=true`
- `QWEN_DB_DUMP_LOCAL_PATH=/cache/asset-retrieval/qwen_embeddings.dump.gz`
- `QWEN_DB_REUSE_LOCAL_DUMP=true`
- `RETRIEVAL_CACHE_DIR=./cache/asset-retrieval`
- `RETRIEVAL_HTTP_PROXY` / `RETRIEVAL_HTTPS_PROXY` / `RETRIEVAL_NO_PROXY` (optional manual overrides)
- `POSTGRES_DATA_DIR=./cache/postgres`
- `POSTGRES_INIT_DIR=./postgres/init`
- `HUGGINGFACE_CACHE_DIR=./cache/huggingface/hub`
- `WAIT_FOR_HEALTH_TIMEOUT_SECONDS=300` (max total wait time for TRELLIS2/retrieval/PCG health checks)

Notes:

- Docker login is required before push: `docker login`.
- Startup/stop scripts support both `docker compose` (v2) and `docker-compose`.
- TRELLIS2 Docker support already exists in `tool_servers/TRELLIS.2`.
- If you change ports in `tool_servers/.env`, update your root project `.env` (`TRELLIS2_PORT`, `RETRIEVAL_API_PORT`, `INFINIGEN_PORT`) to match.
- Retrieval defaults to the compose-managed PostgreSQL service (`DB_HOST=postgres`).
- Retrieval service hardcodes `DB_HOST=postgres` in compose to avoid stale host-side env overrides.
- PostgreSQL data is persisted on host via `POSTGRES_DATA_DIR` and retrieval dump cache remains mapped via `RETRIEVAL_CACHE_DIR`.
- Default `POSTGRES_IMAGE` is `pgvector/pgvector:pg17` to maximize dump compatibility (for example `transaction_timeout` in newer dumps).
- `POSTGRES_INIT_DIR` is mounted to `/docker-entrypoint-initdb.d` and includes `CREATE EXTENSION vector` on first DB initialization.
- PostgreSQL is internal-only by default (no host `5432` port publish), so it avoids host port conflicts.
- TRELLIS2 and retrieval services mount `HUGGINGFACE_CACHE_DIR` into container HF cache path to reuse downloaded model artifacts.
