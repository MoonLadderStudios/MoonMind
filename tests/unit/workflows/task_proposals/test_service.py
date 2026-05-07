from datetime import UTC, datetime
from pathlib import Path
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
from moonmind.workflows.task_proposals.delivery import ProviderDecisionEvent


class _FakeDeliveryService:
    def __init__(self) -> None:
        self.requests = []

    async def deliver(self, request):
        self.requests.append(request)
        return SimpleNamespace(
            provider=request.provider,
            external_key="42",
            external_url="https://github.example/Moon/Repo/issues/42",
            created=True,
            duplicate_source=None,
            delivered_at=datetime(2026, 5, 7, 12, 30, tzinfo=UTC),
            warnings=(),
            provider_metadata={"marker": "moonmind-proposal"},
            to_decision=lambda: {
                "provider": request.provider,
                "externalKey": "42",
                "externalUrl": "https://github.example/Moon/Repo/issues/42",
                "created": True,
            },
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
@pytest.mark.parametrize("runtime_field", ["targetRuntime", "target_runtime", "runtime"])
async def test_create_proposal_normalizes_legacy_task_runtime_fields(
    runtime_field: str,
) -> None:
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
                "task": {
                    "instructions": "Add regression coverage",
                    runtime_field: "claude_code",
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
                "targetRuntime": "gemini_cli",
                "task": {
                    "instructions": "Refactor logic",
                    "runtime": {"mode": "gemini_cli"},
                    "authoredPresets": [
                        {
                            "presetId": "runtime-quality-followup",
                            "presetVersion": "2026-04-17",
                        }
                    ],
                    "steps": [
                        {
                            "type": "skill",
                            "title": "Refactor logic",
                            "skill": {"id": "code.implementation"},
                            "source": {
                                "kind": "preset-derived",
                                "presetId": "runtime-quality-followup",
                                "presetVersion": "2026-04-17",
                            },
                        }
                    ],
                }
            }
        },
    )
    repo.get_proposal_for_update.return_value = proposal
    service = TaskProposalService(repo, redactor=SecretRedactor([], "***"))

    updated_proposal, final_request = await service.promote_proposal(
        proposal_id=proposal.id,
        promoted_by_user_id=uuid4(),
        runtime_mode_override="claude_code",
    )

    repo.commit.assert_awaited()
    assert updated_proposal.status is TaskProposalStatus.PROMOTED

    assert final_request["payload"]["targetRuntime"] == "claude"
    assert final_request["payload"]["task"]["runtime"]["mode"] == "claude"
    assert final_request["payload"]["task"]["authoredPresets"] == [
        {
            "presetId": "runtime-quality-followup",
            "presetVersion": "2026-04-17",
        }
    ]
    assert final_request["payload"]["task"]["steps"][0]["source"] == {
        "kind": "preset-derived",
        "presetId": "runtime-quality-followup",
        "presetVersion": "2026-04-17",
    }
    assert (
        updated_proposal.task_create_request["payload"]["task"]["runtime"]["mode"]
        == "gemini_cli"
    )
    assert updated_proposal.task_create_request["payload"]["targetRuntime"] == "gemini_cli"


@pytest.mark.asyncio
async def test_promote_proposal_preserves_preset_provenance() -> None:
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
                    "instructions": "Add regression coverage",
                    "authoredPresets": [
                        {
                            "presetId": "runtime-quality-followup",
                            "presetVersion": "2026-04-17",
                            "includePath": ["root", "regression-coverage"],
                        }
                    ],
                    "steps": [
                        {
                            "type": "skill",
                            "title": "Add regression coverage",
                            "instructions": "Write the regression test.",
                            "skill": {
                                "id": "moonspec-implement",
                                "args": {"issueKey": "MM-579"},
                            },
                            "source": {
                                "kind": "preset-derived",
                                "presetId": "runtime-quality-followup",
                                "presetVersion": "2026-04-17",
                                "includePath": ["root", "regression-coverage"],
                                "originalStepId": "add-regression-test",
                            },
                        }
                    ],
                },
            }
        },
    )
    repo.get_proposal_for_update.return_value = proposal
    service = TaskProposalService(repo, redactor=SecretRedactor([], "***"))

    updated_proposal, final_request = await service.promote_proposal(
        proposal_id=proposal.id,
        promoted_by_user_id=uuid4(),
    )

    repo.commit.assert_awaited()
    assert updated_proposal.status is TaskProposalStatus.PROMOTED
    task = final_request["payload"]["task"]
    assert task["authoredPresets"] == [
        {
            "presetId": "runtime-quality-followup",
            "presetVersion": "2026-04-17",
            "includePath": ["root", "regression-coverage"],
        }
    ]
    assert task["steps"][0]["source"] == {
        "kind": "preset-derived",
        "presetId": "runtime-quality-followup",
        "presetVersion": "2026-04-17",
        "includePath": ["root", "regression-coverage"],
        "originalStepId": "add-regression-test",
    }


