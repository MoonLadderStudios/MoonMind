from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from moonmind.config.settings import settings
from moonmind.utils.logging import SecretRedactor
from moonmind.workflows.proposals.models import (
    WorkflowProposalOriginSource,
    WorkflowProposalReviewPriority,
    WorkflowProposalStatus,
)
from moonmind.workflows.proposals.service import (
    WorkflowProposalService,
    WorkflowProposalValidationError,
)
from moonmind.workflows.proposals.delivery import ProviderDecisionEvent


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
        status=WorkflowProposalStatus.OPEN,
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
        origin_source=WorkflowProposalOriginSource.QUEUE,
        origin_id=None,
        origin_metadata={},
        workflow_create_request={},
    )
    repo.create_proposal.return_value = record
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))
    service._emit_notification = AsyncMock()

    proposal = await service.create_proposal(
        title="Add Tests",
        summary="Ensure coverage",
        category="Tests",
        tags=["Auth"],
        workflow_create_request={
            "type": "workflow",
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
    assert kwargs["dedup_key"].startswith("workflow-repo:moon/repo:tests:")
    assert len(kwargs["dedup_hash"]) == 64
    assert kwargs["review_priority"] is WorkflowProposalReviewPriority.NORMAL
    assert kwargs["priority_override_reason"] is None
    assert kwargs["workflow_create_request"]["payload"]["workflow"]["publish"]["mode"] == "pr"
    assert kwargs["workflow_create_request"]["payload"]["workflow"]["runtime"]["mode"] is None
    assert kwargs["workflow_create_request"]["payload"]["workflow"]["runtime"]["model"] is None
    assert kwargs["workflow_create_request"]["payload"].get("targetRuntime") is None
    service._emit_notification.assert_awaited_once()
    assert proposal is record

@pytest.mark.asyncio
async def test_emit_notification_blocks_secret_payload_before_webhook(
    monkeypatch,
) -> None:
    repo = AsyncMock()
    repo.has_notification.return_value = False
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))
    service._notifications_enabled = True
    service._notification_webhook = "https://hooks.example.test/proposals"
    service._notification_authorization = None
    service._notification_timeout = 5
    raw_secret = "unit-test-proposal-notification-secret"
    proposal = SimpleNamespace(
        id=uuid4(),
        category="tests",
        repository="Moon/Repo",
        title="Add tests",
        summary=f"Please notify password={raw_secret}",
        review_priority=WorkflowProposalReviewPriority.NORMAL,
        origin_id=None,
        workflow_create_request={},
    )

    class _FailingClient:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("webhook sender must not be called")

    monkeypatch.setattr(settings.security, "high_security_mode", True)
    monkeypatch.setattr(
        "moonmind.workflows.proposals.service.httpx.AsyncClient",
        _FailingClient,
    )

    await service._emit_notification(proposal)

    repo.log_notification.assert_awaited_once()
    _, kwargs = repo.log_notification.await_args
    assert kwargs["status"] == "blocked"
    assert kwargs["target"] == "https://hooks.example.test/proposals"
    assert "workflow_proposal.notification.payload" in kwargs["error"]
    assert raw_secret not in kwargs["error"]
    repo.commit.assert_awaited_once()

@pytest.mark.asyncio
async def test_create_proposal_accepts_enum_origin_source() -> None:
    repo = AsyncMock()
    record = SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.OPEN,
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
        origin_source=WorkflowProposalOriginSource.QUEUE,
        origin_id=None,
        origin_metadata={},
        workflow_create_request={},
    )
    repo.create_proposal.return_value = record
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))
    service._emit_notification = AsyncMock()

    await service.create_proposal(
        title="Add Tests",
        summary="Ensure coverage",
        category="Tests",
        tags=["Auth"],
        workflow_create_request={
            "type": "workflow",
            "priority": 0,
            "maxAttempts": 3,
            "payload": {"repository": "Moon/Repo"},
        },
        origin_source=WorkflowProposalOriginSource.QUEUE,
        origin_id=None,
        origin_metadata={},
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
    )

    repo.create_proposal.assert_awaited_once()
    args, kwargs = repo.create_proposal.await_args
    assert kwargs["origin_source"] == WorkflowProposalOriginSource.QUEUE

