"""Unit tests for attempt-handoff production Activities (MoonLadderStudios/MoonMind#4177).

The Activities supply the production service object, the canonical
installation identity, and the trusted-poster allow-list; all handoff
decisions stay in ``github_issue_attempt``.
"""

from __future__ import annotations

from typing import Any

import pytest

from moonmind.workflows.temporal import github_issue_attempt as attempt
from moonmind.workflows.temporal.activities import attempt_handoff_activities as activities
from moonmind.workflows.temporal.github_issue_attempt import AttemptHandoff


class _FakeService:
    def __init__(self, comments: list[dict[str, Any]] | None = None) -> None:
        self.comments = list(comments or [])
        self.next_id = 1000
        self.creates = 0

    async def list_issue_comments(self, *, repo: str, issue_number: int, github_token: str | None = None):
        return {"ok": True, "reasonCode": "listed", "summary": "listed", "comments": list(self.comments)}

    async def create_issue_comment(self, *, repo: str, issue_number: int, body: str, github_token: str | None = None):
        self.creates += 1
        self.next_id += 1
        self.comments.append({"id": self.next_id, "body": body, "user": {"login": "moonmind-bot"}})
        return {"ok": True, "reasonCode": "created", "summary": "created", "commentId": self.next_id}

    async def update_issue_comment(self, *, repo: str, comment_id: int, body: str, github_token: str | None = None):
        for comment in self.comments:
            if comment["id"] == comment_id:
                comment["body"] = body
        return {"ok": True, "reasonCode": "updated", "summary": "updated", "commentId": comment_id}


def _handoff(deployment: str = "device-a", **overrides: Any) -> AttemptHandoff:
    identity, error = attempt.build_attempt_identity(
        repository="o/r",
        issue_number=1,
        workflow_id="wf",
        run_id="run-1",
        installation_id=deployment,
    )
    assert error == ""
    params: dict[str, Any] = {
        "attempt_id": identity["attemptId"],
        "deployment_id": identity["deploymentId"],
        "repository": identity["repository"],
        "issue_number": identity["issueNumber"],
        "workflow_id": identity["workflowId"],
        "run_id": identity["runId"],
        "activity": attempt.ACTIVITY_ACTIVE,
        "outcome": "pending",
        "next_action": "continue-implementation",
    }
    params.update(overrides)
    return AttemptHandoff(**params)


@pytest.mark.asyncio
async def test_progress_publishes_through_the_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOONMIND_INSTALLATION_ID", "device-a")
    service = _FakeService()
    result = await activities.publish_attempt_progress(
        repository="o/r",
        issue_number=1,
        handoff=_handoff(),
        trusted_posters=["moonmind-bot"],
        force=True,
        service=service,
    )
    assert result["ok"] is True
    assert result["reasonCode"] == "created"
    assert service.creates == 1


@pytest.mark.asyncio
async def test_progress_rejects_deployment_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOONMIND_INSTALLATION_ID", "device-a")
    service = _FakeService()
    result = await activities.publish_attempt_progress(
        repository="o/r",
        issue_number=1,
        handoff=_handoff(deployment="device-b"),
        trusted_posters=["moonmind-bot"],
        force=True,
        service=service,
    )
    assert result["ok"] is False
    assert result["reasonCode"] == "issue_identity_mismatch"
    assert service.creates == 0


@pytest.mark.asyncio
async def test_progress_fails_closed_without_installation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MOONMIND_INSTALLATION_ID", raising=False)
    service = _FakeService()
    result = await activities.publish_attempt_progress(
        repository="o/r",
        issue_number=1,
        handoff=_handoff(),
        trusted_posters=["moonmind-bot"],
        force=True,
        service=service,
    )
    assert result["ok"] is False
    assert result["reasonCode"] == "installation_unconfigured"
    assert service.creates == 0


@pytest.mark.asyncio
async def test_release_requires_all_four_confirmations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOONMIND_INSTALLATION_ID", "device-a")
    service = _FakeService()
    handoff = _handoff(activity=attempt.ACTIVITY_RELEASING)
    proposed = await activities.publish_attempt_release(
        repository="o/r",
        issue_number=1,
        handoff=handoff,
        trusted_posters=["moonmind-bot"],
        release={
            "writers_stopped": True,
            "mutations_settled": True,
            "preservation_verified_or_no_work": False,
            "label_outcome_observed": True,
        },
        service=service,
    )
    assert proposed["ok"] is False
    assert proposed["reasonCode"] == "release_proposed"
    assert service.creates == 0
    released = _handoff(activity=attempt.ACTIVITY_RELEASED, writers_stopped=True)
    completed = await activities.publish_attempt_release(
        repository="o/r",
        issue_number=1,
        handoff=released,
        trusted_posters=["moonmind-bot"],
        release={
            "writers_stopped": True,
            "mutations_settled": True,
            "preservation_verified_or_no_work": True,
            "label_outcome_observed": True,
        },
        service=service,
    )
    assert completed["ok"] is True
    assert completed["reasonCode"] == "created"