@pytest.mark.asyncio
async def test_promote_proposal_preserves_canonical_proposal_intent() -> None:
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
                    "instructions": "Add regression coverage",
                    "proposalPolicy": {
                        "targets": ["project"],
                    },
                },
            }
        },
    )
    repo.get_proposal_for_update.return_value = proposal
    service = TaskProposalService(repo, redactor=SecretRedactor([], "***"))

    updated_proposal, final_request = await service.promote_proposal(
        proposal_id=proposal.id,
        promoted_by_user_id=uuid4(),
    )

    repo.commit.assert_awaited()
    assert updated_proposal.status is TaskProposalStatus.PROMOTED
    assert "proposeTasks" not in final_request["payload"]
    task = final_request["payload"]["task"]
    assert task["proposeTasks"] is False
    assert task["proposalPolicy"] == {"targets": ["project"]}


@pytest.mark.asyncio
async def test_promote_proposal_rejects_preset_derived_steps_without_flat_type() -> None:
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
                    "instructions": "Add regression coverage",
                    "steps": [
                        {
                            "title": "Add regression coverage",
                            "instructions": "Write the regression test.",
                            "source": {
                                "kind": "preset-derived",
                                "presetId": "runtime-quality-followup",
                                "presetVersion": "2026-04-17",
                            },
                        }
                    ],
                },
            }
        },
    )
    repo.get_proposal_for_update.return_value = proposal
    service = TaskProposalService(repo, redactor=SecretRedactor([], "***"))

    with pytest.raises(TaskProposalValidationError, match="flat executable"):
        await service.promote_proposal(
            proposal_id=proposal.id,
            promoted_by_user_id=uuid4(),
        )

    repo.commit.assert_not_awaited()

@pytest.mark.asyncio
async def test_promote_proposal_rejects_unresolved_preset_steps() -> None:
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
                    "instructions": "Apply preset later",
                    "steps": [
                        {
                            "type": "preset",
                            "title": "Runtime quality follow-up",
                            "preset": {
                                "id": "runtime-quality-followup",
                                "inputs": {},
                            },
                        }
                    ],
                }
            }
        },
    )
    repo.get_proposal_for_update.return_value = proposal
    service = TaskProposalService(repo, redactor=SecretRedactor([], "***"))

    with pytest.raises(TaskProposalValidationError, match="stored task payload is invalid"):
        await service.promote_proposal(
            proposal_id=proposal.id,
            promoted_by_user_id=uuid4(),
        )

    repo.commit.assert_not_awaited()

