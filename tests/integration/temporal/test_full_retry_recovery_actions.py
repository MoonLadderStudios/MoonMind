"""Hermetic integration coverage for MM-632 full retry recovery actions."""

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
    url = f"sqlite+aiosqlite:///{tmp_path}/full_retry_recovery_actions.db"
    engine = create_async_engine(url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield maker
    finally:
        await engine.dispose()


async def test_exact_rerun_creates_fresh_execution_without_resume_progress(
    tmp_path: Path,
) -> None:
    async with _db(tmp_path) as maker:
        async with maker() as session:
            service = TemporalExecutionService(session, client_adapter=AsyncMock())
            created = await service.create_execution(
                workflow_type="MoonMind.Run",
                owner_id=uuid4(),
                title="Failed task",
                input_artifact_ref="artifact://input/original",
                plan_artifact_ref="artifact://plan/original",
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                    "repository": "MoonLadderStudios/MoonMind",
                    "taskRunId": "old-task-run",
                    "resumeSource": {"workflowId": "mm:failed", "runId": "run-old"},
                    "resumeCheckpointRef": "artifact://checkpoint/old",
                    "preservedSteps": [{"id": "step-1"}],
                    "completedSteps": [{"id": "step-0"}],
                },
                idempotency_key=None,
            )
            await service.cancel_execution(
                workflow_id=created.workflow_id,
                reason="failed before retry",
                graceful=True,
            )

            response = await service.update_execution(
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
                idempotency_key="exact-rerun",
            )

            source = await service.describe_execution(created.workflow_id)
            rerun = await service.describe_execution(response["workflow_id"])

    assert source.state is MoonMindWorkflowState.CANCELED
    assert source.close_status is TemporalExecutionCloseStatus.CANCELED
    assert rerun.input_ref == "artifact://input/original"
    assert rerun.plan_ref == "artifact://plan/original"
    assert rerun.parameters["repository"] == "MoonLadderStudios/MoonMind"
    assert rerun.parameters["rerunSource"] == {
        "workflowId": source.workflow_id,
        "runId": source.run_id,
    }
    assert "taskRunId" not in rerun.parameters
    assert "resumeSource" not in rerun.parameters
    assert "resumeCheckpointRef" not in rerun.parameters
    assert "preservedSteps" not in rerun.parameters
    assert "completedSteps" not in rerun.parameters
