"""Trusted post-action verification for cross-workflow remediation actions.

MoonMind issue MoonLadderStudios/MoonMind#3622.

The cross-workflow administrative action path historically published a
``remediation.verification`` artifact by serialising whatever ``verification``
mapping the action adapter returned, or by defaulting to ``not_verified``. That
made *action delivery* and *remediation success* the same field: the Checkpoint
Branch adapter could report ``verified`` immediately after persisting a branch
graph even though the target objective was still failing.

This module makes post-action verification a first-class, authoritative phase.
It defines a typed, capability-aware verification contract per action kind, an
explicit verification phase that re-reads *fresh* canonical evidence after the
action (never the pre-action context cache), a bounded stabilisation strategy,
and a normalized outcome vocabulary that separates delivery from repair.

The phase runs at the service/activity boundary inside
``RemediationEvidenceToolService.execute_action``. It performs no side effects of
its own: it only reads fresh durable evidence and classifies it, so worker
restart, Activity retry, and Temporal replay re-run it safely without
duplicating the original action (idempotency of the action itself is enforced by
the owning adapters' stable idempotency keys).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db import models as db_models

# ---------------------------------------------------------------------------
# Normalized verification outcomes
# ---------------------------------------------------------------------------
# These are the trusted, normalized classifications the verification phase may
# publish. They separate the *repair* outcome from the action *delivery* status
# (which stays on the action result). The set distinguishes resolution, no
# change, persistent failure, regression, unavailable evidence, approval, and
# verifier failure, plus explicit cancellation.
VERIFIED_RESOLVED = "verified_resolved"
VERIFIED_NO_CHANGE = "verified_no_change"
STILL_FAILED = "still_failed"
REGRESSED = "regressed"
EVIDENCE_UNAVAILABLE = "evidence_unavailable"
APPROVAL_REQUIRED = "approval_required"
VERIFICATION_FAILED = "verification_failed"
CANCELED = "canceled"

REMEDIATION_VERIFICATION_OUTCOMES: frozenset[str] = frozenset(
    {
        VERIFIED_RESOLVED,
        VERIFIED_NO_CHANGE,
        STILL_FAILED,
        REGRESSED,
        EVIDENCE_UNAVAILABLE,
        APPROVAL_REQUIRED,
        VERIFICATION_FAILED,
        CANCELED,
    }
)

VerificationOutcome = Literal[
    "verified_resolved",
    "verified_no_change",
    "still_failed",
    "regressed",
    "evidence_unavailable",
    "approval_required",
    "verification_failed",
    "canceled",
]

VerifierKind = Literal["full_target_verifier", "targeted_health_check", "unavailable"]

_SNAPSHOT_STAGES: frozenset[str] = frozenset(
    {"before", "immediate_after", "stabilized"}
)

# Delivery statuses (from the action result) for which the action produced no
# side effect that could change the target. Verification of these reads fresh
# evidence and truthfully reports that the target's original outcome stands.
_NON_DELIVERED_STATUSES: frozenset[str] = frozenset(
    {"denied", "rejected", "precondition_failed"}
)

# Canonical lifecycle vocabulary (mirrors moonmind.statuses.*). Kept as literal
# strings here so this module stays free of enum coupling for JSON payloads.
_SUCCESS_STATES: frozenset[str] = frozenset({"completed"})
_FAILURE_STATES: frozenset[str] = frozenset({"failed", "canceled", "no_commit"})
_TERMINAL_CLOSE_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "canceled", "terminated", "timed_out"}
)
_CANCELED_CLOSE_STATUSES: frozenset[str] = frozenset(
    {"canceled", "terminated", "timed_out"}
)


class RemediationVerificationError(RuntimeError):
    """Raised when a verification request is structurally invalid."""


# ---------------------------------------------------------------------------
# Fresh evidence snapshots
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TargetEvidenceSnapshot:
    """One bounded reading of fresh canonical target evidence.

    ``available`` is ``False`` when the owning evidence surface could not be read
    (missing record, unwired subsystem verifier). ``degraded_reason`` then
    carries a bounded, non-sensitive explanation instead of a fabricated state.
    """

    stage: str
    available: bool
    workflow_id: str | None = None
    run_id: str | None = None
    state: str | None = None
    close_status: str | None = None
    paused: bool | None = None
    identities: Mapping[str, Any] = field(default_factory=dict)
    degraded_reason: str | None = None

    def __post_init__(self) -> None:
        if self.stage not in _SNAPSHOT_STAGES:
            raise RemediationVerificationError(
                f"Unsupported evidence snapshot stage: {self.stage}"
            )

    @property
    def terminal(self) -> bool:
        if self.close_status and self.close_status in _TERMINAL_CLOSE_STATUSES:
            return True
        return bool(self.state and self.state in (_SUCCESS_STATES | _FAILURE_STATES))

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "stage": self.stage,
            "available": self.available,
        }
        if self.workflow_id is not None:
            payload["workflowId"] = self.workflow_id
        if self.run_id is not None:
            payload["runId"] = self.run_id
        if self.state is not None:
            payload["state"] = self.state
        if self.close_status is not None:
            payload["closeStatus"] = self.close_status
        if self.paused is not None:
            payload["paused"] = self.paused
        if self.identities:
            payload["identities"] = dict(self.identities)
        if self.degraded_reason is not None:
            payload["degradedReason"] = self.degraded_reason
        return payload


def _unavailable_snapshot(stage: str, reason: str) -> TargetEvidenceSnapshot:
    return TargetEvidenceSnapshot(
        stage=stage, available=False, degraded_reason=reason
    )


# ---------------------------------------------------------------------------
# Typed verification contract + capability-aware registry
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class VerificationContract:
    """Declares how one action kind is verified after delivery.

    The contract is capability-aware: ``automatically_verifiable`` is only true
    when an owning verifier and readable evidence surface exist. An action is
    never advertised as automatically verifiable when no owning verifier exists.
    """

    action_kind: str
    evidence_owner: str
    target_resource_kind: str
    immediate_expected_state: str
    stabilization_seconds: float
    poll_interval_seconds: float
    terminal_timeout_seconds: float
    before_evidence_classes: tuple[str, ...]
    after_evidence_classes: tuple[str, ...]
    verifier_kind: VerifierKind
    automatically_verifiable: bool
    verifier: str

    @property
    def max_polls(self) -> int:
        if self.stabilization_seconds <= 0 or self.poll_interval_seconds <= 0:
            return 0
        # Bounded by the terminal timeout so verification never hangs.
        window = min(self.stabilization_seconds, self.terminal_timeout_seconds)
        return max(1, int(window / self.poll_interval_seconds))

    def to_payload(self) -> dict[str, Any]:
        return {
            "actionKind": self.action_kind,
            "evidenceOwner": self.evidence_owner,
            "targetResourceKind": self.target_resource_kind,
            "immediateExpectedState": self.immediate_expected_state,
            "stabilizationSeconds": self.stabilization_seconds,
            "pollIntervalSeconds": self.poll_interval_seconds,
            "terminalTimeoutSeconds": self.terminal_timeout_seconds,
            "beforeEvidenceClasses": list(self.before_evidence_classes),
            "afterEvidenceClasses": list(self.after_evidence_classes),
            "verifierKind": self.verifier_kind,
            "automaticallyVerifiable": self.automatically_verifiable,
            "verifier": self.verifier,
        }


def _execution_contract(action_kind: str, verifier: str, expected: str) -> VerificationContract:
    return VerificationContract(
        action_kind=action_kind,
        evidence_owner="temporal_execution_canonical_record",
        target_resource_kind="execution",
        immediate_expected_state=expected,
        stabilization_seconds=30.0,
        poll_interval_seconds=5.0,
        terminal_timeout_seconds=60.0,
        before_evidence_classes=("execution_and_steps",),
        after_evidence_classes=("execution_and_steps",),
        verifier_kind="full_target_verifier",
        automatically_verifiable=True,
        verifier=verifier,
    )


def _unavailable_contract(
    action_kind: str,
    *,
    evidence_owner: str,
    resource_kind: str,
    expected: str,
) -> VerificationContract:
    """A truthful contract for an action with no wired owning verifier."""

    return VerificationContract(
        action_kind=action_kind,
        evidence_owner=evidence_owner,
        target_resource_kind=resource_kind,
        immediate_expected_state=expected,
        stabilization_seconds=0.0,
        poll_interval_seconds=0.0,
        terminal_timeout_seconds=0.0,
        before_evidence_classes=(),
        after_evidence_classes=(),
        verifier_kind="unavailable",
        automatically_verifiable=False,
        verifier="unavailable",
    )


_VERIFICATION_CONTRACTS: dict[str, VerificationContract] = {
    "execution.pause": _execution_contract(
        "execution.pause", "execution_pause", "paused_or_non_progressing"
    ),
    "execution.resume": _execution_contract(
        "execution.resume", "execution_resume", "left_paused_or_no_op"
    ),
    "execution.cancel": _execution_contract(
        "execution.cancel", "execution_terminal", "canceled_or_terminal"
    ),
    "execution.force_terminate": _execution_contract(
        "execution.force_terminate", "execution_terminal", "terminated"
    ),
    "execution.request_rerun_same_workflow": _execution_contract(
        "execution.request_rerun_same_workflow",
        "execution_rerun",
        "run_identity_changed_or_no_op",
    ),
    "execution.start_fresh_rerun": _execution_contract(
        "execution.start_fresh_rerun",
        "execution_rerun",
        "fresh_execution_created",
    ),
    "checkpoint_branch.create_from_remediation_context": VerificationContract(
        action_kind="checkpoint_branch.create_from_remediation_context",
        evidence_owner="checkpoint_branch_graph_and_target_execution",
        target_resource_kind="checkpoint_branch",
        immediate_expected_state="branch_created_target_pending_verification",
        stabilization_seconds=0.0,
        poll_interval_seconds=0.0,
        terminal_timeout_seconds=30.0,
        before_evidence_classes=("execution_and_steps", "checkpoint_branches"),
        after_evidence_classes=("checkpoint_branches", "execution_and_steps"),
        verifier_kind="targeted_health_check",
        automatically_verifiable=True,
        verifier="checkpoint_branch",
    ),
    "session.interrupt_turn": _unavailable_contract(
        "session.interrupt_turn",
        evidence_owner="managed_session_control_plane",
        resource_kind="managed_session",
        expected="turn_interrupted",
    ),
    "session.clear": _unavailable_contract(
        "session.clear",
        evidence_owner="managed_session_control_plane",
        resource_kind="managed_session",
        expected="session_cleared",
    ),
    "session.cancel": _unavailable_contract(
        "session.cancel",
        evidence_owner="managed_session_control_plane",
        resource_kind="managed_session",
        expected="session_canceled",
    ),
    "session.terminate": _unavailable_contract(
        "session.terminate",
        evidence_owner="managed_session_control_plane",
        resource_kind="managed_session",
        expected="session_terminated",
    ),
    "session.restart_container": _unavailable_contract(
        "session.restart_container",
        evidence_owner="managed_session_control_plane",
        resource_kind="managed_session",
        expected="new_session_identity",
    ),
    "provider_profile.evict_stale_lease": _unavailable_contract(
        "provider_profile.evict_stale_lease",
        evidence_owner="omnigent_host_lease_registry",
        resource_kind="provider_profile_lease",
        expected="lease_released",
    ),
    "host.drain": _unavailable_contract(
        "host.drain",
        evidence_owner="omnigent_host_lease_registry",
        resource_kind="omnigent_host",
        expected="host_draining",
    ),
    "host.stop": _unavailable_contract(
        "host.stop",
        evidence_owner="omnigent_host_lease_registry",
        resource_kind="omnigent_host",
        expected="host_stopped",
    ),
    "host.restart": _unavailable_contract(
        "host.restart",
        evidence_owner="omnigent_host_lease_registry",
        resource_kind="omnigent_host",
        expected="host_healthy_new_generation",
    ),
    "host.remove": _unavailable_contract(
        "host.remove",
        evidence_owner="omnigent_host_lease_registry",
        resource_kind="omnigent_host",
        expected="host_absent",
    ),
    "host_lease.reconcile_stale": _unavailable_contract(
        "host_lease.reconcile_stale",
        evidence_owner="omnigent_host_lease_registry",
        resource_kind="host_lease",
        expected="lease_consistent",
    ),
    "workload.restart_helper_container": _unavailable_contract(
        "workload.restart_helper_container",
        evidence_owner="managed_runtime_control_plane",
        resource_kind="workload_container",
        expected="container_healthy",
    ),
    "workload.reap_orphan_container": _unavailable_contract(
        "workload.reap_orphan_container",
        evidence_owner="managed_runtime_control_plane",
        resource_kind="workload_container",
        expected="container_absent",
    ),
    "cleanup.request_janitor": _unavailable_contract(
        "cleanup.request_janitor",
        evidence_owner="cleanup_janitor_registry",
        resource_kind="cleanup",
        expected="cleanup_terminal",
    ),
    "cleanup.verify": _unavailable_contract(
        "cleanup.verify",
        evidence_owner="cleanup_janitor_registry",
        resource_kind="cleanup",
        expected="cleanup_consistent",
    ),
    "target.annotate": _unavailable_contract(
        "target.annotate",
        evidence_owner="temporal_execution_canonical_record",
        resource_kind="execution",
        expected="annotation_linked",
    ),
    "target.verify": _unavailable_contract(
        "target.verify",
        evidence_owner="temporal_execution_canonical_record",
        resource_kind="execution",
        expected="terminal_evidence_read",
    ),
}


def verification_contract_for(action_kind: str) -> VerificationContract:
    """Return the typed contract for an action kind.

    Unknown action kinds resolve to a truthful ``unavailable`` contract rather
    than raising, so the phase can still publish a bounded, non-fabricated
    verification result.
    """

    normalized = str(action_kind or "").strip()
    contract = _VERIFICATION_CONTRACTS.get(normalized)
    if contract is not None:
        return contract
    return _unavailable_contract(
        normalized or "unknown",
        evidence_owner="unknown",
        resource_kind="unknown",
        expected="unknown",
    )


def is_action_automatically_verifiable(action_kind: str) -> bool:
    """Capability-aware check: does an owning verifier exist for this action?"""

    return verification_contract_for(action_kind).automatically_verifiable


# ---------------------------------------------------------------------------
# Fresh evidence reader
# ---------------------------------------------------------------------------
class VerificationEvidenceReader(Protocol):
    """Read fresh canonical evidence for a verification snapshot stage."""

    async def read_target_evidence(
        self,
        *,
        contract: VerificationContract,
        workflow_id: str,
        stage: str,
        pinned_run_id: str | None = None,
    ) -> TargetEvidenceSnapshot: ...


class CanonicalRecordEvidenceReader:
    """Default reader that re-reads the authoritative execution record.

    It always issues a fresh read (``populate_existing=True``) so verification
    never reuses the pre-action identity-map state. Resource kinds with no wired
    owning verifier (managed session, host/lease, container, cleanup) return a
    truthful unavailable snapshot with a bounded reason.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def read_target_evidence(
        self,
        *,
        contract: VerificationContract,
        workflow_id: str,
        stage: str,
        pinned_run_id: str | None = None,
    ) -> TargetEvidenceSnapshot:
        if contract.verifier_kind == "unavailable":
            return _unavailable_snapshot(
                stage,
                (
                    f"No owning verifier is wired for {contract.evidence_owner}; "
                    f"{contract.action_kind} cannot be automatically verified."
                ),
            )
        record = await self._session.get(
            db_models.TemporalExecutionCanonicalRecord,
            workflow_id,
            populate_existing=True,
        )
        if record is None:
            return _unavailable_snapshot(
                stage,
                f"Target execution {workflow_id} was not found in canonical evidence.",
            )
        identities: dict[str, Any] = {}
        return TargetEvidenceSnapshot(
            stage=stage,
            available=True,
            workflow_id=record.workflow_id,
            run_id=record.run_id,
            state=_enum_value(record.state),
            close_status=_enum_value(record.close_status),
            paused=bool(record.paused),
            identities=identities,
        )


