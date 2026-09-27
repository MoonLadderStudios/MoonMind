"""Budget rejections explain themselves; an audited reset readmits the issue.

The issue #4503 shape runs through the production search tool: an unlabelled
open issue whose comments hold three expired ``active``/``in_progress``
attempts from three deployments. Selection must say that it was blocked by
historical attempt limits, which attempts it charged, and that none of them
recorded an outcome. Removing a label cannot help (there is none), and no
deployment's sweep can repair evidence it never owned, so the operator's route
is an explicit, scoped, auditable retry reset -- never comment deletion or a
hand-edited counter.
"""

# ruff: noqa: F811 -- imported pytest fixture

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from moonmind.workflows.temporal import story_output_tools as tools
from moonmind.workflows.temporal.github_issue_attempts import (
    OUTCOME_RETRY_RESET,
    AttemptHandoff,
    build_retry_reset_handoff,
    parse_attempt_comment,
    render_attempt_comment,
)
from moonmind.workflows.temporal.github_issue_retry_reset import (
    ACTION_NONE,
    ACTION_REFUSE,
    ACTION_RESET,
    apply_retry_reset,
    inventory_retry_resets,
)
from moonmind.workflows.temporal.issue_claim_store import IssueClaimStore
from tests.unit.workflows.temporal.test_issue_claim_journey import journey  # noqa: F401

_JOURNEY_FIXTURE = journey  # noqa: F841 -- keeps the fixture registration explicit
assert _JOURNEY_FIXTURE is journey

REPO = "example/repo"
ISSUE = 3970
LEASE = timedelta(minutes=30)
ACTOR = {"id": 123, "login": "fixture-owner"}
COLLABORATOR = {"id": 456, "login": "fixture-collaborator"}
NEEDS_ATTENTION = "status: needs-attention"


def _attempt(
    attempt_id: str,
    *,
    deployment: str,
    remaining: int,
    expired_days_ago: float | None,
    predecessor: str = "",
    predecessor_comment_id: str = "",
    **overrides,
) -> AttemptHandoff:
    """An ``active`` attempt; ``expired_days_ago=None`` keeps its lease live."""
    now = datetime.now(UTC)
    expires = (
        now + LEASE
        if expired_days_ago is None
        else now - timedelta(days=expired_days_ago)
    )
    return AttemptHandoff(
        attempt_id=attempt_id,
        deployment_id=deployment,
        repository=REPO,
        issue_number=ISSUE,
        predecessor_attempt_id=predecessor,
        predecessor_comment_id=predecessor_comment_id,
        activity="active",
        outcome="in_progress",
        next_action="continue_implementation",
        retry_allowance=3,
        retry_remaining=remaining,
        lease_renewed_at=(expires - LEASE).isoformat(),
        lease_expires_at=expires.isoformat(),
        **overrides,
    )


def _post(state, handoff: AttemptHandoff, *, user=ACTOR) -> None:
    state["comments"].append(
        {
            "id": len(state["comments"]) + 1,
            "body": render_attempt_comment(handoff),
            "user": dict(user),
            "author_association": "COLLABORATOR",
            "created_at": "2026-09-21T00:00:00Z",
        }
    )


def _stranded_history(state) -> list[str]:
    """Three deployments' attempts, each promoted to active and never finalized."""
    ids = ["att-4503a-aaaa", "att-4503b-bbbb", "att-4503c-cccc"]
    for index, attempt_id in enumerate(ids):
        _post(
            state,
            _attempt(
                attempt_id,
                deployment=f"inst-device-{index}",
                remaining=3 - index,
                expired_days_ago=5 - index * 0.5,
                predecessor=ids[index - 1] if index else "",
                predecessor_comment_id=str(index) if index else "",
            ),
        )
    return ids


