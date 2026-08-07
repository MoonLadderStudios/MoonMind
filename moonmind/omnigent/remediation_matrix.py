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
    REQUIRED_EVIDENCE_CHANNELS,
    ConformanceContractError,
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

# Remediation-specific telemetry the release status must carry (section 5).
REQUIRED_REMEDIATION_TELEMETRY_GROUPS = (
    "creationContextBuild",
    "evidenceDegradation",
    "approvalActionLock",
    "verification",
    "egress",
    "cancellation",
    "cumulativeAttemptExhaustion",
    "autonomousManualOrigin",
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
    host_modes: tuple[str, ...] = ALLOWED_HOST_MODES
    egress: str = EGRESS_NOT_APPLICABLE
    expected_outcome: str = OUTCOME_PASSED
    thresholds: tuple[str, ...] = ()

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

    @property
    def is_mutation(self) -> bool:
        """Rows that gate mutation or autonomous rollout carry a repair phase."""

        return self.gate in (GATE_MANUAL_MUTATION, GATE_AUTONOMOUS_ROLLOUT)


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
    ),
    RemediationMatrixRow(
        "remediation.host.static-on-demand-lifecycle",
        owner="moonmind.remediation.reliability",
        gate=GATE_MANUAL_MUTATION,
        evidence_kind="reliabilitySecurityEvidence",
        action_capability="host.lifecycle",
        verification_capability="verification.host-reconciled",
        authority_mode="approval_gated",
        ui_journey="workflow-detail.remediate.action",
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


def _validate_row_secret_scan(secret_scan: Any, *, row_id: str) -> None:
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
    missing = set(REQUIRED_EVIDENCE_CHANNELS) - set(secret_scan)
    if missing:
        raise RemediationMatrixError(
            f"row {row_id!r} secret scan is missing channels: {sorted(missing)}"
        )
    for channel in REQUIRED_EVIDENCE_CHANNELS:
        result = secret_scan.get(channel)
        evidence_ref = (
            result.get("evidenceRef") if isinstance(result, Mapping) else None
        )
        if (
            not isinstance(result, Mapping)
            or result.get("status") != "passed"
            or not isinstance(evidence_ref, str)
            or not evidence_ref.strip()
        ):
            raise RemediationMatrixError(
                f"row {row_id!r} secret scan channel {channel!r} lacks passing "
                "evidence"
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
        if ui_journey.get(assertion) is not True:
            raise RemediationMatrixError(
                f"row {row.row_id!r} UI journey is missing assertion {assertion!r}"
            )
    for marker in PROHIBITED_UI_JOURNEY_MARKERS:
        if ui_journey.get(marker):
            raise RemediationMatrixError(
                f"row {row.row_id!r} UI journey used prohibited authority {marker!r}"
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
        if not isinstance(result, Mapping) or result.get("within") is not True:
            raise RemediationMatrixError(
                f"row {row.row_id!r} threshold {key!r} was not observed within "
                "limits"
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
        _validate_row_secret_scan(entry.get("secretScan"), row_id=row.row_id)

        architecture = entry.get("architecture")
        if architecture not in architecture_set:
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
            "evidenceRef": self.evidence_ref,
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
    try:
        generated = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
        if generated.tzinfo is None:
            raise ValueError
        age = ((now or datetime.now(timezone.utc)) - generated).total_seconds()
        if age < 0 or age > MAX_EVIDENCE_AGE_SECONDS:
            blockers.append("remediation_release_evidence_stale")
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
    if not isinstance(telemetry, Mapping) or any(
        not isinstance(telemetry.get(group), Mapping) or not telemetry[group]
        for group in REQUIRED_REMEDIATION_TELEMETRY_GROUPS
    ):
        blockers.append("remediation_telemetry_required")

    thresholds = evidence.get("thresholds")
    threshold_results = (
        thresholds.get("results") if isinstance(thresholds, Mapping) else None
    )
    if (
        not isinstance(thresholds, Mapping)
        or thresholds.get("withinLimits") is not True
        or not isinstance(threshold_results, Mapping)
        or not threshold_results
        or any(result is not True for result in threshold_results.values())
    ):
        blockers.append("rollback_threshold_exceeded_or_missing")

    covered_rows = _verify_remediation_manifest(
        evidence,
        evidence_document_path=evidence_document_path,
        blockers=blockers,
    )

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
    )


def _verify_remediation_manifest(
    evidence: Mapping[str, Any],
    *,
    evidence_document_path: Path | None,
    blockers: list[str],
) -> frozenset[str]:
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
        return frozenset()
    if evidence_document_path is None:
        blockers.append("remediation_evidence_document_path_required")
        return frozenset()

    base = evidence_document_path.resolve().parent
    images = evidence.get("images")
    architectures = evidence.get("architectures")
    profile_version = evidence.get("profileVersion")
    profile_sha256 = evidence.get("profileSha256")
    policy_version = evidence.get("launchPolicyVersion")
    agent_profile_version = evidence.get("agentProfileVersion")
    remediation_policy_version = evidence.get("remediationPolicyVersion")

    observed_rows: set[str] = set()
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

    if split_kind:
        blockers.append("split_evidence_kind_rejected")
    if ownership_conflict:
        blockers.append("matrix_row_ownership_conflict")
    missing_kinds = set(REQUIRED_REMEDIATION_EVIDENCE_KINDS) - seen_kinds
    if missing_kinds:
        blockers.append("complete_evidence_kind_coverage_required")
    if observed_rows != set(REQUIRED_REMEDIATION_MATRIX_ROWS):
        blockers.append("matrix_row_coverage_incomplete")
    return frozenset(observed_rows)


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
    "REQUIRED_REMEDIATION_TELEMETRY_GROUPS",
    "REMEDIATION_REPAIR_OUTCOMES",
    "REMEDIATION_DELIVERY_STATUSES",
    "REQUIRED_UI_JOURNEY_ASSERTIONS",
    "PROHIBITED_UI_JOURNEY_MARKERS",
    "RemediationMatrixRow",
    "REMEDIATION_ROW_CATALOG",
    "REQUIRED_REMEDIATION_MATRIX_ROWS",
    "REMEDIATION_ROW_CATALOG_BY_ID",
    "RemediationMatrixError",
    "validate_remediation_evidence_artifact",
    "RemediationReleaseStatus",
    "evaluate_remediation_release",
    "REMEDIATION_RELEASE_EVIDENCE_ENV",
    "load_remediation_release_status",
]
