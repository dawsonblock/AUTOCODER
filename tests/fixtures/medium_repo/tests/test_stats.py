from __future__ import annotations

from src.stats import bucket_histogram, failing_count, summarize_scores


def test_summarize_scores_reports_min_and_max_bucket() -> None:
    summary = summarize_scores([50, 70, 85], [20, 55, 86])

    assert summary["min"] == 0
    assert summary["max"] == 3


def test_summarize_scores_counts_top_bucket_entries() -> None:
    summary = summarize_scores([50, 70, 85], [85, 86, 99])

    assert summary["top_bucket_count"] == 3


def test_summarize_scores_preserves_bucket_order() -> None:
    summary = summarize_scores([50, 70, 85], [20, 55, 70, 99])

    assert summary["buckets"] == [0, 1, 2, 3]


def test_failing_count_uses_failure_cutoff_bucket() -> None:
    assert failing_count([50, 70, 85], [20, 50, 69, 99], 70) == 3


def test_bucket_histogram_counts_each_bucket() -> None:
    histogram = bucket_histogram([50, 70, 85], [20, 55, 70, 85, 99])

    assert histogram == {0: 1, 1: 1, 2: 1, 3: 2}


def test_bucket_histogram_handles_repeated_bucket_values() -> None:
    histogram = bucket_histogram([50, 70, 85], [51, 60, 65])

    assert histogram == {1: 3}


def test_bucket_histogram_handles_empty_input() -> None:
    assert bucket_histogram([50, 70, 85], []) == {}