@pytest.mark.asyncio
async def test_create_proposal_returns_existing_open_duplicate_before_create() -> None:
    repo = AsyncMock()
    existing = SimpleNamespace(
        id=uuid4(),
        status=TaskProposalStatus.OPEN,
        title="Add tests",
        summary="Existing follow-up",
        category="tests",
        tags=["tests"],
        repository="Moon/Repo",
        dedup_key="moon/repo:add-tests",
        dedup_hash="hash",
        review_priority=TaskProposalReviewPriority.NORMAL,
        priority_override_reason=None,
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        origin_source=TaskProposalOriginSource.WORKFLOW,
        origin_id=None,
        origin_metadata={},
        provider="jira",
        provider_metadata={"jira": {"projectKey": "MM"}},
        task_create_request={"payload": {"repository": "Moon/Repo"}},
    )
    repo.find_open_duplicate.return_value = existing
    service = TaskProposalService(repo, redactor=SecretRedactor([], "***"))
    service._emit_notification = AsyncMock()

    proposal = await service.create_proposal(
        title="Add Tests",
        summary="Ensure coverage",
        category="Tests",
        tags=["tests"],
        task_create_request={
            "type": "task",
            "priority": 0,
            "maxAttempts": 3,
            "payload": {"repository": "Moon/Repo"},
        },
        origin_source="workflow",
        origin_id=None,
        origin_metadata={"workflow_id": "wf-1"},
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
        provider="jira",
        provider_metadata={"jira": {"projectKey": "MM"}},
    )

    assert proposal is existing
    assert existing.origin_external_id == "wf-1"
    assert existing.resolved_policy["duplicate"] is True
    assert existing.provider_metadata == {"jira": {"projectKey": "MM"}}
    repo.find_open_duplicate.assert_awaited_once()
    repo.create_proposal.assert_not_awaited()
    repo.commit.assert_awaited_once()
    service._emit_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_proposal_merges_duplicate_delivery_metadata() -> None:
    repo = AsyncMock()
    existing_id = uuid4()
    existing = SimpleNamespace(
        id=existing_id,
        status=TaskProposalStatus.OPEN,
        title="Add tests",
        summary="Existing follow-up",
        category="tests",
        tags=["tests"],
        repository="Moon/Repo",
        dedup_key="moon/repo:add-tests",
        dedup_hash="hash",
        review_priority=TaskProposalReviewPriority.NORMAL,
        priority_override_reason=None,
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        origin_source=TaskProposalOriginSource.WORKFLOW,
        origin_id=None,
        origin_metadata={},
        origin_external_id=None,
        provider="jira",
        external_key=None,
        external_url=None,
        delivered_at=None,
        last_synced_at=None,
        task_snapshot_ref=None,
        provider_metadata={"jira": {"projectKey": "MM"}, "audit": "kept"},
        resolved_policy={"provider": "jira", "decision": "kept"},
        task_create_request={"payload": {"repository": "Moon/Repo"}},
    )
    delivered_at = datetime.now(UTC)
    last_synced_at = datetime.now(UTC)
    repo.find_open_duplicate.return_value = existing
    service = TaskProposalService(repo, redactor=SecretRedactor([], "***"))
    service._emit_notification = AsyncMock()

    proposal = await service.create_proposal(
        title="Add Tests",
        summary="Ensure coverage",
        category="Tests",
        tags=["tests"],
        task_create_request={
            "type": "task",
            "priority": 0,
            "maxAttempts": 3,
            "payload": {"repository": "Moon/Repo"},
        },
        origin_source="workflow",
        origin_id=None,
        origin_metadata={"workflow_id": "wf-1"},
        origin_external_id="wf-1",
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
        provider="jira",
        external_key="MM-597",
        external_url="https://example.atlassian.net/browse/MM-597",
        delivered_at=delivered_at,
        last_synced_at=last_synced_at,
        task_snapshot_ref="artifact://task-snapshot",
        provider_metadata={"jira": {"issueType": "Task"}},
        resolved_policy={"target": "project"},
    )

    assert proposal is existing
    assert existing.provider_metadata == {
        "jira": {"projectKey": "MM", "issueType": "Task"},
        "audit": "kept",
    }
    assert existing.resolved_policy == {
        "provider": "jira",
        "decision": "kept",
        "target": "project",
        "duplicate": True,
        "duplicate_record_id": str(existing_id),
    }
    assert existing.external_key == "MM-597"
    assert existing.external_url == "https://example.atlassian.net/browse/MM-597"
    assert existing.delivered_at is delivered_at
    assert existing.last_synced_at is last_synced_at
    assert existing.task_snapshot_ref == "artifact://task-snapshot"
    repo.create_proposal.assert_not_awaited()
    repo.commit.assert_awaited_once()
    service._emit_notification.assert_not_awaited()


def test_compute_dedup_fields_are_repository_aware_and_title_normalized() -> None:
    service = TaskProposalService(AsyncMock(), redactor=SecretRedactor([], "***"))

    first_key, first_hash = service._compute_dedup_fields(
        repository="Moon/Repo", title="Add   Tests!!"
    )
    second_key, second_hash = service._compute_dedup_fields(
        repository="moon/repo", title="add-tests"
    )
    other_key, other_hash = service._compute_dedup_fields(
        repository="Other/Repo", title="Add Tests"
    )

    assert first_key == second_key == "moon/repo:add-tests"
    assert first_hash == second_hash
    assert other_key == "other/repo:add-tests"
    assert other_hash != first_hash

@pytest.mark.asyncio
async def test_create_proposal_persists_provider_metadata_separately() -> None:
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
        origin_source=TaskProposalOriginSource.WORKFLOW,
        origin_id=None,
        origin_metadata={"workflow_id": "wf-1"},
        provider="jira",
        provider_metadata={"jira": {"projectKey": "MM", "labels": ["moonmind"]}},
        task_create_request={},
    )
    repo.find_open_duplicate.return_value = None
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
        origin_source="workflow",
        origin_id=None,
        origin_metadata={"workflow_id": "wf-1", "trigger_repo": "Moon/Repo"},
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
        provider="jira",
        provider_metadata={"jira": {"projectKey": "MM", "labels": ["moonmind"]}},
        resolved_policy={"provider": "jira", "decision": "accepted"},
    )

    kwargs = repo.create_proposal.await_args.kwargs
    assert kwargs["provider"] == "jira"
    assert kwargs["provider_metadata"] == {
        "jira": {"projectKey": "MM", "labels": ["moonmind"]}
    }
    assert kwargs["resolved_policy"] == {"provider": "jira", "decision": "accepted"}
    assert "provider_metadata" not in kwargs["origin_metadata"]


