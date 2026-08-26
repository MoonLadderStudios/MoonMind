"""Trusted admission command for deterministic control-stop continuations.

The persistence implementation owns the transaction that reserves the destination
identity and its bidirectional lineage.  The command deliberately starts Temporal
only after that transaction succeeds and reconciles duplicate submissions against
the same reserved destination.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from moonmind.workflows.executions.control_stop_continuation import (
    ContinuationBudgetGrant,
    ControlStopContinuationContract,
    ControlStopContinuationError,
    ControlStopContinuationWorkflowInput,
)


@dataclass(frozen=True)
class ControlStopSourceEvidence:
    """Authoritative source projection loaded by the trusted persistence boundary."""

    contract_payload: Mapping[str, Any]
    artifact_digests: Mapping[str, str]
    current_deployment_generation: str
    deployment_promoted: bool


@dataclass(frozen=True)
class ControlStopContinuationReservation:
    destination_workflow_id: str
    source_workflow_id: str
    source_run_id: str
    control_stop_id: str
    workspace_head_ref: str
    remaining_work_ref: str
    created: bool


class ControlStopContinuationRepository(Protocol):
    async def load_source_evidence(
        self, *, source_workflow_id: str, source_run_id: str, control_stop_id: str
    ) -> ControlStopSourceEvidence:
        raise NotImplementedError

    async def reserve_destination(
        self, *, contract: ControlStopContinuationContract
    ) -> ControlStopContinuationReservation:
        """Atomically reserve idempotency identity and bidirectional lineage."""


class ControlStopContinuationStarter(Protocol):
    async def start_or_reconcile(
        self, *, workflow_id: str, input_payload: Mapping[str, Any]
    ) -> str:
        """Start the frozen workflow or return its existing run identifier."""


def _assert_authoritative_evidence(
    contract: ControlStopContinuationContract,
    evidence: ControlStopSourceEvidence,
) -> None:
    required = {
        contract.gate_result_ref: contract.gate_result_digest,
        contract.remaining_work_ref: contract.remaining_work_digest,
        contract.checkpoint_ref: contract.checkpoint_digest,
        contract.workspace_head_ref: contract.workspace_head_digest,
        contract.workspace_manifest_ref: contract.workspace_manifest_digest,
        contract.task_input_snapshot_ref: contract.task_input_snapshot_digest,
        contract.plan_ref: contract.plan_digest,
        contract.lane.launch_policy_ref: contract.lane.launch_policy_digest,
        contract.lane.capability_snapshot_ref: contract.lane.capability_snapshot_digest,
        contract.lane.effective_launch_snapshot_ref: (
            contract.lane.effective_launch_snapshot_digest
        ),
        contract.verification_instruction_ref: (
            contract.verification_instruction_digest
        ),
    }
    if contract.instruction_changes_ref and contract.instruction_changes_digest:
        required[contract.instruction_changes_ref] = contract.instruction_changes_digest
    stale = [
        ref
        for ref, expected_digest in required.items()
        if evidence.artifact_digests.get(ref) != expected_digest
    ]
    if stale:
        raise ControlStopContinuationError(
            "authoritative continuation evidence is missing or stale: "
            + ", ".join(sorted(stale))
        )
    if (
        not evidence.deployment_promoted
        or evidence.current_deployment_generation != contract.deployment_generation
    ):
        raise ControlStopContinuationError(
            "control-stop continuation deployment is not currently promoted"
        )


def _select_continuation_budget(
    *,
    authorized: ContinuationBudgetGrant,
    requested: ContinuationBudgetGrant,
) -> ContinuationBudgetGrant:
    """Validate an explicit operator selection within the frozen grant ceiling."""

    if requested.grant_id != authorized.grant_id:
        raise ControlStopContinuationError(
            "requested continuation budget uses a different grant identity"
        )
    if (
        requested.consumed_attempts != 0
        or requested.consecutive_no_progress_attempts != 0
    ):
        raise ControlStopContinuationError(
            "a new continuation budget must begin with zero consumption"
        )
    if (
        requested.max_attempts > authorized.max_attempts
        or requested.max_consecutive_no_progress_attempts
        > authorized.max_consecutive_no_progress_attempts
    ):
        raise ControlStopContinuationError(
            "requested continuation budget exceeds the authorized frozen grant"
        )
    return requested


async def admit_control_stop_continuation(
    *,
    source_workflow_id: str,
    source_run_id: str,
    control_stop_id: str,
    continuation_budget: ContinuationBudgetGrant,
    instruction_changes_ref: str | None,
    instruction_changes_digest: str | None,
    repository: ControlStopContinuationRepository,
    starter: ControlStopContinuationStarter,
) -> ControlStopContinuationReservation:
    """Admit, reserve, and start/reconcile one linked continuation.

    All mutable source and deployment reads happen before destination reservation.
    The frozen contract is then the sole workflow input.
    """

    evidence = await repository.load_source_evidence(
        source_workflow_id=source_workflow_id,
        source_run_id=source_run_id,
        control_stop_id=control_stop_id,
    )
    contract = ControlStopContinuationContract.model_validate(
        dict(evidence.contract_payload)
    )
    if (
        contract.source_workflow_id != source_workflow_id
        or contract.source_run_id != source_run_id
        or contract.control_stop_id != control_stop_id
    ):
        raise ControlStopContinuationError(
            "requested source identity does not match authoritative evidence"
        )
    selected_budget = _select_continuation_budget(
        authorized=contract.continuation_budget,
        requested=continuation_budget,
    )
    if (
        contract.instruction_changes_ref != instruction_changes_ref
        or contract.instruction_changes_digest != instruction_changes_digest
    ):
        raise ControlStopContinuationError(
            "requested instruction changes do not match authoritative evidence"
        )
    contract = contract.model_copy(update={"continuation_budget": selected_budget})
    _assert_authoritative_evidence(contract, evidence)

    reservation = await repository.reserve_destination(contract=contract)
    if (
        reservation.destination_workflow_id != contract.destination_workflow_id
        or reservation.source_workflow_id != contract.source_workflow_id
        or reservation.source_run_id != contract.source_run_id
        or reservation.control_stop_id != contract.control_stop_id
        or reservation.workspace_head_ref != contract.workspace_head_ref
        or reservation.remaining_work_ref != contract.remaining_work_ref
    ):
        raise ControlStopContinuationError(
            "persisted continuation lineage does not match the frozen contract"
        )
    await starter.start_or_reconcile(
        workflow_id=contract.destination_workflow_id,
        input_payload=ControlStopContinuationWorkflowInput.initial(contract).model_dump(
            by_alias=True, mode="json"
        ),
    )
    return reservation
