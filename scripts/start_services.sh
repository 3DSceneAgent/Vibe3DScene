#!/bin/bash
# Start both MCP server and API server
# Press Ctrl+C to stop both services

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo -e "${GREEN}Starting 3D Scene Agent services...${NC}"
echo -e "${YELLOW}Press Ctrl+C to stop all services${NC}"
echo ""

# Change to project directory
cd "$PROJECT_DIR"

# Default headless blender command/args if not provided
if [ -z "$BLENDER_HEADLESS_CMD" ]; then
    export BLENDER_HEADLESS_CMD="blender"
fi

if [ -z "$BLENDER_HEADLESS_ARGS" ]; then
    # Use placeholder that won't be interpreted by shell
    export BLENDER_HEADLESS_ARGS='--background --python scripts/blender_headless_client.py -- --host {host} --port {port}'
fi

if [ -z "$BLENDER_HEADLESS_LOG_DIR" ]; then
    export BLENDER_HEADLESS_LOG_DIR="/tmp/scene_agent_headless_logs"
fi

if [ -z "$API_WORKERS" ]; then
    export API_WORKERS="1"
fi

# 验证关键环境变量
echo -e "${CYAN}Environment variables:${NC}"
echo "  BLENDER_HEADLESS_CMD=$BLENDER_HEADLESS_CMD"
echo "  BLENDER_HEADLESS_ARGS=$BLENDER_HEADLESS_ARGS"
echo "  BLENDER_HEADLESS_LOG_DIR=$BLENDER_HEADLESS_LOG_DIR"

# Trap to ensure both processes are killed on exit
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

# Start MCP server in background
echo -e "${GREEN}[1/2] Starting MCP server...${NC}"
python mcp_server/server.py > /tmp/mcp_server.log 2>&1 &
MCP_PID=$!
echo -e "${GREEN}      MCP server started (PID: $MCP_PID)${NC}"

# Wait a bit for MCP server to initialize
sleep 4

# Start API server in background
echo -e "${GREEN}[2/2] Starting API server...${NC}"
python main.py --mode api --host 0.0.0.0 --port 8000 --workers "$API_WORKERS" &
API_PID=$!
echo -e "${GREEN}      API server started (PID: $API_PID)${NC}"

echo ""
echo -e "${GREEN}✓ All services running!${NC}"
echo -e "  MCP Server: PID $MCP_PID"
echo -e "  API Server: PID $API_PID (http://0.0.0.0:8000)"
echo ""
echo -e "${YELLOW}Logs:${NC}"
echo -e "  MCP Server: /tmp/mcp_server.log"
echo ""
echo -e "${YELLOW}Press Ctrl+C to stop all services${NC}"
echo ""

# Wait for both processes
wait
