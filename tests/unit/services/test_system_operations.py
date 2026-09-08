from __future__ import annotations

from moonmind.schemas.workflow_control_models import WorkflowControlTarget

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import Base, SettingsAuditEvent
from api_service.services.system_operations import (
    SystemOperationUnavailableError,
    SystemOperationValidationError,
    SystemOperationsService,
    WorkerOperationCommand,
)


def _idempotency_key(value: str) -> str:
    return f"unit-{value}"


@pytest.fixture
def system_operations_session_maker(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/system-ops.db")

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio_run = __import__("asyncio").run
    asyncio_run(_setup())
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    yield session_maker
    asyncio_run(engine.dispose())


class FakeTemporalService:
    def __init__(self) -> None:
        self.pause_calls = 0
        self.resume_calls = 0
        self.metrics = {"queued": 0, "running": 0, "stale_running": 0}

    async def send_quiesce_pause_signal(self, *, request_id, batch, on_progress):
        self.pause_calls += 1
        batch.enumerated = True
        batch.targets = [WorkflowControlTarget(workflowId="fixture-workflow", runId="fixture-run", updateId=request_id, state="safe_point")]
        await on_progress(batch)
        return batch

    async def send_quiesce_resume_signal(self, *, request_id, batch, on_progress):
        self.resume_calls += 1
        batch.enumerated = True
        batch.targets = [WorkflowControlTarget(workflowId="fixture-workflow", runId="fixture-run", updateId=request_id, state="resumed")]
        await on_progress(batch)
        return batch

    async def get_drain_metrics(self) -> dict[str, int]:
        return dict(self.metrics)


class FailingMetricsTemporalService(FakeTemporalService):
    async def get_drain_metrics(self) -> dict[str, int]:
        raise RuntimeError("visibility unavailable")


class MalformedMetricsTemporalService(FakeTemporalService):
    async def get_drain_metrics(self) -> list[int]:
        return [1, 2, 3]


@pytest.mark.asyncio
async def test_worker_snapshot_defaults_and_projects_sanitized_audit(
    system_operations_session_maker,
) -> None:
    async with system_operations_session_maker() as session:
        actor_id = uuid4()
        session.add(
            SettingsAuditEvent(
                event_type="operation_invoked",
                key="operations.workers",
                scope="system",
                actor_user_id=actor_id,
                new_value_json={
                    "action": "pause",
                    "mode": "drain",
                    "status": "succeeded",
                    "token": "should-not-render",
                },
                reason="Maintenance",
            )
        )
        await session.commit()

        snapshot = await SystemOperationsService(
            session, temporal_service=FakeTemporalService()
        ).snapshot()

    assert snapshot.system.workers_paused is False
    assert snapshot.metrics.queued == 0
    assert snapshot.metrics.is_drained is True
    assert snapshot.metrics.metrics_source == "temporal"
    assert snapshot.audit.latest[0].action == "pause"
    assert snapshot.audit.latest[0].actor_user_id == actor_id
    assert "should-not-render" not in snapshot.model_dump_json()


@pytest.mark.asyncio
async def test_pause_requires_mode_reason_confirmation_and_has_idempotency_key(
    system_operations_session_maker,
) -> None:
    async with system_operations_session_maker() as session:
        service = SystemOperationsService(session, temporal_service=FakeTemporalService())

        with pytest.raises(SystemOperationValidationError, match="confirmation"):
            await service.submit(
                WorkerOperationCommand(
                    action="pause",
                    mode="drain",
                    reason="Maint",
                    idempotencyKey=_idempotency_key("missing-confirmation"),
                ),
                actor_user_id=uuid4(),
            )

        snapshot = await service.submit(
            WorkerOperationCommand(
                action="pause",
                mode="drain",
                reason="Maint",
                confirmation="Pause workers confirmed",
                idempotencyKey=_idempotency_key("pause-drain"),
            ),
            actor_user_id=uuid4(),
        )

    assert snapshot.system.workers_paused is True
    assert snapshot.system.mode == "drain"
    assert snapshot.signal_status == "succeeded"


@pytest.mark.asyncio
async def test_quiesce_and_resume_delegate_to_temporal_signal_methods(
    system_operations_session_maker,
) -> None:
    temporal = FakeTemporalService()
    async with system_operations_session_maker() as session:
        service = SystemOperationsService(session, temporal_service=temporal)

        await service.submit(
            WorkerOperationCommand(
                action="pause",
                mode="drain",
                reason="Drain only",
                confirmation="Pause workers confirmed",
                idempotencyKey=_idempotency_key("drain-only"),
            ),
            actor_user_id=uuid4(),
        )
        assert temporal.pause_calls == 0

        await service.submit(
            WorkerOperationCommand(
                action="pause",
                mode="quiesce",
                reason="Stop claims",
                confirmation="Pause workers confirmed",
                idempotencyKey=_idempotency_key("quiesce"),
            ),
            actor_user_id=uuid4(),
        )
        await service.submit(
            WorkerOperationCommand(
                action="resume",
                reason="Done",
                idempotencyKey=_idempotency_key("resume"),
            ),
            actor_user_id=uuid4(),
        )

    assert temporal.pause_calls == 1
    assert temporal.resume_calls == 1


@pytest.mark.asyncio
async def test_submit_returns_actual_subsystem_signal_status(
    system_operations_session_maker,
) -> None:
    async with system_operations_session_maker() as session:
        service = SystemOperationsService(session, temporal_service=FakeTemporalService())

        snapshot = await service.submit(
            WorkerOperationCommand(
                action="pause",
                mode="quiesce",
                reason="Stop claims",
                confirmation="Pause workers confirmed",
                idempotencyKey=_idempotency_key("signal-status"),
            ),
            actor_user_id=uuid4(),
        )

    assert snapshot.signal_status == "succeeded"


@pytest.mark.asyncio
async def test_snapshot_derives_drained_status_from_temporal_metrics(
    system_operations_session_maker,
) -> None:
    """MM-978: worker-pause metrics must not hardcode drained status."""
    temporal = FakeTemporalService()
    temporal.metrics = {"queued": 0, "running": 2, "stale_running": 0}
    async with system_operations_session_maker() as session:
        snapshot = await SystemOperationsService(
            session, temporal_service=temporal
        ).snapshot()

    assert snapshot.metrics.running == 2
    assert snapshot.metrics.is_drained is False
    assert snapshot.metrics.metrics_source == "temporal"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "temporal_service",
    [
        FailingMetricsTemporalService(),
        MalformedMetricsTemporalService(),
        object(),
    ],
)
async def test_snapshot_degrades_when_temporal_metrics_are_unavailable(
    system_operations_session_maker,
    temporal_service,
) -> None:
    async with system_operations_session_maker() as session:
        snapshot = await SystemOperationsService(
            session, temporal_service=temporal_service
        ).snapshot()

    assert snapshot.metrics.queued == 0
    assert snapshot.metrics.running == 0
    assert snapshot.metrics.stale_running == 0
    assert snapshot.metrics.is_drained is False
    assert snapshot.metrics.metrics_source == "unavailable"


