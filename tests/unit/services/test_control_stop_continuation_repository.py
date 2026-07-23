from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.common import WorkflowIDReusePolicy

from api_service.db.models import ControlStopContinuationRecord
from api_service.services.control_stop_continuation import (
    SqlControlStopContinuationRepository,
    TemporalControlStopContinuationStarter,
)
from moonmind.workflows.executions.control_stop_continuation import (
    ControlStopContinuationContract,
    ControlStopContinuationError,
)
from moonmind.workflows.temporal.client import WorkflowStartResult
from moonmind.workflows.temporal.workflows.control_stop_continuation import (
    WORKFLOW_NAME,
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


@pytest.mark.asyncio
async def test_sql_repository_commits_reservation_before_returning() -> None:
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
        await SqlControlStopContinuationRepository(session).reserve_destination(
            contract=contract
        )
        # No caller commit: the repository owns the persistence-before-start
        # authority boundary.
    async with sessions() as session:
        row = (
            await session.execute(select(ControlStopContinuationRecord))
        ).scalar_one()
        assert row.destination_workflow_id == contract.destination_workflow_id

    await engine.dispose()


class _TemporalAdapter:
    def __init__(self, result: WorkflowStartResult) -> None:
        self.result = result
        self.calls = []

    async def start_workflow(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


@pytest.mark.asyncio
async def test_temporal_starter_uses_registered_type_and_reconciles_duplicate() -> None:
    payload = _payload()
    workflow_id = ControlStopContinuationContract.model_validate(
        payload
    ).destination_workflow_id
    adapter = _TemporalAdapter(
        WorkflowStartResult(workflow_id=workflow_id, run_id="existing-run")
    )

    run_id = await TemporalControlStopContinuationStarter(
        adapter=adapter  # type: ignore[arg-type]
    ).start_or_reconcile(workflow_id=workflow_id, input_payload=payload)

    assert run_id == "existing-run"
    assert adapter.calls == [
        {
            "workflow_type": WORKFLOW_NAME,
            "workflow_id": workflow_id,
            "input_args": payload,
            "id_reuse_policy": WorkflowIDReusePolicy.REJECT_DUPLICATE,
            "memo": {
                "owner_id": payload["ownerId"],
                "owner_type": payload["ownerType"],
                "source_workflow_id": payload["sourceWorkflowId"],
                "source_run_id": payload["sourceRunId"],
            },
            "search_attributes": {
                "mm_owner_id": payload["ownerId"],
                "mm_owner_type": payload["ownerType"],
            },
        }
    ]


@pytest.mark.asyncio
async def test_temporal_starter_rejects_mismatched_reconciled_identity() -> None:
    adapter = _TemporalAdapter(
        WorkflowStartResult(workflow_id="other", run_id="unexpected-run")
    )

    with pytest.raises(ControlStopContinuationError, match="different"):
        await TemporalControlStopContinuationStarter(
            adapter=adapter  # type: ignore[arg-type]
        ).start_or_reconcile(
            workflow_id="expected",
            input_payload=_payload(),
        )
