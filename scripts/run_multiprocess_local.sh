#!/bin/bash
# Start multiprocess API workers behind a local nginx gateway (macOS/Linux x86_64).

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

KEEP_ALIVE_AFTER_START="${SCENE_AGENT_KEEP_ALIVE_AFTER_START:-0}"
KEEP_ALIVE_SLEEP_SECONDS="${SCENE_AGENT_KEEP_ALIVE_SLEEP_SECONDS:-300}"

usage() {
    cat <<'EOF'
Usage: scripts/run_multiprocess_local.sh [options]

Options:
  --keep-alive     Keep this script process alive after startup.
  --no-keep-alive  Exit after startup (default behavior).
  -h, --help       Show this help message.

Environment overrides:
  SCENE_AGENT_KEEP_ALIVE_AFTER_START=1
  SCENE_AGENT_KEEP_ALIVE_SLEEP_SECONDS=300
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --keep-alive)
            KEEP_ALIVE_AFTER_START="1"
            ;;
        --no-keep-alive)
            KEEP_ALIVE_AFTER_START="0"
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
    shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

HOST_OS="$(uname -s)"
HOST_ARCH="$(uname -m)"
case "$HOST_OS" in
    Darwin|Linux)
        ;;
    *)
        echo -e "${RED}Unsupported host OS: $HOST_OS${NC}"
        echo "Supported: Darwin and Linux."
        exit 1
        ;;
esac
if [ "$HOST_OS" = "Linux" ] && [ "$HOST_ARCH" != "x86_64" ] && [ "$HOST_ARCH" != "amd64" ]; then
    echo -e "${RED}Unsupported Linux architecture: $HOST_ARCH${NC}"
    echo "This local multiprocess script currently supports Linux x86_64 only."
    exit 1
fi

# Preserve runtime proxy env before loading optional env files.
RUNTIME_HTTP_PROXY="${http_proxy:-${HTTP_PROXY:-}}"
RUNTIME_HTTPS_PROXY="${https_proxy:-${HTTPS_PROXY:-}}"
RUNTIME_ALL_PROXY="${all_proxy:-${ALL_PROXY:-}}"
RUNTIME_NO_PROXY="${no_proxy:-${NO_PROXY:-}}"

ENV_FILE_CANDIDATE="${SCENE_AGENT_ENV_FILE:-}"
if [ -z "$ENV_FILE_CANDIDATE" ]; then
    if [ -f "$PROJECT_DIR/docker/.env.multiprocess" ]; then
        ENV_FILE_CANDIDATE="$PROJECT_DIR/docker/.env.multiprocess"
    elif [ -f "$PROJECT_DIR/.env" ]; then
        ENV_FILE_CANDIDATE="$PROJECT_DIR/.env"
    fi
