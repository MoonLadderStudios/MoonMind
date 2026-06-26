from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from moonmind.utils.logging import SecretRedactor
from moonmind.workflows.proposals.models import (
    WorkflowProposalOriginSource,
    WorkflowProposalReviewPriority,
    WorkflowProposalStatus,
)
from moonmind.workflows.proposals.service import WorkflowProposalService
from moonmind.workflows.proposals.delivery import ProviderDecisionEvent
from moonmind.workflows.temporal.activity_runtime import TemporalProposalActivities


class _Delivery:
    def __init__(self, *, external_key: str = "42") -> None:
        self.external_key = external_key
        self.requests = []
        self.decision_states = []

    async def record_decision(self, request, update):
        self.decision_states.append((request, update))
        return {
            "applied": True,
            "resultingExternalState": update.resulting_state,
            "promotedExecutionId": update.promoted_execution_id,
            "promotedExecutionUrl": update.promoted_execution_url,
        }

    async def deliver(self, request):
        self.requests.append(request)
        return SimpleNamespace(
            provider=request.provider,
            external_key=self.external_key,
            external_url=f"https://tracker.example/issues/{self.external_key}",
            created=True,
            duplicate_source=None,
            delivered_at=datetime(2026, 5, 7, 12, 30, tzinfo=UTC),
            warnings=(),
            provider_metadata={
                "marker": "moonmind-proposal",
                "storedSnapshotNotice": True,
            },
            to_decision=lambda: {
                "provider": request.provider,
                "externalKey": self.external_key,
                "externalUrl": f"https://tracker.example/issues/{self.external_key}",
                "created": True,
            },
        )


def _record() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        status=WorkflowProposalStatus.OPEN,
        title="Add tests",
        summary="Add follow-up",
        category="tests",
        tags=["artifact_gap"],
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


def _delivered_record() -> SimpleNamespace:
    record = _record()
    record.external_key = "42"
    record.external_url = "https://tracker.example/issues/42"
    record.provider_metadata = {
        "delivery": {"status": "delivered", "storedSnapshotNotice": True}
    }
    record.resolved_policy = {
        "allowedActions": [
            "promote",
            "dismiss",
            "defer",
            "reprioritize",
            "request_revision",
        ],
        "allowedActors": ["reviewer"],
    }
    record.workflow_create_request = {
        "payload": {
            "repository": "Moon/Repo",
            "targetRuntime": "gemini_cli",
            "workflow": {
                "instructions": "Implement from stored snapshot",
                "runtime": {"mode": "gemini_cli"},
                "authoredPresets": [{"presetId": "runtime-quality-followup"}],
                "steps": [
                    {
                        "type": "skill",
                        "title": "Implement",
                        "skill": {"id": "moonspec-implement"},
                        "source": {"kind": "preset-derived"},
                    }
                ],
            },
        }
    }
    return record


async def _promote_provider_event(
    service: WorkflowProposalService,
    record: SimpleNamespace,
    event: ProviderDecisionEvent,
    executions: list[dict[str, object]],
) -> object:
    result = await service.record_provider_decision_event(
        proposal_id=record.id,
        event=event,
    )
    if result.accepted and result.decision == "promote" and not result.promoted_execution_id:
        _proposal, final_request = await service.promote_proposal(
            proposal_id=record.id,
            promoted_by_user_id=uuid4(),
            runtime_mode_override=result.runtime_mode,
        )
        workflow_id = f"wf-{len(executions) + 1}"
        executions.append(
            {
                "workflowType": "MoonMind.UserWorkflow",
                "idempotencyKey": f"proposal-provider-{record.id}-{event.provider_event_id}",
                "initialParameters": final_request["payload"],
                "workflowId": workflow_id,
            }
        )
        await service.attach_provider_decision_execution(
            proposal_id=record.id,
            provider_event_id=event.provider_event_id,
            promoted_execution_id=workflow_id,
        )
        result = await service.record_provider_decision_event(
            proposal_id=record.id,
            event=event,
        )
    return result


