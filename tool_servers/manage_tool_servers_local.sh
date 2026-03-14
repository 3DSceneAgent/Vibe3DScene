#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
ENV_EXAMPLE="${SCRIPT_DIR}/.env.example"

ACTION="${1:-status}"

cd "${SCRIPT_DIR}"

if [[ ! -f "${ENV_FILE}" ]]; then
  cp "${ENV_EXAMPLE}" "${ENV_FILE}"
  echo "Created ${ENV_FILE} from template. Please review values before use."
fi

set -a
source "${ENV_FILE}"
set +a

PID_KEYS=(
  TRELLIS2_PID
  RETRIEVAL_PID
  PCG_PID
  SAM_PID
  SAM3D_PID
  SAM_PUBLIC_PID
)

TRELLIS2_PID=""
RETRIEVAL_PID=""
PCG_PID=""
SAM_PID=""
SAM3D_PID=""
SAM_PUBLIC_PID=""

: "${WAIT_FOR_HEALTH_TIMEOUT_SECONDS:=300}"
: "${ENABLE_TOOL_SERVER_TRELLIS2:=false}"
: "${ENABLE_TOOL_SERVER_RETRIEVAL:=false}"
: "${ENABLE_TOOL_SERVER_PCG:=false}"
: "${ENABLE_TOOL_SERVER_SAM:=false}"

: "${TOOL_SERVER_TRELLIS2_CONDA_ENV:=trellis2}"
: "${TOOL_SERVER_RETRIEVAL_CONDA_ENV:=asset-retrieval}"
: "${TOOL_SERVER_PCG_CONDA_ENV:=pcg}"
: "${TOOL_SERVER_SAM_CONDA_ENV:=sam}"
: "${TOOL_SERVER_SAM3D_CONDA_ENV:=sam3d-objects}"

if [[ -z "${TOOL_SERVER_SAM_PUBLIC_CONDA_ENV:-}" ]]; then
  TOOL_SERVER_SAM_PUBLIC_CONDA_ENV="${TOOL_SERVER_SAM3D_CONDA_ENV}"
fi

: "${TOOL_SERVER_RUN_DIR:=./.run}"
: "${TOOL_SERVER_LOG_DIR:=./.run/logs}"
: "${TOOL_SERVER_PID_FILE:=./.run/tool_servers_local.pid}"
: "${TOOL_SERVER_RETRIEVAL_PROVIDER:=assetretrieval3d}"
: "${TOOL_SERVER_RETRIEVAL_DB_HOST:=127.0.0.1}"
if [[ -z "${TOOL_SERVER_RETRIEVAL_DB_PORT:-}" ]]; then
  TOOL_SERVER_RETRIEVAL_DB_PORT="${DB_PORT:-5432}"
fi

: "${TRELLIS2_HOST:=0.0.0.0}"
: "${TRELLIS2_PORT:=8001}"
: "${RETRIEVAL_HOST:=0.0.0.0}"
: "${RETRIEVAL_PORT:=8002}"
: "${PCG_HOST:=0.0.0.0}"
: "${PCG_PORT:=8003}"

: "${SAM_HTTP_HOST:=0.0.0.0}"
: "${SAM_HTTP_PORT:=8004}"
: "${SAM_INTERNAL_PORT:=8001}"
: "${SAM3D_INTERNAL_PORT:=8002}"

TRELLIS2_DIR="${SCRIPT_DIR}/TRELLIS.2"
RETRIEVAL_DIR="${SCRIPT_DIR}/AssetRetrieval3D"
SCENESMITH_RETRIEVAL_DIR="${SCRIPT_DIR}/SceneSmithRetrieval"
PCG_DIR="${SCRIPT_DIR}/PCGIntegrator3D"
SAM_DIR="${SCRIPT_DIR}/SAMServer"

print_usage() {
  cat <<'EOF'
Usage:
  bash tool_servers/manage_tool_servers_local.sh [start|stop|status]

Commands:
  start    Start enabled local tool servers in the background
  stop     Stop all managed local tool server processes from the pid file
  status   Show current process status from the pid file
EOF
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing command: $1" >&2
    exit 1
  }
}

