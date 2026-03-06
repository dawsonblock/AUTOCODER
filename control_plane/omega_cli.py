from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

from control_plane.coverage_analysis import build_coverage_matrix
from control_plane.redis_keys import TREE_INDEX, root_node_id
from control_plane.runtime import build_parser, infer_repo_path, load_runtime

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
    source_path = Path(source_file).resolve()
    test_path = Path(test_file).resolve()
    repo_path = Path(repo).resolve() if repo else infer_repo_path(str(source_path), str(test_path))
    source_relpath = source_path.relative_to(repo_path).as_posix()
    test_relpath = test_path.relative_to(repo_path).as_posix()

    code = source_path.read_text()
    test_code = test_path.read_text()
    repo_snapshot = _snapshot_repo_files(repo_path)

    coverage_matrix, suspicious_lines, failed_count, passed_count, node_ids = build_coverage_matrix(
        str(source_path),
        str(test_path),
        str(repo_path),
        suspicious_line_count=config.budgets.suspicious_line_count,
    )
    if failed_count == 0:
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
            "parent": "",
            "visits": 0,
            "value_sum": 0.0,
            "tests_failed": failed_count,
            "tests_passed": passed_count,
            "is_terminal": "False",
            "expanded": "False",
            "depth": 0,
            "best_reward": 0.0,
            "max_depth_seen": 0,
            "coverage_matrix": json.dumps(coverage_matrix, sort_keys=True),
            "suspicious_lines": json.dumps(suspicious_lines),
            "collected_test_nodes": json.dumps(node_ids),
            "created_at": __import__("time").time(),
        },
    )
    print(f"Seeded task {task_id} with {failed_count} failing test(s); suspicious lines: {suspicious_lines}")
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
