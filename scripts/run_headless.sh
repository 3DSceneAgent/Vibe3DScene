#!/bin/bash
# Start API service in headless/local-client mode.
# In headless mode, MCP is started on-demand by session manager.

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

DEFAULT_BLENDER_MODE="headless"
BLENDER_MODE_OVERRIDE=""
API_WORKERS_OVERRIDE=""
API_PID=""
MCP_PID=""

usage() {
    cat <<'EOF'
Usage: ./scripts/run_headless.sh [options]

Options:
  --blender-mode <mode>   Override BLENDER_MODE (local-client | headless)
  --workers <count>       Override API worker count
  -h, --help              Show this help message

Examples:
  ./scripts/run_headless.sh
  ./scripts/run_headless.sh --blender-mode local-client
  ./scripts/run_headless.sh --workers 2
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --blender-mode)
            if [ -z "${2:-}" ]; then
                echo -e "${RED}Missing value for --blender-mode${NC}"
                usage
                exit 1
            fi
            BLENDER_MODE_OVERRIDE="$2"
            shift 2
            ;;
        --workers)
            if [ -z "${2:-}" ]; then
                echo -e "${RED}Missing value for --workers${NC}"
                usage
                exit 1
            fi
            API_WORKERS_OVERRIDE="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            usage
            exit 1
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

if [ -n "$BLENDER_MODE_OVERRIDE" ]; then
    export BLENDER_MODE="$BLENDER_MODE_OVERRIDE"
fi
if [ -z "${BLENDER_MODE:-}" ]; then
    export BLENDER_MODE="$DEFAULT_BLENDER_MODE"
fi
case "$BLENDER_MODE" in
    local-client|headless) ;;
    *)
        echo -e "${RED}Invalid BLENDER_MODE: $BLENDER_MODE (expected local-client or headless)${NC}"
        exit 1
        ;;
esac

if [ -n "$API_WORKERS_OVERRIDE" ]; then
    export API_WORKERS="$API_WORKERS_OVERRIDE"
fi
if [ -z "${API_WORKERS:-}" ]; then
    export API_WORKERS="1"
fi

if [ -z "${BLENDER_HEADLESS_CMD:-}" ]; then
    export BLENDER_HEADLESS_CMD="blender"
fi
if [ -z "${BLENDER_HEADLESS_ARGS:-}" ]; then
    export BLENDER_HEADLESS_ARGS='--background --python scripts/blender_headless_client.py -- --host {host} --port {port}'
fi
if [ -z "${BLENDER_HEADLESS_LOG_DIR:-}" ]; then
    export BLENDER_HEADLESS_LOG_DIR="/tmp/scene_agent_headless_logs"
fi

echo -e "${GREEN}Starting 3D Scene Agent services...${NC}"
echo -e "${YELLOW}Press Ctrl+C to stop all services${NC}"
echo ""
echo -e "${CYAN}Runtime configuration:${NC}"
echo "  BLENDER_MODE=$BLENDER_MODE"
echo "  API_WORKERS=$API_WORKERS"
echo "  BLENDER_HEADLESS_CMD=$BLENDER_HEADLESS_CMD"
echo "  BLENDER_HEADLESS_ARGS=$BLENDER_HEADLESS_ARGS"
echo "  BLENDER_HEADLESS_LOG_DIR=$BLENDER_HEADLESS_LOG_DIR"
echo ""

cleanup() {
    set +e
    trap - SIGINT SIGTERM EXIT
    echo -e "\n${YELLOW}Stopping services...${NC}"

    for pid in "${API_PID:-}" "${MCP_PID:-}"; do
        if [ -n "$pid" ]; then
            pkill -TERM -P "$pid" 2>/dev/null || true
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done

    for pid in "${API_PID:-}" "${MCP_PID:-}"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            for _ in {1..50}; do
                kill -0 "$pid" 2>/dev/null || break
                sleep 0.1
            done
            if kill -0 "$pid" 2>/dev/null; then
                kill -KILL "$pid" 2>/dev/null || true
            fi
        fi
    done

    api_exit="n/a"
    mcp_exit="n/a"
    if [ -n "${API_PID:-}" ]; then
        wait "$API_PID" 2>/dev/null
        api_exit=$?
    fi
    if [ -n "${MCP_PID:-}" ]; then
        wait "$MCP_PID" 2>/dev/null
        mcp_exit=$?
    fi

    echo -e "${GREEN}All services stopped${NC}"
    echo -e "  MCP exit: ${mcp_exit}"
    echo -e "  API exit: ${api_exit}"
    exit 0
}

trap cleanup SIGINT SIGTERM EXIT

if [ "$BLENDER_MODE" = "local-client" ]; then
    echo -e "${GREEN}[1/2] Starting MCP server...${NC}"
    python mcp_server/server.py > /tmp/mcp_server.log 2>&1 &
    MCP_PID=$!
    echo -e "${GREEN}      MCP server started (PID: $MCP_PID)${NC}"
    sleep 4
else
    echo -e "${GREEN}[1/1] Skipping MCP server startup in headless mode${NC}"
    echo -e "${YELLOW}      MCP for headless sessions is started on-demand${NC}"
fi

if [ "$BLENDER_MODE" = "local-client" ]; then
    echo -e "${GREEN}[2/2] Starting API server...${NC}"
else
    echo -e "${GREEN}[1/1] Starting API server...${NC}"
fi
python main.py --mode api --host 0.0.0.0 --port 8000 --workers "$API_WORKERS" &
API_PID=$!
echo -e "${GREEN}      API server started (PID: $API_PID)${NC}"

echo ""
echo -e "${GREEN}✓ Services running${NC}"
if [ -n "${MCP_PID:-}" ]; then
    echo -e "  MCP Server: PID $MCP_PID"
    echo -e "  MCP Log: /tmp/mcp_server.log"
fi
echo -e "  API Server: PID $API_PID (http://0.0.0.0:8000)"
echo ""
echo -e "${YELLOW}Press Ctrl+C to stop all services${NC}"
echo ""

wait
