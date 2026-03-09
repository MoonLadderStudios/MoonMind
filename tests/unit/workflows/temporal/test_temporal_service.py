from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    MoonMindWorkflowState,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionCloseStatus,
    TemporalExecutionOwnerType,
    TemporalExecutionProjectionSourceMode,
    TemporalExecutionProjectionSyncState,
    TemporalExecutionRecord,
    TemporalWorkflowType,
)
from moonmind.workflows.temporal.service import (
    TemporalExecutionNotFoundError,
    TemporalExecutionService,
    TemporalExecutionValidationError,
)


@asynccontextmanager
async def temporal_db(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/temporal_lifecycle.db"
    engine = create_async_engine(db_url, future=True)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with session_factory() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_execution_initializes_lifecycle_search_attributes(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        owner_id = uuid4()

        record = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=owner_id,
            title="My run",
            input_artifact_ref="artifact://input/1",
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={"foo": "bar"},
            idempotency_key="create-1",
        )

        assert record.workflow_id.startswith("mm:")
        assert record.search_attributes["mm_owner_type"] == "user"
        assert record.state is MoonMindWorkflowState.INITIALIZING
        assert record.owner_type is TemporalExecutionOwnerType.USER
        assert record.search_attributes["mm_owner_id"] == str(owner_id)
        assert record.search_attributes["mm_owner_type"] == "user"
        assert record.search_attributes["mm_state"] == "initializing"
        assert record.search_attributes["mm_entry"] == "run"
        assert record.memo["title"] == "My run"
        assert record.memo["input_ref"] == "artifact://input/1"
        assert record.sync_state is TemporalExecutionProjectionSyncState.FRESH
        assert (
            record.source_mode
            is TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE
        )

        source = await session.get(TemporalExecutionCanonicalRecord, record.workflow_id)
        assert source is not None
        assert source.run_id == record.run_id


@pytest.mark.asyncio
async def test_create_execution_returns_repair_pending_fallback_when_projection_sync_fails(
    tmp_path, monkeypatch
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        async def fail_projection_sync(source, **kwargs):
            raise RuntimeError(f"projection write failed for {source.workflow_id}")

        monkeypatch.setattr(
            service, "_upsert_projection_from_source", fail_projection_sync
        )

        record = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title="repair pending",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key="repair-pending-create",
        )

        assert record.sync_state is TemporalExecutionProjectionSyncState.REPAIR_PENDING
        assert (
            record.source_mode
            is TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE
        )
        assert "projection write failed" in (record.sync_error or "")

        source = await session.get(TemporalExecutionCanonicalRecord, record.workflow_id)
        projection = await session.get(TemporalExecutionRecord, record.workflow_id)
        assert source is not None
        assert projection is None


@pytest.mark.asyncio
async def test_create_execution_defaults_missing_owner_to_system(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        record = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=None,
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        assert record.owner_type is TemporalExecutionOwnerType.SYSTEM
        assert record.owner_id == "system"
        assert record.search_attributes["mm_owner_type"] == "system"
        assert record.search_attributes["mm_owner_id"] == "system"


@pytest.mark.asyncio
async def test_create_execution_rejects_unsupported_workflow_type(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        with pytest.raises(
            TemporalExecutionValidationError, match="Unsupported workflow type"
        ):
            await service.create_execution(
                workflow_type="MoonMind.Unknown",
                owner_id=uuid4(),
                title=None,
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={},
                idempotency_key=None,
            )


@pytest.mark.asyncio
async def test_create_execution_rejects_missing_manifest_artifact_ref(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        with pytest.raises(
            TemporalExecutionValidationError,
            match="manifestArtifactRef is required",
        ):
            await service.create_execution(
                workflow_type="MoonMind.ManifestIngest",
                owner_id=uuid4(),
                title=None,
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={},
                idempotency_key=None,
            )


@pytest.mark.asyncio
async def test_create_execution_rejects_unsupported_failure_policy(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        with pytest.raises(
            TemporalExecutionValidationError,
            match="Unsupported failurePolicy",
        ):
            await service.create_execution(
                workflow_type="MoonMind.Run",
                owner_id=uuid4(),
                title=None,
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy="explode_loudly",
                initial_parameters={},
                idempotency_key=None,
            )


@pytest.mark.asyncio
async def test_create_execution_rejects_empty_failure_policy(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        with pytest.raises(
            TemporalExecutionValidationError,
            match="Unsupported failurePolicy",
        ):
            await service.create_execution(
                workflow_type="MoonMind.Run",
                owner_id=uuid4(),
                title=None,
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy="",
                initial_parameters={},
                idempotency_key=None,
            )


@pytest.mark.asyncio
async def test_create_execution_returns_existing_record_after_idempotency_race(
    tmp_path, monkeypatch
):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/temporal_lifecycle_race.db"
    engine = create_async_engine(db_url, future=True)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with session_factory() as winner_session, session_factory() as loser_session:
            winner_service = TemporalExecutionService(winner_session)
            loser_service = TemporalExecutionService(loser_session)
            owner_id = uuid4()
            key = "create-race"

            async def race_precheck(
                *,
                idempotency_key,
                owner_id,
                owner_type,
                workflow_type,
            ):
                if idempotency_key == key:
                    await winner_service.create_execution(
                        workflow_type="MoonMind.Run",
                        owner_id=owner_id,
                        title="winner",
                        input_artifact_ref=None,
                        plan_artifact_ref=None,
                        manifest_artifact_ref=None,
                        failure_policy=None,
                        initial_parameters={},
                        idempotency_key=key,
                    )
                    monkeypatch.setattr(
                        loser_service,
                        "_find_by_create_idempotency",
                        original_find,
                    )
                return None

            original_find = loser_service._find_by_create_idempotency
            monkeypatch.setattr(
                loser_service,
                "_find_by_create_idempotency",
                race_precheck,
            )

            original_commit = loser_session.commit

            async def race_commit():
                await loser_session.flush()
                raise IntegrityError("insert", {}, Exception("duplicate"))

            monkeypatch.setattr(loser_session, "commit", race_commit)

            record = await loser_service.create_execution(
                workflow_type="MoonMind.Run",
                owner_id=owner_id,
                title="loser",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={},
                idempotency_key=key,
            )

            assert record.memo["title"] == "winner"
            assert record.create_idempotency_key == key

            monkeypatch.setattr(loser_session, "commit", original_commit)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_execution_scopes_idempotency_by_owner_type(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        user_record = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id="shared-owner",
            owner_type="user",
            title="user owned",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key="shared-idempotency-key",
        )
        service_record = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id="shared-owner",
            owner_type="service",
            title="service owned",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key="shared-idempotency-key",
        )
        service_retry = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id="shared-owner",
            owner_type="service",
            title="service retry",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key="shared-idempotency-key",
        )

        assert user_record.workflow_id != service_record.workflow_id
        assert service_retry.workflow_id == service_record.workflow_id
        assert service_retry.memo["title"] == "service owned"


@pytest.mark.asyncio
async def test_list_executions_syncs_page_in_single_projection_commit(
    tmp_path, monkeypatch
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title="first",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )
        await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title="second",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        original_commit = session.commit
        commit_calls = 0

        async def counting_commit():
            nonlocal commit_calls
            commit_calls += 1
            await original_commit()

        monkeypatch.setattr(session, "commit", counting_commit)

        result = await service.list_executions(
            workflow_type=None,
            owner_type=None,
            state=None,
            owner_id=None,
            entry=None,
            page_size=2,
            next_page_token=None,
        )

        assert len(result.items) == 2
        assert commit_calls == 1


from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_request_rerun_uses_continue_as_new_same_workflow_id(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = AsyncMock()

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        original_run_id = created.run_id
        original_started_at = created.started_at
        workflow_id = created.workflow_id
        response = await service.update_execution(
            workflow_id=workflow_id,
            update_name="RequestRerun",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            parameters_patch=None,
            title=None,
            new_manifest_artifact_ref=None,
            mode=None,
            max_concurrency=None,
            node_ids=None,
            idempotency_key="rerun-1",
        )

        refreshed = await service.describe_execution(workflow_id)
        assert response["accepted"] is True
        assert response["applied"] == "continue_as_new"
        assert response["continue_as_new_cause"] == "manual_rerun"
        assert refreshed.workflow_id == workflow_id
        assert refreshed.run_id != original_run_id
        assert refreshed.started_at == original_started_at
        assert refreshed.rerun_count == 1
        assert refreshed.memo["continue_as_new_cause"] == "manual_rerun"
        assert refreshed.memo["latest_temporal_run_id"] == refreshed.run_id
        assert refreshed.search_attributes["mm_continue_as_new_cause"] == "manual_rerun"


@pytest.mark.asyncio
async def test_request_rerun_rejected_for_terminal_execution(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = AsyncMock()

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        await service.cancel_execution(
            workflow_id=created.workflow_id,
            reason="done",
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
            idempotency_key="rerun-terminal",
        )
        refreshed = await service.describe_execution(created.workflow_id)

        assert response == {
            "accepted": False,
            "applied": "immediate",
            "message": "Workflow is in a terminal state and no longer accepts updates.",
        }
        assert refreshed.state is MoonMindWorkflowState.CANCELED


@pytest.mark.asyncio
async def test_manifest_only_updates_rejected_for_non_manifest_workflow(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = AsyncMock()

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        with pytest.raises(TemporalExecutionValidationError) as exc_info:
            await service.update_execution(
                workflow_id=created.workflow_id,
                update_name="Pause",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                parameters_patch=None,
                title=None,
                new_manifest_artifact_ref=None,
                mode=None,
                max_concurrency=None,
                node_ids=None,
                idempotency_key=None,
            )

        assert "only supported for MoonMind.ManifestIngest" in str(exc_info.value)


@pytest.mark.asyncio
async def test_request_rerun_clears_pause_flags_when_continuing_as_new(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = AsyncMock()

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        await service.signal_execution(
            workflow_id=created.workflow_id,
            signal_name="Pause",
            payload=None,
            payload_artifact_ref=None,
        )

        rerun = await service.update_execution(
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
            idempotency_key="rerun-clears-pause",
        )
        refreshed = await service.describe_execution(created.workflow_id)

        assert rerun["applied"] == "continue_as_new"
        assert refreshed.state is MoonMindWorkflowState.EXECUTING
        assert refreshed.paused is False
        assert refreshed.awaiting_external is False


@pytest.mark.asyncio
async def test_update_execution_rejects_unknown_update_name(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="Unsupported update name",
        ):
            await service.update_execution(
                workflow_id=created.workflow_id,
                update_name="UnknownUpdate",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                parameters_patch=None,
                title=None,
                idempotency_key=None,
            )


@pytest.mark.asyncio
async def test_signal_pause_resume_and_external_event_transitions(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = AsyncMock()

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        await service.signal_execution(
            workflow_id=created.workflow_id,
            signal_name="Pause",
            payload=None,
            payload_artifact_ref=None,
        )
        paused = await service.describe_execution(created.workflow_id)
        assert paused.state is MoonMindWorkflowState.AWAITING_EXTERNAL
        assert paused.memo["waiting_reason"] == "operator_paused"
        assert paused.memo["attention_required"] is True

        await service.signal_execution(
            workflow_id=created.workflow_id,
            signal_name="Resume",
            payload=None,
            payload_artifact_ref=None,
        )
        resumed = await service.describe_execution(created.workflow_id)
        assert resumed.state is MoonMindWorkflowState.EXECUTING
        assert "waiting_reason" not in resumed.memo

        await service.signal_execution(
            workflow_id=created.workflow_id,
            signal_name="ExternalEvent",
            payload={"source": "jules", "event_type": "completed"},
            payload_artifact_ref="artifact://events/1",
        )
        signaled = await service.describe_execution(created.workflow_id)
        assert "artifact://events/1" in (signaled.artifact_refs or [])
        assert signaled.state is MoonMindWorkflowState.EXECUTING


@pytest.mark.asyncio
async def test_signal_execution_rejects_unknown_signal_name(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="Unsupported signal name",
        ):
            await service.signal_execution(
                workflow_id=created.workflow_id,
                signal_name="UnknownSignal",
                payload=None,
                payload_artifact_ref=None,
            )


@pytest.mark.asyncio
async def test_cancel_marks_terminal_state_and_close_status(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = AsyncMock()

        created = await service.create_execution(
            workflow_type="MoonMind.ManifestIngest",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref="artifact://manifest/1",
            failure_policy="fail_fast",
            initial_parameters={},
            idempotency_key=None,
        )

        canceled = await service.cancel_execution(
            workflow_id=created.workflow_id,
            reason="stop",
            graceful=True,
        )

        assert canceled.state is MoonMindWorkflowState.CANCELED
        assert canceled.close_status is TemporalExecutionCloseStatus.CANCELED
        assert canceled.closed_at is not None
        assert canceled.search_attributes["mm_state"] == "canceled"


@pytest.mark.asyncio
async def test_forced_cancel_marks_failed_with_terminated_close_status(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = AsyncMock()

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        terminated = await service.cancel_execution(
            workflow_id=created.workflow_id,
            reason="ops kill",
            graceful=False,
        )

        assert terminated.state is MoonMindWorkflowState.FAILED
        assert terminated.close_status is TemporalExecutionCloseStatus.TERMINATED
        assert terminated.memo["summary"] == "forced_termination: ops kill"


@pytest.mark.asyncio
async def test_request_rerun_can_override_inputs_and_parameters(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = AsyncMock()

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref="artifact://input/original",
            plan_artifact_ref="artifact://plan/original",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={"original": True},
            idempotency_key=None,
        )

        await service.update_execution(
            workflow_id=created.workflow_id,
            update_name="RequestRerun",
            input_artifact_ref="artifact://input/new",
            plan_artifact_ref="artifact://plan/new",
            parameters_patch={"force": "yes"},
            title=None,
            new_manifest_artifact_ref=None,
            mode=None,
            max_concurrency=None,
            node_ids=None,
            idempotency_key="rerun-with-overrides",
        )
        refreshed = await service.describe_execution(created.workflow_id)

        assert refreshed.input_ref == "artifact://input/new"
        assert refreshed.plan_ref == "artifact://plan/new"
        assert refreshed.parameters["force"] == "yes"
        assert "artifact://input/new" in refreshed.artifact_refs
        assert "artifact://plan/new" in refreshed.artifact_refs


@pytest.mark.asyncio
async def test_update_inputs_major_reconfiguration_records_distinct_continue_as_new_cause(
    tmp_path,
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = AsyncMock()

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/original",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        response = await service.update_execution(
            workflow_id=created.workflow_id,
            update_name="UpdateInputs",
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/replacement",
            parameters_patch=None,
            title=None,
            new_manifest_artifact_ref=None,
            mode=None,
            max_concurrency=None,
            node_ids=None,
            idempotency_key="update-major-reconfig",
        )
        refreshed = await service.describe_execution(created.workflow_id)

        assert response["accepted"] is True
        assert response["applied"] == "continue_as_new"
        assert response["continue_as_new_cause"] == "major_reconfiguration"
        assert refreshed.memo["continue_as_new_cause"] == "major_reconfiguration"
        assert refreshed.search_attributes["mm_continue_as_new_cause"] == (
            "major_reconfiguration"
        )


@pytest.mark.asyncio
async def test_record_progress_triggers_continue_as_new_for_run_threshold(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(
            session, run_continue_as_new_step_threshold=2
        )

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )
        original_run_id = created.run_id

        first = await service.record_progress(
            workflow_id=created.workflow_id,
            completed_steps=1,
        )
        assert first.run_id == original_run_id
        assert first.step_count == 1

        second = await service.record_progress(
            workflow_id=created.workflow_id,
            completed_steps=1,
        )
        assert second.run_id != original_run_id
        assert second.rerun_count == 1
        assert second.step_count == 0
        assert second.memo["continue_as_new_cause"] == "lifecycle_threshold"
        assert second.search_attributes["mm_continue_as_new_cause"] == (
            "lifecycle_threshold"
        )


@pytest.mark.asyncio
async def test_signal_external_event_requires_source_and_event_type(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        with pytest.raises(TemporalExecutionValidationError):
            await service.signal_execution(
                workflow_id=created.workflow_id,
                signal_name="ExternalEvent",
                payload={"source": "jules"},
                payload_artifact_ref=None,
            )


@pytest.mark.asyncio
async def test_configure_integration_monitoring_persists_visibility_and_callback_key(
    tmp_path,
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title="Run with integration",
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        configured = await service.configure_integration_monitoring(
            workflow_id=created.workflow_id,
            integration_name="Jules",
            correlation_id=None,
            external_operation_id="task-123",
            normalized_status="running",
            provider_status="in_progress",
            callback_supported=True,
            callback_correlation_key=None,
            recommended_poll_seconds=30,
            external_url="https://jules.example.test/tasks/task-123",
            provider_summary={"queue": "primary"},
            result_refs=["artifact://events/start"],
        )

        assert configured.state is MoonMindWorkflowState.AWAITING_EXTERNAL
        assert configured.awaiting_external is True
        assert configured.search_attributes["mm_integration"] == "jules"
        assert (
            configured.memo["external_url"]
            == "https://jules.example.test/tasks/task-123"
        )
        assert configured.integration_state is not None
        assert configured.integration_state["callback_correlation_key"]
        assert configured.integration_state["external_operation_id"] == "task-123"
        assert "artifact://events/start" in configured.artifact_refs


@pytest.mark.asyncio
async def test_configure_integration_monitoring_rejects_blank_external_operation_id(
    tmp_path,
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title="Run with invalid integration id",
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="external_operation_id is required",
        ):
            await service.configure_integration_monitoring(
                workflow_id=created.workflow_id,
                integration_name="jules",
                correlation_id=None,
                external_operation_id="   ",
                normalized_status="running",
                provider_status="running",
                callback_supported=True,
                callback_correlation_key=None,
                recommended_poll_seconds=30,
                external_url=None,
                provider_summary={},
                result_refs=[],
            )


@pytest.mark.asyncio
async def test_ingest_integration_callback_deduplicates_provider_event_ids(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = AsyncMock()

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )
        configured = await service.configure_integration_monitoring(
            workflow_id=created.workflow_id,
            integration_name="jules",
            correlation_id="corr-1",
            external_operation_id="task-123",
            normalized_status="running",
            provider_status="running",
            callback_supported=True,
            callback_correlation_key="cb-123",
            recommended_poll_seconds=15,
            external_url=None,
            provider_summary={},
            result_refs=[],
        )

        first = await service.ingest_integration_callback(
            integration_name="jules",
            callback_correlation_key="cb-123",
            payload={
                "event_type": "status_changed",
                "provider_event_id": "evt-1",
                "normalized_status": "running",
                "provider_status": "running",
            },
            payload_artifact_ref=None,
        )
        second = await service.ingest_integration_callback(
            integration_name="jules",
            callback_correlation_key="cb-123",
            payload={
                "event_type": "status_changed",
                "provider_event_id": "evt-1",
                "normalized_status": "running",
                "provider_status": "running",
            },
            payload_artifact_ref=None,
        )

        assert first.workflow_id == configured.workflow_id
        assert second.integration_state["provider_event_ids_seen"] == ["evt-1"]
        assert "Ignored duplicate external event" in second.memo["summary"]


@pytest.mark.asyncio
async def test_wait_cycle_continue_as_new_preserves_active_integration_monitoring(
    tmp_path,
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(
            session,
            run_continue_as_new_step_threshold=100,
            run_continue_as_new_wait_cycle_threshold=2,
        )

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )
        configured = await service.configure_integration_monitoring(
            workflow_id=created.workflow_id,
            integration_name="jules",
            correlation_id="corr-continue",
            external_operation_id="task-continue",
            normalized_status="running",
            provider_status="running",
            callback_supported=True,
            callback_correlation_key="cb-continue",
            recommended_poll_seconds=5,
            external_url=None,
            provider_summary={},
            result_refs=[],
        )
        original_run_id = configured.run_id

        updated = await service.record_integration_poll(
            workflow_id=created.workflow_id,
            normalized_status="running",
            provider_status="running",
            observed_at=None,
            recommended_poll_seconds=5,
            external_url=None,
            provider_summary={},
            result_refs=[],
            completed_wait_cycles=2,
        )

        assert updated.run_id != original_run_id
        assert updated.state is MoonMindWorkflowState.AWAITING_EXTERNAL
        assert updated.awaiting_external is True
        assert updated.wait_cycle_count == 0
        assert updated.integration_state["external_operation_id"] == "task-continue"
        assert updated.integration_state["callback_correlation_key"] == "cb-continue"


@pytest.mark.asyncio
async def test_mark_execution_failed_rejects_unknown_error_category(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        with pytest.raises(TemporalExecutionValidationError):
            await service.mark_execution_failed(
                workflow_id=created.workflow_id,
                error_category="unknown",
                message="boom",
            )


@pytest.mark.asyncio
async def test_projection_sync_markers_round_trip_between_stale_and_fresh(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        stale = await service.mark_projection_stale(
            workflow_id=created.workflow_id,
            sync_error="visibility lag",
        )
        assert stale.sync_state is TemporalExecutionProjectionSyncState.STALE
        assert stale.sync_error == "visibility lag"

        refreshed = await service.mark_execution_executing(
            workflow_id=created.workflow_id,
            summary="back in sync",
        )
        assert refreshed.sync_state is TemporalExecutionProjectionSyncState.FRESH
        assert refreshed.sync_error is None
        assert refreshed.search_attributes["mm_owner_type"] == "user"


@pytest.mark.asyncio
async def test_update_execution_persists_repair_pending_when_projection_refresh_fails(
    tmp_path, monkeypatch
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = AsyncMock()

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title="Before failure",
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )
        previous_sync_at = created.last_synced_at

        async def fail_projection_sync(source, **kwargs):
            raise RuntimeError(f"projection write failed for {source.workflow_id}")

        monkeypatch.setattr(
            service, "_upsert_projection_from_source", fail_projection_sync
        )

        response = await service.update_execution(
            workflow_id=created.workflow_id,
            update_name="SetTitle",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            parameters_patch=None,
            title="After failure",
            idempotency_key="repair-pending-update",
        )

        assert response["accepted"] is True

        projection = await session.get(TemporalExecutionRecord, created.workflow_id)
        source = await session.get(
            TemporalExecutionCanonicalRecord, created.workflow_id
        )
        assert projection is not None
        assert source is not None
        assert (
            projection.sync_state is TemporalExecutionProjectionSyncState.REPAIR_PENDING
        )
        assert (
            projection.source_mode
            is TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE
        )
        assert "projection write failed" in (projection.sync_error or "")
        assert projection.last_synced_at == previous_sync_at
        assert projection.memo["title"] == "Before failure"
        assert source.memo["title"] == "After failure"


@pytest.mark.asyncio
async def test_orphaned_projection_rows_are_repaired_from_canonical_lists(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        owner_id = uuid4()

        visible = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=owner_id,
            title="visible",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key="visible-row",
        )
        hidden = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=owner_id,
            title="hidden",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key="hidden-row",
        )

        orphaned = await service.mark_projection_orphaned(
            workflow_id=hidden.workflow_id,
            sync_error="temporal execution missing",
        )
        assert orphaned.sync_state is TemporalExecutionProjectionSyncState.ORPHANED

        listed = await service.list_executions(
            workflow_type="MoonMind.Run",
            owner_type="user",
            state=None,
            owner_id=owner_id,
            entry="run",
            page_size=10,
            next_page_token=None,
        )

        assert [item.workflow_id for item in listed.items] == [
            hidden.workflow_id,
            visible.workflow_id,
        ]
        assert listed.count == 2

        repaired = await service.describe_execution(hidden.workflow_id)
        assert repaired.sync_state is TemporalExecutionProjectionSyncState.FRESH
        assert repaired.source_mode is (
            TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE
        )


@pytest.mark.asyncio
async def test_orphaned_projection_rows_with_canonical_source_repair_on_read_and_update(
    tmp_path,
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = AsyncMock()

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title="hidden",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key="hidden-describe-row",
        )

        await service.mark_projection_orphaned(
            workflow_id=created.workflow_id,
            sync_error="temporal execution missing",
        )

        repaired = await service.describe_execution(created.workflow_id)
        assert repaired.sync_state is TemporalExecutionProjectionSyncState.FRESH
        assert repaired.state is MoonMindWorkflowState.INITIALIZING

        response = await service.update_execution(
            workflow_id=created.workflow_id,
            update_name="SetTitle",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            parameters_patch=None,
            title="Should apply",
            idempotency_key="orphaned-update",
        )
        assert response["accepted"] is True

        updated = await service.describe_execution(created.workflow_id)
        assert updated.memo["title"] == "Should apply"


@pytest.mark.asyncio
async def test_ghost_projection_rows_without_canonical_source_are_hidden(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        owner_id = str(uuid4())
        created_at = datetime.now(UTC)
        ghost = TemporalExecutionRecord(
            workflow_id="mm:ghost-row",
            run_id=str(uuid4()),
            namespace="moonmind",
            workflow_type=TemporalWorkflowType.RUN,
            owner_id=owner_id,
            owner_type=TemporalExecutionOwnerType.USER,
            state=MoonMindWorkflowState.EXECUTING,
            close_status=None,
            entry="run",
            search_attributes={
                "mm_owner_type": "user",
                "mm_owner_id": owner_id,
                "mm_state": "executing",
                "mm_updated_at": "2026-03-06T00:00:00+00:00",
                "mm_entry": "run",
            },
            memo={"title": "ghost", "summary": "Ghost row"},
            artifact_refs=[],
            parameters={},
            projection_version=1,
            last_synced_at=created_at,
            sync_state=TemporalExecutionProjectionSyncState.FRESH,
            sync_error=None,
            source_mode=TemporalExecutionProjectionSourceMode.PROJECTION_ONLY,
            started_at=created_at,
            updated_at=created_at,
            closed_at=None,
        )
        session.add(ghost)
        await session.commit()

        listed = await service.list_executions(
            workflow_type="MoonMind.Run",
            owner_type="user",
            state=None,
            owner_id=owner_id,
            entry="run",
            page_size=10,
            next_page_token=None,
        )

        assert listed.count == 0
        assert listed.items == []

        with pytest.raises(TemporalExecutionNotFoundError):
            await service.describe_execution(ghost.workflow_id)


@pytest.mark.asyncio
async def test_mark_execution_succeeded_rejects_terminal_execution(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = AsyncMock()

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        await service.cancel_execution(
            workflow_id=created.workflow_id,
            reason="stop",
            graceful=True,
        )

        with pytest.raises(TemporalExecutionValidationError):
            await service.mark_execution_succeeded(workflow_id=created.workflow_id)

        canceled = await service.describe_execution(created.workflow_id)
        assert canceled.state is MoonMindWorkflowState.CANCELED
        assert canceled.close_status is TemporalExecutionCloseStatus.CANCELED


@pytest.mark.asyncio
async def test_list_executions_filters_owner_and_paginates(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        owner_a = uuid4()
        owner_b = uuid4()

        for idx in range(3):
            await service.create_execution(
                workflow_type="MoonMind.Run",
                owner_id=owner_a,
                title=f"A-{idx}",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={},
                idempotency_key=f"owner-a-{idx}",
            )
        await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=owner_b,
            title="B-0",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key="owner-b-0",
        )
        await service.create_execution(
            workflow_type="MoonMind.ManifestIngest",
            owner_id=owner_a,
            title="manifest-0",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref="artifact://manifest/owner-a",
            failure_policy=None,
            initial_parameters={},
            idempotency_key="owner-a-manifest-0",
        )

        first_page = await service.list_executions(
            workflow_type="MoonMind.Run",
            state=None,
            entry="run",
            owner_type="user",
            owner_id=owner_a,
            repo=None,
            integration=None,
            page_size=2,
            next_page_token=None,
        )
        assert len(first_page.items) == 2
        assert first_page.next_page_token is not None
        assert first_page.count == 3

        second_page = await service.list_executions(
            workflow_type="MoonMind.Run",
            state=None,
            entry="run",
            owner_type="user",
            owner_id=owner_a,
            repo=None,
            integration=None,
            page_size=2,
            next_page_token=first_page.next_page_token,
        )
        assert len(second_page.items) == 1
        assert second_page.count == 3

        manifest_page = await service.list_executions(
            workflow_type=None,
            state=None,
            entry="manifest",
            owner_type="user",
            owner_id=owner_a,
            page_size=10,
            next_page_token=None,
        )
        assert len(manifest_page.items) == 1
        assert manifest_page.items[0].entry == "manifest"


@pytest.mark.asyncio
async def test_list_executions_filters_entry_repo_and_integration(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        owner_id = uuid4()

        matching = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=owner_id,
            title="Matching run",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key="matching-run",
            repository="Moon/Mind",
            integration="github",
        )
        await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=owner_id,
            title="Other repo",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key="other-repo",
            repository="Other/Repo",
            integration="github",
        )
        await service.create_execution(
            workflow_type="MoonMind.ManifestIngest",
            owner_id=owner_id,
            title="Manifest",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref="artifact://manifest/1",
            failure_policy=None,
            initial_parameters={},
            idempotency_key="manifest-run",
        )
        result = await service.list_executions(
            workflow_type=None,
            state=None,
            entry="run",
            owner_type="user",
            owner_id=owner_id,
            repo="Moon/Mind",
            integration="github",
            page_size=10,
            next_page_token=None,
        )

        assert result.count == 1
        assert len(result.items) == 1
        assert result.items[0].workflow_id == matching.workflow_id


@pytest.mark.asyncio
async def test_polling_backoff_resets_after_status_change_and_updates_visibility(
    tmp_path,
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(
            session,
            integration_poll_initial_seconds=5,
            integration_poll_max_seconds=30,
            integration_poll_jitter_ratio=0.0,
        )

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title="Backoff test",
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )
        configured = await service.configure_integration_monitoring(
            workflow_id=created.workflow_id,
            integration_name="jules",
            correlation_id="corr-backoff",
            external_operation_id="task-backoff",
            normalized_status="queued",
            provider_status="pending",
            callback_supported=True,
            callback_correlation_key="cb-backoff",
            recommended_poll_seconds=None,
            external_url=None,
            provider_summary={},
            result_refs=[],
        )
        assert configured.integration_state["poll_interval_seconds"] == 5
        assert configured.search_attributes["mm_stage"] == "queued"

        first_poll = await service.record_integration_poll(
            workflow_id=created.workflow_id,
            normalized_status="queued",
            provider_status="pending",
            observed_at=None,
            recommended_poll_seconds=None,
            external_url=None,
            provider_summary={},
            result_refs=[],
            completed_wait_cycles=0,
        )
        assert first_poll.integration_state["poll_interval_seconds"] == 10

        second_poll = await service.record_integration_poll(
            workflow_id=created.workflow_id,
            normalized_status="running",
            provider_status="in_progress",
            observed_at=None,
            recommended_poll_seconds=None,
            external_url=None,
            provider_summary={},
            result_refs=[],
            completed_wait_cycles=0,
        )
        assert second_poll.integration_state["poll_interval_seconds"] == 5
        assert second_poll.search_attributes["mm_stage"] == "running"


@pytest.mark.asyncio
async def test_late_non_terminal_callback_is_ignored_after_terminal_completion(
    tmp_path,
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = AsyncMock()

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )
        await service.configure_integration_monitoring(
            workflow_id=created.workflow_id,
            integration_name="jules",
            correlation_id="corr-late",
            external_operation_id="task-late",
            normalized_status="running",
            provider_status="running",
            callback_supported=True,
            callback_correlation_key="cb-late",
            recommended_poll_seconds=5,
            external_url=None,
            provider_summary={},
            result_refs=[],
        )
        await service.ingest_integration_callback(
            integration_name="jules",
            callback_correlation_key="cb-late",
            payload={
                "event_type": "completed",
                "provider_event_id": "evt-complete",
                "normalized_status": "succeeded",
                "provider_status": "completed",
            },
            payload_artifact_ref=None,
        )
        late = await service.ingest_integration_callback(
            integration_name="jules",
            callback_correlation_key="cb-late",
            payload={
                "event_type": "progress",
                "provider_event_id": "evt-progress",
                "normalized_status": "running",
                "provider_status": "running",
            },
            payload_artifact_ref=None,
        )

        assert late.integration_state["normalized_status"] == "succeeded"
        assert "Ignored late non-terminal external event" in late.memo["summary"]


@pytest.mark.asyncio
async def test_failed_poll_marks_integration_error_summary(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )
        await service.configure_integration_monitoring(
            workflow_id=created.workflow_id,
            integration_name="jules",
            correlation_id="corr-fail",
            external_operation_id="task-fail",
            normalized_status="running",
            provider_status="running",
            callback_supported=False,
            callback_correlation_key=None,
            recommended_poll_seconds=5,
            external_url=None,
            provider_summary={},
            result_refs=[],
        )
        failed = await service.record_integration_poll(
            workflow_id=created.workflow_id,
            normalized_status="failed",
            provider_status="errored",
            observed_at=None,
            recommended_poll_seconds=None,
            external_url=None,
            provider_summary={"message": "boom"},
            result_refs=[],
            completed_wait_cycles=0,
        )

        assert failed.memo["error_category"] == "integration_error"
        assert failed.awaiting_external is False
