#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
ENV_EXAMPLE="${SCRIPT_DIR}/.env.example"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.tools.yml"
GPU_COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.tools.gpu.yml"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  echo "Created ${ENV_FILE} from template. Please review values before production use."
fi

set -a
source "$ENV_FILE"
set +a

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing command: $1" >&2
    exit 1
  }
}

require_cmd docker

if docker compose version >/dev/null 2>&1; then
  COMPOSE_BIN=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_BIN=(docker-compose)
else
  echo "Neither 'docker compose' nor 'docker-compose' is available." >&2
  exit 1
fi

: "${DOCKER_NETWORK:=scene-agent-tools}"
: "${TOOL_PULL_IMAGES:=true}"
: "${TOOL_RECREATE_CONTAINERS:=true}"
: "${ENABLE_TRELLIS2:=true}"
: "${ENABLE_RETRIEVAL:=true}"
: "${ENABLE_PCG:=true}"
: "${TRELLIS2_ENABLE_GPU:=true}"
: "${TRELLIS2_GPU:=all}"

COMPOSE_ARGS=(-f "$COMPOSE_FILE")
if [[ "$TRELLIS2_ENABLE_GPU" == "true" ]]; then
  COMPOSE_ARGS+=( -f "$GPU_COMPOSE_FILE" )
fi

SERVICES=()
if [[ "$ENABLE_TRELLIS2" == "true" ]]; then
  SERVICES+=(trellis2)
fi
if [[ "$ENABLE_RETRIEVAL" == "true" ]]; then
  SERVICES+=(retrieval)
fi
if [[ "$ENABLE_PCG" == "true" ]]; then
  SERVICES+=(pcg)
fi

if [[ ${#SERVICES[@]} -eq 0 ]]; then
  echo "No services enabled. Set at least one of ENABLE_TRELLIS2/ENABLE_RETRIEVAL/ENABLE_PCG=true." >&2
  exit 1
fi

cd "$SCRIPT_DIR"

if [[ "$TOOL_PULL_IMAGES" == "true" ]]; then
  echo "Pulling images from Docker Hub for: ${SERVICES[*]}"
  "${COMPOSE_BIN[@]}" "${COMPOSE_ARGS[@]}" pull "${SERVICES[@]}"
fi

UP_ARGS=(up -d)
if [[ "$TOOL_RECREATE_CONTAINERS" == "true" ]]; then
  UP_ARGS+=(--force-recreate)
fi

"${COMPOSE_BIN[@]}" "${COMPOSE_ARGS[@]}" "${UP_ARGS[@]}" "${SERVICES[@]}"

echo ""
echo "Tool servers started via docker compose."
if [[ "$ENABLE_TRELLIS2" == "true" ]]; then
  echo "TRELLIS2:       http://${TRELLIS2_HOST:-0.0.0.0}:${TRELLIS2_PORT:-8001}/health"
fi
if [[ "$ENABLE_RETRIEVAL" == "true" ]]; then
  echo "AssetRetrieval: http://${RETRIEVAL_HOST:-0.0.0.0}:${RETRIEVAL_PORT:-8002}/health"
fi
if [[ "$ENABLE_PCG" == "true" ]]; then
  echo "PCGIntegrator:  http://${PCG_HOST:-0.0.0.0}:${PCG_PORT:-8003}/health"
fi
echo ""
echo "Use ${SCRIPT_DIR}/stop_tool_servers.sh to stop all services."