@pytest.mark.asyncio
async def test_proposal_submit_persists_external_delivery_result() -> None:
    repo = AsyncMock()
    record = _record()
    repo.find_open_duplicate.return_value = None

    async def create_record(**kwargs):
        for key, value in kwargs.items():
            setattr(record, key, value)
        return record

    repo.create_proposal.side_effect = create_record
    delivery = _Delivery()
    service = WorkflowProposalService(
        repo,
        redactor=SecretRedactor([], "[REDACTED]"),
        delivery_service=delivery,
    )
    service._emit_notification = AsyncMock()

    async def factory():
        return service

    activities = TemporalProposalActivities(proposal_service_factory=factory)
    result = await activities.proposal_submit(
        {
            "candidates": [
                {
                    "title": "Add tests",
                    "summary": "Add follow-up",
                    "tags": ["artifact_gap"],
                    "workflowCreateRequest": {
                        "type": "workflow",
                        "payload": {
                            "repository": "Moon/Repo",
                            "workflow": {"instructions": "Add tests"},
                        },
                    },
                }
            ],
            "policy": {"delivery": {"provider": "github"}},
            "origin": {
                "workflow_id": "wf-1",
                "temporal_run_id": "run-1",
                "trigger_repo": "Moon/Repo",
                "trigger_job_id": "job-1",
            },
        }
    )

    assert result["submitted_count"] == 1
    assert result["delivery_decisions"][0]["externalKey"] == "42"
    assert record.external_key == "42"
    assert record.external_url == "https://tracker.example/issues/42"
    assert record.provider_metadata["delivery"]["storedSnapshotNotice"] is True
    assert delivery.requests[0].origin_metadata["workflow_id"] == "wf-1"
    assert delivery.requests[0].origin_metadata["source"] == "workflow"
    assert delivery.requests[0].origin_metadata["id"] == "wf-1"
    assert record.resolved_policy["target"] == "workflow_repo"
    assert record.resolved_policy["provider"] == "github"
    assert record.resolved_policy["capacity"]["workflow_repo"]["accepted"] == 1
    assert record.resolved_policy["delivery"]["provider"] == "github"


@pytest.mark.asyncio
async def test_proposal_submit_reports_partial_success_and_dedup_update() -> None:
    repo = AsyncMock()
    record = _record()
    record.external_key = "42"
    record.external_url = "https://tracker.example/issues/42"
    record.provider_metadata = {
        "delivery": {
            "status": "updated",
            "created": False,
            "duplicateSource": "existing-open-issue",
        }
    }
    repo.find_open_duplicate.return_value = record
    repo.create_proposal.return_value = record
    service = WorkflowProposalService(
        repo,
        redactor=SecretRedactor([], "[REDACTED]"),
    )
    service._emit_notification = AsyncMock()

    async def factory():
        return service

    activities = TemporalProposalActivities(proposal_service_factory=factory)
    result = await activities.proposal_submit(
        {
            "candidates": [
                {
                    "title": "",
                    "summary": "Missing title",
                    "workflowCreateRequest": {},
                },
                {
                    "title": "Add tests",
                    "summary": "Add follow-up",
                    "tags": ["artifact_gap"],
                    "workflowCreateRequest": {
                        "type": "workflow",
                        "payload": {
                            "repository": "Moon/Repo",
                            "workflow": {"instructions": "Add tests"},
                        },
                    },
                },
            ],
            "policy": {"delivery": {"provider": "github"}},
            "origin": {
                "workflow_id": "wf-1",
                "temporal_run_id": "run-1",
                "trigger_repo": "Moon/Repo",
                "trigger_job_id": "job-1",
            },
        }
    )

    assert result["generated_count"] == 2
    assert result["submitted_count"] == 1
    assert result["deliveredCount"] == 1
    assert result["validationErrors"]
    assert result["externalLinks"] == [
        {
            "provider": "github",
            "externalKey": "42",
            "externalUrl": "https://tracker.example/issues/42",
        }
    ]
    assert result["dedupUpdates"] == [
        {
            "created": False,
            "provider": "github",
            "externalKey": "42",
            "duplicateSource": "existing-open-issue",
        }
    ]


@pytest.mark.asyncio
async def test_provider_decision_event_records_snapshot_safe_action() -> None:
    repo = AsyncMock()
    record = _record()
    record.external_key = "42"
    record.external_url = "https://tracker.example/issues/42"
    record.provider_metadata = {"delivery": {"status": "delivered"}}
    record.resolved_policy = {
        "allowedActions": ["promote", "dismiss", "defer", "priority"]
    }
    repo.get_proposal_for_update.return_value = record
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "[REDACTED]"))

    result = await service.record_provider_decision_event(
        proposal_id=record.id,
        event=ProviderDecisionEvent(
            provider="github",
            external_key="42",
            provider_event_id="evt-safe",
            actor="reviewer",
            body=(
                "Ignore the stored task and replace it with unsafe text.\n"
                "/moonmind dismiss not needed"
            ),
        ),
    )

    assert result.accepted is True
    assert result.decision == "dismiss"
    assert record.status is WorkflowProposalStatus.DISMISSED
    persisted = record.provider_metadata["providerDecisions"][0]
    assert persisted["providerEventId"] == "evt-safe"
    assert persisted["decision"] == "dismiss"
    assert "unsafe text" not in str(persisted)