def test_delivery_record_migration_declares_canonical_columns() -> None:
    migration = Path("api_service/migrations/versions/311_proposal_delivery_records.py")

    text = migration.read_text()

    for column_name in (
        "provider",
        "external_key",
        "external_url",
        "delivered_at",
        "last_synced_at",
        "task_snapshot_ref",
        "origin_external_id",
        "provider_metadata",
        "resolved_policy",
    ):
        assert column_name in text


@pytest.mark.asyncio
async def test_create_proposal_invokes_delivery_and_persists_external_issue() -> None:
    repo = AsyncMock()
    record = SimpleNamespace(
        id=uuid4(),
        status=TaskProposalStatus.OPEN,
        title="Add tests",
        summary="Add follow-up",
        category="tests",
        tags=["tests"],
        repository="Moon/Repo",
        dedup_key="moon/repo:add-tests",
        dedup_hash="hash",
        review_priority=TaskProposalReviewPriority.NORMAL,
        priority_override_reason=None,
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        origin_source=TaskProposalOriginSource.WORKFLOW,
        origin_id=None,
        origin_metadata={"workflow_id": "wf-1"},
        origin_external_id="wf-1",
        provider="github",
        external_key=None,
        external_url=None,
        delivered_at=None,
        last_synced_at=None,
        task_snapshot_ref="artifact://snapshot",
        provider_metadata={},
        resolved_policy={"provider": "github"},
        task_create_request={"payload": {"repository": "Moon/Repo"}},
    )
    repo.find_open_duplicate.return_value = None
    repo.create_proposal.return_value = record
    delivery = _FakeDeliveryService()
    service = TaskProposalService(
        repo,
        redactor=SecretRedactor([], "***"),
        delivery_service=delivery,
    )
    service._emit_notification = AsyncMock()

    proposal = await service.create_proposal(
        title="Add Tests",
        summary="Ensure coverage",
        category="Tests",
        tags=["tests"],
        task_create_request={
            "type": "task",
            "priority": 0,
            "maxAttempts": 3,
            "payload": {"repository": "Moon/Repo"},
        },
        origin_source="workflow",
        origin_id=None,
        origin_external_id="wf-1",
        origin_metadata={"workflow_id": "wf-1"},
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
        provider="github",
        resolved_policy={"provider": "github"},
        task_snapshot_ref="artifact://snapshot",
    )

    assert proposal.external_key == "42"
    assert proposal.external_url == "https://github.example/Moon/Repo/issues/42"
    assert proposal.delivered_at == datetime(2026, 5, 7, 12, 30, tzinfo=UTC)
    assert proposal.last_synced_at == datetime(2026, 5, 7, 12, 30, tzinfo=UTC)
    assert proposal.provider_metadata["delivery"]["marker"] == "moonmind-proposal"
    assert delivery.requests[0].task_snapshot_ref == "artifact://snapshot"
    assert repo.commit.await_count >= 2


