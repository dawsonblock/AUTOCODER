#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
TMP_DIR = ROOT / "tmp" / "optimization-audit"
PYTHON = ROOT / ".venv" / "bin" / "python"
SIMULATOR_KEY_RE = re.compile(r"\[PubKey:\s*([0-9a-f]+)\s*\]")
TASK_ID_RE = re.compile(r"Seeded task (task_[0-9a-f]+)")

if (
    __name__ == "__main__"
    and PYTHON.exists()
    and Path(sys.executable) != PYTHON
    and os.environ.get("OMEGA_AUDIT_BOOTSTRAPPED") != "1"
):
    env = os.environ.copy()
    env["OMEGA_AUDIT_BOOTSTRAPPED"] = "1"
    os.execve(str(PYTHON), [str(PYTHON), __file__, *sys.argv[1:]], env)

import psycopg  # noqa: E402
import redis  # noqa: E402


@dataclass(frozen=True)
class FixtureSpec:
    name: str
    repo_dir: Path
    source_relpath: str
    test_relpath: str


FIXTURES = (
    FixtureSpec(
        name="sample_repo",
        repo_dir=ROOT / "tests" / "fixtures" / "sample_repo",
        source_relpath="src/algo.py",
        test_relpath="tests/test_algo.py",
    ),
    FixtureSpec(
        name="medium_repo",
        repo_dir=ROOT / "tests" / "fixtures" / "medium_repo",
        source_relpath="src/search.py",
        test_relpath="tests/test_search.py",
    ),
)