@pytest.mark.asyncio
async def test_create_proposal_normalizes_managed_runtime_ids() -> None:
    repo = AsyncMock()
    record = SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.OPEN,
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
        origin_source=WorkflowProposalOriginSource.QUEUE,
        origin_id=None,
        origin_metadata={},
        workflow_create_request={},
    )
    repo.create_proposal.return_value = record
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))
    service._emit_notification = AsyncMock()

    await service.create_proposal(
        title="Add Tests",
        summary="Ensure coverage",
        category="Tests",
        tags=["Auth"],
        workflow_create_request={
            "type": "workflow",
            "priority": 0,
            "maxAttempts": 3,
            "payload": {
                "repository": "Moon/Repo",
                "targetRuntime": "codex_cli",
                "workflow": {
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
    assert kwargs["workflow_create_request"]["payload"]["targetRuntime"] == "codex"
    assert kwargs["workflow_create_request"]["payload"]["workflow"]["runtime"]["mode"] == "claude"

@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_field", ["targetRuntime", "target_runtime", "runtime"])
async def test_create_proposal_normalizes_legacy_task_runtime_fields(
    runtime_field: str,
) -> None:
    repo = AsyncMock()
    record = SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.OPEN,
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
        origin_source=WorkflowProposalOriginSource.QUEUE,
        origin_id=None,
        origin_metadata={},
        workflow_create_request={},
    )
    repo.create_proposal.return_value = record
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))
    service._emit_notification = AsyncMock()

    await service.create_proposal(
        title="Add Tests",
        summary="Ensure coverage",
        category="Tests",
        tags=["Auth"],
        workflow_create_request={
            "type": "workflow",
            "priority": 0,
            "maxAttempts": 3,
            "payload": {
                "repository": "Moon/Repo",
                "workflow": {
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
    assert kwargs["workflow_create_request"]["payload"]["workflow"]["runtime"]["mode"] == "claude"

@pytest.mark.asyncio
async def test_create_proposal_enforces_moonmind_metadata() -> None:
    repo = AsyncMock()
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))

    with pytest.raises(WorkflowProposalValidationError):
        await service.create_proposal(
            title="Run quality issue",
            summary="Missing metadata should fail",
            category="run_quality",
            tags=["loop_detected"],
            workflow_create_request={
                "type": "workflow",
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
        status=WorkflowProposalStatus.OPEN,
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
        origin_source=WorkflowProposalOriginSource.QUEUE,
        origin_id=None,
        origin_metadata={},
        workflow_create_request={},
    )
    repo.create_proposal.return_value = record
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))
    service._emit_notification = AsyncMock()

    await service.create_proposal(
        title="[run_quality] Fix loop",
        summary="Detected loop",
        category="run_quality",
        tags=["loop_detected"],
        workflow_create_request={
            "type": "workflow",
            "priority": 0,
            "maxAttempts": 3,
            "payload": {"repository": "MoonLadderStudios/MoonMind"},
        },
        origin_source="queue",
        origin_id=None,
        origin_metadata={
            "trigger_repo": "moon/org",
            "trigger_job_id": str(uuid4()),
            "signal": {"severity": "medium"},
        },
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
    )

    kwargs = repo.create_proposal.await_args.kwargs
    assert kwargs["review_priority"] is WorkflowProposalReviewPriority.HIGH
    assert kwargs["priority_override_reason"] == "signal:loop_detected"

@pytest.mark.asyncio
async def test_create_proposal_accepts_snake_case_moonmind_signal_metadata() -> None:
    repo = AsyncMock()
    record = SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.OPEN,
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
        origin_source=WorkflowProposalOriginSource.WORKFLOW,
        origin_id=None,
        origin_metadata={},
        workflow_create_request={},
    )
    repo.create_proposal.return_value = record
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))
    service._emit_notification = AsyncMock()

    await service.create_proposal(
        title="[run_quality] Fix retry",
        summary="Detected retry signal",
        category="run_quality",
        tags=["retry"],
        workflow_create_request={
            "type": "workflow",
            "priority": 0,
            "maxAttempts": 3,
            "payload": {"repository": "MoonLadderStudios/MoonMind"},
        },
        origin_source="workflow",
        origin_id=None,
        origin_metadata={
            "trigger_repo": "moon/org",
            "trigger_job_id": str(uuid4()),
            "signal": {"severity": "high", "retries": 2},
        },
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
    )

    kwargs = repo.create_proposal.await_args.kwargs
    assert kwargs["origin_metadata"]["trigger_repo"] == "moon/org"
    assert "triggerRepo" not in kwargs["origin_metadata"]
    assert kwargs["review_priority"] is WorkflowProposalReviewPriority.HIGH
    assert kwargs["priority_override_reason"] == "signal:severity"

@pytest.mark.asyncio
async def test_create_proposal_honors_requested_priority_when_higher() -> None:
    repo = AsyncMock()
    record = SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.OPEN,
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
        origin_source=WorkflowProposalOriginSource.QUEUE,
        origin_id=None,
        origin_metadata={},
        workflow_create_request={},
    )
    repo.create_proposal.return_value = record
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))
    service._emit_notification = AsyncMock()

    await service.create_proposal(
        title="[run_quality] Retry failure",
        summary="Retry flake",
        category="run_quality",
        tags=["retry"],
        workflow_create_request={
            "type": "workflow",
            "priority": 0,
            "maxAttempts": 3,
            "payload": {"repository": "MoonLadderStudios/MoonMind"},
        },
        origin_source="queue",
        origin_id=None,
        origin_metadata={
            "trigger_repo": "moon/org",
            "trigger_job_id": str(uuid4()),
            "signal": {"severity": "medium", "retries": 1},
        },
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
        review_priority="urgent",
    )

    kwargs = repo.create_proposal.await_args.kwargs
    assert kwargs["review_priority"] is WorkflowProposalReviewPriority.URGENT
    assert kwargs["priority_override_reason"] is None