@pytest.mark.asyncio
async def test_quiesce_and_resume_retain_requested_state_when_signal_handler_is_missing(
    system_operations_session_maker,
) -> None:
    async with system_operations_session_maker() as session:
        service = SystemOperationsService(session, temporal_service=object())

        snapshot = await service.submit(
            WorkerOperationCommand(
                action="pause",
                mode="quiesce",
                reason="Stop claims",
                confirmation="Pause workers confirmed",
                idempotencyKey=_idempotency_key("missing-pause-handler"),
            ),
            actor_user_id=uuid4(),
        )
        assert snapshot.signal_status == "requested"
        assert snapshot.control is not None
        assert snapshot.control.enumerated is False

        snapshot = await service.submit(
            WorkerOperationCommand(
                action="resume",
                reason="Done",
                idempotencyKey=_idempotency_key("missing-resume-handler"),
            ),
            actor_user_id=uuid4(),
        )
        assert snapshot.signal_status == "requested"
        assert snapshot.control is not None
        assert snapshot.control.enumerated is False

@pytest.mark.asyncio
async def test_duplicate_idempotency_key_reuses_existing_operation_without_side_effects(
    system_operations_session_maker,
) -> None:
    actor_id = uuid4()
    temporal = FakeTemporalService()
    async with system_operations_session_maker() as session:
        service = SystemOperationsService(session, temporal_service=temporal)
        command = WorkerOperationCommand(
            action="pause",
            mode="quiesce",
            reason="Stop claims",
            confirmation="Pause workers confirmed",
            idempotencyKey=_idempotency_key("explicit-duplicate"),
        )

        first = await service.submit(command, actor_user_id=actor_id)
        second = await service.submit(command, actor_user_id=actor_id)
        result = await session.execute(select(SettingsAuditEvent))
        audit_events = result.scalars().all()

    assert first.signal_status == "succeeded"
    assert second.signal_status == "succeeded"
    assert temporal.pause_calls == 1
    assert len(audit_events) == 1


