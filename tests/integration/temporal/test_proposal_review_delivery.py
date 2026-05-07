from __future__ import annotations

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
from moonmind.workflows.task_proposals.service import TaskProposalService
from moonmind.workflows.temporal.activity_runtime import TemporalProposalActivities


class _Delivery:
    def __init__(self, *, external_key: str = "42") -> None:
        self.external_key = external_key
        self.requests = []

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
        status=TaskProposalStatus.OPEN,
        title="Add tests",
        summary="Add follow-up",
        category="tests",
        tags=["artifact_gap"],
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


@pytest.mark.asyncio
async def test_proposal_submit_persists_external_delivery_result() -> None:
    repo = AsyncMock()
    record = _record()
    repo.find_open_duplicate.return_value = None
    repo.create_proposal.return_value = record
    delivery = _Delivery()
    service = TaskProposalService(
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
                    "taskCreateRequest": {
                        "type": "task",
                        "payload": {
                            "repository": "Moon/Repo",
                            "task": {"instructions": "Add tests"},
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
