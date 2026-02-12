#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
ENV_EXAMPLE="${SCRIPT_DIR}/.env.example"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.tools.yml"
GPU_COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.tools.gpu.yml"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ENV_EXAMPLE" "$ENV_FILE"
fi

set -a
source "$ENV_FILE"
set +a

: "${TRELLIS2_ENABLE_GPU:=true}"
: "${ENABLE_TRELLIS2:=true}"
: "${ENABLE_RETRIEVAL:=true}"
: "${ENABLE_PCG:=true}"

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
  echo "No services enabled. Nothing to stop." >&2
  exit 0
fi

cd "$SCRIPT_DIR"
"${COMPOSE_BIN[@]}" "${COMPOSE_ARGS[@]}" stop "${SERVICES[@]}" || true
"${COMPOSE_BIN[@]}" "${COMPOSE_ARGS[@]}" rm -f "${SERVICES[@]}" || true

echo "Stopped services: ${SERVICES[*]}"
