"""Operator-remediation release-gate support matrix.

Source issue: MoonLadderStudios/MoonMind#3626.

This module defines the ONE versioned, machine-readable operator-remediation
required-row catalog and the evidence-artifact contract that binds each row to
independently observed live evidence. It is the controlling artifact for the
"can an operator safely diagnose and repair a real workflow" release claim.

The contract is deliberately fail-closed and honest about provenance:

* **Support is observed, never asserted.** A row becomes supported only when a
  ``moonmind.operator-remediation-evidence/v1`` artifact carries the observed
  lifecycle result for the rows it owns. A caller-supplied row list, a bare
  ``passed`` boolean, or mere code presence never qualifies support. This
  mirrors :mod:`moonmind.omnigent.cutover` for the Codex cutover matrix.
* **Delivery and repair are separate columns.** Every mutating row must record
  the action *delivery* status and the target *repair* verification outcome as
  two distinct fields, so "the adapter persisted a branch" can never be reported
  as "the target objective is resolved" (MoonLadderStudios/MoonMind#3622 made
  this a first-class phase; this matrix enforces the separation in evidence).
* **The normal product path is the only authority.** Each row observation binds
  a browser-originated normal Create journey; a hidden submission, manual host or
  session id, alternate wire contract, unvalidated policy/profile field,
  direct-Codex fallback, or log-derived authority fails the row closed.
* **Autonomous mutation stays disabled.** A fully passing manual matrix never
  silently authorizes autonomous rollout: the autonomous gate is a hard,
  fail-closed blocker in this version (acceptance criterion 9). No workflow is
  ever granted ``admin_auto`` by publishing evidence.

The canonical authority-mode vocabulary (``observe_only`` / ``approval_gated`` /
``admin_auto``) is owned by
:mod:`moonmind.workflows.temporal.remediation_actions`; the repair-verification
outcome vocabulary is owned by
:mod:`moonmind.workflows.temporal.remediation_verification`. This contract module
carries only compact metadata and does not import those workflow modules, so it
stays free of ``api_service``/SQLAlchemy dependencies. Drift is guarded by tests.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

from moonmind.omnigent.conformance import (
    ConformanceContractError,
    assert_secret_free,
    require_pinned_images,
)
from moonmind.omnigent.cutover import (
    ALLOWED_HOST_MODES,
    MAX_EVIDENCE_AGE_SECONDS,
)

REMEDIATION_MATRIX_VERSION = "operator-remediation-support-matrix/v1"
REMEDIATION_ARTIFACT_SCHEMA_VERSION = "moonmind.operator-remediation-evidence/v1"
REMEDIATION_RELEASE_POLICY_VERSION = "moonmind.operator-remediation-release/v1"

# Gate classes: which release decision a passing row qualifies.
GATE_MANUAL_DIAGNOSIS = "manual_diagnosis"
GATE_MANUAL_MUTATION = "manual_mutation"
GATE_AUTONOMOUS_ROLLOUT = "autonomous_rollout"
GATE_CLASSES = (GATE_MANUAL_DIAGNOSIS, GATE_MANUAL_MUTATION, GATE_AUTONOMOUS_ROLLOUT)

# Canonical remediation authority modes. Mirror
# ``moonmind.workflows.temporal.remediation_actions._SUPPORTED_AUTHORITY_MODES``;
# drift is asserted by tests rather than importing the workflow module here.
AUTHORITY_MODES = ("observe_only", "approval_gated", "admin_auto")

# Expected disposition of a row. A row is satisfied either by a passing
# capability observation or by an *intentional* safety denial; both are release
# evidence, but each row fixes which one it requires.
OUTCOME_PASSED = "passed"
OUTCOME_DENIED = "denied"
ROW_OUTCOMES = (OUTCOME_PASSED, OUTCOME_DENIED)

# Egress authority a row exercises at the host/runtime boundary.
EGRESS_NOT_APPLICABLE = "not_applicable"
EGRESS_RESTRICTED_ALLOWED = "restricted_allowed"
EGRESS_RESTRICTED_DENIED = "restricted_denied"
EGRESS_MODES = (
    EGRESS_NOT_APPLICABLE,
    EGRESS_RESTRICTED_ALLOWED,
    EGRESS_RESTRICTED_DENIED,
)

# Owning evidence kinds. Each protected row is owned by exactly one kind so a
# single hand-authored document cannot splice partial results from separate runs.
REQUIRED_REMEDIATION_EVIDENCE_KINDS = (
    "diagnosisEvidence",
    "recoveryBranchEvidence",
    "actionApprovalEvidence",
    "verificationPreventionEvidence",
    "reliabilitySecurityEvidence",
)
REQUIRED_REMEDIATION_RETAINED_CHANNELS = (
    "logs",
    "events",
    "screenshotsCaptures",
    "diagnostics",
    "artifacts",
    "histories",
    "archives",
)

# Remediation-specific telemetry the release status must carry (section 5).
REQUIRED_REMEDIATION_TELEMETRY_GROUPS = (
    "remediationCreation",
    "contextBuild",
    "evidenceAvailability",
    "approvalOutcomes",
    "actionOutcomesByKindAndRisk",
    "lockCooldownDuplicateAndEscalation",
    "branchLifecycleLatency",
    "verificationOutcomes",
    "repeatedFailureAndAttemptExhaustion",
    "egressOutcomes",
    "operatorCancellationAndTakeover",
    "autonomousAndManualOrigin",
)

# Repair-verification outcome vocabulary. Mirror
# ``moonmind.workflows.temporal.remediation_verification.REMEDIATION_VERIFICATION_OUTCOMES``;
# drift is asserted by tests.
REMEDIATION_REPAIR_OUTCOMES = frozenset(
    {
        "verified_resolved",
        "verified_no_change",
        "still_failed",
        "regressed",
        "evidence_unavailable",
        "approval_required",
        "verification_failed",
        "canceled",
    }
)

# Action-delivery statuses recorded independently of the repair outcome (AC5).
REMEDIATION_DELIVERY_STATUSES = frozenset(
    {"delivered", "denied", "suppressed_idempotent", "not_delivered", "not_applicable"}
)

# The normal-product-path assertions a browser journey must prove for every row.
REQUIRED_UI_JOURNEY_ASSERTIONS = (
    "browserOriginated",
    "importedPinnedRemediationDraft",
    "normalCreateRequest",
    "validatedPolicyProfileFields",
    "workflowDetailFollowThrough",
)
# Authority forms that must never appear; any truthy marker fails the row.
PROHIBITED_UI_JOURNEY_MARKERS = (
    "hiddenSubmission",
    "manualHostOrSessionId",
    "alternateWireContract",
    "unvalidatedPolicyProfileFields",
    "directCodexFallback",
    "logDerivedAuthority",
)

# Each live row must retain these independently digest-bound JSON records.  They
# are deliberately semantic record types, not provider log names, so the matrix
# can be replayed after the host and helper containers are gone.
REQUIRED_REMEDIATION_SOURCE_RECORD_TYPES = frozenset(
    {
        "scenarioObservation",
        "browserTrace",
        "authoredRequest",
        "immutableInputSnapshot",
        "workflowLineage",
        "contextEvidence",
        "profilePolicyAuthority",
        "egressAttestation",
        "approvalDecision",
        "actionResult",
        "verificationResult",
        "publicationOutcome",
        "cleanupOutcome",
        "temporalHistory",
        "sideEffectAudit",
        "retainedEvidenceScan",
    }
)
REMEDIATION_SOURCE_RECORD_CONTENT_TYPE = "application/json"
MAX_REMEDIATION_SOURCE_RECORD_BYTES = 2 * 1024 * 1024

# Every retained source record has one exact versioned schema.  Accepting an
# arbitrary non-empty schema string made a digest-bound but semantically empty
# document sufficient release evidence.  These schemas are intentionally
# record-specific so producers and post-cleanup validators share one contract.
REMEDIATION_SOURCE_RECORD_SCHEMAS: Mapping[str, str] = {
    record_type: f"moonmind.operator-remediation-{record_type}/v1"
    for record_type in REQUIRED_REMEDIATION_SOURCE_RECORD_TYPES
}
REMEDIATION_SOURCE_RECORD_SCHEMAS = {
    **REMEDIATION_SOURCE_RECORD_SCHEMAS,
    "scenarioObservation": "moonmind.operator-remediation-scenario-observation/v1",
}

# Cross-record identity is repeated in every independently retained source
# record.  This is deliberate: it prevents a valid approval, action, or cleanup
# record from a different run/session being spliced into a row after resources
# have been removed.
REMEDIATION_EVIDENCE_IDENTITY_FIELDS = (
    "targetWorkflowId",
    "targetRunId",
    "targetStepId",
    "targetAttemptId",
    "targetBranch",
    "remediationWorkflowId",
    "remediationRunId",
    "remediationStepId",
    "remediationAttemptId",
    "remediationBranch",
    "agentProfileId",
    "providerProfileId",
    "leaseId",
    "hostId",
    "bridgeId",
    "sessionId",
)

REMEDIATION_TELEMETRY_SCHEMA_VERSION = (
    "moonmind.operator-remediation-telemetry/v1"
)
REMEDIATION_RELEASE_THRESHOLD_SCHEMA_VERSION = (
    "moonmind.operator-remediation-release-thresholds/v1"
)
REQUIRED_REMEDIATION_PHASE_LATENCIES = (
    "branchLaunch",
    "hostSession",
    "firstMessage",
    "terminal",
    "publication",
    "cleanup",
)
REMEDIATION_ACTION_RISKS = ("not_applicable", "low", "medium", "high")
REMEDIATION_ACTION_OUTCOMES = (
    "delivered",
    "no_op",
    "failure",
    "unknown",
    "denied",
)
REMEDIATION_APPROVAL_OUTCOMES = (
    "approved",
    "denied",
    "expired",
    "consumed",
    "unauthorized",
    "stale",
    "not_required",
)

# Raw, side-effect-owner observations used to qualify catalog rows.  These
# values live in the independently retained typed records below; the
# ``scenarioObservation`` and ``sideEffectAudit`` records only summarize them.
CONTEXT_FOLLOW_PHASES = ("snapshot", "follow", "reconnect", "cursor_recovery")
CONTEXT_DENIAL_CASES = ("missing", "unauthorized")
CONTEXT_NONDISCLOSURE_PROTECTIONS = ("target_existence", "storage_authority")
RESUME_AUTHORITY_CASES = ("stale", "incomplete", "mismatched")
BRANCH_CHANGED_CHOICES = (
    "model",
    "profile",
    "policy",
    "branch",
    "publish",
    "retrieval",
)
ACTION_RISK_CASES = ("low", "medium")
STALE_AUTHORITY_CASES = (
    "target",
    "run",
    "checkpoint",
    "session",
    "host",
    "lease",
    "credential_generation",
    "policy",
)
SESSION_CONTROL_CASES = ("interrupt", "clear", "cancel", "terminate", "restart")
NON_RESOLVED_VERIFICATION_CASES = (
    "still_failed",
    "regressed",
    "evidence_unavailable",
    "verification_failed",
)
REMEDIATION_DURABLE_PHASES = (
    "diagnosis",
    "approval_wait",
    "action",
    "branch_execution",
    "verification",
    "publication",
    "cleanup",
)
DUPLICATE_EFFECT_CASES = (
    "first_message",
    "host",
    "session",
    "branch",
    "commit",
    "pull_request",
    "action",
    "verification",
)
PROHIBITED_AUTHORITY_CASES = (
    "raw_host_shell",
    "docker",
    "sql",
    "storage_key",
    "filesystem_path",
    "credential",
    "secret_read",
    "redaction_bypass",
)

# Durable identities and refs called out by #3626 section 4.  Rows may use an
# explicit not-applicable artifact, but they may not silently omit a field.
REQUIRED_REMEDIATION_LINEAGE_FIELDS = (
    "authoredRequestRef",
    "immutableInputSnapshotRef",
    "targetWorkflowId",
    "targetRunId",
    "targetStepId",
    "targetAttemptId",
    "targetBranch",
    "remediationWorkflowId",
    "remediationRunId",
    "remediationStepId",
    "remediationAttemptId",
    "remediationBranch",
    "contextRef",
    "evidenceAvailabilityRef",
    "agentProfileRef",
    "providerProfileRef",
    "policyRef",
    "approvalRef",
    "egressRef",
    "leaseRef",
    "hostRef",
    "bridgeRef",
    "sessionRef",
    "firstMessageRef",
    "eventCursorRef",
    "workspaceRef",
    "checkpointRef",
    "actionRequestRef",
    "actionResultRef",
    "beforeStateRef",
    "afterStateRef",
    "verificationResultRef",
    "stabilizedTargetRef",
    "immediateRepairOutcomeRef",
    "preventionOutcomeRef",
    "publicationRef",
    "terminalHarvestRef",
    "cleanupRef",
    "janitorRef",
    "lockReleaseRef",
    "capacityReleaseRef",
)

# Every lineage *Ref is the ref of a typed record in the same manifest.  A
# record may own several related lineage facts, but no opaque or non-resolving
# artifact:// value can qualify support.
REMEDIATION_LINEAGE_REF_RECORD_TYPES: Mapping[str, str] = {
    "authoredRequestRef": "authoredRequest",
    "immutableInputSnapshotRef": "immutableInputSnapshot",
    "contextRef": "contextEvidence",
    "evidenceAvailabilityRef": "contextEvidence",
    "agentProfileRef": "profilePolicyAuthority",
    "providerProfileRef": "profilePolicyAuthority",
    "policyRef": "profilePolicyAuthority",
    "approvalRef": "approvalDecision",
    "egressRef": "egressAttestation",
    "leaseRef": "profilePolicyAuthority",
    "hostRef": "workflowLineage",
    "bridgeRef": "workflowLineage",
    "sessionRef": "workflowLineage",
    "firstMessageRef": "sideEffectAudit",
    "eventCursorRef": "temporalHistory",
    "workspaceRef": "workflowLineage",
    "checkpointRef": "workflowLineage",
    "actionRequestRef": "actionResult",
    "actionResultRef": "actionResult",
    "beforeStateRef": "actionResult",
    "afterStateRef": "actionResult",
    "verificationResultRef": "verificationResult",
    "stabilizedTargetRef": "verificationResult",
    "immediateRepairOutcomeRef": "verificationResult",
    "preventionOutcomeRef": "verificationResult",
    "publicationRef": "publicationOutcome",
    "terminalHarvestRef": "cleanupOutcome",
    "cleanupRef": "cleanupOutcome",
    "janitorRef": "cleanupOutcome",
    "lockReleaseRef": "cleanupOutcome",
    "capacityReleaseRef": "cleanupOutcome",
}


@dataclass(frozen=True, slots=True)
class RemediationMatrixRow:
    """One protected operator-remediation row and its full support contract.

    Every field maps to a required attribute from
    MoonLadderStudios/MoonMind#3626 section 1:

    * ``row_id`` / ``owner`` — scenario identity and owner.
    * ``target_provenance`` / ``remediation_provenance`` — target and
      remediation runtime provenance.
    * ``host_modes`` — supported host mode(s); architecture is bound per
      observation against the released architecture set.
    * ``action_capability`` / ``verification_capability`` — required
      action/verification capability.
    * ``authority_mode`` / ``egress`` — required policy/profile/egress authority.
    * ``ui_journey`` — required UI journey identity.
    * ``evidence_kind`` — owning evidence kind (schema is the module-level
      artifact schema version).
    * ``thresholds`` — named pass/fail threshold keys that must be present and
      within limits for this row.
    * ``gate`` — whether the row gates manual diagnosis, manual mutation, or
      autonomous rollout.
    * ``expected_outcome`` — whether support requires a passing capability or an
      intentional safety denial.
    """

    row_id: str
    owner: str
    gate: str
    evidence_kind: str
    action_capability: str
    verification_capability: str
    authority_mode: str
    ui_journey: str
    target_provenance: tuple[str, ...] = ("omnigent",)
    remediation_provenance: tuple[str, ...] = ("omnigent",)
    host_modes: tuple[str, ...] = ("on_demand",)
    architectures: tuple[str, ...] = ("linux/amd64",)
    egress: str = EGRESS_NOT_APPLICABLE
    expected_outcome: str = OUTCOME_PASSED
    thresholds: tuple[str, ...] = ()
    required_observations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.gate not in GATE_CLASSES:
            raise ValueError(f"row {self.row_id!r} has invalid gate {self.gate!r}")
        if self.evidence_kind not in REQUIRED_REMEDIATION_EVIDENCE_KINDS:
            raise ValueError(
                f"row {self.row_id!r} has invalid evidence kind {self.evidence_kind!r}"
            )
        if self.authority_mode not in AUTHORITY_MODES:
            raise ValueError(
                f"row {self.row_id!r} has invalid authority mode {self.authority_mode!r}"
            )
        if self.egress not in EGRESS_MODES:
            raise ValueError(f"row {self.row_id!r} has invalid egress {self.egress!r}")
        if self.expected_outcome not in ROW_OUTCOMES:
            raise ValueError(
                f"row {self.row_id!r} has invalid expected outcome "
                f"{self.expected_outcome!r}"
            )
        if not self.host_modes or any(
            mode not in ALLOWED_HOST_MODES for mode in self.host_modes
        ):
            raise ValueError(f"row {self.row_id!r} has invalid host modes")
        if not self.architectures or any(
            not architecture.strip() for architecture in self.architectures
        ):
            raise ValueError(f"row {self.row_id!r} has invalid architectures")

    @property
    def is_mutation(self) -> bool:
        """Rows that gate mutation or autonomous rollout carry a repair phase."""

        return self.gate in (GATE_MANUAL_MUTATION, GATE_AUTONOMOUS_ROLLOUT)

    @property
    def action_risk(self) -> str:
        """Return the catalog-owned risk bucket used by action telemetry."""

        if not self.is_mutation:
            return "not_applicable"
        if self.action_capability == "action.high-risk" or self.gate == GATE_AUTONOMOUS_ROLLOUT:
            return "high"
        return "medium"


# Historical targets may have been launched on the legacy direct-Codex path even
# though the remediation runtime is always a stock profile-bound Omnigent host.
_HISTORICAL_TARGET = ("omnigent", "codex_direct_compat")


REMEDIATION_ROW_CATALOG: tuple[RemediationMatrixRow, ...] = (
    # --- Diagnosis and evidence --------------------------------------------
    RemediationMatrixRow(
        "remediation.diagnosis.observe-only",
        owner="moonmind.remediation.diagnosis",
        gate=GATE_MANUAL_DIAGNOSIS,
        evidence_kind="diagnosisEvidence",
        action_capability="evidence.collect",
        verification_capability="diagnosis.report",
        authority_mode="observe_only",
        ui_journey="workflow-detail.remediate.diagnosis",
        target_provenance=_HISTORICAL_TARGET,
        thresholds=("contextBuildSuccessRate",),
    ),
    RemediationMatrixRow(
        "remediation.evidence.partial-historical-degraded",
        owner="moonmind.remediation.diagnosis",
        gate=GATE_MANUAL_DIAGNOSIS,
        evidence_kind="diagnosisEvidence",
        action_capability="evidence.collect.partial",
        verification_capability="diagnosis.degraded-completion",
        authority_mode="observe_only",
        ui_journey="workflow-detail.remediate.diagnosis",
        target_provenance=_HISTORICAL_TARGET,
        thresholds=("evidenceDegradationRate",),
    ),
    RemediationMatrixRow(
        "remediation.evidence.active-snapshot-follow-reconnect",
        owner="moonmind.remediation.diagnosis",
        gate=GATE_MANUAL_DIAGNOSIS,
        evidence_kind="diagnosisEvidence",
        action_capability="evidence.snapshot-follow",
        verification_capability="diagnosis.cursor-recovery",
        authority_mode="observe_only",
        ui_journey="workflow-detail.remediate.live-follow",
        thresholds=("reconnectCursorRecoveryRate",),
        required_observations=CONTEXT_FOLLOW_PHASES,
    ),
    RemediationMatrixRow(
        "remediation.evidence.missing-unauthorized-denied",
        owner="moonmind.remediation.diagnosis",
        gate=GATE_MANUAL_DIAGNOSIS,
        evidence_kind="diagnosisEvidence",
        action_capability="evidence.collect",
        verification_capability="diagnosis.denied-no-leak",
        authority_mode="observe_only",
        ui_journey="workflow-detail.remediate.diagnosis",
        expected_outcome=OUTCOME_DENIED,
        thresholds=("evidenceDenialNoLeakRate",),
        required_observations=(
            *CONTEXT_DENIAL_CASES,
            *CONTEXT_NONDISCLOSURE_PROTECTIONS,
        ),
    ),
    # --- Recovery and branch repair ----------------------------------------
    RemediationMatrixRow(
        "remediation.resume.evidence-gated-success",
        owner="moonmind.remediation.recovery",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="recoveryBranchEvidence",
        action_capability="checkpoint.resume",
        verification_capability="verification.post-action",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.resume",
        thresholds=("verificationResolvedRate",),
        required_observations=("resume", "unchanged_immutable_input"),
    ),
    RemediationMatrixRow(
        "remediation.resume.unavailable-stale-mismatch",
        owner="moonmind.remediation.recovery",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="recoveryBranchEvidence",
        action_capability="checkpoint.resume",
        verification_capability="verification.resume-unavailable",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.resume",
        expected_outcome=OUTCOME_DENIED,
        thresholds=("staleAuthorityRejectionRate",),
        required_observations=RESUME_AUTHORITY_CASES,
    ),
    RemediationMatrixRow(
        "remediation.branch.corrected-instruction-repair",
        owner="moonmind.remediation.recovery",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="recoveryBranchEvidence",
        action_capability="checkpoint.branch",
        verification_capability="verification.post-action",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.branch",
        thresholds=("verificationResolvedRate",),
        required_observations=(
            "branch_created",
            "new_semantic_step_execution",
            "fresh_stock_session",
        ),
    ),
    RemediationMatrixRow(
        "remediation.branch.changed-choices-require-branch",
        owner="moonmind.remediation.recovery",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="recoveryBranchEvidence",
        action_capability="checkpoint.branch",
        verification_capability="verification.branch-not-input-mutation",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.branch",
        thresholds=("immutableInputPreservedRate",),
        required_observations=BRANCH_CHANGED_CHOICES,
    ),
    RemediationMatrixRow(
        "remediation.repair.cumulative-multi-attempt",
        owner="moonmind.remediation.recovery",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="recoveryBranchEvidence",
        action_capability="checkpoint.branch",
        verification_capability="verification.cumulative-progress",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.branch",
        thresholds=("cumulativeProgressPreservedRate",),
        required_observations=("multiple_attempts", "accepted_workspace_progress"),
    ),
    RemediationMatrixRow(
        "remediation.repair.no-progress-exhaustion",
        owner="moonmind.remediation.recovery",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="recoveryBranchEvidence",
        action_capability="checkpoint.branch",
        verification_capability="verification.bounded-escalation",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.branch",
        expected_outcome=OUTCOME_DENIED,
        thresholds=("cumulativeAttemptExhaustionRate",),
    ),
    # --- Actions, approvals, and conflicts ---------------------------------
    RemediationMatrixRow(
        "remediation.action.low-medium-risk-allowed",
        owner="moonmind.remediation.actions",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="actionApprovalEvidence",
        action_capability="action.low-medium-risk",
        verification_capability="verification.post-action",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.action",
        thresholds=("actionDeliveryRate", "verificationResolvedRate"),
        required_observations=ACTION_RISK_CASES,
    ),
    RemediationMatrixRow(
        "remediation.action.approval-gated-approved",
        owner="moonmind.remediation.actions",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="actionApprovalEvidence",
        action_capability="action.approval-gated",
        verification_capability="verification.post-action",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.approval",
        thresholds=("approvalGrantRate", "verificationResolvedRate"),
    ),
    RemediationMatrixRow(
        "remediation.approval.denied-expired-consumed-unauthorized-stale",
        owner="moonmind.remediation.actions",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="actionApprovalEvidence",
        action_capability="action.approval-gated",
        verification_capability="verification.approval-rejected",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.approval",
        expected_outcome=OUTCOME_DENIED,
        thresholds=("approvalRejectionRate",),
        required_observations=(
            "denied",
            "expired",
            "consumed",
            "unauthorized",
            "stale_state",
        ),
    ),
    RemediationMatrixRow(
        "remediation.action.high-risk-stronger-authority",
        owner="moonmind.remediation.actions",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="actionApprovalEvidence",
        action_capability="action.high-risk",
        verification_capability="verification.post-action",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.approval",
        thresholds=("highRiskReviewerAuthorityRate",),
    ),
    RemediationMatrixRow(
        "remediation.staleness.generation-rejected",
        owner="moonmind.remediation.actions",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="actionApprovalEvidence",
        action_capability="action.staleness-guard",
        verification_capability="verification.stale-state-rejected",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.action",
        expected_outcome=OUTCOME_DENIED,
        thresholds=("staleAuthorityRejectionRate",),
        required_observations=(
            "target",
            "run",
            "checkpoint",
            "session",
            "host",
            "lease",
            "credential_generation",
            "policy",
        ),
    ),
    RemediationMatrixRow(
        "remediation.lock.mutation-conflict-diagnosis-parallelism",
        owner="moonmind.remediation.actions",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="actionApprovalEvidence",
        action_capability="action.mutation-lock",
        verification_capability="verification.lock-conflict",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.action",
        thresholds=("mutationLockConflictRate",),
        required_observations=("mutation_conflict", "diagnosis_parallel"),
    ),
    RemediationMatrixRow(
        "remediation.idempotency.duplicate-suppression",
        owner="moonmind.remediation.actions",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="actionApprovalEvidence",
        action_capability="action.idempotency",
        verification_capability="verification.duplicate-suppressed",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.action",
        thresholds=("duplicateSuppressionRate",),
    ),
    RemediationMatrixRow(
        "remediation.session.interrupt-clear-cancel-terminate-restart",
        owner="moonmind.remediation.actions",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="actionApprovalEvidence",
        action_capability="session.control",
        verification_capability="verification.post-action",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.session-control",
        thresholds=("sessionControlDeliveryRate",),
        required_observations=(
            "interrupt",
            "clear",
            "cancel",
            "terminate",
            "restart",
        ),
    ),
    RemediationMatrixRow(
        "remediation.lease.provider-profile-host-reconciliation",
        owner="moonmind.remediation.actions",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="actionApprovalEvidence",
        action_capability="lease.reconcile",
        verification_capability="verification.lease-reconciled",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.action",
        thresholds=("leaseReconciliationRate",),
    ),
    RemediationMatrixRow(
        "remediation.helper.container-restart-reap-linkage",
        owner="moonmind.remediation.actions",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="actionApprovalEvidence",
        action_capability="helper.restart-reap",
        verification_capability="verification.target-linkage",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.action",
        thresholds=("helperReapLinkageRate",),
        required_observations=("restart", "reap", "target_linkage"),
    ),
    RemediationMatrixRow(
        "remediation.cleanup.targeted-janitor-verification",
        owner="moonmind.remediation.actions",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="actionApprovalEvidence",
        action_capability="cleanup.targeted",
        verification_capability="verification.janitor",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.cleanup",
        thresholds=("cleanupJanitorRate",),
        required_observations=("targeted_cleanup", "janitor"),
    ),
    # --- Verification and prevention ---------------------------------------
    RemediationMatrixRow(
        "remediation.verify.action-delivered-target-resolved",
        owner="moonmind.remediation.verification",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="verificationPreventionEvidence",
        action_capability="action.low-medium-risk",
        verification_capability="verification.resolved",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.verification",
        thresholds=("verificationResolvedRate",),
    ),
    RemediationMatrixRow(
        "remediation.verify.action-delivered-no-change",
        owner="moonmind.remediation.verification",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="verificationPreventionEvidence",
        action_capability="action.low-medium-risk",
        verification_capability="verification.no-change",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.verification",
        thresholds=("verificationNoChangeRate",),
    ),
    RemediationMatrixRow(
        "remediation.verify.still-failed-regressed-unavailable",
        owner="moonmind.remediation.verification",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="verificationPreventionEvidence",
        action_capability="action.low-medium-risk",
        verification_capability="verification.non-resolved-outcomes",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.verification",
        thresholds=("verificationNonResolvedRate",),
        required_observations=(
            "still_failed",
            "regressed",
            "evidence_unavailable",
            "verification_failed",
        ),
    ),
    RemediationMatrixRow(
        "remediation.prevention.repair-fail-then-prevention-pr",
        owner="moonmind.remediation.verification",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="verificationPreventionEvidence",
        action_capability="prevention.publish-pr",
        verification_capability="verification.prevention-pr",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.prevention",
        thresholds=("preventionPrPublishRate",),
        required_observations=("immediate_repair_failed", "reviewable_prevention_pr"),
    ),
    RemediationMatrixRow(
        "remediation.prevention.repair-success-separate-analysis",
        owner="moonmind.remediation.verification",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="verificationPreventionEvidence",
        action_capability="prevention.analysis",
        verification_capability="verification.prevention-separate",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.prevention",
        thresholds=("preventionSeparationRate",),
        required_observations=("immediate_repair_succeeded", "separate_analysis"),
    ),
    RemediationMatrixRow(
        "remediation.prevention.pr-verification-failure-not-relabeled",
        owner="moonmind.remediation.verification",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="verificationPreventionEvidence",
        action_capability="prevention.publish-pr",
        verification_capability="verification.no-relabel-on-failure",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.prevention",
        expected_outcome=OUTCOME_DENIED,
        thresholds=("preventionRelabelPreventionRate",),
        required_observations=("pr_verification_failed", "target_not_relabeled"),
    ),
    # --- Reliability and security ------------------------------------------
    RemediationMatrixRow(
        "remediation.reliability.cancellation-each-phase",
        owner="moonmind.remediation.reliability",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="reliabilitySecurityEvidence",
        action_capability="reliability.cancellation",
        verification_capability="verification.canceled",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.cancellation",
        thresholds=("cancellationHonoredRate",),
        required_observations=(
            "diagnosis",
            "approval_wait",
            "action",
            "branch_execution",
            "verification",
            "publication",
            "cleanup",
        ),
    ),
    RemediationMatrixRow(
        "remediation.reliability.worker-restart-temporal-replay",
        owner="moonmind.remediation.reliability",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="reliabilitySecurityEvidence",
        action_capability="reliability.replay",
        verification_capability="verification.replay-safe",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.action",
        thresholds=("replaySafeRate",),
        required_observations=(
            "diagnosis",
            "approval_wait",
            "action",
            "branch_execution",
            "verification",
            "publication",
            "cleanup",
        ),
    ),
    RemediationMatrixRow(
        "remediation.security.duplicate-prevention-idempotency",
        owner="moonmind.remediation.reliability",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="reliabilitySecurityEvidence",
        action_capability="security.duplicate-prevention",
        verification_capability="verification.no-duplicate-effect",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.action",
        thresholds=("duplicateSuppressionRate",),
        required_observations=(
            "first_message",
            "host",
            "session",
            "branch",
            "commit",
            "pull_request",
            "action",
            "verification",
        ),
    ),
    RemediationMatrixRow(
        "remediation.host.static-lifecycle",
        owner="moonmind.remediation.reliability",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="reliabilitySecurityEvidence",
        action_capability="host.lifecycle",
        verification_capability="verification.host-reconciled",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.action",
        host_modes=("static",),
        thresholds=("hostLifecycleReconciliationRate",),
    ),
    RemediationMatrixRow(
        "remediation.host.on-demand-lifecycle",
        owner="moonmind.remediation.reliability",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="reliabilitySecurityEvidence",
        action_capability="host.lifecycle",
        verification_capability="verification.host-reconciled",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.action",
        host_modes=("on_demand",),
        thresholds=("hostLifecycleReconciliationRate",),
    ),
    RemediationMatrixRow(
        "remediation.egress.restricted-allowed",
        owner="moonmind.remediation.reliability",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="reliabilitySecurityEvidence",
        action_capability="egress.allowed",
        verification_capability="verification.egress-allowed",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.action",
        egress=EGRESS_RESTRICTED_ALLOWED,
        thresholds=("egressAllowedRate",),
    ),
    RemediationMatrixRow(
        "remediation.egress.restricted-denied",
        owner="moonmind.remediation.reliability",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="reliabilitySecurityEvidence",
        action_capability="egress.denied",
        verification_capability="verification.egress-denied",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.action",
        egress=EGRESS_RESTRICTED_DENIED,
        expected_outcome=OUTCOME_DENIED,
        thresholds=("egressDenialRate",),
    ),
    RemediationMatrixRow(
        "remediation.security.prohibited-authority-denied",
        owner="moonmind.remediation.reliability",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="reliabilitySecurityEvidence",
        action_capability="security.authority-boundary",
        verification_capability="verification.prohibited-authority-denied",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.action",
        expected_outcome=OUTCOME_DENIED,
        thresholds=("prohibitedAuthorityDenialRate",),
        required_observations=(
            "raw_host_shell",
            "docker",
            "sql",
            "storage_key",
            "filesystem_path",
            "credential",
            "secret_read",
            "redaction_bypass",
        ),
    ),
    RemediationMatrixRow(
        "remediation.cleanup.complete-provider-profile-release-last",
        owner="moonmind.remediation.reliability",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="reliabilitySecurityEvidence",
        action_capability="cleanup.complete",
        verification_capability="verification.provider-profile-release-last",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.cleanup",
        thresholds=("providerProfileReleaseLastRate",),
        required_observations=(
            "terminal_harvest",
            "targeted_cleanup",
            "janitor",
            "lock_release",
            "capacity_release",
            "provider_profile_release_last",
        ),
    ),
    # The autonomous rollout gate row. It is intentionally DENIED in v1: the
    # matrix proves the autonomous-mutation gate refuses ``admin_auto`` and never
    # grants it by publishing evidence (acceptance criterion 9).
    RemediationMatrixRow(
        "remediation.autonomous.rollout-gate-closed",
        owner="moonmind.remediation.autonomous",
        gate=GATE_AUTONOMOUS_ROLLOUT,
        evidence_kind="reliabilitySecurityEvidence",
        action_capability="autonomous.mutation",
        verification_capability="verification.autonomous-gate-closed",
        authority_mode="admin_auto",
        ui_journey="workflow-detail.remediate.action",
        expected_outcome=OUTCOME_DENIED,
        thresholds=("autonomousManualOriginRate",),
    ),
)

REQUIRED_REMEDIATION_MATRIX_ROWS = tuple(
    row.row_id for row in REMEDIATION_ROW_CATALOG
)
REMEDIATION_ROW_CATALOG_BY_ID: dict[str, RemediationMatrixRow] = {
    row.row_id: row for row in REMEDIATION_ROW_CATALOG
}


def remediation_catalog_document() -> dict[str, Any]:
    """Return the catalog as a portable, versioned machine-readable document."""

    return {
        "issue": "MoonLadderStudios/MoonMind#3626",
        "matrixVersion": REMEDIATION_MATRIX_VERSION,
        "evidenceSchema": REMEDIATION_ARTIFACT_SCHEMA_VERSION,
        "lineageFields": list(REQUIRED_REMEDIATION_LINEAGE_FIELDS),
        "retainedEvidenceChannels": list(REQUIRED_REMEDIATION_RETAINED_CHANNELS),
        "sourceRecordContract": {
            "requiredTypes": sorted(REQUIRED_REMEDIATION_SOURCE_RECORD_TYPES),
            "schemas": dict(REMEDIATION_SOURCE_RECORD_SCHEMAS),
            "lineageRefOwners": dict(REMEDIATION_LINEAGE_REF_RECORD_TYPES),
            "contentType": REMEDIATION_SOURCE_RECORD_CONTENT_TYPE,
            "maxBytes": MAX_REMEDIATION_SOURCE_RECORD_BYTES,
            "digest": "sha256",
            "freshnessSeconds": MAX_EVIDENCE_AGE_SECONDS,
        },
        "telemetryContract": {
            "schemaVersion": REMEDIATION_TELEMETRY_SCHEMA_VERSION,
            "groups": list(REQUIRED_REMEDIATION_TELEMETRY_GROUPS),
            "phaseLatencies": list(REQUIRED_REMEDIATION_PHASE_LATENCIES),
            "actionRisks": list(REMEDIATION_ACTION_RISKS),
        },
        "rows": [
            {
                "rowId": row.row_id,
                "owner": row.owner,
                "targetRuntimeProvenance": list(row.target_provenance),
                "remediationRuntimeProvenance": list(row.remediation_provenance),
                "hostModes": list(row.host_modes),
                "architectures": list(row.architectures),
                "actionCapability": row.action_capability,
                "actionRisk": row.action_risk,
                "verificationCapability": row.verification_capability,
                "authorityMode": row.authority_mode,
                "egressAuthority": row.egress,
                "uiJourney": row.ui_journey,
                "evidenceKind": row.evidence_kind,
                "evidenceSchema": REMEDIATION_ARTIFACT_SCHEMA_VERSION,
                "expectedDisposition": row.expected_outcome,
                "thresholds": {
                    threshold: {
                        "rule": "catalog_owned_typed_fact_predicate",
                        "minimumSampleCount": 1,
                    }
                    for threshold in row.thresholds
                },
                "requiredObservations": list(row.required_observations),
                "gate": row.gate,
            }
            for row in REMEDIATION_ROW_CATALOG
        ],
    }

# The rows whose passing observation is required before manual mutation may be
# claimed supported. Diagnosis rows gate diagnosis only.
_MANUAL_DIAGNOSIS_ROWS = frozenset(
    row.row_id
    for row in REMEDIATION_ROW_CATALOG
    if row.gate == GATE_MANUAL_DIAGNOSIS
)
_MANUAL_MUTATION_ROWS = frozenset(
    row.row_id
    for row in REMEDIATION_ROW_CATALOG
    if row.gate == GATE_MANUAL_MUTATION
)
_AUTONOMOUS_ROWS = frozenset(
    row.row_id
    for row in REMEDIATION_ROW_CATALOG
    if row.gate == GATE_AUTONOMOUS_ROLLOUT
)


class RemediationMatrixError(ValueError):
    """Raised when a protected artifact fails observed-evidence row binding."""


def _validate_row_secret_scan(
    secret_scan: Any,
    *,
    row_id: str,
    evidence_document_path: Path | None,
    evidence_time: datetime | None,
) -> None:
    """Bind a row to validated per-channel secret-scan evidence.

    Mirrors :func:`moonmind.omnigent.cutover._validate_row_secret_scan`. A
    self-asserted scalar is never sufficient: every required conformance
    evidence channel must record a passing scan bound to a resolvable
    ``evidenceRef``.
    """

    if not isinstance(secret_scan, Mapping):
        raise RemediationMatrixError(
            f"row {row_id!r} secret scan must carry per-channel evidence"
        )
    missing = set(REQUIRED_REMEDIATION_RETAINED_CHANNELS) - set(secret_scan)
    if missing:
        raise RemediationMatrixError(
            f"row {row_id!r} secret scan is missing channels: {sorted(missing)}"
        )
    for channel in REQUIRED_REMEDIATION_RETAINED_CHANNELS:
        result = secret_scan.get(channel)
        evidence_ref = (
            result.get("evidenceRef") if isinstance(result, Mapping) else None
        )
        if (
            not isinstance(result, Mapping)
            or result.get("status") != "passed"
            or not isinstance(evidence_ref, str)
            or not evidence_ref.strip()
            or not isinstance(result.get("sha256"), str)
            or len(result["sha256"]) != 64
            or any(
                character not in "0123456789abcdef"
                for character in result["sha256"]
            )
            or result.get("contentType") != REMEDIATION_SOURCE_RECORD_CONTENT_TYPE
            or result.get("schemaVersion")
            != "moonmind.retained-evidence-secret-scan/v1"
            or not isinstance(result.get("sizeBytes"), int)
            or isinstance(result.get("sizeBytes"), bool)
            or result["sizeBytes"] < 2
            or result["sizeBytes"] > MAX_REMEDIATION_SOURCE_RECORD_BYTES
            or not isinstance(result.get("generatedAt"), str)
        ):
            raise RemediationMatrixError(
                f"row {row_id!r} secret scan channel {channel!r} lacks passing "
                "evidence"
            )
        try:
            generated = datetime.fromisoformat(
                result["generatedAt"].replace("Z", "+00:00")
            )
            if generated.tzinfo is None:
                raise ValueError
        except ValueError as exc:
            raise RemediationMatrixError(
                f"row {row_id!r} secret scan channel {channel!r} has invalid freshness"
            ) from exc
        if evidence_time is not None:
            age = (evidence_time - generated).total_seconds()
            if age < 0 or age > MAX_EVIDENCE_AGE_SECONDS:
                raise RemediationMatrixError(
                    f"row {row_id!r} secret scan channel {channel!r} is stale"
                )
        if evidence_document_path is None:
            continue
        base = evidence_document_path.resolve().parent
        try:
            scan_path = _evidence_path(evidence_ref)
            if not scan_path.is_absolute():
                scan_path = base / scan_path
            scan_path = scan_path.resolve()
            if scan_path != base and base not in scan_path.parents:
                raise ValueError("secret scan escaped evidence root")
            content = scan_path.read_bytes()
        except (OSError, ValueError) as exc:
            raise RemediationMatrixError(
                f"row {row_id!r} secret scan channel {channel!r} is unreadable"
            ) from exc
        if (
            len(content) != result["sizeBytes"]
            or hashlib.sha256(content).hexdigest() != result["sha256"]
        ):
            raise RemediationMatrixError(
                f"row {row_id!r} secret scan channel {channel!r} digest mismatch"
            )
        try:
            scan_payload = json.loads(content)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RemediationMatrixError(
                f"row {row_id!r} secret scan channel {channel!r} is not JSON"
            ) from exc
        if (
            not isinstance(scan_payload, Mapping)
            or scan_payload.get("schemaVersion") != result["schemaVersion"]
            or scan_payload.get("status") != "passed"
            or scan_payload.get("secretFindings") != 0
            or scan_payload.get("prohibitedAuthorityFindings") != 0
        ):
            raise RemediationMatrixError(
                f"row {row_id!r} secret scan channel {channel!r} did not pass"
            )


def _validate_ui_journey(ui_journey: Any, *, row: RemediationMatrixRow) -> None:
    """Fail closed unless the row records the normal browser product path.

    Section 2 requires the journey to fail on hidden submission, manual host or
    session ids, alternate wire contracts, unvalidated policy/profile fields,
    direct-Codex fallback, or log-derived authority. Each is a prohibited marker
    that must be absent or falsey; each required assertion must be exactly
    ``True``.
    """

    if not isinstance(ui_journey, Mapping):
        raise RemediationMatrixError(
            f"row {row.row_id!r} lacks a normal-product-path UI journey"
        )
    if ui_journey.get("journey") != row.ui_journey:
        raise RemediationMatrixError(
            f"row {row.row_id!r} UI journey is not the required {row.ui_journey!r}"
        )
    for assertion in REQUIRED_UI_JOURNEY_ASSERTIONS:
        if (
            assertion == "normalCreateRequest"
            and row.row_id == "remediation.autonomous.rollout-gate-closed"
        ):
            if ui_journey.get("autonomousAdmissionDenied") is not True:
                raise RemediationMatrixError(
                    f"row {row.row_id!r} did not observe autonomous admission denial"
                )
            continue
        if ui_journey.get(assertion) is not True:
            raise RemediationMatrixError(
                f"row {row.row_id!r} UI journey is missing assertion {assertion!r}"
            )
    for marker in PROHIBITED_UI_JOURNEY_MARKERS:
        if ui_journey.get(marker) is not False:
            raise RemediationMatrixError(
                f"row {row.row_id!r} UI journey did not explicitly exclude "
                f"prohibited authority {marker!r}"
            )


def _validate_delivery_and_repair(entry: Mapping[str, Any], *, row: RemediationMatrixRow) -> None:
    """Enforce action-delivery / target-repair separation (AC5).

    A mutating row must carry ``actionDelivery.status`` and, independently,
    ``repairVerification.outcome``. Reporting the same field twice, or collapsing
    delivery into repair, fails closed.
    """

    if not row.is_mutation:
        return
    delivery = entry.get("actionDelivery")
    repair = entry.get("repairVerification")
    if not isinstance(delivery, Mapping) or not isinstance(repair, Mapping):
        raise RemediationMatrixError(
            f"row {row.row_id!r} must record action delivery and repair "
            "verification as separate evidence"
        )
    delivery_status = delivery.get("status")
    repair_outcome = repair.get("outcome")
    # Delivery and repair vocabularies are disjoint, so a repair outcome can
    # never masquerade as a delivery status (or vice versa): the field a value
    # lives in is itself evidence of what it describes (AC5).
    if delivery_status not in REMEDIATION_DELIVERY_STATUSES:
        raise RemediationMatrixError(
            f"row {row.row_id!r} action delivery status is unrecognized"
        )
    if repair_outcome not in REMEDIATION_REPAIR_OUTCOMES:
        raise RemediationMatrixError(
            f"row {row.row_id!r} repair verification outcome is unrecognized"
        )
    if delivery is repair:
        raise RemediationMatrixError(
            f"row {row.row_id!r} collapses action delivery into repair "
            "verification"
        )


def _validate_thresholds(thresholds: Any, *, row: RemediationMatrixRow) -> None:
    """Every named row threshold must be present and observed within limits."""

    if not row.thresholds:
        return
    if not isinstance(thresholds, Mapping):
        raise RemediationMatrixError(
            f"row {row.row_id!r} lacks observed threshold results"
        )
    for key in row.thresholds:
        result = thresholds.get(key)
        passed = result.get("passed") if isinstance(result, Mapping) else None
        total = result.get("total") if isinstance(result, Mapping) else None
        if (
            not isinstance(result, Mapping)
            or result.get("within") is not True
            or not isinstance(passed, int)
            or isinstance(passed, bool)
            or not isinstance(total, int)
            or isinstance(total, bool)
            or total < 1
            or passed != total
        ):
            raise RemediationMatrixError(
                f"row {row.row_id!r} threshold {key!r} was not observed within "
                "limits"
            )


def _validate_required_observations(
    observations: Any, *, row: RemediationMatrixRow
) -> None:
    """Require every issue-named sub-scenario to have an observed outcome."""

    if not row.required_observations:
        return
    if not isinstance(observations, Mapping):
        raise RemediationMatrixError(
            f"row {row.row_id!r} lacks required scenario observations"
        )
    missing = [
        observation
        for observation in row.required_observations
        if observations.get(observation) is not True
    ]
    if missing:
        raise RemediationMatrixError(
            f"row {row.row_id!r} lacks observed scenarios: {missing}"
        )


def _validate_timings(timings: Any, *, row: RemediationMatrixRow) -> None:
    if not isinstance(timings, Mapping):
        raise RemediationMatrixError(f"row {row.row_id!r} lacks observed timings")
    duration_ms = timings.get("durationMs")
    phase_latencies = timings.get("phaseLatenciesMs")
    if (
        not isinstance(duration_ms, int)
        or isinstance(duration_ms, bool)
        or duration_ms < 0
        or not isinstance(phase_latencies, Mapping)
        or not phase_latencies
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in phase_latencies.values()
        )
    ):
        raise RemediationMatrixError(f"row {row.row_id!r} timings are malformed")
    try:
        started = datetime.fromisoformat(
            str(timings.get("startedAt")).replace("Z", "+00:00")
        )
        completed = datetime.fromisoformat(
            str(timings.get("completedAt")).replace("Z", "+00:00")
        )
        if started.tzinfo is None or completed.tzinfo is None or completed < started:
            raise ValueError
    except ValueError as exc:
        raise RemediationMatrixError(
            f"row {row.row_id!r} timing timestamps are malformed"
        ) from exc


def _required_source_mapping(
    source: Mapping[str, Any], key: str, *, row_id: str, record_type: str
) -> Mapping[str, Any]:
    value = source.get(key)
    if not isinstance(value, Mapping):
        raise RemediationMatrixError(
            f"row {row_id!r} {record_type!r} source record lacks {key!r}"
        )
    return value


def _required_source_string(
    source: Mapping[str, Any], key: str, *, row_id: str, record_type: str
) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RemediationMatrixError(
            f"row {row_id!r} {record_type!r} source record lacks {key!r}"
        )
    return value


def _required_source_bool(
    source: Mapping[str, Any], key: str, *, row_id: str, record_type: str
) -> bool:
    value = source.get(key)
    if not isinstance(value, bool):
        raise RemediationMatrixError(
            f"row {row_id!r} {record_type!r} source record lacks boolean {key!r}"
        )
    return value


def _required_nonnegative_int(
    source: Mapping[str, Any], key: str, *, row_id: str, record_type: str
) -> int:
    value = source.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RemediationMatrixError(
            f"row {row_id!r} {record_type!r} source record has invalid {key!r}"
        )
    return value


def _required_source_string_set(
    source: Mapping[str, Any],
    key: str,
    *,
    allowed: tuple[str, ...],
    row_id: str,
    record_type: str,
) -> frozenset[str]:
    """Return one exact, duplicate-free typed observation set."""

    value = source.get(key)
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(value) != len(set(value))
        or not set(value).issubset(allowed)
    ):
        raise RemediationMatrixError(
            f"row {row_id!r} {record_type!r} source record has invalid {key!r}"
        )
    return frozenset(value)


def _derive_catalog_semantics(
    *, row: RemediationMatrixRow, facts: Mapping[str, Any]
) -> tuple[str, dict[str, bool], dict[str, dict[str, int]]]:
    """Derive row qualification from catalog-owned, side-effect-owner facts.

    Every threshold is a fixed predicate over typed records.  Summary records
    cannot choose the predicate, disposition, observations, numerator, or
    denominator.  Each v1 row represents one required live case, so a named
    threshold has an objective denominator of one; composite cases are also
    expanded into independently derived ``required_observations``.
    """

    row_id = row.row_id
    observations: dict[str, bool] = {}
    if row_id == "remediation.evidence.active-snapshot-follow-reconnect":
        observations = {
            name: name in facts["contextFollowPhases"]
            for name in CONTEXT_FOLLOW_PHASES
        }
    elif row_id == "remediation.evidence.missing-unauthorized-denied":
        observations = {
            **{
                name: name in facts["contextDenialCases"]
                for name in CONTEXT_DENIAL_CASES
            },
            **{
                name: name in facts["contextNondisclosureProtections"]
                for name in CONTEXT_NONDISCLOSURE_PROTECTIONS
            },
        }
    elif row_id == "remediation.resume.evidence-gated-success":
        observations = {
            "resume": facts["resumeOutcome"] == "resumed",
            "unchanged_immutable_input": facts["immutableInputPreserved"],
        }
    elif row_id == "remediation.resume.unavailable-stale-mismatch":
        observations = {
            name: name in facts["resumeAuthorityCases"]
            for name in RESUME_AUTHORITY_CASES
        }
    elif row_id == "remediation.branch.corrected-instruction-repair":
        observations = {
            "branch_created": facts["branchCreated"],
            "new_semantic_step_execution": facts["newSemanticStepExecution"],
            "fresh_stock_session": facts["freshStockSession"],
        }
    elif row_id == "remediation.branch.changed-choices-require-branch":
        observations = {
            name: name in facts["branchChangedChoices"]
            for name in BRANCH_CHANGED_CHOICES
        }
    elif row_id == "remediation.repair.cumulative-multi-attempt":
        observations = {
            "multiple_attempts": facts["attemptCount"] >= 2,
            "accepted_workspace_progress": facts[
                "acceptedWorkspaceProgressPreserved"
            ],
        }
    elif row_id == "remediation.action.low-medium-risk-allowed":
        observations = {
            name: name in facts["actionRiskCasesDelivered"]
            for name in ACTION_RISK_CASES
        }
    elif row_id == "remediation.approval.denied-expired-consumed-unauthorized-stale":
        observations = {
            name: ("stale" if name == "stale_state" else name)
            in facts["approvalOutcomesObserved"]
            for name in row.required_observations
        }
    elif row_id == "remediation.staleness.generation-rejected":
        observations = {
            name: name in facts["staleAuthorityRejections"]
            for name in STALE_AUTHORITY_CASES
        }
    elif row_id == "remediation.lock.mutation-conflict-diagnosis-parallelism":
        observations = {
            "mutation_conflict": facts["lockConflict"],
            "diagnosis_parallel": facts["diagnosisParallelAllowed"],
        }
    elif row_id == "remediation.session.interrupt-clear-cancel-terminate-restart":
        observations = {
            name: name in facts["sessionControlsDelivered"]
            for name in SESSION_CONTROL_CASES
        }
    elif row_id == "remediation.helper.container-restart-reap-linkage":
        observations = {
            "restart": facts["helperRestarted"],
            "reap": facts["helperReaped"],
            "target_linkage": facts["helperTargetLinked"],
        }
    elif row_id == "remediation.cleanup.targeted-janitor-verification":
        observations = {
            "targeted_cleanup": facts["targetedCleanup"],
            "janitor": facts["janitorVerified"],
        }
    elif row_id == "remediation.verify.still-failed-regressed-unavailable":
        observations = {
            name: name in facts["verificationOutcomesObserved"]
            for name in NON_RESOLVED_VERIFICATION_CASES
        }
    elif row_id == "remediation.prevention.repair-fail-then-prevention-pr":
        observations = {
            "immediate_repair_failed": facts["immediateRepairOutcome"]
            in {"still_failed", "regressed", "verification_failed"},
            "reviewable_prevention_pr": facts["preventionPrOutcome"]
            == "published_reviewable",
        }
    elif row_id == "remediation.prevention.repair-success-separate-analysis":
        observations = {
            "immediate_repair_succeeded": facts["immediateRepairOutcome"]
            == "verified_resolved",
            "separate_analysis": facts["preventionAnalysisSeparate"],
        }
    elif row_id == "remediation.prevention.pr-verification-failure-not-relabeled":
        observations = {
            "pr_verification_failed": facts["preventionPrOutcome"]
            == "verification_failed",
            "target_not_relabeled": not facts["targetRelabeledRepaired"],
        }
    elif row_id == "remediation.reliability.cancellation-each-phase":
        observations = {
            name: name in facts["cancellationPhases"]
            for name in REMEDIATION_DURABLE_PHASES
        }
    elif row_id == "remediation.reliability.worker-restart-temporal-replay":
        observations = {
            name: name in facts["replayPhases"]
            for name in REMEDIATION_DURABLE_PHASES
        }
    elif row_id == "remediation.security.duplicate-prevention-idempotency":
        observations = {
            name: name in facts["duplicateEffectsSuppressed"]
            for name in DUPLICATE_EFFECT_CASES
        }
    elif row_id == "remediation.security.prohibited-authority-denied":
        observations = {
            name: name in facts["prohibitedAuthoritiesDenied"]
            for name in PROHIBITED_AUTHORITY_CASES
        }
    elif row_id == "remediation.cleanup.complete-provider-profile-release-last":
        observations = {
            "terminal_harvest": facts["terminalHarvested"],
            "targeted_cleanup": facts["targetedCleanup"],
            "janitor": facts["janitorVerified"],
            "lock_release": facts["lockReleased"],
            "capacity_release": facts["capacityReleased"],
            "provider_profile_release_last": facts["providerProfileReleasedLast"],
        }

    if set(observations) != set(row.required_observations):
        raise RemediationMatrixError(
            f"row {row_id!r} catalog lacks an authoritative observation predicate"
        )

    all_observations = all(observations.values())
    delivered = (
        facts["deliveryStatus"] == "delivered"
        and facts["actionOutcome"] in {"delivered", "no_op"}
    )
    denied_delivery = (
        facts["deliveryStatus"] in {"denied", "not_delivered"}
        and facts["actionOutcome"] in {"denied", "failure"}
    )
    cleanup_complete = (
        facts["cleanupOutcome"] == "completed"
        and facts["remainingLiveResources"] == 0
    )
    resolved = (
        facts["repairOutcome"] == "verified_resolved"
        and not facts["unverifiedMutation"]
    )

    approval_denial_rows = {
        "remediation.resume.unavailable-stale-mismatch",
        "remediation.approval.denied-expired-consumed-unauthorized-stale",
        "remediation.staleness.generation-rejected",
        "remediation.egress.restricted-denied",
        "remediation.security.prohibited-authority-denied",
    }
    expected_approval_requested = (
        row.is_mutation and row.authority_mode == "approval_gated"
    )
    expected_approval_outcome = (
        "not_required"
        if not row.is_mutation or row.authority_mode == "admin_auto"
        else "denied"
        if row_id in approval_denial_rows
        else "approved"
    )
    expected_delivery_status = "delivered"
    expected_repair_outcome = "verified_resolved"
    if not row.is_mutation:
        expected_delivery_status, expected_repair_outcome = (
            "not_applicable",
            "canceled",
        )
    elif row_id in {
        "remediation.idempotency.duplicate-suppression",
        "remediation.security.duplicate-prevention-idempotency",
    }:
        expected_delivery_status, expected_repair_outcome = (
            "suppressed_idempotent",
            "verified_no_change",
        )
    elif row_id == "remediation.lock.mutation-conflict-diagnosis-parallelism":
        expected_delivery_status, expected_repair_outcome = (
            "denied",
            "approval_required",
        )
    elif row_id == "remediation.verify.action-delivered-no-change":
        expected_repair_outcome = "verified_no_change"
    elif row_id in {
        "remediation.verify.still-failed-regressed-unavailable",
        "remediation.prevention.repair-fail-then-prevention-pr",
    }:
        expected_repair_outcome = "still_failed"
    elif row_id == "remediation.prevention.pr-verification-failure-not-relabeled":
        expected_repair_outcome = "verification_failed"
    elif row_id == "remediation.reliability.cancellation-each-phase":
        expected_delivery_status, expected_repair_outcome = (
            "not_delivered",
            "canceled",
        )
    elif row_id == "remediation.repair.no-progress-exhaustion":
        expected_delivery_status, expected_repair_outcome = (
            "not_delivered",
            "still_failed",
        )
    elif row.expected_outcome == OUTCOME_DENIED:
        expected_delivery_status, expected_repair_outcome = (
            "denied",
            "approval_required",
        )
    authority_handoff_valid = (
        facts["approvalRequested"] == expected_approval_requested
        and facts["approvalOutcome"] == expected_approval_outcome
        and facts["actionRequested"]
        == (row.is_mutation and row.authority_mode != "admin_auto")
        and facts["remediationCreated"] == (row.authority_mode != "admin_auto")
    )
    action_verification_handoff_valid = (
        facts["deliveryStatus"] == expected_delivery_status
        and facts["repairOutcome"] == expected_repair_outcome
    )

    threshold_predicates = {
        "contextBuildSuccessRate": (
            facts["remediationCreated"]
            and facts["contextBuildOutcome"] == "success"
            and facts["evidenceOutcome"] == "available"
            and not facts["actionRequested"]
            and facts["deliveryStatus"] == "not_applicable"
        ),
        "evidenceDegradationRate": (
            facts["contextBuildOutcome"] == "degraded"
            and facts["evidenceOutcome"] == "degraded"
            and not facts["actionRequested"]
        ),
        "reconnectCursorRecoveryRate": (
            all_observations
            and facts["contextBuildOutcome"] == "success"
            and facts["evidenceOutcome"] == "available"
        ),
        "evidenceDenialNoLeakRate": (
            all_observations
            and facts["contextBuildOutcome"] == "denied"
            and facts["evidenceOutcome"] == "denied"
            and not facts["actionRequested"]
            and facts["secretFindings"] == 0
            and facts["prohibitedAuthorityFindings"] == 0
        ),
        "verificationResolvedRate": delivered and resolved,
        "staleAuthorityRejectionRate": (
            all_observations
            and facts["approvalOutcome"] == "denied"
            and denied_delivery
            and facts["repairOutcome"] == "approval_required"
        ),
        "immutableInputPreservedRate": (
            all_observations
            and facts["branchCreated"]
            and facts["immutableInputPreserved"]
            and delivered
        ),
        "cumulativeProgressPreservedRate": (
            all_observations
            and facts["branchCreated"]
            and delivered
            and resolved
        ),
        "cumulativeAttemptExhaustionRate": (
            facts["attemptCount"] >= 2
            and facts["noProgressEscalated"]
            and facts["repeatedFailure"]
            and facts["attemptsExhausted"]
            and facts["deliveryStatus"] == "not_delivered"
            and facts["repairOutcome"] == "still_failed"
        ),
        "actionDeliveryRate": (
            all_observations
            and facts["approvalOutcome"] == "approved"
            and delivered
        ),
        "approvalGrantRate": (
            facts["approvalRequested"]
            and facts["approvalOutcome"] == "approved"
            and delivered
        ),
        "approvalRejectionRate": (
            all_observations
            and facts["approvalRequested"]
            and facts["approvalOutcome"] == "denied"
            and denied_delivery
            and facts["repairOutcome"] == "approval_required"
        ),
        "highRiskReviewerAuthorityRate": (
            facts["strongReviewerAuthority"]
            and facts["approvalOutcome"] == "approved"
            and delivered
            and resolved
        ),
        "mutationLockConflictRate": (
            all_observations
            and facts["deliveryStatus"] == "denied"
            and facts["actionOutcome"] == "denied"
            and facts["repairOutcome"] == "approval_required"
        ),
        "duplicateSuppressionRate": (
            all_observations
            and facts["duplicateSuppressed"]
            and facts["duplicateSuppressionCount"] >= 1
            and facts["firstMessageCount"] == 1
            and facts["deliveryStatus"] == "suppressed_idempotent"
            and facts["actionOutcome"] == "no_op"
            and facts["repairOutcome"] == "verified_no_change"
        ),
        "sessionControlDeliveryRate": all_observations and delivered and resolved,
        "leaseReconciliationRate": (
            facts["leaseHostReconciled"] and delivered and resolved
        ),
        "helperReapLinkageRate": all_observations and delivered and cleanup_complete,
        "cleanupJanitorRate": all_observations and cleanup_complete,
        "verificationNoChangeRate": (
            delivered
            and facts["repairOutcome"] == "verified_no_change"
            and not facts["unverifiedMutation"]
        ),
        "verificationNonResolvedRate": (
            all_observations
            and delivered
            and facts["repairOutcome"] in NON_RESOLVED_VERIFICATION_CASES
        ),
        "preventionPrPublishRate": (
            all_observations
            and delivered
            and facts["preventionOutcome"] == "published_pr"
        ),
        "preventionSeparationRate": (
            all_observations
            and resolved
            and facts["preventionOutcome"] == "analyzed_separately"
        ),
        "preventionRelabelPreventionRate": (
            all_observations
            and facts["repairOutcome"] == "verification_failed"
        ),
        "cancellationHonoredRate": (
            all_observations
            and facts["operatorCancelled"]
            and facts["repairOutcome"] == "canceled"
            and cleanup_complete
        ),
        "replaySafeRate": (
            all_observations
            and facts["replayCount"] >= len(REMEDIATION_DURABLE_PHASES)
            and facts["replayOutcome"] == "passed"
            and cleanup_complete
        ),
        "hostLifecycleReconciliationRate": (
            facts["hostLifecycleOutcome"] == "reconciled" and cleanup_complete
        ),
        "egressAllowedRate": (
            facts["egressDecision"] == "allowed"
            and facts["egressAttestationOutcome"] == "passed"
            and delivered
        ),
        "egressDenialRate": (
            facts["egressDecision"] == "denied"
            and facts["egressAttestationOutcome"] == "passed"
            and denied_delivery
        ),
        "prohibitedAuthorityDenialRate": (
            all_observations
            and denied_delivery
            and facts["secretFindings"] == 0
            and facts["prohibitedAuthorityFindings"] == 0
        ),
        "providerProfileReleaseLastRate": all_observations and cleanup_complete,
        "autonomousManualOriginRate": (
            facts["origin"] == "autonomous"
            and not facts["remediationCreated"]
            and not facts["actionRequested"]
            and facts["approvalOutcome"] == "not_required"
            and facts["deliveryStatus"] == "denied"
        ),
    }
    missing_thresholds = set(row.thresholds) - set(threshold_predicates)
    if missing_thresholds:
        raise RemediationMatrixError(
            f"row {row_id!r} catalog lacks threshold predicates: "
            f"{sorted(missing_thresholds)}"
        )
    threshold_predicates = {
        threshold: predicate
        and authority_handoff_valid
        and action_verification_handoff_valid
        for threshold, predicate in threshold_predicates.items()
    }
    samples = {
        threshold: {
            "passed": int(threshold_predicates[threshold]),
            "total": 1,
        }
        for threshold in row.thresholds
    }
    qualified = all_observations and all(
        sample["passed"] == sample["total"] for sample in samples.values()
    )
    observed_disposition = (
        row.expected_outcome
        if qualified
        else OUTCOME_DENIED
        if row.expected_outcome == OUTCOME_PASSED
        else OUTCOME_PASSED
    )
    return observed_disposition, observations, samples


def derive_remediation_observation_from_source_records(
    *,
    row: RemediationMatrixRow,
    manifest_by_type: Mapping[str, Mapping[str, Any]],
    sources: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Derive one row exclusively from typed, cross-bound source records.

    ``scenarioObservation`` remains a useful producer summary, but it is not an
    authority: every claim in it is compared with facts owned by the browser,
    workflow, approval, action, verification, publication, egress, cleanup, and
    history records below. ``sideEffectAudit`` is checked as a second summary
    and cannot supply a disposition, observation, or threshold count.
    """

    row_id = row.row_id
    identities: list[Mapping[str, Any]] = []
    for record_type in sorted(REQUIRED_REMEDIATION_SOURCE_RECORD_TYPES):
        source = sources[record_type]
        if source.get("row") != row_id:
            raise RemediationMatrixError(
                f"row {row_id!r} {record_type!r} source record has mismatched row identity"
            )
        identity = _required_source_mapping(
            source, "identity", row_id=row_id, record_type=record_type
        )
        missing_identity = [
            field
            for field in REMEDIATION_EVIDENCE_IDENTITY_FIELDS
            if not isinstance(identity.get(field), str) or not identity[field].strip()
        ]
        if missing_identity:
            raise RemediationMatrixError(
                f"row {row_id!r} {record_type!r} source record lacks identities: "
                f"{missing_identity}"
            )
        identities.append(identity)
    identity = dict(identities[0])
    if any(dict(candidate) != identity for candidate in identities[1:]):
        raise RemediationMatrixError(
            f"row {row_id!r} source records have mismatched workflow/run/session identities"
        )

    browser = sources["browserTrace"]
    authored = sources["authoredRequest"]
    immutable_input = sources["immutableInputSnapshot"]
    workflow = sources["workflowLineage"]
    context = sources["contextEvidence"]
    profile = sources["profilePolicyAuthority"]
    egress = sources["egressAttestation"]
    approval = sources["approvalDecision"]
    action = sources["actionResult"]
    verification = sources["verificationResult"]
    publication = sources["publicationOutcome"]
    cleanup = sources["cleanupOutcome"]
    temporal = sources["temporalHistory"]
    audit = sources["sideEffectAudit"]
    retained_scan = sources["retainedEvidenceScan"]
    scenario = sources["scenarioObservation"]

    ui_journey = dict(
        _required_source_mapping(
            browser, "uiJourney", row_id=row_id, record_type="browserTrace"
        )
    )
    host_mode = _required_source_string(
        browser, "hostMode", row_id=row_id, record_type="browserTrace"
    )
    architecture = _required_source_string(
        browser, "architecture", row_id=row_id, record_type="browserTrace"
    )
    remediation_created = _required_source_bool(
        browser, "remediationCreated", row_id=row_id, record_type="browserTrace"
    )

    authority_mode = _required_source_string(
        authored, "authorityMode", row_id=row_id, record_type="authoredRequest"
    )
    action_capability = _required_source_string(
        authored, "actionCapability", row_id=row_id, record_type="authoredRequest"
    )
    verification_capability = _required_source_string(
        authored,
        "verificationCapability",
        row_id=row_id,
        record_type="authoredRequest",
    )
    origin = _required_source_string(
        authored, "origin", row_id=row_id, record_type="authoredRequest"
    )
    action_risk = _required_source_string(
        authored, "actionRisk", row_id=row_id, record_type="authoredRequest"
    )
    expected_origin = "autonomous" if row.authority_mode == "admin_auto" else "manual"
    if (
        authority_mode != row.authority_mode
        or action_capability != row.action_capability
        or verification_capability != row.verification_capability
        or origin != expected_origin
        or action_risk != row.action_risk
    ):
        raise RemediationMatrixError(
            f"row {row_id!r} authored request conflicts with catalog authority"
        )

    target_provenance = _required_source_string(
        immutable_input,
        "targetProvenance",
        row_id=row_id,
        record_type="immutableInputSnapshot",
    )
    input_digest = _required_source_string(
        immutable_input,
        "inputDigest",
        row_id=row_id,
        record_type="immutableInputSnapshot",
    )
    if (
        immutable_input.get("immutable") is not True
        or len(input_digest) != 64
        or any(character not in "0123456789abcdef" for character in input_digest)
    ):
        raise RemediationMatrixError(
            f"row {row_id!r} immutable input snapshot is not digest-bound"
        )

    remediation_provenance = _required_source_string(
        workflow,
        "remediationProvenance",
        row_id=row_id,
        record_type="workflowLineage",
    )
    lineage = dict(
        _required_source_mapping(
            workflow, "lineage", row_id=row_id, record_type="workflowLineage"
        )
    )
    missing_lineage = [
        field
        for field in REQUIRED_REMEDIATION_LINEAGE_FIELDS
        if not isinstance(lineage.get(field), str) or not lineage[field].strip()
    ]
    if missing_lineage:
        raise RemediationMatrixError(
            f"row {row_id!r} lacks durable lineage fields: {missing_lineage}"
        )
    for field in REMEDIATION_EVIDENCE_IDENTITY_FIELDS[:10]:
        if lineage.get(field) != identity[field]:
            raise RemediationMatrixError(
                f"row {row_id!r} lineage identity {field!r} does not match source records"
            )
    for field, record_type in REMEDIATION_LINEAGE_REF_RECORD_TYPES.items():
        if lineage.get(field) != manifest_by_type[record_type].get("ref"):
            raise RemediationMatrixError(
                f"row {row_id!r} lineage ref {field!r} is not bound to its typed source record"
            )

    timings = dict(
        _required_source_mapping(
            workflow, "timings", row_id=row_id, record_type="workflowLineage"
        )
    )
    phase_latencies = _required_source_mapping(
        timings,
        "phaseLatenciesMs",
        row_id=row_id,
        record_type="workflowLineage",
    )
    if set(phase_latencies) != set(REQUIRED_REMEDIATION_PHASE_LATENCIES):
        raise RemediationMatrixError(
            f"row {row_id!r} phase latency dimensions are incomplete"
        )
    resume_outcome = _required_source_string(
        workflow, "resumeOutcome", row_id=row_id, record_type="workflowLineage"
    )
    if resume_outcome not in {"not_applicable", "resumed", "unavailable"}:
        raise RemediationMatrixError(f"row {row_id!r} resume outcome is invalid")
    resume_authority_cases = _required_source_string_set(
        workflow,
        "resumeAuthorityCases",
        allowed=RESUME_AUTHORITY_CASES,
        row_id=row_id,
        record_type="workflowLineage",
    )
    branch_created = _required_source_bool(
        workflow, "branchCreated", row_id=row_id, record_type="workflowLineage"
    )
    new_semantic_step_execution = _required_source_bool(
        workflow,
        "newSemanticStepExecution",
        row_id=row_id,
        record_type="workflowLineage",
    )
    fresh_stock_session = _required_source_bool(
        workflow,
        "freshStockSession",
        row_id=row_id,
        record_type="workflowLineage",
    )
    immutable_input_preserved = _required_source_bool(
        workflow,
        "immutableInputPreserved",
        row_id=row_id,
        record_type="workflowLineage",
    )
    branch_changed_choices = _required_source_string_set(
        workflow,
        "branchChangedChoices",
        allowed=BRANCH_CHANGED_CHOICES,
        row_id=row_id,
        record_type="workflowLineage",
    )
    accepted_workspace_progress_preserved = _required_source_bool(
        workflow,
        "acceptedWorkspaceProgressPreserved",
        row_id=row_id,
        record_type="workflowLineage",
    )
    attempt_count = _required_nonnegative_int(
        workflow, "attemptCount", row_id=row_id, record_type="workflowLineage"
    )
    host_lifecycle_outcome = _required_source_string(
        workflow,
        "hostLifecycleOutcome",
        row_id=row_id,
        record_type="workflowLineage",
    )
    if host_lifecycle_outcome not in {"not_applicable", "reconciled", "failed"}:
        raise RemediationMatrixError(
            f"row {row_id!r} host lifecycle outcome is invalid"
        )

    context_build_outcome = _required_source_string(
        context, "contextBuildOutcome", row_id=row_id, record_type="contextEvidence"
    )
    evidence_outcome = _required_source_string(
        context, "evidenceOutcome", row_id=row_id, record_type="contextEvidence"
    )
    if context_build_outcome not in {"success", "degraded", "unavailable", "denied"}:
        raise RemediationMatrixError(f"row {row_id!r} context build outcome is invalid")
    if evidence_outcome not in {"available", "degraded", "unavailable", "denied"}:
        raise RemediationMatrixError(f"row {row_id!r} evidence outcome is invalid")
    context_follow_phases = _required_source_string_set(
        context,
        "followPhases",
        allowed=CONTEXT_FOLLOW_PHASES,
        row_id=row_id,
        record_type="contextEvidence",
    )
    context_denial_cases = _required_source_string_set(
        context,
        "denialCases",
        allowed=CONTEXT_DENIAL_CASES,
        row_id=row_id,
        record_type="contextEvidence",
    )
    context_nondisclosure_protections = _required_source_string_set(
        context,
        "nondisclosureProtections",
        allowed=CONTEXT_NONDISCLOSURE_PROTECTIONS,
        row_id=row_id,
        record_type="contextEvidence",
    )

    if (
        profile.get("authorityMode") != authority_mode
        or profile.get("actionCapability") != action_capability
        or profile.get("verificationCapability") != verification_capability
        or profile.get("actionRisk") != action_risk
        or profile.get("agentProfileId") != identity["agentProfileId"]
        or profile.get("providerProfileId") != identity["providerProfileId"]
        or profile.get("leaseId") != identity["leaseId"]
        or profile.get("profileValidated") is not True
        or profile.get("policyValidated") is not True
        or profile.get("credentialGenerationFresh") is not True
    ):
        raise RemediationMatrixError(
            f"row {row_id!r} profile/policy authority does not match the authored request"
        )
    strong_reviewer_authority = _required_source_bool(
        profile,
        "strongReviewerAuthority",
        row_id=row_id,
        record_type="profilePolicyAuthority",
    )
    lease_host_reconciled = _required_source_bool(
        profile,
        "leaseHostReconciled",
        row_id=row_id,
        record_type="profilePolicyAuthority",
    )

    egress_authority = _required_source_string(
        egress, "authority", row_id=row_id, record_type="egressAttestation"
    )
    egress_decision = _required_source_string(
        egress, "decision", row_id=row_id, record_type="egressAttestation"
    )
    egress_attestation = _required_source_string(
        egress,
        "attestationOutcome",
        row_id=row_id,
        record_type="egressAttestation",
    )
    expected_egress_decision = {
        EGRESS_NOT_APPLICABLE: "not_applicable",
        EGRESS_RESTRICTED_ALLOWED: "allowed",
        EGRESS_RESTRICTED_DENIED: "denied",
    }[row.egress]
    if egress_authority != row.egress or egress_decision != expected_egress_decision:
        raise RemediationMatrixError(f"row {row_id!r} egress authority is mismatched")
    if egress_attestation not in {"passed", "failed"}:
        raise RemediationMatrixError(f"row {row_id!r} egress attestation is invalid")

    approval_requested = _required_source_bool(
        approval, "requested", row_id=row_id, record_type="approvalDecision"
    )
    approval_outcome = _required_source_string(
        approval, "outcome", row_id=row_id, record_type="approvalDecision"
    )
    if approval_outcome not in REMEDIATION_APPROVAL_OUTCOMES:
        raise RemediationMatrixError(f"row {row_id!r} approval outcome is invalid")
    approval_outcomes_observed = _required_source_string_set(
        approval,
        "outcomesObserved",
        allowed=REMEDIATION_APPROVAL_OUTCOMES,
        row_id=row_id,
        record_type="approvalDecision",
    )

    delivery_status = _required_source_string(
        action, "deliveryStatus", row_id=row_id, record_type="actionResult"
    )
    action_outcome = _required_source_string(
        action, "outcome", row_id=row_id, record_type="actionResult"
    )
    if (
        delivery_status not in REMEDIATION_DELIVERY_STATUSES
        or action_outcome not in REMEDIATION_ACTION_OUTCOMES
        or action.get("actionKind") != action_capability
        or action.get("risk") != action_risk
    ):
        raise RemediationMatrixError(f"row {row_id!r} action result is invalid")
    expected_action_outcomes = {
        "delivered": {"delivered", "no_op"},
        "denied": {"denied"},
        "suppressed_idempotent": {"no_op"},
        "not_delivered": {"failure", "unknown"},
        "not_applicable": {"no_op"},
    }
    if action_outcome not in expected_action_outcomes[delivery_status]:
        raise RemediationMatrixError(
            f"row {row_id!r} action delivery and outcome disagree"
        )
    action_flags = {
        key: _required_source_bool(action, key, row_id=row_id, record_type="actionResult")
        for key in (
            "lockConflict",
            "cooldown",
            "duplicateSuppressed",
            "nestedRemediationDenied",
            "noProgressEscalated",
        )
    }
    action_requested = _required_source_bool(
        action, "requested", row_id=row_id, record_type="actionResult"
    )
    action_risk_cases_delivered = _required_source_string_set(
        action,
        "riskCasesDelivered",
        allowed=ACTION_RISK_CASES,
        row_id=row_id,
        record_type="actionResult",
    )
    stale_authority_rejections = _required_source_string_set(
        action,
        "staleAuthorityRejections",
        allowed=STALE_AUTHORITY_CASES,
        row_id=row_id,
        record_type="actionResult",
    )
    diagnosis_parallel_allowed = _required_source_bool(
        action,
        "diagnosisParallelAllowed",
        row_id=row_id,
        record_type="actionResult",
    )
    session_controls_delivered = _required_source_string_set(
        action,
        "sessionControlsDelivered",
        allowed=SESSION_CONTROL_CASES,
        row_id=row_id,
        record_type="actionResult",
    )
    prohibited_authorities_denied = _required_source_string_set(
        action,
        "prohibitedAuthoritiesDenied",
        allowed=PROHIBITED_AUTHORITY_CASES,
        row_id=row_id,
        record_type="actionResult",
    )

    repair_outcome = _required_source_string(
        verification,
        "outcome",
        row_id=row_id,
        record_type="verificationResult",
    )
    if (
        repair_outcome not in REMEDIATION_REPAIR_OUTCOMES
        or verification.get("verificationCapability") != verification_capability
    ):
        raise RemediationMatrixError(f"row {row_id!r} verification result is invalid")
    unverified_mutation = _required_source_bool(
        verification,
        "unverifiedMutation",
        row_id=row_id,
        record_type="verificationResult",
    )
    repeated_failure = _required_source_bool(
        verification,
        "repeatedFailure",
        row_id=row_id,
        record_type="verificationResult",
    )
    attempts_exhausted = _required_source_bool(
        verification,
        "attemptsExhausted",
        row_id=row_id,
        record_type="verificationResult",
    )
    prevention_outcome = _required_source_string(
        verification,
        "preventionOutcome",
        row_id=row_id,
        record_type="verificationResult",
    )
    verification_outcomes_observed = _required_source_string_set(
        verification,
        "outcomesObserved",
        allowed=tuple(REMEDIATION_REPAIR_OUTCOMES),
        row_id=row_id,
        record_type="verificationResult",
    )
    immediate_repair_outcome = _required_source_string(
        verification,
        "immediateRepairOutcome",
        row_id=row_id,
        record_type="verificationResult",
    )
    if immediate_repair_outcome not in REMEDIATION_REPAIR_OUTCOMES:
        raise RemediationMatrixError(
            f"row {row_id!r} immediate repair outcome is invalid"
        )
    prevention_analysis_separate = _required_source_bool(
        verification,
        "preventionAnalysisSeparate",
        row_id=row_id,
        record_type="verificationResult",
    )
    target_relabeled_repaired = _required_source_bool(
        verification,
        "targetRelabeledRepaired",
        row_id=row_id,
        record_type="verificationResult",
    )
    publication_outcome = _required_source_string(
        publication, "outcome", row_id=row_id, record_type="publicationOutcome"
    )
    prevention_pr_outcome = _required_source_string(
        publication,
        "preventionPrOutcome",
        row_id=row_id,
        record_type="publicationOutcome",
    )
    if prevention_pr_outcome not in {
        "not_applicable",
        "published_reviewable",
        "verification_failed",
    }:
        raise RemediationMatrixError(
            f"row {row_id!r} prevention PR outcome is invalid"
        )

    cleanup_outcome = _required_source_string(
        cleanup, "outcome", row_id=row_id, record_type="cleanupOutcome"
    )
    remaining_live_resources = _required_nonnegative_int(
        cleanup,
        "remainingLiveResources",
        row_id=row_id,
        record_type="cleanupOutcome",
    )
    cleanup_flags: dict[str, bool] = {}
    for key in (
        "terminalHarvested",
        "janitorVerified",
        "lockReleased",
        "capacityReleased",
        "providerProfileReleasedLast",
    ):
        cleanup_flags[key] = _required_source_bool(
            cleanup, key, row_id=row_id, record_type="cleanupOutcome"
        )
    targeted_cleanup = _required_source_bool(
        cleanup, "targetedCleanup", row_id=row_id, record_type="cleanupOutcome"
    )
    helper_restarted = _required_source_bool(
        cleanup, "helperRestarted", row_id=row_id, record_type="cleanupOutcome"
    )
    helper_reaped = _required_source_bool(
        cleanup, "helperReaped", row_id=row_id, record_type="cleanupOutcome"
    )
    helper_target_linked = _required_source_bool(
        cleanup, "helperTargetLinked", row_id=row_id, record_type="cleanupOutcome"
    )
    operator_cancelled = _required_source_bool(
        cleanup, "operatorCancelled", row_id=row_id, record_type="cleanupOutcome"
    )
    operator_takeover = _required_source_bool(
        cleanup, "operatorTakeover", row_id=row_id, record_type="cleanupOutcome"
    )

    replay_count = _required_nonnegative_int(
        temporal, "replayCount", row_id=row_id, record_type="temporalHistory"
    )
    replay_outcome = _required_source_string(
        temporal, "replayOutcome", row_id=row_id, record_type="temporalHistory"
    )
    cancellation_phases = _required_source_string_set(
        temporal,
        "cancellationPhases",
        allowed=REMEDIATION_DURABLE_PHASES,
        row_id=row_id,
        record_type="temporalHistory",
    )
    replay_phases = _required_source_string_set(
        temporal,
        "replayPhases",
        allowed=REMEDIATION_DURABLE_PHASES,
        row_id=row_id,
        record_type="temporalHistory",
    )
    duplicate_effects_suppressed = _required_source_string_set(
        temporal,
        "duplicateEffectsSuppressed",
        allowed=DUPLICATE_EFFECT_CASES,
        row_id=row_id,
        record_type="temporalHistory",
    )
    first_message_count = _required_nonnegative_int(
        temporal,
        "firstMessageCount",
        row_id=row_id,
        record_type="temporalHistory",
    )
    duplicate_suppression_count = _required_nonnegative_int(
        temporal,
        "duplicateSuppressionCount",
        row_id=row_id,
        record_type="temporalHistory",
    )

    secret_findings = _required_nonnegative_int(
        retained_scan,
        "secretFindings",
        row_id=row_id,
        record_type="retainedEvidenceScan",
    )
    prohibited_authority_findings = _required_nonnegative_int(
        retained_scan,
        "prohibitedAuthorityFindings",
        row_id=row_id,
        record_type="retainedEvidenceScan",
    )
    channels = retained_scan.get("channels")
    if not isinstance(channels, list) or set(channels) != set(
        REQUIRED_REMEDIATION_RETAINED_CHANNELS
    ):
        raise RemediationMatrixError(
            f"row {row_id!r} retained evidence scan has incomplete channels"
        )

    semantic_facts = {
        "remediationCreated": remediation_created,
        "contextBuildOutcome": context_build_outcome,
        "evidenceOutcome": evidence_outcome,
        "contextFollowPhases": context_follow_phases,
        "contextDenialCases": context_denial_cases,
        "contextNondisclosureProtections": context_nondisclosure_protections,
        "resumeOutcome": resume_outcome,
        "resumeAuthorityCases": resume_authority_cases,
        "branchCreated": branch_created,
        "newSemanticStepExecution": new_semantic_step_execution,
        "freshStockSession": fresh_stock_session,
        "immutableInputPreserved": immutable_input_preserved,
        "branchChangedChoices": branch_changed_choices,
        "acceptedWorkspaceProgressPreserved": accepted_workspace_progress_preserved,
        "attemptCount": attempt_count,
        "hostLifecycleOutcome": host_lifecycle_outcome,
        "strongReviewerAuthority": strong_reviewer_authority,
        "leaseHostReconciled": lease_host_reconciled,
        "approvalRequested": approval_requested,
        "approvalOutcome": approval_outcome,
        "approvalOutcomesObserved": approval_outcomes_observed,
        "actionRequested": action_requested,
        "deliveryStatus": delivery_status,
        "actionOutcome": action_outcome,
        "actionRiskCasesDelivered": action_risk_cases_delivered,
        "staleAuthorityRejections": stale_authority_rejections,
        "diagnosisParallelAllowed": diagnosis_parallel_allowed,
        "sessionControlsDelivered": session_controls_delivered,
        "prohibitedAuthoritiesDenied": prohibited_authorities_denied,
        **action_flags,
        "repairOutcome": repair_outcome,
        "unverifiedMutation": unverified_mutation,
        "repeatedFailure": repeated_failure,
        "attemptsExhausted": attempts_exhausted,
        "verificationOutcomesObserved": verification_outcomes_observed,
        "immediateRepairOutcome": immediate_repair_outcome,
        "preventionOutcome": prevention_outcome,
        "preventionAnalysisSeparate": prevention_analysis_separate,
        "targetRelabeledRepaired": target_relabeled_repaired,
        "publicationOutcome": publication_outcome,
        "preventionPrOutcome": prevention_pr_outcome,
        "cleanupOutcome": cleanup_outcome,
        "remainingLiveResources": remaining_live_resources,
        **cleanup_flags,
        "targetedCleanup": targeted_cleanup,
        "helperRestarted": helper_restarted,
        "helperReaped": helper_reaped,
        "helperTargetLinked": helper_target_linked,
        "operatorCancelled": operator_cancelled,
        "operatorTakeover": operator_takeover,
        "replayCount": replay_count,
        "replayOutcome": replay_outcome,
        "cancellationPhases": cancellation_phases,
        "replayPhases": replay_phases,
        "duplicateEffectsSuppressed": duplicate_effects_suppressed,
        "firstMessageCount": first_message_count,
        "duplicateSuppressionCount": duplicate_suppression_count,
        "egressDecision": egress_decision,
        "egressAttestationOutcome": egress_attestation,
        "origin": origin,
        "secretFindings": secret_findings,
        "prohibitedAuthorityFindings": prohibited_authority_findings,
    }
    observed_disposition, observations, threshold_samples = (
        _derive_catalog_semantics(row=row, facts=semantic_facts)
    )
    audit_disposition = _required_source_string(
        audit, "observedDisposition", row_id=row_id, record_type="sideEffectAudit"
    )
    audit_observations = dict(
        _required_source_mapping(
            audit, "observations", row_id=row_id, record_type="sideEffectAudit"
        )
    )
    audit_threshold_samples = dict(
        _required_source_mapping(
            audit,
            "thresholdSamples",
            row_id=row_id,
            record_type="sideEffectAudit",
        )
    )
    audit_first_message_count = _required_nonnegative_int(
        audit, "firstMessageCount", row_id=row_id, record_type="sideEffectAudit"
    )
    audit_duplicate_suppression_count = _required_nonnegative_int(
        audit,
        "duplicateSuppressionCount",
        row_id=row_id,
        record_type="sideEffectAudit",
    )
    if (
        audit_disposition != observed_disposition
        or audit_observations != observations
        or audit_threshold_samples != threshold_samples
        or audit_first_message_count != first_message_count
        or audit_duplicate_suppression_count != duplicate_suppression_count
    ):
        raise RemediationMatrixError(
            f"row {row_id!r} side-effect audit conflicts with authoritative typed facts"
        )

    thresholds = {
        threshold: {
            "within": sample["passed"] == sample["total"],
            **sample,
        }
        for threshold, sample in threshold_samples.items()
    }

    derived: dict[str, Any] = {
        "observedDisposition": observed_disposition,
        "hostMode": host_mode,
        "architecture": architecture,
        "targetProvenance": target_provenance,
        "remediationProvenance": remediation_provenance,
        "authorityMode": authority_mode,
        "egress": egress_authority,
        "actionCapability": action_capability,
        "verificationCapability": verification_capability,
        "uiJourney": ui_journey,
        "actionDelivery": {"status": delivery_status},
        "repairVerification": {"outcome": repair_outcome},
        "timings": timings,
        "observations": observations,
        "lineage": lineage,
        "thresholds": thresholds,
        "telemetryFacts": {
            "remediationCreated": remediation_created,
            "contextBuildOutcome": context_build_outcome,
            "evidenceOutcome": evidence_outcome,
            "approvalRequested": approval_requested,
            "approvalOutcome": approval_outcome,
            "actionRequested": action_requested,
            "actionOutcome": action_outcome,
            "actionKind": action_capability,
            "actionRisk": action_risk,
            **action_flags,
            "phaseLatenciesMs": dict(phase_latencies),
            "verificationOutcome": repair_outcome,
            "unverifiedMutation": unverified_mutation,
            "repeatedFailure": repeated_failure,
            "attemptsExhausted": attempts_exhausted,
            "egressDecision": egress_decision,
            "egressAttestationOutcome": egress_attestation,
            "operatorCancelled": operator_cancelled,
            "operatorTakeover": operator_takeover,
            "origin": origin,
            "cleanupOutcome": cleanup_outcome,
            "remainingLiveResources": remaining_live_resources,
            "replayCount": replay_count,
            "firstMessageCount": first_message_count,
            "duplicateSuppressionCount": duplicate_suppression_count,
            "secretFindings": secret_findings,
            "prohibitedAuthorityFindings": prohibited_authority_findings,
        },
    }

    scenario_claims = {
        "row": row_id,
        "observed": True,
        "observedDisposition": observed_disposition,
        "hostMode": host_mode,
        "architecture": architecture,
        "targetProvenance": target_provenance,
        "remediationProvenance": remediation_provenance,
        "remainingLiveResources": remaining_live_resources,
        "timings": timings,
        "thresholdSamples": threshold_samples,
        "observations": observations,
        "lineage": lineage,
    }
    if row.is_mutation:
        scenario_claims["actionDelivery"] = derived["actionDelivery"]
        scenario_claims["repairVerification"] = derived["repairVerification"]
    if any(scenario.get(key) != value for key, value in scenario_claims.items()):
        raise RemediationMatrixError(
            f"row {row_id!r} scenario observation conflicts with authoritative source records"
        )
    return derived


