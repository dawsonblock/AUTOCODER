from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

from control_plane.coverage_analysis import build_coverage_report
from control_plane.metadata_store import MetadataStore
from control_plane.planning import RepairPlanner
from control_plane.redis_keys import TREE_INDEX, root_node_id
from control_plane.runtime import build_parser, infer_repo_path, load_runtime, sha256_text

IGNORED_SNAPSHOT_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "target",
    "tmp",
}
MAX_SNAPSHOT_BYTES = 512 * 1024


def _build_fix_parser() -> argparse.ArgumentParser:
    parser = build_parser("Seed a Kernel Omega repair task.")
    parser.add_argument("source_file")
    parser.add_argument("test_file")
    parser.add_argument("--repo", default=None)
    return parser


def _snapshot_repo_files(repo_path: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(repo_path.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(repo_path)
        if any(part in IGNORED_SNAPSHOT_DIRS for part in relative.parts):
            continue
        if path.is_symlink() or path.stat().st_size > MAX_SNAPSHOT_BYTES:
            continue
        try:
            snapshot[relative.as_posix()] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
    return snapshot


def fix_tests(source_file: str, test_file: str, profile: str, repo: str | None) -> int:
    config, client = load_runtime(profile)
    metadata = MetadataStore(config)
    source_path = Path(source_file).resolve()
    test_path = Path(test_file).resolve()
    repo_path = Path(repo).resolve() if repo else infer_repo_path(str(source_path), str(test_path))
    source_relpath = source_path.relative_to(repo_path).as_posix()
    test_relpath = test_path.relative_to(repo_path).as_posix()

    seed_started = time.perf_counter()
    code = source_path.read_text()
    test_code = test_path.read_text()
    snapshot_started = time.perf_counter()
    repo_snapshot = _snapshot_repo_files(repo_path)
    snapshot_seconds = time.perf_counter() - snapshot_started
    snapshot_bytes = sum(len(value.encode("utf-8")) for value in repo_snapshot.values())
    snapshot_digest = sha256_text(json.dumps(repo_snapshot, sort_keys=True))

    coverage_report = build_coverage_report(
        str(source_path),
        str(test_path),
        str(repo_path),
        suspicious_line_count=config.budgets.suspicious_line_count,
    )
    task_plan = RepairPlanner.build_task_plan(
        coverage_report.matrix,
        coverage_report.suspicious_lines,
        failed_count=coverage_report.failed_count,
        passed_count=coverage_report.passed_count,
        budgets=config.budgets,
    )
    if coverage_report.failed_count == 0:
        print("No failing tests detected. Refusing to seed a solved task.", file=sys.stderr)
        return 1

    task_id = f"task_{uuid4().hex[:8]}"
    root_id = root_node_id(task_id)
    client.sadd(TREE_INDEX, task_id)
    client.hset(
        root_id,
        mapping={
            "tree_id": task_id,
            "code": code,
            "test_code": test_code,
            "source_path": str(source_path),
            "test_path": str(test_path),
            "source_relpath": source_relpath,
            "test_relpath": test_relpath,
            "repo_path": str(repo_path),
            "repo_snapshot": json.dumps(repo_snapshot, sort_keys=True),
            "repo_snapshot_digest": snapshot_digest,
            "repo_snapshot_bytes": snapshot_bytes,
            "repo_snapshot_files": len(repo_snapshot),
            "parent": "",
            "visits": 0,
            "value_sum": 0.0,
            "tests_failed": coverage_report.failed_count,
            "tests_passed": coverage_report.passed_count,
            "is_terminal": "False",
            "expanded": "False",
            "depth": 0,
            "best_reward": 0.0,
            "max_depth_seen": 0,
            "generated_capsules": 0,
            "coverage_matrix": json.dumps(coverage_report.matrix, sort_keys=True),
            "suspicious_lines": json.dumps(coverage_report.suspicious_lines),
            "plan_json": task_plan.to_json(),
            "plan_summary": task_plan.summary,
            "collected_test_nodes": json.dumps(coverage_report.node_ids),
            "created_at": __import__("time").time(),
        },
    )
    metadata.record_task(
        task_id=task_id,
        repo_path=str(repo_path),
        source_path=str(source_path),
        test_path=str(test_path),
        profile=profile,
        objective="repair",
        trigger="manual",
        status="seeded",
        metadata={
            "suspicious_lines": coverage_report.suspicious_lines,
            "coverage_matrix": coverage_report.matrix,
            "collected_test_nodes": coverage_report.node_ids,
            "tests_failed": coverage_report.failed_count,
            "tests_passed": coverage_report.passed_count,
            "task_plan": json.loads(task_plan.to_json()),
            "timings": {
                "repo_snapshot_seconds": snapshot_seconds,
                **coverage_report.timings,
                "seed_total_seconds": time.perf_counter() - seed_started,
            },
            "snapshot": {
                "digest": snapshot_digest,
                "bytes": snapshot_bytes,
                "files": len(repo_snapshot),
            },
        },
    )
    metadata.record_candidate(
        candidate_id=root_id,
        task_id=task_id,
        parent_id=None,
        patch_kind="seed",
        status="seeded",
        source_relpath=source_relpath,
        files_touched=[source_relpath],
        diff_summary="Initial task root candidate",
        static_score=0.0,
        metadata={"is_root": True},
    )
    print(
        f"Seeded task {task_id} with {coverage_report.failed_count} failing test(s); "
        f"suspicious lines: {coverage_report.suspicious_lines}; plan: {task_plan.strategy}"
    )
    return 0


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "fix":
        parser = _build_fix_parser()
        args = parser.parse_args(sys.argv[2:])
        raise SystemExit(fix_tests(args.source_file, args.test_file, args.profile, args.repo))

    parser = argparse.ArgumentParser(description="Kernel Omega CLI")
    parser.add_argument("command", nargs="?")
    parser.print_help()
    raise SystemExit(1)


if __name__ == "__main__":
    main()
