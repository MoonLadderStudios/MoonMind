"""Typed, publication-only recovery contracts and deterministic decisions.

GitHub issue MoonLadderStudios/MoonMind#3481.

This module deliberately contains no implementation-agent or verifier entrypoint.
It defines the compact Temporal payload and the fail-closed decision boundary used
before the GitHub publication Activity. Large candidate data and observations are
artifact references.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PublicationSemanticContext = Literal["accepted", "incomplete_draft_handoff"]
PublicationReconciliation = Literal[
    "already_completed", "safe_to_retry", "conflict", "ambiguous"
]
PUBLICATION_ONLY_PHASES = (
    "contract_validation",
    "publication_state_reconciliation",
    "optional_workspace_restoration",
    "publication_operation",
    "publication_verification",
    "artifact_summary_persistence",
    "cleanup",
)


class PublicationRecoveryError(ValueError):
    """A bounded, operator-visible publication recovery rejection."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _required_text(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text or "\n" in text or "\r" in text:
        raise ValueError(f"{name} must be a compact non-blank value")
    return text


class PublicationRecoveryTarget(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    kind: Literal["publication"] = "publication"
    publication_kind: Literal["pull_request"] = Field(
        "pull_request", alias="publicationKind"
    )
    source_publication_operation_id: str = Field(
        ..., alias="sourcePublicationOperationId"
    )
    semantic_context: PublicationSemanticContext = Field(..., alias="semanticContext")

    @field_validator("source_publication_operation_id")
    @classmethod
    def _operation_id(cls, value: str) -> str:
        return _required_text(value, "sourcePublicationOperationId")


class PublicationIntent(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    repository: str
    base_ref: str = Field(..., alias="baseRef")
    head_ref: str = Field(..., alias="headRef")
    mode: Literal["pr", "draft_pr"] = "pr"
    branch_policy: str = Field(..., alias="branchPolicy")
    github_authority_ref: str = Field(..., alias="githubAuthorityRef")

    @field_validator(
        "repository", "base_ref", "head_ref", "branch_policy", "github_authority_ref"
    )
    @classmethod
    def _compact(cls, value: str, info: Any) -> str:
        return _required_text(value, info.field_name)


class PublicationContinuation(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    phase: Literal["resume_publication"] = "resume_publication"
    publication_idempotency_key: str = Field(..., alias="publicationIdempotencyKey")
    candidate_ref: str = Field(..., alias="candidateRef")
    before_publication_checkpoint_ref: str | None = Field(
        None, alias="beforePublicationCheckpointRef"
    )
    verified_remote_candidate_ref: str | None = Field(
        None, alias="verifiedRemoteCandidateRef"
    )
    expected_head_sha: str = Field(..., alias="expectedHeadSha")
    expected_tree_digest: str = Field(..., alias="expectedTreeDigest")
    expected_diff_digest: str = Field(..., alias="expectedDiffDigest")
    prior_observations_ref: str = Field(..., alias="priorObservationsRef")
    remaining_work_ref: str | None = Field(None, alias="remainingWorkRef")

    @field_validator(
        "publication_idempotency_key",
        "candidate_ref",
        "expected_head_sha",
        "expected_tree_digest",
        "expected_diff_digest",
        "prior_observations_ref",
    )
    @classmethod
    def _compact(cls, value: str, info: Any) -> str:
        return _required_text(value, info.field_name)

    @model_validator(mode="after")
    def _publication_ready_source(self) -> "PublicationContinuation":
        if not (
            self.before_publication_checkpoint_ref
            or self.verified_remote_candidate_ref
        ):
            raise ValueError(
                "beforePublicationCheckpointRef or "
                "verifiedRemoteCandidateRef is required"
            )
        return self


class PublicationRecoveryContract(BaseModel):
    """Frozen input for one linked publication-only run."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    schema_version: Literal["publication-recovery-v1"] = Field(
        "publication-recovery-v1", alias="schemaVersion"
    )
    source_workflow_id: str = Field(..., alias="sourceWorkflowId")
    source_run_id: str = Field(..., alias="sourceRunId")
    source_semantic_outcome: str = Field(..., alias="sourceSemanticOutcome")
    target: PublicationRecoveryTarget
    continuation: PublicationContinuation
    intent: PublicationIntent
    candidate_accepted: bool = Field(..., alias="candidateAccepted")
    candidate_contaminated: bool = Field(False, alias="candidateContaminated")
    has_publishable_change: bool = Field(..., alias="hasPublishableChange")
    publication_authority_current: bool = Field(
        ..., alias="publicationAuthorityCurrent"
    )
    incomplete_draft_authorized: bool = Field(
        False, alias="incompleteDraftAuthorized"
    )

    @field_validator("source_workflow_id", "source_run_id", "source_semantic_outcome")
    @classmethod
    def _compact(cls, value: str, info: Any) -> str:
        return _required_text(value, info.field_name)

    @model_validator(mode="after")
    def _eligibility(self) -> "PublicationRecoveryContract":
        if not self.has_publishable_change:
            raise PublicationRecoveryError(
                "PUBLICATION_NO_CHANGE", "candidate has no publishable changes"
            )
        if self.candidate_contaminated:
            raise PublicationRecoveryError(
                "PUBLICATION_CANDIDATE_UNSAFE", "candidate is contaminated"
            )
        if not self.publication_authority_current:
            raise PublicationRecoveryError(
                "PUBLICATION_AUTHORITY_UNAVAILABLE",
                "publication authority is absent or stale",
            )
        if self.target.semantic_context == "accepted":
            if not self.candidate_accepted:
                raise PublicationRecoveryError(
                    "PUBLICATION_CANDIDATE_UNACCEPTED",
                    "candidate is not accepted",
                )
        else:
            if (
                not self.incomplete_draft_authorized
                or self.intent.mode != "draft_pr"
                or not self.continuation.remaining_work_ref
            ):
                raise PublicationRecoveryError(
                    "PUBLICATION_DRAFT_HANDOFF_INVALID",
                    "incomplete draft requires authorization, draft mode, "
                    "and remaining work",
                )
        expected_key = publication_operation_key(
            source_workflow_id=self.source_workflow_id,
            source_run_id=self.source_run_id,
            publication_kind=self.target.publication_kind,
            repository=self.intent.repository,
            head_ref=self.intent.head_ref,
            base_ref=self.intent.base_ref,
        )
        if self.continuation.publication_idempotency_key != expected_key:
            raise PublicationRecoveryError(
                "PUBLICATION_IDEMPOTENCY_MISMATCH",
                "publication idempotency key contradicts frozen intent",
            )
        return self


def publication_operation_key(
    *,
    source_workflow_id: str,
    source_run_id: str,
    publication_kind: str,
    repository: str,
    head_ref: str,
    base_ref: str,
) -> str:
    """Return the stable operation identity reused by every retry boundary."""

    identity = {
        "base": _required_text(base_ref, "baseRef"),
        "head": _required_text(head_ref, "headRef"),
        "kind": _required_text(publication_kind, "publicationKind"),
        "repository": _required_text(repository, "repository").lower(),
        "run": _required_text(source_run_id, "sourceRunId"),
        "workflow": _required_text(source_workflow_id, "sourceWorkflowId"),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"publication:{digest}"


def publication_recovery_workflow_id(
    contract: PublicationRecoveryContract,
) -> str:
    """Map duplicate submissions to one linked destination workflow."""

    digest = contract.continuation.publication_idempotency_key.removeprefix(
        "publication:"
    )
    return f"{contract.source_workflow_id}:publication-recovery:{digest[:24]}"[:300]


def validate_restored_candidate(
    contract: PublicationRecoveryContract,
    restoration: Mapping[str, Any],
) -> str:
    """Validate a deterministic destination restore before publication.

    Raw source paths are intentionally not accepted. The restoration Activity
    must return a managed destination locator and exact git/tree/diff proof.
    """

    locator = restoration.get("destinationWorkspaceLocator")
    if not isinstance(locator, Mapping) or not str(
        locator.get("agentRunId") or ""
    ).strip():
        raise PublicationRecoveryError(
            "PUBLICATION_RESTORATION_INVALID",
            "deterministic destination workspace locator is required",
        )
    expected = {
        "headSha": contract.continuation.expected_head_sha,
        "treeDigest": contract.continuation.expected_tree_digest,
        "diffDigest": contract.continuation.expected_diff_digest,
    }
    for field, value in expected.items():
        if str(restoration.get(field) or "").strip() != value:
            raise PublicationRecoveryError(
                "PUBLICATION_CANDIDATE_MISMATCH",
                f"restored candidate {field} does not match accepted evidence",
            )
    evidence_ref = str(restoration.get("restorationEvidenceRef") or "").strip()
    if not evidence_ref:
        raise PublicationRecoveryError(
            "PUBLICATION_RESTORATION_INVALID",
            "restoration evidence reference is required",
        )
    return evidence_ref


class PublicationObservation(BaseModel):
    """Current GitHub observation produced by the authority-owning Activity."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    authoritative: bool
    authority_available: bool = Field(..., alias="authorityAvailable")
    remote_branch_exists: bool = Field(False, alias="remoteBranchExists")
    remote_head_sha: str | None = Field(None, alias="remoteHeadSha")
    pull_request_exists: bool = Field(False, alias="pullRequestExists")
    pull_request_url: str | None = Field(None, alias="pullRequestUrl")
    pull_request_head_ref: str | None = Field(None, alias="pullRequestHeadRef")
    pull_request_base_ref: str | None = Field(None, alias="pullRequestBaseRef")
    pull_request_head_sha: str | None = Field(None, alias="pullRequestHeadSha")
    pull_request_draft: bool | None = Field(None, alias="pullRequestDraft")
    conflicting_evidence: bool = Field(False, alias="conflictingEvidence")
    transient_absence_only: bool = Field(False, alias="transientAbsenceOnly")


class PublicationReconciliationDecision(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    outcome: PublicationReconciliation
    reason_code: str = Field(..., alias="reasonCode")
    mutation_allowed: bool = Field(..., alias="mutationAllowed")
    existing_pull_request_url: str | None = Field(
        None, alias="existingPullRequestUrl"
    )


def reconcile_publication_state(
    contract: PublicationRecoveryContract,
    observation: PublicationObservation,
) -> PublicationReconciliationDecision:
    """Reconcile authoritative current state before any GitHub mutation."""

    if not observation.authority_available:
        return PublicationReconciliationDecision(
            outcome="conflict",
            reasonCode="publication_authority_unavailable",
            mutationAllowed=False,
        )
    if not observation.authoritative or observation.transient_absence_only:
        return PublicationReconciliationDecision(
            outcome="ambiguous",
            reasonCode="publication_observation_not_authoritative",
            mutationAllowed=False,
        )
    if observation.conflicting_evidence:
        return PublicationReconciliationDecision(
            outcome="conflict",
            reasonCode="conflicting_publication_evidence",
            mutationAllowed=False,
        )
    if observation.remote_branch_exists:
        if observation.remote_head_sha != contract.continuation.expected_head_sha:
            return PublicationReconciliationDecision(
                outcome="conflict",
                reasonCode="remote_head_mismatch",
                mutationAllowed=False,
            )
    if observation.pull_request_exists:
        matches = all(
            (
                observation.pull_request_head_ref == contract.intent.head_ref,
                observation.pull_request_base_ref == contract.intent.base_ref,
                observation.pull_request_head_sha
                == contract.continuation.expected_head_sha,
                bool(observation.pull_request_url),
            )
        )
        draft_matches = (
            contract.intent.mode != "draft_pr"
            or observation.pull_request_draft is True
        )
        if not matches or not draft_matches:
            return PublicationReconciliationDecision(
                outcome="conflict",
                reasonCode="pull_request_identity_mismatch",
                mutationAllowed=False,
            )
        return PublicationReconciliationDecision(
            outcome="already_completed",
            reasonCode="matching_pull_request_reconciled",
            mutationAllowed=False,
            existingPullRequestUrl=observation.pull_request_url,
        )
    return PublicationReconciliationDecision(
        outcome="safe_to_retry",
        reasonCode=(
            "matching_remote_head_reconciled"
            if observation.remote_branch_exists
            else "publication_absent_authoritatively"
        ),
        mutationAllowed=True,
    )


class PublicationRecoveryRolloutPolicy(BaseModel):
    """Mutable admission policy; a frozen contract remains deterministic in flight."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    enabled: bool = False
    shadow: bool = False
    canary_repositories: tuple[str, ...] = Field(
        default=(), alias="canaryRepositories"
    )
    canary_owner_ids: tuple[str, ...] = Field(default=(), alias="canaryOwnerIds")
    allowed_modes: tuple[Literal["pr", "draft_pr"], ...] = Field(
        default=("pr", "draft_pr"), alias="allowedModes"
    )
    generation: str = "disabled"

    def admission_reason(
        self, *, repository: str, owner_id: str | None, mode: str
    ) -> str | None:
        if not self.enabled:
            return "publication_recovery_disabled"
        if self.shadow:
            return "publication_recovery_shadow_only"
        if mode not in self.allowed_modes:
            return "publication_mode_not_allowed"
        if self.canary_repositories or self.canary_owner_ids:
            if (
                repository.lower()
                not in {item.lower() for item in self.canary_repositories}
                and (owner_id or "") not in self.canary_owner_ids
            ):
                return "publication_recovery_not_in_canary"
        return None


class PublicationRecoveryEvidence(BaseModel):
    """Durable terminal proof for the linked publication operation."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    source_workflow_id: str = Field(..., alias="sourceWorkflowId")
    source_run_id: str = Field(..., alias="sourceRunId")
    destination_workflow_id: str = Field(..., alias="destinationWorkflowId")
    publication_idempotency_key: str = Field(..., alias="publicationIdempotencyKey")
    reconciliation_outcome: PublicationReconciliation = Field(
        ..., alias="reconciliationOutcome"
    )
    expected_head_sha: str = Field(..., alias="expectedHeadSha")
    observed_head_sha: str = Field(..., alias="observedHeadSha")
    repository: str
    base_ref: str = Field(..., alias="baseRef")
    head_ref: str = Field(..., alias="headRef")
    pull_request_url: str = Field(..., alias="pullRequestUrl")
    pull_request_state: Literal["open"] = Field("open", alias="pullRequestState")
    pull_request_draft: bool = Field(..., alias="pullRequestDraft")
    github_authority_ref: str = Field(..., alias="githubAuthorityRef")
    secret_scan_ref: str = Field(..., alias="secretScanRef")
    diagnostics_ref: str = Field(..., alias="diagnosticsRef")
    publication_observations_ref: str = Field(..., alias="publicationObservationsRef")
    source_semantic_outcome: str = Field(..., alias="sourceSemanticOutcome")
    implementation_rerun: Literal[False] = Field(False, alias="implementationRerun")
    verification_rerun: Literal[False] = Field(False, alias="verificationRerun")
    auxiliary_failure_class: str | None = Field(None, alias="auxiliaryFailureClass")

    @model_validator(mode="after")
    def _verified_identity(self) -> "PublicationRecoveryEvidence":
        if self.expected_head_sha != self.observed_head_sha:
            raise PublicationRecoveryError(
                "PUBLICATION_VERIFICATION_FAILED",
                "observed head does not match candidate",
            )
        for name in (
            "pull_request_url",
            "secret_scan_ref",
            "diagnostics_ref",
            "publication_observations_ref",
        ):
            _required_text(getattr(self, name), name)
        return self


def publication_action_eligibility(
    payload: Mapping[str, Any] | None,
) -> tuple[bool, str | None]:
    """Project exact action availability without trusting a failed status alone."""

    if not isinstance(payload, Mapping):
        return False, "publication_recovery_evidence_missing"
    contract_payload = dict(payload)
    ambiguity = str(
        contract_payload.pop("ambiguityState", "reconciled") or "reconciled"
    ).strip().lower()
    if contract_payload.get("hasPublishableChange") is not True:
        return False, "publication_no_change"
    if contract_payload.get("candidateContaminated") is True:
        return False, "publication_candidate_unsafe"
    if contract_payload.get("publicationAuthorityCurrent") is not True:
        return False, "publication_authority_unavailable"
    try:
        PublicationRecoveryContract.model_validate(contract_payload)
    except ValueError as exc:
        text = str(exc)
        for code in (
            "PUBLICATION_CANDIDATE_UNACCEPTED",
            "PUBLICATION_DRAFT_HANDOFF_INVALID",
            "PUBLICATION_IDEMPOTENCY_MISMATCH",
        ):
            if code in text:
                return False, code.lower()
        return False, "publication_recovery_contract_invalid"
    if ambiguity not in {"none", "reconciled"}:
        return False, "publication_state_ambiguous"
    return True, None


__all__ = [
    "PUBLICATION_ONLY_PHASES",
    "PublicationContinuation",
    "PublicationIntent",
    "PublicationObservation",
    "PublicationRecoveryContract",
    "PublicationRecoveryError",
    "PublicationRecoveryEvidence",
    "PublicationRecoveryRolloutPolicy",
    "PublicationRecoveryTarget",
    "PublicationReconciliationDecision",
    "publication_action_eligibility",
    "publication_operation_key",
    "publication_recovery_workflow_id",
    "reconcile_publication_state",
    "validate_restored_candidate",
]
