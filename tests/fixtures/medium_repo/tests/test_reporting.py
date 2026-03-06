from __future__ import annotations

from src.reporting import render_summary


def test_render_summary_renders_all_buckets() -> None:
    assert render_summary([50, 70, 85], [20, 55, 90]) == "buckets=0,1,3; min=0; max=3; top=1"


def test_render_summary_handles_exact_cutoff_values() -> None:
    assert render_summary([50, 70, 85], [50, 70, 85]) == "buckets=1,2,3; min=1; max=3; top=1"


def test_render_summary_handles_single_score() -> None:
    assert render_summary([50, 70, 85], [84]) == "buckets=2; min=2; max=2; top=0"


def test_render_summary_handles_low_scores() -> None:
    assert render_summary([50, 70, 85], [10, 20]) == "buckets=0,0; min=0; max=0; top=0"


def test_render_summary_handles_high_scores() -> None:
    assert render_summary([50, 70, 85], [99, 100]) == "buckets=3,3; min=3; max=3; top=2"


def test_render_summary_handles_mixed_scores() -> None:
    assert render_summary([50, 70, 85], [45, 50, 71, 90]) == "buckets=0,1,2,3; min=0; max=3; top=1"


def test_render_summary_handles_empty_list() -> None:
    assert render_summary([50, 70, 85], []) == "buckets=; min=0; max=0; top=0"
