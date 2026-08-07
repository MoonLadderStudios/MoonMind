"""Unit tests for the trusted post-action verification phase.

Issue MoonLadderStudios/MoonMind#3622. These tests exercise the normalized
verification outcomes, the capability-aware contract registry, and the bounded
stabilization / cancellation behavior of ``RemediationVerificationPhase`` without
a database — a scripted evidence reader drives the fresh-evidence reads.
"""

from __future__ import annotations

import pytest

from moonmind.workflows.temporal.remediation_verification import (
    APPROVAL_REQUIRED,
    CANCELED,
    EVIDENCE_UNAVAILABLE,
    REGRESSED,
    REMEDIATION_VERIFICATION_OUTCOMES,
    STILL_FAILED,
    VERIFICATION_FAILED,
    VERIFIED_NO_CHANGE,
    VERIFIED_RESOLVED,
    RemediationVerificationPhase,
    TargetEvidenceSnapshot,
    is_action_automatically_verifiable,
    verification_contract_for,
)


def _snap(stage="immediate_after", *, available=True, **kw):
    return TargetEvidenceSnapshot(
        stage=stage, available=available, workflow_id="wf", **kw
    )


class ScriptedReader:
    """Return ``before`` first, then successive ``after`` snapshots per read."""

    def __init__(self, before, after_sequence, *, raise_on_stage=None):
        self._before = before
        self._after = list(after_sequence)
        self._index = -1
        self._raise_on_stage = raise_on_stage

    async def read_target_evidence(self, *, contract, workflow_id, stage, pinned_run_id=None):
        if self._raise_on_stage is not None and stage == self._raise_on_stage:
            raise RuntimeError("evidence surface exploded")
        if stage == "before":
            return self._before
        self._index += 1
        idx = min(self._index, len(self._after) - 1)
        return self._after[idx]


async def _run(action_kind, before, after_sequence, *, delivery="applied",
               canceled=False, raise_on_stage=None, max_poll_cap=6):
    reader = ScriptedReader(before, after_sequence, raise_on_stage=raise_on_stage)
    canceled_holder = {"v": canceled}
    phase = RemediationVerificationPhase(
        reader=reader,
        sleep=_noop_sleep,
        is_canceled=lambda: canceled_holder["v"],
        max_poll_cap=max_poll_cap,
    )
    contract = verification_contract_for(action_kind)
    return await phase.run(
        contract=contract,
        action_kind=action_kind,
        action_id="act-1",
        delivery_status=delivery,
        target_workflow_id="wf",
        pinned_run_id="run-0",
        before_snapshot=before,
        action_result={"beforeEvidenceRefs": ["b"], "afterEvidenceRefs": ["a"]},
    )


async def _noop_sleep(_seconds):
    return None


# --------------------------------------------------------------------------
# Registry / capability awareness
# --------------------------------------------------------------------------
def test_contract_registry_is_capability_aware():
    assert is_action_automatically_verifiable("execution.pause") is True
    assert is_action_automatically_verifiable("execution.cancel") is True
    assert is_action_automatically_verifiable(
        "checkpoint_branch.create_from_remediation_context"
    ) is True
    # No owning verifier wired: never advertised as automatically verifiable.
    assert is_action_automatically_verifiable("session.clear") is False
    assert is_action_automatically_verifiable("host.stop") is False
    assert is_action_automatically_verifiable("workload.reap_orphan_container") is False
    assert is_action_automatically_verifiable("cleanup.request_janitor") is False
    # Unknown action kinds resolve to a truthful unavailable contract.
    contract = verification_contract_for("totally.unknown")
    assert contract.verifier_kind == "unavailable"
    assert contract.automatically_verifiable is False


def test_contract_payload_declares_required_fields():
    contract = verification_contract_for("execution.cancel")
    payload = contract.to_payload()
    for field in (
        "evidenceOwner",
        "targetResourceKind",
        "immediateExpectedState",
        "stabilizationSeconds",
        "terminalTimeoutSeconds",
        "beforeEvidenceClasses",
        "afterEvidenceClasses",
        "verifierKind",
        "automaticallyVerifiable",
    ):
        assert field in payload


# --------------------------------------------------------------------------
# Each normalized outcome
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pause_verified_resolved():
    before = _snap("before", state="executing", paused=False, run_id="r")
    after = _snap(state="executing", paused=True, run_id="r")
    result = await _run("execution.pause", before, [after])
    assert result.outcome == VERIFIED_RESOLVED
    assert result.delivery_status == "applied"


@pytest.mark.asyncio
async def test_pause_no_change_when_already_paused():
    before = _snap("before", state="executing", paused=True, run_id="r")
    after = _snap(state="executing", paused=True, run_id="r")
    result = await _run("execution.pause", before, [after])
    assert result.outcome == VERIFIED_NO_CHANGE


@pytest.mark.asyncio
async def test_pause_still_failed_when_still_progressing():
    before = _snap("before", state="executing", paused=False, run_id="r")
    after = _snap(state="executing", paused=False, run_id="r")
    result = await _run("execution.pause", before, [after])
    assert result.outcome == STILL_FAILED


@pytest.mark.asyncio
async def test_cancel_verified_resolved_when_terminal():
    before = _snap("before", state="executing", run_id="r")
    after = _snap(state="canceled", close_status="canceled", run_id="r")
    result = await _run("execution.cancel", before, [after])
    assert result.outcome == VERIFIED_RESOLVED


