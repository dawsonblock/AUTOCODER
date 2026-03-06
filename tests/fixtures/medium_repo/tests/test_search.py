from __future__ import annotations

from src.search import percentile_bucket, rightmost_leq, score_window


def test_rightmost_leq_returns_negative_one_when_everything_is_greater() -> None:
    assert rightmost_leq([10, 20, 30], 5) == -1


def test_rightmost_leq_finds_interior_predecessor() -> None:
    assert rightmost_leq([10, 20, 30], 25) == 1


def test_rightmost_leq_finds_exact_match_at_start() -> None:
    assert rightmost_leq([10, 20, 30], 10) == 0


def test_rightmost_leq_finds_exact_match_in_middle() -> None:
    assert rightmost_leq([10, 20, 30, 40], 30) == 2


def test_rightmost_leq_finds_exact_match_at_end() -> None:
    assert rightmost_leq([10, 20, 30, 40], 40) == 3


def test_percentile_bucket_advances_on_exact_cutoff() -> None:
    assert percentile_bucket([50, 70, 85], 70) == 2


def test_percentile_bucket_uses_previous_cutoff_for_interior_value() -> None:
    assert percentile_bucket([50, 70, 85], 83) == 2


def test_score_window_maps_multiple_scores() -> None:
    assert score_window([50, 70, 85], [49, 50, 84, 85]) == [0, 1, 2, 3]
