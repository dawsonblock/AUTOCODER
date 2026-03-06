from control_plane.smt_oracle import Z3EquivalenceChecker


def test_smt_oracle_detects_non_equivalent_comparison() -> None:
    oracle = Z3EquivalenceChecker()

    assert not oracle.is_semantically_equivalent("x < y", "x <= y")


def test_smt_oracle_flags_equivalent_expression() -> None:
    oracle = Z3EquivalenceChecker()

    assert oracle.is_semantically_equivalent("x < y", "x < y")

