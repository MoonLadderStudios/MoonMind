from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import Base, SettingsAuditEvent
from api_service.services.system_operations import (
    SystemOperationValidationError,
    SystemOperationsService,
    WorkerOperationCommand,
)


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
                WorkerOperationCommand(action="pause", mode="drain", reason="Maint"),
                actor_user_id=uuid4(),
            )

        snapshot = await service.submit(
            WorkerOperationCommand(
                action="pause",
                mode="drain",
                reason="Maint",
                confirmation="Pause workers confirmed",
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
            ),
            actor_user_id=uuid4(),
        )
        await service.submit(
            WorkerOperationCommand(action="resume", reason="Done"),
            actor_user_id=uuid4(),
        )

    assert temporal.pause_calls == 1
    assert temporal.resume_calls == 1


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
    assert "confirmation" not in audit.new_value_json
