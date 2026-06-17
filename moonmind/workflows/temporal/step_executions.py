"""Pure helpers for Step Execution identity, manifests, and idempotency."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import posixpath
import re
from typing import Any, Literal

from pydantic import ValidationError

from moonmind.schemas.step_execution_models import (
    MemorySideEffectSummary,
    StepExecutionIdentityModel,
)
from moonmind.schemas.temporal_models import (
    StepExecutionManifestModel as BoundaryStepExecutionManifestModel,
    WorkspacePolicy,
)
from moonmind.workflows.temporal.step_checkpoints import (
    checkpoint_kinds_for_workspace_policy,
)

GitEffectDisposition = Literal[
    "accepted",
    "candidate",
    "discarded",
    "superseded",
    "blocked",
    "none",
]
SideEffectClass = Literal[
    "workspace_mutation",
    "artifact_write",
    "external_idempotent",
    "external_non_idempotent",
    "publication",
    "provider_account",
    "memory_update",
    "retrieval_index_update",
]
SideEffectDisposition = Literal["accepted", "candidate", "blocked", "discarded"]

_GATED_SIDE_EFFECT_CLASSES = {
    "external_non_idempotent",
    "publication",
    "provider_account",
}
# Side-effect classes whose effects cannot safely repeat. When one of these
# already occurred on a prior attempt, a later attempt must account for it
# explicitly (Section 11, rule 3) rather than pretending the step can reset.
_NON_IDEMPOTENT_SIDE_EFFECT_CLASSES = {
    "external_non_idempotent",
    "publication",
    "provider_account",
}
_COMPENSATION_OPERATION_PREFIX = "compensate"
_GATED_OPERATION_PREFIXES = (
    "jira.transition",
    "repo.merge",
    "repo.publish",
    "deployment.",
    "provider_account.",
)
_SECRET_LIKE_PATTERNS = (
    re.compile(r"ghp_[A-Za-z0-9_]+"),
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    re.compile(r"AIza[A-Za-z0-9_-]+"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"(?i)token\s*=\s*[^/\s&]+"),
    re.compile(r"(?i)password\s*=\s*[^/\s&]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def _sanitize_diagnostic_text(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in _SECRET_LIKE_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _normalized_absolute_workspace_path(value: str | None) -> str | None:
    path = str(value or "").strip().replace("\\", "/")
    if not path.startswith("/"):
        return None
    return posixpath.normpath(path)


def _target_within_approved_workspace_roots(
    target: str | None,
    approved_workspace_roots: Sequence[str],
) -> bool:
    target_path = _normalized_absolute_workspace_path(target)
    if target_path is None:
        return False
    for root in approved_workspace_roots:
        root_path = _normalized_absolute_workspace_path(root)
        if root_path is None:
            continue
        if target_path == root_path or target_path.startswith(f"{root_path}/"):
            return True
    return False


def step_execution_id(
    *,
    workflow_id: str,
    run_id: str,
    logical_step_id: str,
    execution_ordinal: int,
) -> str:
    return StepExecutionIdentityModel(
        workflowId=workflow_id,
        runId=run_id,
        logicalStepId=logical_step_id,
        executionOrdinal=execution_ordinal,
    ).step_execution_id


def step_execution_operation_idempotency_key(
    *,
    workflow_id: str,
    run_id: str,
    logical_step_id: str,
    execution_ordinal: int,
    operation: str,
) -> str:
    operation_id = str(operation or "").strip()
    if not operation_id:
        raise ValueError("operation must be a non-empty string")
    identity = step_execution_id(
        workflow_id=workflow_id,
        run_id=run_id,
        logical_step_id=logical_step_id,
        execution_ordinal=execution_ordinal,
    )
    return f"{identity}:{operation_id}"


def workspace_policy_metadata(
    *,
    policy: WorkspacePolicy,
    checkpoint_ref: str | None = None,
    checkpoint_valid: bool | None = None,
    rejection_reason: str | None = None,
) -> dict[str, Any]:
    """Build compact workspace policy diagnostics for a step execution manifest."""

    required_kinds = checkpoint_kinds_for_workspace_policy(policy)
    checkpoint_text = str(checkpoint_ref or "").strip() or None
    evidence_required = bool(required_kinds) and (
        policy != "fresh_branch_from_source"
        or checkpoint_text is not None
        or checkpoint_valid is not None
    )
    evidence_accepted = (
        not evidence_required
        or (checkpoint_text is not None and checkpoint_valid is not False)
    )
    metadata: dict[str, Any] = {
        "policy": policy,
        "requiredCheckpointKinds": list(required_kinds),
        "checkpointRef": checkpoint_text,
        "checkpointValid": checkpoint_valid,
        "evidenceRequired": evidence_required,
        "evidenceAccepted": evidence_accepted,
    }
    if rejection_reason:
        metadata["rejectionReason"] = str(rejection_reason).strip()
    elif not evidence_accepted:
        metadata["rejectionReason"] = "missing_required_checkpoint_evidence"
    return metadata


def validate_workspace_policy_launch(
    *,
    policy: WorkspacePolicy,
    checkpoint_ref: str | None = None,
    checkpoint_valid: bool | None = None,
) -> dict[str, Any]:
    """Return a deterministic launch gate decision for workspace policy evidence."""

    metadata = workspace_policy_metadata(
        policy=policy,
        checkpoint_ref=checkpoint_ref,
        checkpoint_valid=checkpoint_valid,
    )
    return {
        "allowed": bool(metadata["evidenceAccepted"]),
        "policy": policy,
        "reason": None
        if metadata["evidenceAccepted"]
        else metadata["rejectionReason"],
        "workspace": metadata,
    }


def git_effect_metadata(
    *,
    disposition: GitEffectDisposition,
    baseline_commit: str | None = None,
    head_commit: str | None = None,
    working_tree_diff_ref: str | None = None,
    patch_ref: str | None = None,
    workspace_checkpoint_ref: str | None = None,
    typed_artifact_ref: str | None = None,
    published_ref: str | None = None,
    no_change_accepted: bool = False,
) -> dict[str, Any]:
    """Build compact git effect metadata and enforce accepted-output evidence."""

    effect = {
        "disposition": disposition,
        "baselineCommit": str(baseline_commit or "").strip() or None,
        "headCommit": str(head_commit or "").strip() or None,
        "workingTreeDiffRef": str(working_tree_diff_ref or "").strip() or None,
        "patchRef": str(patch_ref or "").strip() or None,
        "workspaceCheckpointRef": str(workspace_checkpoint_ref or "").strip() or None,
        "typedArtifactRef": str(typed_artifact_ref or "").strip() or None,
        "publishedRef": str(published_ref or "").strip() or None,
        "noChangeAccepted": bool(no_change_accepted),
    }
    accepted_output = any(
        (
            effect["headCommit"],
            effect["publishedRef"],
            effect["typedArtifactRef"],
            effect["noChangeAccepted"],
        )
    )
    effect["acceptedOutputPresent"] = accepted_output
    if disposition == "accepted" and not accepted_output:
        raise ValueError(
            "accepted git effect requires commit/ref, typed artifact, or no-change disposition"
        )
    return effect


def logical_step_success_allowed(
    *,
    outputs: Mapping[str, Any] | None = None,
    git_effect: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether a logical implementation step has accepted output evidence."""

    effect = dict(git_effect or {})
    if effect.get("disposition") == "accepted" and effect.get(
        "acceptedOutputPresent"
    ):
        return True
    output_payload = dict(outputs or {})
    for key in (
        "commitSha",
        "commitRef",
        "branchRef",
        "publishedRef",
        "typedArtifactRef",
        "primaryRef",
        "summaryRef",
    ):
        value = output_payload.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return bool(output_payload.get("noChangeAccepted"))


