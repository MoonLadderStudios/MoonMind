import pytest

from moonmind.pr_resolver_core import (
    ResolverAction,
    ResolverSnapshot,
    ResolverState,
    classify_github_snapshot,
    reduce_resolver_state,
)


def test_unknown_and_degraded_provider_state_fail_closed():
    state = ResolverState()
    for checks in ("unknown", "degraded", "unavailable"):
        decision = reduce_resolver_state(
            previous_state=state,
            snapshot=ResolverSnapshot(mergeable=True, checks=checks, comments="clear"),
        )
        assert decision.action == ResolverAction.WAIT
        assert not decision.merge_eligible


def test_ready_snapshot_is_the_only_merge_path():
    decision = reduce_resolver_state(
        previous_state=ResolverState(),
        snapshot=ResolverSnapshot(mergeable=True, checks="passing", comments="clear"),
    )
    assert decision.action == ResolverAction.ATTEMPT_MERGE
    assert decision.merge_eligible


def test_blockers_select_bounded_remediation():
    decision = reduce_resolver_state(
        previous_state=ResolverState(remediation_attempts=5),
        snapshot=ResolverSnapshot(mergeable=False, checks="passing", comments="clear"),
    )
    assert decision.action == ResolverAction.STOP_MANUAL_REVIEW
    assert decision.reason_code == "attempts_exhausted"


@pytest.mark.parametrize(
    ("snapshot", "classification"),
    [
        ({"pullRequestMerged": True}, "already_merged"),
        ({"pullRequestOpen": False}, "manual_review"),
        ({"blockers": [{"kind": "merge_conflict"}]}, "merge_conflicts"),
        ({"checksComplete": True, "checksPassing": False}, "ci_failures"),
        ({"checksComplete": False}, "ci_running"),
        (
            {"blockers": [{"kind": "automated_review_pending", "summary": "changes requested"}]},
            "actionable_comments",
        ),
        ({"blockers": [{"kind": "automated_review_pending"}]}, "review_grace"),
        ({"ready": True, "blockers": []}, "ready_to_merge"),
        ({"blockers": [{"kind": "provider", "retryable": True}]}, "mergeability_transient"),
        ({"blockers": [{"kind": "policy", "retryable": False}]}, "manual_review"),
    ],
)
def test_shared_host_parity_corpus(snapshot, classification):
    assert classify_github_snapshot(snapshot)["classification"] == classification
