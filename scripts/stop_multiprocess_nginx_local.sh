#!/bin/bash
# Stop multiprocess API workers and nginx started by run_multiprocess_nginx_local.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="${SCENE_AGENT_RUN_DIR:-/tmp/scene_agent_multiprocess}"
PID_DIR="$RUN_DIR/pids"
NGINX_PREFIX="$RUN_DIR/nginx"
NGINX_CONF="$RUN_DIR/nginx.conf"
GATEWAY_PORT="${GATEWAY_PORT:-8000}"
WORKER_1_PORT="${WORKER_1_PORT:-18001}"
WORKER_2_PORT="${WORKER_2_PORT:-18002}"

stop_pid_file() {
    local name="$1"
    local pid_file="$2"
    if [ ! -f "$pid_file" ]; then
        return 0
    fi
    local pid
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
        kill -TERM "$pid" >/dev/null 2>&1 || true
        for _ in {1..40}; do
            if ! kill -0 "$pid" >/dev/null 2>&1; then
                break
            fi
            sleep 0.1
        done
        if kill -0 "$pid" >/dev/null 2>&1; then
            kill -KILL "$pid" >/dev/null 2>&1 || true
        fi
    fi
    rm -f "$pid_file"
    echo "stopped $name"
}

stop_by_listen_port() {
    local name="$1"
    local port="$2"
    if ! command -v lsof >/dev/null 2>&1; then
        return 0
    fi
    local pids
    pids="$(lsof -t -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | tr '\n' ' ' || true)"
    if [ -z "$pids" ]; then
        return 0
    fi
    for pid in $pids; do
        kill -TERM "$pid" >/dev/null 2>&1 || true
    done
    sleep 0.3
    for pid in $pids; do
        if kill -0 "$pid" >/dev/null 2>&1; then
            kill -KILL "$pid" >/dev/null 2>&1 || true
        fi
    done
    echo "stopped $name on port $port"
}

if [ -f "$PID_DIR/nginx.pid" ]; then
    if command -v nginx >/dev/null 2>&1 && [ -f "$NGINX_CONF" ]; then
        nginx -p "$NGINX_PREFIX/" -c "$NGINX_CONF" -s quit >/dev/null 2>&1 || true
    fi
fi
stop_pid_file "nginx" "$PID_DIR/nginx.pid"
stop_pid_file "worker-1" "$PID_DIR/worker-1.pid"
stop_pid_file "worker-2" "$PID_DIR/worker-2.pid"

# Fallback for stale processes that outlive pid files.
stop_by_listen_port "gateway" "$GATEWAY_PORT"
stop_by_listen_port "worker-1" "$WORKER_1_PORT"
stop_by_listen_port "worker-2" "$WORKER_2_PORT"

if [ -f "$PID_DIR/redis.managed" ]; then
    stop_pid_file "redis" "$PID_DIR/redis.pid"
    rm -f "$PID_DIR/redis.managed"
fi

if [ -f "$PID_DIR/redis.docker.managed" ]; then
    redis_container_name="$(cat "$PID_DIR/redis.docker.managed" 2>/dev/null || true)"
    if [ -n "$redis_container_name" ] && command -v docker >/dev/null 2>&1; then
        docker rm -f "$redis_container_name" >/dev/null 2>&1 || true
        echo "stopped redis-docker $redis_container_name"
    fi
    rm -f "$PID_DIR/redis.docker.managed"
fi

echo "done"