def side_effect_record(
    *,
    effect_class: SideEffectClass,
    operation: str,
    target: str | None = None,
    idempotency_key: str | None = None,
    workflow_state_accepted: bool = False,
    disposition: SideEffectDisposition | None = None,
    effect_kind: Literal["normal", "cleanup", "compensation"] = "normal",
    reason: str | None = None,
    approved_workspace_roots: Sequence[str] = (),
    memory_effect: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact side-effect decision and gate unsafe effect classes."""

    operation_text = str(operation or "").strip()
    if not operation_text:
        raise ValueError("operation must be a non-empty string")
    key_text = str(idempotency_key or "").strip() or None
    if effect_class == "external_idempotent" and key_text is None:
        raise ValueError("external_idempotent side effects require an idempotency key")
    if effect_kind in {"cleanup", "compensation"} and key_text is None:
        raise ValueError("cleanup and compensation side effects require an idempotency key")
    operation_lower = operation_text.lower()
    gated_operation = any(
        operation_lower.startswith(prefix) for prefix in _GATED_OPERATION_PREFIXES
    )
    workspace_boundary_blocked = (
        effect_class == "workspace_mutation"
        and bool(approved_workspace_roots)
        and not _target_within_approved_workspace_roots(
            target,
            approved_workspace_roots,
        )
    )
    workflow_gate_blocked = (
        effect_class in _GATED_SIDE_EFFECT_CLASSES or gated_operation
    ) and not workflow_state_accepted
    blocked = workspace_boundary_blocked or workflow_gate_blocked
    final_disposition = disposition or ("blocked" if blocked else "accepted")
    record: dict[str, Any] = {
        "class": effect_class,
        "kind": effect_kind,
        "operation": operation_text,
        "target": _sanitize_diagnostic_text(target),
        "idempotencyKey": _sanitize_diagnostic_text(key_text),
        "workflowStateAccepted": workflow_state_accepted,
        "disposition": final_disposition,
    }
    if workspace_boundary_blocked:
        record["reason"] = "workspace_target_outside_approved_roots"
    elif workflow_gate_blocked:
        record["reason"] = (
            _sanitize_diagnostic_text(reason) or "workflow_state_not_gate_approved"
        )
    elif reason:
        record["reason"] = _sanitize_diagnostic_text(reason)
    if memory_effect is not None:
        if effect_class != "memory_update":
            raise ValueError("memory_effect is only valid for memory_update records")
        record["memory"] = MemorySideEffectSummary.model_validate(
            dict(memory_effect)
        ).model_dump(by_alias=True, mode="json")
    return record


def memory_side_effect_summary(
    *,
    state: str,
    target: str,
    reason: str,
    proposal_ref: str,
    source: Mapping[str, Any] | StepExecutionIdentityModel,
    decision_ref: str | None = None,
    application_result_ref: str | None = None,
    privileged_action: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact, validated terminal memory side-effect projection."""

    payload = {
        "state": state,
        "target": target,
        "reason": reason,
        "proposalRef": proposal_ref,
        "decisionRef": decision_ref,
        "applicationResultRef": application_result_ref,
        "source": source,
        "privilegedAction": dict(privileged_action)
        if privileged_action is not None
        else None,
    }
    return MemorySideEffectSummary.model_validate(payload).model_dump(
        by_alias=True,
        mode="json",
    )


def memory_write_gate_decision(
    *,
    target: str,
    terminal_disposition: str | None,
    publication_gate_passed: bool | None,
    policy_decision: str | None,
) -> dict[str, Any]:
    """Return deterministic fail-closed memory promotion gate metadata."""

    target_text = str(target or "").strip()
    decision = str(policy_decision or "").strip()
    repo_target = target_text.startswith("repo://")
    if not decision:
        return {
            "allowed": False,
            "state": "proposed",
            "reason": "missing_policy_decision",
        }
    if decision not in {
        "reject",
        "accept_for_run_context",
        "approve_repo_application",
        "supersede",
        "blocked",
    }:
        return {
            "allowed": False,
            "state": "proposed",
            "reason": "unknown_policy_decision",
        }
    if decision in {"reject", "blocked"}:
        return {
            "allowed": False,
            "state": "rejected" if decision == "reject" else "proposed",
            "reason": "policy_blocked",
        }
    if decision == "supersede":
        return {"allowed": False, "state": "superseded", "reason": "superseded"}
    if decision == "accept_for_run_context":
        if terminal_disposition in {"failed_unrecoverable", "discarded", "blocked"}:
            return {
                "allowed": False,
                "state": "proposed",
                "reason": "terminal_disposition_not_promotable",
            }
        return {
            "allowed": True,
            "state": "accepted_for_run_context",
            "reason": "policy_approved_for_later_attempts",
        }
    if repo_target and terminal_disposition != "accepted":
        return {
            "allowed": False,
            "state": "proposed",
            "reason": "terminal_disposition_not_accepted",
        }
    if repo_target and publication_gate_passed is not True:
        return {
            "allowed": False,
            "state": "proposed",
            "reason": "publication_gate_not_passed",
        }
    return {
        "allowed": True,
        "state": "applied_to_repo" if repo_target else "accepted_for_run_context",
        "reason": "accepted_disposition_and_publication_gate_passed"
        if repo_target
        else "policy_approved_for_later_attempts",
    }


def _side_effect_already_occurred(record: Mapping[str, Any]) -> bool:
    """Return whether a recorded side effect is a non-idempotent effect that ran."""

    if record.get("kind", "normal") not in (None, "normal"):
        # cleanup/compensation records are reconciliation, not original effects.
        return False
    if record.get("class") not in _NON_IDEMPOTENT_SIDE_EFFECT_CLASSES:
        return False
    # Only effects that were actually allowed to run need to be reconciled. A
    # blocked or discarded effect never mutated external state.
    return record.get("disposition") == "accepted"


def compensation_subject_key(record: Mapping[str, Any]) -> str:
    """Return a stable identity for the prior effect a compensation reconciles.

    Used to dedupe compensation across successive reattempts so an
    already-occurred non-idempotent effect is compensated at most once.
    """

    existing_key = str(record.get("idempotencyKey") or "").strip()
    if existing_key:
        return existing_key
    effect_class = str(record.get("class") or "").strip()
    operation = str(record.get("operation") or "").strip()
    target = str(record.get("target") or "").strip()
    return f"{effect_class}:{operation}:{target}"


def already_occurred_non_idempotent_effects(
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Filter recorded side effects to non-idempotent effects that already ran."""

    return [
        dict(record)
        for record in records
        if isinstance(record, Mapping) and _side_effect_already_occurred(record)
    ]


def plan_reattempt_compensation(
    *,
    workflow_id: str,
    run_id: str,
    logical_step_id: str,
    execution_ordinal: int,
    prior_side_effect_records: Sequence[Mapping[str, Any]] = (),
    already_compensated_subjects: Sequence[str] = (),
    policy_permits_non_idempotent_reattempt: bool = False,
) -> dict[str, Any]:
    """Plan explicit, idempotent, observable compensation for a reattempt.

    Implements Section 11 rules 3 and 4: when a non-idempotent external effect
    already occurred on a prior attempt, a later attempt must account for it
    explicitly with compensation that is explicit, idempotent, and observable —
    not merely reset the step.

    The plan is deterministic: each outstanding effect maps to exactly one
    compensation side-effect record whose idempotency key derives from the new
    attempt identity, and effects already compensated on an earlier reattempt
    are skipped so the same external mutation is never compensated twice.
    """

    already_compensated = {
        str(subject).strip()
        for subject in already_compensated_subjects
        if str(subject).strip()
    }
    outstanding = already_occurred_non_idempotent_effects(prior_side_effect_records)
    compensations: list[dict[str, Any]] = []
    accounted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for effect in outstanding:
        subject = compensation_subject_key(effect)
        effect_summary = {
            "class": effect.get("class"),
            "operation": effect.get("operation"),
            "target": effect.get("target"),
            "idempotencyKey": effect.get("idempotencyKey"),
            "subject": subject,
        }
        if subject in already_compensated:
            skipped.append(effect_summary)
            continue
        operation = str(effect.get("operation") or "").strip()
        compensation_operation = f"{_COMPENSATION_OPERATION_PREFIX}:{operation or 'unknown'}"
        compensation_key = step_execution_operation_idempotency_key(
            workflow_id=workflow_id,
            run_id=run_id,
            logical_step_id=logical_step_id,
            execution_ordinal=execution_ordinal,
            operation=compensation_operation,
        )
        record = side_effect_record(
            effect_class="external_idempotent",
            operation=compensation_operation,
            target=effect.get("target"),
            idempotency_key=compensation_key,
            effect_kind="compensation",
            # Compensation is the authorized reconciliation action that makes a
            # reattempt safe; it must be allowed to run rather than gated.
            workflow_state_accepted=True,
            reason=f"compensate_prior_{effect.get('class')}",
        )
        record["compensates"] = effect_summary
        compensations.append(record)
        accounted.append(effect_summary)

    requires_compensation = bool(accounted)
    # Compensation is only complete when every planned compensation was actually
    # accepted. A blocked compensation leaves the prior non-idempotent effect
    # uncompensated, so the count-based check (always true, since compensations
    # and accounted grow in lockstep) would wrongly report completion.
    compensation_complete = all(
        record.get("disposition") == "accepted" for record in compensations
    )
    # A reattempt is only safe to advance once every already-occurred
    # non-idempotent effect is accounted for: either compensated here, already
    # compensated on an earlier reattempt, or explicitly permitted by policy.
    reattempt_allowed = (
        not requires_compensation
        or compensation_complete
        or policy_permits_non_idempotent_reattempt
    )
    plan: dict[str, Any] = {
        "logicalStepId": logical_step_id,
        "reattemptExecutionOrdinal": execution_ordinal,
        "requiresCompensation": requires_compensation,
        "outstandingEffects": accounted,
        "alreadyCompensated": skipped,
        "compensations": compensations,
        "compensationComplete": compensation_complete,
        "policyPermitsNonIdempotentReattempt": bool(
            policy_permits_non_idempotent_reattempt
        ),
        "reattemptAllowed": bool(reattempt_allowed),
    }
    if not reattempt_allowed:
        plan["reason"] = "uncompensated_non_idempotent_effects"
    return plan


def validate_step_execution_manifest_payload(
    manifest_payload: Any,
    *,
    manifest_artifact_ref: str | None = None,
) -> dict[str, Any]:
    """Validate raw Step Execution manifests at workflow read/replay boundaries.

    Writers use ``StepExecutionManifestModel`` directly and remain strict. This
    helper gives boundaries that consume already-persisted manifests a compact
    invalid result instead of crashing on blank, unknown, or newly introduced
    enum values during a rolling deployment.
    """

    if not isinstance(manifest_payload, Mapping):
        return {
            "valid": False,
            "failureCode": "invalid_step_execution_manifest",
            "message": (
                "step execution manifest rejected at workflow boundary: "
                "payload must be a mapping"
            ),
            "manifestArtifactRef": _non_blank_text(manifest_artifact_ref),
            "stepExecutionId": None,
            "logicalStepId": None,
            "executionOrdinal": None,
        }

    validation_payload = dict(manifest_payload)
    validation_payload.pop("manifestArtifactRef", None)
    validation_payload.pop("artifactRef", None)
    if "stepExecutionId" not in validation_payload:
        workflow_id = _non_blank_text(validation_payload.get("workflowId"))
        run_id = _non_blank_text(validation_payload.get("runId"))
        logical_step_id = _non_blank_text(validation_payload.get("logicalStepId"))
        execution_ordinal = _positive_int_or_none(
            validation_payload.get("executionOrdinal")
        )
        if workflow_id and run_id and logical_step_id and execution_ordinal is not None:
            validation_payload["stepExecutionId"] = step_execution_id(
                workflow_id=workflow_id,
                run_id=run_id,
                logical_step_id=logical_step_id,
                execution_ordinal=execution_ordinal,
            )

    try:
        manifest = BoundaryStepExecutionManifestModel.model_validate(
            validation_payload
        )
    except ValidationError as exc:
        return {
            "valid": False,
            "failureCode": "invalid_step_execution_manifest",
            "message": _validation_error_summary(
                exc,
                fallback="step execution manifest rejected at workflow boundary",
            ),
            "manifestArtifactRef": _non_blank_text(
                manifest_artifact_ref
                or manifest_payload.get("manifestArtifactRef")
                or manifest_payload.get("artifactRef")
            ),
            "stepExecutionId": _non_blank_text(
                manifest_payload.get("stepExecutionId")
            ),
            "logicalStepId": _non_blank_text(manifest_payload.get("logicalStepId")),
            "executionOrdinal": _positive_int_or_none(
                manifest_payload.get("executionOrdinal")
            ),
        }
    return {
        "valid": True,
        "failureCode": None,
        "message": "step execution manifest validation passed",
        "manifestArtifactRef": _non_blank_text(
            manifest_artifact_ref
            or manifest_payload.get("manifestArtifactRef")
            or manifest_payload.get("artifactRef")
        ),
        "stepExecutionId": manifest.step_execution_id,
        "logicalStepId": manifest.logical_step_id,
        "executionOrdinal": manifest.execution_ordinal,
        "reason": manifest.reason,
        "status": manifest.status,
        "terminalDisposition": manifest.terminal_disposition,
    }


def _validation_error_summary(exc: ValidationError, *, fallback: str) -> str:
    errors = exc.errors(include_input=False)
    if not errors:
        return fallback
    first = errors[0]
    location = ".".join(
        str(part) for part in first.get("loc", ()) if part != "__root__"
    )
    message = str(first.get("msg") or "").strip()
    if location and message:
        return f"{fallback}: {location}: {message}"[:1000]
    if message:
        return f"{fallback}: {message}"[:1000]
    return fallback


def _non_blank_text(value: Any) -> str | None:
    candidate = str(value or "").strip()
    return candidate or None


def _positive_int_or_none(value: Any) -> int | None:
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        return None
    return candidate if candidate > 0 else None
