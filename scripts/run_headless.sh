#!/bin/bash
# Start API service in headless mode (single-worker only).

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: ./scripts/run_headless.sh

This launcher always starts in headless mode with a single API worker.
API_PORT and other settings are resolved by Python from env/.env/defaults.
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    usage
    exit 0
fi

if [ "$#" -ne 0 ]; then
    echo "Unknown argument(s): $*"
    usage
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

dotenv_has_key() {
    local key="$1"
    if [ ! -f ".env" ]; then
        return 1
    fi
    grep -Eq "^[[:space:]]*${key}=" ".env"
}

if [ -n "${BLENDER_MODE:-}" ] && [ "$BLENDER_MODE" != "headless" ]; then
    echo "BLENDER_MODE=$BLENDER_MODE is ignored by run_headless.sh; forcing headless"
fi
export BLENDER_MODE="headless"

# Single-process headless restarts should not inherit stale Redis runtime keys
# (ports/workers/session leases) from previous crashed runs.
if [ -z "${SCENE_AGENT_RESET_REDIS_RUNTIME_ON_START:-}" ]; then
    export SCENE_AGENT_RESET_REDIS_RUNTIME_ON_START="1"
fi
if [ -z "${SESSION_SWEEP_INTERVAL_SECONDS:-}" ]; then
    if ! dotenv_has_key "SESSION_SWEEP_INTERVAL_SECONDS"; then
        export SESSION_SWEEP_INTERVAL_SECONDS="5"
    fi
fi
if [ -z "${SESSION_IDLE_TIMEOUT_SECONDS:-}" ]; then
    # Single-process UX default: release runtime slot after 200 seconds idle.
    # If .env already defines SESSION_IDLE_TIMEOUT_SECONDS, keep .env value.
    if ! dotenv_has_key "SESSION_IDLE_TIMEOUT_SECONDS"; then
        export SESSION_IDLE_TIMEOUT_SECONDS="200"
    fi
fi

echo "Starting headless API (workers=1)..."
exec python main.py --mode api --host 0.0.0.0 --workers 1
