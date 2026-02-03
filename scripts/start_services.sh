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

# Trap to ensure both processes are killed on exit
cleanup() {
    echo -e "\n${YELLOW}Stopping services...${NC}"
    # Kill all child processes
    pkill -P $$ 2>/dev/null || true
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
sleep 2

# Start API server in background
echo -e "${GREEN}[2/2] Starting API server...${NC}"
python main.py --mode api --host 0.0.0.0 --port 8000 &
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
