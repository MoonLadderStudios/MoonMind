"""Pure builder for the failed-run recovery manifest (MM-881).

Every failed run emits a recovery manifest before terminal failure is reported.
The manifest names the last accepted step, the failed logical step and its
execution ordinal, checkpoint refs, the checkpoint validation result,
side-effect dispositions, resume allowance, and the blocked reason when resume
is not allowed.

The builder is a pure function over compact, ref-only workflow state so it can
be exercised at the workflow boundary and unit tested directly. It never embeds
large or unsafe content; only refs, dispositions, and bounded reason codes flow
into the manifest. Restore-time checkpoint re-validation still happens (and
fails closed) when a resume is actually attempted; this manifest records the
recovery decision and evidence available at terminal failure.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from moonmind.schemas.temporal_models import (
    EvidenceRefStatusModel,
    FailedRunRecoveryManifestModel,
    RecoveryCheckpointValidationModel,
    RecoverySideEffectDispositionModel,
    RecoveryStepRefModel,
)
from moonmind.workflows.executions.runtime_capabilities import RuntimeExecutionCapabilities
from moonmind.workflows.temporal.recovery_decision import decide_checkpoint_recovery

# Checkpoint boundaries a failed step can resume from, most-progressed first.
# A resumable checkpoint restores workspace state so the failed logical step can
# be re-executed without rerunning already-accepted prior steps.
_RESUMABLE_BOUNDARY_PRIORITY: tuple[str, ...] = (
    "after_gate",
    "after_execution",
    "before_execution",
    "after_prepare",
    "before_recovery_restoration",
    "before_publication",
)

# Map checkpoint validation failure codes to manifest validation outcomes.
_VALIDATION_FAILURE_RESULT: dict[str, str] = {
    "artifact_missing": "missing",
    "artifact_unauthorized": "unauthorized",
    "artifact_corrupted": "corrupted",
    "unsafe_checkpoint": "corrupted",
    "policy_incompatible": "incompatible",
    "workspace_incompatible": "incompatible",
    "checkpoint_kind_incompatible": "incompatible",
    "unsupported_checkpoint_kind": "incompatible",
}

# Manifest validation outcome -> EvidenceRefStatusModel status for a degraded
# checkpoint evidence ref.
_EVIDENCE_STATUS_FOR_RESULT: dict[str, str] = {
    "missing": "missing",
    "corrupted": "invalid",
    "unauthorized": "unauthorized",
    "incompatible": "incompatible",
    "invalid": "invalid",
    "not_evaluated": "unavailable",
}

# Manifest validation outcome -> compact blocked reason code.
_BLOCKED_REASON_FOR_RESULT: dict[str, str] = {
    "missing": "no_checkpoint_evidence",
    "corrupted": "checkpoint_corrupted",
    "unauthorized": "checkpoint_unauthorized",
    "incompatible": "workspace_policy_incompatible",
    "invalid": "checkpoint_invalid",
    "not_evaluated": "checkpoint_not_validated",
}

# Side-effect classes whose external effects cannot safely repeat; an accepted
# (already-occurred) effect of these classes must be compensated on resume.
_NON_IDEMPOTENT_SIDE_EFFECT_CLASSES = {
    "external_non_idempotent",
    "publication",
    "provider_account",
}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    return candidate or None


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 1 else None


def _row_terminal_disposition(
    row: Mapping[str, Any],
    terminal_dispositions: Mapping[str, str],
) -> str | None:
    logical_step_id = _text(row.get("logicalStepId"))
    if logical_step_id and logical_step_id in terminal_dispositions:
        disposition = _text(terminal_dispositions.get(logical_step_id))
        if disposition:
            return disposition
    return _text(row.get("terminalDisposition"))


def _checkpoint_refs_for_step(
    logical_step_id: str | None,
    *,
    rows_by_id: Mapping[str, Mapping[str, Any]],
    checkpoint_refs_by_boundary: Mapping[str, Mapping[str, str]],
) -> dict[str, str]:
    """Merge checkpoint refs (by boundary) from workflow state and ledger row."""

    if not logical_step_id:
        return {}
    merged: dict[str, str] = {}
    by_boundary = checkpoint_refs_by_boundary.get(logical_step_id)
    if isinstance(by_boundary, Mapping):
        for boundary, ref in by_boundary.items():
            boundary_text = _text(boundary)
            ref_text = _text(ref)
            if boundary_text and ref_text:
                merged.setdefault(boundary_text, ref_text)
    row = rows_by_id.get(logical_step_id)
    if isinstance(row, Mapping):
        refs = row.get("refs")
        if isinstance(refs, Mapping):
            row_by_boundary = refs.get("checkpointRefsByBoundary")
            if isinstance(row_by_boundary, Mapping):
                for boundary, ref in row_by_boundary.items():
                    boundary_text = _text(boundary)
                    ref_text = _text(ref)
                    if boundary_text and ref_text:
                        merged.setdefault(boundary_text, ref_text)
    return merged


def _select_resume_checkpoint(
    refs_by_boundary: Mapping[str, str],
) -> tuple[str | None, str | None]:
    """Pick the most-progressed resumable checkpoint ref and its boundary."""

    for boundary in _RESUMABLE_BOUNDARY_PRIORITY:
        ref = refs_by_boundary.get(boundary)
        if ref:
            return ref, boundary
    # Fall back to any present checkpoint ref so checkpoint-backed recovery
    # stays the default failed-run path even at a non-standard boundary.
    for boundary, ref in refs_by_boundary.items():
        if ref:
            return ref, boundary
    return None, None


def _disposition_for_side_effect(record: Mapping[str, Any]) -> str:
    disposition = _text(record.get("disposition"))
    effect_class = _text(record.get("class"))
    kind = _text(record.get("kind")) or "normal"
    if disposition == "blocked":
        return "blocked"
    if disposition in {"discarded", "candidate"}:
        return "discarded"
    # Accepted (or unspecified) effects: an already-occurred non-idempotent
    # external effect must be compensated before resume can reuse the step.
    if (
        kind == "normal"
        and effect_class in _NON_IDEMPOTENT_SIDE_EFFECT_CLASSES
    ):
        return "needs_compensation"
    return "accepted"


def _build_side_effect_dispositions(
    side_effect_records: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[RecoverySideEffectDispositionModel]:
    dispositions: list[RecoverySideEffectDispositionModel] = []
    for records in side_effect_records.values():
        if not isinstance(records, Sequence):
            continue
        for record in records:
            if not isinstance(record, Mapping):
                continue
            operation = _text(record.get("operation"))
            effect_class = _text(record.get("class")) or "unknown"
            if not operation:
                continue
            dispositions.append(
                RecoverySideEffectDispositionModel(
                    effect_class=effect_class,
                    operation=operation,
                    disposition=_disposition_for_side_effect(record),
                    target=_text(record.get("target")),
                    reason=_text(record.get("reason")),
                    idempotencyKey=_text(record.get("idempotencyKey")),
                    compensationRef=_text(record.get("compensationRef")),
                )
            )
    return dispositions


def _side_effect_resume_block_reason(
    dispositions: Sequence[RecoverySideEffectDispositionModel],
) -> str | None:
    """Return a blocked reason when a recorded side effect makes resume unsafe.

    Resuming re-executes the failed logical step from its checkpoint boundary,
    so a blocked non-idempotent effect, or an accepted non-idempotent external
    mutation that has not yet been compensated, would be re-applied. Resume must
    stay ineligible until the side-effect policy in
    ``docs/Steps/StepExecutionsAndCheckpointing.md`` §11 has accounted for the
    non-repeatable mutation (recorded as a completed ``compensationRef``).
    """

    block_reason: str | None = None
    for disposition in dispositions:
        if disposition.disposition == "blocked":
            return "side_effect_blocked"
        if (
            disposition.disposition == "needs_compensation"
            and not disposition.compensation_ref
        ):
            block_reason = "side_effect_needs_compensation"
    return block_reason


def _resolve_failed_step(
    *,
    rows: Sequence[Mapping[str, Any]],
    failure_diagnostic: Mapping[str, Any] | None,
    recovery_failed_step_id: str | None,
) -> tuple[str | None, int | None]:
    """Determine the failed logical step id and its execution ordinal."""

    failed_step_id: str | None = None
    if isinstance(failure_diagnostic, Mapping):
        failed_step_id = _text(failure_diagnostic.get("stepId"))
    if not failed_step_id:
        for row in reversed(list(rows)):
            if not isinstance(row, Mapping):
                continue
            if _text(row.get("status")) == "failed":
                failed_step_id = _text(row.get("logicalStepId"))
                if failed_step_id:
                    break
    if not failed_step_id:
        failed_step_id = _text(recovery_failed_step_id)
    if not failed_step_id:
        return None, None
    execution_ordinal: int | None = None
    for row in rows:
        if isinstance(row, Mapping) and _text(row.get("logicalStepId")) == failed_step_id:
            execution_ordinal = _positive_int(row.get("executionOrdinal"))
            break
    return failed_step_id, execution_ordinal


def _resolve_last_accepted_step(
    *,
    rows: Sequence[Mapping[str, Any]],
    terminal_dispositions: Mapping[str, str],
) -> RecoveryStepRefModel | None:
    last_accepted: RecoveryStepRefModel | None = None
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        logical_step_id = _text(row.get("logicalStepId"))
        if not logical_step_id:
            continue
        if _row_terminal_disposition(row, terminal_dispositions) != "accepted":
            continue
        last_accepted = RecoveryStepRefModel(
            logicalStepId=logical_step_id,
            executionOrdinal=_positive_int(row.get("executionOrdinal")),
            terminalDisposition="accepted",
            title=_text(row.get("title")),
        )
    return last_accepted


def build_failed_run_recovery_manifest(
    *,
    workflow_id: str,
    run_id: str,
    created_at: datetime,
    step_ledger_rows: Sequence[Mapping[str, Any]] = (),
    terminal_dispositions: Mapping[str, str] | None = None,
    checkpoint_refs_by_boundary: Mapping[str, Mapping[str, str]] | None = None,
    side_effect_records: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    failure_diagnostic: Mapping[str, Any] | None = None,
    recovery_failed_step_id: str | None = None,
    checkpoint_validation_failure: Mapping[str, Any] | None = None,
    checkpoint_kind: str | None = None,
    runtime_capabilities: RuntimeExecutionCapabilities | None = None,
    restore_route_registered: bool = False,
) -> FailedRunRecoveryManifestModel:
    """Build a fail-closed recovery manifest for a failed run.

    ``checkpoint_validation_failure`` is supplied when the run failed precisely
    because a resume-path checkpoint validation/restoration failed; it carries
    ``failureCode`` and optional ``checkpointRef`` so the manifest records the
    real degraded outcome (missing/corrupted/unauthorized/incompatible) and
    blocks resume rather than degrading to a silent full rerun.
    """

    rows = [row for row in step_ledger_rows if isinstance(row, Mapping)]
    rows_by_id: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        logical_step_id = _text(row.get("logicalStepId"))
        if logical_step_id:
            rows_by_id.setdefault(logical_step_id, row)
    terminal_dispositions = dict(terminal_dispositions or {})
    checkpoint_refs_by_boundary = dict(checkpoint_refs_by_boundary or {})
    side_effect_records = dict(side_effect_records or {})

    failure_stage = None
    failure_category = None
    if isinstance(failure_diagnostic, Mapping):
        failure_stage = _text(failure_diagnostic.get("stage"))
        failure_category = _text(failure_diagnostic.get("category"))

    failed_step_id, failed_execution_ordinal = _resolve_failed_step(
        rows=rows,
        failure_diagnostic=failure_diagnostic,
        recovery_failed_step_id=recovery_failed_step_id,
    )
    last_accepted_step = _resolve_last_accepted_step(
        rows=rows, terminal_dispositions=terminal_dispositions
    )

    # Gather checkpoint refs available for the failed step (and, as fallback,
    # the last accepted step so a resume can restore from the prior boundary).
    failed_refs = _checkpoint_refs_for_step(
        failed_step_id,
        rows_by_id=rows_by_id,
        checkpoint_refs_by_boundary=checkpoint_refs_by_boundary,
    )
    accepted_refs: dict[str, str] = {}
    if last_accepted_step is not None:
        accepted_refs = _checkpoint_refs_for_step(
            last_accepted_step.logical_step_id,
            rows_by_id=rows_by_id,
            checkpoint_refs_by_boundary=checkpoint_refs_by_boundary,
        )
    resume_refs = failed_refs or accepted_refs
    resume_ref, resume_boundary = _select_resume_checkpoint(resume_refs)

    side_effect_dispositions = _build_side_effect_dispositions(side_effect_records)

    # Determine the checkpoint validation outcome.
    validation_failure_code: str | None = None
    degraded_result: str | None = None
    degraded_ref: str | None = None
    if isinstance(checkpoint_validation_failure, Mapping):
        validation_failure_code = _text(
            checkpoint_validation_failure.get("failureCode")
        )
        degraded_ref = _text(
            checkpoint_validation_failure.get("checkpointRef")
        ) or resume_ref
        degraded_result = _VALIDATION_FAILURE_RESULT.get(
            validation_failure_code or "", "invalid"
        )

    checkpoint_evidence: list[EvidenceRefStatusModel] = []
    for boundary, ref in resume_refs.items():
        is_degraded = (
            degraded_result is not None
            and degraded_ref is not None
            and ref == degraded_ref
        )
        if is_degraded:
            checkpoint_evidence.append(
                EvidenceRefStatusModel(
                    category="checkpoint",
                    status=_EVIDENCE_STATUS_FOR_RESULT.get(degraded_result, "invalid"),
                    artifactRef=ref,
                    boundary=boundary,
                    reasonCode=validation_failure_code or degraded_result,
                )
            )
        else:
            checkpoint_evidence.append(
                EvidenceRefStatusModel(
                    category="checkpoint",
                    status="available",
                    artifactRef=ref,
                    boundary=boundary,
                )
            )

    if degraded_result is not None:
        validation = RecoveryCheckpointValidationModel(
            result=degraded_result,
            failureCode=validation_failure_code,
            checkpointRef=degraded_ref,
            boundary=resume_boundary,
        )
    elif resume_ref:
        validation = RecoveryCheckpointValidationModel(
            result="valid",
            checkpointRef=resume_ref,
            boundary=resume_boundary,
        )
    else:
        validation = RecoveryCheckpointValidationModel(result="missing")

    # Resume is allowed only when checkpoint validation is valid, a checkpoint
    # ref exists, there is a failed logical step to resume, and no recorded
    # side effect still requires compensation. A blocked or uncompensated
    # non-idempotent external mutation would be re-applied on resume, so it
    # keeps resume ineligible until compensation is recorded.
    side_effect_block_reason = _side_effect_resume_block_reason(
        side_effect_dispositions
    )
    eligibility = decide_checkpoint_recovery(
        checkpoint_ref=resume_ref,
        checkpoint_boundary=resume_boundary,
        checkpoint_kind=checkpoint_kind,
        capabilities=runtime_capabilities,
        restore_route_registered=restore_route_registered,
        artifact_valid=validation.result == "valid" and bool(failed_step_id),
        side_effect_safe=side_effect_block_reason is None,
        source_workflow_id=workflow_id,
        source_run_id=run_id,
        evidence=checkpoint_evidence,
    )
    resume_allowed = eligibility.eligible

    if resume_allowed:
        blocked_reason = None
    else:
        if not failed_step_id:
            blocked_reason = "no_failed_step_execution_to_resume"
        elif degraded_result is not None:
            blocked_reason = _BLOCKED_REASON_FOR_RESULT.get(
                degraded_result, "checkpoint_invalid"
            )
        elif not resume_ref:
            blocked_reason = "no_checkpoint_evidence"
        elif validation.result != "valid":
            blocked_reason = _BLOCKED_REASON_FOR_RESULT.get(
                validation.result, "checkpoint_not_validated"
            )
        else:
            # Checkpoint evidence is valid; resume is blocked solely by a
            # blocked or uncompensated non-idempotent side effect.
            blocked_reason = side_effect_block_reason or "checkpoint_not_validated"
        # Stable machine code is authoritative; blockedReason remains the
        # compact legacy projection during the Temporal replay window.
        blocked_reason = eligibility.disabled_reason_code or blocked_reason

    return FailedRunRecoveryManifestModel(
        workflowId=workflow_id,
        runId=run_id,
        failureStage=failure_stage,
        failureCategory=failure_category,
        failedLogicalStepId=failed_step_id,
        failedExecutionOrdinal=failed_execution_ordinal,
        lastAcceptedStep=last_accepted_step,
        checkpointRefs=checkpoint_evidence,
        validation=validation,
        sideEffectDispositions=side_effect_dispositions,
        resumeAllowed=resume_allowed,
        blockedReason=blocked_reason,
        recoveryEligibility=eligibility,
        createdAt=created_at,
    )