@pytest.mark.asyncio
async def test_dismiss_proposal_updates_status() -> None:
    repo = AsyncMock()
    proposal = SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.OPEN,
        decision_note=None,
        decided_by_user_id=None,
    )
    repo.get_proposal_for_update.return_value = proposal
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))

    dismissed = await service.dismiss_proposal(
        proposal_id=proposal.id,
        dismissed_by_user_id=uuid4(),
        note="not now",
    )

    repo.commit.assert_awaited()
    repo.refresh.assert_awaited_with(proposal)
    assert dismissed.status is WorkflowProposalStatus.DISMISSED
    assert "not now" in (dismissed.decision_note or "")

@pytest.mark.asyncio
async def test_update_review_priority_persists_value() -> None:
    repo = AsyncMock()
    proposal = SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.OPEN,
        review_priority=WorkflowProposalReviewPriority.NORMAL,
    )
    repo.get_proposal_for_update.return_value = proposal

    async def _update_priority(*, proposal, priority, user_id):
        proposal.review_priority = priority
        proposal.decided_by_user_id = user_id
        return proposal

    repo.update_priority.side_effect = _update_priority
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))

    updated = await service.update_review_priority(
        proposal_id=proposal.id,
        priority="urgent",
        updated_by_user_id=uuid4(),
    )

    repo.update_priority.assert_awaited()
    repo.commit.assert_awaited()
    assert updated is proposal
    assert updated.review_priority is WorkflowProposalReviewPriority.URGENT

@pytest.mark.asyncio
async def test_promote_proposal_applies_runtime_override() -> None:
    repo = AsyncMock()
    proposal = SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.OPEN,
        repository="Moon/Repo",
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        workflow_create_request={
            "payload": {
                "repository": "Moon/Repo",
                "targetRuntime": "gemini_cli",
                "workflow": {
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
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))

    updated_proposal, final_request = await service.promote_proposal(
        proposal_id=proposal.id,
        promoted_by_user_id=uuid4(),
        runtime_mode_override="claude_code",
    )

    repo.commit.assert_awaited()
    assert updated_proposal.status is WorkflowProposalStatus.PROMOTED

    assert final_request["payload"]["targetRuntime"] == "claude"
    assert final_request["payload"]["workflow"]["runtime"]["mode"] == "claude"
    assert final_request["payload"]["workflow"]["authoredPresets"] == [
        {
            "presetId": "runtime-quality-followup",
            "presetVersion": "2026-04-17",
        }
    ]
    assert final_request["payload"]["workflow"]["steps"][0]["source"] == {
        "kind": "preset-derived",
        "presetId": "runtime-quality-followup",
        "presetVersion": "2026-04-17",
    }
    assert (
        updated_proposal.workflow_create_request["payload"]["workflow"]["runtime"]["mode"]
        == "gemini_cli"
    )
    assert updated_proposal.workflow_create_request["payload"]["targetRuntime"] == "gemini_cli"


@pytest.mark.asyncio
async def test_promote_proposal_rejects_unsupported_runtime_before_commit() -> None:
    repo = AsyncMock()
    proposal = SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.OPEN,
        repository="Moon/Repo",
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        workflow_create_request={
            "payload": {
                "repository": "Moon/Repo",
                "workflow": {
                    "instructions": "Refactor logic",
                    "runtime": {"mode": "gemini_cli"},
                },
            }
        },
    )
    repo.get_proposal_for_update.return_value = proposal
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))

    with pytest.raises(WorkflowProposalValidationError, match="runtimeMode must be one of"):
        await service.promote_proposal(
            proposal_id=proposal.id,
            promoted_by_user_id=uuid4(),
            runtime_mode_override="codex_cloud",
        )

    repo.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_promote_proposal_preserves_preset_provenance() -> None:
    repo = AsyncMock()
    proposal = SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.OPEN,
        repository="Moon/Repo",
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        workflow_create_request={
            "payload": {
                "repository": "Moon/Repo",
                "workflow": {
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
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))

    updated_proposal, final_request = await service.promote_proposal(
        proposal_id=proposal.id,
        promoted_by_user_id=uuid4(),
    )

    repo.commit.assert_awaited()
    assert updated_proposal.status is WorkflowProposalStatus.PROMOTED
    task = final_request["payload"]["workflow"]
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
async def test_promote_proposal_uses_reviewed_flattened_payload_without_reexpansion() -> None:
    repo = AsyncMock()
    reviewed_steps = [
        {
            "id": "implement-mm-573",
            "type": "skill",
            "title": "Implement MM-573",
            "instructions": "Implement the reviewed MM-573 payload.",
            "skill": {
                "id": "moonspec-implement",
                "args": {"issueKey": "MM-573"},
            },
            "source": {
                "kind": "preset-derived",
                "presetSlug": "jira-orchestrate",
                "presetVersion": "reviewed-version",
                "originalStepId": "implement-story",
            },
        }
    ]
    proposal = SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.OPEN,
        repository="Moon/Repo",
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        workflow_create_request={
            "payload": {
                "repository": "Moon/Repo",
                "workflow": {
                    "instructions": "Promote reviewed MM-573 payload.",
                    "taskTemplate": {
                        "slug": "jira-orchestrate",
                        "version": "live-version-that-must-not-be-expanded",
                    },
                    "authoredPresets": [
                        {
                            "presetSlug": "jira-orchestrate",
                            "presetVersion": "reviewed-version",
                        }
                    ],
                    "steps": reviewed_steps,
                },
            }
        },
    )
    repo.get_proposal_for_update.return_value = proposal
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))

    updated_proposal, final_request = await service.promote_proposal(
        proposal_id=proposal.id,
        promoted_by_user_id=uuid4(),
    )

    repo.commit.assert_awaited()
    assert updated_proposal.status is WorkflowProposalStatus.PROMOTED
    task = final_request["payload"]["workflow"]
    assert len(task["steps"]) == 1
    assert task["steps"][0]["id"] == reviewed_steps[0]["id"]
    assert task["steps"][0]["type"] == reviewed_steps[0]["type"]
    assert task["steps"][0]["skill"] == reviewed_steps[0]["skill"]
    assert task["steps"][0]["source"]["presetVersion"] == "reviewed-version"
    assert task["taskTemplate"]["version"] == "live-version-that-must-not-be-expanded"


