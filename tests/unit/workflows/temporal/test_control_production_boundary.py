"""Real UserWorkflow controls, replay and projection side-effect owners."""
from __future__ import annotations

import asyncio
from datetime import UTC, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.common import SearchAttributeKey, SearchAttributePair, TypedSearchAttributes
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.core.sync import sync_execution_projection
from api_service.db.models import Base, TemporalExecutionCanonicalRecord, TemporalExecutionRecord, TemporalWorkflowType
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.service import TemporalExecutionService
from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow
from tests.helpers.temporal_visibility import register_deployment_search_attributes


@pytest.mark.asyncio
async def test_control_adapter_confirms_actual_workflow_safe_point_and_replays(tmp_path, monkeypatch):
    from moonmind.schemas.workflow_control_models import WorkflowControlBatch, WorkflowControlTarget
    import hashlib
    queue = f"control-boundary-{uuid4()}"
    async with await WorkflowEnvironment.start_time_skipping() as env:
        await register_deployment_search_attributes(env)
        with env.auto_time_skipping_disabled():
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
                    adapter = TemporalClientAdapter(env.client)
                    # Test-server Visibility is unimplemented. Resume the production
                    # batch contract at its persisted-target boundary, using the
                    # actual server run identity; no workflow or client substitutes.
                    for generation, action, desired in ((1, "Pause", "safe_point"), (2, "Resume", "resumed")):
                        request_id = str(uuid4())
                        update_id = hashlib.sha256(f"{request_id}:{action}:{desc.id}:{desc.run_id}".encode()).hexdigest()
                        batch = WorkflowControlBatch(requestId=request_id, action=action, generation=generation, enumerated=True,
                            targets=[WorkflowControlTarget(workflowId=desc.id, runId=desc.run_id, updateId=update_id)])
                        for _ in range(3):
                            batch = await adapter._send_update_to_running_workflows(update_name=action, batch=batch)
                            if batch.targets[0].state == desired:
                                break
                            await asyncio.sleep(0.05)
                        if batch.targets[0].state != desired:
                            print(batch.model_dump_json(), await handle.query("get_status"), await handle.query("control_state"))
                            await handle.get_update_handle(update_id).result(rpc_timeout=timedelta(seconds=2))
                        assert batch.targets[0].state == desired
                        if action == "Pause":
                            await _assert_control_progress_preserves_confirmed_evidence(tmp_path, batch)
                            await _assert_projection_writer_interleaving(tmp_path, monkeypatch, desc, await handle.describe())
                            vanished = await env.client.start_workflow(
                                MoonMindUserWorkflow.run,
                                {"workflow_type": "MoonMind.UserWorkflow", "initial_parameters": {},
                                 "scheduled_for": ((await env.get_current_time()) + timedelta(days=30)).isoformat()},
                                id=str(uuid4()), task_queue=queue,
                                search_attributes=TypedSearchAttributes([
                                    SearchAttributePair(SearchAttributeKey.for_keyword("mm_owner_type"), "user"),
                                    SearchAttributePair(SearchAttributeKey.for_keyword("mm_owner_id"), str(uuid4())),
                                ]),
                            )
                            vanished_desc = await vanished.describe()
                            await vanished.terminate(reason="Target vanishes during control fan-out")
                            mixed = batch.model_copy(deep=True)
                            missing_update = hashlib.sha256(
                                f"{batch.request_id}:Pause:{vanished_desc.id}:{vanished_desc.run_id}".encode()
                            ).hexdigest()
                            mixed.targets.append(WorkflowControlTarget(
                                workflowId=vanished_desc.id, runId=vanished_desc.run_id, updateId=missing_update,
                            ))
                            for _ in range(2):
                                mixed = await adapter.send_batch_pause_update(batch=mixed)
                                assert mixed.status == "partial"
                                assert [target.state for target in mixed.targets] == ["safe_point", "unknown"]
                                assert mixed.targets[0].update_id == batch.targets[0].update_id
                                assert mixed.targets[1].reason == "update_acceptance_unavailable"
                            await _assert_durable_mixed_fanout(tmp_path, mixed, adapter)
                    await handle.execute_update("Pause", {"controlGeneration": 1})
                    assert (await handle.query("control_state"))["resumed"]
                    await handle.execute_update("Pause")
                    await handle.execute_update("Resume")
                    history = await handle.fetch_history()
                    await Replayer(workflows=[MoonMindUserWorkflow], workflow_runner=UnsandboxedWorkflowRunner()).replay_workflow(history)
                finally:
                    if (await handle.describe()).status.name == "RUNNING":
                        await handle.terminate(reason="Hermetic boundary test complete")