def _exhausted_by_recorded_failures(state, *, retry_attention: bool) -> list[str]:
    """Three finalized failures on an issue that now reads Needs attention.

    With ``retry_attention`` the last finalization recorded that it sent the
    issue to attention with no allowance left; otherwise the attention label
    was applied for a reason the retry history does not record.
    """
    ids = [f"att-failed{index}-aaaa" for index in range(3)]
    for index, attempt_id in enumerate(ids):
        exhausted = retry_attention and index == len(ids) - 1
        _post(
            state,
            replace(
                _attempt(
                    attempt_id,
                    deployment="inst-device-a",
                    remaining=3 - index,
                    expired_days_ago=3,
                    predecessor=ids[index - 1] if index else "",
                    predecessor_comment_id=str(index) if index else "",
                ),
                activity="released",
                outcome="failed",
                writers_stopped=True,
                **(
                    {
                        "pending_disposition": "to_needs_attention",
                        "next_action": "obtain_attention",
                        "retry_remaining": 0,
                    }
                    if exhausted
                    else {}
                ),
            ),
        )
    state["labels"] = [NEEDS_ATTENTION]
    return ids


async def _search(service, owner: str):
    return await tools.load_github_issue_preset_brief(
        {"repository": REPO, "issueSearch": ""},
        {"execution_owner": owner},
        github_service_factory=lambda: service,
    )


@pytest.mark.asyncio
async def test_a_budget_rejection_names_the_charged_attempts(journey):
    state, service, _sessions = journey
    ids = _stranded_history(state)

    result = await _search(service, "default/search-blocked")

    assert result.completion_disposition == "idle", result.outputs
    evidence = result.outputs["searchEvidence"]
    assert evidence["rejectionCounts"] == {"budget_exhausted": 1}
    assert evidence["unfinishedAttemptAccounting"] == 1
    sample = evidence["rejectedCandidates"][0]
    assert sample["issueNumber"] == ISSUE
    retry = sample["claimEvidence"]["retry"]
    assert retry["allowance"] == 3
    assert retry["unresolvedAttempts"] == 3
    assert [item["attemptId"] for item in retry["chargedAttempts"]] == ids
    summary = result.outputs["summary"]
    assert summary.startswith("Search completed without selecting an issue.")
    assert "1 matching issue was blocked by historical attempt limits" in summary
    assert "never recorded an outcome" in summary


@pytest.mark.asyncio
async def test_an_audited_reset_readmits_the_issue_and_keeps_every_attempt(journey):
    state, service, sessions = journey
    ids = _stranded_history(state)

    inventory = await inventory_retry_resets(service=service, repository=REPO)
    [plan] = inventory["issues"]
    assert plan["action"] == ACTION_RESET, plan
    assert plan["supersedes"] == ids
    assert plan["retry"]["unresolvedAttempts"] == 3
    # The inventory writes nothing.
    assert len(state["comments"]) == 3

    applied = await apply_retry_reset(
        service=service,
        plan=plan,
        reason="Stranded attempt records; owning deployments' evidence is gone.",
    )

    assert applied["applied"] is True, applied
    assert len(state["comments"]) == 4
    reset = parse_attempt_comment(state["comments"][-1]["body"]).handoff
    assert reset.outcome == OUTCOME_RETRY_RESET
    assert reset.activity == "released"
    assert reset.retry_history == tuple(ids)
    assert reset.predecessor_attempt_id == ids[-1]
    assert "fixture-owner" in reset.reset_authorization
    # Nothing was deleted or rewritten.
    for comment, attempt_id in zip(state["comments"][:3], ids):
        assert parse_attempt_comment(comment["body"]).handoff.outcome == "in_progress"
        assert parse_attempt_comment(comment["body"]).attempt_id == attempt_id

    admitted = await _search(service, "default/after-reset")

    assert admitted.completion_disposition != "idle", admitted.outputs
    receipt = await IssueClaimStore(sessions).get("default/after-reset")
    assert receipt.issue_number == ISSUE
    handoff = parse_attempt_comment(receipt.comment_body).handoff
    assert handoff.retry_remaining == 3
    assert handoff.predecessor_attempt_id == reset.attempt_id


