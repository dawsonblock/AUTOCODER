#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing Python runtime at $PYTHON_BIN" >&2
  exit 1
fi

echo "[verify] Ruff"
"$ROOT_DIR/.venv/bin/ruff" check "$ROOT_DIR/control_plane" "$ROOT_DIR/tests" "$ROOT_DIR/guest_agent/funnel.py"

echo "[verify] Mypy"
"$ROOT_DIR/.venv/bin/mypy" "$ROOT_DIR/control_plane" "$ROOT_DIR/guest_agent/funnel.py" --ignore-missing-imports

echo "[verify] Pytest"
"$ROOT_DIR/.venv/bin/pytest" -q

if [[ "${OMEGA_RUN_INTEGRATION:-0}" == "1" ]]; then
  echo "[verify] Integration"
  "$ROOT_DIR/.venv/bin/pytest" "$ROOT_DIR/tests/integration/test_simulator_flow.py" -q
fi

echo "[verify] Shell"
bash -n "$ROOT_DIR"/scripts/*.sh

echo "[verify] Python compile"
"$PYTHON_BIN" -m compileall "$ROOT_DIR/control_plane" "$ROOT_DIR/guest_agent/funnel.py" >/dev/null

echo "[verify] Rust guest"
(cd "$ROOT_DIR/guest_agent" && cargo check)

echo "[verify] Rust hypervisor"
(cd "$ROOT_DIR/omega_hypervisor" && cargo check)

echo "[verify] Dashboard"
(cd "$ROOT_DIR/dashboard" && npm run build)

echo "[verify] OK"
