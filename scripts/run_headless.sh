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

if [ -n "${BLENDER_MODE:-}" ] && [ "$BLENDER_MODE" != "headless" ]; then
    echo "BLENDER_MODE=$BLENDER_MODE is ignored by run_headless.sh; forcing headless"
fi
export BLENDER_MODE="headless"

echo "Starting headless API (workers=1)..."
exec python main.py --mode api --host 0.0.0.0 --workers 1