@pytest.mark.asyncio
async def test_cancel_still_failed_when_active():
    before = _snap("before", state="executing", run_id="r")
    after = _snap(state="executing", run_id="r")
    result = await _run("execution.cancel", before, [after])
    assert result.outcome == STILL_FAILED
    # Bounded stabilization ran and timed out rather than hanging.
    assert result.stabilization["required"] is True


@pytest.mark.asyncio
async def test_resume_regressed_when_resumed_then_failed():
    # Resumed (unpaused, progressing) immediately after, then failed during
    # bounded stabilization -> regressed.
    before = _snap("before", state="executing", paused=True, run_id="r")
    immediate = _snap("immediate_after", state="executing", paused=False, run_id="r")
    stabilized = _snap("stabilized", state="failed", close_status="failed", run_id="r")
    result = await _run("execution.resume", before, [immediate, stabilized])
    assert result.outcome == REGRESSED


@pytest.mark.asyncio
async def test_rerun_verified_resolved_on_new_run_success():
    before = _snap("before", state="failed", close_status="failed", run_id="r0")
    after = _snap(state="completed", close_status="completed", run_id="r1")
    result = await _run("execution.request_rerun_same_workflow", before, [after])
    assert result.outcome == VERIFIED_RESOLVED
    assert result.resulting_identity.get("runId") == "r1"


@pytest.mark.asyncio
async def test_rerun_no_change_when_run_identity_unchanged():
    before = _snap("before", state="executing", run_id="r0")
    after = _snap(state="executing", run_id="r0")
    result = await _run("execution.request_rerun_same_workflow", before, [after])
    assert result.outcome == VERIFIED_NO_CHANGE


@pytest.mark.asyncio
async def test_checkpoint_branch_still_failed_when_target_objective_unresolved():
    # Branch runtime "completes" (delivery applied) but the target objective is
    # still failed — this is the exact defect the issue fixes.
    before = _snap("before", state="failed", close_status="failed", run_id="r")
    after = _snap(state="failed", close_status="failed", run_id="r")
    result = await _run(
        "checkpoint_branch.create_from_remediation_context", before, [after]
    )
    assert result.outcome == STILL_FAILED


@pytest.mark.asyncio
async def test_checkpoint_branch_verified_resolved_when_target_completed():
    before = _snap("before", state="failed", close_status="failed", run_id="r")
    after = _snap(state="completed", close_status="completed", run_id="r")
    result = await _run(
        "checkpoint_branch.create_from_remediation_context", before, [after]
    )
    assert result.outcome == VERIFIED_RESOLVED


@pytest.mark.asyncio
async def test_evidence_unavailable_when_fresh_read_missing():
    before = _snap("before", state="executing", run_id="r")
    after = TargetEvidenceSnapshot(
        stage="immediate_after", available=False, degraded_reason="record vanished"
    )
    result = await _run("execution.pause", before, [after])
    assert result.outcome == EVIDENCE_UNAVAILABLE
    assert result.reason


@pytest.mark.asyncio
async def test_evidence_unavailable_for_unwired_capability():
    before = _snap("before", state="executing", run_id="r")
    after = _snap(state="executing", run_id="r")
    result = await _run("session.clear", before, [after])
    assert result.outcome == EVIDENCE_UNAVAILABLE
    assert result.contract.automatically_verifiable is False


@pytest.mark.asyncio
async def test_approval_required_delivery_defers_verification():
    before = _snap("before", state="executing", run_id="r")
    result = await _run(
        "execution.pause", before, [before], delivery="approval_required"
    )
    assert result.outcome == APPROVAL_REQUIRED


@pytest.mark.asyncio
async def test_verification_failed_when_reader_raises():
    before = _snap("before", state="executing", run_id="r")
    result = await _run(
        "execution.pause", before, [before], raise_on_stage="immediate_after"
    )
    assert result.outcome == VERIFICATION_FAILED


@pytest.mark.asyncio
async def test_canceled_before_evidence_read():
    before = _snap("before", state="executing", run_id="r")
    result = await _run("execution.pause", before, [before], canceled=True)
    assert result.outcome == CANCELED


@pytest.mark.asyncio
async def test_not_applied_delivery_reports_no_change():
    before = _snap("before", state="failed", close_status="failed", run_id="r")
    result = await _run("execution.cancel", before, [before], delivery="denied")
    assert result.outcome == VERIFIED_NO_CHANGE


@pytest.mark.asyncio
async def test_payload_separates_delivery_and_repair_and_records_states():
    before = _snap("before", state="executing", paused=False, run_id="r")
    after = _snap(state="executing", paused=True, run_id="r")
    result = await _run("execution.pause", before, [after])
    payload = result.to_payload()
    assert payload["deliveryStatus"] == "applied"
    assert payload["outcome"] == VERIFIED_RESOLVED
    assert payload["status"] == VERIFIED_RESOLVED
    assert "before" in payload["targetStates"]
    assert "immediateAfter" in payload["targetStates"]
    metadata = result.to_metadata()
    assert metadata["verificationOutcome"] == VERIFIED_RESOLVED
    assert metadata["verificationDeliveryStatus"] == "applied"


def test_all_outcomes_are_in_the_normalized_set():
    for outcome in (
        VERIFIED_RESOLVED,
        VERIFIED_NO_CHANGE,
        STILL_FAILED,
        REGRESSED,
        EVIDENCE_UNAVAILABLE,
        APPROVAL_REQUIRED,
        VERIFICATION_FAILED,
        CANCELED,
    ):
        assert outcome in REMEDIATION_VERIFICATION_OUTCOMES
    assert len(REMEDIATION_VERIFICATION_OUTCOMES) == 8