@pytest.mark.asyncio
async def test_promote_proposal_preserves_canonical_proposal_intent() -> None:
    repo = AsyncMock()
    proposal = SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.OPEN,
        repository="Moon/Repo",
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        workflow_create_request={
            "payload": {
                "repository": "Moon/Repo",
                "workflow": {
                    "instructions": "Add regression coverage",
                    "proposalPolicy": {
                        "targets": ["project"],
                    },
                },
            }
        },
    )
    repo.get_proposal_for_update.return_value = proposal
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))

    updated_proposal, final_request = await service.promote_proposal(
        proposal_id=proposal.id,
        promoted_by_user_id=uuid4(),
    )

    repo.commit.assert_awaited()
    assert updated_proposal.status is WorkflowProposalStatus.PROMOTED
    assert "proposeTasks" not in final_request["payload"]
    task = final_request["payload"]["workflow"]
    assert task["proposeTasks"] is False
    assert task["proposalPolicy"] == {"targets": ["project"]}


@pytest.mark.asyncio
async def test_promote_proposal_rejects_preset_derived_steps_without_flat_type() -> None:
    repo = AsyncMock()
    proposal = SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.OPEN,
        repository="Moon/Repo",
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        workflow_create_request={
            "payload": {
                "repository": "Moon/Repo",
                "workflow": {
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
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))

    with pytest.raises(WorkflowProposalValidationError, match="flat executable"):
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
        status=WorkflowProposalStatus.OPEN,
        repository="Moon/Repo",
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        workflow_create_request={
            "payload": {
                "repository": "Moon/Repo",
                "workflow": {
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
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))

    with pytest.raises(WorkflowProposalValidationError, match="stored workflow payload is invalid"):
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
        status=WorkflowProposalStatus.OPEN,
        title="Add tests",
        summary="Existing follow-up",
        category="tests",
        tags=["tests"],
        repository="Moon/Repo",
        dedup_key="moon/repo:add-tests",
        dedup_hash="hash",
        review_priority=WorkflowProposalReviewPriority.NORMAL,
        priority_override_reason=None,
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        origin_source=WorkflowProposalOriginSource.WORKFLOW,
        origin_id=None,
        origin_metadata={},
        provider="jira",
        provider_metadata={"jira": {"projectKey": "MM"}},
        workflow_create_request={"payload": {"repository": "Moon/Repo"}},
    )
    repo.find_open_duplicate.return_value = existing
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))
    service._emit_notification = AsyncMock()

    proposal = await service.create_proposal(
        title="Add Tests",
        summary="Ensure coverage",
        category="Tests",
        tags=["tests"],
        workflow_create_request={
            "type": "workflow",
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
        status=WorkflowProposalStatus.OPEN,
        title="Add tests",
        summary="Existing follow-up",
        category="tests",
        tags=["tests"],
        repository="Moon/Repo",
        dedup_key="moon/repo:add-tests",
        dedup_hash="hash",
        review_priority=WorkflowProposalReviewPriority.NORMAL,
        priority_override_reason=None,
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        origin_source=WorkflowProposalOriginSource.WORKFLOW,
        origin_id=None,
        origin_metadata={},
        origin_external_id=None,
        provider="jira",
        external_key=None,
        external_url=None,
        delivered_at=None,
        last_synced_at=None,
        workflow_snapshot_ref=None,
        provider_metadata={"jira": {"projectKey": "MM"}, "audit": "kept"},
        resolved_policy={"provider": "jira", "decision": "kept"},
        workflow_create_request={"payload": {"repository": "Moon/Repo"}},
    )
    delivered_at = datetime.now(UTC)
    last_synced_at = datetime.now(UTC)
    repo.find_open_duplicate.return_value = existing
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))
    service._emit_notification = AsyncMock()

    proposal = await service.create_proposal(
        title="Add Tests",
        summary="Ensure coverage",
        category="Tests",
        tags=["tests"],
        workflow_create_request={
            "type": "workflow",
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
        workflow_snapshot_ref="artifact://task-snapshot",
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
    assert existing.workflow_snapshot_ref == "artifact://task-snapshot"
    repo.create_proposal.assert_not_awaited()
    repo.commit.assert_awaited_once()
    service._emit_notification.assert_not_awaited()


def test_compute_dedup_fields_include_target_repository_category_and_title() -> None:
    service = WorkflowProposalService(AsyncMock(), redactor=SecretRedactor([], "***"))

    first_key, first_hash = service._compute_dedup_fields(
        target_class="workflow-repo",
        repository="Moon/Repo",
        category="Tests",
        title="Add   Tests!!",
    )
    second_key, second_hash = service._compute_dedup_fields(
        target_class="workflow_repo",
        repository="moon/repo",
        category="tests",
        title="add-tests",
    )
    other_key, other_hash = service._compute_dedup_fields(
        target_class="workflow-repo",
        repository="Other/Repo",
        category="Tests",
        title="Add Tests",
    )
    other_category_key, other_category_hash = service._compute_dedup_fields(
        target_class="workflow-repo",
        repository="Moon/Repo",
        category="Security",
        title="Add Tests",
    )
    moonmind_key, moonmind_hash = service._compute_dedup_fields(
        target_class="moonmind",
        repository="Moon/Repo",
        category="Tests",
        title="Add Tests",
    )

    assert first_key == second_key == "workflow-repo:moon/repo:tests:add-tests"
    assert first_hash == second_hash
    assert other_key == "workflow-repo:other/repo:tests:add-tests"
    assert other_hash != first_hash
    assert other_category_key == "workflow-repo:moon/repo:security:add-tests"
    assert other_category_hash != first_hash
    assert moonmind_key == "moonmind:moon/repo:tests:add-tests"
    assert moonmind_hash != first_hash

@pytest.mark.asyncio
async def test_create_proposal_persists_provider_metadata_separately() -> None:
    repo = AsyncMock()
    record = SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.OPEN,
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
        origin_source=WorkflowProposalOriginSource.WORKFLOW,
        origin_id=None,
        origin_metadata={"workflow_id": "wf-1"},
        provider="jira",
        provider_metadata={"jira": {"projectKey": "MM", "labels": ["moonmind"]}},
        workflow_create_request={},
    )
    repo.find_open_duplicate.return_value = None
    repo.create_proposal.return_value = record
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))
    service._emit_notification = AsyncMock()

    await service.create_proposal(
        title="Add Tests",
        summary="Ensure coverage",
        category="Tests",
        tags=["Auth"],
        workflow_create_request={
            "type": "workflow",
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
        "workflow_snapshot_ref",
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
        status=WorkflowProposalStatus.OPEN,
        title="Add tests",
        summary="Add follow-up",
        category="tests",
        tags=["tests"],
        repository="Moon/Repo",
        dedup_key="moon/repo:add-tests",
        dedup_hash="hash",
        review_priority=WorkflowProposalReviewPriority.NORMAL,
        priority_override_reason=None,
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        origin_source=WorkflowProposalOriginSource.WORKFLOW,
        origin_id=None,
        origin_metadata={"workflow_id": "wf-1"},
        origin_external_id="wf-1",
        provider="github",
        external_key=None,
        external_url=None,
        delivered_at=None,
        last_synced_at=None,
        workflow_snapshot_ref="artifact://snapshot",
        provider_metadata={},
        resolved_policy={"provider": "github"},
        workflow_create_request={"payload": {"repository": "Moon/Repo"}},
    )
    repo.find_open_duplicate.return_value = None
    repo.create_proposal.return_value = record
    delivery = _FakeDeliveryService()
    service = WorkflowProposalService(
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
        workflow_create_request={
            "type": "workflow",
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
        workflow_snapshot_ref="artifact://snapshot",
    )

    assert proposal.external_key == "42"
    assert proposal.external_url == "https://github.example/Moon/Repo/issues/42"
    assert proposal.delivered_at == datetime(2026, 5, 7, 12, 30, tzinfo=UTC)
    assert proposal.last_synced_at == datetime(2026, 5, 7, 12, 30, tzinfo=UTC)
    assert proposal.provider_metadata["delivery"]["marker"] == "moonmind-proposal"
    assert delivery.requests[0].workflow_snapshot_ref == "artifact://snapshot"
    assert repo.commit.await_count >= 2


@pytest.mark.asyncio
async def test_redeliver_proposal_reuses_trusted_delivery_adapter() -> None:
    repo = AsyncMock()
    record = SimpleNamespace(
        id=uuid4(),
        provider="github",
        external_key="42",
        external_url="https://github.example/Moon/Repo/issues/42",
        repository="Moon/Repo",
        title="Add tests",
        summary="Add follow-up",
        category="tests",
        tags=["tests"],
        dedup_key="moon/repo:add-tests",
        dedup_hash="hash",
        review_priority=WorkflowProposalReviewPriority.NORMAL,
        origin_metadata={},
        provider_metadata={"delivery": {"status": "failed"}},
        resolved_policy={},
        workflow_snapshot_ref="artifact://snapshot",
        workflow_create_request={"payload": {"repository": "Moon/Repo"}},
        delivered_at=None,
        last_synced_at=None,
    )
    repo.get_proposal.return_value = record
    delivery = _FakeDeliveryService()
    service = WorkflowProposalService(
        repo,
        redactor=SecretRedactor([], "***"),
        delivery_service=delivery,
    )

    updated = await service.redeliver_proposal(proposal_id=record.id)

    assert updated is record
    assert delivery.requests[0].record_id == str(record.id)
    assert record.provider_metadata["delivery"]["status"] == "delivered"
    repo.get_proposal_for_update.assert_not_called()
    repo.refresh.assert_awaited_with(record)


@pytest.mark.asyncio
async def test_sync_proposal_delivery_records_recovery_audit_without_adapter_sync() -> None:
    repo = AsyncMock()
    record = SimpleNamespace(
        id=uuid4(),
        provider="jira",
        external_key="MM-42",
        external_url="https://jira.example/browse/MM-42",
        repository="MM",
        title="Add tests",
        summary="Add follow-up",
        category="tests",
        tags=["tests"],
        dedup_key="mm:add-tests",
        dedup_hash="hash",
        review_priority=WorkflowProposalReviewPriority.NORMAL,
        origin_metadata={},
        provider_metadata={"delivery": {"status": "delivered"}},
        resolved_policy={},
        workflow_snapshot_ref="artifact://snapshot",
        workflow_create_request={"payload": {"repository": "Moon/Repo"}},
        delivered_at=None,
        last_synced_at=None,
    )
    repo.get_proposal.return_value = record
    service = WorkflowProposalService(
        repo,
        redactor=SecretRedactor([], "***"),
        delivery_service=object(),
    )

    updated = await service.sync_proposal_delivery(proposal_id=record.id)

    assert updated is record
    assert record.provider_metadata["sync"]["status"] == "inspected"
    assert record.last_synced_at is not None
    repo.get_proposal_for_update.assert_not_called()
    repo.commit.assert_awaited()


@pytest.mark.asyncio
async def test_redeliver_proposal_requires_configured_delivery_service() -> None:
    repo = AsyncMock()
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))

    with pytest.raises(WorkflowProposalValidationError):
        await service.redeliver_proposal(proposal_id=uuid4())

    repo.get_proposal.assert_not_called()


