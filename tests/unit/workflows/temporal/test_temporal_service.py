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
        assert record.owner_type == "user"
        assert record.owner_id == str(owner_id)
        assert record.state is MoonMindWorkflowState.INITIALIZING
        assert record.search_attributes["mm_owner_type"] == "user"
        assert record.search_attributes["mm_owner_id"] == str(owner_id)
        assert record.search_attributes["mm_state"] == "initializing"
        assert record.search_attributes["mm_entry"] == "run"
        assert record.memo["title"] == "My run"
        assert record.memo["input_ref"] == "artifact://input/1"


@pytest.mark.asyncio
async def test_create_execution_without_owner_uses_system_visibility_identity(tmp_path):
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
            idempotency_key="system-create-1",
        )

        assert record.owner_type == "system"
        assert record.owner_id == "system"
        assert record.search_attributes["mm_owner_type"] == "system"
        assert record.search_attributes["mm_owner_id"] == "system"


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
                *, idempotency_key, owner_type, owner_id, workflow_type
            ):
                if idempotency_key == key:
                    await winner_service.create_execution(
                        workflow_type="MoonMind.Run",
                        owner_id=owner_id,
                        owner_type=owner_type,
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
        original_started_at = created.started_at
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
        assert paused.waiting_reason == "operator_paused"
        assert paused.attention_required is True

        await service.signal_execution(
            workflow_id=created.workflow_id,
            signal_name="Resume",
            payload=None,
            payload_artifact_ref=None,
        )
        resumed = await service.describe_execution(created.workflow_id)
        assert resumed.state is MoonMindWorkflowState.EXECUTING
        assert resumed.waiting_reason is None
        assert resumed.attention_required is False

        await service.signal_execution(
            workflow_id=created.workflow_id,
            signal_name="ExternalEvent",
            payload={"source": "jules", "event_type": "completed"},
            payload_artifact_ref="artifact://events/1",
        )
        signaled = await service.describe_execution(created.workflow_id)
        assert "artifact://events/1" in (signaled.artifact_refs or [])
        assert signaled.state is MoonMindWorkflowState.EXECUTING
        assert signaled.waiting_reason is None


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
async def test_update_inputs_major_reconfiguration_records_distinct_continue_as_new_cause(
    tmp_path,
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

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

        first_page = await service.list_executions(
            workflow_type="MoonMind.Run",
            owner_type="user",
            state=None,
            owner_id=owner_a,
            entry="run",
            page_size=2,
            next_page_token=None,
        )
        assert len(first_page.items) == 2
        assert first_page.next_page_token is not None
        assert first_page.count == 3
        assert all(item.owner_type == "user" for item in first_page.items)
        assert all(item.entry == "run" for item in first_page.items)

        second_page = await service.list_executions(
            workflow_type="MoonMind.Run",
            owner_type="user",
            state=None,
            owner_id=owner_a,
            entry="run",
            page_size=2,
            next_page_token=first_page.next_page_token,
        )
        assert len(second_page.items) == 1
        assert second_page.count == 3


@pytest.mark.asyncio
async def test_list_executions_rejects_page_token_scope_changes(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        owner_id = uuid4()

        first = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=owner_id,
            title="first",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key="scope-first",
        )
        second = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=owner_id,
            title="second",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key="scope-second",
        )
        await service.cancel_execution(
            workflow_id=first.workflow_id,
            reason=None,
            graceful=True,
        )

        first_page = await service.list_executions(
            workflow_type="MoonMind.Run",
            owner_type="user",
            state=None,
            owner_id=owner_id,
            entry="run",
            page_size=1,
            next_page_token=None,
        )

        assert first_page.next_page_token is not None
        assert first_page.items[0].workflow_id in {
            first.workflow_id,
            second.workflow_id,
        }

        with pytest.raises(
            TemporalExecutionValidationError, match="Invalid nextPageToken"
        ):
            await service.list_executions(
                workflow_type="MoonMind.Run",
                owner_type="user",
                state="canceled",
                owner_id=owner_id,
                entry="run",
                page_size=1,
                next_page_token=first_page.next_page_token,
            )


@pytest.mark.asyncio
async def test_record_progress_noop_does_not_change_recency(tmp_path):
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

        original_updated_at = created.updated_at
        original_mm_updated_at = created.search_attributes["mm_updated_at"]

        unchanged = await service.record_progress(
            workflow_id=created.workflow_id,
            completed_steps=0,
            completed_wait_cycles=0,
        )

        assert unchanged.updated_at == original_updated_at
        assert unchanged.search_attributes["mm_updated_at"] == original_mm_updated_at


@pytest.mark.asyncio
async def test_describe_execution_canonicalizes_legacy_alias_and_repairs_drift(
    tmp_path,
):
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
            idempotency_key="drifted-record",
        )

        created.owner_type = "service"
        created.owner_id = "unknown"
        created.entry = "manifest"
        created.memo = {"title": "", "summary": ""}
        created.search_attributes = {
            "mm_owner_type": "unknown",
            "mm_owner_id": "unknown",
        }
        await session.commit()

        described = await service.describe_execution(f"task:{created.workflow_id}")

        assert described.workflow_id == created.workflow_id
        assert described.owner_type == "system"
        assert described.owner_id == "system"
        assert described.entry == "run"
        assert described.memo["title"] == "Run"
        assert described.memo["summary"] == "Execution initialized."
        assert described.search_attributes["mm_owner_type"] == "system"
        assert described.search_attributes["mm_owner_id"] == "system"
        assert described.search_attributes["mm_entry"] == "run"
        assert described.search_attributes["mm_state"] == "initializing"


@pytest.mark.asyncio
async def test_describe_execution_prefers_valid_search_attribute_owner_identity(
    tmp_path,
):
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
            idempotency_key="search-attr-owner-id",
        )

        created.owner_type = "unknown"
        created.owner_id = "unknown"
        created.search_attributes = {
            "mm_owner_type": "bogus",
            "mm_owner_id": "user-123",
        }
        await session.commit()

        described = await service.describe_execution(created.workflow_id)

        assert described.owner_type == "user"
        assert described.owner_id == "user-123"
        assert described.search_attributes["mm_owner_type"] == "user"
        assert described.search_attributes["mm_owner_id"] == "user-123"


@pytest.mark.asyncio
async def test_list_executions_repairs_waiting_metadata_before_serialization(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        created = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title="Waiting run",
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key="awaiting-repair",
        )

        await service.mark_execution_awaiting_external(
            workflow_id=created.workflow_id,
            waiting_reason="approval_required",
            attention_required=True,
        )

        drifted = await service.describe_execution(created.workflow_id)
        drifted.awaiting_external = False
        drifted.waiting_reason = None
        drifted.search_attributes = {}
        await session.commit()

        page = await service.list_executions(
            workflow_type="MoonMind.Run",
            owner_type="user",
            state="awaiting_external",
            owner_id=created.owner_id,
            entry="run",
            page_size=10,
            next_page_token=None,
        )

        assert len(page.items) == 1
        repaired = page.items[0]
        assert repaired.awaiting_external is True
        assert repaired.waiting_reason == "unknown_external"
        assert repaired.attention_required is True
        assert repaired.search_attributes["mm_state"] == "awaiting_external"
        assert repaired.search_attributes["mm_entry"] == "run"