exec_detached() {
  if command -v setsid >/dev/null 2>&1; then
    exec setsid "$@"
  fi
  if command -v nohup >/dev/null 2>&1; then
    exec nohup "$@" </dev/null
  fi
  exec "$@"
}

resolve_conda_python() {
  local env_name="$1"
  local conda_base
  local candidate

  conda_base="$(conda info --base)"
  if [[ "${env_name}" == "base" ]]; then
    candidate="${conda_base}/bin/python"
  else
    candidate="${conda_base}/envs/${env_name}/bin/python"
  fi

  if [[ ! -x "${candidate}" ]]; then
    echo "Conda python not found for env '${env_name}': ${candidate}" >&2
    return 1
  fi

  printf '%s' "${candidate}"
}

normalized_retrieval_provider() {
  printf '%s' "${TOOL_SERVER_RETRIEVAL_PROVIDER}" | tr '[:upper:]' '[:lower:]'
}

retrieval_service_label() {
  case "$(normalized_retrieval_provider)" in
    assetretrieval3d)
      printf 'AssetRetrieval3D'
      ;;
    scenesmith)
      printf 'SceneSmithRetrieval'
      ;;
    *)
      printf 'Retrieval(%s)' "${TOOL_SERVER_RETRIEVAL_PROVIDER}"
      ;;
  esac
}

require_existing_path() {
  local path="$1"
  local label="$2"
  if [[ -z "${path}" || ! -e "${path}" ]]; then
    echo "${label} does not exist: ${path}" >&2
    return 1
  fi
}

validate_scenesmith_config() {
  local enable_hssd="${SCENESMITH_ENABLE_HSSD:-true}"
  local enable_ambientcg="${SCENESMITH_ENABLE_AMBIENTCG:-false}"

  if [[ "$(normalized_retrieval_provider)" != "scenesmith" ]]; then
    return 0
  fi
  if [[ "${enable_hssd}" != "true" && "${enable_ambientcg}" != "true" ]]; then
    echo "SceneSmith retrieval requires at least one of SCENESMITH_ENABLE_HSSD or SCENESMITH_ENABLE_AMBIENTCG to be true." >&2
    return 1
  fi
  if [[ "${enable_hssd}" == "true" ]]; then
    require_existing_path "${HSSD_DATA_PATH:-}" "HSSD_DATA_PATH" || return 1
    require_existing_path "${HSSD_PREPROCESSED_PATH:-}" "HSSD_PREPROCESSED_PATH" || return 1
    mkdir -p "${HSSD_ARTIFACT_ROOT:-${SCENESMITH_RETRIEVAL_DIR}/artifacts/hssd_service}"
  fi
  if [[ "${enable_ambientcg}" == "true" ]]; then
    require_existing_path "${AMBIENTCG_DATA_PATH:-}" "AMBIENTCG_DATA_PATH" || return 1
    require_existing_path "${AMBIENTCG_EMBEDDINGS_PATH:-}" "AMBIENTCG_EMBEDDINGS_PATH" || return 1
    mkdir -p "${AMBIENTCG_ARTIFACT_ROOT:-${SCENESMITH_RETRIEVAL_DIR}/artifacts/ambientcg_service}"
  fi
}

pid_is_running() {
  local pid="$1"
  [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1
}

read_pid() {
  local key="$1"
  if [[ -f "${TOOL_SERVER_PID_FILE}" ]]; then
    awk -F= -v k="${key}" '$1 == k { print $2 }' "${TOOL_SERVER_PID_FILE}" | tail -n1
  fi
}

set_pid_var() {
  local key="$1"
  local value="$2"

  case "${key}" in
    TRELLIS2_PID) TRELLIS2_PID="${value}" ;;
    RETRIEVAL_PID) RETRIEVAL_PID="${value}" ;;
    PCG_PID) PCG_PID="${value}" ;;
    SAM_PID) SAM_PID="${value}" ;;
    SAM3D_PID) SAM3D_PID="${value}" ;;
    SAM_PUBLIC_PID) SAM_PUBLIC_PID="${value}" ;;
    *)
      echo "Unknown pid key: ${key}" >&2
      exit 1
      ;;
  esac
}

