from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from moonmind.utils.logging import SecretRedactor
from moonmind.workflows.task_proposals.models import (
    TaskProposalOriginSource,
    TaskProposalReviewPriority,
    TaskProposalStatus,
)
from moonmind.workflows.task_proposals.service import (
    TaskProposalService,
    TaskProposalValidationError,
)


@pytest.mark.asyncio
async def test_create_proposal_defers_runtime_defaults_until_promotion() -> None:
    repo = AsyncMock()
    record = SimpleNamespace(
        id=uuid4(),
        status=TaskProposalStatus.OPEN,
        title="Add tests",
        summary="Add follow-up",
        category="tests",
        tags=["tests"],
        repository="Moon/Repo",
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
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
    service = TaskProposalService(repo, redactor=SecretRedactor([], "***"))
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
            "payload": {"repository": "Moon/Repo"},
        },
        origin_source="queue",
        origin_id=None,
        origin_metadata={},
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
    )

    repo.create_proposal.assert_awaited_once()
    args, kwargs = repo.create_proposal.await_args
    assert kwargs["repository"] == "Moon/Repo"
    assert kwargs["category"] == "tests"
    assert kwargs["dedup_key"].startswith("moon/repo")
    assert len(kwargs["dedup_hash"]) == 64
    assert kwargs["review_priority"] is TaskProposalReviewPriority.NORMAL
    assert kwargs["priority_override_reason"] is None
    assert kwargs["task_create_request"]["payload"]["task"]["publish"]["mode"] == "pr"
    assert kwargs["task_create_request"]["payload"]["task"]["runtime"]["mode"] is None
    assert kwargs["task_create_request"]["payload"]["task"]["runtime"]["model"] is None
    assert kwargs["task_create_request"]["payload"].get("targetRuntime") is None
    service._emit_notification.assert_awaited_once()
    assert proposal is record


