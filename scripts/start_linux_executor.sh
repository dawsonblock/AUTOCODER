#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "start_linux_executor.sh is Linux-only." >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${OMEGA_ENV_FILE:-}"
PUBKEY_FILE_ARG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --pubkey-file)
      PUBKEY_FILE_ARG="$2"
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

LOG_DIR="${OMEGA_LOG_DIR:-$ROOT_DIR/tmp/linux-executor}"
PUBKEY_FILE="${PUBKEY_FILE_ARG:-${OMEGA_PUBKEY_FILE:-$HOME/.config/kernel-omega/linux-executor.pub}}"

mkdir -p "$LOG_DIR" "$(dirname "$PUBKEY_FILE")"
LOG_FILE="$LOG_DIR/omega_hypervisor.log"

cd "$ROOT_DIR/omega_hypervisor"
cargo run --release 2>&1 | while IFS= read -r line; do
  printf '%s\n' "$line" | tee -a "$LOG_FILE"
  if [[ "$line" =~ \[PubKey:\ ([0-9a-f]+)\ \] ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}" > "$PUBKEY_FILE"
    echo "[pubkey] wrote $PUBKEY_FILE" | tee -a "$LOG_FILE"
  fi
done