get_pid_var() {
  local key="$1"

  case "${key}" in
    TRELLIS2_PID) printf '%s' "${TRELLIS2_PID}" ;;
    RETRIEVAL_PID) printf '%s' "${RETRIEVAL_PID}" ;;
    PCG_PID) printf '%s' "${PCG_PID}" ;;
    SAM_PID) printf '%s' "${SAM_PID}" ;;
    SAM3D_PID) printf '%s' "${SAM3D_PID}" ;;
    SAM_PUBLIC_PID) printf '%s' "${SAM_PUBLIC_PID}" ;;
    *)
      echo "Unknown pid key: ${key}" >&2
      exit 1
      ;;
  esac
}

clear_pid_state() {
  local key
  for key in "${PID_KEYS[@]}"; do
    set_pid_var "${key}" ""
  done
}

load_pid_state() {
  local key
  for key in "${PID_KEYS[@]}"; do
    set_pid_var "${key}" "$(read_pid "${key}")"
  done
}

write_pid_line() {
  local key="$1"
  local value="$2"

  if [[ -n "${value}" ]]; then
    printf '%s=%s\n' "${key}" "${value}" >> "${TOOL_SERVER_PID_FILE}"
  fi
}

write_pid_file() {
  mkdir -p "$(dirname "${TOOL_SERVER_PID_FILE}")"
  : > "${TOOL_SERVER_PID_FILE}"

  write_pid_line "TRELLIS2_PID" "${TRELLIS2_PID}"
  write_pid_line "RETRIEVAL_PID" "${RETRIEVAL_PID}"
  write_pid_line "PCG_PID" "${PCG_PID}"
  write_pid_line "SAM_PID" "${SAM_PID}"
  write_pid_line "SAM3D_PID" "${SAM3D_PID}"
  write_pid_line "SAM_PUBLIC_PID" "${SAM_PUBLIC_PID}"

  if [[ ! -s "${TOOL_SERVER_PID_FILE}" ]]; then
    rm -f "${TOOL_SERVER_PID_FILE}"
  fi
}

resolve_healthcheck_host() {
  local host="$1"
  if [[ -z "${host}" || "${host}" == "0.0.0.0" || "${host}" == "::" || "${host}" == "[::]" ]]; then
    printf '127.0.0.1'
    return
  fi
  printf '%s' "${host}"
}

wait_for_http_health() {
  local service_name="$1"
  local url="$2"
  local pid="$3"
  local deadline=$((SECONDS + WAIT_FOR_HEALTH_TIMEOUT_SECONDS))

  echo "Waiting for ${service_name} health: ${url}"
  while true; do
    if curl -fsS --max-time 5 "${url}" >/dev/null 2>&1; then
      echo "${service_name} is healthy."
      return 0
    fi

    if [[ -n "${pid}" ]] && ! pid_is_running "${pid}"; then
      echo "${service_name} exited before becoming healthy: ${url}" >&2
      return 1
    fi

    if (( SECONDS >= deadline )); then
      echo "Timed out waiting for ${service_name} health: ${url}" >&2
      return 1
    fi

    sleep 2
  done
}

kill_group_or_pid() {
  local pid="$1"
  if ! pid_is_running "${pid}"; then
    return 0
  fi

  kill -TERM -"${pid}" >/dev/null 2>&1 || kill -TERM "${pid}" >/dev/null 2>&1 || true
}

force_kill_group_or_pid() {
  local pid="$1"
  if ! pid_is_running "${pid}"; then
    return 0
  fi

  kill -KILL -"${pid}" >/dev/null 2>&1 || kill -KILL "${pid}" >/dev/null 2>&1 || true
}

wait_pid_exit() {
  local pid="$1"
  local wait_seconds="${2:-15}"
  local waited=0

  while (( waited < wait_seconds )); do
    if ! pid_is_running "${pid}"; then
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done

  return 1
}

stop_pid_key() {
  local key="$1"
  local pid
  pid="$(get_pid_var "${key}")"

  if [[ -z "${pid}" ]]; then
    return 0
  fi

  if pid_is_running "${pid}"; then
    kill_group_or_pid "${pid}"
    wait_pid_exit "${pid}" 15 || force_kill_group_or_pid "${pid}"
  else
    echo "Removing stale pid for ${key}: ${pid}"
  fi

  set_pid_var "${key}" ""
}

