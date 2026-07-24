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
    def _bounded_consumption(self) -> "ContinuationBudgetGrant":
        if self.consumed_attempts > self.max_attempts:
            raise ValueError("consumedAttempts exceeds the continuation grant")
        if (
            self.consecutive_no_progress_attempts
            > self.max_consecutive_no_progress_attempts
        ):
            raise ValueError("no-progress consumption exceeds the continuation grant")
        return self

    def consume(self, *, progress: bool) -> "ContinuationBudgetGrant":
        if self.consumed_attempts >= self.max_attempts:
            raise ControlStopContinuationError("continuation_attempt_budget_exhausted")
        no_progress = 0 if progress else self.consecutive_no_progress_attempts + 1
        if no_progress > self.max_consecutive_no_progress_attempts:
            raise ControlStopContinuationError("continuation_no_progress_budget_exhausted")
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

    source_step_execution_id: str = Field(
        alias="sourceStepExecutionId", min_length=1
    )
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
    def _negative_verifier_is_control_evidence(self) -> "PreservedControlStopStep":
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
    disposition: Literal["already_performed", "reconcile", "reapply", "blocked"]
    idempotency_key: str | None = Field(None, alias="idempotencyKey")
    authorization_ref: str | None = Field(None, alias="authorizationRef")

    @model_validator(mode="after")
    def _safe_disposition(self) -> "PreservedSideEffect":
        if self.disposition == "reconcile" and not self.idempotency_key:
            raise ValueError("reconciliation requires a stable idempotencyKey")
        if self.disposition == "reapply" and not self.authorization_ref:
            raise ValueError("reapplication requires an authorizationRef")
        return self


class ControlStopContinuationContract(BaseModel):
    """Immutable workflow input for one linked remediation continuation."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["control-stop-continuation/v1"] = Field(
        "control-stop-continuation/v1", alias="schemaVersion"
    )
    target_kind: Literal["control_stop"] = Field(alias="targetKind")
    phase: Literal["continue_to_remediation"]
    source_outcome_kind: Literal["workflow_gate"] = Field(alias="sourceOutcomeKind")
    source_workflow_id: str = Field(alias="sourceWorkflowId", min_length=1)
    source_run_id: str = Field(alias="sourceRunId", min_length=1)
    owner_type: Literal["user", "service", "system"] = Field(alias="ownerType")
    owner_id: str = Field(alias="ownerId", min_length=1)
    control_stop_id: str = Field(alias="controlStopId", min_length=1)
    semantic_verdict: Literal["ADDITIONAL_WORK_NEEDED"] = Field(
        alias="semanticVerdict"
    )
    stop_reason: Literal[
        "remediation_budget_exhausted",
        "no_progress_attempts_exhausted",
        "semantic_no_progress_exhausted",
    ] = Field(alias="stopReason")
    gate_result_ref: str = Field(alias="gateResultRef", min_length=1)
    gate_result_digest: str = Field(alias="gateResultDigest", min_length=1)
    remaining_work_ref: str = Field(alias="remainingWorkRef", min_length=1)
    remaining_work_digest: str = Field(alias="remainingWorkDigest", min_length=1)
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
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=200)
    instruction_changes_ref: str | None = Field(None, alias="instructionChangesRef")
    instruction_changes_digest: str | None = Field(
        None, alias="instructionChangesDigest"
    )

    @model_validator(mode="after")
    def _fail_closed_admission(self) -> "ControlStopContinuationContract":
        if not self.terminal_head:
            raise ValueError("selected checkpoint is not the source terminal head")
        if not self.deployment_promoted:
            raise ValueError("control-stop continuation deployment is not promoted")
        if any(effect.disposition == "blocked" for effect in self.side_effects):
            raise ValueError("source side effects block continuation")
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
        return self

    @property
    def destination_workflow_id(self) -> str:
        identity = {
            "sourceWorkflowId": self.source_workflow_id,
            "sourceRunId": self.source_run_id,
            "controlStopId": self.control_stop_id,
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
                "checkpointRef": self.workspace_head_ref,
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
            "capabilitySetVersion": self.capture_capability_set_version,
            "capabilityDigest": self.capture_capability_digest,
            "idempotencyKey": f"{self.destination_workflow_id}:restore",
        }

    def remediation_request(
        self,
        *,
        destination_run_id: str,
        destination_workspace_locator: dict[str, Any],
        attempt: int | None = None,
        workspace_head_ref: str | None = None,
        remaining_work_ref: str | None = None,
    ) -> dict[str, Any]:
        """Build one profile-bound remediation operation."""

        attempt = attempt or self.source_budget.consumed_attempts + 1
        step_execution_id = (
            f"{self.destination_workflow_id}:remediation:execution:{attempt}"
        )
        return {
            "agentKind": "external",
            "agentId": "omnigent",
            "executionProfileRef": self.lane.provider_profile_id,
            "correlationId": self.destination_workflow_id,
            "idempotencyKey": step_execution_id,
            "instructionRef": remaining_work_ref or self.remaining_work_ref,
            "inputRefs": [
                remaining_work_ref or self.remaining_work_ref,
                self.gate_result_ref,
                self.plan_ref,
                self.task_input_snapshot_ref,
                workspace_head_ref or self.workspace_head_ref,
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
                "continuationBudget": self.continuation_budget.model_dump(
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
        """Capture the cumulative destination head idempotently after remediation."""

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
            "capabilitySetVersion": self.restore_capability_set_version,
            "capabilityDigest": self.restore_capability_digest,
            "artifactNamespace": self.destination_workflow_id,
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
    ) -> dict[str, Any]:
        """Build the verifier paired with a newly captured cumulative head."""

        step_execution_id = (
            f"{self.destination_workflow_id}:verification:execution:{attempt}"
        )
        return {
            "agentKind": "external",
            "agentId": "omnigent",
            "executionProfileRef": self.lane.provider_profile_id,
            "correlationId": self.destination_workflow_id,
            "idempotencyKey": step_execution_id,
            "instructionRef": self.gate_result_ref,
            "inputRefs": [
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
                "logicalStepId": "verification",
                "stepExecutionId": step_execution_id,
                "attemptOrdinal": attempt,
                "providerProfileId": self.lane.provider_profile_id,
                "providerProfileGeneration": self.lane.provider_profile_generation,
                "executionProfileRef": self.lane.execution_profile_id,
                "launchPolicyRef": self.lane.launch_policy_ref,
                "expectedVerdicts": [
                    "FULLY_IMPLEMENTED",
                    "ADDITIONAL_WORK_NEEDED",
                    "BLOCKED",
                ],
            },
        }
