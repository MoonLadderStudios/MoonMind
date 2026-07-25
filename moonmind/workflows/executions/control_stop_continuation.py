"""Typed, deterministic admission for workflow-gate remediation continuations.

This module deliberately contains no registry, credential, workspace, or Temporal
lookups.  API code freezes those mutable decisions before destination creation;
workflow code validates this contract again before performing its first mutation.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.security.outbound_scan import scan_outbound_text


class ControlStopContinuationError(ValueError):
    """A control-stop continuation contract is unsafe or incomplete."""


class ContinuationBudgetGrant(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    grant_id: str = Field(alias="grantId", min_length=1, max_length=200)
    max_attempts: int = Field(alias="maxAttempts", ge=1, le=100)
    max_consecutive_no_progress_attempts: int = Field(
        alias="maxConsecutiveNoProgressAttempts", ge=1, le=100
    )
    consumed_attempts: int = Field(0, alias="consumedAttempts", ge=0)
    consecutive_no_progress_attempts: int = Field(
        0, alias="consecutiveNoProgressAttempts", ge=0
    )

    @model_validator(mode="after")
    def _bounded_consumption(self) -> ContinuationBudgetGrant:
        if self.consumed_attempts > self.max_attempts:
            raise ValueError("consumedAttempts exceeds the continuation grant")
        if (
            self.consecutive_no_progress_attempts
            > self.max_consecutive_no_progress_attempts
        ):
            raise ValueError("no-progress consumption exceeds the continuation grant")
        return self

    def consume(self, *, progress: bool) -> ContinuationBudgetGrant:
        if self.consumed_attempts >= self.max_attempts:
            raise ControlStopContinuationError("continuation_attempt_budget_exhausted")
        no_progress = 0 if progress else self.consecutive_no_progress_attempts + 1
        if no_progress > self.max_consecutive_no_progress_attempts:
            raise ControlStopContinuationError(
                "continuation_no_progress_budget_exhausted"
            )
        return self.model_copy(
            update={
                "consumed_attempts": self.consumed_attempts + 1,
                "consecutive_no_progress_attempts": no_progress,
            }
        )


class SourceBudgetEvidence(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    max_attempts: int = Field(alias="maxAttempts", ge=1)
    consumed_attempts: int = Field(alias="consumedAttempts", ge=1)
    exhausted_dimension: Literal[
        "remediation_attempts", "consecutive_no_progress_attempts"
    ] = Field(alias="exhaustedDimension")


class ControlStopRolloutPolicy(BaseModel):
    """Frozen admission policy for one continuation destination.

    Current deployment policy is evaluated before destination creation.  This
    compact snapshot is then the replay authority, so disabling new admissions
    never changes an already-linked workflow.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    mode: Literal["shadow", "canary", "enabled"]
    canary_owner_ids: list[str] = Field(default_factory=list, alias="canaryOwnerIds")
    allowed_provider_profile_ids: list[str] = Field(
        alias="allowedProviderProfileIds", min_length=1
    )
    allowed_execution_profile_refs: list[str] = Field(
        alias="allowedExecutionProfileRefs", min_length=1
    )
    allowed_launch_policy_refs: list[str] = Field(
        alias="allowedLaunchPolicyRefs", min_length=1
    )
    allowed_runtime: Literal["external/omnigent"] = Field(
        "external/omnigent", alias="allowedRuntime"
    )
    allowed_product: Literal["codex-native"] = Field(
        "codex-native", alias="allowedProduct"
    )
    allowed_host_profile: Literal["omnigent-host-codex"] = Field(
        "omnigent-host-codex", alias="allowedHostProfile"
    )