any_running_pids() {
  local key
  local pid

  for key in "${PID_KEYS[@]}"; do
    pid="$(get_pid_var "${key}")"
    if pid_is_running "${pid}"; then
      return 0
    fi
  done

  return 1
}

status_pid_key() {
  local key="$1"
  local label="$2"
  local pid
  pid="$(get_pid_var "${key}")"

  if [[ -z "${pid}" ]]; then
    echo "[status] ${label}: not tracked"
    return
  fi

  if pid_is_running "${pid}"; then
    echo "[status] ${label}: running (pid=${pid})"
  else
    echo "[status] ${label}: stale pid (pid=${pid})"
  fi
}

start_trellis2() {
  local log_file="${TOOL_SERVER_LOG_DIR}/trellis2.log"
  local health_host
  local health_url
  local python_bin

  echo "Starting TRELLIS2 on ${TRELLIS2_HOST}:${TRELLIS2_PORT}"
  python_bin="$(resolve_conda_python "${TOOL_SERVER_TRELLIS2_CONDA_ENV}")" || return 1
  (
    cd "${TRELLIS2_DIR}"
    exec_detached "${python_bin}" -m uvicorn api:app --host "${TRELLIS2_HOST}" --port "${TRELLIS2_PORT}" --workers 1
  ) >> "${log_file}" 2>&1 &
  TRELLIS2_PID="$!"
  write_pid_file

  health_host="$(resolve_healthcheck_host "${TRELLIS2_HOST}")"
  health_url="http://${health_host}:${TRELLIS2_PORT}/health"
  wait_for_http_health "TRELLIS2" "${health_url}" "${TRELLIS2_PID}"
}

start_retrieval() {
  local log_file="${TOOL_SERVER_LOG_DIR}/retrieval.log"
  local health_host
  local health_url
  local provider
  local python_bin

  provider="$(normalized_retrieval_provider)"

  echo "Starting $(retrieval_service_label) on ${RETRIEVAL_HOST}:${RETRIEVAL_PORT}"
  python_bin="$(resolve_conda_python "${TOOL_SERVER_RETRIEVAL_CONDA_ENV}")" || return 1
  case "${provider}" in
    assetretrieval3d)
      (
        cd "${RETRIEVAL_DIR}"
        export BACKEND_HOST="${RETRIEVAL_HOST}"
        export BACKEND_PORT="${RETRIEVAL_PORT}"
        export DB_HOST="${TOOL_SERVER_RETRIEVAL_DB_HOST}"
        export DB_PORT="${TOOL_SERVER_RETRIEVAL_DB_PORT}"
        exec_detached "${python_bin}" -m uvicorn backend.app:app --host "${RETRIEVAL_HOST}" --port "${RETRIEVAL_PORT}" --workers 1
      ) >> "${log_file}" 2>&1 &
      ;;
    scenesmith)
      if [[ ! -d "${SCENESMITH_RETRIEVAL_DIR}" ]]; then
        echo "SceneSmith retrieval directory does not exist: ${SCENESMITH_RETRIEVAL_DIR}" >&2
        return 1
      fi
      validate_scenesmith_config || return 1
      (
        cd "${SCENESMITH_RETRIEVAL_DIR}"
        exec_detached "${python_bin}" -m uvicorn app:create_app --factory --host "${RETRIEVAL_HOST}" --port "${RETRIEVAL_PORT}"
      ) >> "${log_file}" 2>&1 &
      ;;
    *)
      echo "Unsupported TOOL_SERVER_RETRIEVAL_PROVIDER: ${TOOL_SERVER_RETRIEVAL_PROVIDER}" >&2
      return 1
      ;;
  esac
  RETRIEVAL_PID="$!"
  write_pid_file

  health_host="$(resolve_healthcheck_host "${RETRIEVAL_HOST}")"
  health_url="http://${health_host}:${RETRIEVAL_PORT}/health"
  wait_for_http_health "$(retrieval_service_label)" "${health_url}" "${RETRIEVAL_PID}" || return 1

  if [[ "${provider}" == "scenesmith" ]]; then
    if [[ "${SCENESMITH_ENABLE_HSSD:-true}" == "true" ]]; then
      wait_for_http_health \
        "SceneSmith HSSD" \
        "http://${health_host}:${RETRIEVAL_PORT}/hssd/healthz" \
        "${RETRIEVAL_PID}" || return 1
    fi
    if [[ "${SCENESMITH_ENABLE_AMBIENTCG:-false}" == "true" ]]; then
      wait_for_http_health \
        "SceneSmith AmbientCG" \
        "http://${health_host}:${RETRIEVAL_PORT}/ambientcg/healthz" \
        "${RETRIEVAL_PID}" || return 1
    fi
  fi
}

