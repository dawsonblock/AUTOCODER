from control_plane.fault_localization import FaultLocalizer


def test_fault_localizer_ranks_failed_lines_first() -> None:
    matrix = {
        4: {"failed": 2, "passed": 0},
        6: {"failed": 1, "passed": 3},
        9: {"failed": 0, "passed": 5},
    }
    localizer = FaultLocalizer(total_failed_tests=2, total_passed_tests=5)

    ranked = localizer.rank_suspicious_lines(matrix)

    assert ranked[0][0] == 4
    assert 9 not in [line for line, _ in ranked]

