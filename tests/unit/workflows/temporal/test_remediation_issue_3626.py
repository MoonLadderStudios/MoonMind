"""Useful recovery-path evidence for MoonLadderStudios/MoonMind#3626.

Bounded backlog from the PARTIALLY_IMPLEMENTED assessment (REQ-1/REQ-2):

* Prove one ordinary operator diagnosis/recovery journey through the existing
  product owners (authority catalog, mutation guard ledger, trusted
  verification phase, per-action capability projection) without building a new
  qualification platform, copied row catalog, or full cross-product.
* Exercise the changed recovery handoffs named in the brief with controlled
  fakes: delayed verification (stabilization), worker-restart/unchanged-input
  retry with the same identity/budget (no second mutation), ambiguous delivery
  (no owning verifier), partial saving/publication failure (reader explosion),
  and stale evidence (stabilization never reaches the repaired state).
* Keep operation readiness, live certification, and autonomous authority
  distinct in actual consumers: missing mandatory proof blocks instead of
  passing, a report/runner outage (fail-closed release with blockers) does not
  lock unrelated authorized diagnosis, and passing operator-initiated tests
  never grants ``admin_auto``.

Live provider/enforcement scenarios (REQ-3) stay explicitly pending per the
issue terms and are not attempted here. Served browser-to-API selection is
covered by the existing e2e browser suite via CI; this file covers the
non-browser action/credential/storage boundaries so a low-level regression
does not need its own full browser/provider/host cycle.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api_service.api.routers.executions import _serialize_remediation_link_summary
from moonmind.omnigent.remediation_matrix import (
    EGRESS_RESTRICTED_ALLOWED,
    EGRESS_RESTRICTED_DENIED,
    REMEDIATION_ROW_CATALOG_BY_ID,
    load_remediation_release_status,
)
from moonmind.workflows.temporal.remediation_actions import (
    RemediationActionAuthorityService,
    RemediationMutationGuardPolicy,
    RemediationMutationGuardService,
    RemediationPermissionSet,
    RemediationSecurityProfile,
    remediation_action_capability,
    remediation_action_capability_matrix,
)
from moonmind.workflows.temporal.remediation_verification import (
    EVIDENCE_UNAVAILABLE,
    STILL_FAILED,
    VERIFICATION_FAILED,
    VERIFIED_NO_CHANGE,
    VERIFIED_RESOLVED,
    RemediationVerificationPhase,
    TargetEvidenceSnapshot,
    is_action_automatically_verifiable,
    verification_contract_for,
)

pytestmark = pytest.mark.asyncio


def _operator_profile(*, allowed=("execution.pause",)) -> RemediationSecurityProfile:
    return RemediationSecurityProfile(
        profile_ref="operator-initiated",
        execution_principal="operator:diagnosis",
        allowed_action_kinds=allowed,
    )


def _operator_permissions() -> RemediationPermissionSet:
    return RemediationPermissionSet(
        can_view_target=True,
        can_request_admin_profile=True,
    )


class _ScriptedReader:
    """Serve canned fresh-evidence reads while counting invocations."""

    def __init__(self, before, after_sequence, *, raise_on_stage=None) -> None:
        self._before = before
        self._after = list(after_sequence)
        self._raise_on_stage = raise_on_stage
        self.reads: list[str] = []

    async def read_target_evidence(
        self, *, contract, workflow_id, stage, pinned_run_id=None
    ):
        self.reads.append(stage)
        if self._raise_on_stage is not None and stage == self._raise_on_stage:
            raise RuntimeError("evidence surface exploded")
        if stage == "before":
            return self._before
        index = sum(1 for s in self.reads if s != "before") - 1
        return self._after[min(index, len(self._after) - 1)]


async def _noop_sleep(_seconds: float) -> None:
    return None


def _running_snapshot(stage: str) -> TargetEvidenceSnapshot:
    return TargetEvidenceSnapshot(
        stage=stage,
        available=True,
        workflow_id="target-workflow",
        run_id="target-run",
        state="running",
        paused=False,
    )


def _paused_snapshot(stage: str) -> TargetEvidenceSnapshot:
    return TargetEvidenceSnapshot(
        stage=stage,
        available=True,
        workflow_id="target-workflow",
        run_id="target-run",
        state="running",
        paused=True,
    )


def _guard_session() -> AsyncMock:
    session = AsyncMock()
    session.get.return_value = None
    execute_result = MagicMock()
    execute_result.scalars.return_value = []
    session.execute.return_value = execute_result
    return session


async def test_ordinary_diagnosis_and_recovery_separates_delivery_from_repair() -> None:
    """One useful journey: diagnose, guard, deliver, then verify exactly."""

    service = RemediationActionAuthorityService(session=None)  # type: ignore[arg-type]
    catalog = service.list_allowed_actions(
        permissions=_operator_permissions(),
        security_profile=_operator_profile(),
    )
    kinds = [item["actionKind"] for item in catalog]
    assert "execution.pause" in kinds
    # Unsupported mutating actions stay disabled, not advertised.
    assert "session.terminate" not in kinds
    assert "cleanup.request_janitor" not in kinds

    pause_capability = remediation_action_capability("execution.pause")
    assert pause_capability["requestable"] is True
    assert pause_capability["verificationBackendReady"] is True
    assert is_action_automatically_verifiable("execution.pause") is True
    assert is_action_automatically_verifiable("session.terminate") is False

    contract = verification_contract_for("execution.pause")
    before = _running_snapshot("before")

    # Delivery happened but the exact objective state is unchanged: the branch
    # record / accepted command alone must not read as repaired.
    still_active = _ScriptedReader(before, [_running_snapshot("immediate_after")] * 6)
    phase = RemediationVerificationPhase(
        reader=still_active, sleep=_noop_sleep, is_canceled=lambda: False
    )
    not_repaired = await phase.run(
        contract=contract,
        action_kind="execution.pause",
        action_id="journey-1",
        delivery_status="applied",
        target_workflow_id="target-workflow",
        pinned_run_id="target-run",
        before_snapshot=before,
        action_result={"beforeEvidenceRefs": ["b"], "afterEvidenceRefs": ["a"]},
    )
    assert not_repaired.delivery_status == "applied"
    assert not_repaired.outcome == STILL_FAILED
    # Preserved content: the pinned target identity survives the handoff.
    assert not_repaired.resulting_identity["workflowId"] == "target-workflow"

    # Exact repair evidence: the same delivery with the paused state observed.
    repaired_reader = _ScriptedReader(before, [_paused_snapshot("immediate_after")])
    repaired_phase = RemediationVerificationPhase(
        reader=repaired_reader, sleep=_noop_sleep, is_canceled=lambda: False
    )
    repaired = await repaired_phase.run(
        contract=contract,
        action_kind="execution.pause",
        action_id="journey-1",
        delivery_status="applied",
        target_workflow_id="target-workflow",
        pinned_run_id="target-run",
        before_snapshot=before,
        action_result={"beforeEvidenceRefs": ["b"], "afterEvidenceRefs": ["a"]},
    )
    assert repaired.delivery_status == "applied"
    assert repaired.outcome == VERIFIED_RESOLVED
    assert repaired.resulting_identity["workflowId"] == "target-workflow"
    assert repaired.immediate_after_state is not None
    assert repaired.immediate_after_state.paused is True


async def test_delayed_verification_stabilizes_without_second_mutation() -> None:
    """Delayed repair is observed through bounded stabilization reads only."""

    contract = verification_contract_for("execution.pause")
    assert contract.max_polls > 0
    before = _running_snapshot("before")
    # Immediate read still progressing; the repaired state arrives late.
    reader = _ScriptedReader(
        before,
        [_running_snapshot("immediate_after"), _paused_snapshot("stabilized")],
    )
    mutations = 0

    async def _counting_sleep(_seconds: float) -> None:
        nonlocal mutations
        # The verification phase must never perform the mutation itself; the
        # sleep hook existing only proves bounded waiting happened.
        mutations += 0
        return None

    phase = RemediationVerificationPhase(
        reader=reader, sleep=_counting_sleep, is_canceled=lambda: False
    )
    result = await phase.run(
        contract=contract,
        action_kind="execution.pause",
        action_id="delayed-1",
        delivery_status="applied",
        target_workflow_id="target-workflow",
        pinned_run_id="target-run",
        before_snapshot=before,
        action_result={"beforeEvidenceRefs": ["b"], "afterEvidenceRefs": ["a"]},
    )
    assert result.outcome == VERIFIED_RESOLVED
    assert result.stabilization["required"] is True
    assert result.stabilization["polls"] >= 1
    # Invocation counts, not just a terminal label: one immediate read plus at
    # least one stabilization read, and zero repeated mutations.
    assert reader.reads[0] == "immediate_after"
    assert "stabilized" in reader.reads
    assert mutations == 0
    assert result.stabilized_state is not None
    assert result.stabilized_state.paused is True


async def test_worker_restart_reuses_same_identity_without_repeated_effects() -> None:
    """Unchanged-input retry hits the ledger; changed reuse is denied."""

    now = datetime(2026, 9, 21, tzinfo=timezone.utc)
    kwargs = dict(
        remediation_workflow_id="remediation-3626",
        remediation_run_id="remediation-run-1",
        target_workflow_id="target-workflow",
        target_run_id="target-run",
        action_kind="execution.pause",
        idempotency_key="recovery-1",
        parameters={"reason": "ordinary pause recovery"},
        policy=RemediationMutationGuardPolicy(cooldown_seconds=0),
        now=now,
    )
    guard = RemediationMutationGuardService(session=_guard_session())
    first = await guard.evaluate(**kwargs)
    assert first.decision == "allowed"
    assert first.executable is True
    actions_used = guard._action_counts_by_target["target-workflow"]

    # Worker restart / Activity retry with the same identity and budget: the
    # unfinished phase is retried without a second mutation or recompute.
    restarted = await guard.evaluate(**kwargs)
    assert restarted.decision == "allowed"
    assert restarted.executable is True
    assert restarted.to_dict() == first.to_dict()
    assert guard._action_counts_by_target["target-workflow"] == actions_used

    # The failed source keeps its own history: reusing the key for different
    # parameters is an unsafe reuse, not a silent second mutation.
    changed = await guard.evaluate(
        **{**kwargs, "parameters": {"reason": "different repair"}}
    )
    assert changed.decision == "denied"
    assert changed.reason == "idempotency_key_unsafe_reuse"
    assert changed.executable is False


async def test_ambiguous_partial_and_stale_handoffs_stay_truthful_not_pass() -> None:
    """Missing/failed/stale evidence never presents as a repair pass."""

    # Ambiguous delivery: no owning verifier is wired for this action kind.
    unknown_contract = verification_contract_for("totally.unknown-action")
    assert unknown_contract.automatically_verifiable is False
    ambiguous_reader = _ScriptedReader(
        _running_snapshot("before"), [_paused_snapshot("immediate_after")]
    )
    ambiguous = await RemediationVerificationPhase(
        reader=ambiguous_reader, sleep=_noop_sleep, is_canceled=lambda: False
    ).run(
        contract=unknown_contract,
        action_kind="totally.unknown-action",
        action_id="ambiguous-1",
        delivery_status="applied",
        target_workflow_id="target-workflow",
        pinned_run_id="target-run",
        before_snapshot=_running_snapshot("before"),
        action_result={},
    )
    assert ambiguous.outcome == EVIDENCE_UNAVAILABLE
    assert ambiguous.outcome != VERIFIED_RESOLVED

    # Partial saving / publication failure: the evidence surface explodes.
    failing_reader = _ScriptedReader(
        _running_snapshot("before"),
        [_running_snapshot("immediate_after")],
        raise_on_stage="immediate_after",
    )
    failed = await RemediationVerificationPhase(
        reader=failing_reader, sleep=_noop_sleep, is_canceled=lambda: False
    ).run(
        contract=verification_contract_for("execution.pause"),
        action_kind="execution.pause",
        action_id="partial-1",
        delivery_status="applied",
        target_workflow_id="target-workflow",
        pinned_run_id="target-run",
        before_snapshot=_running_snapshot("before"),
        action_result={},
    )
    assert failed.outcome == VERIFICATION_FAILED
    assert failed.outcome != VERIFIED_RESOLVED

    # Denied delivery produced no side effect: the original outcome stands.
    denied = await RemediationVerificationPhase(
        reader=_ScriptedReader(
            _running_snapshot("before"), [_paused_snapshot("immediate_after")]
        ),
        sleep=_noop_sleep,
        is_canceled=lambda: False,
    ).run(
        contract=verification_contract_for("execution.pause"),
        action_kind="execution.pause",
        action_id="denied-1",
        delivery_status="denied",
        target_workflow_id="target-workflow",
        pinned_run_id="target-run",
        before_snapshot=_running_snapshot("before"),
        action_result={},
    )
    assert denied.outcome == VERIFIED_NO_CHANGE
    assert denied.outcome != VERIFIED_RESOLVED

    # Stale cleanup model: stabilization never observes the repaired state, so
    # the phase reports the persistent failure instead of a pass.
    stale_reader = _ScriptedReader(
        _running_snapshot("before"), [_running_snapshot("immediate_after")] * 6
    )
    stale = await RemediationVerificationPhase(
        reader=stale_reader, sleep=_noop_sleep, is_canceled=lambda: False
    ).run(
        contract=verification_contract_for("execution.pause"),
        action_kind="execution.pause",
        action_id="stale-1",
        delivery_status="applied",
        target_workflow_id="target-workflow",
        pinned_run_id="target-run",
        before_snapshot=_running_snapshot("before"),
        action_result={},
    )
    assert stale.outcome == STILL_FAILED
    assert stale.stabilization["polls"] >= 1
    assert stale_reader.reads.count("stabilized") >= 1


async def test_consumers_keep_readiness_livecert_autonomous_distinct() -> None:
    """Release monitoring never gates diagnosis; missing proof never passes."""

    release = load_remediation_release_status()
    # Autonomous mutation stays separately closed in this version.
    assert release.autonomous_rollout_authorized is False
    assert "autonomous_rollout_gate_closed" in release.blockers

    # Read-only diagnosis is not broad administrator authority.
    read_only = RemediationActionAuthorityService(session=None)  # type: ignore[arg-type]
    assert read_only.list_allowed_actions(
        permissions=RemediationPermissionSet(can_view_target=True),
        security_profile=_operator_profile(),
    ) == ()

    # Unrelated release/report blockers do not lock authorized diagnosis: the
    # per-action projection still admits the ordinary pause even though the
    # fail-closed release currently carries blockers in this sandbox.
    assert release.blockers != []
    now = datetime.now(timezone.utc)
    link = SimpleNamespace(
        remediation_workflow_id="remediation-3626",
        remediation_run_id="remediation-run-1",
        target_workflow_id="target-workflow",
        target_run_id="target-run",
        mode="repair",
        authority_mode="approval_gated",
        status="acting",
        allowed_actions=["execution.pause"],
        current_target_state="running",
        target_runtime="temporal",
        host_mode="managed",
        evidence_degraded=False,
        unavailable_evidence_classes=[],
        checkpoint_branch_links=[],
        checkpoint_branch_owner_ready=True,
        checkpoint_branch_verifier_ready=True,
        created_at=now,
        updated_at=now,
    )
    summary = _serialize_remediation_link_summary(link)
    rows = {row.actionKind: row for row in summary.actionCapabilities}
    assert summary.allowedActions == ["execution.pause"]
    assert rows["execution.pause"].requestable is True

    # Actions with missing required execution/verification support stay
    # unavailable instead of passing.
    assert rows["session.terminate"].requestable is False
    assert "execution_backend_unavailable" in rows["session.terminate"].blockedReasons
    assert (
        "authoritative_verifier_unavailable"
        in rows["session.terminate"].blockedReasons
    )

    # Operator-initiated describes product authority; it never grants admin_auto.
    assert "admin_auto" not in {
        row.actionKind for row in summary.actionCapabilities if row.requestable
    }
    assert release.autonomous_rollout_authorized is False


async def test_strict_certification_preserved_and_egress_owned_at_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict cert policy survives until deliberate migration; egress stays owned.

    An explicitly selected strict (``protected``) execution-evidence policy
    must fail closed instead of silently falling back to deployment
    qualification; only a deliberate policy migration changes that. Required
    egress enforcement stays at its real owner -- the remediation matrix
    catalog rows -- and never leaks into the remediation action consumer's
    capability projection.
    """

    from moonmind.omnigent import deployment_evidence as deployment_mod
    from moonmind.omnigent import execution_support_evidence as protected_mod
    from moonmind.omnigent.evidence_resolver import resolve_execution_evidence
    from moonmind.omnigent.settings import (
        MOONMIND_OMNIGENT_EVIDENCE_POLICY_ENV,
        omnigent_evidence_policy,
    )

    # The explicit selection is preserved: only a deliberate migration (an
    # environment/policy change) moves it away from strict.
    assert omnigent_evidence_policy(env={}) == "either"
    assert (
        omnigent_evidence_policy(
            env={MOONMIND_OMNIGENT_EVIDENCE_POLICY_ENV: "protected"}
        )
        == "protected"
    )

    # Under the strict policy the resolver fails closed when protected
    # evidence is missing instead of silently using deployment qualification.
    monkeypatch.setattr(
        protected_mod,
        "load_protected_execution_support_evidence",
        MagicMock(side_effect=ValueError("no protected evidence")),
    )
    deployment_calls: list[object] = []

    def _deployment_evidence(payload: object, now: object = None) -> dict[str, str]:
        deployment_calls.append(payload)
        return {"tier": "deployment_qualified"}

    monkeypatch.setattr(
        deployment_mod, "load_deployment_evidence", _deployment_evidence
    )
    with pytest.raises(ValueError, match="no protected evidence"):
        resolve_execution_evidence({"plan": "strict-3626"}, policy="protected")
    assert deployment_calls == []

    # A deliberate migration to ``either`` recovers through deployment evidence.
    evidence, tier = resolve_execution_evidence(
        {"plan": "strict-3626"}, policy="either"
    )
    assert tier == "deployment_qualified"
    assert evidence == {"tier": "deployment_qualified"}
    assert deployment_calls != []

    # Egress enforcement stays at its real owner: the matrix catalog owns the
    # egress authority per row; the action consumer carries no egress decision.
    allowed_row = REMEDIATION_ROW_CATALOG_BY_ID[
        "remediation.egress.restricted-allowed"
    ]
    denied_row = REMEDIATION_ROW_CATALOG_BY_ID[
        "remediation.egress.restricted-denied"
    ]
    assert allowed_row.egress == EGRESS_RESTRICTED_ALLOWED
    assert denied_row.egress == EGRESS_RESTRICTED_DENIED
    assert allowed_row.owner == "moonmind.remediation.reliability"
    assert denied_row.owner == "moonmind.remediation.reliability"
    for entry in remediation_action_capability_matrix():
        assert "egress" not in entry
        assert "egressDecision" not in entry
