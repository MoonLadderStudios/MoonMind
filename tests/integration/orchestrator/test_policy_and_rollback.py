from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db import base as db_base
from api_service.db import models as db_models
from api_service.main import app
from moonmind.config.settings import settings
from moonmind.schemas.workflow_models import (
    OrchestratorApprovalRequest,
    OrchestratorApprovalStatus,
    OrchestratorCreateRunRequest,
    OrchestratorRunStatus,
)
from moonmind.workflows.orchestrator.action_plan import generate_action_plan
from moonmind.workflows.orchestrator.services import OrchestratorService
from moonmind.workflows.orchestrator.storage import ArtifactStorage
from moonmind.workflows.orchestrator import tasks as orchestrator_tasks
from moonmind.workflows.orchestrator.repositories import OrchestratorRepository
from moonmind.workflows.orchestrator.service_profiles import ServiceProfile


@pytest.mark.asyncio
async def test_protected_service_requires_approval(tmp_path: Path, monkeypatch) -> None:
    """Runs targeting protected services should pause until approval arrives."""

    db_url = f"sqlite+aiosqlite:///{tmp_path}/orch_policy.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )

    async with db_base.engine.begin() as conn:
        await conn.run_sync(db_models.Base.metadata.create_all)

    async with db_base.async_session_maker() as session:
        gate = db_models.ApprovalGate(
            service_name="api",
            requirement=db_models.OrchestratorApprovalRequirement.NONE,
            approver_roles=["sre"],
            valid_for_minutes=30,
        )
        gate.requirement = db_models.OrchestratorApprovalRequirement.PRE_RUN
        session.add(gate)
        await session.commit()

    artifact_override = f"test-artifacts/{uuid4()}"
    monkeypatch.setenv("ORCHESTRATOR_ARTIFACT_ROOT", artifact_override)
    app.state.settings = settings
    enqueued: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        "api_service.api.routers.orchestrator.enqueue_action_plan",
        lambda *args, **kwargs: enqueued.append((args, kwargs)),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        create_payload = OrchestratorCreateRunRequest(
            instruction="Fix dependency",
            target_service="api",
            approval_token=None,
        )
        created = await client.post(
            "/orchestrator/runs",
            json=create_payload.model_dump(mode="json"),
        )
        assert created.status_code == 202
        run_summary = created.json()
        run_id = run_summary["runId"]
        assert run_summary["status"] == OrchestratorRunStatus.AWAITING_APPROVAL.value
        assert run_summary["approvalRequired"] is True
        assert run_summary["approvalStatus"] == OrchestratorApprovalStatus.AWAITING.value

        approval_payload = OrchestratorApprovalRequest(
            approver={"id": "user-1", "role": "sre"},
            token="signed-token",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        approval_response = await client.post(
            f"/orchestrator/runs/{run_id}/approvals",
            json=approval_payload.model_dump(mode="json"),
        )
        assert approval_response.status_code == 200
        approval_body = approval_response.json()
        assert approval_body["status"] == OrchestratorRunStatus.PENDING.value
        assert (
            approval_body["approvalStatus"]
            == OrchestratorApprovalStatus.GRANTED.value
        )

    async with db_base.async_session_maker() as session:
        run = await session.get(db_models.OrchestratorRun, UUID(run_id))
        assert run is not None
        assert run.status == db_models.OrchestratorRunStatus.PENDING
        assert run.approval_token is not None
        assert run.approval_gate_id is not None
    assert enqueued


@pytest.mark.asyncio
async def test_verify_failure_triggers_rollback(tmp_path: Path, monkeypatch) -> None:
    """A failed verify step should be followed by rollback execution."""

    db_url = f"sqlite+aiosqlite:///{tmp_path}/orch_retry.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )

    async with db_base.engine.begin() as conn:
        await conn.run_sync(db_models.Base.metadata.create_all)

    artifact_override = f"test-artifacts/{uuid4()}"
    monkeypatch.setenv("ORCHESTRATOR_ARTIFACT_ROOT", artifact_override)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    profile = ServiceProfile(
        key="api",
        compose_service="api",
        workspace_path=repo_path,
        allowlist_globs=("*.txt",),
        healthcheck=None,
    )
    plan = generate_action_plan("Exercise rollback", profile)
    for step in plan.steps:
        if step.name == db_models.OrchestratorPlanStep.VERIFY:
            step.parameters["healthcheck"] = {
                "url": "http://127.0.0.1:9",
                "timeoutSeconds": 1,
                "intervalSeconds": 0.1,
                "expectedStatus": 200,
            }
        if step.name == db_models.OrchestratorPlanStep.ROLLBACK:
            step.parameters["strategies"] = [
                {"type": "noop", "commands": [["echo", "rollback"]]},
            ]
            step.parameters.pop("service", None)
            step.parameters.pop("logArtifact", None)

    async with db_base.async_session_maker() as session:
        service = OrchestratorService(
            repository=OrchestratorRepository(session),
            artifact_storage=ArtifactStorage(tmp_path / "artifacts"),
        )
        run = await service.create_run(plan, approval_token=None, priority=None)
        run_id = run.id

    with pytest.raises(Exception):
        await orchestrator_tasks._execute_plan_step_async(
            run_id, db_models.OrchestratorPlanStep.VERIFY.value
        )

    await orchestrator_tasks._execute_plan_step_async(
        run_id, db_models.OrchestratorPlanStep.ROLLBACK.value
    )

    async with db_base.async_session_maker() as session:
        repo = OrchestratorRepository(session)
        refreshed = await repo.get_run(run_id, with_relations=True)
        assert refreshed is not None
        assert refreshed.status == db_models.OrchestratorRunStatus.ROLLED_BACK
        artifacts = await repo.list_artifacts(run_id)
        assert artifacts
        assert any(
            artifact.artifact_type
            == db_models.OrchestratorRunArtifactType.ROLLBACK_LOG
            for artifact in artifacts
        )