start_pcg() {
  local log_file="${TOOL_SERVER_LOG_DIR}/pcg.log"
  local health_host
  local health_url
  local python_bin

  echo "Starting PCGIntegrator3D on ${PCG_HOST}:${PCG_PORT}"
  python_bin="$(resolve_conda_python "${TOOL_SERVER_PCG_CONDA_ENV}")" || return 1
  (
    cd "${PCG_DIR}"
    exec_detached "${python_bin}" -m uvicorn app.main:app --host "${PCG_HOST}" --port "${PCG_PORT}" --workers 1
  ) >> "${log_file}" 2>&1 &
  PCG_PID="$!"
  write_pid_file

  health_host="$(resolve_healthcheck_host "${PCG_HOST}")"
  health_url="http://${health_host}:${PCG_PORT}/health"
  wait_for_http_health "PCGIntegrator3D" "${health_url}" "${PCG_PID}"
}

start_sam_service() {
  local log_file="${TOOL_SERVER_LOG_DIR}/sam_service.log"
  local python_bin

  echo "Starting SAM internal service on 127.0.0.1:${SAM_INTERNAL_PORT}"
  python_bin="$(resolve_conda_python "${TOOL_SERVER_SAM_CONDA_ENV}")" || return 1
  (
    cd "${SAM_DIR}"
    exec_detached "${python_bin}" -m uvicorn sam3d_server.sam_service:app --host 127.0.0.1 --port "${SAM_INTERNAL_PORT}" --workers 1
  ) >> "${log_file}" 2>&1 &
  SAM_PID="$!"
  write_pid_file

  wait_for_http_health "SAM internal service" "http://127.0.0.1:${SAM_INTERNAL_PORT}/healthz" "${SAM_PID}"
}

start_sam3d_service() {
  local log_file="${TOOL_SERVER_LOG_DIR}/sam3d_service.log"
  local python_bin

  echo "Starting SAM3D internal service on 127.0.0.1:${SAM3D_INTERNAL_PORT}"
  python_bin="$(resolve_conda_python "${TOOL_SERVER_SAM3D_CONDA_ENV}")" || return 1
  (
    cd "${SAM_DIR}"
    exec_detached "${python_bin}" -m uvicorn sam3d_server.sam3d_service:app --host 127.0.0.1 --port "${SAM3D_INTERNAL_PORT}" --workers 1
  ) >> "${log_file}" 2>&1 &
  SAM3D_PID="$!"
  write_pid_file

  wait_for_http_health "SAM3D internal service" "http://127.0.0.1:${SAM3D_INTERNAL_PORT}/healthz" "${SAM3D_PID}"
}

start_sam_public_service() {
  local log_file="${TOOL_SERVER_LOG_DIR}/sam_public.log"
  local health_host
  local health_url
  local python_bin

  echo "Starting SAM public service on ${SAM_HTTP_HOST}:${SAM_HTTP_PORT}"
  python_bin="$(resolve_conda_python "${TOOL_SERVER_SAM_PUBLIC_CONDA_ENV}")" || return 1
  (
    cd "${SAM_DIR}"
    export SAM_INTERNAL_BASE_URL="http://127.0.0.1:${SAM_INTERNAL_PORT}"
    export SAM3D_INTERNAL_BASE_URL="http://127.0.0.1:${SAM3D_INTERNAL_PORT}"
    exec_detached "${python_bin}" -m uvicorn sam3d_server.main_service:app --host "${SAM_HTTP_HOST}" --port "${SAM_HTTP_PORT}" --workers 1
  ) >> "${log_file}" 2>&1 &
  SAM_PUBLIC_PID="$!"
  write_pid_file

  health_host="$(resolve_healthcheck_host "${SAM_HTTP_HOST}")"
  health_url="http://${health_host}:${SAM_HTTP_PORT}/healthz"
  wait_for_http_health "SAM public service" "${health_url}" "${SAM_PUBLIC_PID}"
}