@pytest.mark.asyncio
async def test_sync_proposal_delivery_requires_configured_delivery_service() -> None:
    repo = AsyncMock()
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))

    with pytest.raises(WorkflowProposalValidationError):
        await service.sync_proposal_delivery(proposal_id=uuid4())

    repo.get_proposal.assert_not_called()


@pytest.mark.asyncio
async def test_create_proposal_records_sanitized_delivery_failure() -> None:
    class FailingDelivery:
        async def deliver(self, request):
            from moonmind.workflows.proposals.delivery import ProposalDeliveryError

            raise ProposalDeliveryError(
                "provider rejected request",
                provider=request.provider,
                destination=request.destination,
                retryable=False,
            )

    repo = AsyncMock()
    record = SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.OPEN,
        title="Add tests",
        summary="Add follow-up",
        category="tests",
        tags=["tests"],
        repository="Moon/Repo",
        dedup_key="moon/repo:add-tests",
        dedup_hash="hash",
        review_priority=WorkflowProposalReviewPriority.NORMAL,
        priority_override_reason=None,
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        origin_source=WorkflowProposalOriginSource.WORKFLOW,
        origin_id=None,
        origin_metadata={"workflow_id": "wf-1"},
        origin_external_id="wf-1",
        provider="github",
        external_key=None,
        external_url=None,
        delivered_at=None,
        last_synced_at=None,
        workflow_snapshot_ref="artifact://snapshot",
        provider_metadata={},
        resolved_policy={"provider": "github"},
        workflow_create_request={"payload": {"repository": "Moon/Repo"}},
    )
    repo.find_open_duplicate.return_value = None
    repo.create_proposal.return_value = record
    service = WorkflowProposalService(
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
        workflow_create_request={
            "type": "workflow",
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
        workflow_snapshot_ref="artifact://snapshot",
    )

    assert proposal.provider_metadata["delivery"]["status"] == "failed"
    assert proposal.provider_metadata["delivery"]["error"]["sanitizedReason"] == (
        "provider rejected request"
    )


@pytest.mark.asyncio
async def test_create_proposal_records_sanitized_unexpected_delivery_failure() -> None:
    class FailingDelivery:
        async def deliver(self, request):
            raise RuntimeError("token=ghp_secret adapter exploded")

    repo = AsyncMock()
    record = SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.OPEN,
        title="Add tests",
        summary="Add follow-up",
        category="tests",
        tags=["tests"],
        repository="Moon/Repo",
        dedup_key="moon/repo:add-tests",
        dedup_hash="hash",
        review_priority=WorkflowProposalReviewPriority.NORMAL,
        priority_override_reason=None,
        proposed_by_worker_id="worker-1",
        proposed_by_user_id=None,
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        origin_source=WorkflowProposalOriginSource.WORKFLOW,
        origin_id=None,
        origin_metadata={"workflow_id": "wf-1"},
        origin_external_id="wf-1",
        provider="github",
        external_key=None,
        external_url=None,
        delivered_at=None,
        last_synced_at=None,
        workflow_snapshot_ref="artifact://snapshot",
        provider_metadata={},
        resolved_policy={"provider": "github"},
        workflow_create_request={"payload": {"repository": "Moon/Repo"}},
    )
    repo.find_open_duplicate.return_value = None
    repo.create_proposal.return_value = record
    service = WorkflowProposalService(
        repo,
        redactor=SecretRedactor(["ghp_secret"], "***"),
        delivery_service=FailingDelivery(),
    )
    service._emit_notification = AsyncMock()

    proposal = await service.create_proposal(
        title="Add Tests",
        summary="Ensure coverage",
        category="Tests",
        tags=["tests"],
        workflow_create_request={
            "type": "workflow",
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
        workflow_snapshot_ref="artifact://snapshot",
    )

    error = proposal.provider_metadata["delivery"]["error"]
    assert error["sanitizedReason"] == "provider delivery failed"
    assert error["errorType"] == "RuntimeError"
    assert "ghp_secret" not in str(error)


@pytest.mark.asyncio
async def test_record_provider_decision_event_persists_idempotent_metadata() -> None:
    repo = AsyncMock()
    proposal = SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.OPEN,
        provider="github",
        external_key="42",
        external_url="https://github.example/Moon/Repo/issues/42",
        provider_metadata={"delivery": {"status": "delivered"}},
        resolved_policy={"allowedActions": ["promote", "dismiss", "defer", "reprioritize"]},
        review_priority=WorkflowProposalReviewPriority.NORMAL,
        decision_note=None,
    )
    repo.get_proposal_for_update.return_value = proposal
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))

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
    assert proposal.review_priority is WorkflowProposalReviewPriority.URGENT
    decision_row = proposal.provider_metadata["providerDecisions"][0]
    assert decision_row["provider"] == "github"
    assert decision_row["externalKey"] == "42"
    assert decision_row["providerEventId"] == "evt-1"
    assert decision_row["actor"] == "reviewer"
    assert decision_row["decision"] == "reprioritize"
    assert decision_row["accepted"] is True
    assert decision_row["note"] == "urgent"
    assert decision_row["priority"] == "urgent"
    assert decision_row["deferUntil"] is None
    assert decision_row["resultingExternalState"] == "reprioritized"
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
async def test_record_provider_decision_event_rejects_unverified_actor_before_state_change() -> None:
    repo = AsyncMock()
    proposal = SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.OPEN,
        provider="github",
        external_key="42",
        external_url="https://github.example/Moon/Repo/issues/42",
        provider_metadata={},
        resolved_policy={"allowedActions": ["promote"], "allowedActors": ["lead"]},
        review_priority=WorkflowProposalReviewPriority.NORMAL,
        decision_note=None,
    )
    repo.get_proposal_for_update.return_value = proposal
    service = WorkflowProposalService(repo, redactor=SecretRedactor(["ghp_secret"], "***"))

    result = await service.record_provider_decision_event(
        proposal_id=proposal.id,
        event=ProviderDecisionEvent(
            provider="github",
            external_key="42",
            provider_event_id="evt-auth",
            actor="reviewer",
            body="/moonmind promote ghp_secret",
            authenticity_verified=False,
        ),
    )

    assert result.accepted is False
    assert result.reason == "provider_auth_failed"
    assert proposal.status is WorkflowProposalStatus.OPEN
    persisted = proposal.provider_metadata["providerDecisions"][0]
    assert persisted["accepted"] is False
    assert persisted["reason"] == "provider_auth_failed"
    assert "ghp_secret" not in str(persisted)


