"""Pure helpers for Step Attempt identity, manifests, and idempotency."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import posixpath
import re
from typing import Any, Literal

from moonmind.schemas.step_attempt_models import (
    AttemptReason,
    AttemptStatus,
    StepAttemptIdentityModel,
    StepAttemptManifestModel,
)
from moonmind.schemas.temporal_models import WorkspacePolicy
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


def step_attempt_id(
    *,
    workflow_id: str,
    run_id: str,
    logical_step_id: str,
    attempt: int,
) -> str:
    return StepAttemptIdentityModel(
        workflowId=workflow_id,
        runId=run_id,
        logicalStepId=logical_step_id,
        attempt=attempt,
    ).step_attempt_id


def step_attempt_operation_idempotency_key(
    *,
    workflow_id: str,
    run_id: str,
    logical_step_id: str,
    attempt: int,
    operation: str,
) -> str:
    operation_id = str(operation or "").strip()
    if not operation_id:
        raise ValueError("operation must be a non-empty string")
    identity = step_attempt_id(
        workflow_id=workflow_id,
        run_id=run_id,
        logical_step_id=logical_step_id,
        attempt=attempt,
    )
    return f"{identity}:{operation_id}"


def workspace_policy_metadata(
    *,
    policy: WorkspacePolicy,
    checkpoint_ref: str | None = None,
    checkpoint_valid: bool | None = None,
    rejection_reason: str | None = None,
) -> dict[str, Any]:
    """Build compact workspace policy diagnostics for an attempt manifest."""

    required_kinds = checkpoint_kinds_for_workspace_policy(policy)
    checkpoint_text = str(checkpoint_ref or "").strip() or None
    evidence_required = bool(required_kinds)
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
    return record


def build_step_attempt_manifest_payload(
    *,
    workflow_id: str,
    run_id: str,
    logical_step_id: str,
    attempt: int,
    reason: AttemptReason,
    status: AttemptStatus,
    updated_at: datetime,
    started_at: datetime | None = None,
    summary: str | None = None,
    lineage: Mapping[str, Any] | None = None,
    input_refs: Sequence[str] = (),
    context: Mapping[str, Any] | None = None,
    workspace: Mapping[str, Any] | None = None,
    git_effect: Mapping[str, Any] | None = None,
    execution: Mapping[str, Any] | None = None,
    outputs: Mapping[str, Any] | None = None,
    checks: Sequence[Mapping[str, Any]] = (),
    side_effects: Mapping[str, Any] | None = None,
    side_effect_records: Sequence[Mapping[str, Any]] = (),
    dependency_effects: Mapping[str, Any] | None = None,
    budget: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    bounded_outputs = dict(outputs or {})
    if summary is not None:
        bounded_outputs.setdefault("summary", summary[:500])
    prepared_refs = [str(ref).strip() for ref in input_refs if str(ref).strip()]
    input_payload: dict[str, Any] = {}
    if prepared_refs:
        input_payload["preparedInputRefs"] = prepared_refs
    workspace_payload = dict(workspace or {})
    if git_effect is not None:
        workspace_payload["gitEffect"] = dict(git_effect)
    side_effect_payload = dict(side_effects or {})
    if side_effect_records:
        side_effect_payload["records"] = [dict(record) for record in side_effect_records]
    manifest = StepAttemptManifestModel(
        workflowId=workflow_id,
        runId=run_id,
        logicalStepId=logical_step_id,
        attempt=attempt,
        lineage=dict(lineage) if lineage is not None else None,
        reason=reason,
        status=status,
        startedAt=started_at or updated_at,
        updatedAt=updated_at,
        input=input_payload,
        context=dict(context or {}),
        workspace=workspace_payload,
        execution=dict(execution or {}),
        outputs=bounded_outputs,
        checks=[dict(check) for check in checks],
        sideEffects=side_effect_payload,
        dependencyEffects=dict(dependency_effects or {}),
        budget=dict(budget or {}),
    )
    return manifest.model_dump(by_alias=True, mode="json")