@pytest.mark.asyncio
async def test_reusing_idempotency_key_for_different_command_is_rejected(
    system_operations_session_maker,
) -> None:
    async with system_operations_session_maker() as session:
        service = SystemOperationsService(session, temporal_service=FakeTemporalService())
        await service.submit(
            WorkerOperationCommand(
                action="pause",
                mode="drain",
                reason="Maint",
                confirmation="Pause workers confirmed",
                idempotencyKey=_idempotency_key("conflict"),
            ),
            actor_user_id=uuid4(),
        )

        with pytest.raises(SystemOperationValidationError, match="idempotency"):
            await service.submit(
                WorkerOperationCommand(
                    action="resume",
                    reason="Done",
                    idempotencyKey=_idempotency_key("conflict"),
                ),
                actor_user_id=uuid4(),
            )


@pytest.mark.asyncio
async def test_snapshot_includes_operations_command_catalog(
    system_operations_session_maker,
) -> None:
    async with system_operations_session_maker() as session:
        snapshot = await SystemOperationsService(
            session, temporal_service=FakeTemporalService()
        ).snapshot()

    command_ids = {command.id for command in snapshot.commands}
    assert {
        "pause-workers",
        "resume-workers",
        "drain-queue",
        "quiesce-runtime-family",
        "enable-maintenance-mode",
        "disable-launch-scheduling",
        "update-operational-reason",
        "set-operational-banner",
    }.issubset(command_ids)
    pause_command = next(
        command for command in snapshot.commands if command.id == "pause-workers"
    )
    assert pause_command.requires_confirmation is True
    assert pause_command.required_permission == "operations.invoke"


@pytest.mark.asyncio
async def test_operation_audit_persists_non_secret_command_metadata(
    system_operations_session_maker,
) -> None:
    actor_id = uuid4()
    async with system_operations_session_maker() as session:
        service = SystemOperationsService(session, temporal_service=FakeTemporalService())
        await service.submit(
            WorkerOperationCommand(
                action="pause",
                mode="drain",
                reason="Maint",
                confirmation="Pause workers confirmed",
                idempotencyKey=_idempotency_key("audit"),
            ),
            actor_user_id=actor_id,
        )
        result = await session.execute(select(SettingsAuditEvent))
        audit = result.scalar_one()

    assert audit.event_type == "operation_invoked"
    assert audit.key == "operations.workers"
    assert audit.scope == "system"
    assert audit.actor_user_id == actor_id
    assert audit.reason == "Maint"
    assert audit.new_value_json["action"] == "pause"
    assert audit.new_value_json["target"] == "workers"
    assert audit.new_value_json["status"] == "succeeded"
    assert audit.new_value_json["resultStatus"] == "succeeded"
    assert audit.new_value_json["idempotencyKey"] == _idempotency_key("audit")
    assert "confirmation" not in audit.new_value_json


