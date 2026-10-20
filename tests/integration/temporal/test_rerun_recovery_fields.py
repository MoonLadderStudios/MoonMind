"""Fresh reruns and edited retries replace the source run's rerun provenance."""

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
    url = f"sqlite+aiosqlite:///{tmp_path}/rerun_recovery_fields.db"
    engine = create_async_engine(url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield maker
    finally:
        await engine.dispose()


async def test_generic_rerun_replaces_source_rerun_provenance(
    tmp_path: Path,
) -> None:
    async with _db(tmp_path) as maker:
        async with maker() as session:
            service = TemporalExecutionService(session, client_adapter=AsyncMock(describe_workflow=AsyncMock(return_value=None)))
            created = await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=uuid4(),
                title="Recovery source",
                input_artifact_ref="artifact://input/source",
                plan_artifact_ref="artifact://plan/source",
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                    "workflow": {
                        "title": "Recovery source",
                        "instructions": "Original",
                        "recovery": {
                            "kind": "exact_full_rerun",
                            "sourceWorkflowId": "mm:old",
                            "sourceRunId": "run-old",
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
                idempotency_key="rerun-mm-643",
            )
            rerun = await service.describe_execution(created.workflow_id)

    assert result["continue_as_new_cause"] == "manual_rerun"
    assert rerun.parameters["workflow"] == {
        "title": "Recovery source",
        "instructions": "Original",
        "recovery": {
            "kind": "exact_full_rerun",
            "sourceWorkflowId": source_workflow_id,
            "sourceRunId": source_run_id,
        },
    }


async def test_edited_full_retry_replaces_source_rerun_provenance(
    tmp_path: Path,
) -> None:
    async with _db(tmp_path) as maker:
        async with maker() as session:
            service = TemporalExecutionService(session, client_adapter=AsyncMock(describe_workflow=AsyncMock(return_value=None)))
            created = await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=uuid4(),
                title="Recovery source",
                input_artifact_ref="artifact://input/source",
                plan_artifact_ref="artifact://plan/source",
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                    "workflow": {
                        "title": "Recovery source",
                        "instructions": "Original",
                        "recovery": {
                            "kind": "exact_full_rerun",
                            "sourceWorkflowId": "mm:old",
                            "sourceRunId": "run-old",
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
                idempotency_key="edited-retry-mm-643",
            )
            edited_retry = await service.describe_execution(created.workflow_id)

    assert result["continue_as_new_cause"] == "major_reconfiguration"
    assert edited_retry.parameters["workflow"] == {
        "title": "Recovery source",
        "instructions": "Original",
    }
