"""SQL persistence adapter for trusted control-stop continuation admission."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import ControlStopContinuationRecord
from moonmind.services.control_stop_continuation import (
    ControlStopContinuationReservation,
    ControlStopSourceEvidence,
)
from moonmind.workflows.executions.control_stop_continuation import (
    ControlStopContinuationContract,
    ControlStopContinuationError,
)
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.workflows.control_stop_continuation import (
    WORKFLOW_NAME,
)


class TemporalControlStopContinuationStarter:
    """Start or reconcile the one frozen destination workflow identity."""

    def __init__(self, adapter: TemporalClientAdapter | None = None) -> None:
        self._adapter = adapter or TemporalClientAdapter()

    async def start_or_reconcile(
        self, *, workflow_id: str, input_payload: Mapping[str, Any]
    ) -> str:
        result = await self._adapter.start_workflow(
            workflow_type=WORKFLOW_NAME,
            workflow_id=workflow_id,
            input_args=dict(input_payload),
        )
        if result.workflow_id != workflow_id:
            raise ControlStopContinuationError(
                "Temporal reconciled a different continuation workflow identity"
            )
        return result.run_id


class SqlControlStopContinuationRepository:
    """Reserve a destination in the same transaction as its lineage."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _source_row(
        self,
        *,
        source_workflow_id: str,
        source_run_id: str,
        control_stop_id: str,
        for_update: bool = False,
    ) -> ControlStopContinuationRecord:
        statement = select(ControlStopContinuationRecord).where(
            ControlStopContinuationRecord.source_workflow_id == source_workflow_id,
            ControlStopContinuationRecord.source_run_id == source_run_id,
            ControlStopContinuationRecord.control_stop_id == control_stop_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await self._session.execute(statement)).scalar_one_or_none()
        if row is None:
            raise ControlStopContinuationError(
                "authoritative control-stop continuation evidence was not found"
            )
        return row

    async def load_source_evidence(
        self, *, source_workflow_id: str, source_run_id: str, control_stop_id: str
    ) -> ControlStopSourceEvidence:
        row = await self._source_row(
            source_workflow_id=source_workflow_id,
            source_run_id=source_run_id,
            control_stop_id=control_stop_id,
        )
        return ControlStopSourceEvidence(
            contract_payload=dict(row.contract_payload),
            artifact_digests={
                str(key): str(value) for key, value in row.artifact_digests.items()
            },
            current_deployment_generation=row.deployment_generation,
            deployment_promoted=row.deployment_promoted,
        )

    async def reserve_destination(
        self, *, contract: ControlStopContinuationContract
    ) -> ControlStopContinuationReservation:
        async with self._session.begin_nested():
            row = await self._source_row(
                source_workflow_id=contract.source_workflow_id,
                source_run_id=contract.source_run_id,
                control_stop_id=contract.control_stop_id,
                for_update=True,
            )
            created = row.destination_workflow_id is None
            if created:
                row.destination_workflow_id = contract.destination_workflow_id
                row.workspace_head_ref = contract.workspace_head_ref
                row.remaining_work_ref = contract.remaining_work_ref
                row.reserved_at = datetime.now(UTC)
                await self._session.flush()
            elif (
                row.destination_workflow_id != contract.destination_workflow_id
                or row.workspace_head_ref != contract.workspace_head_ref
                or row.remaining_work_ref != contract.remaining_work_ref
            ):
                raise ControlStopContinuationError(
                    "existing control-stop reservation conflicts with frozen contract"
                )

        # Persist lineage before the external Temporal start side effect.
        await self._session.commit()

        return ControlStopContinuationReservation(
            destination_workflow_id=str(row.destination_workflow_id),
            source_workflow_id=row.source_workflow_id,
            source_run_id=row.source_run_id,
            control_stop_id=row.control_stop_id,
            workspace_head_ref=str(row.workspace_head_ref),
            remaining_work_ref=str(row.remaining_work_ref),
            created=created,
        )
