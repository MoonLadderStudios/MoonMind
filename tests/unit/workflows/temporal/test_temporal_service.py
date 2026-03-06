from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    MoonMindWorkflowState,
    TemporalExecutionCloseStatus,
)
from moonmind.workflows.temporal.service import (
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
        assert record.state is MoonMindWorkflowState.INITIALIZING
        assert record.search_attributes["mm_owner_type"] == "user"
        assert record.search_attributes["mm_owner_id"] == str(owner_id)
        assert record.search_attributes["mm_state"] == "initializing"
        assert record.search_attributes["mm_entry"] == "run"
        assert record.memo["title"] == "My run"
        assert record.memo["input_ref"] == "artifact://input/1"


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

            async def race_precheck(*, idempotency_key, owner_id, workflow_type):
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
async def test_request_rerun_uses_continue_as_new_same_workflow_id(tmp_path):
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

        original_run_id = created.run_id
        workflow_id = created.workflow_id
        response = await service.update_execution(
            workflow_id=workflow_id,
            update_name="RequestRerun",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            parameters_patch=None,
            title=None,
            idempotency_key="rerun-1",
        )

        refreshed = await service.describe_execution(workflow_id)
        assert response["accepted"] is True
        assert response["applied"] == "continue_as_new"
        assert refreshed.workflow_id == workflow_id
        assert refreshed.run_id != original_run_id
        assert refreshed.rerun_count == 1


@pytest.mark.asyncio
async def test_request_rerun_clears_pause_flags_when_continuing_as_new(tmp_path):
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
            idempotency_key="rerun-clears-pause",
        )
        refreshed = await service.describe_execution(created.workflow_id)

        assert rerun["applied"] == "continue_as_new"
        assert refreshed.state is MoonMindWorkflowState.EXECUTING
        assert refreshed.paused is False
        assert refreshed.awaiting_external is False


@pytest.mark.asyncio
async def test_signal_pause_resume_and_external_event_transitions(tmp_path):
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
async def test_cancel_marks_terminal_state_and_close_status(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

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
            idempotency_key="rerun-with-overrides",
        )
        refreshed = await service.describe_execution(created.workflow_id)

        assert refreshed.input_ref == "artifact://input/new"
        assert refreshed.plan_ref == "artifact://plan/new"
        assert refreshed.parameters["force"] == "yes"
        assert "artifact://input/new" in refreshed.artifact_refs
        assert "artifact://plan/new" in refreshed.artifact_refs


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
async def test_mark_execution_succeeded_rejects_terminal_execution(tmp_path):
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
            entry=None,
            owner_type="user",
            owner_id=owner_a,
            page_size=2,
            next_page_token=None,
        )
        assert len(first_page.items) == 2
        assert first_page.next_page_token is not None
        assert first_page.count == 3

        second_page = await service.list_executions(
            workflow_type="MoonMind.Run",
            state=None,
            entry=None,
            owner_type="user",
            owner_id=owner_a,
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