@pytest.mark.asyncio
async def test_snapshot_projects_audit_target_result_and_idempotency_key(
    system_operations_session_maker,
) -> None:
    actor_id = uuid4()
    async with system_operations_session_maker() as session:
        service = SystemOperationsService(session, temporal_service=FakeTemporalService())
        snapshot = await service.submit(
            WorkerOperationCommand(
                action="pause",
                mode="drain",
                reason="Maint",
                confirmation="Pause workers confirmed",
                idempotencyKey=_idempotency_key("projected-audit"),
            ),
            actor_user_id=actor_id,
        )

    latest = snapshot.audit.latest[0]
    assert latest.actor_user_id == actor_id
    assert latest.target == "workers"
    assert latest.result_status == "succeeded"
    assert latest.signal_status == "succeeded"
    assert latest.idempotency_key == _idempotency_key("projected-audit")


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_state", [False, True])
@pytest.mark.parametrize("conflicting", [False, True])
async def test_concurrent_command_identity_has_one_durable_owner(
    system_operations_session_maker, existing_state, conflicting,
):
    import asyncio
    from sqlalchemy import func

    if existing_state:
        async with system_operations_session_maker() as session:
            await SystemOperationsService(session).submit(WorkerOperationCommand(
                action="pause", mode="drain", reason="Existing state",
                confirmation="pause", idempotencyKey="seed-state",
            ), actor_user_id=None)

    # Hold the interleaving at a real transaction boundary. Without the authority
    # lock, both sessions pass the lookup and allocate the same next generation.
    class InterleavedService(SystemOperationsService):
        async def _persist_audit(self, *args, **kwargs):
            await asyncio.sleep(0.02)
            return await super()._persist_audit(*args, **kwargs)

    async def submit(action):
        async with system_operations_session_maker() as session:
            return await InterleavedService(session).submit(WorkerOperationCommand(
                action=action, mode="drain" if action == "pause" else None,
                reason="Concurrent command", confirmation="confirmed",
                idempotencyKey="concurrent-identity",
            ), actor_user_id=None)

    results = await asyncio.gather(
        submit("pause"), submit("resume" if conflicting else "pause"),
        return_exceptions=True,
    )

    successes = [result for result in results if not isinstance(result, Exception)]
    errors = [result for result in results if isinstance(result, Exception)]
    assert len(successes) == (1 if conflicting else 2), results
    if conflicting:
        assert len(errors) == 1
        assert isinstance(errors[0], SystemOperationValidationError)
        assert errors[0].code == "worker_operation_idempotency_conflict"
    else:
        assert not errors
    assert all(result.system.version == 1 + int(existing_state) for result in successes)
    async with system_operations_session_maker() as session:
        count = await session.scalar(select(func.count()).select_from(SettingsAuditEvent))
        assert count == 1 + int(existing_state)
        snapshot = await SystemOperationsService(session).snapshot()
        assert snapshot.system.version == 1 + int(existing_state)
        assert any(event.idempotency_key == "concurrent-identity" for event in snapshot.audit.latest)


@pytest.mark.asyncio
@pytest.mark.parametrize("states", [None, ["failed"], ["unknown"], ["pending"], ["resumed", "failed"], ["resumed"]])
async def test_resume_command_remains_actionable_until_every_target_confirms(
    system_operations_session_maker, states,
):
    from moonmind.schemas.workflow_control_models import WorkflowControlBatch

    async with system_operations_session_maker() as session:
        service = SystemOperationsService(session)
        command = WorkerOperationCommand(
            action="resume", reason="Resume workflows", idempotencyKey="resume-evidence",
        )
        snapshot = await service.submit(command, actor_user_id=None)
        assert snapshot.system.workers_paused is False
        if states is not None:
            audit = await service._audit_event_by_idempotency_key("resume-evidence")
            batch = WorkflowControlBatch.model_validate(audit.new_value_json["control"])
            batch.enumerated = True
            batch.targets = [WorkflowControlTarget(
                workflowId=f"workflow-{i}", runId=f"run-{i}", updateId=f"update-{i}", state=state,
            ) for i, state in enumerate(states)]
            await service._persist_control_progress(audit.id, batch)
        snapshot = await service.snapshot()
        resume = next(command for command in snapshot.commands if command.id == "resume-workers")
        assert resume.available is (states != ["resumed"])
        if states != ["resumed"]:
            assert resume.unavailable_reason is None
            retried = await service.submit(command.model_copy(update={
                "idempotency_key": "resume-evidence-retry",
            }), actor_user_id=None)
            assert retried.control.generation == snapshot.control.generation + 1
            assert retried.control.request_id == "resume-evidence-retry"


