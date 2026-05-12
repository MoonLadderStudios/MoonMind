"""Hermetic integration coverage for MM-643 backend-computed Resume eligibility."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    MoonMindWorkflowState,
    TemporalExecutionCloseStatus,
)
from moonmind.workflows.temporal.service import TemporalExecutionService

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@asynccontextmanager
async def _db(tmp_path: Path):
    url = f"sqlite+aiosqlite:///{tmp_path}/backend_resume_eligibility.db"
    engine = create_async_engine(url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield maker
    finally:
        await engine.dispose()


def _checkpoint_payload(*, workflow_id: str, run_id: str) -> dict[str, object]:
    return {
        "schemaVersion": "v1",
        "source": {"workflowId": workflow_id, "runId": run_id},
        "taskInputSnapshotRef": "artifact://snapshot/source",
        "planDigest": "sha256:plan",
        "failedStep": {
            "logicalStepId": "implement",
            "order": 2,
            "attempt": 1,
            "title": "Implement",
        },
        "preservedSteps": [
            {
                "logicalStepId": "plan",
                "order": 1,
                "status": "succeeded",
                "sourceAttempt": 1,
                "artifacts": {"outputSummary": "artifact://summary"},
                "stateCheckpointRef": "artifact://workspace/before-plan",
            }
        ],
        "preparedArtifactRefs": ["artifact://prepared"],
        "resumeWorkspace": {"branch": "feature", "commit": "abc123"},
    }


async def test_accepted_resume_carries_canonical_recovery_and_resume_refs(
    tmp_path: Path,
) -> None:
    async with _db(tmp_path) as maker:
        async with maker() as session:
            service = TemporalExecutionService(session, client_adapter=AsyncMock())
            created = await service.create_execution(
                workflow_type="MoonMind.Run",
                owner_id=uuid4(),
                title="Resume source",
                input_artifact_ref="artifact://input/source",
                plan_artifact_ref="artifact://plan/source",
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                    "taskRunId": "old-task-run",
                    "task": {"title": "Resume source", "instructions": "Original"},
                },
                idempotency_key=None,
            )
            created.state = MoonMindWorkflowState.FAILED
            created.close_status = TemporalExecutionCloseStatus.FAILED
            created.memo = {
                **created.memo,
                "task_input_snapshot_ref": "artifact://snapshot/source",
                "resume_checkpoint_ref": "artifact://checkpoint/source",
            }
            await session.commit()

            result = await service.create_failed_step_resume_execution(
                created,
                resume_checkpoint_ref=None,
                idempotency_key="resume-mm-643",
                checkpoint_payload=_checkpoint_payload(
                    workflow_id=created.workflow_id,
                    run_id=created.run_id,
                ),
            )
            resumed = await service.describe_execution(result["execution"]["workflowId"])

    task_payload = resumed.parameters["task"]
    assert task_payload["recovery"] == {
        "kind": "resume_from_failed_step",
        "sourceWorkflowId": created.workflow_id,
        "sourceRunId": created.run_id,
    }
    assert task_payload["resume"] == {
        "kind": "resume_from_failed_step",
        "sourceWorkflowId": created.workflow_id,
        "sourceRunId": created.run_id,
        "failedStepId": "implement",
        "failedStepAttempt": 1,
        "resumeCheckpointRef": "artifact://checkpoint/source",
        "taskInputSnapshotRef": "artifact://snapshot/source",
        "planRef": "artifact://plan/source",
        "planDigest": "sha256:plan",
    }
    assert "taskRunId" not in resumed.parameters
