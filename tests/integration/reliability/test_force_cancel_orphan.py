"""Force cancel crosses HTTP, storage and real Temporal without a live worker."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from temporalio import workflow
from temporalio.client import WorkflowExecutionStatus
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.api.routers import executions
from api_service.core.sync import sync_execution_projection
from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionCloseStatus,
    TemporalExecutionProjectionSyncState,
    TemporalExecutionRecord,
)
from moonmind.config.settings import settings
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.service import TemporalExecutionService
from tests.integration.reliability.test_release_routing_journey import connect
from tests.unit.workflows.temporal.test_temporal_service import temporal_db

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.reliability_journey]


@workflow.defn(name="MoonMind.UserWorkflow")
class OrphanCancelWorkflow:
    @workflow.run
    async def run(self, mode: str) -> str | None:
        if mode == "complete":
            return "finished"
        if mode == "parent":
            await workflow.execute_child_workflow(
                OrphanCancelWorkflow.run,
                "wait",
                id=workflow.info().workflow_id + ":child",
                parent_close_policy=workflow.ParentClosePolicy.TERMINATE,
            )
        await workflow.wait_condition(lambda: False)
        return None

    @workflow.query
    def ready(self):
        return True


async def wait_ready(handle):
    async with asyncio.timeout(15):
        while True:
            try:
                if await handle.query("ready", rpc_timeout=timedelta(seconds=1)):
                    return
            except Exception:
                # Queries can race the first workflow task; the outer deadline
                # bounds these readiness retries and fails an unready workflow.
                pass
            await asyncio.sleep(0.05)


def api(service, monkeypatch, *, owner="owner"):
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    app = FastAPI()
    app.include_router(executions.router)
    app.dependency_overrides[executions._get_service] = lambda: service
    user = SimpleNamespace(id=owner, is_superuser=False)
    routes = list(app.routes)
    while routes:
        route = routes.pop()
        routes.extend(getattr(getattr(route, "original_router", route), "routes", ()))
        for dependency in getattr(getattr(route, "dependant", None), "dependencies", ()):
            if getattr(dependency.call, "__name__", "") in {
                "_strict_current_user", "_optional_current_user",
            }:
                app.dependency_overrides[dependency.call] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test")


async def add_record(session, workflow_id, run_id, *, orphan=False, canceled=False):
    cls = TemporalExecutionRecord if orphan else TemporalExecutionCanonicalRecord
    record = cls(
        workflow_id=workflow_id, run_id=run_id, namespace="default",
        workflow_type="MoonMind.UserWorkflow", owner_id="owner", owner_type="user",
        entry="user_workflow", state="canceled" if canceled else "executing",
        close_status="canceled" if canceled else None,
        closed_at=datetime.now(UTC) if canceled else None,
        memo={"summary": "Canceled by user." if canceled else "Agent is running."},
        parameters={}, artifact_refs=[], search_attributes={},
    )
    if orphan:
        record.sync_state = TemporalExecutionProjectionSyncState.ORPHANED
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def test_force_cancel_stale_terminal_parent_without_worker(tmp_path, monkeypatch):
    client = await connect()
    queue = "force-cancel-" + uuid4().hex
    async with Worker(
        client, task_queue=queue, workflows=[OrphanCancelWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        parent = await client.start_workflow(
            OrphanCancelWorkflow.run, "parent", id=queue, task_queue=queue,
            execution_timeout=timedelta(minutes=2),
        )
        child = client.get_workflow_handle(queue + ":child")
        await wait_ready(child)
    # Both worker and runtime can be gone. Recreate the escaped stale local
    # CANCELED projection while Temporal only retained the graceful request.
    await parent.cancel()
    assert (await parent.describe()).status == WorkflowExecutionStatus.RUNNING
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path / "sessions"))
    async with temporal_db(tmp_path) as session:
        record = await add_record(session, parent.id, parent.first_execution_run_id, canceled=True)
        service = TemporalExecutionService(session, client_adapter=TemporalClientAdapter(client))
        monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
        capabilities = executions._build_action_capabilities(record)
        assert not capabilities.can_cancel
        assert capabilities.can_force_cancel
        async with api(service, monkeypatch) as http:
            response = await http.post(f"/api/executions/{parent.id}/cancel", json={"graceful": False})
            assert response.status_code == 202, response.text
            assert response.json()["closeStatus"] == "terminated"
            assert (await parent.describe()).status == WorkflowExecutionStatus.TERMINATED
            async with asyncio.timeout(15):
                while (await child.describe()).status != WorkflowExecutionStatus.TERMINATED:
                    await asyncio.sleep(0.05)
            # A retry after Temporal closure/DB failure is safe and reconciles.
            response = await http.post(f"/api/executions/{parent.id}/cancel", json={"graceful": False})
            assert response.status_code == 202, response.text
            assert response.json()["closeStatus"] == "terminated"
        await sync_execution_projection(session, await parent.describe())
        await session.commit()
        session.expire_all()
        for cls in (TemporalExecutionCanonicalRecord, TemporalExecutionRecord):
            persisted = await session.get(cls, parent.id)
            assert persisted.close_status == TemporalExecutionCloseStatus.TERMINATED
            assert persisted.closed_at is not None
        for handle in (parent, child):
            await Replayer(
                workflows=[OrphanCancelWorkflow], workflow_runner=UnsandboxedWorkflowRunner(),
            ).replay_workflow(await handle.fetch_history())


@pytest.mark.parametrize("orphan", [False, True])
async def test_force_cancel_absent_run_preserves_ownership(tmp_path, monkeypatch, orphan):
    client = await connect()
    async with temporal_db(tmp_path) as session:
        record = await add_record(session, "missing-" + uuid4().hex, str(uuid4()), orphan=orphan)
        service = TemporalExecutionService(session, client_adapter=TemporalClientAdapter(client))
        cleanup = AsyncMock(wraps=service._best_effort_terminate_workflow_scoped_managed_sessions)
        monkeypatch.setattr(service, "_best_effort_terminate_workflow_scoped_managed_sessions", cleanup)
        async with api(service, monkeypatch, owner="different-owner") as http:
            response = await http.post(f"/api/executions/{record.workflow_id}/cancel", json={"graceful": False})
            assert response.status_code == 404
        assert record.state == MoonMindWorkflowState.EXECUTING
        async with api(service, monkeypatch) as http:
            response = await http.post(f"/api/executions/{record.workflow_id}/cancel", json={"graceful": False})
            assert response.status_code == 202, response.text
            assert response.json()["closeStatus"] == "canceled"
            assert "Temporal execution not found" in response.json()["summary"]
        await session.refresh(record)
        assert record.state == MoonMindWorkflowState.CANCELED
        assert record.closed_at is not None
        cleanup.assert_not_awaited()


async def test_force_cancel_preserves_completed_run_and_does_not_kill_reused_id(tmp_path, monkeypatch):
    client = await connect()
    queue = "force-completed-" + uuid4().hex
    async with Worker(
        client, task_queue=queue, workflows=[OrphanCancelWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        completed = await client.start_workflow(
            OrphanCancelWorkflow.run, "complete", id=queue, task_queue=queue,
        )
        assert await completed.result() == "finished"
    replacement = await client.start_workflow(
        OrphanCancelWorkflow.run, "wait", id=queue, task_queue=queue,
        execution_timeout=timedelta(minutes=2),
    )
    try:
        async with temporal_db(tmp_path) as session:
            record = await add_record(session, queue, completed.first_execution_run_id)
            service = TemporalExecutionService(session, client_adapter=TemporalClientAdapter(client))
            cleanup = AsyncMock(wraps=service._best_effort_terminate_workflow_scoped_managed_sessions)
            monkeypatch.setattr(service, "_best_effort_terminate_workflow_scoped_managed_sessions", cleanup)
            async with api(service, monkeypatch) as http:
                response = await http.post(f"/api/executions/{queue}/cancel", json={"graceful": False})
                assert response.status_code == 202, response.text
                assert response.json()["state"] == "completed"
            assert record.close_status == TemporalExecutionCloseStatus.COMPLETED
            assert (await replacement.describe()).status == WorkflowExecutionStatus.RUNNING
            cleanup.assert_not_awaited()
    finally:
        await replacement.terminate(reason="Isolated test cleanup")
