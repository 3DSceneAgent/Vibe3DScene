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
: "${BLENDER_HEADLESS_CMD:=blender}"
: "${BLENDER_HEADLESS_ARGS:=--background --python scripts/blender_headless_client.py -- --host \{host\} --port \{port\}}"
: "${API_WORKERS:=1}"
export BLENDER_HEADLESS_CMD
export BLENDER_HEADLESS_ARGS
export API_WORKERS
echo $BLENDER_HEADLESS_ARGS

# Trap to ensure both processes are killed on exit
cleanup() {
    set +e
    echo -e "\n${YELLOW}Stopping services...${NC}"
    if [ -n "${API_PID:-}" ]; then
        pkill -TERM -P "$API_PID" 2>/dev/null || true
        kill -TERM "$API_PID" 2>/dev/null || true
    fi
    if [ -n "${MCP_PID:-}" ]; then
        pkill -TERM -P "$MCP_PID" 2>/dev/null || true
        kill -TERM "$MCP_PID" 2>/dev/null || true
    fi
    pkill -TERM -P $$ 2>/dev/null || true
    wait
    echo -e "${GREEN}All services stopped${NC}"
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