def _validate_lineage_and_source_manifest(
    entry: Mapping[str, Any],
    *,
    row: RemediationMatrixRow,
    evidence_document_path: Path | None,
    evidence_time: datetime | None,
) -> dict[str, Any]:
    """Resolve, type-check, cross-bind, and derive all source evidence."""

    if evidence_document_path is None:
        raise RemediationMatrixError(
            f"row {row.row_id!r} source evidence requires a document path"
        )
    manifest = entry.get("evidenceManifest")
    if not isinstance(manifest, list):
        raise RemediationMatrixError(
            f"row {row.row_id!r} lacks a source evidence manifest"
        )
    observed_types: set[str] = set()
    observed_refs: set[str] = set()
    manifest_by_type: dict[str, Mapping[str, Any]] = {}
    sources: dict[str, Mapping[str, Any]] = {}
    base = evidence_document_path.resolve().parent
    for record in manifest:
        if not isinstance(record, Mapping):
            raise RemediationMatrixError(
                f"row {row.row_id!r} source evidence record is malformed"
            )
        record_type = record.get("type")
        ref = record.get("ref")
        digest = record.get("sha256")
        schema_version = record.get("schemaVersion")
        generated_at = record.get("generatedAt")
        size_bytes = record.get("sizeBytes")
        if (
            record_type not in REQUIRED_REMEDIATION_SOURCE_RECORD_TYPES
            or record_type in observed_types
            or not isinstance(ref, str)
            or not ref.strip()
            or ref in observed_refs
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or schema_version != REMEDIATION_SOURCE_RECORD_SCHEMAS.get(record_type)
            or record.get("contentType") != REMEDIATION_SOURCE_RECORD_CONTENT_TYPE
            or not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or size_bytes < 2
            or size_bytes > MAX_REMEDIATION_SOURCE_RECORD_BYTES
            or not isinstance(generated_at, str)
        ):
            raise RemediationMatrixError(
                f"row {row.row_id!r} source evidence record is malformed"
            )
        try:
            generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            if generated.tzinfo is None:
                raise ValueError
        except ValueError as exc:
            raise RemediationMatrixError(
                f"row {row.row_id!r} source evidence timestamp is invalid"
            ) from exc
        if evidence_time is not None:
            age = (evidence_time - generated).total_seconds()
            if age < 0 or age > MAX_EVIDENCE_AGE_SECONDS:
                raise RemediationMatrixError(
                    f"row {row.row_id!r} source evidence is stale"
                )
        try:
            record_path = _evidence_path(ref)
            if not record_path.is_absolute():
                record_path = base / record_path
            record_path = record_path.resolve()
            if record_path != base and base not in record_path.parents:
                raise ValueError("source record escaped evidence root")
            content = record_path.read_bytes()
        except (OSError, ValueError) as exc:
            raise RemediationMatrixError(
                f"row {row.row_id!r} source evidence ref is unreadable"
            ) from exc
        if len(content) != size_bytes or hashlib.sha256(content).hexdigest() != digest:
            raise RemediationMatrixError(
                f"row {row.row_id!r} source evidence digest or size mismatch"
            )
        try:
            source = json.loads(content)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RemediationMatrixError(
                f"row {row.row_id!r} source evidence is not JSON"
            ) from exc
        if (
            not isinstance(source, Mapping)
            or source.get("schemaVersion") != schema_version
            or source.get("generatedAt") != generated_at
        ):
            raise RemediationMatrixError(
                f"row {row.row_id!r} source evidence schema/freshness mismatch"
            )
        try:
            assert_secret_free(content.decode("utf-8"))
        except ConformanceContractError as exc:
            raise RemediationMatrixError(
                f"row {row.row_id!r} source evidence contains secret-like material"
            ) from exc
        observed_types.add(str(record_type))
        observed_refs.add(ref)
        manifest_by_type[str(record_type)] = record
        sources[str(record_type)] = source

    missing_types = REQUIRED_REMEDIATION_SOURCE_RECORD_TYPES - observed_types
    if missing_types:
        raise RemediationMatrixError(
            f"row {row.row_id!r} lacks source evidence types: {sorted(missing_types)}"
        )
    derived = derive_remediation_observation_from_source_records(
        row=row,
        manifest_by_type=manifest_by_type,
        sources=sources,
    )
    lineage = entry.get("lineage")
    if not isinstance(lineage, Mapping) or dict(lineage) != derived["lineage"]:
        raise RemediationMatrixError(
            f"row {row.row_id!r} outer lineage conflicts with typed source evidence"
        )
    for key in (
        "observedDisposition",
        "hostMode",
        "architecture",
        "targetProvenance",
        "remediationProvenance",
        "authorityMode",
        "egress",
        "actionCapability",
        "verificationCapability",
        "uiJourney",
        "timings",
        "observations",
        "thresholds",
    ):
        if entry.get(key) != derived[key]:
            raise RemediationMatrixError(
                f"row {row.row_id!r} field {key!r} conflicts with typed source evidence"
            )
    if row.is_mutation:
        for key in ("actionDelivery", "repairVerification"):
            if entry.get(key) != derived[key]:
                raise RemediationMatrixError(
                    f"row {row.row_id!r} field {key!r} conflicts with typed source evidence"
                )
    return derived


