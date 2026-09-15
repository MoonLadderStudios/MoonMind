"""Replay the stranded legacy backlog through the operator command and search."""

import argparse
import json
from dataclasses import replace

import pytest

from moonmind.workflows.adapters import github_service
from moonmind.workflows.temporal import github_issue_claim_lease as leases
from moonmind.workflows.temporal import story_output_tools
from moonmind.workflows.temporal.github_issue_attempts import (
    parse_attempt_comment,
    render_attempt_comment,
)
from moonmind.workflows.temporal.issue_claim_store import IssueClaimStore
from tests.unit.workflows.temporal.test_issue_claim_journey import journey as journey  # noqa: F401

# Reference the imported pytest fixture so static analysis sees it as used.
# The test functions request `journey` by name; this keeps the import alive.
_JOURNEY_FIXTURE = journey

from tests.unit.workflows.temporal.test_issue_reservation_policy import (
    CUTOVER,
    ISSUE,
    REPOSITORY,
    _legacy_comment,
)
from tools import recover_legacy_issue_claims as command


def _args(tmp_path, *, apply=False):
    return argparse.Namespace(
        repository=REPOSITORY,
        cutover_at=CUTOVER.isoformat(),
        issue=[],
        limit=500,
        report=str(tmp_path / "recovery.json"),
        apply=apply,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_ack", [False, True])
@pytest.mark.parametrize("include_all_authors", [None, False])
@pytest.mark.parametrize("cached_claim", [False, True])
async def test_command_recovers_legacy_backlog_for_default_search(
    journey, monkeypatch, tmp_path, lost_ack, include_all_authors, cached_claim
):
    state, service, sessions = journey
    state["comments"] = [_legacy_comment()]
    state["labels"] = ["status: in-progress", "area: tests"]
    monkeypatch.setattr(github_service, "GitHubService", lambda: service)
    monkeypatch.setattr(leases, "legacy_cutover_at", lambda: None)
    if cached_claim:
        legacy = parse_attempt_comment(state["comments"][0]["body"]).handoff
        await IssueClaimStore(sessions).prepare(
            owner=legacy.workflow_id,
            repository=REPOSITORY,
            issue_number=ISSUE,
            attempt_id=legacy.attempt_id,
            actor_id="123",
            comment_body=state["comments"][0]["body"],
        )
    inputs = {"repository": REPOSITORY, "issueSearch": ""}
    if include_all_authors is not None:
        inputs["includeAllAuthors"] = include_all_authors

    async def search(owner):
        return await story_output_tools.load_github_issue_preset_brief(
            inputs,
            {"execution_owner": owner},
            github_service_factory=lambda: service,
        )

    before = await search("default/before-cutover")
    assert before.outputs.get("issue", {}).get("number") is None
    original = state["comments"][0]["body"]
    args = _args(tmp_path)
    assert await command.run(args) == 0
    assert state["comments"][0]["body"] == original
    assert json.loads((tmp_path / "recovery.json").read_text())["plannedWrites"] == 1

    state["lose_update_ack"] = lost_ack
    args.apply = True
    assert await command.run(args) == 0
    report = json.loads((tmp_path / "recovery.json").read_text())
    assert report["reservationsRetired"] == 1
    retired = parse_attempt_comment(state["comments"][0]["body"]).handoff
    previous = parse_attempt_comment(original).handoff
    assert replace(
        retired,
        lease_renewed_at=previous.lease_renewed_at,
        lease_expires_at=previous.lease_expires_at,
    ) == previous
    assert state["labels"] == ["status: in-progress", "area: tests"]

    after = await search("default/after-cutover")
    assert after.outputs.get("issue", {}).get("number") == ISSUE, after.outputs
    if cached_claim:
        cached = await IssueClaimStore(sessions).get(legacy.workflow_id)
        assert cached.ownership_ended
        assert not cached.released
        assert cached.comment_body == original
    assert "area: tests" in state["labels"]
    assert state["comments"][0]["body"].startswith("MoonMind")
    # A restart rereads the shared receipt: no second migration write is needed.
    assert await command.run(args) == 0
    assert json.loads((tmp_path / "recovery.json").read_text())["plannedWrites"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["poster", "comment_id", "missing_comment"])
async def test_search_preserves_cached_claim_without_matching_remote_retirement(
    journey, monkeypatch, tmp_path, mismatch
):
    state, service, sessions = journey
    state["comments"] = [_legacy_comment()]
    original = state["comments"][0]["body"]
    legacy = parse_attempt_comment(original).handoff
    store = IssueClaimStore(sessions)
    await store.prepare(
        owner=legacy.workflow_id,
        repository=REPOSITORY,
        issue_number=ISSUE,
        attempt_id=legacy.attempt_id,
        actor_id="123",
        comment_body=original,
    )
    monkeypatch.setattr(github_service, "GitHubService", lambda: service)
    monkeypatch.setattr(leases, "legacy_cutover_at", lambda: None)
    assert await command.run(_args(tmp_path, apply=True)) == 0
    if mismatch == "poster":
        state["comments"][0]["user"] = {"id": 456, "login": "other-owner"}
    elif mismatch == "comment_id":
        async with store.locked(legacy.workflow_id) as row:
            row.comment_id = "different-comment"
    else:
        state["comments"] = []
    before_comments = list(state["comments"])

    result = await story_output_tools.load_github_issue_preset_brief(
        {"repository": REPOSITORY, "issueSearch": ""},
        {"execution_owner": "default/after-cutover"},
        github_service_factory=lambda: service,
    )

    assert result.outputs.get("issue", {}).get("number") is None
    cached = await store.get(legacy.workflow_id)
    assert not cached.ownership_ended
    assert cached.comment_body == original
    assert state["comments"] == before_comments


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["live_successor", "hold", "foreign_scope", "untrusted_poster"]
)
async def test_command_rechecks_whole_issue_before_retiring(
    journey, monkeypatch, tmp_path, change
):
    state, service, _ = journey
    state["comments"] = [_legacy_comment()]
    original = state["comments"][0]["body"]
    monkeypatch.setattr(github_service, "GitHubService", lambda: service)
    read = service.list_issue_comments
    reads = 0

    async def concurrent_read(**kwargs):
        nonlocal reads
        reads += 1
        if reads == 2:
            if change == "untrusted_poster":
                state["comments"][0].update(
                    user={"id": 456, "login": "outsider"}, author_association="NONE"
                )
                return await read(**kwargs)
            successor = _legacy_comment(attemptId="att-successor")
            successor["id"] = 92
            handoff = parse_attempt_comment(successor["body"]).handoff
            if change == "live_successor":
                handoff = leases.with_lease(handoff)
            elif change == "hold":
                handoff = replace(handoff, operator_hold=True)
            else:
                handoff = replace(handoff, issue_number=ISSUE + 1)
            successor["body"] = render_attempt_comment(handoff)
            state["comments"].append(successor)
        return await read(**kwargs)

    monkeypatch.setattr(service, "list_issue_comments", concurrent_read)
    assert await command.run(_args(tmp_path, apply=True)) != 0
    assert state["comments"][0]["body"] == original
    report = json.loads((tmp_path / "recovery.json").read_text())
    assert report["reservationsRetired"] == 0
    assert report["issues"][0]["applyResult"]["reasonCode"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["inventory_read", "readback"])
async def test_command_retains_missing_evidence_in_report(
    journey, monkeypatch, tmp_path, failure
):
    state, service, _ = journey
    state["comments"] = [_legacy_comment()]
    original = state["comments"][0]["body"]
    monkeypatch.setattr(github_service, "GitHubService", lambda: service)
    read = service.list_issue_comments
    reads = 0

    async def unavailable_read(**kwargs):
        nonlocal reads
        reads += 1
        if reads == (1 if failure == "inventory_read" else 3):
            state["failed_read_path"] = f"/repos/{REPOSITORY}/issues/{ISSUE}/comments"
        return await read(**kwargs)

    monkeypatch.setattr(service, "list_issue_comments", unavailable_read)
    assert await command.run(_args(tmp_path, apply=True)) != 0
    report = json.loads((tmp_path / "recovery.json").read_text())
    assert report["reservationsRetired"] == 0
    evidence = report["issues"][0]
    if failure == "inventory_read":
        assert state["comments"][0]["body"] == original
    else:
        assert state["comments"][0]["body"] != original
        evidence = evidence["applyResult"]
    assert evidence["reasonCode"] == "claim_read_failure"
