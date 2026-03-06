from control_plane.planning import RepairPlanner
from control_plane.settings import BudgetSettings


def _budgets() -> BudgetSettings:
    return BudgetSettings(
        max_capsules=6,
        max_wall_seconds=60,
        max_diff_lines=10,
        max_touched_files=1,
        max_depth=4,
        max_children=4,
        suspicious_line_count=5,
    )


def test_planner_builds_focused_plan_for_single_failure() -> None:
    coverage = {
        4: {"failed": 1, "passed": 0},
        6: {"failed": 1, "passed": 2},
    }

    plan = RepairPlanner.build_task_plan(
        coverage,
        [4, 6],
        failed_count=1,
        passed_count=2,
        budgets=_budgets(),
    )

    assert plan.strategy == "focused"
    assert plan.focus_lines == [4, 6]
    assert plan.ucb_exploration < 1.2


def test_planner_balances_when_multiple_failures_exist() -> None:
    coverage = {
        4: {"failed": 2, "passed": 0},
        6: {"failed": 1, "passed": 2},
        8: {"failed": 1, "passed": 1},
    }

    plan = RepairPlanner.build_task_plan(
        coverage,
        [4, 6, 8],
        failed_count=2,
        passed_count=3,
        budgets=_budgets(),
    )

    assert plan.strategy == "balanced"
    expansion = plan.expansion_plan(depth=1, generated_capsules=1)
    assert expansion.max_candidates <= plan.max_children
    assert expansion.target_lines[:2] == [4, 6]


def test_planner_enforces_remaining_capsule_budget() -> None:
    coverage = {
        4: {"failed": 1, "passed": 0},
    }

    plan = RepairPlanner.build_task_plan(
        coverage,
        [4],
        failed_count=1,
        passed_count=0,
        budgets=_budgets(),
    )

    exhausted = plan.expansion_plan(depth=0, generated_capsules=plan.max_capsules)

    assert exhausted.remaining_capsules == 0
    assert exhausted.max_candidates == 0
