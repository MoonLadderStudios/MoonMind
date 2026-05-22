"""Hermetic integration coverage for MM-645 exact full rerun workflow."""

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
    url = f"sqlite+aiosqlite:///{tmp_path}/exact_full_rerun_workflow.db"
    engine = create_async_engine(url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield maker
    finally:
        await engine.dispose()


async def test_failed_execution_direct_rerun_creates_exact_full_rerun_from_original_inputs(
    tmp_path: Path,
) -> None:
    async with _db(tmp_path) as maker:
        async with maker() as session:
            service = TemporalExecutionService(session, client_adapter=AsyncMock())
            source = await service.create_execution(
                workflow_type="MoonMind.Run",
                owner_id=uuid4(),
                title="MM-645 failed source",
                input_artifact_ref="artifact://input/source",
                plan_artifact_ref="artifact://plan/source",
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                    "repository": "MoonLadderStudios/MoonMind",
                    "taskRunId": "old-task-run",
                    "resumeSource": {"workflowId": "mm:old", "runId": "run-old"},
                    "resumeCheckpointRef": "artifact://checkpoint/old",
                    "preservedSteps": [{"logicalStepId": "plan"}],
                    "completedSteps": [{"logicalStepId": "plan"}],
                    "task": {
                        "title": "Original task",
                        "instructions": "Run the original task input unchanged.",
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
                summary="failed before exact rerun",
            )
            source.memo = {
                **dict(source.memo or {}),
                "task_input_snapshot_ref": "artifact://snapshot/source",
                "task_input_snapshot_version": "2026-05-13T00:00:00Z",
            }
            source.artifact_refs = [
                "artifact://input/source",
                "artifact://plan/source",
                "artifact://snapshot/source",
            ]
            await session.commit()
            await session.refresh(source)

            response = await service.update_execution(
                workflow_id=source.workflow_id,
                update_name="RequestRerun",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                parameters_patch=None,
                title=None,
                new_manifest_artifact_ref=None,
                mode=None,
                max_concurrency=None,
                node_ids=None,
                idempotency_key="exact-full-rerun-mm-645",
            )
            source_after_rerun = await service.describe_execution(source.workflow_id)
            rerun = await service.describe_execution(response["workflow_id"])

    assert response["continue_as_new_cause"] == "manual_rerun"
    assert rerun.workflow_id != source_after_rerun.workflow_id
    assert source_after_rerun.state is MoonMindWorkflowState.FAILED
    assert source_after_rerun.close_status is TemporalExecutionCloseStatus.FAILED

    assert rerun.input_ref == "artifact://input/source"
    assert rerun.plan_ref == "artifact://plan/source"
    assert rerun.parameters["repository"] == "MoonLadderStudios/MoonMind"
    assert rerun.parameters["rerunSource"] == {
        "workflowId": source_after_rerun.workflow_id,
        "runId": source_after_rerun.run_id,
    }
    assert rerun.parameters["task"] == {
        "title": "Original task",
        "instructions": "Run the original task input unchanged.",
        "recovery": {
            "kind": "exact_full_rerun",
            "sourceWorkflowId": source_after_rerun.workflow_id,
            "sourceRunId": source_after_rerun.run_id,
        },
    }
    assert "taskRunId" not in rerun.parameters
    assert "resumeSource" not in rerun.parameters
    assert "resumeCheckpointRef" not in rerun.parameters
    assert "preservedSteps" not in rerun.parameters
    assert "completedSteps" not in rerun.parameters
    assert "resume" not in rerun.parameters["task"]
