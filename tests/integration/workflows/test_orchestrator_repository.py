from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

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
    OrchestratorTaskStep,
    OrchestratorTaskStepStatus,
)
from moonmind.workflows.orchestrator.repositories import OrchestratorRepository
from moonmind.workflows.automation import models as workflow_models


@pytest.mark.asyncio
async def test_list_runs_supports_filters_and_pagination(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/repo.db", future=True)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    plan = OrchestratorActionPlan(
        steps=[{"name": OrchestratorPlanStep.ANALYZE.value}],
        service_context={"service": "api"},
    )

    async with async_session() as session:
        session.add(plan)
        await session.flush()
        now = datetime.now(timezone.utc)
        for idx, status in enumerate(
            [OrchestratorRunStatus.SUCCEEDED, OrchestratorRunStatus.FAILED]
        ):
            run = OrchestratorRun(
                instruction=f"Run {idx}",
                target_service="api" if idx == 0 else "worker",
                priority=OrchestratorRunPriority.NORMAL,
                status=status,
                queued_at=now,
            )
            run.action_plan = plan
            session.add(run)
        await session.commit()

        repo = OrchestratorRepository(session)
        succeeded = await repo.list_runs(status=OrchestratorRunStatus.SUCCEEDED)
        assert len(succeeded) == 1
        assert succeeded[0].status == OrchestratorRunStatus.SUCCEEDED

        worker_runs = await repo.list_runs(target_service="worker")
        assert worker_runs and worker_runs[0].target_service == "worker"

        paginated = await repo.list_runs(limit=1, offset=1)
        assert len(paginated) == 1


@pytest.mark.asyncio
async def test_get_run_with_relations_returns_details(tmp_path) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/repo_details.db", future=True
    )
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        plan = OrchestratorActionPlan(
            steps=[{"name": OrchestratorPlanStep.VERIFY.value}],
            service_context={"service": "api"},
        )
        session.add(plan)
        await session.flush()

        run = OrchestratorRun(
            instruction="Inspect",
            target_service="api",
            queued_at=datetime.now(timezone.utc),
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

        task_state = workflow_models.WorkflowTaskState(
            id=uuid4(),
            orchestrator_run_id=run.id,
            workflow_run_id=None,
            task_name="verify",
            status=workflow_models.WorkflowTaskStatus.SUCCEEDED,
            attempt=1,
            plan_step=OrchestratorPlanStep.VERIFY,
            plan_step_status=OrchestratorPlanStepStatus.SUCCEEDED,
        )
        session.add(task_state)
        await session.commit()

        repo = OrchestratorRepository(session)
        hydrated = await repo.get_run(run.id, with_relations=True)
        assert hydrated is not None
        assert hydrated.action_plan is not None
        assert hydrated.artifacts
        assert hydrated.task_states


@pytest.mark.asyncio
async def test_list_artifacts_orders_by_created(tmp_path) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/repo_artifacts.db", future=True
    )
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        plan = OrchestratorActionPlan(
            steps=[{"name": OrchestratorPlanStep.ANALYZE.value}],
            service_context={"service": "api"},
        )
        session.add(plan)
        await session.flush()
        run = OrchestratorRun(
            instruction="Artifacts",
            target_service="api",
            queued_at=datetime.now(timezone.utc),
        )
        run.action_plan = plan
        session.add(run)
        await session.flush()

        first = OrchestratorRunArtifact(
            run_id=run.id,
            artifact_type=OrchestratorRunArtifactType.PATCH_DIFF,
            path="patch.diff",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        second = OrchestratorRunArtifact(
            run_id=run.id,
            artifact_type=OrchestratorRunArtifactType.VERIFY_LOG,
            path="verify.log",
            created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        session.add_all([first, second])
        await session.commit()

        repo = OrchestratorRepository(session)
        artifacts = await repo.list_artifacts(run.id)
        assert [artifact.path for artifact in artifacts] == ["patch.diff", "verify.log"]


@pytest.mark.asyncio
async def test_update_task_step_state_clears_timestamps(tmp_path) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/task_step_timestamps.db", future=True
    )
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        plan = OrchestratorActionPlan(
            steps=[{"name": OrchestratorPlanStep.ANALYZE.value}],
            service_context={"service": "api"},
        )
        session.add(plan)
        await session.flush()
        run = OrchestratorRun(
            instruction="Timestamps test",
            target_service="api",
            priority=OrchestratorRunPriority.NORMAL,
            queued_at=datetime.now(timezone.utc),
        )
        run.action_plan = plan
        session.add(run)
        await session.flush()

        step = OrchestratorTaskStep(
            task_id=run.id,
            step_id="step-1",
            step_index=0,
            title="analyze",
            instructions="Inspect",
            skill_id="update-moonmind",
            status=OrchestratorTaskStepStatus.QUEUED,
            skill_args={},
            started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            finished_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            attempt=3,
            artifact_refs=[],
        )
        session.add(step)
        await session.commit()

        repo = OrchestratorRepository(session)
        await repo.update_task_step_state(
            step,
            started_at=None,
            finished_at=None,
            status=OrchestratorTaskStepStatus.SUCCEEDED,
        )
        await repo.commit()

        session.expunge_all()
        refreshed = await session.get(OrchestratorTaskStep, step.id)
        assert refreshed is not None
        assert refreshed.started_at is None
        assert refreshed.finished_at is None
