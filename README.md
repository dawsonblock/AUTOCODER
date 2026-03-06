<p align="center">
  <img src="https://img.shields.io/badge/version-19.0.0-blue?style=for-the-badge" alt="Version" />
  <img src="https://img.shields.io/badge/python-3.11+-3776ab?style=for-the-badge&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/rust-2021-dea584?style=for-the-badge&logo=rust&logoColor=white" alt="Rust" />
  <img src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge" alt="License" />
</p>

# 🧬 Kernel Omega — Autonomous Software Repair Fabric

> **Deterministic, hardware-isolated, cryptographically-signed code repair.**  
> Kernel Omega explores a search tree of candidate patches, proves semantic non-equivalence via Z3 SMT, executes tests inside Firecracker micro-VMs, and merges only policy-compliant fixes — all without human intervention.

---

## ✨ Highlights

| Capability | Detail |
|---|---|
| **Tree Search** | Redis-backed UCB tree search over deterministic candidate mutations |
| **Z3 SMT Oracle** | Prunes semantically-equivalent candidates before execution |
| **AST Patching** | Targeted, syntax-aware comparison-operator mutations |
| **Fault Localization** | Spectrum-based suspicious-line ranking from coverage matrices |
| **Hardware Isolation** | Firecracker micro-VMs via Rust hypervisor on Linux KVM |
| **Cryptographic Signing** | Ed25519 patch signatures with policy-gate verification |
| **Content-Addressable Store** | MinIO-backed CAS for deduplicating code snapshots |
| **Live Dashboard** | React + Vite telemetry UI with real-time search-tree metrics |

---

## 🏗️ Architecture

```
                  ┌──────────────────────────────────────────────┐
                  │              CONTROL  PLANE  (Python)        │
                  │                                              │
  omega fix ──────▶  CLI  ──▶  Global Memory (Redis)            │
                  │                    │                         │
                  │            ┌───────┴────────┐               │
                  │            ▼                ▼               │
                  │      Forest Worker       Policy Gate         │
                  │   (UCB + Z3 prune)      (deny rules)        │
                  │            │                │               │
                  │            ▼                ▼               │
                  │       Dispatcher      Merger ◀─ Signature   │
                  │            │                                 │
                  │            │                                │
                  └────────────┼────────────────────────────────┘
                               │  TaskCapsule via Redis stream + TCP
                  ┌────────────▼────────────────────────────────┐
                  │         DATA  PLANE  (Rust / Firecracker)   │
                  │                                              │
                  │   Omega Hypervisor ──▶ Firecracker µVM      │
                  │         │                    │               │
                  │         ▼                    ▼               │
                  │    Guest Agent ────▶ Verification Funnel     │
                  │    (vsock)           (pytest inside VM)      │
                  └──────────────────────────────────────────────┘
```

---

## 📂 Repository Layout

```
.
├── control_plane/           # Python search & orchestration
│   ├── omega_cli.py         #   CLI entry-point (omega fix)
│   ├── forest_worker.py     #   UCB search / expansion loop
│   ├── dispatcher.py        #   Dispatch queue -> executor sockets
│   ├── planning.py          #   Task planning & expansion policy
│   ├── smt_oracle.py        #   Z3 equivalence checker
│   ├── tree_sitter_engine.py#   Python AST-level targeted patching
│   ├── coverage_analysis.py #   Spectrum-based fault localization
│   ├── policy_gate.py       #   Security & diff-budget rules
│   ├── merger.py            #   Auto-merge verified patches
│   ├── global_memory.py     #   Redis-backed shared state
│   ├── metadata_store.py    #   Postgres task/candidate/run ledger
│   ├── simulator_executor.py#   macOS dev-mode executor
│   ├── telemetry_api.py     #   REST API for the dashboard
│   └── signatures.py        #   Ed25519 sign / verify
├── omega_hypervisor/        # Rust Firecracker executor (glommio)
├── guest_agent/             # Rust guest agent (vsock) + funnel.py
├── dashboard/               # React + Vite telemetry UI
├── config/                  # TOML profiles & env templates
│   ├── omega.toml           #   Base configuration
│   ├── dev-macos.toml       #   macOS simulator overrides
│   ├── linux-prod.toml      #   Linux production overrides
│   └── split-brain-mac.toml #   Mac brain + Linux muscle
├── scripts/                 # Bootstrap, snapshot, start/stop
├── telemetry/               # bpftrace kernel-level tracing
├── tests/                   # Unit + integration test suite
├── docs/                    # Additional guides (UTM setup, Linux bring-up)
└── KERNEL/                  # Design documents & specs
```

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.11+**
- **Docker** (for Redis & MinIO)
- **Rust toolchain** (only for the Linux data-plane)

### 1 — macOS Simulator (fastest path)

