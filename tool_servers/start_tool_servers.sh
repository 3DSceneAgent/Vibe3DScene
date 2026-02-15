#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
ENV_EXAMPLE="${SCRIPT_DIR}/.env.example"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.tools.yml"
GPU_COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.tools.gpu.yml"
PROXY_COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.tools.proxy.generated.yml"

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
: "${HUGGINGFACE_CACHE_DIR:=./cache/huggingface/hub}"
: "${RETRIEVAL_CACHE_DIR:=./cache/asset-retrieval}"
: "${POSTGRES_DATA_DIR:=./cache/postgres}"

# If host shell has proxy variables, route TRELLIS2 downloads through host:7890.
HOST_HTTP_PROXY="${HTTP_PROXY:-${http_proxy:-}}"
HOST_HTTPS_PROXY="${HTTPS_PROXY:-${https_proxy:-}}"
HOST_NO_PROXY="${NO_PROXY:-${no_proxy:-}}"
TRELLIS2_PROXY_HTTP=""
TRELLIS2_PROXY_HTTPS=""
TRELLIS2_PROXY_NO_PROXY=""
USE_PROXY_OVERRIDE=false
rm -f "$PROXY_COMPOSE_FILE"

if [[ -n "$HOST_HTTP_PROXY" ]]; then
  TRELLIS2_PROXY_HTTP="${TRELLIS2_HTTP_PROXY:-http://host.docker.internal:7890}"
fi
if [[ -n "$HOST_HTTPS_PROXY" ]]; then
  TRELLIS2_PROXY_HTTPS="${TRELLIS2_HTTPS_PROXY:-http://host.docker.internal:7890}"
fi
if [[ -n "$HOST_NO_PROXY" ]]; then
  TRELLIS2_PROXY_NO_PROXY="${TRELLIS2_NO_PROXY:-localhost,127.0.0.1,host.docker.internal,postgres}"
fi

if [[ -n "$TRELLIS2_PROXY_HTTP" || -n "$TRELLIS2_PROXY_HTTPS" || -n "$TRELLIS2_PROXY_NO_PROXY" ]]; then
  USE_PROXY_OVERRIDE=true
  {
    echo "services:"
    echo "  trellis2:"
    echo "    extra_hosts:"
    echo "      - \"host.docker.internal:host-gateway\""
    echo "    environment:"
    if [[ -n "$TRELLIS2_PROXY_HTTP" ]]; then
      echo "      HTTP_PROXY: \"$TRELLIS2_PROXY_HTTP\""
      echo "      http_proxy: \"$TRELLIS2_PROXY_HTTP\""
    fi
    if [[ -n "$TRELLIS2_PROXY_HTTPS" ]]; then
      echo "      HTTPS_PROXY: \"$TRELLIS2_PROXY_HTTPS\""
      echo "      https_proxy: \"$TRELLIS2_PROXY_HTTPS\""
    fi
    if [[ -n "$TRELLIS2_PROXY_NO_PROXY" ]]; then
      echo "      NO_PROXY: \"$TRELLIS2_PROXY_NO_PROXY\""
      echo "      no_proxy: \"$TRELLIS2_PROXY_NO_PROXY\""
    fi
  } > "$PROXY_COMPOSE_FILE"
fi

COMPOSE_ARGS=(-f "$COMPOSE_FILE")
if [[ "$TRELLIS2_ENABLE_GPU" == "true" ]]; then
  COMPOSE_ARGS+=( -f "$GPU_COMPOSE_FILE" )
fi
if [[ "$USE_PROXY_OVERRIDE" == "true" ]]; then
  COMPOSE_ARGS+=( -f "$PROXY_COMPOSE_FILE" )
fi

SERVICES=()
if [[ "$ENABLE_TRELLIS2" == "true" ]]; then
  SERVICES+=(trellis2)
fi
if [[ "$ENABLE_RETRIEVAL" == "true" ]]; then
  SERVICES+=(postgres)
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

if [[ "$ENABLE_TRELLIS2" == "true" || "$ENABLE_RETRIEVAL" == "true" ]]; then
  mkdir -p "$HUGGINGFACE_CACHE_DIR"
fi

if [[ "$ENABLE_RETRIEVAL" == "true" ]]; then
  mkdir -p "$RETRIEVAL_CACHE_DIR"
  mkdir -p "$POSTGRES_DATA_DIR"
fi

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
