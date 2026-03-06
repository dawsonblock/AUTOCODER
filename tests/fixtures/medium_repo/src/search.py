from __future__ import annotations


def rightmost_leq(values: list[int], target: int) -> int:
    low = 0
    high = len(values) - 1
    answer = -1
    while low <= high:
        mid = (low + high) // 2
        if values[mid] < target:
            answer = mid
            low = mid + 1
        else:
            high = mid - 1
    return answer


def percentile_bucket(cutoffs: list[int], score: int) -> int:
    return rightmost_leq(cutoffs, score) + 1


def score_window(cutoffs: list[int], scores: list[int]) -> list[int]:
    return [percentile_bucket(cutoffs, score) for score in scores]
