from __future__ import annotations

from src.search import percentile_bucket, score_window


def summarize_scores(cutoffs: list[int], scores: list[int]) -> dict[str, object]:
    buckets = score_window(cutoffs, scores)
    return {
        "buckets": buckets,
        "min": min(buckets, default=0),
        "max": max(buckets, default=0),
        "top_bucket_count": buckets.count(len(cutoffs)),
    }


def failing_count(cutoffs: list[int], scores: list[int], failure_cutoff: int) -> int:
    failure_bucket = percentile_bucket(cutoffs, failure_cutoff)
    return sum(1 for bucket in score_window(cutoffs, scores) if bucket <= failure_bucket)


def bucket_histogram(cutoffs: list[int], scores: list[int]) -> dict[int, int]:
    histogram: dict[int, int] = {}
    for bucket in score_window(cutoffs, scores):
        histogram[bucket] = histogram.get(bucket, 0) + 1
    return histogram
