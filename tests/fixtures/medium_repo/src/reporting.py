from __future__ import annotations

from src.stats import summarize_scores


def render_summary(cutoffs: list[int], scores: list[int]) -> str:
    summary = summarize_scores(cutoffs, scores)
    buckets = ",".join(str(bucket) for bucket in summary["buckets"])
    return (
        f"buckets={buckets}; "
        f"min={summary['min']}; "
        f"max={summary['max']}; "
        f"top={summary['top_bucket_count']}"
    )