@pytest.mark.asyncio
async def test_a_reset_is_not_repeated_once_recorded(journey):
    state, service, _sessions = journey
    _stranded_history(state)
    [plan] = (await inventory_retry_resets(service=service, repository=REPO))["issues"]
    await apply_retry_reset(service=service, plan=plan, reason="stranded records")

    again = await apply_retry_reset(
        service=service, plan=plan, reason="stranded records"
    )

    assert again["applied"] is False
    assert again["reasonCode"] == "claim_changed"
    assert len(state["comments"]) == 4


@pytest.mark.asyncio
async def test_recorded_outcomes_are_reset_only_when_explicitly_included(journey):
    state, service, _sessions = journey
    previous = ""
    for index in range(3):
        attempt_id = f"att-recorded{index}-aaaa"
        _post(
            state,
            replace(
                _attempt(
                    attempt_id,
                    deployment="inst-device-a",
                    remaining=3 - index,
                    expired_days_ago=3,
                    predecessor=previous,
                    predecessor_comment_id=str(index) if index else "",
                ),
                activity="released",
                outcome="failed",
                writers_stopped=True,
            ),
        )
        previous = attempt_id

    [default_plan] = (await inventory_retry_resets(service=service, repository=REPO))[
        "issues"
    ]
    [included_plan] = (
        await inventory_retry_resets(
            service=service, repository=REPO, include_recorded_outcomes=True
        )
    )["issues"]

    assert default_plan["action"] == ACTION_NONE
    assert default_plan["reasonCode"] == "recorded_outcomes_only"
    assert included_plan["action"] == ACTION_RESET


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["live_reservation", "operator_hold"])
async def test_a_reset_never_overrides_a_live_owner_or_a_hold(journey, blocker):
    state, service, _sessions = journey
    ids = _stranded_history(state)
    if blocker == "live_reservation":
        extra = _attempt(
            "att-4503live-dddd",
            deployment="inst-device-d",
            remaining=1,
            expired_days_ago=None,
            predecessor=ids[-1],
            predecessor_comment_id="3",
        )
    else:
        extra = replace(
            _attempt(
                "att-4503hold-dddd",
                deployment="inst-device-d",
                remaining=1,
                expired_days_ago=1,
                predecessor=ids[-1],
                predecessor_comment_id="3",
            ),
            operator_hold=True,
            operator_hold_reason="paused by operator",
        )
    _post(state, extra)

    [plan] = (await inventory_retry_resets(service=service, repository=REPO))["issues"]

    assert plan["action"] == ACTION_REFUSE
    assert plan["reasonCode"] == blocker
    refused = await apply_retry_reset(
        service=service, plan=plan, reason="stranded records"
    )
    assert refused["applied"] is False
    assert len(state["comments"]) == 4


@pytest.mark.asyncio
async def test_the_operator_command_inventories_by_default_and_applies_on_request(
    journey, monkeypatch, capsys
):
    from moonmind.config.settings import settings
    from moonmind.workflows.adapters import github_service
    from tools import reset_issue_retry_allowance as command

    state, service, sessions = journey
    ids = _stranded_history(state)
    monkeypatch.setattr(github_service, "GitHubService", lambda: service)
    # No arguments: the repository is the deployment's configured one.
    monkeypatch.setattr(settings.workflow, "github_repository", REPO)

    assert await command.run(command.parse_args([])) == 0
    assert len(state["comments"]) == 3
    assert f"#{ISSUE}: reset" in capsys.readouterr().out

    with pytest.raises(SystemExit):
        command.parse_args(["--apply"])

    assert (
        await command.run(
            command.parse_args(["--apply", "--reason", "stranded attempt records"])
        )
        == 0
    )
    reset = parse_attempt_comment(state["comments"][-1]["body"]).handoff
    assert reset.outcome == OUTCOME_RETRY_RESET
    assert reset.retry_history == tuple(ids)
    admitted = await _search(service, "default/after-command")
    assert admitted.completion_disposition != "idle", admitted.outputs
    assert (
        await IssueClaimStore(sessions).get("default/after-command")
    ).issue_number == ISSUE