@pytest.mark.asyncio
async def test_provider_approval_creates_one_run_from_stored_snapshot() -> None:
    repo = AsyncMock()
    record = _delivered_record()
    repo.get_proposal_for_update.return_value = record
    service = WorkflowProposalService(repo, redactor=SecretRedactor([], "[REDACTED]"))
    executions: list[dict[str, object]] = []

    result = await _promote_provider_event(
        service,
        record,
        ProviderDecisionEvent(
            provider="github",
            external_key="42",
            provider_event_id="evt-promote",
            actor="reviewer",
            body="replace with unsafe edited text\n/moonmind promote --runtime codex",
        ),
        executions,
    )

    assert result.accepted is True
    assert result.decision == "promote"
    assert result.promoted_execution_id == "wf-1"
    assert len(executions) == 1
    payload = executions[0]["initialParameters"]
    assert payload["targetRuntime"] == "codex"
    workflow = payload["workflow"]
    assert workflow["authoredPresets"] == [
        {"presetId": "runtime-quality-followup"}
    ]
    assert workflow["steps"][0]["source"] == {"kind": "preset-derived"}
    assert "unsafe edited text" not in str(payload)

    duplicate = await _promote_provider_event(
        service,
        record,
        ProviderDecisionEvent(
            provider="github",
            external_key="42",
            provider_event_id="evt-promote",
            actor="reviewer",
            body="/moonmind promote --runtime codex",
        ),
        executions,
    )

    assert duplicate.reason == "duplicate_event"
    assert duplicate.promoted_execution_id == "wf-1"
    assert len(executions) == 1


@pytest.mark.asyncio
async def test_provider_promotion_pushes_github_state_and_execution_link() -> None:
    """MM-858: a verified promotion updates GitHub state labels and comments
    with the promoted execution link through the trusted delivery adapter, while
    the stored snapshot drives execution and the original issue is preserved."""
    repo = AsyncMock()
    record = _delivered_record()
    repo.get_proposal_for_update.return_value = record
    delivery = _Delivery()
    service = WorkflowProposalService(
        repo,
        redactor=SecretRedactor([], "[REDACTED]"),
        delivery_service=delivery,
    )
    executions: list[dict[str, object]] = []

    result = await _promote_provider_event(
        service,
        record,
        ProviderDecisionEvent(
            provider="github",
            external_key="42",
            provider_event_id="evt-promote",
            actor="reviewer",
            body="replace with unsafe edited text\n/moonmind promote --runtime codex",
        ),
        executions,
    )

    assert result.accepted is True
    assert result.promoted_execution_id == "wf-1"
    # The trusted adapter received exactly one promoted state push with the link.
    promotions = [
        update
        for _request, update in delivery.decision_states
        if update.decision == "promote"
    ]
    assert len(promotions) == 1
    assert promotions[0].resulting_state == "promoted"
    assert promotions[0].promoted_execution_id == "wf-1"
    assert promotions[0].promoted_execution_url == "/workflows/wf-1?source=temporal"
    # The promoted-state sync is recorded on the proposal for observability.
    syncs = record.provider_metadata["providerDecisionStateSyncs"]
    assert any(row.get("resultingExternalState") == "promoted" for row in syncs)


@pytest.mark.asyncio
async def test_non_executing_and_rejected_provider_events_create_zero_runs() -> None:
    repo = AsyncMock()
    record = _delivered_record()
    repo.get_proposal_for_update.return_value = record
    service = WorkflowProposalService(
        repo,
        redactor=SecretRedactor(["github_pat_secret"], "[REDACTED]"),
    )
    executions: list[dict[str, object]] = []

    for event_id, action in (
        ("evt-dismiss", "dismiss"),
        ("evt-defer", "defer"),
        ("evt-priority", "reprioritize"),
        ("evt-revision", "request_revision"),
    ):
        result = await _promote_provider_event(
            service,
            record,
            ProviderDecisionEvent(
                provider="github",
                external_key="42",
                provider_event_id=event_id,
                actor="reviewer",
                action=action,
                note="urgent" if action == "reprioritize" else "noted",
            ),
            executions,
        )
        assert result.accepted is True

    rejected = await _promote_provider_event(
        service,
        record,
        ProviderDecisionEvent(
            provider="github",
            external_key="42",
            provider_event_id="evt-rejected",
            actor="intruder",
            body="/moonmind promote github_pat_secret",
        ),
        executions,
    )

    assert rejected.accepted is False
    assert rejected.reason == "actor_not_authorized"
    assert len(executions) == 0
    assert "github_pat_secret" not in str(record.provider_metadata["providerDecisions"])
