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


async def test_generic_rerun_does_not_carry_resume_reference_fields(
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
                    "resumeSource": {
                        "sourceWorkflowId": "mm:old",
                        "sourceRunId": "run-old",
                    },
                    "resumeCheckpointRef": "artifact://checkpoint/old",
                    "preservedSteps": [{"logicalStepId": "plan"}],
                    "completedSteps": [{"logicalStepId": "plan"}],
                    "task": {
                        "title": "Resume source",
                        "instructions": "Original",
                        "recovery": {
                            "kind": "resume_from_failed_step",
                            "sourceWorkflowId": "mm:old",
                            "sourceRunId": "run-old",
                        },
                        "resume": {
                            "kind": "resume_from_failed_step",
                            "sourceWorkflowId": "mm:old",
                            "sourceRunId": "run-old",
                            "failedStepId": "implement",
                            "resumeCheckpointRef": "artifact://checkpoint/old",
                            "taskInputSnapshotRef": "artifact://snapshot/old",
                        },
                    },
                },
                idempotency_key=None,
            )
            created.state = MoonMindWorkflowState.FAILED
            created.close_status = TemporalExecutionCloseStatus.FAILED
            await session.commit()
            source_workflow_id = created.workflow_id
            source_run_id = created.run_id

            result = await service.update_execution(
                workflow_id=created.workflow_id,
                update_name="RequestRerun",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                parameters_patch=None,
                title=None,
                new_manifest_artifact_ref=None,
                mode=None,
                max_concurrency=None,
                node_ids=None,
                idempotency_key="rerun-mm-643",
            )
            rerun = await service.describe_execution(created.workflow_id)

    assert result["continue_as_new_cause"] == "manual_rerun"
    assert "resumeSource" not in rerun.parameters
    assert "resumeCheckpointRef" not in rerun.parameters
    assert "preservedSteps" not in rerun.parameters
    assert "completedSteps" not in rerun.parameters
    assert rerun.parameters["task"] == {
        "title": "Resume source",
        "instructions": "Original",
        "recovery": {
            "kind": "exact_full_rerun",
            "sourceWorkflowId": source_workflow_id,
            "sourceRunId": source_run_id,
        },
    }


async def test_edited_full_retry_does_not_carry_resume_reference_fields(
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
                    "resumeSource": {
                        "sourceWorkflowId": "mm:old",
                        "sourceRunId": "run-old",
                    },
                    "resumeCheckpointRef": "artifact://checkpoint/old",
                    "preservedSteps": [{"logicalStepId": "plan"}],
                    "completedSteps": [{"logicalStepId": "plan"}],
                    "task": {
                        "title": "Resume source",
                        "instructions": "Original",
                        "recovery": {
                            "kind": "resume_from_failed_step",
                            "sourceWorkflowId": "mm:old",
                            "sourceRunId": "run-old",
                        },
                        "resume": {
                            "kind": "resume_from_failed_step",
                            "sourceWorkflowId": "mm:old",
                            "sourceRunId": "run-old",
                            "failedStepId": "implement",
                            "resumeCheckpointRef": "artifact://checkpoint/old",
                            "taskInputSnapshotRef": "artifact://snapshot/old",
                        },
                    },
                },
                idempotency_key=None,
            )

            result = await service.update_execution(
                workflow_id=created.workflow_id,
                update_name="UpdateInputs",
                input_artifact_ref=None,
                plan_artifact_ref="artifact://plan/replacement",
                parameters_patch=None,
                title=None,
                new_manifest_artifact_ref=None,
                mode=None,
                max_concurrency=None,
                node_ids=None,
                idempotency_key="edited-retry-mm-643",
            )
            edited_retry = await service.describe_execution(created.workflow_id)

    assert result["continue_as_new_cause"] == "major_reconfiguration"
    assert "resumeSource" not in edited_retry.parameters
    assert "resumeCheckpointRef" not in edited_retry.parameters
    assert "preservedSteps" not in edited_retry.parameters
    assert "completedSteps" not in edited_retry.parameters
    assert edited_retry.parameters["task"] == {
        "title": "Resume source",
        "instructions": "Original",
    }