@pytest.mark.asyncio
async def test_record_provider_decision_event_records_non_executing_outcomes() -> None:
    repo = AsyncMock()
    proposal = SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.OPEN,
        provider="jira",
        external_key="MM-599",
        external_url="https://jira.example/browse/MM-599",
        provider_metadata={},
        resolved_policy={
            "allowedActions": ["dismiss", "defer", "reprioritize", "request_revision"],
            "allowedActors": ["reviewer"],
        },
        review_priority=WorkflowProposalReviewPriority.NORMAL,
        decision_note=None,
    )
    repo.get_proposal_for_update.return_value = proposal
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))

    decisions = [
        ("evt-dismiss", "dismiss", "closed"),
        ("evt-defer", "defer", "deferred"),
        ("evt-priority", "reprioritize", "triage"),
        ("evt-revision", "request_revision", "needs-author"),
    ]
    for event_id, action, external_state in decisions:
        result = await service.record_provider_decision_event(
            proposal_id=proposal.id,
            event=ProviderDecisionEvent(
                provider="jira",
                external_key="MM-599",
                provider_event_id=event_id,
                actor="reviewer",
                action=action,
                note="urgent" if action == "reprioritize" else "noted",
                external_state=external_state,
            ),
        )
        assert result.accepted is True

    rows = proposal.provider_metadata["providerDecisions"]
    assert [row["decision"] for row in rows] == [
        "dismiss",
        "defer",
        "reprioritize",
        "request_revision",
    ]
    assert rows[1]["resultingExternalState"] == "deferred"
    assert rows[2]["priority"] == "urgent"
    assert rows[3]["resultingExternalState"] == "needs-author"
    assert proposal.review_priority is WorkflowProposalReviewPriority.URGENT


