#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${OMEGA_ENV_FILE:-}"
PROFILE_ARG=""
WORKERS_ARG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --profile)
      PROFILE_ARG="$2"
      shift 2
      ;;
    --workers)
      WORKERS_ARG="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -n "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

PROFILE="${PROFILE_ARG:-${OMEGA_PROFILE:-split-brain-mac}}"
WORKERS="${WORKERS_ARG:-${OMEGA_FOREST_WORKERS:-1}}"
LOG_DIR="${OMEGA_LOG_DIR:-$ROOT_DIR/tmp/control-plane}"
STARTUP_COMPLETE=0
STARTED_PID_FILES=()

mkdir -p "$LOG_DIR"

if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
  echo "Missing $ROOT_DIR/.venv/bin/python. Create the virtualenv first." >&2
  exit 1
fi

if [[ -z "${OMEGA_TRUSTED_PUBKEY:-}" && -z "${OMEGA_TRUSTED_PUBKEY_FILE:-}" ]]; then
  echo "Set OMEGA_TRUSTED_PUBKEY or OMEGA_TRUSTED_PUBKEY_FILE before starting the control plane." >&2
  exit 1
fi

if [[ -n "${OMEGA_TRUSTED_PUBKEY_FILE:-}" && ! -f "${OMEGA_TRUSTED_PUBKEY_FILE/#\~/$HOME}" ]]; then
  echo "OMEGA_TRUSTED_PUBKEY_FILE points to a missing file: ${OMEGA_TRUSTED_PUBKEY_FILE}" >&2
  exit 1
fi

cleanup_partial_startup() {
  if [[ "$STARTUP_COMPLETE" == "1" ]]; then
    return
  fi
  for pid_file in "${STARTED_PID_FILES[@]}"; do
    if [[ -f "$pid_file" ]]; then
      pid="$(cat "$pid_file")"
      if kill -0 "$pid" >/dev/null 2>&1; then
        kill "$pid" >/dev/null 2>&1 || true
      fi
      rm -f "$pid_file"
    fi
  done
}

trap cleanup_partial_startup EXIT

start_service() {
  local name="$1"
  shift
  local log_file="$LOG_DIR/$name.log"
  local pid_file="$LOG_DIR/$name.pid"
  if command -v setsid >/dev/null 2>&1; then
    setsid "$@" </dev/null >"$log_file" 2>&1 &
  else
    nohup "$@" </dev/null >"$log_file" 2>&1 &
  fi
  local pid="$!"
  echo "$pid" >"$pid_file"
  sleep 0.2
  if ! kill -0 "$pid" >/dev/null 2>&1; then
    echo "[failed] $name did not stay running" >&2
    if [[ -s "$log_file" ]]; then
      tail -n 20 "$log_file" >&2
    fi
    return 1
  fi
  STARTED_PID_FILES+=("$pid_file")
  echo "[started] $name (pid $pid)"
}

start_service global_memory "$ROOT_DIR/.venv/bin/python" -m control_plane.global_memory --profile "$PROFILE"
start_service policy_gate "$ROOT_DIR/.venv/bin/python" -m control_plane.policy_gate --profile "$PROFILE"
start_service merger "$ROOT_DIR/.venv/bin/python" -m control_plane.merger --profile "$PROFILE"
start_service dispatcher "$ROOT_DIR/.venv/bin/python" -m control_plane.dispatcher --profile "$PROFILE"
start_service telemetry_api "$ROOT_DIR/.venv/bin/python" -m control_plane.telemetry_api --profile "$PROFILE"

for index in $(seq 1 "$WORKERS"); do
  start_service "forest_worker_$index" "$ROOT_DIR/.venv/bin/python" -m control_plane.forest_worker --profile "$PROFILE"
done

STARTUP_COMPLETE=1
echo "Logs: $LOG_DIR"