class FrozenProfileBoundLane(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    runtime: Literal["external/omnigent"]
    product: Literal["codex-native"]
    execution_profile_id: str = Field(alias="executionProfileId", min_length=1)
    provider_profile_id: str = Field(alias="providerProfileId", min_length=1)
    provider_profile_generation: str = Field(
        alias="providerProfileGeneration", min_length=1
    )
    host_profile: Literal["omnigent-host-codex"] = Field(alias="hostProfile")
    launch_policy_ref: str = Field(alias="launchPolicyRef", min_length=1)
    launch_policy_digest: str = Field(alias="launchPolicyDigest", min_length=1)
    capability_snapshot_ref: str = Field(alias="capabilitySnapshotRef", min_length=1)
    capability_snapshot_digest: str = Field(
        alias="capabilitySnapshotDigest", min_length=1
    )
    effective_launch_snapshot_ref: str = Field(
        alias="effectiveLaunchSnapshotRef", min_length=1
    )
    effective_launch_snapshot_digest: str = Field(
        alias="effectiveLaunchSnapshotDigest", min_length=1
    )
    checkpoint_boundary_support: dict[str, list[str]] = Field(
        alias="checkpointBoundarySupport"
    )


class PreservedControlStopStep(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    source_step_execution_id: str = Field(alias="sourceStepExecutionId", min_length=1)
    logical_step_id: str = Field(alias="logicalStepId", min_length=1)
    execution_ordinal: int = Field(alias="executionOrdinal", ge=1)
    terminal_disposition: Literal["accepted", "accepted_control_result"] = Field(
        alias="terminalDisposition"
    )
    output_refs: dict[str, str] = Field(default_factory=dict, alias="outputRefs")
    checkpoint_ref: str | None = Field(None, alias="checkpointRef")
    dependency_output_signatures: dict[str, str] = Field(
        default_factory=dict, alias="dependencyOutputSignatures"
    )
    semantic_verdict: str | None = Field(None, alias="semanticVerdict")

    @model_validator(mode="after")
    def _negative_verifier_is_control_evidence(self) -> PreservedControlStopStep:
        if (
            self.semantic_verdict == "ADDITIONAL_WORK_NEEDED"
            and self.terminal_disposition != "accepted_control_result"
        ):
            raise ValueError(
                "the negative verifier must be preserved as an accepted control result"
            )
        return self


class PreservedSideEffect(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    operation: str = Field(min_length=1)
    evidence_ref: str = Field(alias="evidenceRef", min_length=1)
    disposition: Literal["already_performed"]


class ContinuationVerificationResult(BaseModel):
    """Typed semantic evidence returned by the authoritative verifier."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    verdict: Literal[
        "FULLY_IMPLEMENTED",
        "ADDITIONAL_WORK_NEEDED",
        "BLOCKED",
        "FAILED_UNRECOVERABLE",
        "ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION",
    ]
    verification_ref: str = Field(alias="verificationRef", min_length=1)
    remaining_work_ref: str | None = Field(None, alias="remainingWorkRef")
    progress: bool
    progress_evidence_ref: str | None = Field(None, alias="progressEvidenceRef")

    @model_validator(mode="after")
    def _remaining_work_is_authoritative(self) -> ContinuationVerificationResult:
        if self.verdict == "ADDITIONAL_WORK_NEEDED" and not self.remaining_work_ref:
            raise ValueError("ADDITIONAL_WORK_NEEDED requires remainingWorkRef")
        return self


class ContinuationAttemptEvidence(BaseModel):
    """Compact, replay-safe evidence for one remediation/verification pair."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    attempt_ordinal: int = Field(alias="attemptOrdinal", ge=1)
    remediation_step_execution_id: str = Field(
        alias="remediationStepExecutionId", min_length=1
    )
    verification_step_execution_id: str = Field(
        alias="verificationStepExecutionId", min_length=1
    )
    candidate_ref: str = Field(alias="candidateRef", min_length=1)
    candidate_digest: str = Field(alias="candidateDigest", min_length=1)
    progress: bool
    semantic_verdict: str = Field(alias="semanticVerdict", min_length=1)
    verification_ref: str = Field(alias="verificationRef", min_length=1)
    remaining_work_ref: str | None = Field(None, alias="remainingWorkRef")
    lifecycle: dict[str, str | bool | int | None] = Field(default_factory=dict)


class ControlStopContinuationState(BaseModel):
    """State transferred verbatim across Continue-As-New boundaries."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    destination_workspace_locator: dict[str, Any] = Field(
        alias="destinationWorkspaceLocator"
    )
    restoration_evidence_ref: str = Field(alias="restorationEvidenceRef", min_length=1)
    restoration_evidence_digest: str = Field(
        alias="restorationEvidenceDigest", min_length=1
    )
    latest_workspace_head_ref: str = Field(alias="latestWorkspaceHeadRef", min_length=1)
    latest_workspace_head_digest: str = Field(
        alias="latestWorkspaceHeadDigest", min_length=1
    )
    latest_verification_ref: str = Field(alias="latestVerificationRef", min_length=1)
    remaining_work_ref: str = Field(alias="remainingWorkRef", min_length=1)
    continuation_budget: ContinuationBudgetGrant = Field(alias="continuationBudget")
    attempts: list[ContinuationAttemptEvidence] = Field(default_factory=list)
    continue_as_new_count: int = Field(0, alias="continueAsNewCount", ge=0)


class ControlStopContinuationContract(BaseModel):
    """Immutable workflow input for one linked remediation continuation."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["control-stop-continuation/v2"] = Field(
        "control-stop-continuation/v2", alias="schemaVersion"
    )
    target_kind: Literal["control_stop"] = Field(alias="targetKind")
    phase: Literal["continue_to_remediation"]
    source_outcome_kind: Literal["workflow_gate"] = Field(alias="sourceOutcomeKind")
    source_workflow_id: str = Field(alias="sourceWorkflowId", min_length=1)
    source_run_id: str = Field(alias="sourceRunId", min_length=1)
    owner_type: Literal["user", "service", "system"] = Field(alias="ownerType")
    owner_id: str = Field(alias="ownerId", min_length=1)
    control_stop_id: str = Field(alias="controlStopId", min_length=1)
    semantic_verdict: Literal["ADDITIONAL_WORK_NEEDED"] = Field(alias="semanticVerdict")
    stop_reason: Literal[
        "remediation_budget_exhausted",
        "no_progress_attempts_exhausted",
        "semantic_no_progress_exhausted",
    ] = Field(alias="stopReason")
    gate_result_ref: str = Field(alias="gateResultRef", min_length=1)
    gate_result_digest: str = Field(alias="gateResultDigest", min_length=1)
    remaining_work_ref: str = Field(alias="remainingWorkRef", min_length=1)
    remaining_work_digest: str = Field(alias="remainingWorkDigest", min_length=1)
    checkpoint_ref: str = Field(alias="checkpointRef", min_length=1)
    checkpoint_digest: str = Field(alias="checkpointDigest", min_length=1)
    workspace_head_ref: str = Field(alias="workspaceHeadRef", min_length=1)
    workspace_head_digest: str = Field(
        alias="workspaceHeadDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    workspace_base_commit: str = Field(alias="workspaceBaseCommit", min_length=1)
    workspace_manifest_ref: str = Field(alias="workspaceManifestRef", min_length=1)
    workspace_manifest_digest: str = Field(
        alias="workspaceManifestDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    checkpoint_kind: Literal["worktree_archive"] = Field(alias="checkpointKind")
    checkpoint_boundary: Literal["after_gate"] = Field(alias="checkpointBoundary")
    terminal_head: bool = Field(alias="terminalHead")
    task_input_snapshot_ref: str = Field(alias="taskInputSnapshotRef", min_length=1)
    task_input_snapshot_digest: str = Field(
        alias="taskInputSnapshotDigest", min_length=1
    )
    plan_ref: str = Field(alias="planRef", min_length=1)
    plan_digest: str = Field(alias="planDigest", min_length=1)
    lane: FrozenProfileBoundLane
    preserved_steps: list[PreservedControlStopStep] = Field(
        alias="preservedSteps", min_length=1
    )
    side_effects: list[PreservedSideEffect] = Field(alias="sideEffects")
    source_budget: SourceBudgetEvidence = Field(alias="sourceBudget")
    continuation_budget: ContinuationBudgetGrant = Field(alias="continuationBudget")
    deployment_generation: str = Field(alias="deploymentGeneration", min_length=1)
    deployment_promoted: bool = Field(alias="deploymentPromoted")
    rollout: ControlStopRolloutPolicy
    restore_capability_set_version: str = Field(
        alias="restoreCapabilitySetVersion", min_length=1
    )
    restore_capability_digest: str = Field(
        alias="restoreCapabilityDigest", min_length=1
    )
    capture_capability_set_version: Literal[
        "runtime-execution-capabilities-v1",
        "runtime-execution-capabilities-v2",
        "runtime-execution-capabilities-v3",
    ] = Field(alias="captureCapabilitySetVersion")
    capture_capability_digest: str = Field(
        alias="captureCapabilityDigest", min_length=1
    )
    verification_instruction_ref: str = Field(
        alias="verificationInstructionRef", min_length=1
    )
    verification_instruction_digest: str = Field(
        alias="verificationInstructionDigest", min_length=1
    )
    continue_as_new_after_attempts: int = Field(
        10, alias="continueAsNewAfterAttempts", ge=1, le=100
    )
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=200)
    instruction_changes_ref: str | None = Field(None, alias="instructionChangesRef")
    instruction_changes_digest: str | None = Field(
        None, alias="instructionChangesDigest"
    )

    @model_validator(mode="after")
    def _fail_closed_admission(self) -> ControlStopContinuationContract:
        if not self.terminal_head:
            raise ValueError("selected checkpoint is not the source terminal head")
        if not self.deployment_promoted:
            raise ValueError("control-stop continuation deployment is not promoted")
        if self.rollout.mode == "shadow":
            raise ValueError("control-stop continuation is shadow diagnostics only")
        if (
            self.rollout.mode == "canary"
            and self.owner_id not in self.rollout.canary_owner_ids
        ):
            raise ValueError("owner is not selected for the control-stop canary")
        if (
            self.lane.provider_profile_id
            not in self.rollout.allowed_provider_profile_ids
        ):
            raise ValueError("selected Provider Profile is not rollout-allowlisted")
        if (
            self.lane.execution_profile_id
            not in self.rollout.allowed_execution_profile_refs
        ):
            raise ValueError("selected execution profile is not rollout-allowlisted")
        if self.lane.launch_policy_ref not in self.rollout.allowed_launch_policy_refs:
            raise ValueError("selected launch policy is not rollout-allowlisted")
        if bool(self.instruction_changes_ref) != bool(self.instruction_changes_digest):
            raise ValueError("instruction changes require both ref and digest")
        if not any(
            step.semantic_verdict == "ADDITIONAL_WORK_NEEDED"
            and step.terminal_disposition == "accepted_control_result"
            for step in self.preserved_steps
        ):
            raise ValueError("preserved terminal verifier evidence is required")
        if self.phase not in self.lane.checkpoint_boundary_support.get(
            self.checkpoint_boundary, []
        ):
            raise ValueError(
                "frozen runtime capabilities do not authorize the checkpoint "
                "boundary and continuation phase"
            )
        scan_payload = self.model_dump(by_alias=True, mode="json")
        scan = scan_outbound_text(
            json.dumps(scan_payload, sort_keys=True, separators=(",", ":")),
            location="control_stop_continuation.contract",
            high_security_mode=True,
        )
        if not scan.allowed:
            raise ValueError(
                "control-stop continuation contract failed secret scanning"
            )
        return self

    @property
    def destination_workflow_id(self) -> str:
        identity = {
            "sourceWorkflowId": self.source_workflow_id,
            "sourceRunId": self.source_run_id,
            "controlStopId": self.control_stop_id,
            "checkpointRef": self.checkpoint_ref,
            "checkpointDigest": self.checkpoint_digest,
            "workspaceHeadRef": self.workspace_head_ref,
            "workspaceHeadDigest": self.workspace_head_digest,
            "idempotencyKey": self.idempotency_key,
        }
        digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:32]
        return f"control-stop-continuation-{digest}"

    @property
    def destination_workspace_id(self) -> str:
        return f"{self.destination_workflow_id}:workspace"

    def workflow_entry(self) -> dict[str, Any]:
        """Return the deterministic initial controller state.

        The first operation is intentionally remediation. Restoration and
        provenance import are prerequisites and are not business attempts.
        """

        return {
            "destinationWorkflowId": self.destination_workflow_id,
            "destinationWorkspaceId": self.destination_workspace_id,
            "candidateState": "recovered_candidate",
            "workspaceHeadRef": self.workspace_head_ref,
            "remainingWorkRef": self.remaining_work_ref,
            "latestVerificationRef": self.gate_result_ref,
            "nextSemanticOperation": "remediation",
            "nextAttemptOrdinal": self.source_budget.consumed_attempts + 1,
            "continuationBudget": self.continuation_budget.model_dump(
                by_alias=True, mode="json"
            ),
            "runtime": "external/omnigent",
            "product": "codex-native",
            "providerProfileId": self.lane.provider_profile_id,
            "hostProfile": "omnigent-host-codex",
            "sourceBudget": self.source_budget.model_dump(by_alias=True, mode="json"),
            "proposedGrant": self.continuation_budget.model_dump(
                by_alias=True, mode="json"
            ),
            "rolloutGeneration": self.deployment_generation,
        }

    def restore_request(self, *, destination_run_id: str) -> dict[str, Any]:
        """Build the canonical cold-restore activity request.

        The recovery workflow validates this request and its result before an
        Omnigent AgentRun may be started.
        """

        terminal_verifier = next(
            step
            for step in reversed(self.preserved_steps)
            if step.semantic_verdict == "ADDITIONAL_WORK_NEEDED"
            and step.terminal_disposition == "accepted_control_result"
        )
        return {
            "schemaVersion": "v1",
            "recoveryIdentity": {
                "workflowId": self.destination_workflow_id,
                "runId": destination_run_id,
                "logicalStepId": "continue-remediation",
                "executionOrdinal": self.source_budget.consumed_attempts + 1,
            },
            "source": {
                "workflowId": self.source_workflow_id,
                "runId": self.source_run_id,
                "logicalStepId": terminal_verifier.logical_step_id,
                "executionOrdinal": terminal_verifier.execution_ordinal,
                "checkpointRef": self.checkpoint_ref,
                "checkpointBoundary": self.checkpoint_boundary,
            },
            "checkpoint": {
                "kind": self.checkpoint_kind,
                "baseCommit": self.workspace_base_commit,
                "archiveRef": self.workspace_head_ref,
                "archiveDigest": self.workspace_head_digest,
                "manifestRef": self.workspace_manifest_ref,
                "manifestDigest": self.workspace_manifest_digest,
            },
            "destination": {
                "runtimeId": "codex_cli",
                "agentRunId": self.destination_workspace_id,
                "repository": self.repository,
                "relativePath": "repo",
            },
            "workspacePolicy": "restore_pre_execution",
            "resumePhase": "continue_to_remediation",
            "capabilitySetVersion": self.restore_capability_set_version,
            "capabilityDigest": self.restore_capability_digest,
            "idempotencyKey": f"{self.destination_workflow_id}:restore",
        }

    def remediation_request(
        self,
        *,
        destination_run_id: str,
        destination_workspace_locator: dict[str, Any],
        attempt: int | None = None,
        workspace_head_ref: str | None = None,
        latest_verification_ref: str | None = None,
        remaining_work_ref: str | None = None,
        continuation_budget: ContinuationBudgetGrant | None = None,
    ) -> dict[str, Any]:
        """Build one retry-safe profile-bound remediation operation."""

        attempt = attempt or self.source_budget.consumed_attempts + 1
        remaining_work_ref = remaining_work_ref or self.remaining_work_ref
        latest_verification_ref = latest_verification_ref or self.gate_result_ref
        workspace_head_ref = workspace_head_ref or self.workspace_head_ref
        continuation_budget = continuation_budget or self.continuation_budget
        step_execution_id = (
            f"{self.destination_workflow_id}:remediation:execution:{attempt}"
        )
        return {
            "agentKind": "external",
            "agentId": "omnigent",
            "executionProfileRef": self.lane.provider_profile_id,
            "correlationId": self.destination_workflow_id,
            "idempotencyKey": step_execution_id,
            "instructionRef": remaining_work_ref,
            "inputRefs": [
                remaining_work_ref,
                latest_verification_ref,
                workspace_head_ref,
                self.plan_ref,
                self.task_input_snapshot_ref,
            ],
            "workspaceSpec": {
                "workspaceLocator": destination_workspace_locator,
                "workspacePolicy": "continue_from_restored_control_stop",
            },
            "parameters": {
                "workflowId": self.destination_workflow_id,
                "runId": destination_run_id,
                "logicalStepId": "remediation",
                "stepExecutionId": step_execution_id,
                "attemptOrdinal": attempt,
                "providerProfileId": self.lane.provider_profile_id,
                "providerProfileGeneration": self.lane.provider_profile_generation,
                "executionProfileRef": self.lane.execution_profile_id,
                "launchPolicyRef": self.lane.launch_policy_ref,
                "sourceWorkflowId": self.source_workflow_id,
                "sourceRunId": self.source_run_id,
                "controlStopId": self.control_stop_id,
                "continuationBudget": continuation_budget.model_dump(
                    by_alias=True, mode="json"
                ),
            },
        }

    def capture_request(
        self,
        *,
        destination_run_id: str,
        destination_workspace_locator: dict[str, Any],
        attempt: int,
    ) -> dict[str, Any]:
        """Capture the cumulative candidate produced by one remediation attempt."""

        return {
            "schemaVersion": "v1",
            "identity": {
                "workflowId": self.destination_workflow_id,
                "runId": destination_run_id,
                "logicalStepId": "remediation",
                "executionOrdinal": attempt,
            },
            "boundary": "after_execution",
            "checkpointKind": "worktree_archive",
            "workspaceLocator": destination_workspace_locator,
            "expectedRuntimeId": "codex_cli",
            "capabilitySetVersion": self.capture_capability_set_version,
            "capabilityDigest": self.capture_capability_digest,
            "artifactNamespace": (
                f"control-stop-continuations/{self.destination_workflow_id}/"
                f"attempts/{attempt}"
            ),
            "idempotencyKey": (
                f"{self.destination_workflow_id}:remediation:{attempt}:capture"
            ),
        }

    def verification_request(
        self,
        *,
        destination_run_id: str,
        destination_workspace_locator: dict[str, Any],
        attempt: int,
        workspace_head_ref: str,
        remaining_work_ref: str,
    ) -> dict[str, Any]:
        """Build the authoritative verifier operation for a captured candidate."""

        step_execution_id = (
            f"{self.destination_workflow_id}:verification:execution:{attempt}"
        )
        return {
            "agentKind": "external",
            "agentId": "omnigent",
            "executionProfileRef": self.lane.provider_profile_id,
            "correlationId": self.destination_workflow_id,
            "idempotencyKey": step_execution_id,
            "inputRefs": [
                workspace_head_ref,
                remaining_work_ref,
                self.plan_ref,
                self.task_input_snapshot_ref,
            ],
            "workspaceSpec": {
                "workspaceLocator": destination_workspace_locator,
                "workspacePolicy": "verify_restored_control_stop_candidate",
            },
            "parameters": {
                "omnigent": {
                    "prompt": {
                        "instructionRef": self.verification_instruction_ref,
                    }
                },
                "workflowId": self.destination_workflow_id,
                "runId": destination_run_id,
                "logicalStepId": "verification",
                "stepExecutionId": step_execution_id,
                "attemptOrdinal": attempt,
                "providerProfileId": self.lane.provider_profile_id,
                "providerProfileGeneration": self.lane.provider_profile_generation,
                "executionProfileRef": self.lane.execution_profile_id,
                "launchPolicyRef": self.lane.launch_policy_ref,
                "sourceWorkflowId": self.source_workflow_id,
                "sourceRunId": self.source_run_id,
                "controlStopId": self.control_stop_id,
                "candidateRef": workspace_head_ref,
                "expectedResultContract": {
                    "metadata.controlStopVerification": (
                        "ContinuationVerificationResult"
                    ),
                },
            },
        }


class ControlStopContinuationWorkflowInput(BaseModel):
    """Stable workflow input for initial execution and Continue-As-New."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["control-stop-continuation-workflow/v1"] = Field(
        "control-stop-continuation-workflow/v1", alias="schemaVersion"
    )
    contract: ControlStopContinuationContract
    state: ControlStopContinuationState | None = None

    @classmethod
    def initial(
        cls, contract: ControlStopContinuationContract
    ) -> ControlStopContinuationWorkflowInput:
        return cls(contract=contract)