@pytest.mark.asyncio
async def test_command_refreshes_state_loaded_before_another_session_commits(
    system_operations_session_maker,
):
    async with system_operations_session_maker() as session:
        service = SystemOperationsService(session)
        pause = WorkerOperationCommand(
            action="pause", mode="drain", reason="Maintain admission", confirmation="pause",
            idempotencyKey="initial-state",
        )
        await service.submit(pause, actor_user_id=None)
        stale_row = await service._state_row()
        assert stale_row.value_json["version"] == 1
        async with system_operations_session_maker() as other_session:
            await SystemOperationsService(other_session).submit(WorkerOperationCommand(
                action="resume", reason="Another operator resumed", idempotencyKey="other-session",
            ), actor_user_id=None)
        # The existing ORM identity must be refreshed after the authority lock.
        result = await service.submit(pause.model_copy(update={
            "idempotency_key": "latest-state",
        }), actor_user_id=None)
        assert result.system.version == 3
        assert stale_row.value_json["version"] == 3
        assert result.system.workers_paused is True


@pytest.mark.asyncio
async def test_paused_admission_state_gates_new_commands_with_new_generation(
    system_operations_session_maker,
):
    """A pause marks admission paused; the opposite command reopens it cleanly.

    New work arriving mid-pause follows the admission policy surfaced through
    command availability: while paused, pause/drain/quiesce stay unavailable
    and resume stays available. The opposite (resume) command allocates a new
    monotonic generation under a new request identity, so stale observations
    from the pause generation cannot overwrite the resumed state. Schedule,
    continuation, and child-start paths inside an already-running workflow are
    not new API admissions and are quiesced only at the workflow safe boundary
    (see WorkerPauseSystem.md); shared-queue operator/manifest workflows are
    excluded from enumeration by the UserWorkflow-only selection policy.
    """
    async with system_operations_session_maker() as session:
        service = SystemOperationsService(session, temporal_service=FakeTemporalService())
        paused = await service.submit(
            WorkerOperationCommand(
                action="pause", mode="quiesce", reason="Guard admission",
                confirmation="pause", idempotencyKey="admission-pause",
            ),
            actor_user_id=None,
        )
        assert paused.system.workers_paused is True
        pause_generation = paused.control.generation
        by_id = {command.id: command for command in paused.commands}
        for command_id in ("pause-workers", "drain-queue", "quiesce-runtime-family"):
            assert by_id[command_id].available is False
            assert by_id[command_id].unavailable_reason == (
                "Submission admission is already paused."
            )
        assert by_id["resume-workers"].available is True
        assert by_id["resume-workers"].unavailable_reason is None

        resumed = await service.submit(
            WorkerOperationCommand(
                action="resume", reason="Reopen admission",
                idempotencyKey="admission-resume",
            ),
            actor_user_id=None,
        )
        assert resumed.system.workers_paused is False
        assert resumed.control.request_id == "admission-resume"
        assert resumed.control.generation == pause_generation + 1
        reopened = {command.id: command for command in resumed.commands}
        assert reopened["pause-workers"].available is True
        assert reopened["resume-workers"].available is False
        assert reopened["resume-workers"].unavailable_reason == (
            "Submission admission is already open."
        )


@pytest.mark.asyncio
async def test_terminal_control_batches_skip_snapshot_reconciliation(
    system_operations_session_maker,
):
    """Snapshot reads perform no Temporal work for terminal batches."""
    from moonmind.schemas.workflow_control_models import WorkflowControlBatch, WorkflowControlTarget

    class CountingTemporalService(FakeTemporalService):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def send_quiesce_pause_signal(self, *, request_id, batch, on_progress):
            self.calls += 1
            return batch

        async def send_quiesce_resume_signal(self, *, request_id, batch, on_progress):
            self.calls += 1
            return batch

    terminal_states = {
        "succeeded": ["safe_point"],
        "failed": ["failed"],
        "empty": [],
    }
    temporal = CountingTemporalService()
    async with system_operations_session_maker() as session:
        service = SystemOperationsService(session, temporal_service=temporal)
        for terminal, states in terminal_states.items():
            key = f"terminal-bound-{terminal}"
            await service.submit(
                WorkerOperationCommand(
                    action="resume", reason="Terminal bound",
                    idempotencyKey=key,
                ),
                actor_user_id=None,
            )
            audit = await service._audit_event_by_idempotency_key(key)
            batch = WorkflowControlBatch.model_validate(audit.new_value_json["control"])
            batch.enumerated = True
            batch.targets = [WorkflowControlTarget(
                workflowId=f"{key}-wf-{i}", runId=f"run-{i}", updateId=f"update-{i}", state=state,
            ) for i, state in enumerate(states)]
            await service._persist_control_progress(audit.id, batch)
            calls_before = temporal.calls
            snapshot = await service.snapshot()
            assert snapshot.control.status == terminal
            assert temporal.calls == calls_before