@pytest.mark.asyncio
async def test_create_proposal_records_sanitized_delivery_failure() -> None:
    class FailingDelivery:
        async def deliver(self, request):
            from moonmind.workflows.task_proposals.delivery import ProposalDeliveryError

            raise ProposalDeliveryError(
                "provider rejected request",
                provider=request.provider,
                destination=request.destination,
                retryable=False,
            )

    repo = AsyncMock()
    record = SimpleNamespace(
        id=uuid4(),
        status=TaskProposalStatus.OPEN,
        title="Add tests",
        summary="Add follow-up",
        category="tests",
        tags=["tests"],
        repository="Moon/Repo",
        dedup_key="moon/repo:add-tests",
        dedup_hash="hash",
        review_priority=TaskProposalReviewPriority.NORMAL,
        priority_override_reason=None,
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        origin_source=TaskProposalOriginSource.WORKFLOW,
        origin_id=None,
        origin_metadata={"workflow_id": "wf-1"},
        origin_external_id="wf-1",
        provider="github",
        external_key=None,
        external_url=None,
        delivered_at=None,
        last_synced_at=None,
        task_snapshot_ref="artifact://snapshot",
        provider_metadata={},
        resolved_policy={"provider": "github"},
        task_create_request={"payload": {"repository": "Moon/Repo"}},
    )
    repo.find_open_duplicate.return_value = None
    repo.create_proposal.return_value = record
    service = TaskProposalService(
        repo,
        redactor=SecretRedactor([], "***"),
        delivery_service=FailingDelivery(),
    )
    service._emit_notification = AsyncMock()

    proposal = await service.create_proposal(
        title="Add Tests",
        summary="Ensure coverage",
        category="Tests",
        tags=["tests"],
        task_create_request={
            "type": "task",
            "priority": 0,
            "maxAttempts": 3,
            "payload": {"repository": "Moon/Repo"},
        },
        origin_source="workflow",
        origin_id=None,
        origin_external_id="wf-1",
        origin_metadata={"workflow_id": "wf-1"},
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
        provider="github",
        resolved_policy={"provider": "github"},
        task_snapshot_ref="artifact://snapshot",
    )

    assert proposal.provider_metadata["delivery"]["status"] == "failed"
    assert proposal.provider_metadata["delivery"]["error"]["sanitizedReason"] == (
        "provider rejected request"
    )


@pytest.mark.asyncio
async def test_record_provider_decision_event_persists_idempotent_metadata() -> None:
    repo = AsyncMock()
    proposal = SimpleNamespace(
        id=uuid4(),
        status=TaskProposalStatus.OPEN,
        provider="github",
        external_key="42",
        external_url="https://github.example/Moon/Repo/issues/42",
        provider_metadata={"delivery": {"status": "delivered"}},
        resolved_policy={"allowedActions": ["promote", "dismiss", "defer", "priority"]},
        review_priority=TaskProposalReviewPriority.NORMAL,
        decision_note=None,
    )
    repo.get_proposal_for_update.return_value = proposal
    service = TaskProposalService(repo, redactor=SecretRedactor([], "***"))

    result = await service.record_provider_decision_event(
        proposal_id=proposal.id,
        event=ProviderDecisionEvent(
            provider="github",
            external_key="42",
            provider_event_id="evt-1",
            actor="reviewer",
            body="/moonmind priority urgent",
        ),
    )

    assert result.accepted is True
    assert proposal.review_priority is TaskProposalReviewPriority.URGENT
    decision_row = proposal.provider_metadata["providerDecisions"][0]
    assert decision_row["provider"] == "github"
    assert decision_row["externalKey"] == "42"
    assert decision_row["providerEventId"] == "evt-1"
    assert decision_row["actor"] == "reviewer"
    assert decision_row["decision"] == "priority"
    assert decision_row["accepted"] is True
    assert decision_row["note"] == "urgent"
    assert decision_row["priority"] == "urgent"
    assert decision_row["deferUntil"] is None
    assert decision_row["resultingExternalState"] == "priority"
    assert decision_row["observedAt"]
    repo.commit.assert_awaited_once()
    repo.refresh.assert_awaited_once_with(proposal)

    duplicate = await service.record_provider_decision_event(
        proposal_id=proposal.id,
        event=ProviderDecisionEvent(
            provider="github",
            external_key="42",
            provider_event_id="evt-1",
            actor="reviewer",
            body="/moonmind priority urgent",
        ),
    )

    assert duplicate.accepted is True
    assert len(proposal.provider_metadata["providerDecisions"]) == 1


@pytest.mark.asyncio
async def test_record_provider_decision_event_rejects_disallowed_action() -> None:
    repo = AsyncMock()
    proposal = SimpleNamespace(
        id=uuid4(),
        status=TaskProposalStatus.OPEN,
        provider="github",
        external_key="42",
        external_url="https://github.example/Moon/Repo/issues/42",
        provider_metadata={},
        resolved_policy={"allowedActions": ["dismiss"]},
        review_priority=TaskProposalReviewPriority.NORMAL,
        decision_note=None,
    )
    repo.get_proposal_for_update.return_value = proposal
    service = TaskProposalService(repo, redactor=SecretRedactor([], "***"))

    result = await service.record_provider_decision_event(
        proposal_id=proposal.id,
        event=ProviderDecisionEvent(
            provider="github",
            external_key="42",
            provider_event_id="evt-2",
            actor="reviewer",
            body="/moonmind promote",
        ),
    )

    assert result.accepted is False
    assert result.reason == "action_not_allowed"
    assert proposal.status is TaskProposalStatus.OPEN
    assert proposal.provider_metadata["providerDecisions"][0]["accepted"] is False