def run_cmd(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _python() -> str:
    return str(PYTHON if PYTHON.exists() else Path(sys.executable).resolve())


def ensure_postgres_database(database_url: str) -> None:
    parsed = urlparse(database_url)
    db_name = parsed.path.lstrip("/")
    admin_url = postgres_admin_url(database_url)
    with psycopg.connect(admin_url, autocommit=True) as conn, conn.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
        if cursor.fetchone() is None:
            cursor.execute(f'CREATE DATABASE "{db_name}"')


def reset_postgres_database(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn, conn.cursor() as cursor:
        cursor.execute("DROP SCHEMA public CASCADE")
        cursor.execute("CREATE SCHEMA public")


def port_is_available(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def choose_audit_postgres_port(env: dict[str, str]) -> int:
    if env.get("OMEGA_AUDIT_POSTGRES_PORT"):
        return int(env["OMEGA_AUDIT_POSTGRES_PORT"])
    if env.get("OMEGA_AUDIT_POSTGRES_URL"):
        parsed = urlparse(env["OMEGA_AUDIT_POSTGRES_URL"])
        if parsed.port:
            return parsed.port
    preferred = env.get("OMEGA_POSTGRES_PORT")
    if preferred and port_is_available(int(preferred)):
        return int(preferred)
    for candidate in range(5433, 5501):
        if port_is_available(candidate):
            return candidate
    raise RuntimeError("unable to find a free host port for the audit postgres service")


def docker_host_port(container_name: str, container_port: str) -> str | None:
    result = run_cmd(["docker", "inspect", container_name], cwd=ROOT)
    if result.returncode != 0:
        return None
    payload = json.loads(result.stdout)
    ports = payload[0].get("NetworkSettings", {}).get("Ports", {})
    bindings = ports.get(container_port) or []
    if not bindings:
        return None
    return str(bindings[0].get("HostPort", ""))


def postgres_admin_url(database_url: str) -> str:
    parsed = urlparse(database_url)
    return parsed._replace(path="/postgres").geturl()


def ensure_infra(compose_env: dict[str, str]) -> None:
    for service in ("redis", "minio"):
        result = run_cmd(["docker", "compose", "up", "-d", service], cwd=ROOT, env=compose_env)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"docker compose up failed for {service}")

    result = run_cmd(
        ["docker", "compose", "up", "-d", "--force-recreate", "postgres"],
        cwd=ROOT,
        env=compose_env,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "docker compose up failed for postgres")

    expected_port = compose_env["OMEGA_POSTGRES_PORT"]
    actual_port = docker_host_port("omega_postgres", "5432/tcp")
    if actual_port != expected_port:
        raise RuntimeError(
            f"omega_postgres is bound to {actual_port or 'no host port'} instead of expected {expected_port}"
        )


def wait_for_redis(redis_url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    client = redis.Redis.from_url(redis_url, decode_responses=True)
    while time.time() < deadline:
        try:
            if client.ping():
                return
        except Exception:
            time.sleep(0.5)
            continue
    raise TimeoutError(f"Redis did not become ready: {redis_url}")


def wait_for_postgres(database_url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with psycopg.connect(database_url, connect_timeout=2):
                return
        except Exception:
            time.sleep(0.5)
    raise TimeoutError(f"Postgres did not become ready: {database_url}")


def wait_for_http(url: str, timeout: float = 30.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            time.sleep(0.5)
    raise TimeoutError(f"HTTP endpoint did not become ready: {url}")


def stream_to_file(stream, target: Path) -> None:
    with target.open("a", encoding="utf-8") as handle:
        for line in stream:
            handle.write(line)
            handle.flush()


def start_process(name: str, args: list[str], env: dict[str, str]) -> tuple[subprocess.Popen[str], Path]:
    log_path = TMP_DIR / f"{name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    child_env = env.copy()
    child_env.setdefault("PYTHONUNBUFFERED", "1")
    handle = subprocess.Popen(
        args,
        cwd=str(ROOT),
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    return handle, log_path


def start_simulator(env: dict[str, str]) -> tuple[subprocess.Popen[str], Path, str]:
    proc, log_path = start_process(
        "simulator_executor",
        [_python(), "-m", "control_plane.simulator_executor", "--profile", "dev-macos"],
        env,
    )
    pubkey = ""
    deadline = time.time() + 20
    assert proc.stdout is not None
    with log_path.open("w", encoding="utf-8") as handle:
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    raise RuntimeError(f"simulator executor exited early; see {log_path}")
                time.sleep(0.1)
                continue
            handle.write(line)
            handle.flush()
            match = SIMULATOR_KEY_RE.search(line)
            if match:
                pubkey = match.group(1)
                break
    if not pubkey:
        proc.terminate()
        raise TimeoutError(f"simulator executor did not print a pubkey; see {log_path}")
    assert proc.stdout is not None
    thread = threading.Thread(target=stream_to_file, args=(proc.stdout, log_path), daemon=True)
    thread.start()
    return proc, log_path, pubkey


def start_daemons(env: dict[str, str]) -> list[tuple[str, subprocess.Popen[str], Path]]:
    services = [
        ("global_memory", [_python(), "-m", "control_plane.global_memory", "--profile", "dev-macos"]),
        ("policy_gate", [_python(), "-m", "control_plane.policy_gate", "--profile", "dev-macos"]),
        ("merger", [_python(), "-m", "control_plane.merger", "--profile", "dev-macos"]),
        ("dispatcher", [_python(), "-m", "control_plane.dispatcher", "--profile", "dev-macos"]),
        ("telemetry_api", [_python(), "-m", "control_plane.telemetry_api", "--profile", "dev-macos"]),
        ("forest_worker", [_python(), "-m", "control_plane.forest_worker", "--profile", "dev-macos"]),
    ]
    handles: list[tuple[str, subprocess.Popen[str], Path]] = []
    for name, args in services:
        proc, log_path = start_process(name, args, env)
        assert proc.stdout is not None
        thread = threading.Thread(target=stream_to_file, args=(proc.stdout, log_path), daemon=True)
        thread.start()
        time.sleep(0.2)
        if proc.poll() is not None:
            raise RuntimeError(f"{name} exited early; see {log_path}")
        handles.append((name, proc, log_path))
    return handles


def stop_processes(processes: list[tuple[str, subprocess.Popen[str], Path]]) -> None:
    for _, proc, _ in processes:
        if proc.poll() is None:
            proc.terminate()
    deadline = time.time() + 10
    for _, proc, _ in processes:
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        if proc.poll() is None:
            proc.kill()


def seed_task(fixture_repo: Path, source_relpath: str, test_relpath: str, env: dict[str, str]) -> tuple[str, str]:
    source_path = fixture_repo / source_relpath
    test_path = fixture_repo / test_relpath
    result = run_cmd(
        [
            _python(),
            "-m",
            "control_plane.omega_cli",
            "fix",
            str(source_path),
            str(test_path),
            "--repo",
            str(fixture_repo),
            "--profile",
            "dev-macos",
        ],
        cwd=ROOT,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "omega_cli fix failed")
    match = TASK_ID_RE.search(result.stdout)
    if not match:
        raise RuntimeError(f"unable to parse task id from omega_cli output: {result.stdout}")
    return match.group(1), result.stdout.strip()


def wait_for_terminal_task(task_id: str, database_url: str, timeout: float = 120.0) -> tuple[str, dict[str, Any]]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with psycopg.connect(database_url, autocommit=True) as conn, conn.cursor() as cursor:
            cursor.execute("SELECT status, metadata FROM tasks WHERE task_id = %s", (task_id,))
            row = cursor.fetchone()
        if row:
            status, metadata = row
            if status in {"merged", "budget_exhausted", "verified"}:
                return status, metadata or {}
        time.sleep(0.5)
    raise TimeoutError(f"task {task_id} did not reach a terminal status")


def collect_fixture_metrics(task_id: str, name: str, database_url: str, executor_cores: int) -> dict[str, Any]:
    with psycopg.connect(database_url, autocommit=True) as conn, conn.cursor() as cursor:
        cursor.execute("SELECT status, metadata, created_at FROM tasks WHERE task_id = %s", (task_id,))
        task_status, task_metadata, task_created_at = cursor.fetchone()
        cursor.execute(
            """
            SELECT status, runtime, success, tests_passed, tests_failed, artifact_uri, error_signature, meta, started_at, finished_at
            FROM executions
            WHERE task_id = %s
            ORDER BY started_at NULLS FIRST, finished_at NULLS FIRST
            """,
            (task_id,),
        )
        execution_rows = cursor.fetchall()
        cursor.execute(
            """
            SELECT status, metadata
            FROM candidates
            WHERE task_id = %s AND patch_kind <> 'seed'
            """,
            (task_id,),
        )
        candidate_rows = cursor.fetchall()
        cursor.execute(
            """
            SELECT accepted, reasons
            FROM policy_decisions
            WHERE candidate_id IN (SELECT candidate_id FROM candidates WHERE task_id = %s)
            """,
            (task_id,),
        )
        policy_rows = cursor.fetchall()

    task_metadata = task_metadata or {}
    seed_timings = task_metadata.get("timings", {})
    snapshot_meta = task_metadata.get("snapshot", {})
    execution_count = len(execution_rows)
    candidate_count = len(candidate_rows)
    accepted_count = sum(1 for accepted, _ in policy_rows if accepted)
    total_runtime = sum(float(runtime or 0.0) for _, runtime, *_ in execution_rows)
    finished_times = [row[9] for row in execution_rows if row[9] is not None]
    wall_seconds = (
        (max(finished_times) - task_created_at).total_seconds()
        if finished_times
        else max(1.0, float(seed_timings.get("seed_total_seconds", 0.0)))
    )
    queue_waits = []
    materialize_times = []
    full_suite_times = []
    failed_stage_counts: dict[str, int] = {}
    for _, _, _, _, _, _, _, meta, _, _ in execution_rows:
        meta = meta or {}
        dispatch_meta = meta.get("dispatch", {})
        simulator_meta = meta.get("simulator", {})
        funnel_meta = meta.get("funnel", {})
        stage_meta = funnel_meta.get("stages", {})
        if "queue_wait_ms" in dispatch_meta:
            queue_waits.append(float(dispatch_meta["queue_wait_ms"]))
        if "materialize_ms" in simulator_meta:
            materialize_times.append(float(simulator_meta["materialize_ms"]))
        if "full_suite_ms" in stage_meta:
            full_suite_times.append(float(stage_meta["full_suite_ms"]))
        failed_stage = funnel_meta.get("failed_stage")
        if failed_stage:
            failed_stage_counts[failed_stage] = failed_stage_counts.get(failed_stage, 0) + 1

    generation_times = [
        float((metadata or {}).get("generation_seconds", 0.0)) * 1000.0
        for _, metadata in candidate_rows
        if (metadata or {}).get("generation_seconds") is not None
    ]

    hotspot_totals = {
        "seed_ms": float(seed_timings.get("seed_total_seconds", 0.0)) * 1000.0,
        "coverage_ms": float(seed_timings.get("coverage_total_seconds", 0.0)) * 1000.0,
        "generation_ms": sum(generation_times),
        "queue_wait_ms": sum(queue_waits),
        "executor_ms": total_runtime * 1000.0,
        "full_suite_ms": sum(full_suite_times),
        "materialize_ms": sum(materialize_times),
    }
    ranked_hotspots = sorted(hotspot_totals.items(), key=lambda item: item[1], reverse=True)

    return {
        "name": name,
        "task_id": task_id,
        "status": task_status,
        "candidates_generated": candidate_count,
        "executions": execution_count,
        "accepted": accepted_count,
        "wall_seconds": wall_seconds,
        "candidates_per_minute": (candidate_count / wall_seconds) * 60.0 if wall_seconds else 0.0,
        "executions_per_minute": (execution_count / wall_seconds) * 60.0 if wall_seconds else 0.0,
        "executor_utilization": min(1.0, total_runtime / max(wall_seconds * executor_cores, 1.0)),
        "full_suite_runs_per_accepted": (len(full_suite_times) / accepted_count) if accepted_count else 0.0,
        "seed_ms": float(seed_timings.get("seed_total_seconds", 0.0)) * 1000.0,
        "coverage_ms": float(seed_timings.get("coverage_total_seconds", 0.0)) * 1000.0,
        "generation_ms": sum(generation_times) / max(len(generation_times), 1),
        "queue_wait_ms": sum(queue_waits) / max(len(queue_waits), 1),
        "executor_ms": (total_runtime / max(execution_count, 1)) * 1000.0,
        "full_suite_ms": sum(full_suite_times) / max(len(full_suite_times), 1),
        "materialize_ms": sum(materialize_times) / max(len(materialize_times), 1),
        "snapshot_bytes": int(snapshot_meta.get("bytes", 0) or 0),
        "snapshot_files": int(snapshot_meta.get("files", 0) or 0),
        "failed_stage_counts": failed_stage_counts,
        "hotspots": ranked_hotspots[:3],
    }


def build_upgrade_candidates(fixtures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def avg(key: str) -> float:
        return sum(float(item[key]) for item in fixtures) / max(len(fixtures), 1)

    coverage_pressure = avg("coverage_ms")
    full_suite_pressure = avg("full_suite_ms")
    snapshot_pressure = avg("snapshot_bytes")
    queue_pressure = avg("queue_wait_ms")

    candidates: list[dict[str, Any]] = [
        {
            "title": "Cache and parallelize coverage collection",
            "expected_impact": "High",
            "problem": f"Average coverage stage is {coverage_pressure:.1f} ms and happens before every search tree begins.",
            "implementation": "Cache collected pytest node ids and coverage matrices by task input hash, and run per-node coverage in a bounded worker pool.",
            "risk": "Low. Ochiai scoring stays the same; only the collection strategy changes.",
            "rollout": "Ship behind a config flag and compare suspicious-line parity on both fixtures before enabling by default.",
            "score": coverage_pressure,
        },
        {
            "title": "Replace inline repo snapshots with task-level bundles",
            "expected_impact": "High" if snapshot_pressure > 50_000 else "Medium",
            "problem": f"Average repo snapshot payload is {snapshot_pressure:.0f} bytes and is re-materialized for every candidate execution.",
            "implementation": "Store the repo snapshot once per task in CAS and send only a task-level bundle reference plus candidate overlay files.",
            "risk": "Medium. Transport changes touch both simulator and Linux executor materialization.",
            "rollout": "Keep inline snapshots as fallback until bundle mode matches current execution results on both fixtures.",
            "score": snapshot_pressure / 1000.0,
        },
        {
            "title": "Add an impacted-test tier before full-suite execution",
            "expected_impact": "High" if full_suite_pressure > 0 else "Medium",
            "problem": f"Average full-suite stage is {full_suite_pressure:.1f} ms and is the most expensive funnel step once targeted tests pass.",
            "implementation": "Select impacted tests from touched paths and coverage metadata, and run the full suite only for candidates that survive that tier.",
            "risk": "Medium. Must preserve final full-suite validation for accepted candidates.",
            "rollout": "Run the impacted tier for observability first, then allow it to short-circuit only non-winning candidates.",
            "score": full_suite_pressure,
        },
        {
            "title": "Make dispatch capacity-aware",
            "expected_impact": "Medium",
            "problem": f"Average queue wait is {queue_pressure:.1f} ms and current dispatch prefers a single hashed core without load-based fallback.",
            "implementation": "Track per-core inflight slots and reroute to healthy cores when the preferred core is saturated.",
            "risk": "Low. It changes scheduling, not verification or policy semantics.",
            "rollout": "Start with telemetry-only slot tracking, then enable fallback core assignment behind a flag.",
            "score": queue_pressure,
        },
        {
            "title": "Defer Linux warm-resource pooling until measured on Firecracker",
            "expected_impact": "Unknown",
            "problem": "The current audit runs on the simulator path, so clone/restore pressure on real Firecracker is not yet measured.",
            "implementation": "Add the same stage metrics to the Linux executor and rerun this audit on a Linux/KVM host before changing isolation mechanics.",
            "risk": "Medium. Warm-resource reuse can affect cleanup and isolation guarantees.",
            "rollout": "Do not optimize this path until Linux measurements show clone/restore dominating wall time.",
            "score": -1,
        },
    ]
    return sorted(candidates, key=lambda item: item["score"], reverse=True)


def build_report(fixtures: list[dict[str, Any]], telemetry: dict[str, Any], upgrades: list[dict[str, Any]]) -> str:
    lines = [
        "# Kernel Omega Optimization Audit",
        "",
        "## Baseline",
        "",
        "| Fixture | Status | Candidates | Executions | Wall (s) | Cand/min | Exec/min | Utilization |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for fixture in fixtures:
        lines.append(
            f"| {fixture['name']} | {fixture['status']} | {fixture['candidates_generated']} | {fixture['executions']} | "
            f"{fixture['wall_seconds']:.2f} | {fixture['candidates_per_minute']:.1f} | {fixture['executions_per_minute']:.1f} | "
            f"{fixture['executor_utilization'] * 100:.1f}% |"
        )

    lines.extend(
        [
            "",
            "## Hotspots",
            "",
        ]
    )
    for fixture in fixtures:
        lines.append(f"### {fixture['name']}")
        lines.append("")
        lines.append(
            f"- Snapshot: {fixture['snapshot_bytes']} bytes across {fixture['snapshot_files']} files"
        )
        lines.append(
            f"- Stage averages: seed {fixture['seed_ms']:.1f} ms, coverage {fixture['coverage_ms']:.1f} ms, "
            f"generation {fixture['generation_ms']:.1f} ms, queue {fixture['queue_wait_ms']:.1f} ms, "
            f"executor {fixture['executor_ms']:.1f} ms, full suite {fixture['full_suite_ms']:.1f} ms"
        )
        if fixture["failed_stage_counts"]:
            lines.append(f"- Stage dropoff: `{json.dumps(fixture['failed_stage_counts'], sort_keys=True)}`")
        hotspot_summary = ", ".join(f"{name}={value:.1f} ms" for name, value in fixture["hotspots"])
        lines.append(f"- Top measured hotspots: {hotspot_summary}")
        lines.append("")

    lines.extend(
        [
            "## Prioritized Upgrade Backlog",
            "",
        ]
    )
    for upgrade in upgrades:
        lines.append(f"### {upgrade['title']}")
        lines.append("")
        lines.append(f"- Expected impact: {upgrade['expected_impact']}")
        lines.append(f"- Problem: {upgrade['problem']}")
        lines.append(f"- Implementation sketch: {upgrade['implementation']}")
        lines.append(f"- Correctness / trust risk: {upgrade['risk']}")
        lines.append(f"- Rollout / fallback: {upgrade['rollout']}")
        lines.append("")

    lines.extend(
        [
            "## Roadmap",
            "",
            "### Do now",
            "",
            f"- {upgrades[0]['title']}",
            f"- {upgrades[1]['title']}",
            "",
            "### Do next",
            "",
            f"- {upgrades[2]['title']}",
            f"- {upgrades[3]['title']}",
            "",
            "### Defer",
            "",
            f"- {upgrades[-1]['title']}",
            "",
            "## Telemetry Snapshot",
            "",
            "```json",
            json.dumps(telemetry, sort_keys=True, indent=2),
            "```",
        ]
    )
    return "\n".join(lines)


def copy_fixture_repo(fixture: FixtureSpec) -> Path:
    target = TMP_DIR / fixture.name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(fixture.repo_dir, target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Kernel Omega optimization audit.")
    parser.add_argument("--profile", default="dev-macos")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--report", default=str(TMP_DIR / "optimization_audit_report.md"))
    parser.add_argument("--json", dest="json_path", default=str(TMP_DIR / "optimization_audit_report.json"))
    parser.add_argument("--skip-infra", action="store_true")
    args = parser.parse_args()

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["OMEGA_PROFILE"] = args.profile
    env["OMEGA_REDIS_URL"] = env.get("OMEGA_AUDIT_REDIS_URL", "redis://127.0.0.1:6379/15")
    audit_postgres_port = choose_audit_postgres_port(env)
    env["OMEGA_POSTGRES_PORT"] = str(audit_postgres_port)
    env["OMEGA_POSTGRES_URL"] = env.get(
        "OMEGA_AUDIT_POSTGRES_URL",
        f"postgresql://postgres:postgres@127.0.0.1:{audit_postgres_port}/omega_audit",
    )
    env["OMEGA_EXECUTOR_HOST"] = "127.0.0.1"
    env["OMEGA_EXECUTOR_BASE_PORT"] = env.get("OMEGA_AUDIT_EXECUTOR_BASE_PORT", "18100")
    env["OMEGA_EXECUTOR_CORES"] = env.get("OMEGA_AUDIT_EXECUTOR_CORES", "2")
    env["OMEGA_TELEMETRY_PORT"] = env.get("OMEGA_AUDIT_TELEMETRY_PORT", "18787")

    if not args.skip_infra:
        ensure_infra(env)
    wait_for_redis(env["OMEGA_REDIS_URL"])
    wait_for_postgres(postgres_admin_url(env["OMEGA_POSTGRES_URL"]))
    ensure_postgres_database(env["OMEGA_POSTGRES_URL"])
    wait_for_postgres(env["OMEGA_POSTGRES_URL"])
    reset_postgres_database(env["OMEGA_POSTGRES_URL"])
    redis.Redis.from_url(env["OMEGA_REDIS_URL"], decode_responses=True).flushdb()

    simulator_proc, _, pubkey = start_simulator(env)
    env["OMEGA_TRUSTED_PUBKEY"] = pubkey
    daemons = start_daemons(env)
    processes = [("simulator_executor", simulator_proc, TMP_DIR / "simulator_executor.log"), *daemons]

    try:
        telemetry_base = f"http://127.0.0.1:{env['OMEGA_TELEMETRY_PORT']}"
        telemetry = wait_for_http(f"{telemetry_base}/healthz")
        if telemetry.get("ok") is not True:
            raise RuntimeError("telemetry API did not report healthy")

        fixture_reports = []
        for fixture in FIXTURES:
            repo_copy = copy_fixture_repo(fixture)
            task_id, seed_output = seed_task(repo_copy, fixture.source_relpath, fixture.test_relpath, env)
            status, task_metadata = wait_for_terminal_task(task_id, env["OMEGA_POSTGRES_URL"], timeout=args.timeout)
            fixture_metrics = collect_fixture_metrics(
                task_id,
                fixture.name,
                env["OMEGA_POSTGRES_URL"],
                int(env["OMEGA_EXECUTOR_CORES"]),
            )
            fixture_metrics["status"] = status
            fixture_metrics["seed_output"] = seed_output
            fixture_metrics["task_metadata"] = task_metadata
            fixture_reports.append(fixture_metrics)

        dashboard_snapshot = wait_for_http(f"{telemetry_base}/api/dashboard")
        upgrades = build_upgrade_candidates(fixture_reports)
        report_text = build_report(fixture_reports, dashboard_snapshot, upgrades)
        report_payload = {
            "fixtures": fixture_reports,
            "telemetry": dashboard_snapshot,
            "upgrades": upgrades,
        }

        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_text, encoding="utf-8")
        json_path = Path(args.json_path)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(report_payload, sort_keys=True, indent=2), encoding="utf-8")
        print(f"Wrote optimization audit report to {report_path}")
        print(f"Wrote optimization audit JSON to {json_path}")
    finally:
        stop_processes(processes)


if __name__ == "__main__":
    main()