@pytest.mark.asyncio
async def test_snapshot_does_not_reconcile_superseded_generation(
    system_operations_session_maker,
):
    """Older observations never overwrite the current requested state."""
    temporal = FakeTemporalService()
    async with system_operations_session_maker() as session:
        service = SystemOperationsService(session, temporal_service=temporal)
        await service.submit(
            WorkerOperationCommand(
                action="pause", mode="quiesce", reason="First command",
                confirmation="pause", idempotencyKey="first-command",
            ),
            actor_user_id=None,
        )
        await service.submit(
            WorkerOperationCommand(
                action="resume", reason="Newer command replaces the request",
                idempotencyKey="newer-command",
            ),
            actor_user_id=None,
        )
        stale = await service._audit_event_by_idempotency_key("first-command")
        from moonmind.schemas.workflow_control_models import WorkflowControlBatch

        stale_batch = WorkflowControlBatch.model_validate(stale.new_value_json["control"])
        with __import__("pytest").raises(ValueError, match="request authority"):
            await service._persist_control_progress(stale.id, stale_batch.model_copy(update={
                "generation": stale_batch.generation + 1,
            }))
        snapshot = await service.snapshot()
        assert snapshot.control.request_id == "newer-command"


@pytest.mark.asyncio
async def test_confirmed_terminal_dispositions_survive_stale_observers(
    system_operations_session_maker,
):
    """already_terminal/unsupported/superseded are retained like safe_point."""
    from moonmind.schemas.workflow_control_models import WorkflowControlBatch

    async with system_operations_session_maker() as session:
        service = SystemOperationsService(session)
        await service.submit(
            WorkerOperationCommand(
                action="pause", mode="quiesce", reason="Terminal dispositions",
                confirmation="pause", idempotencyKey="terminal-dispositions",
            ),
            actor_user_id=None,
        )
        audit = await service._audit_event_by_idempotency_key("terminal-dispositions")
        batch = WorkflowControlBatch.model_validate(audit.new_value_json["control"])
        batch.enumerated = True
        batch.targets = [WorkflowControlTarget(
            workflowId=f"workflow-{i}", runId=f"run-{i}", updateId=f"update-{i}", state=state,
        ) for i, state in enumerate(["already_terminal", "unsupported", "superseded"])]
        await service._persist_control_progress(audit.id, batch)
        stale = batch.model_copy(deep=True)
        for target in stale.targets:
            target.state, target.reason = "requested", None
        merged = await service._persist_control_progress(audit.id, stale)
        assert [target.state for target in merged.targets] == [
            "already_terminal", "unsupported", "superseded",
        ]


@pytest.mark.asyncio
async def test_empty_enumeration_reports_empty_scope(system_operations_session_maker):
    """No eligible runs is reported as empty, never as workflow-confirmed."""
    from moonmind.schemas.workflow_control_models import WorkflowControlBatch

    async with system_operations_session_maker() as session:
        service = SystemOperationsService(session)
        await service.submit(
            WorkerOperationCommand(
                action="pause", mode="quiesce", reason="Empty scope",
                confirmation="pause", idempotencyKey="empty-scope",
            ),
            actor_user_id=None,
        )
        audit = await service._audit_event_by_idempotency_key("empty-scope")
        batch = WorkflowControlBatch.model_validate(audit.new_value_json["control"])
        batch.enumerated = True
        batch.targets = []
        await service._persist_control_progress(audit.id, batch)
        snapshot = await service.snapshot()
        assert snapshot.control.status == "empty"