@pytest.mark.asyncio
async def test_a_collaborator_posted_reset_does_not_readmit_the_issue(journey):
    """Trusted provenance is not reset authority; only the operator account resets."""
    state, service, _sessions = journey
    ids = _stranded_history(state)
    _post(
        state,
        build_retry_reset_handoff(
            attempt_id="att-4503forged-eeee",
            deployment_id="inst-collaborator",
            repository=REPO,
            issue_number=ISSUE,
            authorized_by=COLLABORATOR["login"],
            authorized_at=datetime.now(UTC).isoformat(),
            reason="Self-declared reset.",
            predecessor_attempt_id=ids[-1],
            predecessor_comment_id="3",
            superseded_attempt_ids=ids,
            allowance=3,
        ),
        user=COLLABORATOR,
    )

    result = await _search(service, "default/forged-reset")

    assert result.completion_disposition == "idle", result.outputs
    evidence = result.outputs["searchEvidence"]
    assert evidence["rejectionCounts"] == {"budget_exhausted": 1}
    retry = evidence["rejectedCandidates"][0]["claimEvidence"]["retry"]
    assert retry["unauthorizedResets"] == 1
    assert [item["attemptId"] for item in retry["chargedAttempts"]] == ids


@pytest.mark.asyncio
async def test_a_reset_resolves_the_attention_its_exhausted_budget_left(journey):
    state, service, sessions = journey
    _exhausted_by_recorded_failures(state, retry_attention=True)

    [plan] = (
        await inventory_retry_resets(
            service=service, repository=REPO, include_recorded_outcomes=True
        )
    )["issues"]
    assert plan["action"] == ACTION_RESET, plan
    assert plan["resolvesAttention"] is True
    # The inventory writes nothing.
    assert state["labels"] == [NEEDS_ATTENTION]
    assert len(state["comments"]) == 3

    applied = await apply_retry_reset(
        service=service,
        plan=plan,
        reason="Recorded failures came from the launcher fixed since.",
    )

    assert applied["applied"] is True, applied
    assert applied["attentionResolved"] is True
    assert state["labels"] == []
    assert len(state["comments"]) == 4
    admitted = await _search(service, "default/after-attention-reset")
    assert admitted.completion_disposition != "idle", admitted.outputs
    receipt = await IssueClaimStore(sessions).get("default/after-attention-reset")
    assert receipt.issue_number == ISSUE


@pytest.mark.asyncio
async def test_attention_the_retry_budget_did_not_cause_is_not_reset(journey):
    state, service, _sessions = journey
    _exhausted_by_recorded_failures(state, retry_attention=False)

    [plan] = (
        await inventory_retry_resets(
            service=service, repository=REPO, include_recorded_outcomes=True
        )
    )["issues"]

    assert plan["action"] == ACTION_REFUSE
    assert plan["reasonCode"] == "attention_not_from_retry_budget"
    refused = await apply_retry_reset(
        service=service, plan=plan, reason="Recorded failures are stale."
    )
    assert refused["applied"] is False
    assert state["labels"] == [NEEDS_ATTENTION]
    assert len(state["comments"]) == 3


@pytest.mark.asyncio
async def test_an_apply_that_records_nothing_does_not_report_success(
    journey, monkeypatch, capsys
):
    from moonmind.config.settings import settings
    from moonmind.workflows.adapters import github_service
    from tools import reset_issue_retry_allowance as command

    state, service, _sessions = journey
    ids = _stranded_history(state)
    _post(
        state,
        _attempt(
            "att-4503live-dddd",
            deployment="inst-device-d",
            remaining=1,
            expired_days_ago=None,
            predecessor=ids[-1],
            predecessor_comment_id="3",
        ),
    )
    monkeypatch.setattr(github_service, "GitHubService", lambda: service)
    monkeypatch.setattr(settings.workflow, "github_repository", REPO)

    status = await command.run(
        command.parse_args(["--apply", "--reason", "stranded attempt records"])
    )

    out = capsys.readouterr().out
    assert status != 0
    assert "Resets recorded" not in out
    assert "No resets were recorded" in out
    assert len(state["comments"]) == 4
