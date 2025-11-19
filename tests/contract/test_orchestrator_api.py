from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db import base as db_base
from api_service.db.models import (
    Base,
    OrchestratorActionPlan,
    OrchestratorPlanStep,
    OrchestratorPlanStepStatus,
    OrchestratorRun,
    OrchestratorRunArtifact,
    OrchestratorRunArtifactType,
    OrchestratorRunPriority,
    OrchestratorRunStatus,
    OrchestratorTaskState,
)
from api_service.main import app
from moonmind.config.settings import settings
from moonmind.schemas.workflow_models import (
    OrchestratorArtifactListResponse,
    OrchestratorRunDetailModel,
    OrchestratorRunListResponse,
)
from moonmind.workflows.speckit_celery import models as workflow_models


@pytest.mark.asyncio
async def test_orchestrator_visibility_contract(tmp_path) -> None:
    """Exercise orchestrator visibility endpoints against the contract."""

    db_url = f"sqlite+aiosqlite:///{tmp_path}/orchestrator_contract.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )

    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    plan_steps = [
        {"name": OrchestratorPlanStep.ANALYZE.value},
        {"name": OrchestratorPlanStep.PATCH.value},
        {"name": OrchestratorPlanStep.BUILD.value},
        {"name": OrchestratorPlanStep.VERIFY.value},
    ]

    async with db_base.async_session_maker() as session:
        plan = OrchestratorActionPlan(
            steps=plan_steps,
            service_context={"service": "api"},
        )
        session.add(plan)
        await session.flush()

        now = datetime.now(timezone.utc)
        run = OrchestratorRun(
            instruction="Fix dependency",
            target_service="api",
            priority=OrchestratorRunPriority.HIGH,
            status=OrchestratorRunStatus.SUCCEEDED,
            queued_at=now,
            started_at=now,
            completed_at=now,
            metrics_snapshot={
                "run": {"status": OrchestratorRunStatus.SUCCEEDED.value},
                "steps": {"verify": {"status": "succeeded"}},
            },
        )
        run.action_plan = plan
        session.add(run)
        await session.flush()

        artifact = OrchestratorRunArtifact(
            run_id=run.id,
            artifact_type=OrchestratorRunArtifactType.VERIFY_LOG,
            path="verify.log",
        )
        session.add(artifact)
        await session.flush()

        task_state = workflow_models.SpecWorkflowTaskState(
            id=uuid4(),
            workflow_run_id=None,
            orchestrator_run_id=run.id,
            task_name="verify",
            status=workflow_models.SpecWorkflowTaskStatus.SUCCEEDED,
            attempt=1,
            plan_step=OrchestratorPlanStep.VERIFY,
            plan_step_status=OrchestratorPlanStepStatus.SUCCEEDED,
            celery_state=OrchestratorTaskState.SUCCESS,
            celery_task_id="verify-1",
            message="Verify completed",
            artifact_paths=[str(artifact.id)],
            started_at=now,
            finished_at=now,
        )
        session.add(task_state)
        await session.commit()
        run_id = run.id
        artifact_id = artifact.id

    app.state.settings = settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        list_response = await client.get("/orchestrator/runs")
        assert list_response.status_code == 200
        collection = OrchestratorRunListResponse.model_validate(list_response.json())
        assert collection.runs
        assert collection.runs[0].run_id == run_id

        filtered = await client.get(
            "/orchestrator/runs",
            params={
                "status": OrchestratorRunStatus.SUCCEEDED.value,
                "service": "api",
                "limit": 5,
                "offset": 0,
            },
        )
        assert filtered.status_code == 200
        filtered_model = OrchestratorRunListResponse.model_validate(filtered.json())
        assert filtered_model.runs
        assert filtered_model.runs[0].target_service == "api"

        detail_response = await client.get(f"/orchestrator/runs/{run_id}")
        assert detail_response.status_code == 200
        detail_model = OrchestratorRunDetailModel.model_validate(detail_response.json())
        assert detail_model.action_plan is not None
        assert (
            detail_model.steps
            and detail_model.steps[0].name == OrchestratorPlanStep.VERIFY
        )
        assert detail_model.metrics_snapshot is not None

        artifacts_response = await client.get(f"/orchestrator/runs/{run_id}/artifacts")
        assert artifacts_response.status_code == 200
        artifacts_model = OrchestratorArtifactListResponse.model_validate(
            artifacts_response.json()
        )
        assert artifacts_model.artifacts
        assert artifacts_model.artifacts[0].artifact_id == artifact_id