async def _assert_projection_writer_interleaving(tmp_path, monkeypatch, old_description, paused_description):
    """Exercise four real writers with actual Temporal descriptions and artifact bytes."""
    from api_service.db import base as db_base
    from api_service.db.models import TemporalExecutionOwnerType
    from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore, DbRuntimeBindingStore
    from moonmind.workflows.temporal.activities.omnigent_session_activities import _project_runtime_binding_to_execution
    from moonmind.workflows.temporal.artifacts import LocalTemporalArtifactStore, TemporalArtifactRepository, TemporalArtifactService
    from moonmind.workflows.temporal.worker_runtime import _persist_child_run_task_input_snapshot
    from tests.unit.omnigent.test_generic_plane_n_way_concurrency import _zen_plan

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/projection.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_base, "async_session_maker", sessions)
    try:
        # The fixture is an immutable plan input. Both the persisted plan and
        # returned runtime-binding identity go through their production owners.
        plan = await DbExecutionPlanStore(sessions).persist(_zen_plan(old_description.id))
        binding_store = DbRuntimeBindingStore(sessions)
        binding = await binding_store.create_initial(execution_plan_ref=plan.planRef, execution_scope_ref=old_description.id, provider_leases={})
        binding_state = await binding_store.get_state(binding.runtimeBindingRef)
        owner_id = paused_description.search_attributes["mm_owner_id"][0]
        async with sessions() as session:
            canonical = TemporalExecutionCanonicalRecord(
                workflow_id=old_description.id, run_id=old_description.run_id,
                workflow_type=TemporalWorkflowType.USER_WORKFLOW, entry="user_workflow",
                owner_type=TemporalExecutionOwnerType.USER, owner_id=owner_id,
                parameters={"targetRuntime": "codex_cli"}, updated_at=old_description.start_time,
            )
            session.add(canonical)
            await session.commit()
            await sync_execution_projection(session, paused_description)
            await session.commit()
            semantic_time = canonical.updated_at
            service = TemporalArtifactService(TemporalArtifactRepository(session), store=LocalTemporalArtifactStore(tmp_path / "artifact-bytes"))
            snapshot = await _persist_child_run_task_input_snapshot(
                session=session, record=canonical,
                parameters={"workflow": {"title": "Scheduled control", "instructions": "Wait for the scheduled boundary"}, "targetRuntime": "codex_cli"},
                artifact_service=service,
            )
            _, contents = await service.read(artifact_id=snapshot, principal=owner_id, allow_restricted_raw=True)
            assert b"Wait for the scheduled boundary" in contents
        await _project_runtime_binding_to_execution(workflow_id=old_description.id, state=binding_state)
        async with sessions() as session:
            # Older lifecycle evidence cannot erase snapshot/binding owner writes.
            await sync_execution_projection(session, old_description)
            canonical = await session.get(TemporalExecutionCanonicalRecord, old_description.id)
            await TemporalExecutionService(session)._upsert_projection_from_source(canonical)
            await session.commit()
            for model in (TemporalExecutionCanonicalRecord, TemporalExecutionRecord):
                record = await session.get(model, old_description.id, populate_existing=True)
                assert record.memo["task_input_snapshot_ref"] == snapshot
                assert record.memo["omnigent_runtime_binding_ref"] == binding.runtimeBindingRef
                assert record.memo["omnigent_runtime_binding_revision"] == binding_state.revision
                assert snapshot in record.artifact_refs
                assert record.updated_at.replace(tzinfo=UTC) == semantic_time.replace(tzinfo=UTC)
            projection = await session.get(TemporalExecutionRecord, old_description.id)
            await session.delete(projection)
            await session.commit()
            repaired = await sync_execution_projection(session, paused_description)
            await session.commit()
            assert repaired.memo["task_input_snapshot_ref"] == snapshot
            assert repaired.memo["omnigent_runtime_binding_ref"] == binding.runtimeBindingRef
    finally:
        await engine.dispose()


async def _assert_control_progress_preserves_confirmed_evidence(tmp_path, confirmed):
    """A delayed observer cannot erase a real workflow's confirmed safe point."""
    from api_service.services.system_operations import SystemOperationsService, WorkerOperationCommand

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/control-audit.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            service = SystemOperationsService(session)
            await service.submit(WorkerOperationCommand(
                action="pause", mode="quiesce", reason="persist real evidence",
                confirmation="pause", idempotencyKey=confirmed.request_id,
            ), actor_user_id=None)
            audit = await service._audit_event_by_idempotency_key(confirmed.request_id)
            audit_id = audit.id
            await service._persist_control_progress(audit_id, confirmed)
        async with sessions() as session:
            stale = confirmed.model_copy(deep=True)
            stale.targets[0].state = "pending"
            service = SystemOperationsService(session)
            merged = await service._persist_control_progress(audit_id, stale)
            assert merged.targets[0].state == "safe_point"
            assert (await service.snapshot()).control.status == "succeeded"
            conflicting = confirmed.model_copy(deep=True)
            conflicting.generation += 1
            with pytest.raises(ValueError, match="request authority"):
                await service._persist_control_progress(audit_id, conflicting)
            await session.rollback()
            assert (await service.snapshot()).control.targets[0].state == "safe_point"
    finally:
        await engine.dispose()


async def _assert_durable_mixed_fanout(tmp_path, observed, adapter):
    from api_service.services.system_operations import SystemOperationsService, WorkerOperationCommand

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/mixed-control.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            owner = SystemOperationsService(session)
            await owner.submit(WorkerOperationCommand(
                action="pause", mode="quiesce", reason="mixed target evidence",
                confirmation="pause", idempotencyKey=observed.request_id,
            ), actor_user_id=None)
            intent = observed.model_copy(deep=True)
            for target in intent.targets:
                target.state, target.reason = "requested", None
            audit = await owner._audit_event_by_idempotency_key(intent.request_id)
            await owner._persist_control_progress(audit.id, intent)
        async with sessions() as session:
            temporal = TemporalExecutionService(session, client_adapter=adapter)
            owner = SystemOperationsService(session, temporal_service=temporal)
            result = await owner.snapshot()
            assert result.control.status == "partial"
            assert [target.state for target in result.control.targets] == ["safe_point", "unknown"]
        async with sessions() as session:
            audit = await SystemOperationsService(session)._audit_event_by_idempotency_key(intent.request_id)
            persisted = audit.new_value_json["control"]["targets"]
            assert [target["state"] for target in persisted] == ["safe_point", "unknown"]
            assert persisted[1]["reason"] == "update_acceptance_unavailable"
    finally:
        await engine.dispose()
