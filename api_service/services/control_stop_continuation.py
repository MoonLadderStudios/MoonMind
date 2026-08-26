"""SQL persistence adapter for trusted control-stop continuation admission."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.common import WorkflowIDReusePolicy

from api_service.db.models import ControlStopContinuationRecord
from moonmind.config.settings import settings
from moonmind.services.control_stop_continuation import (
    ControlStopContinuationReservation,
    ControlStopSourceEvidence,
)
from moonmind.workflows.executions.control_stop_continuation import (
    ControlStopContinuationContract,
    ControlStopContinuationError,
    ControlStopContinuationWorkflowInput,
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
        workflow_input = ControlStopContinuationWorkflowInput.model_validate(
            dict(input_payload)
        )
        contract = workflow_input.contract
        result = await self._adapter.start_workflow(
            workflow_type=WORKFLOW_NAME,
            workflow_id=workflow_id,
            input_args=dict(input_payload),
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            memo={
                "owner_id": contract.owner_id,
                "owner_type": contract.owner_type,
                "source_workflow_id": contract.source_workflow_id,
                "source_run_id": contract.source_run_id,
            },
            search_attributes={
                "mm_owner_id": contract.owner_id,
                "mm_owner_type": contract.owner_type,
            },
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

    async def load_source_identity(
        self, *, source_workflow_id: str, source_run_id: str
    ) -> tuple[str, ControlStopSourceEvidence]:
        statement = select(ControlStopContinuationRecord).where(
            ControlStopContinuationRecord.source_workflow_id == source_workflow_id,
            ControlStopContinuationRecord.source_run_id == source_run_id,
        )
        rows = list((await self._session.execute(statement)).scalars())
        if len(rows) != 1:
            raise ControlStopContinuationError(
                "authoritative control-stop continuation identity was not found"
                if not rows
                else "authoritative control-stop continuation identity is ambiguous"
            )
        row = rows[0]
        return row.control_stop_id, ControlStopSourceEvidence(
            contract_payload=dict(row.contract_payload),
            artifact_digests={
                str(key): str(value) for key, value in row.artifact_digests.items()
            },
            current_deployment_generation=row.deployment_generation,
            deployment_promoted=row.deployment_promoted,
        )

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
        contract = ControlStopContinuationContract.model_validate(
            dict(row.contract_payload)
        )
        flags = settings.feature_flags
        canary_owners = {
            item.strip()
            for item in flags.control_stop_continuation_canary_owner_ids.split(",")
            if item.strip()
        }
        allowed_provider_profiles = {
            item.strip()
            for item in (
                flags.control_stop_continuation_allowed_provider_profile_ids.split(",")
            )
            if item.strip()
        }
        allowed_execution_profiles = {
            item.strip()
            for item in (
                flags.control_stop_continuation_allowed_execution_profile_refs.split(
                    ","
                )
            )
            if item.strip()
        }
        allowed_launch_policies = {
            item.strip()
            for item in (
                flags.control_stop_continuation_allowed_launch_policy_refs.split(",")
            )
            if item.strip()
        }
        deployment_promoted = (
            flags.control_stop_continuation_enabled
            and not flags.control_stop_continuation_shadow
            and (not canary_owners or contract.owner_id in canary_owners)
            and contract.lane.provider_profile_id in allowed_provider_profiles
            and contract.lane.execution_profile_id in allowed_execution_profiles
            and contract.lane.launch_policy_ref in allowed_launch_policies
        )
        return ControlStopSourceEvidence(
            contract_payload=dict(row.contract_payload),
            artifact_digests={
                str(key): str(value) for key, value in row.artifact_digests.items()
            },
            current_deployment_generation=(flags.control_stop_continuation_generation),
            deployment_promoted=deployment_promoted,
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
                row.contract_payload = contract.model_dump(by_alias=True, mode="json")
                row.reserved_at = datetime.now(UTC)
                await self._session.flush()
            elif (
                row.destination_workflow_id != contract.destination_workflow_id
                or row.workspace_head_ref != contract.workspace_head_ref
                or row.remaining_work_ref != contract.remaining_work_ref
                or dict(row.contract_payload)
                != contract.model_dump(by_alias=True, mode="json")
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