fi
if [ -n "$ENV_FILE_CANDIDATE" ]; then
    if [ ! -f "$ENV_FILE_CANDIDATE" ]; then
        echo -e "${RED}SCENE_AGENT_ENV_FILE not found: $ENV_FILE_CANDIDATE${NC}"
        exit 1
    fi
    echo -e "${CYAN}Loading env file:${NC} $ENV_FILE_CANDIDATE"
    while IFS= read -r raw_line || [ -n "$raw_line" ]; do
        line="${raw_line#"${raw_line%%[![:space:]]*}"}"
        if [ -z "$line" ]; then
            continue
        fi
        case "$line" in
            \#*) continue ;;
        esac
        if [[ "$line" != *=* ]]; then
            continue
        fi
        key="${line%%=*}"
        value="${line#*=}"
        key="${key%"${key##*[![:space:]]}"}"
        if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
            echo -e "${YELLOW}Skip invalid env key in $ENV_FILE_CANDIDATE: $key${NC}"
            continue
        fi
        value="${value#"${value%%[![:space:]]*}"}"
        if [[ "$value" =~ ^\".*\"$ ]]; then
            value="${value:1:${#value}-2}"
        elif [[ "$value" =~ ^\'.*\'$ ]]; then
            value="${value:1:${#value}-2}"
        fi
        export "$key=$value"
    done < "$ENV_FILE_CANDIDATE"
fi

RUN_DIR="${SCENE_AGENT_RUN_DIR:-/tmp/scene_agent_multiprocess}"
PID_DIR="$RUN_DIR/pids"
LOG_DIR="$RUN_DIR/logs"
NGINX_PREFIX="$RUN_DIR/nginx"
NGINX_CONF="$RUN_DIR/nginx.conf"

GATEWAY_PORT="${GATEWAY_PORT:-8000}"
WORKER_1_PORT="${WORKER_1_PORT:-18001}"
WORKER_2_PORT="${WORKER_2_PORT:-18002}"

mkdir -p "$PID_DIR" "$LOG_DIR" "$NGINX_PREFIX/logs" "$NGINX_PREFIX/temp/client_body" \
         "$NGINX_PREFIX/temp/proxy" "$NGINX_PREFIX/temp/fastcgi" \
         "$NGINX_PREFIX/temp/uwsgi" "$NGINX_PREFIX/temp/scgi"

if [ -z "${REDIS_URL:-}" ]; then
    export REDIS_URL="redis://127.0.0.1:6379/0"
fi
if [ -z "${BLENDER_MODE:-}" ]; then
    export BLENDER_MODE="headless"
fi
if [ -z "${SCENE_AGENT_RESET_REDIS_RUNTIME_ON_START:-}" ]; then
    export SCENE_AGENT_RESET_REDIS_RUNTIME_ON_START="0"
fi
if [ -z "${BLENDER_HEADLESS_CMD:-}" ]; then
    if [ -x "/Applications/Blender.app/Contents/MacOS/Blender" ]; then
        export BLENDER_HEADLESS_CMD="/Applications/Blender.app/Contents/MacOS/Blender"
    else
        export BLENDER_HEADLESS_CMD="blender"
    fi
fi
if [ -z "${BLENDER_HEADLESS_ARGS:-}" ]; then
    export BLENDER_HEADLESS_ARGS='--background --python scripts/blender_headless_client.py -- --host {host} --port {port}'
fi
if [ -z "${SESSION_SHARED_STORAGE_ROOT:-}" ]; then
    export SESSION_SHARED_STORAGE_ROOT="/tmp/scene_agent_sessions"
fi
if [ -z "${SESSION_BLEND_ROOT:-}" ]; then
    export SESSION_BLEND_ROOT="$SESSION_SHARED_STORAGE_ROOT"
fi

normalize_proxy_env() {
    local http_proxy_value="${RUNTIME_HTTP_PROXY:-${http_proxy:-${HTTP_PROXY:-${SCENE_AGENT_HTTP_PROXY:-}}}}"
    local https_proxy_value="${RUNTIME_HTTPS_PROXY:-${https_proxy:-${HTTPS_PROXY:-${SCENE_AGENT_HTTPS_PROXY:-${http_proxy_value:-}}}}}"
    local all_proxy_value="${RUNTIME_ALL_PROXY:-${all_proxy:-${ALL_PROXY:-${SCENE_AGENT_ALL_PROXY:-}}}}"
    local no_proxy_value="${RUNTIME_NO_PROXY:-${no_proxy:-${NO_PROXY:-${SCENE_AGENT_NO_PROXY:-}}}}"

    if [ -n "${http_proxy_value:-}" ]; then
        export HTTP_PROXY="$http_proxy_value"
        export http_proxy="$http_proxy_value"
    fi
    if [ -n "${https_proxy_value:-}" ]; then
        export HTTPS_PROXY="$https_proxy_value"
        export https_proxy="$https_proxy_value"
    fi
    if [ -n "${all_proxy_value:-}" ]; then
        export ALL_PROXY="$all_proxy_value"
        export all_proxy="$all_proxy_value"
    fi
    if [ -n "${no_proxy_value:-}" ]; then
        export NO_PROXY="$no_proxy_value"
        export no_proxy="$no_proxy_value"
    fi
}

ensure_no_proxy_contains_localhost() {
    local current="${NO_PROXY:-${no_proxy:-}}"
    local merged="$current"
    for token in "127.0.0.1" "localhost" "::1"; do
        if [ -z "$merged" ]; then
            merged="$token"
            continue
        fi
        case ",$merged," in
            *",$token,"*) ;;
            *) merged="$merged,$token" ;;
        esac
    done
    export NO_PROXY="$merged"
    export no_proxy="$merged"
}

normalize_proxy_env
ensure_no_proxy_contains_localhost

for cmd in python nginx curl; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo -e "${RED}Missing required command: $cmd${NC}"
        exit 1
    fi
done

resolve_blender_headless_cmd() {
    if [ -x "$BLENDER_HEADLESS_CMD" ]; then
        return 0
    fi
    if command -v "$BLENDER_HEADLESS_CMD" >/dev/null 2>&1; then
        BLENDER_HEADLESS_CMD="$(command -v "$BLENDER_HEADLESS_CMD")"
        export BLENDER_HEADLESS_CMD
        return 0
    fi
    if [ -x "/Applications/Blender.app/Contents/MacOS/Blender" ]; then
        BLENDER_HEADLESS_CMD="/Applications/Blender.app/Contents/MacOS/Blender"
        export BLENDER_HEADLESS_CMD
        return 0
    fi
    return 1
}

if ! resolve_blender_headless_cmd; then
    echo -e "${RED}BLENDER_HEADLESS_CMD is not available: $BLENDER_HEADLESS_CMD${NC}"
    echo "Ensure Blender is installed and available in PATH (for example: 'blender')."
    echo "Or set BLENDER_HEADLESS_CMD to a valid executable path."
    exit 1
fi

wait_http() {
    local url="$1"
    local attempts="${2:-30}"
    local i
    for ((i=1; i<=attempts; i++)); do
        if curl -fsS "$url" >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

STARTUP_COMPLETE="0"
STOP_SCRIPT="$SCRIPT_DIR/stop_multiprocess_local.sh"
cleanup_on_error() {
    local exit_code=$?
    if [ "$exit_code" -ne 0 ] && [ "$STARTUP_COMPLETE" != "1" ]; then
        echo -e "${YELLOW}Startup failed, cleaning up partial processes...${NC}"
        "$STOP_SCRIPT" >/dev/null 2>&1 || true
    fi
    exit "$exit_code"
}
trap cleanup_on_error EXIT

check_stale_pid() {
    local name="$1"
    local pid_file="$PID_DIR/$name.pid"
    if [ ! -f "$pid_file" ]; then
        return 0
    fi
    local pid
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
        echo -e "${RED}$name is already running (pid=$pid). Stop it first:${NC}"
        echo "  $STOP_SCRIPT"
        exit 1
    fi
    rm -f "$pid_file"
}

check_stale_pid "worker-1"
check_stale_pid "worker-2"
check_stale_pid "nginx"
check_stale_pid "redis"

redis_ping_local() {
    python - <<'PY' >/dev/null 2>&1
import socket

s = socket.socket()
s.settimeout(0.5)
try:
    s.connect(("127.0.0.1", 6379))
    s.sendall(b"*1\r\n$4\r\nPING\r\n")
    data = s.recv(64)
    if b"PONG" not in data:
        raise RuntimeError("unexpected redis response")
except Exception:
    raise SystemExit(1)
finally:
    s.close()
PY
}

wait_redis_local() {
    local attempts="${1:-40}"
    local i
    for ((i=1; i<=attempts; i++)); do
        if redis_ping_local; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

case "$REDIS_URL" in
    redis://127.0.0.1:6379/*|redis://localhost:6379/*)
        if redis_ping_local; then
            echo -e "${YELLOW}Using existing local Redis on 127.0.0.1:6379${NC}"
        else
            if ! command -v redis-server >/dev/null 2>&1; then
                echo -e "${RED}Local startup requires redis-server on 127.0.0.1:6379.${NC}"
                echo "Install Redis and rerun."
                exit 1
            fi
            echo -e "${GREEN}Starting local Redis (redis-server)...${NC}"
            redis-server \
                --port 6379 \
                --save "" \
                --appendonly no \
                --daemonize yes \
                --pidfile "$PID_DIR/redis.pid" \
                --logfile "$LOG_DIR/redis.log"
            echo "1" > "$PID_DIR/redis.managed"
            if ! wait_redis_local 40; then
                echo -e "${RED}Local Redis failed health check on 127.0.0.1:6379${NC}"
                exit 1
            fi
        fi
        ;;
    *)
        echo -e "${RED}Local startup requires REDIS_URL to point to local Redis on 127.0.0.1:6379.${NC}"
        echo "Current REDIS_URL: ${REDIS_URL}"
        echo "Set REDIS_URL=redis://127.0.0.1:6379/0 (or localhost equivalent)."
        exit 1
        ;;
esac

cleanup_stale_session_ports() {
    if ! command -v lsof >/dev/null 2>&1; then
        return 0
    fi
    local headless_base="${BLENDER_HEADLESS_BASE_PORT:-9876}"
    local mcp_base="${BLENDER_MCP_BASE_PORT:-9877}"
    local clean_range="${SCENE_AGENT_SESSION_PORT_CLEAN_RANGE:-80}"
    local start_port="$1"
    local end_port="$2"
    local port
    for ((port=start_port; port<=end_port; port++)); do
        while IFS= read -r row; do
            if [ -z "$row" ]; then
                continue
            fi
            cmd="$(echo "$row" | awk '{print $1}')"
            pid="$(echo "$row" | awk '{print $2}')"
            case "$cmd" in
                blender|python|Python)
                    kill -TERM "$pid" >/dev/null 2>&1 || true
                    sleep 0.02
                    if kill -0 "$pid" >/dev/null 2>&1; then
                        kill -KILL "$pid" >/dev/null 2>&1 || true
                    fi
                    ;;
            esac
        done < <(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $1, $2}')
    done
    echo -e "${YELLOW}Cleaned stale session listeners in ports ${start_port}-${end_port}${NC}"
    unset headless_base mcp_base clean_range start_port end_port port cmd pid
}

if [ "${SCENE_AGENT_CLEAN_STALE_SESSION_PROCS:-1}" = "1" ]; then
    headless_base="${BLENDER_HEADLESS_BASE_PORT:-9876}"
    mcp_base="${BLENDER_MCP_BASE_PORT:-9877}"
    clean_range="${SCENE_AGENT_SESSION_PORT_CLEAN_RANGE:-80}"
    cleanup_stale_session_ports "$headless_base" "$((headless_base + clean_range - 1))"
    cleanup_stale_session_ports "$mcp_base" "$((mcp_base + clean_range - 1))"
fi

start_worker() {
    local worker_id="$1"
    local port="$2"
    local log_file="$LOG_DIR/${worker_id}.log"
    local pid_file="$PID_DIR/${worker_id}.pid"

    API_WORKERS=1 \
    API_WORKER_ID="$worker_id" \
    API_WORKER_ADVERTISE_URL="http://127.0.0.1:${port}" \
    REDIS_URL="$REDIS_URL" \
    python main.py --mode api --host 127.0.0.1 --port "$port" --workers 1 \
        >"$log_file" 2>&1 &
    local pid=$!
    echo "$pid" > "$pid_file"
    echo -e "${GREEN}Started ${worker_id} on :${port} (pid=${pid})${NC}"
}

start_worker "worker-1" "$WORKER_1_PORT"
start_worker "worker-2" "$WORKER_2_PORT"

if ! wait_http "http://127.0.0.1:${WORKER_1_PORT}/health" 40; then
    echo -e "${RED}worker-1 health check failed.${NC}"
    exit 1
fi
if ! wait_http "http://127.0.0.1:${WORKER_2_PORT}/health" 40; then
    echo -e "${RED}worker-2 health check failed.${NC}"
    exit 1
fi

cat > "$NGINX_CONF" <<EOF
worker_processes  1;
pid logs/nginx.pid;
error_log logs/error.log info;

events {
    worker_connections 1024;
}

http {
    access_log logs/access.log;
    client_body_temp_path temp/client_body;
    proxy_temp_path temp/proxy;
    fastcgi_temp_path temp/fastcgi;
    uwsgi_temp_path temp/uwsgi;
    scgi_temp_path temp/scgi;

    upstream scene_agent_api {
        least_conn;
        server 127.0.0.1:${WORKER_1_PORT};
        server 127.0.0.1:${WORKER_2_PORT};
        keepalive 64;
    }

    server {
        listen ${GATEWAY_PORT};
        server_name _;
        client_max_body_size 50m;

        location / {
            proxy_pass http://scene_agent_api;
            proxy_http_version 1.1;
            proxy_set_header Host \$host;
            proxy_set_header X-Real-IP \$remote_addr;
            proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
            proxy_set_header Connection "";
            proxy_buffering off;
            proxy_request_buffering off;
            proxy_read_timeout 3600;
            proxy_send_timeout 3600;
            add_header X-Accel-Buffering no;
        }
    }
}
EOF

echo -e "${GREEN}Starting nginx gateway on :${GATEWAY_PORT}...${NC}"
nginx -p "$NGINX_PREFIX/" -c "$NGINX_CONF"

if [ -f "$NGINX_PREFIX/logs/nginx.pid" ]; then
    cp "$NGINX_PREFIX/logs/nginx.pid" "$PID_DIR/nginx.pid"
fi

if ! wait_http "http://127.0.0.1:${GATEWAY_PORT}/health"; then
    echo -e "${RED}Gateway health check failed. Check logs in ${LOG_DIR}.${NC}"
    exit 1
fi

STARTUP_COMPLETE="1"
trap - EXIT

echo ""
echo -e "${GREEN}Multiprocess backend is running.${NC}"
echo -e "${CYAN}Gateway:${NC} http://127.0.0.1:${GATEWAY_PORT}"
echo -e "${CYAN}Worker-1:${NC} http://127.0.0.1:${WORKER_1_PORT}"
echo -e "${CYAN}Worker-2:${NC} http://127.0.0.1:${WORKER_2_PORT}"
echo -e "${CYAN}Blender cmd:${NC} $BLENDER_HEADLESS_CMD"
if [ -n "${HTTP_PROXY:-${http_proxy:-}}" ]; then
    echo -e "${CYAN}HTTP proxy:${NC} ${HTTP_PROXY:-${http_proxy}}"
fi
echo -e "${CYAN}Logs:${NC} ${LOG_DIR}"
echo ""
echo -e "${YELLOW}Stop command:${NC} $STOP_SCRIPT"

if [ "$KEEP_ALIVE_AFTER_START" = "1" ]; then
    echo -e "${YELLOW}Keep-alive mode enabled. Script will stay running to keep the container/session active.${NC}"
    echo -e "${YELLOW}Press Ctrl+C to exit this script. Backend processes will continue until you run:${NC} $STOP_SCRIPT"
    while true; do
        sleep "$KEEP_ALIVE_SLEEP_SECONDS"
    done
fi