@pytest.mark.asyncio
async def test_promote_proposal_allows_provider_accepted_snapshot_with_runtime_control() -> None:
    repo = AsyncMock()
    proposal = SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.ACCEPTED,
        repository="Moon/Repo",
        promoted_at=None,
        promoted_by_user_id=None,
        decided_by_user_id=None,
        decision_note=None,
        workflow_create_request={
            "payload": {
                "repository": "Moon/Repo",
                "targetRuntime": "gemini_cli",
                "workflow": {
                    "instructions": "Implement MM-599",
                    "runtime": {"mode": "gemini_cli"},
                    "authoredPresets": [{"presetId": "runtime-quality-followup"}],
                    "steps": [
                        {
                            "type": "skill",
                            "title": "Run implementation",
                            "skill": {"id": "moonspec-implement"},
                            "source": {
                                "kind": "preset-derived",
                                "presetId": "runtime-quality-followup",
                            },
                        }
                    ],
                },
            }
        },
    )
    repo.get_proposal_for_update.return_value = proposal
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))

    updated, final_request = await service.promote_proposal(
        proposal_id=proposal.id,
        promoted_by_user_id=uuid4(),
        runtime_mode_override="codex",
    )

    assert updated.status is WorkflowProposalStatus.PROMOTED
    assert final_request["payload"]["targetRuntime"] == "codex"
    task = final_request["payload"]["workflow"]
    assert task["runtime"]["mode"] == "codex"
    assert task["authoredPresets"] == [{"presetId": "runtime-quality-followup"}]
    assert task["steps"][0]["source"]["kind"] == "preset-derived"


