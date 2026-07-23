"""Deterministic workflow-entry policy for typed recovery targets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from moonmind.schemas.workflow_recovery_models import WorkflowRecoveryTargetModel


@dataclass(frozen=True)
class RecoveryEntryPolicy:
    target_kind: str
    continuation_phase: str
    checkpoint_ref: str
    checkpoint_boundary: str
    restore_operation_id: str | None
    restore_idempotency_key: str | None
    new_budget_ref: str | None
    publication_idempotency_key: str | None
    publication_observation_ref: str | None
    side_effect_disposition_ref: str
    execution_route: str
    requires_budget_authority: bool
    requires_side_effect_authority: bool
    run_semantic_work: bool
    publication_only: bool
    restoration_only: bool


def compile_recovery_entry_policy(
    payload: Mapping[str, Any],
    *,
    destination_workflow_id: str,
) -> RecoveryEntryPolicy:
    """Re-admit a frozen target before the destination schedules any activity."""

    contract = WorkflowRecoveryTargetModel.model_validate(payload)
    contract.require_admitted()
    if contract.destination.workflow_id != destination_workflow_id:
        raise ValueError("RECOVERY_DESTINATION_IDENTITY_MISMATCH")

    kind = contract.target.kind
    phase = contract.continuation.phase
    publication_only = kind == "publication"
    restoration_only = kind == "restoration_failure"
    execution_routes = {
        "rerun_failed_step": "failed_step",
        "continue_to_gate": "gate",
        "continue_after_gate": "post_gate",
        "continue_to_remediation": "remediation",
        "resume_publication": "publication",
        "retry_restoration": "restoration",
    }
    return RecoveryEntryPolicy(
        target_kind=kind,
        continuation_phase=phase,
        checkpoint_ref=contract.checkpoint.ref,
        checkpoint_boundary=contract.checkpoint.boundary,
        restore_operation_id=contract.target.restore_operation_id,
        restore_idempotency_key=contract.target.restore_idempotency_key,
        new_budget_ref=contract.continuation.new_budget_ref,
        publication_idempotency_key=contract.target.publication_idempotency_key,
        publication_observation_ref=contract.target.publication_observation_ref,
        side_effect_disposition_ref=contract.side_effect_disposition_ref,
        execution_route=execution_routes[phase],
        requires_budget_authority=phase == "continue_to_remediation",
        requires_side_effect_authority=True,
        run_semantic_work=not publication_only and not restoration_only,
        publication_only=publication_only,
        restoration_only=restoration_only,
    )


__all__ = ["RecoveryEntryPolicy", "compile_recovery_entry_policy"]