def derive_remediation_row_evidence(
    entry: Mapping[str, Any],
    *,
    row: RemediationMatrixRow,
    evidence_document_path: Path,
    evidence_time: datetime | None = None,
) -> dict[str, Any]:
    """Public shared boundary for live assembly and post-cleanup replay."""

    return _validate_lineage_and_source_manifest(
        entry,
        row=row,
        evidence_document_path=evidence_document_path,
        evidence_time=evidence_time,
    )


def validate_remediation_evidence_artifact(
    payload: Any,
    *,
    expected_kind: str | None,
    images: Any,
    architectures: Any,
    profile_version: Any,
    profile_sha256: Any,
    policy_version: Any,
    agent_profile_version: Any,
    remediation_policy_version: Any,
    evidence_document_path: Path | None = None,
    evidence_time: datetime | None = None,
) -> tuple[str, frozenset[str]]:
    """Bind one protected artifact to the rows it independently proves.

    Returns the owning evidence kind and the frozen set of rows it proves.
    Raises :class:`RemediationMatrixError` when identity, ownership, host mode,
    runtime provenance, immutable images, architecture, conformance profile,
    launch-policy/agent-profile/remediation-policy version, expected disposition,
    delivery/repair separation, UI journey authority, thresholds, or the
    raw-channel secret scan cannot be independently confirmed. A caller-supplied
    row name or a bare ``passed`` flag is never sufficient.
    """

    if not isinstance(payload, Mapping):
        raise RemediationMatrixError("artifact is not an object")
    if payload.get("schemaVersion") != REMEDIATION_ARTIFACT_SCHEMA_VERSION:
        raise RemediationMatrixError("unsupported artifact schema")
    if payload.get("matrixVersion") != REMEDIATION_MATRIX_VERSION:
        raise RemediationMatrixError("unsupported support-matrix version")
    kind = payload.get("kind")
    if not isinstance(kind, str) or kind not in REQUIRED_REMEDIATION_EVIDENCE_KINDS:
        raise RemediationMatrixError("unsupported evidence kind")
    if expected_kind is not None and kind != expected_kind:
        raise RemediationMatrixError("evidence kind does not match manifest")
    producer = payload.get("producerVersion")
    if not isinstance(producer, str) or not producer.strip():
        raise RemediationMatrixError("producer version is required")
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise RemediationMatrixError("artifact declares no observed rows")

    if not isinstance(images, Mapping):
        raise RemediationMatrixError("release images are required")
    try:
        require_pinned_images(images)
    except ConformanceContractError as exc:
        raise RemediationMatrixError("release images must be immutable") from exc
    architecture_set = (
        {item for item in architectures if isinstance(item, str) and item.strip()}
        if isinstance(architectures, list)
        else set()
    )
    if not architecture_set:
        raise RemediationMatrixError("release architectures are required")
    if not isinstance(profile_version, str) or not isinstance(profile_sha256, str):
        raise RemediationMatrixError("conformance profile evidence is required")
    if not isinstance(policy_version, str) or not policy_version.strip():
        raise RemediationMatrixError("launch policy version is required")
    if not isinstance(agent_profile_version, str) or not agent_profile_version.strip():
        raise RemediationMatrixError("agent profile version is required")
    if (
        not isinstance(remediation_policy_version, str)
        or not remediation_policy_version.strip()
    ):
        raise RemediationMatrixError("remediation policy version is required")

    observed: dict[str, set[str]] = {}
    for entry in rows:
        if not isinstance(entry, Mapping):
            raise RemediationMatrixError("observed row is not an object")
        row_id = entry.get("row")
        row = (
            REMEDIATION_ROW_CATALOG_BY_ID.get(row_id)
            if isinstance(row_id, str)
            else None
        )
        if row is None:
            raise RemediationMatrixError(f"unknown protected row: {row_id!r}")
        if row.evidence_kind != kind:
            raise RemediationMatrixError(
                f"row {row_id!r} is not owned by evidence kind {kind!r}"
            )
        if entry.get("observedDisposition") != row.expected_outcome:
            raise RemediationMatrixError(
                f"row {row_id!r} did not observe its required disposition "
                f"{row.expected_outcome!r}"
            )
        if entry.get("gate") != row.gate:
            raise RemediationMatrixError(f"row {row_id!r} declares the wrong gate")
        if entry.get("hostMode") not in row.host_modes:
            raise RemediationMatrixError(f"row {row_id!r} has an unsupported host mode")
        if entry.get("targetProvenance") not in row.target_provenance:
            raise RemediationMatrixError(
                f"row {row_id!r} has unexpected target runtime provenance"
            )
        if entry.get("remediationProvenance") not in row.remediation_provenance:
            raise RemediationMatrixError(
                f"row {row_id!r} has unexpected remediation runtime provenance"
            )
        if entry.get("authorityMode") != row.authority_mode:
            raise RemediationMatrixError(
                f"row {row_id!r} authority mode does not match the catalog"
            )
        if entry.get("egress") != row.egress:
            raise RemediationMatrixError(
                f"row {row_id!r} egress authority does not match the catalog"
            )
        if entry.get("actionCapability") != row.action_capability:
            raise RemediationMatrixError(
                f"row {row_id!r} action capability does not match the catalog"
            )
        if entry.get("verificationCapability") != row.verification_capability:
            raise RemediationMatrixError(
                f"row {row_id!r} verification capability does not match the catalog"
            )
        _validate_ui_journey(entry.get("uiJourney"), row=row)
        _validate_delivery_and_repair(entry, row=row)
        _validate_thresholds(entry.get("thresholds"), row=row)
        _validate_required_observations(entry.get("observations"), row=row)
        _validate_timings(entry.get("timings"), row=row)
        _validate_lineage_and_source_manifest(
            entry,
            row=row,
            evidence_document_path=evidence_document_path,
            evidence_time=evidence_time,
        )
        _validate_row_secret_scan(
            entry.get("secretScan"),
            row_id=row.row_id,
            evidence_document_path=evidence_document_path,
            evidence_time=evidence_time,
        )

        architecture = entry.get("architecture")
        if architecture not in architecture_set or architecture not in row.architectures:
            raise RemediationMatrixError(
                f"row {row_id!r} was not observed on a released architecture"
            )
        if architecture in observed.get(row_id, set()):
            raise RemediationMatrixError(
                f"row {row_id!r} observed more than once on architecture "
                f"{architecture!r}"
            )
        row_images = entry.get("images")
        if not isinstance(row_images, Mapping) or dict(row_images) != dict(images):
            raise RemediationMatrixError(
                f"row {row_id!r} images are not the released immutable digests"
            )
        if (
            entry.get("profileVersion") != profile_version
            or entry.get("profileSha256") != profile_sha256
        ):
            raise RemediationMatrixError(
                f"row {row_id!r} is not the canonical conformance profile"
            )
        if entry.get("launchPolicyVersion") != policy_version:
            raise RemediationMatrixError(
                f"row {row_id!r} launch policy version mismatch"
            )
        if entry.get("agentProfileVersion") != agent_profile_version:
            raise RemediationMatrixError(
                f"row {row_id!r} agent profile version mismatch"
            )
        if entry.get("remediationPolicyVersion") != remediation_policy_version:
            raise RemediationMatrixError(
                f"row {row_id!r} remediation policy version mismatch"
            )
        observed.setdefault(row_id, set()).add(architecture)

    # Every released architecture requires its own live evidence for each owned
    # row. Membership in the release list is not enough.
    for row_id, seen_architectures in observed.items():
        missing_architectures = architecture_set - seen_architectures
        if missing_architectures:
            raise RemediationMatrixError(
                f"row {row_id!r} was not observed on every released architecture: "
                f"{sorted(missing_architectures)}"
            )
    return kind, frozenset(observed)