@pytest.mark.asyncio
async def test_attach_provider_decision_execution_persists_promoted_run_id() -> None:
    repo = AsyncMock()
    proposal = SimpleNamespace(
        id=uuid4(),
        provider_metadata={
            "providerDecisions": [
                {
                    "providerEventId": "evt-promote",
                    "decision": "promote",
                    "accepted": True,
                }
            ]
        },
    )
    repo.get_proposal_for_update.return_value = proposal
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))

    updated = await service.attach_provider_decision_execution(
        proposal_id=proposal.id,
        provider_event_id="evt-promote",
        promoted_execution_id="wf-promoted-1",
    )

    row = updated.provider_metadata["providerDecisions"][0]
    assert row["promotedExecutionId"] == "wf-promoted-1"
    assert row["resultingExternalState"] == "promoted"


@pytest.mark.asyncio
async def test_record_provider_decision_event_rejects_disallowed_action() -> None:
    repo = AsyncMock()
    proposal = SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.OPEN,
        provider="github",
        external_key="42",
        external_url="https://github.example/Moon/Repo/issues/42",
        provider_metadata={},
        resolved_policy={"allowedActions": ["dismiss"]},
        review_priority=WorkflowProposalReviewPriority.NORMAL,
        decision_note=None,
    )
    repo.get_proposal_for_update.return_value = proposal
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))

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
    assert proposal.status is WorkflowProposalStatus.OPEN
    assert proposal.provider_metadata["providerDecisions"][0]["accepted"] is False


@pytest.mark.asyncio
async def test_record_provider_decision_event_rejects_external_identity_mismatch() -> None:
    repo = AsyncMock()
    proposal = SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.OPEN,
        provider="github",
        external_key="42",
        external_url="https://github.example/Moon/Repo/issues/42",
        provider_metadata={},
        resolved_policy={"allowedActions": ["promote", "dismiss", "defer", "priority"]},
        review_priority=WorkflowProposalReviewPriority.NORMAL,
        decision_note=None,
    )
    repo.get_proposal_for_update.return_value = proposal
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "***"))

    result = await service.record_provider_decision_event(
        proposal_id=proposal.id,
        event=ProviderDecisionEvent(
            provider="github",
            external_key="99",
            provider_event_id="evt-3",
            actor="reviewer",
            body="/moonmind promote",
        ),
    )

    assert result.accepted is False
    assert result.reason == "provider_identity_mismatch"
    assert proposal.status is WorkflowProposalStatus.OPEN
    decision_row = proposal.provider_metadata["providerDecisions"][0]
    assert decision_row["accepted"] is False
    assert decision_row["reason"] == "provider_identity_mismatch"
