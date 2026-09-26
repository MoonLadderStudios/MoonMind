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


@pytest.mark.asyncio
async def test_idle_summary_names_the_operational_causes(http_plan):
    """The reported run: 65 budget, 7 lifecycle, 1 prerequisite, 1 cooldown.

    The summary leads with what happened and why, in plain words, instead of
    an undifferentiated reason-code list behind author-scope wording.
    """
    candidates = (
        [_candidate(n, SELF) for n in range(1, 66)]
        + [
            _candidate(n, SELF, labels=[{"name": "status: code-review"}])
            for n in range(66, 73)
        ]
        + [_candidate(73, SELF), _candidate(74, SELF)]
    )
    http_plan.plan = {
        "authenticated_user": {**SELF, "type": "User"},
        "list_payload": candidates,
    }

    async def blockers(issue):
        if issue["number"] != 73:
            return []
        return [
            {
                "source": "prerequisite",
                "repository": REPOSITORY,
                "number": 99,
                "statusKnown": True,
                "done": False,
            }
        ]

    async def reserve(number):
        if number == 74:
            raise ActiveIssueClaimConflict(
                "retry_lineage_blocks_admission",
                evidence={"source": "github_comments", "reasonCode": "cooling_down"},
            )
        raise ActiveIssueClaimConflict(
            "retry_lineage_blocks_admission",
            evidence={
                "source": "github_comments",
                "reasonCode": "budget_exhausted",
                "retry": {
                    "allowance": 3,
                    "unresolvedAttempts": 3,
                    "chargedAttempts": [
                        {
                            "attemptId": f"att-{number}-{index}",
                            "outcome": "in_progress",
                            "activity": "active",
                            "unresolved": True,
                        }
                        for index in range(3)
                    ],
                },
            },
        )

    selected, result = await resolve_issue(
        repository=REPOSITORY,
        query="",
        github_service=_Service(),
        blockers_from_issue=blockers,
        reserve_candidate=reserve,
    )

    assert selected is None
    assert result["disposition"] == "idle"
    evidence = result["searchEvidence"]
    assert evidence["rejectionCounts"] == {
        "budget_exhausted": 65,
        "lifecycle_ineligible": 7,
        "blocked_prerequisite": 1,
        "cooling_down": 1,
    }
    assert evidence["unfinishedAttemptAccounting"] == 65
    assert result["summary"].startswith(
        "Search completed without selecting an issue. 65 matching issues were "
        "blocked by historical attempt limits (65 with attempts that never "
        "recorded an outcome), 7 by lifecycle status, 1 by a prerequisite, and "
        "1 by cooldown."
    )
    # Budget samples carry the charged attempts, even behind label rejections.
    budget = [
        item
        for item in evidence["rejectedCandidates"]
        if item["reasonCode"] == "budget_exhausted"
    ]
    assert budget and budget[0]["claimEvidence"]["retry"]["allowance"] == 3
