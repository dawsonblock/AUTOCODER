#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${OMEGA_LOG_DIR:-$ROOT_DIR/tmp/control-plane}"

if [[ ! -d "$LOG_DIR" ]]; then
  echo "No control-plane log directory found at $LOG_DIR"
  exit 0
fi

shopt -s nullglob
for pid_file in "$LOG_DIR"/*.pid; do
  pid="$(cat "$pid_file")"
  if kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid"
    echo "[stopped] $(basename "$pid_file" .pid) (pid $pid)"
  fi
  rm -f "$pid_file"
done