def _evidence_path(ref: str) -> Path:
    """Resolve only deployment-local evidence; remote refs are not authority."""

    parsed = urlparse(ref)
    if parsed.scheme == "file":
        if parsed.netloc not in {"", "localhost"}:
            raise ValueError("remediation_evidence_ref_not_local")
        return Path(unquote(parsed.path))
    if parsed.scheme:
        raise ValueError("remediation_evidence_ref_not_local")
    return Path(ref)


def _telemetry_rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _latency_distribution(values: list[int]) -> dict[str, int]:
    ordered = sorted(values)
    if not ordered:
        return {"sampleCount": 0, "maxMs": 0, "p95Ms": 0}
    index = max(0, ((len(ordered) * 95 + 99) // 100) - 1)
    return {
        "sampleCount": len(ordered),
        "maxMs": ordered[-1],
        "p95Ms": ordered[index],
    }


def derive_remediation_telemetry(
    rows: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the complete v1 operator projection from validated row facts."""

    facts = {
        row_id: entry["telemetryFacts"]
        for row_id, entry in rows.items()
    }
    total = len(facts)
    created = sum(1 for item in facts.values() if item["remediationCreated"])
    context_success = sum(
        1 for item in facts.values() if item["contextBuildOutcome"] == "success"
    )
    evidence_degraded = sum(
        1 for item in facts.values() if item["evidenceOutcome"] == "degraded"
    )
    evidence_unavailable = sum(
        1
        for item in facts.values()
        if item["evidenceOutcome"] in {"unavailable", "denied"}
    )

    approval_requests = sum(1 for item in facts.values() if item["approvalRequested"])
    approval_counts = {
        outcome: sum(
            1 for item in facts.values() if item["approvalOutcome"] == outcome
        )
        for outcome in REMEDIATION_APPROVAL_OUTCOMES
    }

    expected_buckets = {
        f"{row.action_capability}|{row.action_risk}"
        for row in REMEDIATION_ROW_CATALOG
    }
    action_buckets: dict[str, dict[str, int]] = {
        key: {
            "sampleCount": 0,
            "requestCount": 0,
            "deliveryCount": 0,
            "noOpCount": 0,
            "failureCount": 0,
            "unknownCount": 0,
            "denialCount": 0,
        }
        for key in sorted(expected_buckets)
    }
    for item in facts.values():
        bucket = action_buckets[f"{item['actionKind']}|{item['actionRisk']}"]
        bucket["sampleCount"] += 1
        bucket["requestCount"] += int(item["actionRequested"])
        bucket[
            {
                "delivered": "deliveryCount",
                "no_op": "noOpCount",
                "failure": "failureCount",
                "unknown": "unknownCount",
                "denied": "denialCount",
            }[item["actionOutcome"]]
        ] += 1
    by_risk = {
        risk: {
            "sampleCount": sum(
                bucket["sampleCount"]
                for key, bucket in action_buckets.items()
                if key.endswith(f"|{risk}")
            ),
            "requestCount": sum(
                bucket["requestCount"]
                for key, bucket in action_buckets.items()
                if key.endswith(f"|{risk}")
            ),
            "deliveryCount": sum(
                bucket["deliveryCount"]
                for key, bucket in action_buckets.items()
                if key.endswith(f"|{risk}")
            ),
            "noOpCount": sum(
                bucket["noOpCount"]
                for key, bucket in action_buckets.items()
                if key.endswith(f"|{risk}")
            ),
            "failureCount": sum(
                bucket["failureCount"]
                for key, bucket in action_buckets.items()
                if key.endswith(f"|{risk}")
            ),
            "unknownCount": sum(
                bucket["unknownCount"]
                for key, bucket in action_buckets.items()
                if key.endswith(f"|{risk}")
            ),
            "denialCount": sum(
                bucket["denialCount"]
                for key, bucket in action_buckets.items()
                if key.endswith(f"|{risk}")
            ),
        }
        for risk in REMEDIATION_ACTION_RISKS
    }

    phase_latencies = {
        phase: _latency_distribution(
            [int(item["phaseLatenciesMs"][phase]) for item in facts.values()]
        )
        for phase in REQUIRED_REMEDIATION_PHASE_LATENCIES
    }
    verification_rows = [
        facts[row_id]
        for row_id in rows
        if REMEDIATION_ROW_CATALOG_BY_ID[row_id].is_mutation
    ]
    verification_distribution = {
        outcome: sum(
            1 for item in verification_rows if item["verificationOutcome"] == outcome
        )
        for outcome in sorted(REMEDIATION_REPAIR_OUTCOMES)
    }
    lock_conflicts = sum(1 for item in facts.values() if item["lockConflict"])
    cooldowns = sum(1 for item in facts.values() if item["cooldown"])
    duplicate_suppressions = sum(
        int(item["duplicateSuppressed"]) + int(item["duplicateSuppressionCount"])
        for item in facts.values()
    )
    nested_denials = sum(
        1 for item in facts.values() if item["nestedRemediationDenied"]
    )
    escalations = sum(1 for item in facts.values() if item["noProgressEscalated"])
    egress_failures = sum(
        1
        for item in facts.values()
        if item["egressAttestationOutcome"] == "failed"
    )
    manual_count = sum(1 for item in facts.values() if item["origin"] == "manual")
    autonomous_count = total - manual_count

    groups = {
        "remediationCreation": {
            "sampleCount": total,
            "successCount": created,
            "successRate": _telemetry_rate(created, total),
        },
        "contextBuild": {
            "sampleCount": total,
            "successCount": context_success,
            "successRate": _telemetry_rate(context_success, total),
        },
        "evidenceAvailability": {
            "sampleCount": total,
            "degradedCount": evidence_degraded,
            "unavailableOrDeniedCount": evidence_unavailable,
            "degradedOrUnavailableRate": _telemetry_rate(
                evidence_degraded + evidence_unavailable, total
            ),
        },
        "approvalOutcomes": {
            "requestCount": approval_requests,
            "distribution": approval_counts,
            "denialRate": _telemetry_rate(approval_counts["denied"], approval_requests),
            "expirationRate": _telemetry_rate(
                approval_counts["expired"], approval_requests
            ),
            "staleRejectionRate": _telemetry_rate(
                approval_counts["stale"], approval_requests
            ),
        },
        "actionOutcomesByKindAndRisk": {
            "sampleCount": total,
            "buckets": action_buckets,
            "byRisk": by_risk,
        },
        "lockCooldownDuplicateAndEscalation": {
            "sampleCount": total,
            "lockConflictCount": lock_conflicts,
            "cooldownCount": cooldowns,
            "duplicateSuppressionCount": duplicate_suppressions,
            "nestedRemediationDenialCount": nested_denials,
            "noProgressEscalationCount": escalations,
            "lockConflictRate": _telemetry_rate(lock_conflicts, total),
            "cooldownRate": _telemetry_rate(cooldowns, total),
            "duplicateSuppressionRate": _telemetry_rate(duplicate_suppressions, total),
            "noProgressEscalationRate": _telemetry_rate(escalations, total),
        },
        "branchLifecycleLatency": {
            "sampleCount": total,
            "phaseLatenciesMs": phase_latencies,
        },
        "verificationOutcomes": {
            "sampleCount": len(verification_rows),
            "distribution": verification_distribution,
            "unverifiedMutationCount": sum(
                1 for item in verification_rows if item["unverifiedMutation"]
            ),
        },
        "repeatedFailureAndAttemptExhaustion": {
            "sampleCount": len(verification_rows),
            "repeatedFailureCount": sum(
                1 for item in verification_rows if item["repeatedFailure"]
            ),
            "attemptExhaustionCount": sum(
                1 for item in verification_rows if item["attemptsExhausted"]
            ),
        },
        "egressOutcomes": {
            "sampleCount": total,
            "allowedCount": sum(
                1 for item in facts.values() if item["egressDecision"] == "allowed"
            ),
            "deniedCount": sum(
                1 for item in facts.values() if item["egressDecision"] == "denied"
            ),
            "attestationFailureCount": egress_failures,
            "attestationFailureRate": _telemetry_rate(egress_failures, total),
        },
        "operatorCancellationAndTakeover": {
            "sampleCount": total,
            "cancellationCount": sum(
                1 for item in facts.values() if item["operatorCancelled"]
            ),
            "takeoverCount": sum(
                1 for item in facts.values() if item["operatorTakeover"]
            ),
        },
        "autonomousAndManualOrigin": {
            "sampleCount": total,
            "manualCount": manual_count,
            "autonomousCount": autonomous_count,
            "autonomousDeniedCount": sum(
                1
                for row_id, item in facts.items()
                if item["origin"] == "autonomous"
                and rows[row_id]["observedDisposition"] == OUTCOME_DENIED
            ),
        },
    }
    return {
        "schemaVersion": REMEDIATION_TELEMETRY_SCHEMA_VERSION,
        "groups": groups,
    }


def validate_remediation_telemetry_schema(telemetry: Any) -> None:
    """Reject missing dimensions and incorrect kind/risk/phase buckets."""

    if (
        not isinstance(telemetry, Mapping)
        or telemetry.get("schemaVersion") != REMEDIATION_TELEMETRY_SCHEMA_VERSION
        or not isinstance(telemetry.get("groups"), Mapping)
        or set(telemetry["groups"]) != set(REQUIRED_REMEDIATION_TELEMETRY_GROUPS)
    ):
        raise RemediationMatrixError("remediation telemetry schema is incomplete")
    groups = telemetry["groups"]
    required_fields = {
        "remediationCreation": {"sampleCount", "successCount", "successRate"},
        "contextBuild": {"sampleCount", "successCount", "successRate"},
        "evidenceAvailability": {
            "sampleCount",
            "degradedCount",
            "unavailableOrDeniedCount",
            "degradedOrUnavailableRate",
        },
        "approvalOutcomes": {
            "requestCount",
            "distribution",
            "denialRate",
            "expirationRate",
            "staleRejectionRate",
        },
        "actionOutcomesByKindAndRisk": {"sampleCount", "buckets", "byRisk"},
        "lockCooldownDuplicateAndEscalation": {
            "sampleCount",
            "lockConflictCount",
            "cooldownCount",
            "duplicateSuppressionCount",
            "nestedRemediationDenialCount",
            "noProgressEscalationCount",
            "lockConflictRate",
            "cooldownRate",
            "duplicateSuppressionRate",
            "noProgressEscalationRate",
        },
        "branchLifecycleLatency": {"sampleCount", "phaseLatenciesMs"},
        "verificationOutcomes": {
            "sampleCount",
            "distribution",
            "unverifiedMutationCount",
        },
        "repeatedFailureAndAttemptExhaustion": {
            "sampleCount",
            "repeatedFailureCount",
            "attemptExhaustionCount",
        },
        "egressOutcomes": {
            "sampleCount",
            "allowedCount",
            "deniedCount",
            "attestationFailureCount",
            "attestationFailureRate",
        },
        "operatorCancellationAndTakeover": {
            "sampleCount",
            "cancellationCount",
            "takeoverCount",
        },
        "autonomousAndManualOrigin": {
            "sampleCount",
            "manualCount",
            "autonomousCount",
            "autonomousDeniedCount",
        },
    }
    for group, fields in required_fields.items():
        value = groups.get(group)
        if not isinstance(value, Mapping) or set(value) != fields:
            raise RemediationMatrixError(
                f"remediation telemetry group {group!r} has incorrect dimensions"
            )
    approval_distribution = groups["approvalOutcomes"]["distribution"]
    if not isinstance(approval_distribution, Mapping) or set(
        approval_distribution
    ) != set(REMEDIATION_APPROVAL_OUTCOMES):
        raise RemediationMatrixError("approval telemetry buckets are incomplete")
    action_group = groups["actionOutcomesByKindAndRisk"]
    expected_action_buckets = {
        f"{row.action_capability}|{row.action_risk}"
        for row in REMEDIATION_ROW_CATALOG
    }
    if (
        not isinstance(action_group["buckets"], Mapping)
        or set(action_group["buckets"]) != expected_action_buckets
        or not isinstance(action_group["byRisk"], Mapping)
        or set(action_group["byRisk"]) != set(REMEDIATION_ACTION_RISKS)
    ):
        raise RemediationMatrixError("action telemetry kind/risk buckets are incomplete")
    phase_latencies = groups["branchLifecycleLatency"]["phaseLatenciesMs"]
    if not isinstance(phase_latencies, Mapping) or set(phase_latencies) != set(
        REQUIRED_REMEDIATION_PHASE_LATENCIES
    ):
        raise RemediationMatrixError("remediation phase latency dimensions are incomplete")
    verification_distribution = groups["verificationOutcomes"]["distribution"]
    if not isinstance(verification_distribution, Mapping) or set(
        verification_distribution
    ) != set(REMEDIATION_REPAIR_OUTCOMES):
        raise RemediationMatrixError("verification telemetry buckets are incomplete")


def derive_remediation_release_thresholds(
    rows: Mapping[str, Mapping[str, Any]], telemetry: Mapping[str, Any]
) -> dict[str, Any]:
    """Derive objective promotion/rollback results from validated evidence."""

    facts = [entry["telemetryFacts"] for entry in rows.values()]
    row_thresholds_pass = all(
        result.get("within") is True
        for entry in rows.values()
        for result in entry["thresholds"].values()
    )
    results = {
        "allRowThresholdSamplesPassed": row_thresholds_pass,
        "semanticSourceRecordsValid": len(rows) == len(REQUIRED_REMEDIATION_MATRIX_ROWS),
        "secretAndAuthorityFindingsZero": all(
            item["secretFindings"] == 0
            and item["prohibitedAuthorityFindings"] == 0
            for item in facts
        ),
        "egressAttestationFailuresZero": all(
            item["egressAttestationOutcome"] == "passed" for item in facts
        ),
        "unknownActionOutcomesZero": all(
            item["actionOutcome"] != "unknown" for item in facts
        ),
        "cleanupFailuresZero": all(
            item["cleanupOutcome"] == "completed"
            and item["remainingLiveResources"] == 0
            for item in facts
        ),
    }
    validate_remediation_telemetry_schema(telemetry)
    return {
        "schemaVersion": REMEDIATION_RELEASE_THRESHOLD_SCHEMA_VERSION,
        "withinLimits": all(results.values()),
        "results": results,
    }


@dataclass(frozen=True, slots=True)
class RemediationReleaseStatus:
    """Authoritative, fail-closed operator-remediation release status.

    ``manual_diagnosis_supported`` and ``manual_mutation_supported`` become true
    only when every gating row is independently proven by observed evidence and
    the release document is fresh, complete, threshold-compliant, and secret
    free. ``autonomous_rollout_authorized`` is always ``False`` in this version:
    the autonomous gate is a hard blocker (acceptance criterion 9).
    """

    matrix_version: str
    covered_rows: frozenset[str]
    manual_diagnosis_supported: bool
    manual_mutation_supported: bool
    autonomous_rollout_authorized: bool
    blockers: tuple[str, ...]
    evidence_ref: str | None
    generated_at: str | None = None
    expires_at: str | None = None
    telemetry: Mapping[str, Any] | None = None
    thresholds: Mapping[str, Any] | None = None
    policy_version: str = REMEDIATION_RELEASE_POLICY_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "policyVersion": self.policy_version,
            "matrixVersion": self.matrix_version,
            "coveredRows": sorted(self.covered_rows),
            "requiredRows": list(REQUIRED_REMEDIATION_MATRIX_ROWS),
            "manualDiagnosisSupported": self.manual_diagnosis_supported,
            "manualMutationSupported": self.manual_mutation_supported,
            "autonomousRolloutAuthorized": self.autonomous_rollout_authorized,
            "promotionAllowed": not self.blockers,
            "manualPromotionAllowed": self.manual_mutation_supported,
            "autonomousPromotionAllowed": self.autonomous_rollout_authorized,
            "rollbackRequired": any(
                blocker != "autonomous_rollout_gate_closed"
                for blocker in self.blockers
            ),
            "evidenceRef": self.evidence_ref,
            "generatedAt": self.generated_at,
            "expiresAt": self.expires_at,
            "telemetry": dict(self.telemetry or {}),
            "thresholds": dict(self.thresholds or {}),
            "alerts": [
                {
                    "code": blocker,
                    "severity": (
                        "warning"
                        if blocker == "autonomous_rollout_gate_closed"
                        else "critical"
                    ),
                    "operatorAction": (
                        "keep_autonomous_mutation_disabled"
                        if blocker == "autonomous_rollout_gate_closed"
                        else "block_or_rollback_manual_promotion"
                    ),
                }
                for blocker in self.blockers
            ],
            "catalog": remediation_catalog_document(),
            "blockers": list(self.blockers),
        }


def evaluate_remediation_release(
    *,
    evidence: Mapping[str, Any] | None,
    evidence_document_path: Path | None = None,
    evidence_ref: str | None = None,
    now: datetime | None = None,
) -> RemediationReleaseStatus:
    """Resolve the fail-closed operator-remediation release status.

    A missing, stale, malformed, secret-bearing, or over-threshold artifact
    blocks promotion (section 5). The combined matrix is assembled only from
    complete passing observed rows and immutable release inputs, never from a
    self-asserted pass or a spliced row list (acceptance criterion 7). The
    autonomous rollout gate stays closed regardless of manual coverage.
    """

    blockers: list[str] = []
    if not evidence:
        blockers.append("remediation_release_evidence_missing")
        return RemediationReleaseStatus(
            matrix_version=REMEDIATION_MATRIX_VERSION,
            covered_rows=frozenset(),
            manual_diagnosis_supported=False,
            manual_mutation_supported=False,
            autonomous_rollout_authorized=False,
            blockers=("autonomous_rollout_gate_closed", *dict.fromkeys(blockers)),
            evidence_ref=evidence_ref,
        )

    if evidence.get("schemaVersion") != REMEDIATION_RELEASE_POLICY_VERSION:
        blockers.append("unsupported_release_evidence_version")
    if evidence.get("matrixVersion") != REMEDIATION_MATRIX_VERSION:
        blockers.append("unsupported_support_matrix_version")

    generated_at = evidence.get("generatedAt")
    expires_at: str | None = None
    try:
        generated = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
        if generated.tzinfo is None:
            raise ValueError
        age = ((now or datetime.now(timezone.utc)) - generated).total_seconds()
        if age < 0 or age > MAX_EVIDENCE_AGE_SECONDS:
            blockers.append("remediation_release_evidence_stale")
        expires_at = datetime.fromtimestamp(
            generated.timestamp() + MAX_EVIDENCE_AGE_SECONDS,
            tz=timezone.utc,
        ).isoformat()
    except (TypeError, ValueError):
        blockers.append("remediation_release_evidence_timestamp_invalid")

    policy_version = evidence.get("launchPolicyVersion")
    if not isinstance(policy_version, str) or not policy_version.strip():
        blockers.append("launch_policy_version_required")
    agent_profile_version = evidence.get("agentProfileVersion")
    if not isinstance(agent_profile_version, str) or not agent_profile_version.strip():
        blockers.append("agent_profile_version_required")
    remediation_policy_version = evidence.get("remediationPolicyVersion")
    if (
        not isinstance(remediation_policy_version, str)
        or not remediation_policy_version.strip()
    ):
        blockers.append("remediation_policy_version_required")

    images = evidence.get("images")
    try:
        if not isinstance(images, Mapping):
            raise ConformanceContractError("release images are required")
        require_pinned_images(images)
    except ConformanceContractError:
        blockers.append("immutable_release_images_required")
    architectures = evidence.get("architectures")
    if not isinstance(architectures, list) or not architectures or any(
        not isinstance(item, str) or not item.strip() for item in architectures
    ):
        blockers.append("tested_architectures_required")

    telemetry = evidence.get("telemetry")
    try:
        validate_remediation_telemetry_schema(telemetry)
    except RemediationMatrixError:
        blockers.append("remediation_telemetry_required_or_invalid")

    thresholds = evidence.get("thresholds")
    covered_rows, derived_rows = _verify_remediation_manifest(
        evidence,
        evidence_document_path=evidence_document_path,
        blockers=blockers,
    )
    derived_telemetry: Mapping[str, Any] | None = None
    derived_thresholds: Mapping[str, Any] | None = None
    if set(derived_rows) == set(REQUIRED_REMEDIATION_MATRIX_ROWS):
        try:
            derived_telemetry = derive_remediation_telemetry(derived_rows)
            if telemetry != derived_telemetry:
                blockers.append("remediation_telemetry_diverges_from_evidence")
            derived_thresholds = derive_remediation_release_thresholds(
                derived_rows, derived_telemetry
            )
            if thresholds != derived_thresholds:
                blockers.append("release_thresholds_diverge_from_telemetry")
            if derived_thresholds.get("withinLimits") is not True:
                blockers.append("rollback_threshold_exceeded_or_missing")
        except (KeyError, TypeError, RemediationMatrixError):
            blockers.append("remediation_telemetry_derivation_failed")
    else:
        blockers.append("rollback_threshold_exceeded_or_missing")

    manual_diagnosis_ok = (
        not blockers and _MANUAL_DIAGNOSIS_ROWS <= covered_rows
    )
    manual_mutation_ok = (
        not blockers
        and (_MANUAL_DIAGNOSIS_ROWS | _MANUAL_MUTATION_ROWS) <= covered_rows
    )

    # Acceptance criterion 9: keep autonomous mutating remediation fail-closed.
    # It is never authorized by publishing evidence in this matrix version.
    blockers.append("autonomous_rollout_gate_closed")

    return RemediationReleaseStatus(
        matrix_version=REMEDIATION_MATRIX_VERSION,
        covered_rows=covered_rows,
        manual_diagnosis_supported=manual_diagnosis_ok,
        manual_mutation_supported=manual_mutation_ok,
        autonomous_rollout_authorized=False,
        blockers=tuple(dict.fromkeys(blockers)),
        evidence_ref=evidence_ref,
        generated_at=generated_at if isinstance(generated_at, str) else None,
        expires_at=expires_at,
        telemetry=(
            derived_telemetry
            if isinstance(derived_telemetry, Mapping)
            else telemetry if isinstance(telemetry, Mapping) else None
        ),
        thresholds=(
            derived_thresholds
            if isinstance(derived_thresholds, Mapping)
            else thresholds if isinstance(thresholds, Mapping) else None
        ),
    )


def _verify_remediation_manifest(
    evidence: Mapping[str, Any],
    *,
    evidence_document_path: Path | None,
    blockers: list[str],
) -> tuple[frozenset[str], dict[str, Mapping[str, Any]]]:
    """Resolve every manifest ref locally, bind its bytes to its digest, and
    re-validate the observed per-row evidence it carries.

    Digest integrity alone is not support: each artifact is re-parsed and its
    owned rows re-validated against the release document's declared images,
    architectures, and profile/policy/agent-profile/remediation-policy versions.
    Each evidence kind is bound to exactly one artifact, so split coverage cannot
    be spliced into apparent completeness (acceptance criterion 7).
    """

    manifest = evidence.get("evidenceManifest")
    if not isinstance(manifest, list) or not manifest:
        blockers.append("provenance_bound_evidence_manifest_required")
        return frozenset(), {}
    if evidence_document_path is None:
        blockers.append("remediation_evidence_document_path_required")
        return frozenset(), {}

    base = evidence_document_path.resolve().parent
    images = evidence.get("images")
    architectures = evidence.get("architectures")
    profile_version = evidence.get("profileVersion")
    profile_sha256 = evidence.get("profileSha256")
    policy_version = evidence.get("launchPolicyVersion")
    agent_profile_version = evidence.get("agentProfileVersion")
    remediation_policy_version = evidence.get("remediationPolicyVersion")
    try:
        evidence_time = datetime.fromisoformat(
            str(evidence.get("generatedAt")).replace("Z", "+00:00")
        )
        if evidence_time.tzinfo is None:
            evidence_time = None
    except ValueError:
        evidence_time = None

    observed_rows: set[str] = set()
    derived_rows: dict[str, Mapping[str, Any]] = {}
    seen_kinds: set[str] = set()
    ownership_conflict = False
    split_kind = False
    for item in manifest:
        if not isinstance(item, Mapping):
            blockers.append("provenance_bound_evidence_manifest_invalid")
            continue
        ref = item.get("ref")
        expected = item.get("sha256")
        kind = item.get("kind")
        if (
            not isinstance(ref, str)
            or not isinstance(expected, str)
            or len(expected) != 64
            or any(character not in "0123456789abcdef" for character in expected)
        ):
            blockers.append("provenance_bound_evidence_manifest_invalid")
            continue
        try:
            artifact_path = _evidence_path(ref)
            if not artifact_path.is_absolute():
                artifact_path = base / artifact_path
            content = artifact_path.read_bytes()
        except (OSError, ValueError):
            blockers.append("evidence_manifest_ref_unreadable")
            continue
        if hashlib.sha256(content).hexdigest() != expected:
            blockers.append("evidence_manifest_digest_mismatch")
            continue
        try:
            payload = json.loads(content)
            artifact_kind, rows = validate_remediation_evidence_artifact(
                payload,
                expected_kind=kind if isinstance(kind, str) else None,
                images=images,
                architectures=architectures,
                profile_version=profile_version,
                profile_sha256=profile_sha256,
                policy_version=policy_version,
                agent_profile_version=agent_profile_version,
                remediation_policy_version=remediation_policy_version,
                evidence_document_path=artifact_path,
                evidence_time=evidence_time,
            )
        except (json.JSONDecodeError, UnicodeError, RemediationMatrixError):
            blockers.append("evidence_row_binding_invalid")
            continue
        if artifact_kind in seen_kinds:
            split_kind = True
            continue
        seen_kinds.add(artifact_kind)
        if observed_rows & rows:
            ownership_conflict = True
        observed_rows |= rows
        entries = {
            str(entry.get("row")): entry
            for entry in payload.get("rows", [])
            if isinstance(entry, Mapping) and isinstance(entry.get("row"), str)
        }
        for row_id in rows:
            derived_rows[row_id] = derive_remediation_row_evidence(
                entries[row_id],
                row=REMEDIATION_ROW_CATALOG_BY_ID[row_id],
                evidence_document_path=artifact_path,
                evidence_time=evidence_time,
            )

    if split_kind:
        blockers.append("split_evidence_kind_rejected")
    if ownership_conflict:
        blockers.append("matrix_row_ownership_conflict")
    missing_kinds = set(REQUIRED_REMEDIATION_EVIDENCE_KINDS) - seen_kinds
    if missing_kinds:
        blockers.append("complete_evidence_kind_coverage_required")
    if observed_rows != set(REQUIRED_REMEDIATION_MATRIX_ROWS):
        blockers.append("matrix_row_coverage_incomplete")
    return frozenset(observed_rows), derived_rows


REMEDIATION_RELEASE_EVIDENCE_ENV = (
    "MOONMIND_OMNIGENT_REMEDIATION_RELEASE_EVIDENCE_REF"
)


def load_remediation_release_status(
    *,
    env: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> RemediationReleaseStatus:
    """Load the operator-remediation release status for the deployment.

    Mirrors :func:`moonmind.omnigent.cutover.effective_phase`: a deployment-local
    JSON document is mounted and named by ``MOONMIND_OMNIGENT_REMEDIATION_RELEASE
    _EVIDENCE_REF``. A missing, remote, unreadable, or malformed document fails
    closed with a blocker and never claims support. Merely setting the env var
    without a valid, complete, threshold-compliant document does not qualify
    support, and the autonomous rollout gate stays closed regardless.
    """

    values = os.environ if env is None else env
    raw_ref = str(values.get(REMEDIATION_RELEASE_EVIDENCE_ENV, "")).strip()
    if not raw_ref:
        return evaluate_remediation_release(evidence=None, evidence_ref=None, now=now)

    try:
        document_path = _evidence_path(raw_ref)
        payload = json.loads(document_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("remediation_release_evidence_not_object")
    except (OSError, ValueError, json.JSONDecodeError, UnicodeError):
        return RemediationReleaseStatus(
            matrix_version=REMEDIATION_MATRIX_VERSION,
            covered_rows=frozenset(),
            manual_diagnosis_supported=False,
            manual_mutation_supported=False,
            autonomous_rollout_authorized=False,
            blockers=(
                "autonomous_rollout_gate_closed",
                "remediation_release_evidence_unreadable",
            ),
            evidence_ref=raw_ref,
        )

    return evaluate_remediation_release(
        evidence=payload,
        evidence_document_path=document_path,
        evidence_ref=raw_ref,
        now=now,
    )


__all__ = [
    "REMEDIATION_MATRIX_VERSION",
    "REMEDIATION_ARTIFACT_SCHEMA_VERSION",
    "REMEDIATION_RELEASE_POLICY_VERSION",
    "GATE_MANUAL_DIAGNOSIS",
    "GATE_MANUAL_MUTATION",
    "GATE_AUTONOMOUS_ROLLOUT",
    "GATE_CLASSES",
    "AUTHORITY_MODES",
    "OUTCOME_PASSED",
    "OUTCOME_DENIED",
    "ROW_OUTCOMES",
    "EGRESS_MODES",
    "EGRESS_NOT_APPLICABLE",
    "EGRESS_RESTRICTED_ALLOWED",
    "EGRESS_RESTRICTED_DENIED",
    "REQUIRED_REMEDIATION_EVIDENCE_KINDS",
    "REQUIRED_REMEDIATION_RETAINED_CHANNELS",
    "REQUIRED_REMEDIATION_TELEMETRY_GROUPS",
    "REMEDIATION_REPAIR_OUTCOMES",
    "REMEDIATION_DELIVERY_STATUSES",
    "REQUIRED_UI_JOURNEY_ASSERTIONS",
    "PROHIBITED_UI_JOURNEY_MARKERS",
    "REQUIRED_REMEDIATION_SOURCE_RECORD_TYPES",
    "REMEDIATION_SOURCE_RECORD_SCHEMAS",
    "REMEDIATION_EVIDENCE_IDENTITY_FIELDS",
    "REMEDIATION_SOURCE_RECORD_CONTENT_TYPE",
    "MAX_REMEDIATION_SOURCE_RECORD_BYTES",
    "REQUIRED_REMEDIATION_LINEAGE_FIELDS",
    "REMEDIATION_LINEAGE_REF_RECORD_TYPES",
    "REMEDIATION_TELEMETRY_SCHEMA_VERSION",
    "REMEDIATION_RELEASE_THRESHOLD_SCHEMA_VERSION",
    "REQUIRED_REMEDIATION_PHASE_LATENCIES",
    "REMEDIATION_ACTION_RISKS",
    "RemediationMatrixRow",
    "REMEDIATION_ROW_CATALOG",
    "REQUIRED_REMEDIATION_MATRIX_ROWS",
    "REMEDIATION_ROW_CATALOG_BY_ID",
    "remediation_catalog_document",
    "RemediationMatrixError",
    "derive_remediation_observation_from_source_records",
    "derive_remediation_row_evidence",
    "derive_remediation_telemetry",
    "validate_remediation_telemetry_schema",
    "derive_remediation_release_thresholds",
    "validate_remediation_evidence_artifact",
    "RemediationReleaseStatus",
    "evaluate_remediation_release",
    "REMEDIATION_RELEASE_EVIDENCE_ENV",
    "load_remediation_release_status",
]
