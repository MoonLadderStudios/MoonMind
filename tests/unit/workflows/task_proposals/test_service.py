from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from moonmind.utils.logging import SecretRedactor
from moonmind.workflows.task_proposals.models import (
    TaskProposalOriginSource,
    TaskProposalReviewPriority,
    TaskProposalStatus,
)
from moonmind.workflows.task_proposals.service import TaskProposalService


@pytest.mark.asyncio
async def test_create_proposal_persists_normalized_payload() -> None:
    repo = AsyncMock()
    queue = SimpleNamespace()
    queue.normalize_task_job_payload = MagicMock(
        return_value={
            "repository": "MoonLadderStudios/MoonMind",
            "task": {"instructions": "tests"},
        }
    )
    record = SimpleNamespace(
        id=uuid4(),
        status=TaskProposalStatus.OPEN,
        title="Add tests",
        summary="Add follow-up",
        category="tests",
        tags=["tests"],
        repository="MoonLadderStudios/MoonMind",
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
        promoted_job_id=None,
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        origin_source=TaskProposalOriginSource.QUEUE,
        origin_id=None,
        origin_metadata={},
        task_create_request={},
    )
    repo.create_proposal.return_value = record
    service = TaskProposalService(repo, queue, redactor=SecretRedactor([], "***"))
    service._emit_notification = AsyncMock()

    proposal = await service.create_proposal(
        title="Add Tests",
        summary="Ensure coverage",
        category="Tests",
        tags=["Auth"],
        task_create_request={
            "type": "task",
            "priority": 0,
            "maxAttempts": 3,
            "payload": {"repository": "MoonLadderStudios/MoonMind"},
        },
        origin_source="queue",
        origin_id=None,
        origin_metadata={},
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
    )

    repo.create_proposal.assert_awaited_once()
    args, kwargs = repo.create_proposal.await_args
    assert kwargs["repository"] == "MoonLadderStudios/MoonMind"
    assert kwargs["category"] == "tests"
    assert kwargs["dedup_key"].startswith("moonladderstudios/moonmind")
    assert len(kwargs["dedup_hash"]) == 64
    assert kwargs["review_priority"] is TaskProposalReviewPriority.NORMAL
    service._emit_notification.assert_awaited_once()
    assert proposal is record


@pytest.mark.asyncio
async def test_promote_proposal_calls_queue_and_updates_record() -> None:
    repo = AsyncMock()
    queue = SimpleNamespace()
    queue.normalize_task_job_payload = MagicMock(
        return_value={"repository": "Moon/Repo", "task": {"instructions": "edited"}}
    )
    job = SimpleNamespace(id=uuid4())
    queue.create_job = AsyncMock(return_value=job)
    proposal = SimpleNamespace(
        id=uuid4(),
        status=TaskProposalStatus.OPEN,
        promoted_job_id=None,
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        task_create_request={
            "type": "task",
            "priority": 0,
            "maxAttempts": 3,
            "affinityKey": None,
            "payload": {"repository": "Moon/Repo"},
        },
    )
    repo.get_proposal_for_update.return_value = proposal
    service = TaskProposalService(repo, queue, redactor=SecretRedactor([], "***"))

    updated, created_job = await service.promote_proposal(
        proposal_id=proposal.id,
        promoted_by_user_id=uuid4(),
        priority_override=5,
        max_attempts_override=4,
        note="ship",
    )

    queue.create_job.assert_awaited_once()
    repo.commit.assert_awaited()
    repo.refresh.assert_awaited_with(proposal)
    assert updated.status is TaskProposalStatus.PROMOTED
    assert created_job is job


