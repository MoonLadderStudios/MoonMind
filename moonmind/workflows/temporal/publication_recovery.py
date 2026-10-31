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
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from moonmind.publish.saved_candidate import (
    SavedPublicationAdmission,
    SavedPublicationCommit,
)

from moonmind.core.artifacts import durable_artifact_ref
from moonmind.workflows.temporal.bounded_story_loop import PublicationFeasibility

if TYPE_CHECKING:
    from moonmind.workflows.temporal.artifacts import ArtifactAccessContext

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
    secret_scan_ref: str = Field(..., alias="secretScanRef")
    diagnostics_ref: str = Field(..., alias="diagnosticsRef")
    remaining_work_ref: str | None = Field(None, alias="remainingWorkRef")

    @field_validator(
        "publication_idempotency_key",
        "candidate_ref",
        "expected_head_sha",
        "expected_tree_digest",
        "expected_diff_digest",
        "prior_observations_ref",
        "secret_scan_ref",
        "diagnostics_ref",
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
    pull_request_body: str | None = Field(None, alias="pullRequestBody")
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

    enabled: bool = True
    shadow: bool = False
    canary_repositories: tuple[str, ...] = Field(
        default=(), alias="canaryRepositories"
    )
    canary_owner_ids: tuple[str, ...] = Field(default=(), alias="canaryOwnerIds")
    allowed_modes: tuple[Literal["pr", "draft_pr", "branch"], ...] = Field(
        default=("pr", "draft_pr", "branch"), alias="allowedModes"
    )
    generation: str = "default"

    def availability_reason(self, *, owner_id: str | None) -> str | None:
        """Why no destination can be admitted for this owner, before one is chosen."""
        if not self.enabled:
            return "publication_recovery_disabled"
        if self.shadow:
            return "publication_recovery_shadow_only"
        if not self.allowed_modes:
            return "publication_mode_not_allowed"
        if (
            self.canary_owner_ids
            and not self.canary_repositories
            and (owner_id or "") not in self.canary_owner_ids
        ):
            return "publication_recovery_not_in_canary"
        return None

    def destination_limits(self, *, owner_id: str | None) -> dict[str, list[str]]:
        """The modes and, under a repository canary, repositories admission accepts."""
        limits = {"allowedModes": list(self.allowed_modes)}
        if self.canary_repositories and (owner_id or "") not in self.canary_owner_ids:
            limits["canaryRepositories"] = list(self.canary_repositories)
        return limits

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
    publication_outcome: Literal["new", "reconciled"] = Field(
        ..., alias="publicationOutcome"
    )
    expected_head_sha: str = Field(..., alias="expectedHeadSha")
    observed_head_sha: str = Field(..., alias="observedHeadSha")
    expected_tree_digest: str = Field(..., alias="expectedTreeDigest")
    observed_tree_digest: str = Field(..., alias="observedTreeDigest")
    expected_diff_digest: str = Field(..., alias="expectedDiffDigest")
    observed_diff_digest: str = Field(..., alias="observedDiffDigest")
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
    semantic_context: PublicationSemanticContext = Field(..., alias="semanticContext")
    remaining_work_ref: str | None = Field(None, alias="remainingWorkRef")
    restoration_evidence_ref: str | None = Field(
        None, alias="restorationEvidenceRef"
    )

    @model_validator(mode="after")
    def _verified_identity(self) -> "PublicationRecoveryEvidence":
        mismatches = [
            name
            for name, expected, observed in (
                ("head", self.expected_head_sha, self.observed_head_sha),
                ("tree", self.expected_tree_digest, self.observed_tree_digest),
                ("diff", self.expected_diff_digest, self.observed_diff_digest),
            )
            if expected != observed
        ]
        if mismatches:
            raise PublicationRecoveryError(
                "PUBLICATION_VERIFICATION_FAILED",
                "observed candidate identity does not match accepted "
                f"{', '.join(mismatches)} evidence",
            )
        for name in (
            "pull_request_url",
            "secret_scan_ref",
            "diagnostics_ref",
            "publication_observations_ref",
        ):
            _required_text(getattr(self, name), name)
        if self.semantic_context == "incomplete_draft_handoff":
            _required_text(self.remaining_work_ref, "remaining_work_ref")
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


SAVED_WORK_PUBLICATION_SCHEMA_VERSION = "saved-work-publication-v1"
SAVED_WORK_PUBLICATION_RESULT_SCHEMA_VERSION = "saved-work-publication-result-v1"
# Placeholder that lets admission validation run before the destination base
# is observed; it never becomes a remote expectation.
_UNOBSERVED_BASE_SHA = "0" * 40


class SavedWorkPublicationDestination(BaseModel):
    """The destination, objective, and application policy one request admits."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    repository: str
    objective: Literal["branch", "pr", "draft_pr"]
    base_branch: str | None = Field(None, alias="baseBranch")
    head_branch: str = Field(..., alias="headBranch")
    strategy: Literal["baseline_delta", "additive_import", "empty_initialization"]
    path_prefix: str | None = Field(None, alias="pathPrefix")
    authorized_deletions: tuple[str, ...] = Field((), alias="authorizedDeletions")
    expected_base_sha: str | None = Field(None, alias="expectedBaseSha")
    expected_head_sha: str | None = Field(None, alias="expectedHeadSha")


def saved_work_publication_operation_key(
    *,
    saved_work_digest: str,
    destination: SavedWorkPublicationDestination,
    github_authority_ref: str,
    admission_generation: str | None = None,
) -> str:
    """Identity for one saved result, admitted destination, strategy, and branch."""

    identity = {
        "authority": _required_text(github_authority_ref, "githubAuthorityRef"),
        "destination": {
            **destination.model_dump(by_alias=True, mode="json"),
            "repository": destination.repository.lower(),
        },
        "savedWork": _required_text(saved_work_digest, "savedWorkDigest"),
    }
    # Retained requests without a generation keep their exact historical key.
    # A new explicit admission can preserve the same requested output while
    # leaving the fenced operation and its immutable receipts available.
    if admission_generation is not None:
        identity["admissionGeneration"] = _required_text(
            admission_generation, "admissionGeneration"
        )
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"saved-publication:{digest}"


class SavedWorkPublicationContract(BaseModel):
    """Frozen input for publishing one immutable saved result (#4018).

    It carries references and policy only: no credential, model Profile, or
    source workspace. The destination base is observed once by the prepare
    Activity when ``expectedBaseSha`` is omitted, then persisted with the
    candidate before any effect.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    schema_version: Literal["saved-work-publication-v1"] = Field(
        SAVED_WORK_PUBLICATION_SCHEMA_VERSION, alias="schemaVersion"
    )
    source_workflow_id: str = Field(..., alias="sourceWorkflowId")
    source_run_id: str = Field(..., alias="sourceRunId")
    saved_work_ref: str = Field(..., alias="savedWorkRef")
    saved_work_digest: str = Field(..., alias="savedWorkDigest")
    # Retained workflow payloads carry this creator/use-claim accounting value.
    # Artifact read authority is rebound to execution grants at the Activity.
    admitted_principal: str = Field(..., alias="admittedPrincipal")
    destination: SavedWorkPublicationDestination
    github_authority_ref: str = Field(..., alias="githubAuthorityRef")
    commit: SavedPublicationCommit
    pull_request_title: str | None = Field(None, alias="pullRequestTitle")
    pull_request_body: str | None = Field(None, alias="pullRequestBody")
    publication_idempotency_key: str = Field(..., alias="publicationIdempotencyKey")
    admission_generation: str | None = Field(
        None, alias="admissionGeneration", min_length=1, max_length=128
    )

    @field_validator("admission_generation")
    @classmethod
    def _generation(cls, value: str | None) -> str | None:
        return None if value is None else _required_text(value, "admissionGeneration")

    @field_validator(
        "source_workflow_id",
        "source_run_id",
        "saved_work_ref",
        "saved_work_digest",
        "admitted_principal",
        "github_authority_ref",
        "publication_idempotency_key",
    )
    @classmethod
    def _compact(cls, value: str, info: Any) -> str:
        return _required_text(value, info.field_name)

    @model_validator(mode="after")
    def _admissible(self) -> "SavedWorkPublicationContract":
        # One validation owner: the publisher's admission rejects unsupported
        # objectives, strategies, branches, mappings, and deletions.
        self.admission(
            expected_base_sha=self.destination.expected_base_sha
            or (
                None
                if self.destination.strategy == "empty_initialization"
                else _UNOBSERVED_BASE_SHA
            ),
            authority_ref=self.github_authority_ref,
        )
        if self.destination.objective != "branch" and not (
            self.pull_request_title or ""
        ).strip():
            raise ValueError("a pull request objective requires pullRequestTitle")
        expected_key = saved_work_publication_operation_key(
            saved_work_digest=self.saved_work_digest,
            destination=self.destination,
            github_authority_ref=self.github_authority_ref,
            admission_generation=self.admission_generation,
        )
        if self.publication_idempotency_key != expected_key:
            raise PublicationRecoveryError(
                "PUBLICATION_IDEMPOTENCY_MISMATCH",
                "publication idempotency key contradicts the admitted decision",
            )
        return self

    @property
    def draft(self) -> bool:
        return self.destination.objective == "draft_pr"

    def admission(
        self, *, expected_base_sha: str | None, authority_ref: str
    ) -> SavedPublicationAdmission:
        """Bind the observed base and resolved authority into one admission."""

        destination = self.destination
        return SavedPublicationAdmission(
            savedWorkDigest=self.saved_work_digest,
            repository=destination.repository,
            objective="branch" if destination.objective == "branch" else "pr",
            baseBranch=destination.base_branch,
            headBranch=destination.head_branch,
            strategy=destination.strategy,
            expectedBaseSha=expected_base_sha,
            expectedHeadSha=destination.expected_head_sha,
            pathPrefix=destination.path_prefix,
            authorizedDeletions=destination.authorized_deletions,
            authorityRef=authority_ref,
            commit=self.commit,
        )


def saved_work_publication_workflow_id(
    contract: SavedWorkPublicationContract,
) -> str:
    """Map duplicate submissions of one decision to one linked workflow."""

    digest = contract.publication_idempotency_key.removeprefix("saved-publication:")
    return f"{contract.source_workflow_id}:saved-work-publication:{digest[:24]}"[:300]


async def _recorded_publication_admission(
    contract: PublicationRecoveryContract | SavedWorkPublicationContract,
) -> tuple[str, bool]:
    """Verify the owning API's immutable admission of this exact running workflow."""
    import asyncio

    from temporalio import activity
    from temporalio.service import RPCError

    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactAuthorizationError,
        TemporalArtifactUnavailableError,
    )

    def denied() -> NoReturn:
        raise TemporalArtifactAuthorizationError(
            "publication requires its recorded API input admission"
        )

    if not activity.in_activity():
        denied()
    info = activity.info()
    namespace = getattr(info, "workflow_namespace", None) or getattr(
        info, "namespace", None
    )
    client = activity.client()
    if not namespace or client.namespace != namespace:
        denied()
    saved_work = isinstance(contract, SavedWorkPublicationContract)
    model = SavedWorkPublicationContract if saved_work else PublicationRecoveryContract
    expected_workflow = (
        saved_work_publication_workflow_id(contract)
        if saved_work
        else publication_recovery_workflow_id(contract)
    )
    root_ref = (
        contract.saved_work_ref
        if saved_work
        else contract.continuation.before_publication_checkpoint_ref
    )
    try:
        async with asyncio.timeout(10):
            handle = client.get_workflow_handle(
                info.workflow_id, run_id=info.workflow_run_id
            )
            async for event in handle.fetch_history_events(page_size=1):
                if event.event_id != 1 or not event.HasField(
                    "workflow_execution_started_event_attributes"
                ):
                    denied()
                started = event.workflow_execution_started_event_attributes
                if (
                    started.workflow_type.name != "MoonMind.PublicationRecoveryV1"
                    or started.HasField("parent_workflow_execution")
                    or len(started.input.payloads) != 1
                ):
                    denied()
                values = await client.data_converter.decode(started.input.payloads)
                try:
                    recorded = model.model_validate(values[0])
                except (ValueError, TypeError, IndexError):
                    denied()
                if (
                    recorded.model_dump(mode="json", by_alias=True)
                    != contract.model_dump(mode="json", by_alias=True)
                    or expected_workflow != info.workflow_id
                ):
                    denied()
                expected = {
                    "publication_semantic_context": (
                        "saved_work" if saved_work else contract.target.semantic_context
                    ),
                    "source_workflow_id": contract.source_workflow_id,
                    "source_run_id": contract.source_run_id,
                    "publication_idempotency_key": (
                        contract.publication_idempotency_key
                        if saved_work
                        else contract.continuation.publication_idempotency_key
                    ),
                }
                if saved_work:
                    expected["saved_work_digest"] = contract.saved_work_digest
                for key, value in expected.items():
                    if key not in started.memo.fields:
                        denied()
                    decoded = await client.data_converter.decode(
                        [started.memo.fields[key]]
                    )
                    if decoded != [value]:
                        denied()
                return namespace, "artifact_input_admission" in started.memo.fields
            denied()
    except (RPCError, TimeoutError, ConnectionError) as exc:
        raise TemporalArtifactUnavailableError(
            str(root_ref or "publication"), dependency="authority", operation="read"
        ) from exc


async def verified_publication_source_access(
    contract: PublicationRecoveryContract,
) -> ArtifactAccessContext:
    """Read the admitted checkpoint source without borrowing operator/creator roles.

    The restore plane still validates its exact source/candidate and the closed
    archive/manifest hashes. Its context grants only ordinary source-execution
    rights; restricted raw and quarantine policy remain unchanged.
    """
    from moonmind.workflows.temporal.artifacts import ArtifactAccessContext

    await _recorded_publication_admission(contract)
    return ArtifactAccessContext.for_execution(
        contract.source_workflow_id, principal="service:publication-recovery"
    )


async def recover_recorded_saved_work_inputs(
    service: Any, contract: SavedWorkPublicationContract
) -> None:
    """Recover the exact input admission of retained v1 publication runs.

    New API admissions record their grant installation in server-owned memo.
    This decoder is removable when older admissions have left retention. Authority comes from this exact
    run's immutable API start/memo, never its Activity payload's principal.
    """
    if contract.schema_version != SAVED_WORK_PUBLICATION_SCHEMA_VERSION:
        return
    from temporalio import activity

    from moonmind.publish.saved_work_source import (
        parse_saved_work_publication_manifest,
        saved_work_artifact_id,
    )
    from moonmind.workflows.temporal.artifacts import (
        ArtifactAccessContext,
        TemporalArtifactAuthorizationError,
        TemporalArtifactStateError,
    )

    def denied() -> NoReturn:
        raise TemporalArtifactAuthorizationError(
            "publication requires its recorded API input admission"
        )

    access = ArtifactAccessContext.for_activity(
        principal="service:saved-work-publication"
    )
    info = activity.info()
    root_id = saved_work_artifact_id(contract.saved_work_ref)
    links = await service._repository.list_links(root_id)
    activity_namespace = getattr(info, "workflow_namespace", None) or getattr(
        info, "namespace", None
    )
    if any(
        link.namespace == activity_namespace
        and link.workflow_id == info.workflow_id
        and link.link_type in {"input.artifact.admitted", "input.artifact.raw"}
        for link in links
    ):
        return
    namespace, declared_grants = await _recorded_publication_admission(contract)
    if declared_grants:
        # New admissions already installed their rights. Missing links are not
        # permission to recreate an intentionally revoked or corrupted grant.
        denied()
    if not any(
        link.namespace == namespace
        and link.workflow_id == contract.source_workflow_id
        and link.run_id == contract.source_run_id
        for link in links
    ):
        denied()
    root = await service._repository.get_artifact(root_id)
    if (
        root.content_type
        != "application/vnd.moonmind.saved-work-manifest+json;version=1"
        or "sha256:" + str(root.sha256).removeprefix("sha256:")
        != contract.saved_work_digest
    ):
        raise TemporalArtifactStateError("recorded publication manifest changed")
    # The receipt attests a raw-readable exact closure. Its temporary process
    # context never gains operator/recovery authority, and shared state and
    # quarantine checks still run before any durable input link is installed.
    root_access = access._with_recorded_publication_inputs([root_id])
    _, body = await service.read(artifact_id=root_id, access=root_access)
    if "sha256:" + hashlib.sha256(body).hexdigest() != contract.saved_work_digest:
        raise TemporalArtifactStateError("recorded publication manifest bytes changed")
    _, objects = parse_saved_work_publication_manifest(body)
    ids = [root_id, *(saved_work_artifact_id(obj.ref) for obj in objects)]
    proof = await service.dependency_proof(root_artifact_id=root_id, access=root_access)
    if proof is not None:
        # The sealed producer proof preserves nested required inputs. The old
        # receipt is not permission to adopt another execution's artifacts.
        for entry in proof:
            if not entry.required:
                continue
            child_links = await service._repository.list_links(entry.artifact_id)
            if not any(
                link.namespace == namespace
                and link.workflow_id == contract.source_workflow_id
                and link.run_id == contract.source_run_id
                for link in child_links
            ):
                denied()
            if entry.artifact_id not in ids:
                # Producer metadata proof does not attest raw access in the old
                # API receipt. Newly discovered descendants must independently
                # be raw-readable by the original execution before delegation.
                child = await service._repository.get_artifact(entry.artifact_id)
                if getattr(
                    child.redaction_level, "value", child.redaction_level
                ) == "restricted" and not any(
                    link.namespace == namespace
                    and link.workflow_id == contract.source_workflow_id
                    and link.link_type == "input.artifact.raw"
                    for link in child_links
                ):
                    denied()
                await service.read(
                    artifact_id=entry.artifact_id,
                    access=ArtifactAccessContext.for_execution(
                        contract.source_workflow_id,
                        principal="service:saved-work-publication",
                    ),
                )
            ids.append(entry.artifact_id)
        await service.verified_dependencies(
            root_artifact_id=root_id,
            access=access._with_recorded_publication_inputs(ids),
        )
    # Only unsealed retained admissions use the historical output decoder as
    # the whole closure. Completed seals never fall back after corruption.
    for obj in objects:
        artifact = await service._repository.get_artifact(
            saved_work_artifact_id(obj.ref)
        )
        if (
            artifact.content_type != obj.content_type
            or "sha256:" + str(artifact.sha256).removeprefix("sha256:") != obj.digest
        ):
            raise TemporalArtifactStateError("recorded publication input changed")
    await service.delegate_read_access(
        artifact_ids=ids,
        workflow_id=info.workflow_id,
        namespace=namespace,
        access=access._with_recorded_publication_inputs(ids),
    )


class SavedWorkPublicationResult(BaseModel):
    """Durable terminal record with separate push and pull-request outcomes."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    schema_version: Literal["saved-work-publication-result-v1"] = Field(
        SAVED_WORK_PUBLICATION_RESULT_SCHEMA_VERSION, alias="schemaVersion"
    )
    source_workflow_id: str = Field(..., alias="sourceWorkflowId")
    source_run_id: str = Field(..., alias="sourceRunId")
    destination_workflow_id: str = Field(..., alias="destinationWorkflowId")
    destination_run_id: str = Field(..., alias="destinationRunId")
    publication_idempotency_key: str = Field(..., alias="publicationIdempotencyKey")
    saved_work_ref: str = Field(..., alias="savedWorkRef")
    saved_work_digest: str = Field(..., alias="savedWorkDigest")
    outcome: Literal[
        "published", "no_change", "conflict", "unavailable", "rejected", "cancelled"
    ]
    reason_code: str | None = Field(None, alias="reasonCode")
    admission: dict[str, Any] | None = None
    decision_digest: str | None = Field(None, alias="decisionDigest")
    candidate: dict[str, Any] | None = None
    push: dict[str, Any] | None = None
    pull_request: dict[str, Any] | None = Field(None, alias="pullRequest")
    implementation_rerun: Literal[False] = Field(False, alias="implementationRerun")
    verification_rerun: Literal[False] = Field(False, alias="verificationRerun")

    @model_validator(mode="after")
    def _confirmed_effects(self) -> "SavedWorkPublicationResult":
        if self.outcome == "published":
            push_status = (self.push or {}).get("status")
            if push_status not in {"pushed", "reconciled"} or not (self.push or {}).get(
                "remoteVerified"
            ):
                raise ValueError("a published outcome requires a verified push")
        return self


__all__ = [
    "PUBLICATION_ONLY_PHASES",
    "SAVED_WORK_PUBLICATION_RESULT_SCHEMA_VERSION",
    "SAVED_WORK_PUBLICATION_SCHEMA_VERSION",
    "SavedWorkPublicationContract",
    "SavedWorkPublicationDestination",
    "SavedWorkPublicationResult",
    "saved_work_publication_operation_key",
    "saved_work_publication_workflow_id",
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
    "extract_pull_request_url",
    "prepare_publication_feasibility",
    "resolve_native_pr_branches",
    "resolve_run_publication_feasibility",
    "validate_restored_candidate",
]


_GITHUB_PR_URL_PATTERN = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+",
    re.IGNORECASE,
)


def _publication_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return " ".join(value.split()) or None


def _publication_branch(value: Any) -> str | None:
    branch = str(value or "").strip()
    while True:
        previous = branch
        for prefix in ("refs/remotes/origin/", "refs/heads/", "origin/"):
            if branch.startswith(prefix):
                branch = branch.removeprefix(prefix)
        if branch == previous:
            return branch or None


def _publish_base_branch(payload: Mapping[str, Any]) -> str | None:
    base = payload.get("prBaseBranch")
    if base is None:
        base = payload.get("baseBranch")
    return _publication_text(base)


def extract_pull_request_url(outputs: Any) -> str | None:
    """Decode current and retained provider PR metadata, preserving precedence."""
    if not isinstance(outputs, Mapping):
        return None

    for provider_key in (
        "providerNativePullRequest",
        "provider_native_pull_request",
    ):
        provider_native = outputs.get(provider_key)
        if not isinstance(provider_native, Mapping):
            continue
        for field in ("url", "pullRequestUrl", "prUrl"):
            value = provider_native.get(field)
            if not isinstance(value, str) or not value.strip():
                continue
            match = _GITHUB_PR_URL_PATTERN.search(value)
            if match is not None:
                return match.group(0)

    candidate_fields = (
        "pull_request_url",
        "pullRequestUrl",
        "pr_url",
        "prUrl",
        "url",
        "external_url",
        "externalUrl",
        "stdout_tail",
        "stderr_tail",
        "summary",
        "message",
    )
    for field in candidate_fields:
        value = outputs.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        match = _GITHUB_PR_URL_PATTERN.search(value)
        if match is not None:
            return match.group(0)
    return None


def prepare_publication_feasibility(outputs: Any) -> PublicationFeasibility:
    """Classify one step's accepted repository evidence at the publication owner."""
    if extract_pull_request_url(outputs):
        return PublicationFeasibility(
            feasible=True, reason="verified_remote_head"
        )
    if not isinstance(outputs, Mapping):
        return PublicationFeasibility(
            feasible=False, reason="publication_state_ambiguous"
        )
    evidence = outputs.get("acceptedRepositoryEvidence")
    if not isinstance(evidence, Mapping):
        evidence = outputs.get("accepted_repository_evidence")
    if not isinstance(evidence, Mapping):
        return PublicationFeasibility(
            feasible=False, reason="publication_state_ambiguous"
        )
    candidate_diff_ref = durable_artifact_ref(
        evidence.get("candidateDiffRef")
    )
    evidence_refs = tuple(
        ref
        for ref in (
            durable_artifact_ref(evidence.get("evidenceRef")),
            candidate_diff_ref,
        )
        if ref
    )
    if evidence.get("publicationAuthorized") is False:
        return PublicationFeasibility(
            feasible=False,
            reason="publication_unauthorized",
            evidenceRefs=evidence_refs,
        )
    if evidence.get("candidateContaminated") is True:
        return PublicationFeasibility(
            feasible=False,
            reason="candidate_contaminated",
            evidenceRefs=evidence_refs,
        )
    push_status = str(evidence.get("pushStatus") or "").strip().lower()
    if push_status in {"pushed", "published"}:
        return PublicationFeasibility(
            feasible=True,
            reason="verified_remote_head",
            evidenceRefs=evidence_refs,
        )
    commit_count = evidence.get("commitsAheadOfBase")
    if isinstance(commit_count, bool):
        commit_count = None
    if isinstance(commit_count, (int, float)):
        return PublicationFeasibility(
            feasible=int(commit_count) > 0,
            reason=(
                "commits_ahead_of_base"
                if int(commit_count) > 0
                else "no_candidate_change"
            ),
            evidenceRefs=evidence_refs,
        )
    if candidate_diff_ref:
        return PublicationFeasibility(
            feasible=True,
            reason="safe_candidate_diff",
            evidenceRefs=evidence_refs,
        )
    if evidence.get("repositoryChanged") is False:
        return PublicationFeasibility(
            feasible=False,
            reason="no_candidate_change",
            evidenceRefs=evidence_refs,
        )
    return PublicationFeasibility(
        feasible=False,
        reason="publication_state_ambiguous",
        evidenceRefs=evidence_refs,
    )


def resolve_run_publication_feasibility(
    decision: PublicationFeasibility, *, accepted_head_available: bool,
) -> PublicationFeasibility:
    """Fill an ambiguous read-only result from the run's confirmed published head.

    Definitive unauthorized, contaminated and no-change outcomes remain binding.
    """
    if decision.reason == "publication_state_ambiguous" and accepted_head_available:
        return decision.model_copy(update={"feasible": True, "reason": "verified_remote_head"})
    return decision


def resolve_native_pr_branches(
    *,
    parameters: Mapping[str, Any],
    agent_outputs: Mapping[str, Any],
    workspace_spec: Mapping[str, Any],
    last_node_inputs: Mapping[str, Any],
    publish_payload: Mapping[str, Any],
    publish_context: Mapping[str, Any],
    accepted_head: str | None,
    branch_defaults_enabled: bool,
) -> tuple[str, str]:
    """Use the confirmed head and the recorded metadata precedence.

    A read-only verifier's branch is not evidence of a pushed candidate. The
    accepted run head takes precedence; the older fallback only serves retained
    histories that have not recorded the branch-defaults patch.
    """
    if branch_defaults_enabled:
        head_candidates = (
            accepted_head,
            agent_outputs.get("push_branch"),
            publish_context.get("branch"),
            agent_outputs.get("branch"),
            agent_outputs.get("targetBranch"),
            workspace_spec.get("targetBranch"),
            last_node_inputs.get("targetBranch"),
        )
        base_candidates = (
            _publish_base_branch(publish_payload),
            agent_outputs.get("push_base_branch"),
            agent_outputs.get("baseBranch"),
            agent_outputs.get("base_branch"),
            publish_context.get("baseRef"),
            workspace_spec.get("startingBranch"),
            last_node_inputs.get("startingBranch"),
            "main",
        )
    else:
        head_candidates = (
            accepted_head,
            agent_outputs.get("push_branch"),
            publish_context.get("branch"),
            agent_outputs.get("branch"),
            agent_outputs.get("targetBranch"),
            workspace_spec.get("targetBranch"),
            parameters.get("targetBranch"),
            last_node_inputs.get("targetBranch"),
            workspace_spec.get("branch"),
            last_node_inputs.get("branch"),
        )
        base_candidates = (
            agent_outputs.get("baseBranch"),
            agent_outputs.get("base_branch"),
            publish_context.get("baseRef"),
            workspace_spec.get("startingBranch"),
            workspace_spec.get("branch"),
            last_node_inputs.get("startingBranch"),
            last_node_inputs.get("branch"),
            "main",
        )
    head_branch = next(
        (
            candidate
            for candidate in (_publication_text(value) for value in head_candidates)
            if candidate
        ),
        "",
    )
    base_branch = next(
        (
            candidate
            for candidate in (
                _publication_branch(value)
                for value in base_candidates
            )
            if candidate
        ),
        "main",
    )
    return head_branch, base_branch
