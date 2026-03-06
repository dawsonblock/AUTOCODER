from __future__ import annotations

import math
from typing import Dict, List, Tuple


CoverageMatrix = Dict[int, Dict[str, int]]


class FaultLocalizer:
    def __init__(self, total_failed_tests: int, total_passed_tests: int):
        self.total_failed = total_failed_tests
        self.total_passed = total_passed_tests

    def calculate_ochiai(self, failed_executions: int, passed_executions: int) -> float:
        if self.total_failed == 0 or (failed_executions + passed_executions) == 0:
            return 0.0
        denominator = math.sqrt(self.total_failed * (failed_executions + passed_executions))
        return failed_executions / denominator if denominator > 0 else 0.0

    def rank_suspicious_lines(self, coverage_matrix: CoverageMatrix) -> List[Tuple[int, float]]:
        ranked: list[tuple[int, float]] = []
        for line_num, stats in coverage_matrix.items():
            failed = stats.get("failed", 0)
            if failed == 0:
                continue
            ranked.append((line_num, self.calculate_ochiai(failed, stats.get("passed", 0))))
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked

    def get_top_k_lines(self, coverage_matrix: CoverageMatrix, k: int = 5) -> List[int]:
        return [line for line, _ in self.rank_suspicious_lines(coverage_matrix)[:k]]