@pytest.mark.asyncio
async def test_dismiss_proposal_updates_status() -> None:
    repo = AsyncMock()
    queue = SimpleNamespace()
    proposal = SimpleNamespace(
        id=uuid4(),
        status=TaskProposalStatus.OPEN,
        decision_note=None,
        decided_by_user_id=None,
    )
    repo.get_proposal_for_update.return_value = proposal
    service = TaskProposalService(repo, queue, redactor=SecretRedactor([], "***"))

    dismissed = await service.dismiss_proposal(
        proposal_id=proposal.id,
        dismissed_by_user_id=uuid4(),
        note="not now",
    )

    repo.commit.assert_awaited()
    repo.refresh.assert_awaited_with(proposal)
    assert dismissed.status is TaskProposalStatus.DISMISSED
    assert "not now" in (dismissed.decision_note or "")


@pytest.mark.asyncio
async def test_promote_proposal_accepts_task_override() -> None:
    repo = AsyncMock()
    queue = SimpleNamespace()
    queue.normalize_task_job_payload = MagicMock(
        return_value={"repository": "Moon/Repo", "task": {"instructions": "edited"}}
    )
    job = SimpleNamespace(id=uuid4())
    queue.create_job = AsyncMock(return_value=job)
    proposal = SimpleNamespace(
        id=uuid4(),
        status=TaskProposalStatus.OPEN,
        promoted_job_id=None,
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        task_create_request={
            "type": "task",
            "priority": 0,
            "maxAttempts": 3,
            "payload": {"repository": "Moon/Repo", "task": {"instructions": "old"}},
        },
    )
    repo.get_proposal_for_update.return_value = proposal
    service = TaskProposalService(repo, queue, redactor=SecretRedactor([], "***"))

    override = {
        "type": "task",
        "priority": 1,
        "maxAttempts": 2,
        "payload": {"repository": "Moon/Repo", "task": {"instructions": "edited"}},
    }
    await service.promote_proposal(
        proposal_id=proposal.id,
        promoted_by_user_id=uuid4(),
        priority_override=None,
        max_attempts_override=None,
        note=None,
        task_create_request_override=override,
    )

    queue.create_job.assert_awaited_once()
    args, kwargs = queue.create_job.await_args
    assert kwargs["payload"]["task"]["instructions"] == "edited"


@pytest.mark.asyncio
async def test_update_review_priority_persists_value() -> None:
    repo = AsyncMock()
    queue = SimpleNamespace()
    proposal = SimpleNamespace(
        id=uuid4(),
        status=TaskProposalStatus.OPEN,
        review_priority=TaskProposalReviewPriority.NORMAL,
    )
    repo.get_proposal_for_update.return_value = proposal
    async def _update_priority(*, proposal, priority, user_id):
        proposal.review_priority = priority
        proposal.decided_by_user_id = user_id
        return proposal

    repo.update_priority.side_effect = _update_priority
    service = TaskProposalService(repo, queue, redactor=SecretRedactor([], "***"))

    updated = await service.update_review_priority(
        proposal_id=proposal.id,
        priority="urgent",
        updated_by_user_id=uuid4(),
    )

    repo.update_priority.assert_awaited()
    repo.commit.assert_awaited()
    assert updated is proposal
    assert updated.review_priority is TaskProposalReviewPriority.URGENT


@pytest.mark.asyncio
async def test_snooze_and_unsnooze_proposal(monkeypatch) -> None:
    repo = AsyncMock()
    queue = SimpleNamespace()
    proposal = SimpleNamespace(
        id=uuid4(),
        status=TaskProposalStatus.OPEN,
        snoozed_until=None,
        snoozed_by_user_id=None,
        snooze_note=None,
    )
    repo.get_proposal_for_update.return_value = proposal
    repo.snooze.return_value = proposal
    repo.unsnooze.return_value = proposal
    service = TaskProposalService(repo, queue, redactor=SecretRedactor([], "***"))

    until = datetime.now(UTC) + timedelta(hours=1)
    await service.snooze_proposal(
        proposal_id=proposal.id,
        until=until,
        note="later",
        user_id=uuid4(),
    )
    repo.snooze.assert_awaited()

    await service.unsnooze_proposal(proposal_id=proposal.id, user_id=uuid4())
    repo.unsnooze.assert_awaited()
