from control_plane.tree_sitter_engine import TargetedTreeSitterPatcher


SOURCE = """
def choose(low: int, high: int) -> bool:
    return low < high
""".strip()


def test_patcher_swaps_operator_on_suspicious_line() -> None:
    patcher = TargetedTreeSitterPatcher()

    candidates = patcher.generate_targeted_patches(SOURCE, [2], max_candidates=4)

    assert "low <= high" in candidates[0]

