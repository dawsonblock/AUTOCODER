from __future__ import annotations

from pathlib import Path

from control_plane.coverage_analysis import build_coverage_report


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "sample_repo"


def test_build_coverage_report_returns_timing_breakdown() -> None:
    report = build_coverage_report(
        str(FIXTURE / "src" / "algo.py"),
        str(FIXTURE / "tests" / "test_algo.py"),
        str(FIXTURE),
        suspicious_line_count=5,
    )

    assert report.failed_count >= 1
    assert report.node_ids
    assert report.timings["pytest_collect_seconds"] >= 0
    assert report.timings["coverage_matrix_seconds"] >= 0
    assert report.timings["coverage_total_seconds"] >= report.timings["coverage_matrix_seconds"]
