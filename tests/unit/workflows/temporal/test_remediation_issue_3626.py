"""Ordinary diagnosis/recovery journey for GitHub issue #3626.

Reuses the existing remediation consumers (capability evaluation and the
trusted post-action verification phase) to prove one useful ordinary journey
with per-operation evidence instead of an all-matrix prerequisite. Covers
unchanged-input recovery without repeated effects, exact-objective repair
proof for a corrective Checkpoint Branch, consumer-level readiness
distinction, unrelated-outage isolation, and the closed autonomous gate.

Shared evidence reused (not duplicated): source-host removal/restore and
cumulative remediation journeys, the served browser-to-API product-path
journey, and the full release-matrix conformance suite.
"""

from __future__ import annotations

import pytest

from moonmind.workflows.temporal.remediation_actions import (
    RemediationCapabilityContext,
    remediation_action_capability,
)
from moonmind.workflows.temporal.remediation_verification import (
    STILL_FAILED,
    VERIFIED_NO_CHANGE,
    VERIFIED_RESOLVED,
    RemediationVerificationPhase,
    TargetEvidenceSnapshot,
    verification_contract_for,
)


def _snap(stage="immediate_after", **kw):
    return TargetEvidenceSnapshot(
        stage=stage, available=True, workflow_id="wf", **kw
    )


class _CountingReader:
    """Scripted evidence reader that records fresh-read invocation counts."""

    def __init__(self, before, after_sequence):
        self._before = before
        self._after = list(after_sequence)
        self.before_reads = 0
        self.after_reads = 0

    async def read_target_evidence(
        self, *, contract, workflow_id, stage, pinned_run_id=None
    ):
        if stage == "before":
            self.before_reads += 1
            return self._before
        self.after_reads += 1
        idx = min(self.after_reads - 1, len(self._after) - 1)
        return self._after[idx]


async def _noop_sleep(_seconds):
    return None


def _run_phase(action_kind, before, after_sequence, *, delivery="applied"):
    reader = _CountingReader(before, after_sequence)
    phase = RemediationVerificationPhase(
        reader=reader,
        sleep=_noop_sleep,
        is_canceled=lambda: False,
    )
    contract = verification_contract_for(action_kind)
    return reader, phase.run(
        contract=contract,
        action_kind=action_kind,
        action_id="act-3626",
        delivery_status=delivery,
        target_workflow_id="wf",
        pinned_run_id="run-0",
        before_snapshot=before,
        action_result={"beforeEvidenceRefs": ["b"], "afterEvidenceRefs": ["a"]},
    )


def test_ordinary_pause_journey_uses_per_operation_evidence() -> None:
    """The actual pause operation is requestable without passing every row."""
    context = RemediationCapabilityContext(
        target_runtime="temporal",
        target_state_eligible=True,
    )
    capability = remediation_action_capability("execution.pause", context=context)
    assert capability["requestable"] is True
    assert capability["blockedReasons"] == []
    # Per-operation proof: an unrelated disabled identity stays disabled while
    # the actual operation remains requestable (no all-matrix prerequisite).
    disabled = remediation_action_capability("session.clear", context=context)
    assert disabled["requestable"] is False
    assert "execution_backend_unavailable" in disabled["blockedReasons"]


@pytest.mark.asyncio
async def test_unchanged_input_recovery_reports_no_change_without_second_mutation() -> None:
    """Same run identity preserved: no repeated compute or second mutation."""
    before = _snap("before", state="executing", paused=True, run_id="r0")
    after = _snap(state="executing", paused=True, run_id="r0")
    reader, coro = _run_phase("execution.pause", before, [after])
    result = await coro
    assert result.outcome == VERIFIED_NO_CHANGE
    assert result.delivery_status == "applied"
    # Exact content preserved plus bounded fresh reads (no repeated effects).
    assert result.to_payload()["targetStates"]["immediateAfter"]["runId"] == "r0"
    assert reader.before_reads == 0  # before snapshot supplied by the caller
    assert 1 <= reader.after_reads <= 7  # immediate + bounded stabilization


@pytest.mark.asyncio
async def test_checkpoint_branch_record_is_not_repair_proof() -> None:
    """A branch delivery with an unresolved objective stays STILL_FAILED."""
    before = _snap("before", state="failed", close_status="failed", run_id="r")
    still_failed = _snap(state="failed", close_status="failed", run_id="r")
    reader, coro = _run_phase(
        "checkpoint_branch.create_from_remediation_context",
        before,
        [still_failed],
    )
    result = await coro
    assert result.outcome == STILL_FAILED
    assert result.delivery_status == "applied"
    assert reader.after_reads >= 1

    repaired = _snap(state="completed", close_status="completed", run_id="r")
    _, coro = _run_phase(
        "checkpoint_branch.create_from_remediation_context", before, [repaired]
    )
    assert (await coro).outcome == VERIFIED_RESOLVED


def test_readiness_certification_and_authority_stay_distinct() -> None:
    """Missing mandatory proof fails closed; it is never presented as a pass."""
    base = RemediationCapabilityContext(target_runtime="temporal")
    assert (
        remediation_action_capability("execution.pause", context=base)["requestable"]
        is True
    )
    execution_outage = RemediationCapabilityContext(
        target_runtime="temporal",
        execution_backend_readiness={"execution.pause": False},
    )
    paused = remediation_action_capability(
        "execution.pause", context=execution_outage
    )
    assert paused["requestable"] is False
    assert "execution_backend_unavailable" in paused["blockedReasons"]

    verification_outage = RemediationCapabilityContext(
        target_runtime="temporal",
        verification_backend_readiness={"execution.pause": False},
    )
    unverified = remediation_action_capability(
        "execution.pause", context=verification_outage
    )
    assert unverified["requestable"] is False
    assert "authoritative_verifier_unavailable" in unverified["blockedReasons"]

    # Credential/resource restriction owners stay distinct from readiness.
    denied = remediation_action_capability(
        "execution.pause",
        context=RemediationCapabilityContext(
            target_runtime="temporal",
            policy_allowed_action_kinds=(),
            caller_allowed_action_kinds=("execution.pause",),
        ),
    )
    assert denied["requestable"] is False
    assert "target_policy_denied" in denied["blockedReasons"]
    assert "execution_backend_unavailable" not in denied["blockedReasons"]


def test_unrelated_execution_outage_does_not_lock_sibling_diagnosis() -> None:
    """One backend outage disables only its own operation, not a sibling."""
    context = RemediationCapabilityContext(
        target_runtime="temporal",
        execution_backend_readiness={
            "execution.pause": False,
            "execution.resume": True,
        },
    )
    paused = remediation_action_capability("execution.pause", context=context)
    assert paused["requestable"] is False
    sibling = remediation_action_capability("execution.resume", context=context)
    assert sibling["requestable"] is True


def test_operator_initiated_readiness_never_mints_autonomous_authority() -> None:
    """Passing operator capability checks grant no admin_auto authority."""
    capability = remediation_action_capability(
        "execution.pause",
        context=RemediationCapabilityContext(target_runtime="temporal"),
    )
    assert capability["requestable"] is True
    # The capability contract carries readiness booleans only; there is no
    # authority-mode grant to mistake for autonomous mutation approval.
    assert "authorityMode" not in capability
    assert "admin_auto" not in str(capability)
