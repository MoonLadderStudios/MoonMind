"""Explain bounded candidate exhaustion without weakening admission rules."""

# ruff: noqa: F811 -- imported pytest fixture

import pytest

from moonmind.workflows.temporal.github_issue_search import resolve_issue
from moonmind.workflows.temporal.issue_claim_store import ActiveIssueClaimConflict
from tests.unit.workflows.temporal.test_github_issue_search_author_scope_4257 import (
    REPOSITORY,
    SELF,
    _candidate,
    _Service,
    http_plan,  # noqa: F401
)


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "work"])
async def test_late_owner_conflicts_remain_visible_after_many_exclusions(
    http_plan, query
):
    candidates = [
        _candidate(n, SELF, labels=[{"name": "status: in-progress"}])
        for n in range(1, 41)
    ] + [_candidate(41, SELF), _candidate(42, SELF)]
    http_plan.plan = {
        "authenticated_user": {**SELF, "type": "User"},
        "list_payload": candidates,
        "search_payload": {"incomplete_results": False, "items": candidates},
    }

    async def blockers(issue):
        return (
            [
                {
                    "source": "prerequisite",
                    "repository": REPOSITORY,
                    "number": 99,
                    "statusKnown": True,
                    "done": False,
                }
            ]
            if issue["number"] == 41
            else []
        )

    async def reserve(number):
        assert number == 42
        raise ActiveIssueClaimConflict(
            "active_attempt_conflict: reserved",
            evidence={
                "source": "local_claim",
                "owner": "default/" + "owner" * 100,
            },
        )

    selected, result = await resolve_issue(
        repository=REPOSITORY,
        query=query,
        github_service=_Service(),
        blockers_from_issue=blockers,
        reserve_candidate=reserve,
    )
    assert selected is None
    assert result["reasonCode"] == "unresolved_issue_attempts"
    assert "#42" in result["summary"]
    evidence = result["searchEvidence"]
    # No reconciliation hook is supplied here, so the advisory labels report
    # exactly that rather than an ownership verdict they cannot support.
    assert evidence["rejectionCounts"] == {
        "stale_status_label_unreconciled": 40,
        "blocked_prerequisite": 1,
        "active_attempt_conflict": 1,
    }
    assert len(evidence["rejectedCandidates"]) == 20
    assert evidence["rejectedCandidatesTruncated"]
    assert (
        evidence["rejectedCandidates"][-1]["claimEvidence"]["owner"]
        == "default/" + "owner" * 100
    )
    assert "selectedIssueAuthor" not in evidence


@pytest.mark.asyncio
async def test_claim_read_failure_is_not_downgraded_to_idle(http_plan):
    http_plan.plan = {
        "authenticated_user": {**SELF, "type": "User"},
        "list_payload": [_candidate(1, SELF)],
    }

    async def blockers(issue):
        return []

    async def reserve(number):
        raise ValueError("read_failure: comments could not be read")

    with pytest.raises(ValueError, match="read_failure"):
        await resolve_issue(
            repository=REPOSITORY,
            query="",
            github_service=_Service(),
            blockers_from_issue=blockers,
            reserve_candidate=reserve,
        )
