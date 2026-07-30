"""Verification-phase, bounded-termination, and summary-separation coverage.

Implementation reference: MoonLadderStudios/MoonMind#3512 — finish the
remediation verification phase, wall-clock budget enforcement, and the
separated immediate-repair / prevention / verification summary contract.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from moonmind.workflows.temporal.remediation_context import (
    REMEDIATION_VERIFICATION_RESOLUTIONS,
    build_remediation_final_summary,
    build_remediation_prevention_outcome,
    build_remediation_repair_decision,
    build_remediation_summary_block,
    build_remediation_verification_result,
    normalize_remediation_verification_resolution,
)
from moonmind.workflows.temporal.remediation_loop import (
    ConsumedRemediationBudgets,
    RemediationLoopPhase,
    RemediationLoopSpec,
    RemediationLoopState,
    decide_remediation_continuation,
    remediation_wall_clock_exhausted,
)
from moonmind.workflows.temporal.remediation_tools import (
    RemediationEvidenceToolService,
    RemediationTargetHealthSnapshot,
    _delivery_verification_payload,
)

_TS = datetime(2026, 7, 30, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# AC7 — maxWallClockSeconds is a durable, consumed budget                       #
# --------------------------------------------------------------------------- #


def _wall_clock_spec(*, max_wall_clock_seconds: int | None) -> RemediationLoopSpec:
    budgets: dict[str, object] = {"hardMaxAttempts": 10}
    if max_wall_clock_seconds is not None:
        budgets["maxWallClockSeconds"] = max_wall_clock_seconds
    return RemediationLoopSpec.model_validate(
        {
            "kind": "remediation_loop",
            "loopId": "loop",
            "remediationTool": {
                "type": "skill",
                "name": "auto",
                "inputs": {"instructions": "Fix gaps."},
            },
            "verificationTool": {
                "type": "skill",
                "name": "verify",
                "inputs": {"instructions": "Verify."},
            },
            "workspacePolicy": "continue_from_loop_head",
            "budgets": budgets,
            "terminalPolicy": {
                "fullyImplemented": "advance",
                "additionalWorkNeeded": "continue_when_allowed",
                "blocked": "stop",
                "noDetermination": "retry_evidence_or_stop",
                "failedUnrecoverable": "stop",
            },
            "sideEffectPolicy": "workflow_owned",
            "publicationPolicy": "evaluate_after_terminal",
        }
    )


def _continuation_state(*, attempts: int = 1) -> RemediationLoopState:
    return RemediationLoopState(
        loopId="loop",
        attemptOrdinal=attempts,
        phase=RemediationLoopPhase.CONTINUATION_DECIDING,
        consumedBudgets=ConsumedRemediationBudgets(attempts=attempts),
        loopStartedAt="2026-07-30T00:00:00+00:00",
    )


def test_wall_clock_helper_only_bounds_configured_and_known_elapsed() -> None:
    spec = _wall_clock_spec(max_wall_clock_seconds=100)
    assert remediation_wall_clock_exhausted(spec=spec, elapsed_wall_clock_seconds=None) is False
    assert remediation_wall_clock_exhausted(spec=spec, elapsed_wall_clock_seconds=99) is False
    assert remediation_wall_clock_exhausted(spec=spec, elapsed_wall_clock_seconds=100) is True
    unbounded = _wall_clock_spec(max_wall_clock_seconds=None)
    assert (
        remediation_wall_clock_exhausted(
            spec=unbounded, elapsed_wall_clock_seconds=10**9
        )
        is False
    )


def test_wall_clock_budget_stops_additional_work_before_attempts_exhaust() -> None:
    spec = _wall_clock_spec(max_wall_clock_seconds=100)
    stopped = decide_remediation_continuation(
        spec=spec,
        state=_continuation_state(),
        verdict="ADDITIONAL_WORK_NEEDED",
        gate_result_ref="artifact://gate/latest",
        elapsed_wall_clock_seconds=250,
    )
    assert stopped.continue_loop is False
    assert stopped.reason == "wall_clock_budget_exhausted"
    assert stopped.next_phase == RemediationLoopPhase.STOPPED_REMAINING_WORK

    within = decide_remediation_continuation(
        spec=spec,
        state=_continuation_state(),
        verdict="ADDITIONAL_WORK_NEEDED",
        gate_result_ref="artifact://gate/latest",
        elapsed_wall_clock_seconds=10,
    )
    assert within.continue_loop is True
    assert within.reason == "verification_requested_remediation"


def test_loop_started_at_is_carried_state() -> None:
    state = _continuation_state()
    dumped = state.model_dump(by_alias=True, mode="json")
    assert dumped["loopStartedAt"] == "2026-07-30T00:00:00+00:00"
    restored = RemediationLoopState.model_validate(dumped)
    assert restored.loop_started_at == state.loop_started_at


# --------------------------------------------------------------------------- #
# AC3 — verification is a first-class phase with fresh-evidence resolutions     #
# --------------------------------------------------------------------------- #


def test_verification_resolution_vocabulary_is_complete() -> None:
    assert REMEDIATION_VERIFICATION_RESOLUTIONS == {
        "verified_resolved",
        "verified_no_change",
        "still_failed",
        "regressed",
        "evidence_unavailable",
        "approval_required",
        "verification_failed",
    }
    assert normalize_remediation_verification_resolution("nonsense") == "verification_failed"
    assert normalize_remediation_verification_resolution("regressed") == "regressed"


def test_verification_result_links_action_target_and_fresh_evidence() -> None:
    payload = build_remediation_verification_result(
        action_id="action-1",
        action_kind="execution.request_rerun_same_workflow",
        resolution="verified_resolved",
        target_workflow_id="target-wf",
        target_run_id="target-run",
        target_session_id="session-1",
        target_step_id="step-1",
        action_result_ref="art_result",
        fresh_evidence_ref="art_fresh",
        before_state_ref="art_before",
        after_immediate_state_ref="art_after",
        after_stabilization_state_ref="art_stable",
        verification_hint="Target reached authoritative terminal success.",
        timestamp=_TS,
    )
    assert payload["resolution"] == "verified_resolved"
    assert payload["phase"] == "complete"
    assert payload["preventionChange"] is False
    assert payload["target"] == {
        "workflowId": "target-wf",
        "runId": "target-run",
        "sessionId": "session-1",
        "stepId": "step-1",
    }
    assert payload["artifactRefs"] == {
        "actionResult": "art_result",
        "freshEvidence": "art_fresh",
        "beforeState": "art_before",
        "afterImmediateState": "art_after",
        "afterStabilizationState": "art_stable",
    }


@pytest.mark.parametrize(
    "resolution",
    ["verified_resolved", "verified_no_change", "still_failed", "regressed"],
)
def test_evidence_backed_resolutions_require_fresh_evidence(resolution: str) -> None:
    with pytest.raises(ValueError, match="fresh_evidence_ref"):
        build_remediation_verification_result(
            action_id="action-1",
            action_kind="execution.resume",
            resolution=resolution,
            target_workflow_id="target-wf",
            target_run_id="target-run",
            action_result_ref="art_result",
            timestamp=_TS,
        )


def test_evidence_unavailable_must_not_cite_fresh_evidence() -> None:
    ok = build_remediation_verification_result(
        action_id="action-1",
        action_kind="execution.resume",
        resolution="evidence_unavailable",
        target_workflow_id="target-wf",
        target_run_id="target-run",
        action_result_ref="art_result",
        timestamp=_TS,
    )
    assert ok["resolution"] == "evidence_unavailable"
    with pytest.raises(ValueError, match="must not cite fresh"):
        build_remediation_verification_result(
            action_id="action-1",
            action_kind="execution.resume",
            resolution="evidence_unavailable",
            target_workflow_id="target-wf",
            target_run_id="target-run",
            action_result_ref="art_result",
            fresh_evidence_ref="art_fresh",
            timestamp=_TS,
        )


def test_delivery_verification_payload_is_constrained_not_free_text() -> None:
    pending = _delivery_verification_payload(
        None, verification_required=True, action_kind="execution.resume", action_id="a1"
    )
    assert pending == {
        "phase": "pending",
        "status": "pending",
        "actionKind": "execution.resume",
        "actionId": "a1",
    }
    not_required = _delivery_verification_payload(
        {"status": "verified"},
        verification_required=False,
        action_kind="checkpoint_branch.create_from_remediation_context",
        action_id="a2",
    )
    assert not_required["phase"] == "not_required"
    assert not_required["status"] == "verified"


class _StubPublisher:
    def __init__(self) -> None:
        self.json_calls: list[dict[str, object]] = []
        self.annotation_calls: list[dict[str, object]] = []

    async def publish_json_artifact(self, **kwargs):
        self.json_calls.append(kwargs)
        return SimpleNamespace(artifact_id="art_verification")

    async def publish_target_annotation(self, **kwargs):
        self.annotation_calls.append(kwargs)
        return SimpleNamespace(artifact_id="art_annotation")


def _verify_service(publisher: _StubPublisher) -> RemediationEvidenceToolService:
    service = object.__new__(RemediationEvidenceToolService)
    service._lifecycle_publisher = publisher

    async def _load_link(remediation_workflow_id):
        return SimpleNamespace(
            remediation_workflow_id="remediation-wf",
            remediation_run_id="remediation-run",
            target_workflow_id="target-wf",
            target_run_id="target-run",
        )

    async def prepare_action_request(*, remediation_workflow_id, action_kind, principal):
        return SimpleNamespace(
            target=RemediationTargetHealthSnapshot(
                workflow_id="target-wf",
                pinned_run_id="target-run",
                current_run_id="target-run",
                state="completed",
                close_status="completed",
                title=None,
                summary=None,
                target_run_changed=False,
            )
        )

    service._load_link = _load_link
    service.prepare_action_request = prepare_action_request
    return service


@pytest.mark.asyncio
async def test_verify_action_publishes_first_class_resolution_and_annotates_target() -> None:
    publisher = _StubPublisher()
    service = _verify_service(publisher)

    result = await service.verify_action(
        remediation_workflow_id="remediation-wf",
        action_kind="execution.request_rerun_same_workflow",
        action_id="action-1",
        action_result_ref="art_result",
        resolution="verified_resolved",
        fresh_evidence_ref="art_fresh",
        verification_hint="Rerun reached terminal success.",
    )

    assert result["resolution"] == "verified_resolved"
    assert result["preventionChange"] is False
    assert result["artifactRefs"]["verification"] == "art_verification"
    assert result["artifactRefs"]["targetAnnotation"] == "art_annotation"
    # The verification artifact is published as a first-class remediation artifact.
    assert publisher.json_calls[0]["artifact_type"] == "remediation.verification"
    assert publisher.json_calls[0]["payload"]["resolution"] == "verified_resolved"
    # The target is annotated (supplemental) and its native artifacts preserved.
    annotation = publisher.annotation_calls[0]["payload"]
    assert annotation["metadata"]["nativeArtifactPolicy"] == "preserve"
    assert annotation["metadata"]["verificationResolution"] == "verified_resolved"


@pytest.mark.asyncio
async def test_prevention_change_verification_is_separate_and_unannotated() -> None:
    publisher = _StubPublisher()
    service = _verify_service(publisher)

    result = await service.verify_action(
        remediation_workflow_id="remediation-wf",
        action_kind="prevention.pull_request",
        action_id="prevention-1",
        action_result_ref="art_result",
        resolution="verified_resolved",
        fresh_evidence_ref="art_fresh",
        prevention_change=True,
    )

    assert result["preventionChange"] is True
    assert "targetAnnotation" not in result["artifactRefs"]
    # A prevention-change verification does not annotate the target as repaired.
    assert publisher.annotation_calls == []
    assert publisher.json_calls[0]["payload"]["preventionChange"] is True


@pytest.mark.asyncio
async def test_verify_action_is_reachable_through_the_mcp_registry() -> None:
    from moonmind.mcp.remediation_tool_registry import (
        RemediationToolExecutionContext,
        RemediationToolRegistry,
    )

    registry = RemediationToolRegistry()
    names = {tool.name for tool in registry.list_tools()}
    assert "remediation.verify_action" in names

    captured: dict[str, object] = {}

    class _RecordingService:
        async def verify_action(self, **kwargs):
            captured.update(kwargs)
            return {"resolution": kwargs["resolution"], "artifactRefs": {}}

    result = await registry.call_tool(
        tool="remediation.verify_action",
        arguments={
            "remediationWorkflowId": "remediation-wf",
            "actionKind": "execution.resume",
            "actionId": "action-1",
            "actionResultRef": "art_result",
            "resolution": "verified_resolved",
            "freshEvidenceRef": "art_fresh",
        },
        context=RemediationToolExecutionContext(
            service=_RecordingService(),  # type: ignore[arg-type]
            principal="user:owner",
        ),
    )

    assert result["resolution"] == "verified_resolved"
    assert captured["action_result_ref"] == "art_result"
    assert captured["fresh_evidence_ref"] == "art_fresh"
    assert captured["principal"] == "user:owner"


@pytest.mark.asyncio
async def test_verify_action_rejects_unknown_resolution() -> None:
    service = _verify_service(_StubPublisher())
    with pytest.raises(Exception, match="verification resolution"):
        await service.verify_action(
            remediation_workflow_id="remediation-wf",
            action_kind="execution.resume",
            action_id="action-1",
            action_result_ref="art_result",
            resolution="totally_made_up",
        )


# --------------------------------------------------------------------------- #
# AC8 — immediate repair, prevention, and verification report separately        #
# --------------------------------------------------------------------------- #


def _summary_block() -> dict[str, object]:
    return build_remediation_summary_block(
        target_workflow_id="target-wf",
        target_run_id="target-run",
        phase="verifying",
        mode="observe_then_act",
        authority_mode="admin_auto",
        resolution="resolved_after_action",
    )


def test_repair_decision_records_target_disposition() -> None:
    payload = build_remediation_repair_decision(
        target_workflow_id="target-wf",
        pinned_run_id="target-run",
        decision="attempted",
        decision_reason="Evidence-gated resume.",
        repair_outcome="repaired",
        target_disposition="resumed",
        action_request_ref="art_req",
        action_result_ref="art_res",
        verification_ref="art_ver",
    )
    assert payload["targetDisposition"] == "resumed"


def test_repaired_outcome_requires_a_progressing_disposition() -> None:
    with pytest.raises(ValueError, match="target disposition"):
        build_remediation_repair_decision(
            target_workflow_id="target-wf",
            pinned_run_id="target-run",
            decision="attempted",
            decision_reason="Attempted but target never resumed.",
            repair_outcome="repaired",
            target_disposition="remained_failed",
            action_request_ref="art_req",
            action_result_ref="art_res",
            verification_ref="art_ver",
        )


def test_reviewable_prevention_change_requires_separate_verification() -> None:
    with pytest.raises(ValueError, match="separate verification"):
        build_remediation_prevention_outcome(
            status="reviewable_change_created",
            root_cause_category="flaky_dependency",
            summary="Opened a fix PR.",
            pull_request_url="https://example.com/pr/1",
        )
    passed = build_remediation_prevention_outcome(
        status="reviewable_change_created",
        root_cause_category="flaky_dependency",
        summary="Opened a fix PR.",
        pull_request_url="https://example.com/pr/1",
        verification_status="passed",
        verification_ref="art_prevention_verify",
    )
    assert passed["verification"] == {
        "status": "passed",
        "verificationRef": "art_prevention_verify",
    }


def test_prevention_verification_passed_requires_ref() -> None:
    with pytest.raises(ValueError, match="verification_ref"):
        build_remediation_prevention_outcome(
            status="findings_reported",
            root_cause_category="config_drift",
            summary="Reported findings.",
            verification_status="passed",
        )


def test_final_summary_reports_repair_prevention_and_verification_separately() -> None:
    repair = build_remediation_repair_decision(
        target_workflow_id="target-wf",
        pinned_run_id="target-run",
        decision="skipped",
        decision_reason="Immediate repair unsafe; prevention only.",
        repair_outcome="not_attempted",
        target_disposition="remained_failed",
    )
    prevention = build_remediation_prevention_outcome(
        status="reviewable_change_created",
        root_cause_category="flaky_dependency",
        summary="Opened a fix PR.",
        pull_request_url="https://example.com/pr/1",
        verification_status="pending",
    )
    verification = {
        "resolution": "still_failed",
        "verificationRef": "art_verification",
    }
    final = build_remediation_final_summary(
        summary=_summary_block(),
        repair=repair,
        prevention=prevention,
        verification=verification,
        operator_follow_up=[
            "Review prevention PR #1",
            "Decide whether to rerun the target once the fix merges",
        ],
        lock_release="released",
    )

    # A prevention PR must not relabel the target as repaired.
    assert final["repair"]["repairOutcome"] == "not_attempted"
    assert final["repair"]["targetDisposition"] == "remained_failed"
    assert final["prevention"]["status"] == "reviewable_change_created"
    assert final["prevention"]["verification"]["status"] == "pending"
    assert final["verification"]["resolution"] == "still_failed"
    assert final["operatorFollowUp"] == [
        "Review prevention PR #1",
        "Decide whether to rerun the target once the fix merges",
    ]


def test_final_summary_rejects_unknown_verification_resolution() -> None:
    with pytest.raises(ValueError, match="verification.resolution"):
        build_remediation_final_summary(
            summary=_summary_block(),
            repair=build_remediation_repair_decision(
                target_workflow_id="target-wf",
                pinned_run_id="target-run",
                decision="skipped",
                decision_reason="No repair.",
                repair_outcome="not_attempted",
            ),
            prevention=build_remediation_prevention_outcome(
                status="no_reviewable_fix",
                root_cause_category="unknown",
                summary="No fix identified.",
            ),
            verification={"resolution": "made_up"},
            lock_release="not_held",
        )
