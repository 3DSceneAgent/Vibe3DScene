#!/usr/bin/env bash
# Clear stale Redis runtime state (session leases, worker presence, reserved ports).

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./scripts/cleanup_redis_runtime.sh [--yes]

Options:
  --yes      Skip interactive confirmation.
  -h, --help Show this help message.

This script removes Redis runtime keys used for headless coordination:
workers, worker session maps, session meta/lease/fence, reserved ports, thread VLM runtime keys.
EOF
}

AUTO_CONFIRM="0"
for arg in "$@"; do
  case "$arg" in
    --yes)
      AUTO_CONFIRM="1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      usage
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

if [ "$AUTO_CONFIRM" != "1" ]; then
  echo "This will clear Redis runtime occupancy/session keys used by Scene Agent."
  echo "It is safe for local recovery, but do not run during active multiprocess traffic."
  read -r -p "Continue? [y/N] " CONFIRM
  case "$CONFIRM" in
    y|Y|yes|YES)
      ;;
    *)
      echo "Aborted."
      exit 0
      ;;
  esac
fi

python - <<'PY'
from __future__ import annotations

import json
import sys

from scene_agent.env import load_project_dotenv
from scene_agent.config import get_settings
from scene_agent.session import get_session_coordinator

load_project_dotenv()
settings = get_settings()
coordinator = get_session_coordinator()

if coordinator.registry is None:
    print(
        "ERROR: Redis registry is unavailable. "
        "Check REDIS_URL/redis connectivity before cleanup.",
        file=sys.stderr,
    )
    sys.exit(2)

result = coordinator.clear_runtime_state()
if result is None:
    print("ERROR: Failed to clear runtime state from Redis.", file=sys.stderr)
    sys.exit(3)

summary = {
    "redis_url": settings.redis_url,
    "redis_key_prefix": settings.redis_key_prefix,
    "deleted_keys": int(result.get("deleted_keys", 0)),
    "matched_patterns": int(result.get("matched_patterns", 0)),
}
print(json.dumps(summary, ensure_ascii=False, indent=2))
PY

