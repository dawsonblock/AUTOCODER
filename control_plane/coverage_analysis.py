from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from control_plane.fault_localization import CoverageMatrix, FaultLocalizer


@dataclass
class CoverageBuildReport:
    matrix: CoverageMatrix
    suspicious_lines: list[int]
    failed_count: int
    passed_count: int
    node_ids: list[str]
    timings: dict[str, float]


def _pytest_node_ids(test_path: Path, repo_path: Path) -> list[str]:
    rel_test = test_path.relative_to(repo_path) if test_path.is_relative_to(repo_path) else test_path
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(rel_test), "--collect-only", "-q"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    node_ids = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("="):
            continue
        if "::" not in stripped:
            continue
        path_part, _, suffix = stripped.partition("::")
        candidate = Path(path_part)
        if candidate.is_absolute():
            candidate = candidate.resolve()
        elif candidate.exists():
            candidate = candidate.resolve()
        else:
            candidate = (repo_path / candidate).resolve()
        try:
            normalized = candidate.relative_to(repo_path)
        except ValueError:
            normalized = candidate
        node_ids.append(f"{normalized}::{suffix}")
    return node_ids or [str(rel_test)]


def _match_file_key(files: dict[str, dict], source_path: Path, repo_path: Path) -> str | None:
    source_abs = source_path.resolve()
    for key in files:
        candidate = Path(key)
        if not candidate.is_absolute():
            candidate = (repo_path / candidate).resolve()
        if candidate == source_abs:
            return key
    return None


def build_coverage_report(
    source_path: str,
    test_path: str,
    repo_path: str,
    suspicious_line_count: int,
) -> CoverageBuildReport:
    source = Path(source_path).resolve()
    test = Path(test_path).resolve()
    repo = Path(repo_path).resolve()

    started = time.perf_counter()
    node_collect_started = time.perf_counter()
    node_ids = _pytest_node_ids(test, repo)
    node_collect_seconds = time.perf_counter() - node_collect_started
    matrix: CoverageMatrix = {}
    total_passed = 0
    total_failed = 0
    coverage_seconds = 0.0

    for node_id in node_ids:
        node_started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="omega_cov_") as tmp_dir:
            data_file = Path(tmp_dir) / ".coverage"
            report_file = Path(tmp_dir) / "coverage.json"
            run_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "coverage",
                    "run",
                    "--branch",
                    "--data-file",
                    str(data_file),
                    "-m",
                    "pytest",
                    "-q",
                    node_id,
                ],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "coverage",
                    "json",
                    "--data-file",
                    str(data_file),
                    "-o",
                    str(report_file),
                ],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )

            if run_result.returncode == 0:
                total_passed += 1
                bucket = "passed"
            else:
                total_failed += 1
                bucket = "failed"

            if not report_file.exists():
                continue

            report = json.loads(report_file.read_text())
            files = report.get("files", {})
            key = _match_file_key(files, source, repo)
            if not key:
                continue
            for line in files[key].get("executed_lines", []):
                matrix.setdefault(int(line), {"passed": 0, "failed": 0})
                matrix[int(line)][bucket] += 1
        coverage_seconds += time.perf_counter() - node_started

    ranking_started = time.perf_counter()
    suspicious_lines = FaultLocalizer(total_failed, total_passed).get_top_k_lines(
        matrix,
        k=suspicious_line_count,
    )
    ranking_seconds = time.perf_counter() - ranking_started
    return CoverageBuildReport(
        matrix=matrix,
        suspicious_lines=suspicious_lines,
        failed_count=total_failed,
        passed_count=total_passed,
        node_ids=node_ids,
        timings={
            "pytest_collect_seconds": node_collect_seconds,
            "coverage_matrix_seconds": coverage_seconds,
            "suspicious_ranking_seconds": ranking_seconds,
            "coverage_total_seconds": time.perf_counter() - started,
        },
    )


def build_coverage_matrix(
    source_path: str,
    test_path: str,
    repo_path: str,
    suspicious_line_count: int,
) -> tuple[CoverageMatrix, list[int], int, int, list[str]]:
    report = build_coverage_report(
        source_path,
        test_path,
        repo_path,
        suspicious_line_count,
    )
    return report.matrix, report.suspicious_lines, report.failed_count, report.passed_count, report.node_ids
