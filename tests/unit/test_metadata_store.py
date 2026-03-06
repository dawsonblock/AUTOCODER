from control_plane.metadata_store import (
    can_transition_candidate_status,
    can_transition_task_status,
)


def test_task_status_merged_is_terminal() -> None:
    assert can_transition_task_status("merged", "merged")
    assert not can_transition_task_status("merged", "verified")


def test_candidate_status_rejected_policy_blocks_later_verified() -> None:
    assert can_transition_candidate_status("verified", "rejected_policy")
    assert not can_transition_candidate_status("rejected_policy", "verified")


def test_candidate_status_accepted_can_merge_but_not_regress() -> None:
    assert can_transition_candidate_status("accepted", "merged")
    assert not can_transition_candidate_status("accepted", "verified")


def test_candidate_status_merged_is_terminal() -> None:
    assert can_transition_candidate_status("merged", "merged")
    assert not can_transition_candidate_status("merged", "failed")


def test_candidate_status_failed_is_terminal() -> None:
    assert can_transition_candidate_status("failed", "failed")
    assert not can_transition_candidate_status("failed", "queued")
