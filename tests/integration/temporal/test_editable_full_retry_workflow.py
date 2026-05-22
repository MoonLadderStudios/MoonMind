"""Hermetic integration coverage for MM-644 editable full retry workflow."""

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
    url = f"sqlite+aiosqlite:///{tmp_path}/editable_full_retry_workflow.db"
    engine = create_async_engine(url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield maker
    finally:
        await engine.dispose()


async def test_changed_edited_full_retry_creates_fresh_execution_with_provenance_and_no_resume_carryover(
    tmp_path: Path,
) -> None:
    async with _db(tmp_path) as maker:
        async with maker() as session:
            service = TemporalExecutionService(session, client_adapter=AsyncMock())
            source = await service.create_execution(
                workflow_type="MoonMind.Run",
                owner_id=uuid4(),
                title="MM-644 failed source",
                input_artifact_ref="artifact://input/source",
                plan_artifact_ref="artifact://plan/source",
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                    "repository": "MoonLadderStudios/MoonMind",
                    "resumeSource": {"workflowId": "mm:old", "runId": "run-old"},
                    "resumeCheckpointRef": "artifact://checkpoint/old",
                    "preservedSteps": [{"logicalStepId": "plan"}],
                    "completedSteps": [{"logicalStepId": "plan"}],
                    "task": {
                        "title": "Original failed task",
                        "instructions": "Original instructions.",
                        "recovery": {
                            "kind": "recover_from_failed_step",
                            "sourceWorkflowId": "mm:old",
                            "sourceRunId": "run-old",
                        },
                        "resume": {
                            "kind": "recover_from_failed_step",
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
            source = await service.record_terminal_state(
                workflow_id=source.workflow_id,
                state="failed",
                close_status="failed",
                summary="failed before edited retry",
            )
            source.memo = {
                **dict(source.memo or {}),
                "task_input_snapshot_ref": "artifact://snapshot/source",
                "resume_checkpoint_ref": "artifact://checkpoint/old",
            }
            source.artifact_refs = [
                "artifact://input/source",
                "artifact://snapshot/source",
                "artifact://checkpoint/old",
            ]
            await session.commit()
            await session.refresh(source)

            result = await service.update_execution(
                workflow_id=source.workflow_id,
                update_name="RequestRerun",
                input_artifact_ref="artifact://input/edited",
                plan_artifact_ref=None,
                parameters_patch={
                    "task": {
                        "title": "Original failed task",
                        "instructions": "MM-644 edited retry instructions.",
                        "recovery": {
                            "kind": "edited_full_retry",
                            "sourceWorkflowId": source.workflow_id,
                            "sourceRunId": source.run_id,
                        },
                    }
                },
                title=None,
                new_manifest_artifact_ref=None,
                mode=None,
                max_concurrency=None,
                node_ids=None,
                idempotency_key="edited-full-retry-mm-644",
            )
            assert "workflow_id" in result, result
            refreshed_source = await service.describe_execution(source.workflow_id)
            edited_retry = await service.describe_execution(result["workflow_id"])

    assert result["continue_as_new_cause"] == "manual_rerun"
    assert edited_retry.workflow_id != refreshed_source.workflow_id
    assert refreshed_source.state is MoonMindWorkflowState.FAILED
    assert refreshed_source.close_status is TemporalExecutionCloseStatus.FAILED
    assert refreshed_source.parameters["task"]["instructions"] == "Original instructions."
    assert refreshed_source.parameters["resumeCheckpointRef"] == "artifact://checkpoint/old"

    assert edited_retry.input_ref == "artifact://input/edited"
    assert edited_retry.parameters["rerunSource"] == {
        "workflowId": refreshed_source.workflow_id,
        "runId": refreshed_source.run_id,
    }
    assert edited_retry.parameters["task"]["instructions"] == (
        "MM-644 edited retry instructions."
    )
    assert edited_retry.parameters["task"]["recovery"] == {
        "kind": "edited_full_retry",
        "sourceWorkflowId": refreshed_source.workflow_id,
        "sourceRunId": refreshed_source.run_id,
    }
    assert "resumeSource" not in edited_retry.parameters
    assert "resumeCheckpointRef" not in edited_retry.parameters
    assert "preservedSteps" not in edited_retry.parameters
    assert "completedSteps" not in edited_retry.parameters
    assert "resume" not in edited_retry.parameters["task"]
