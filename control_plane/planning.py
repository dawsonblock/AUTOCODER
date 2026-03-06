from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from control_plane.fault_localization import CoverageMatrix, FaultLocalizer
from control_plane.settings import BudgetSettings


@dataclass
class ExpansionPlan:
    target_lines: list[int]
    max_candidates: int
    allow_comparison_fallback: bool
    remaining_capsules: int


@dataclass
class TaskPlan:
    strategy: str
    summary: str
    focus_lines: list[int]
    fallback_lines: list[int]
    ucb_exploration: float
    max_children: int
    max_capsules: int
    max_depth: int

    def remaining_capsules(self, generated_capsules: int) -> int:
        return max(self.max_capsules - max(generated_capsules, 0), 0)

    def expansion_plan(self, *, depth: int, generated_capsules: int) -> ExpansionPlan:
        remaining = self.remaining_capsules(generated_capsules)
        if remaining == 0:
            return ExpansionPlan([], 0, False, 0)

        if self.strategy == "focused":
            target_lines = self.focus_lines[: min(len(self.focus_lines), 2 + min(depth, 2))]
            max_candidates = min(self.max_children, 2 if depth == 0 else 3)
        elif self.strategy == "balanced":
            window = min(len(self.focus_lines), max(3, min(len(self.focus_lines), 3 + depth)))
            target_lines = self.focus_lines[:window]
            max_candidates = min(self.max_children, max(2, min(self.max_children, 2 + depth)))
        else:
            window = min(len(self.fallback_lines), max(3, min(len(self.fallback_lines), self.max_children + depth)))
            target_lines = self.fallback_lines[:window]
            max_candidates = self.max_children

        return ExpansionPlan(
            target_lines=target_lines,
            max_candidates=min(max_candidates, remaining),
            allow_comparison_fallback=True,
            remaining_capsules=remaining,
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, value: str | bytes) -> "TaskPlan":
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        return cls(**json.loads(value))


class RepairPlanner:
    @staticmethod
    def build_task_plan(
        coverage_matrix: CoverageMatrix,
        suspicious_lines: list[int],
        *,
        failed_count: int,
        passed_count: int,
        budgets: BudgetSettings,
    ) -> TaskPlan:
        ranked_lines = [
            line
            for line, _ in FaultLocalizer(failed_count, passed_count).rank_suspicious_lines(coverage_matrix)
        ]
        focus_lines = suspicious_lines or ranked_lines[: budgets.suspicious_line_count]
        fallback_lines = ranked_lines or sorted(coverage_matrix)

        if not focus_lines:
            strategy = "fallback"
            ucb_exploration = 1.8
            summary = "Fallback plan: broaden search across all covered comparison lines."
        elif failed_count == 1 and len(focus_lines) <= budgets.suspicious_line_count:
            strategy = "focused"
            ucb_exploration = 1.05
            summary = f"Focused plan: prioritize the top {len(focus_lines)} suspicious lines first."
        else:
            strategy = "balanced"
            ucb_exploration = 1.35
            summary = (
                f"Balanced plan: explore {len(focus_lines)} suspicious lines with controlled widening by depth."
            )

        return TaskPlan(
            strategy=strategy,
            summary=summary,
            focus_lines=focus_lines,
            fallback_lines=fallback_lines,
            ucb_exploration=ucb_exploration,
            max_children=budgets.max_children,
            max_capsules=budgets.max_capsules,
            max_depth=budgets.max_depth,
        )

    @staticmethod
    def legacy_task_plan(suspicious_lines: list[int], budgets: BudgetSettings) -> TaskPlan:
        focus_lines = suspicious_lines[: budgets.suspicious_line_count]
        return TaskPlan(
            strategy="balanced",
            summary="Legacy plan: balanced search over suspicious lines.",
            focus_lines=focus_lines,
            fallback_lines=focus_lines,
            ucb_exploration=1.35,
            max_children=budgets.max_children,
            max_capsules=budgets.max_capsules,
            max_depth=budgets.max_depth,
        )
