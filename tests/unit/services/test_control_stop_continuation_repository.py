from copy import deepcopy

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import ControlStopContinuationRecord
from api_service.services.control_stop_continuation import (
    SqlControlStopContinuationRepository,
)
from moonmind.workflows.executions.control_stop_continuation import (
    ControlStopContinuationContract,
    ControlStopContinuationError,
)
from tests.unit.services.test_control_stop_continuation import _evidence
from tests.unit.workflows.executions.test_control_stop_continuation import _payload


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: ControlStopContinuationRecord.__table__.create(
                sync_connection
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_sql_repository_persists_one_reservation_across_sessions() -> None:
    engine, sessions = await _database()
    payload = _payload()
    evidence = _evidence(payload)
    async with sessions() as session:
        session.add(
            ControlStopContinuationRecord(
                source_workflow_id=payload["sourceWorkflowId"],
                source_run_id=payload["sourceRunId"],
                control_stop_id=payload["controlStopId"],
                contract_payload=deepcopy(payload),
                artifact_digests=dict(evidence.artifact_digests),
                deployment_generation=payload["deploymentGeneration"],
                deployment_promoted=True,
            )
        )
        await session.commit()

    contract = ControlStopContinuationContract.model_validate(payload)
    async with sessions() as session:
        first = await SqlControlStopContinuationRepository(
            session
        ).reserve_destination(contract=contract)
        await session.commit()
    async with sessions() as session:
        second = await SqlControlStopContinuationRepository(
            session
        ).reserve_destination(contract=contract)
        await session.commit()

    assert first.created is True
    assert second.created is False
    assert first.destination_workflow_id == second.destination_workflow_id
    assert second.workspace_head_ref == payload["workspaceHeadRef"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_sql_repository_rejects_conflicting_frozen_destination() -> None:
    engine, sessions = await _database()
    payload = _payload()
    evidence = _evidence(payload)
    async with sessions() as session:
        session.add(
            ControlStopContinuationRecord(
                source_workflow_id=payload["sourceWorkflowId"],
                source_run_id=payload["sourceRunId"],
                control_stop_id=payload["controlStopId"],
                contract_payload=deepcopy(payload),
                artifact_digests=dict(evidence.artifact_digests),
                deployment_generation=payload["deploymentGeneration"],
                deployment_promoted=True,
                destination_workflow_id="conflicting-destination",
                workspace_head_ref=payload["workspaceHeadRef"],
                remaining_work_ref=payload["remainingWorkRef"],
            )
        )
        await session.commit()
        with pytest.raises(ControlStopContinuationError, match="conflicts"):
            await SqlControlStopContinuationRepository(
                session
            ).reserve_destination(
                contract=ControlStopContinuationContract.model_validate(payload)
            )

    await engine.dispose()
