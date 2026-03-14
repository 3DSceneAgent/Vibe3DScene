#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
ENV_EXAMPLE="${SCRIPT_DIR}/.env.example"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.tools.yml"
GPU_COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.tools.gpu.yml"
SAMSERVER_GPU_COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.tools.samserver.gpu.yml"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ENV_EXAMPLE" "$ENV_FILE"
fi

cd "$SCRIPT_DIR"

set -a
source "$ENV_FILE"
set +a

: "${TRELLIS2_ENABLE_GPU:=true}"
: "${SAMSERVER_ENABLE_GPU:=true}"
: "${ENABLE_TRELLIS2:=true}"
: "${ENABLE_RETRIEVAL:=true}"
: "${ENABLE_PCG:=true}"
: "${ENABLE_SAMSERVER:=false}"
: "${TOOL_SERVER_RETRIEVAL_PROVIDER:=assetretrieval3d}"

normalized_retrieval_provider() {
  printf '%s' "${TOOL_SERVER_RETRIEVAL_PROVIDER}" | tr '[:upper:]' '[:lower:]'
}

retrieval_compose_service_name() {
  case "$(normalized_retrieval_provider)" in
    assetretrieval3d)
      printf 'retrieval'
      ;;
    scenesmith)
      printf 'scenesmith_retrieval'
      ;;
    *)
      echo "Unsupported TOOL_SERVER_RETRIEVAL_PROVIDER: ${TOOL_SERVER_RETRIEVAL_PROVIDER}" >&2
      exit 1
      ;;
  esac
}

if docker compose version >/dev/null 2>&1; then
  COMPOSE_BIN=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_BIN=(docker-compose)
else
  echo "Neither 'docker compose' nor 'docker-compose' is available." >&2
  exit 1
fi

COMPOSE_ARGS=(-f "$COMPOSE_FILE")
if [[ "$TRELLIS2_ENABLE_GPU" == "true" ]]; then
  COMPOSE_ARGS+=( -f "$GPU_COMPOSE_FILE" )
fi
if [[ "$SAMSERVER_ENABLE_GPU" == "true" ]]; then
  COMPOSE_ARGS+=( -f "$SAMSERVER_GPU_COMPOSE_FILE" )
fi

SERVICES=()
if [[ "$ENABLE_TRELLIS2" == "true" ]]; then
  SERVICES+=(trellis2)
fi
if [[ "$ENABLE_RETRIEVAL" == "true" ]]; then
  SERVICES+=("$(retrieval_compose_service_name)")
  if [[ "$(normalized_retrieval_provider)" == "assetretrieval3d" ]]; then
    SERVICES+=(postgres)
  fi
fi
if [[ "$ENABLE_PCG" == "true" ]]; then
  SERVICES+=(pcg)
fi
if [[ "$ENABLE_SAMSERVER" == "true" ]]; then
  SERVICES+=(samserver)
fi

if [[ ${#SERVICES[@]} -eq 0 ]]; then
  echo "No services enabled. Nothing to stop." >&2
  exit 0
fi

"${COMPOSE_BIN[@]}" "${COMPOSE_ARGS[@]}" stop "${SERVICES[@]}" || true
"${COMPOSE_BIN[@]}" "${COMPOSE_ARGS[@]}" rm -f "${SERVICES[@]}" || true

echo "Stopped services: ${SERVICES[*]}"
