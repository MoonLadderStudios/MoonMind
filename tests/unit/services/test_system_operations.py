from __future__ import annotations

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

    async def send_quiesce_pause_signal(self) -> int:
        self.pause_calls += 1
        return 3

    async def send_resume_signal(self) -> int:
        self.resume_calls += 1
        return 2


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

        snapshot = await SystemOperationsService(session).snapshot()

    assert snapshot.system.workers_paused is False
    assert snapshot.metrics.queued == 0
    assert snapshot.metrics.is_drained is True
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

    assert snapshot.signal_status == "succeeded:3"


@pytest.mark.asyncio
async def test_quiesce_and_resume_fail_fast_when_signal_handler_is_missing(
    system_operations_session_maker,
) -> None:
    async with system_operations_session_maker() as session:
        service = SystemOperationsService(session, temporal_service=object())

        with pytest.raises(SystemOperationUnavailableError, match="Quiesce pause"):
            await service.submit(
                WorkerOperationCommand(
                    action="pause",
                    mode="quiesce",
                    reason="Stop claims",
                    confirmation="Pause workers confirmed",
                    idempotencyKey=_idempotency_key("missing-pause-handler"),
                ),
                actor_user_id=uuid4(),
            )

        with pytest.raises(SystemOperationUnavailableError, match="Resume signal"):
            await service.submit(
                WorkerOperationCommand(
                    action="resume",
                    reason="Done",
                    idempotencyKey=_idempotency_key("missing-resume-handler"),
                ),
                actor_user_id=uuid4(),
            )


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

    assert first.signal_status == "succeeded:3"
    assert second.signal_status == "succeeded:3"
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
        snapshot = await SystemOperationsService(session).snapshot()

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