stop_services() {
  load_pid_state

  if [[ ! -f "${TOOL_SERVER_PID_FILE}" ]]; then
    echo "No pid file found. Nothing to stop."
    return 0
  fi

  echo "Stopping managed local tool servers"
  stop_pid_key "SAM_PUBLIC_PID"
  stop_pid_key "SAM3D_PID"
  stop_pid_key "SAM_PID"
  stop_pid_key "PCG_PID"
  stop_pid_key "RETRIEVAL_PID"
  stop_pid_key "TRELLIS2_PID"
  write_pid_file
  echo "All tracked services stopped."
}

status_services() {
  load_pid_state

  echo "[status] pid file: ${TOOL_SERVER_PID_FILE}"
  if [[ -f "${TOOL_SERVER_PID_FILE}" ]]; then
    cat "${TOOL_SERVER_PID_FILE}"
  else
    echo "[status] pid file does not exist"
  fi

  status_pid_key "TRELLIS2_PID" "TRELLIS2"
  status_pid_key "RETRIEVAL_PID" "$(retrieval_service_label)"
  status_pid_key "PCG_PID" "PCGIntegrator3D"
  status_pid_key "SAM_PID" "SAM internal service"
  status_pid_key "SAM3D_PID" "SAM3D internal service"
  status_pid_key "SAM_PUBLIC_PID" "SAM public service"
}

start_services() {
  local enabled_count=0

  require_cmd curl
  require_cmd conda

  if [[ "${ENABLE_TOOL_SERVER_TRELLIS2}" == "true" ]]; then
    enabled_count=$((enabled_count + 1))
  fi
  if [[ "${ENABLE_TOOL_SERVER_RETRIEVAL}" == "true" ]]; then
    enabled_count=$((enabled_count + 1))
  fi
  if [[ "${ENABLE_TOOL_SERVER_PCG}" == "true" ]]; then
    enabled_count=$((enabled_count + 1))
  fi
  if [[ "${ENABLE_TOOL_SERVER_SAM}" == "true" ]]; then
    enabled_count=$((enabled_count + 1))
  fi

  if (( enabled_count == 0 )); then
    echo "No local services enabled. Set at least one ENABLE_TOOL_SERVER_* flag to true." >&2
    exit 1
  fi

  load_pid_state
  if any_running_pids; then
    echo "Managed services appear to be running already. Use stop before starting again." >&2
    status_services
    exit 1
  fi

  if [[ -f "${TOOL_SERVER_PID_FILE}" ]]; then
    echo "Removing stale pid file: ${TOOL_SERVER_PID_FILE}"
    rm -f "${TOOL_SERVER_PID_FILE}"
    clear_pid_state
  fi

  mkdir -p "${TOOL_SERVER_RUN_DIR}" "${TOOL_SERVER_LOG_DIR}" "$(dirname "${TOOL_SERVER_PID_FILE}")"

  if [[ "${ENABLE_TOOL_SERVER_TRELLIS2}" == "true" ]] && ! start_trellis2; then
    stop_services
    exit 1
  fi

  if [[ "${ENABLE_TOOL_SERVER_RETRIEVAL}" == "true" ]] && ! start_retrieval; then
    stop_services
    exit 1
  fi

  if [[ "${ENABLE_TOOL_SERVER_PCG}" == "true" ]] && ! start_pcg; then
    stop_services
    exit 1
  fi

  if [[ "${ENABLE_TOOL_SERVER_SAM}" == "true" ]]; then
    if ! start_sam_service; then
      stop_services
      exit 1
    fi
    if ! start_sam3d_service; then
      stop_services
      exit 1
    fi
    if ! start_sam_public_service; then
      stop_services
      exit 1
    fi
  fi

  echo "Managed local tool servers started successfully."
  status_services
}

case "${ACTION}" in
  start)
    start_services
    ;;
  stop)
    stop_services
    ;;
  status)
    status_services
    ;;
  *)
    print_usage >&2
    exit 1
    ;;
esac