# ---------------------------------------------------------------------------
# Verification result
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class VerificationResult:
    """The typed, publishable output of the verification phase."""

    action_kind: str
    action_id: str
    outcome: str
    delivery_status: str
    reason: str
    contract: VerificationContract
    before_state: TargetEvidenceSnapshot
    immediate_after_state: TargetEvidenceSnapshot | None
    stabilized_state: TargetEvidenceSnapshot | None
    resulting_identity: Mapping[str, Any] = field(default_factory=dict)
    stabilization: Mapping[str, Any] = field(default_factory=dict)
    evidence_refs: Mapping[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        target_states: dict[str, Any] = {"before": self.before_state.to_payload()}
        if self.immediate_after_state is not None:
            target_states["immediateAfter"] = self.immediate_after_state.to_payload()
        if self.stabilized_state is not None:
            target_states["stabilized"] = self.stabilized_state.to_payload()
        payload: dict[str, Any] = {
            "schemaVersion": "v1",
            "actionKind": self.action_kind,
            "actionId": self.action_id,
            # Repair verification outcome — distinct from delivery.
            "status": self.outcome,
            "outcome": self.outcome,
            "deliveryStatus": self.delivery_status,
            "automaticallyVerifiable": self.contract.automatically_verifiable,
            "verifierKind": self.contract.verifier_kind,
            "evidenceOwner": self.contract.evidence_owner,
            "targetResourceKind": self.contract.target_resource_kind,
            "reason": self.reason,
            "contract": self.contract.to_payload(),
            "targetStates": target_states,
            "stabilization": dict(self.stabilization),
            "evidenceRefs": dict(self.evidence_refs),
        }
        if self.resulting_identity:
            payload["resultingIdentity"] = dict(self.resulting_identity)
        return payload

    def to_metadata(self) -> dict[str, Any]:
        """Compact metadata so the dashboard can render classification cheaply."""

        return {
            "verificationOutcome": self.outcome,
            "verificationDeliveryStatus": self.delivery_status,
            "verificationVerifierKind": self.contract.verifier_kind,
            "verificationAutomaticallyVerifiable": (
                self.contract.automatically_verifiable
            ),
        }


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------
def _is_active(snapshot: TargetEvidenceSnapshot) -> bool:
    if not snapshot.available:
        return False
    if snapshot.close_status and snapshot.close_status in _TERMINAL_CLOSE_STATUSES:
        return False
    return not (snapshot.state and snapshot.state in (_SUCCESS_STATES | _FAILURE_STATES))


def _stabilized(
    immediate: TargetEvidenceSnapshot | None,
    stabilized: TargetEvidenceSnapshot | None,
) -> TargetEvidenceSnapshot | None:
    if stabilized is not None and stabilized.available:
        return stabilized
    return immediate


def _classify_execution_pause(
    before: TargetEvidenceSnapshot,
    immediate: TargetEvidenceSnapshot | None,
    stabilized: TargetEvidenceSnapshot | None,
) -> tuple[str, str]:
    final = _stabilized(immediate, stabilized)
    if final is None or not final.available:
        return EVIDENCE_UNAVAILABLE, "Fresh target evidence was unavailable after pause."
    if final.paused is True:
        if before.paused is True:
            return VERIFIED_NO_CHANGE, "Target was already paused; pause was a no-op."
        return VERIFIED_RESOLVED, "Target reached a paused state after the pause action."
    if not _is_active(final):
        return VERIFIED_NO_CHANGE, "Target reached a terminal state; nothing to pause."
    return STILL_FAILED, "Target is still progressing after the pause action."


def _failed_terminal(snapshot: TargetEvidenceSnapshot | None) -> bool:
    if snapshot is None or not snapshot.available:
        return False
    if snapshot.close_status and snapshot.close_status in {"failed", "timed_out"}:
        return True
    return bool(snapshot.state and snapshot.state in {"failed", "no_commit"})


def _classify_execution_resume(
    before: TargetEvidenceSnapshot,
    immediate: TargetEvidenceSnapshot | None,
    stabilized: TargetEvidenceSnapshot | None,
) -> tuple[str, str]:
    final = _stabilized(immediate, stabilized)
    if final is None or not final.available:
        return EVIDENCE_UNAVAILABLE, "Fresh target evidence was unavailable after resume."
    # Regression: the target looked resumed (unpaused and progressing) right
    # after the action, then reached a failed terminal state during stabilization.
    if (
        immediate is not None
        and immediate.available
        and immediate.paused is False
        and _is_active(immediate)
        and _failed_terminal(final)
    ):
        return REGRESSED, "Target resumed then regressed to a failed terminal state."
    if final.paused is False:
        if _failed_terminal(final):
            return STILL_FAILED, "Target reached a failed state after the resume action."
        if before.paused is True:
            return VERIFIED_RESOLVED, "Target left the paused state after the resume action."
        return VERIFIED_NO_CHANGE, "Target was not paused; resume was a no-op."
    return STILL_FAILED, "Target is still paused after the resume action."


def _classify_execution_terminal(
    before: TargetEvidenceSnapshot,
    immediate: TargetEvidenceSnapshot | None,
    stabilized: TargetEvidenceSnapshot | None,
) -> tuple[str, str]:
    """Cancel / force-terminate: expect a terminal (canceled/terminated) state."""

    final = _stabilized(immediate, stabilized)
    if final is None or not final.available:
        return (
            EVIDENCE_UNAVAILABLE,
            "Fresh target evidence was unavailable after termination.",
        )

    def _is_terminated(snapshot: TargetEvidenceSnapshot) -> bool:
        if snapshot.close_status and snapshot.close_status in _CANCELED_CLOSE_STATUSES:
            return True
        return bool(snapshot.state and snapshot.state in _FAILURE_STATES)

    before_terminated = _is_terminated(before)
    if _is_terminated(final):
        if before_terminated:
            return VERIFIED_NO_CHANGE, "Target was already terminal; termination was a no-op."
        return VERIFIED_RESOLVED, "Target reached a terminated/canceled state."
    # It looked terminal immediately after but is active at stabilization.
    if immediate is not None and _is_terminated(immediate) and _is_active(final):
        return REGRESSED, "Target left its terminal state during stabilization."
    return STILL_FAILED, "Target is still active after the termination action."


def _classify_execution_rerun(
    before: TargetEvidenceSnapshot,
    immediate: TargetEvidenceSnapshot | None,
    stabilized: TargetEvidenceSnapshot | None,
) -> tuple[str, str]:
    final = _stabilized(immediate, stabilized)
    if final is None or not final.available:
        return EVIDENCE_UNAVAILABLE, "Fresh target evidence was unavailable after rerun."
    run_changed = bool(final.run_id and before.run_id and final.run_id != before.run_id)
    if not run_changed:
        # No new run identity: the rerun was accepted but did not change the run.
        if _is_active(final):
            return (
                VERIFIED_NO_CHANGE,
                "Rerun accepted; target run identity did not change.",
            )
        if final.state in _SUCCESS_STATES:
            return VERIFIED_NO_CHANGE, "Target already completed; rerun was a no-op."
        return STILL_FAILED, "Target remains in a failed terminal state after rerun."
    # A new run identity exists: classify by the resulting run health.
    if final.state in _SUCCESS_STATES:
        return VERIFIED_RESOLVED, "Rerun produced a new run that reached success."
    if _is_active(final):
        # Regression check: was it healthy immediately after, now failed?
        if immediate is not None and immediate.state in _SUCCESS_STATES and (
            final.state in _FAILURE_STATES
        ):
            return REGRESSED, "Rerun succeeded then regressed to a failed state."
        return (
            VERIFIED_NO_CHANGE,
            "Rerun produced a new run that is still in progress.",
        )
    return STILL_FAILED, "Rerun produced a new run that reached a failed state."


def _classify_checkpoint_branch(
    before: TargetEvidenceSnapshot,
    immediate: TargetEvidenceSnapshot | None,
    stabilized: TargetEvidenceSnapshot | None,
) -> tuple[str, str]:
    """Creating a Checkpoint Branch is a *candidate*, not a repair.

    Delivery (branch graph persisted) is reported separately as the delivery
    status. Repair verification re-reads the *target* execution: the target
    objective is only resolved if the target itself reached success. A branch
    runtime that completes while the target objective remains failed is
    ``still_failed`` — this is the exact defect this issue fixes.
    """

    final = _stabilized(immediate, stabilized)
    if final is None or not final.available:
        return (
            EVIDENCE_UNAVAILABLE,
            "Fresh target evidence was unavailable after branch creation.",
        )
    if final.state in _SUCCESS_STATES:
        return (
            VERIFIED_RESOLVED,
            "Target reached success; the checkpoint branch resolved the objective.",
        )
    return (
        STILL_FAILED,
        "Checkpoint branch created as a candidate; target objective is not yet "
        "resolved and requires downstream branch and target verification.",
    )


_CLASSIFIERS: dict[
    str,
    Callable[
        [
            TargetEvidenceSnapshot,
            TargetEvidenceSnapshot | None,
            TargetEvidenceSnapshot | None,
        ],
        tuple[str, str],
    ],
] = {
    "execution_pause": _classify_execution_pause,
    "execution_resume": _classify_execution_resume,
    "execution_terminal": _classify_execution_terminal,
    "execution_rerun": _classify_execution_rerun,
    "checkpoint_branch": _classify_checkpoint_branch,
}


# ---------------------------------------------------------------------------
# Resulting identity extraction
# ---------------------------------------------------------------------------
def _resulting_identity(
    contract: VerificationContract,
    action_result: Mapping[str, Any],
    final: TargetEvidenceSnapshot | None,
) -> dict[str, Any]:
    identity: dict[str, Any] = {}
    if final is not None and final.available:
        if final.run_id:
            identity["runId"] = final.run_id
        if final.workflow_id:
            identity["workflowId"] = final.workflow_id
    after_refs = action_result.get("afterEvidenceRefs")
    if isinstance(after_refs, Sequence) and not isinstance(after_refs, (str, bytes)):
        for ref in after_refs:
            ref_text = str(ref)
            if ref_text.startswith("checkpoint-branch:"):
                identity["branchId"] = ref_text.split(":", 1)[1]
            elif ref_text.startswith("managed-session:"):
                identity["sessionRef"] = ref_text.split(":", 1)[1]
            elif ":rerun-request:" in ref_text:
                identity.setdefault("rerunRequestRef", ref_text)
    return identity


# ---------------------------------------------------------------------------
# The verification phase
# ---------------------------------------------------------------------------
class RemediationVerificationPhase:
    """Run the trusted post-action verification phase for one action."""

    def __init__(
        self,
        *,
        reader: VerificationEvidenceReader,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        is_canceled: Callable[[], bool] | None = None,
        max_poll_cap: int = 6,
    ) -> None:
        self._reader = reader
        self._sleep = sleep or asyncio.sleep
        self._is_canceled = is_canceled or (lambda: False)
        self._max_poll_cap = max(0, int(max_poll_cap))

    async def read_before_snapshot(
        self,
        *,
        contract: VerificationContract,
        workflow_id: str,
        pinned_run_id: str | None = None,
    ) -> TargetEvidenceSnapshot:
        """Read the pre-action baseline evidence through the owning reader."""

        return await self._reader.read_target_evidence(
            contract=contract,
            workflow_id=workflow_id,
            stage="before",
            pinned_run_id=pinned_run_id,
        )

    async def run(
        self,
        *,
        contract: VerificationContract,
        action_kind: str,
        action_id: str,
        delivery_status: str,
        target_workflow_id: str,
        pinned_run_id: str | None,
        before_snapshot: TargetEvidenceSnapshot,
        action_result: Mapping[str, Any],
    ) -> VerificationResult:
        evidence_refs = {
            "before": list(_string_sequence(action_result.get("beforeEvidenceRefs"))),
            "immediateAfter": list(
                _string_sequence(action_result.get("afterEvidenceRefs"))
            ),
        }

        def _result(
            outcome: str,
            reason: str,
            *,
            immediate: TargetEvidenceSnapshot | None = None,
            stabilized: TargetEvidenceSnapshot | None = None,
            stabilization: Mapping[str, Any] | None = None,
        ) -> VerificationResult:
            final = _stabilized(immediate, stabilized)
            return VerificationResult(
                action_kind=action_kind,
                action_id=action_id,
                outcome=outcome,
                delivery_status=delivery_status,
                reason=reason,
                contract=contract,
                before_state=before_snapshot,
                immediate_after_state=immediate,
                stabilized_state=stabilized,
                resulting_identity=_resulting_identity(contract, action_result, final),
                stabilization=dict(stabilization or {"required": False}),
                evidence_refs=evidence_refs,
            )

        # Cancellation takes precedence: a canceled verification never claims a
        # repair outcome.
        if self._is_canceled():
            return _result(CANCELED, "Verification was canceled before evidence read.")

        # Approval-gated deliveries defer repair verification to the approval path.
        if delivery_status == "approval_required":
            return _result(
                APPROVAL_REQUIRED,
                "Action requires approval before it is applied; repair not yet "
                "verifiable.",
            )

        # Capability-aware: no owning verifier exists for this action kind.
        if not contract.automatically_verifiable:
            return _result(
                EVIDENCE_UNAVAILABLE,
                (
                    f"No owning verifier is wired for evidence owner "
                    f"'{contract.evidence_owner}'; {action_kind} reports a truthful "
                    "unavailable capability rather than a fabricated verification."
                ),
            )

        # A denied/rejected/precondition-failed action produced no side effect.
        if delivery_status in _NON_DELIVERED_STATUSES:
            return _result(
                VERIFIED_NO_CHANGE,
                f"Action was not applied (delivery status '{delivery_status}'); "
                "target evidence is unchanged and the original outcome stands.",
            )

        try:
            immediate = await self._reader.read_target_evidence(
                contract=contract,
                workflow_id=target_workflow_id,
                stage="immediate_after",
                pinned_run_id=pinned_run_id,
            )
        except Exception as exc:  # noqa: BLE001 - classify verifier failure truthfully
            return _result(
                VERIFICATION_FAILED,
                f"Verifier failed to read fresh evidence ({type(exc).__name__}).",
            )

        stabilized_snapshot: TargetEvidenceSnapshot | None = None
        stabilization = await self._stabilize(
            contract=contract,
            target_workflow_id=target_workflow_id,
            pinned_run_id=pinned_run_id,
            immediate=immediate,
        )
        stabilized_snapshot = stabilization.pop("_snapshot", None)  # type: ignore[assignment]
        if stabilization.get("canceled"):
            return _result(
                CANCELED,
                "Verification was canceled during bounded stabilization.",
                immediate=immediate,
                stabilized=stabilized_snapshot,
                stabilization=stabilization,
            )
        if stabilization.get("verifierFailed"):
            return _result(
                VERIFICATION_FAILED,
                "Verifier failed to read fresh evidence during stabilization.",
                immediate=immediate,
                stabilized=stabilized_snapshot,
                stabilization=stabilization,
            )

        final = _stabilized(immediate, stabilized_snapshot)
        if final is None or not final.available:
            return _result(
                EVIDENCE_UNAVAILABLE,
                (final.degraded_reason if final is not None else None)
                or "Fresh canonical evidence was unavailable after the action.",
                immediate=immediate,
                stabilized=stabilized_snapshot,
                stabilization=stabilization,
            )

        classifier = _CLASSIFIERS.get(contract.verifier)
        if classifier is None:
            return _result(
                VERIFICATION_FAILED,
                f"No classifier is registered for verifier '{contract.verifier}'.",
                immediate=immediate,
                stabilized=stabilized_snapshot,
                stabilization=stabilization,
            )
        try:
            outcome, reason = classifier(before_snapshot, immediate, stabilized_snapshot)
        except Exception as exc:  # noqa: BLE001
            return _result(
                VERIFICATION_FAILED,
                f"Verifier classification raised ({type(exc).__name__}).",
                immediate=immediate,
                stabilized=stabilized_snapshot,
                stabilization=stabilization,
            )
        return _result(
            outcome,
            reason,
            immediate=immediate,
            stabilized=stabilized_snapshot,
            stabilization=stabilization,
        )

    async def _stabilize(
        self,
        *,
        contract: VerificationContract,
        target_workflow_id: str,
        pinned_run_id: str | None,
        immediate: TargetEvidenceSnapshot,
    ) -> dict[str, Any]:
        max_polls = min(contract.max_polls, self._max_poll_cap)
        info: dict[str, Any] = {
            "required": max_polls > 0,
            "polls": 0,
            "stableReached": False,
            "timedOut": False,
            "canceled": False,
            "_snapshot": None,
        }
        if max_polls <= 0:
            info["stableReached"] = True
            return info
        # Already terminal: no need to poll further.
        if immediate.available and immediate.terminal:
            info["stableReached"] = True
            return info
        latest = immediate
        for poll in range(1, max_polls + 1):
            if self._is_canceled():
                info["canceled"] = True
                break
            await self._sleep(contract.poll_interval_seconds)
            info["polls"] = poll
            try:
                latest = await self._reader.read_target_evidence(
                    contract=contract,
                    workflow_id=target_workflow_id,
                    stage="stabilized",
                    pinned_run_id=pinned_run_id,
                )
            except Exception:  # noqa: BLE001
                info["verifierFailed"] = True
                break
            info["_snapshot"] = latest
            if latest.available and latest.terminal:
                info["stableReached"] = True
                break
        else:
            info["timedOut"] = True
        if info["_snapshot"] is None and latest is not immediate:
            info["_snapshot"] = latest
        return info


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _string_sequence(value: Any) -> list[str]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [str(item) for item in value]
    return []


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", value)
    text = str(enum_value).strip()
    return text or None


def snapshot_from_health(
    *,
    stage: str,
    workflow_id: str | None,
    run_id: str | None,
    state: str | None,
    close_status: str | None,
    paused: bool | None = None,
) -> TargetEvidenceSnapshot:
    """Build a snapshot from an already-read health record (e.g. the before read)."""

    available = bool(workflow_id)
    return TargetEvidenceSnapshot(
        stage=stage,
        available=available,
        workflow_id=workflow_id,
        run_id=run_id,
        state=state or None,
        close_status=close_status or None,
        paused=paused,
        degraded_reason=None if available else "Target health was not resolvable.",
    )


__all__ = [
    "APPROVAL_REQUIRED",
    "CANCELED",
    "EVIDENCE_UNAVAILABLE",
    "REGRESSED",
    "REMEDIATION_VERIFICATION_OUTCOMES",
    "STILL_FAILED",
    "VERIFICATION_FAILED",
    "VERIFIED_NO_CHANGE",
    "VERIFIED_RESOLVED",
    "CanonicalRecordEvidenceReader",
    "RemediationVerificationError",
    "RemediationVerificationPhase",
    "TargetEvidenceSnapshot",
    "VerificationContract",
    "VerificationEvidenceReader",
    "VerificationOutcome",
    "VerificationResult",
    "is_action_automatically_verifiable",
    "snapshot_from_health",
    "verification_contract_for",
]