@pytest.mark.asyncio
async def test_create_proposal_accepts_enum_origin_source() -> None:
    repo = AsyncMock()
    record = SimpleNamespace(
        id=uuid4(),
        status=TaskProposalStatus.OPEN,
        title="Add tests",
        summary="Add follow-up",
        category="tests",
        tags=["tests"],
        repository="Moon/Repo",
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
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
    service = TaskProposalService(repo, redactor=SecretRedactor([], "***"))
    service._emit_notification = AsyncMock()

    await service.create_proposal(
        title="Add Tests",
        summary="Ensure coverage",
        category="Tests",
        tags=["Auth"],
        task_create_request={
            "type": "task",
            "priority": 0,
            "maxAttempts": 3,
            "payload": {"repository": "Moon/Repo"},
        },
        origin_source=TaskProposalOriginSource.QUEUE,
        origin_id=None,
        origin_metadata={},
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
    )

    repo.create_proposal.assert_awaited_once()
    args, kwargs = repo.create_proposal.await_args
    assert kwargs["origin_source"] == TaskProposalOriginSource.QUEUE


@pytest.mark.asyncio
async def test_create_proposal_normalizes_managed_runtime_ids() -> None:
    repo = AsyncMock()
    record = SimpleNamespace(
        id=uuid4(),
        status=TaskProposalStatus.OPEN,
        title="Add tests",
        summary="Add follow-up",
        category="tests",
        tags=["tests"],
        repository="Moon/Repo",
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
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
    service = TaskProposalService(repo, redactor=SecretRedactor([], "***"))
    service._emit_notification = AsyncMock()

    await service.create_proposal(
        title="Add Tests",
        summary="Ensure coverage",
        category="Tests",
        tags=["Auth"],
        task_create_request={
            "type": "task",
            "priority": 0,
            "maxAttempts": 3,
            "payload": {
                "repository": "Moon/Repo",
                "targetRuntime": "codex_cli",
                "task": {
                    "instructions": "Add regression coverage",
                    "runtime": {"mode": "claude_code"},
                },
            },
        },
        origin_source="queue",
        origin_id=None,
        origin_metadata={},
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
    )

    kwargs = repo.create_proposal.await_args.kwargs
    assert kwargs["task_create_request"]["payload"]["targetRuntime"] == "codex"
    assert kwargs["task_create_request"]["payload"]["task"]["runtime"]["mode"] == "claude"


@pytest.mark.asyncio
async def test_create_proposal_enforces_moonmind_metadata() -> None:
    repo = AsyncMock()
    service = TaskProposalService(repo, redactor=SecretRedactor([], "***"))

    with pytest.raises(TaskProposalValidationError):
        await service.create_proposal(
            title="Run quality issue",
            summary="Missing metadata should fail",
            category="run_quality",
            tags=["loop_detected"],
            task_create_request={
                "type": "task",
                "priority": 0,
                "maxAttempts": 3,
                "payload": {"repository": "MoonLadderStudios/MoonMind"},
            },
            origin_source="queue",
            origin_id=None,
            origin_metadata={"triggerRepo": "moon/org"},
            proposed_by_worker_id="worker-1",
            proposed_by_user_id=None,
        )


@pytest.mark.asyncio
async def test_create_proposal_overrides_priority_for_moonmind() -> None:
    repo = AsyncMock()
    record = SimpleNamespace(
        id=uuid4(),
        status=TaskProposalStatus.OPEN,
        title="Run Quality",
        summary="Fix loop",
        category="run_quality",
        tags=["loop_detected"],
        repository="MoonLadderStudios/MoonMind",
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
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
    service = TaskProposalService(repo, redactor=SecretRedactor([], "***"))
    service._emit_notification = AsyncMock()

    await service.create_proposal(
        title="[run_quality] Fix loop",
        summary="Detected loop",
        category="run_quality",
        tags=["loop_detected"],
        task_create_request={
            "type": "task",
            "priority": 0,
            "maxAttempts": 3,
            "payload": {"repository": "MoonLadderStudios/MoonMind"},
        },
        origin_source="queue",
        origin_id=None,
        origin_metadata={
            "triggerRepo": "moon/org",
            "triggerJobId": str(uuid4()),
            "signal": {"severity": "medium"},
        },
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
    )

    kwargs = repo.create_proposal.await_args.kwargs
    assert kwargs["review_priority"] is TaskProposalReviewPriority.HIGH
    assert kwargs["priority_override_reason"] == "signal:loop_detected"


@pytest.mark.asyncio
async def test_create_proposal_honors_requested_priority_when_higher() -> None:
    repo = AsyncMock()
    record = SimpleNamespace(
        id=uuid4(),
        status=TaskProposalStatus.OPEN,
        title="Run Quality",
        summary="Fix retry",
        category="run_quality",
        tags=["retry"],
        repository="MoonLadderStudios/MoonMind",
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
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
    service = TaskProposalService(repo, redactor=SecretRedactor([], "***"))
    service._emit_notification = AsyncMock()

    await service.create_proposal(
        title="[run_quality] Retry failure",
        summary="Retry flake",
        category="run_quality",
        tags=["retry"],
        task_create_request={
            "type": "task",
            "priority": 0,
            "maxAttempts": 3,
            "payload": {"repository": "MoonLadderStudios/MoonMind"},
        },
        origin_source="queue",
        origin_id=None,
        origin_metadata={
            "triggerRepo": "moon/org",
            "triggerJobId": str(uuid4()),
            "signal": {"severity": "medium", "retries": 1},
        },
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
        review_priority="urgent",
    )

    kwargs = repo.create_proposal.await_args.kwargs
    assert kwargs["review_priority"] is TaskProposalReviewPriority.URGENT
    assert kwargs["priority_override_reason"] is None






@pytest.mark.asyncio
async def test_dismiss_proposal_updates_status() -> None:
    repo = AsyncMock()
    proposal = SimpleNamespace(
        id=uuid4(),
        status=TaskProposalStatus.OPEN,
        decision_note=None,
        decided_by_user_id=None,
    )
    repo.get_proposal_for_update.return_value = proposal
    service = TaskProposalService(repo, redactor=SecretRedactor([], "***"))

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
async def test_update_review_priority_persists_value() -> None:
    repo = AsyncMock()
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
    service = TaskProposalService(repo, redactor=SecretRedactor([], "***"))

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
async def test_promote_proposal_applies_runtime_override() -> None:
    repo = AsyncMock()
    proposal = SimpleNamespace(
        id=uuid4(),
        status=TaskProposalStatus.OPEN,
        repository="Moon/Repo",
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        task_create_request={
            "payload": {
                "repository": "Moon/Repo",
                "task": {
                    "instructions": "Refactor logic",
                    "runtime": {"mode": "gemini_cli"},
                }
            }
        },
    )
    repo.get_proposal_for_update.return_value = proposal
    service = TaskProposalService(repo, redactor=SecretRedactor([], "***"))

    override_request = {
        "type": "task",
        "payload": {
            "repository": "Moon/Repo",
            "task": {
                "instructions": "Refactor logic",
                "runtime": {"mode": "claude"},
            }
        }
    }

    updated_proposal, final_request = await service.promote_proposal(
        proposal_id=proposal.id,
        promoted_by_user_id=uuid4(),
        task_create_request_override=override_request,
    )

    repo.commit.assert_awaited()
    assert updated_proposal.status is TaskProposalStatus.PROMOTED
    
    assert final_request["payload"]["task"]["runtime"]["mode"] == "claude"
    assert updated_proposal.task_create_request["payload"]["task"]["runtime"]["mode"] == "gemini_cli"


@pytest.mark.asyncio
async def test_promote_proposal_override_normalizes_managed_runtime_ids() -> None:
    repo = AsyncMock()
    proposal = SimpleNamespace(
        id=uuid4(),
        status=TaskProposalStatus.OPEN,
        repository="Moon/Repo",
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        task_create_request={
            "payload": {
                "repository": "Moon/Repo",
                "task": {
                    "instructions": "Refactor logic",
                    "runtime": {"mode": "gemini_cli"},
                }
            }
        },
    )
    repo.get_proposal_for_update.return_value = proposal
    service = TaskProposalService(repo, redactor=SecretRedactor([], "***"))

    override_request = {
        "type": "task",
        "payload": {
            "repository": "Moon/Repo",
            "targetRuntime": "codex_cli",
            "task": {
                "instructions": "Refactor logic",
                "runtime": {"mode": "claude_code"},
            },
        },
    }

    updated_proposal, final_request = await service.promote_proposal(
        proposal_id=proposal.id,
        promoted_by_user_id=uuid4(),
        task_create_request_override=override_request,
    )

    repo.commit.assert_awaited()
    assert updated_proposal.status is TaskProposalStatus.PROMOTED
    assert final_request["payload"]["targetRuntime"] == "codex"
    assert final_request["payload"]["task"]["runtime"]["mode"] == "claude"






