"""Real UserWorkflow Update/query, adapter, service and persisted control evidence."""
from __future__ import annotations

import asyncio
import shutil
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from temporalio.common import SearchAttributeKey, SearchAttributePair, TypedSearchAttributes
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.core.sync import sync_execution_projection
from api_service.db.models import Base, TemporalExecutionCanonicalRecord, TemporalExecutionRecord, TemporalWorkflowType
from api_service.services.system_operations import SystemOperationsService, WorkerOperationCommand
from moonmind.workflows.temporal import client as client_module
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.service import TemporalExecutionService
from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow
from tests.helpers.temporal_visibility import register_deployment_search_attributes


@pytest.mark.integration
@pytest.mark.integration_ci
@pytest.mark.asyncio
async def test_control_intent_survives_service_restart_and_confirms_real_safe_point(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/controls.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    queue = f"control-boundary-{uuid4()}"
    monkeypatch.setattr(client_module, "_MOONMIND_TASK_QUEUES", (queue,))
    try:
        async with await WorkflowEnvironment.start_local(dev_server_existing_path=shutil.which("temporal")) as env:
            await register_deployment_search_attributes(env)
            async with Worker(env.client, task_queue=queue, workflows=[MoonMindUserWorkflow], workflow_runner=UnsandboxedWorkflowRunner()):
                handle = await env.client.start_workflow(
                    MoonMindUserWorkflow.run,
                    {"workflow_type": "MoonMind.UserWorkflow", "initial_parameters": {},
                     "scheduled_for": ((await env.get_current_time()) + timedelta(days=30)).isoformat()},
                    id=f"control-{uuid4()}", task_queue=queue,
                    search_attributes=TypedSearchAttributes([
                        SearchAttributePair(SearchAttributeKey.for_keyword("mm_owner_type"), "user"),
                        SearchAttributePair(SearchAttributeKey.for_keyword("mm_owner_id"), str(uuid4())),
                    ]),
                )
                try:
                    desc = await handle.describe()
                    async with sessions() as session:
                        session.add(TemporalExecutionCanonicalRecord(
                            workflow_id=desc.id, run_id=desc.run_id,
                            workflow_type=TemporalWorkflowType.USER_WORKFLOW, entry="user_workflow",
                            parameters={"targetRuntime": "codex_cli"},
                        ))
                        await session.commit()
                        projection = await sync_execution_projection(session, desc)
                        await session.commit()
                        assert projection.workflow_id == handle.id
                        assert projection.run_id == desc.run_id
                        assert projection.parameters["targetRuntime"] == "codex_cli"

                    command = WorkerOperationCommand(action="pause", mode="quiesce", reason="boundary", confirmation="pause", idempotencyKey=str(uuid4()))
                    # No transport available: the actual DB persists intent before fan-out.
                    async with sessions() as session:
                        pending = await SystemOperationsService(session).submit(command, actor_user_id=None)
                        assert pending.control.status == "requested"
                    # A new service/session continues the persisted request with the actual client.
                    async with sessions() as session:
                        temporal = TemporalExecutionService(session, client_adapter=TemporalClientAdapter(env.client))
                        service = SystemOperationsService(session, temporal_service=temporal)
                        for _ in range(30):
                            paused = await service.snapshot()
                            if paused.control.status == "succeeded":
                                break
                            await asyncio.sleep(0.05)
                        assert paused.control.enumerated
                        assert [target.workflow_id for target in paused.control.targets] == [handle.id]
                        target = paused.control.targets[0]
                        assert target.run_id == desc.run_id
                        assert target.state == "safe_point", paused.control.model_dump()
                        assert (await handle.query("control_state"))["safePoint"]
                        again = await service.submit(command, actor_user_id=None)
                        assert again.control.targets[0].update_id == target.update_id
                        resumed = await service.submit(WorkerOperationCommand(action="resume", reason="boundary", idempotencyKey=str(uuid4())), actor_user_id=None)
                        assert resumed.control.targets[0].state == "resumed", resumed.control.model_dump()
                        # A delayed older system request cannot re-pause the new generation.
                        await handle.execute_update("Pause", {"controlGeneration": paused.control.generation})
                        assert (await handle.query("control_state"))["resumed"]
                        # Previous serialized no-argument updates remain valid.
                        await handle.execute_update("Pause")
                        await handle.execute_update("Resume")
                        stored = await session.get(TemporalExecutionRecord, handle.id)
                        assert stored.run_id == desc.run_id
                    history = await handle.fetch_history()
                    await Replayer(workflows=[MoonMindUserWorkflow], workflow_runner=UnsandboxedWorkflowRunner()).replay_workflow(history)
                finally:
                    await handle.terminate(reason="Hermetic boundary test complete")
    finally:
        await engine.dispose()