```bash
# Clone & set up
git clone https://github.com/dawsonblock/AUTOCODER.git
cd AUTOCODER
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Spin up infra
docker compose up -d            # Redis + MinIO + Postgres

# If local port 5432 is already taken:
# OMEGA_POSTGRES_PORT=5433 docker compose up -d

# Launch the executor (simulator mode)
.venv/bin/python -m control_plane.simulator_executor --profile dev-macos
# Copy the printed public key → OMEGA_TRUSTED_PUBKEY

# Launch the control-plane daemons
.venv/bin/python -m control_plane.global_memory   --profile dev-macos
.venv/bin/python -m control_plane.policy_gate     --profile dev-macos
.venv/bin/python -m control_plane.merger          --profile dev-macos
.venv/bin/python -m control_plane.dispatcher      --profile dev-macos
.venv/bin/python -m control_plane.forest_worker   --profile dev-macos

# Seed a repair task
.venv/bin/python -m control_plane.omega_cli fix \
  tests/fixtures/sample_repo/src/algo.py \
  tests/fixtures/sample_repo/tests/test_algo.py \
  --profile dev-macos
```

### 2 — Live Dashboard

```bash
cd dashboard
npm install && npm run dev      # → http://localhost:5173
```

### 3 — Linux Firecracker (production)

> Requires Linux x86_64 with KVM, Firecracker, and `bpftrace`.

```bash
./scripts/bootstrap.sh
./scripts/build_golden_snapshot.sh
cd omega_hypervisor && cargo run --release
# Export the printed key as OMEGA_TRUSTED_PUBKEY
# Then start the Python daemons with --profile linux-prod
```

Before the first real Linux run, use the bring-up runbook:
- [docs/linux-firecracker-bringup.md](/Users/dawsonblock/SANDBOX/THEAUTOCODER/docs/linux-firecracker-bringup.md)

---

## 🔀 Split-Brain Deployment

> **Mac Brain, Linux Muscle** — run the Python control plane on your Mac while Firecracker executes tests on a remote Linux host over Tailscale.

| Host | Runs | Needs |
|---|---|---|
| **macOS** | Control plane, merger, CLI | Python, Redis client, executor pubkey |
| **Linux** | Rust hypervisor, Firecracker µVMs | KVM, Docker, Rust, Tailscale |

```bash
# Linux host
docker compose up -d
./scripts/bootstrap.sh && ./scripts/build_golden_snapshot.sh
./scripts/start_linux_executor.sh --env-file config/env/linux-executor.example.env

# macOS host
./scripts/start_control_plane.sh \
  --env-file config/env/split-brain-mac.example.env \
  --profile split-brain-mac \
  --workers 2
```

See [config/env/](config/env/) for env-file templates.

For Linux host validation and expected Firecracker log signatures, see:
- [docs/linux-firecracker-bringup.md](/Users/dawsonblock/SANDBOX/THEAUTOCODER/docs/linux-firecracker-bringup.md)

---

## ⚙️ Configuration

All settings live in **TOML** files under `config/`.

| Key | Default | Description |
|---|---|---|
| `budgets.max_capsules` | `200` | Enforced total candidate budget per task |
| `budgets.max_depth` | `6` | Max MCTS tree depth |
| `budgets.max_children` | `4` | Candidates generated per expansion |
| `budgets.max_diff_lines` | `10` | Max diff size the merger will accept |
| `policy.deny_edit_tests` | `true` | Block patches that modify test files |
| `policy.deny_disable_asserts` | `true` | Block patches that weaken assertions |
| `policy.require_hardware_isolation` | `true` | Enforce Firecracker execution |
| `metadata.postgres_url` | `postgresql://postgres:postgres@127.0.0.1:5432/omega` | Durable task/candidate/run metadata |

Override any value via profile-specific files (e.g. `dev-macos.toml`).

---

## 🧪 Testing

```bash
# Run the full test suite
pytest

# With coverage
pytest --cov=control_plane --cov-report=term-missing

# Type checking & linting
mypy control_plane/
ruff check .
```

## 📈 Optimization Audit

```bash
# Runs the measured v1 audit against the sample and medium fixtures.
# The runner will use a dedicated Postgres host port automatically if 5432 is busy.
.venv/bin/python scripts/run_optimization_audit.py

# Override the audit database port explicitly if needed
OMEGA_AUDIT_POSTGRES_PORT=5434 .venv/bin/python scripts/run_optimization_audit.py
```

Outputs:
- [tmp/optimization-audit/optimization_audit_report.md](/Users/dawsonblock/SANDBOX/THEAUTOCODER/tmp/optimization-audit/optimization_audit_report.md)
- [tmp/optimization-audit/optimization_audit_report.json](/Users/dawsonblock/SANDBOX/THEAUTOCODER/tmp/optimization-audit/optimization_audit_report.json)

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Search & Orchestration | Python 3.11+, Redis, Monte-Carlo Tree Search |
| Formal Methods | Z3 SMT Solver, tree-sitter |
| Execution Sandbox | Rust, Firecracker, glommio, vsock |
| Content Store | MinIO (S3-compatible) |
| Cryptography | Ed25519 (PyNaCl + ed25519-dalek) |
| Dashboard | React 18, Vite 5, Lucide Icons |
| Observability | bpftrace, custom telemetry API |

---

## 📜 Notes

- V1 repairs a single Python source file at a time, targeting comparison-operator bugs.
- The current control plane uses Redis for hot scheduling state and Postgres for durable task, candidate, execution, and policy metadata.
- Full security guarantees (cryptographic attestation, hardware isolation) only apply to the **linux-prod** profile.
- LLM-guided repair orchestration (Phase 5) is intentionally deferred.
- ARM64 Firecracker support is available but less proven than x86_64 or the split-brain path.

---

<p align="center">
  <sub>Built with determinism, formal verification, and zero trust.</sub>
</p>
