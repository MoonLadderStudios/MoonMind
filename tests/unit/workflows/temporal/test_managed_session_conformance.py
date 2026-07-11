"""Suite-level tests for the cross-runtime managed-session conformance suite (MM-883)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.schemas.managed_session_models import (
    MANAGED_SESSION_CONTROL_ACTIONS,
)
from moonmind.workflows.temporal.managed_session_conformance import (
    KNOWN_MANAGED_SESSION_RUNTIMES,
    MANAGED_SESSION_CONFORMANCE_SUITE_ID,
    MANAGED_SESSION_CONFORMANCE_REPORT_VERSION,
    MM883_COVERAGE_IDS,
    REQUIRED_MANAGED_SESSION_BEHAVIORS,
    ManagedSessionBehaviorSupport,
    ManagedSessionRuntimeCapabilities,
    RuntimeExecutionCapabilities,
    build_managed_session_conformance_summary,
    codex_managed_session_capabilities,
    evaluate_managed_session_conformance,
    managed_session_capabilities_for_runtime,
    migrate_managed_session_conformance_report,
    runtime_execution_capabilities_for_runtime,
    run_managed_session_conformance,
    unsupported_runtime_managed_session_capabilities,
)


def _supported(behavior: str) -> ManagedSessionBehaviorSupport:
    checkpoint_fields = {}
    if behavior in {
        "session_state_checkpoint",
        "step_workspace_checkpoint_capture",
        "step_workspace_checkpoint_restore",
    }:
        checkpoint_fields = {
            "owner": f"{behavior}-owner",
            "workspaceAuthorities": ("managed_runtime",),
            "checkpointKinds": (f"{behavior}-kind",),
            "idempotency": "stable idempotency key",
            "retryReplay": "same invocation on retry and replay",
            "securityBoundary": "owning worker resolves local state",
            "boundaryTest": f"test_{behavior}_boundary",
        }
    if behavior == "step_workspace_checkpoint_restore":
        checkpoint_fields["compatibleWorkspacePolicies"] = ("restore_pre_execution",)
    return ManagedSessionBehaviorSupport(
        behavior=behavior,
        supported=True,
        invocation=f"{behavior}-invocation",
        evidence=(f"{behavior}-evidence",),
        **checkpoint_fields,
    )


def _fully_supported_behaviors() -> tuple[ManagedSessionBehaviorSupport, ...]:
    return tuple(
        _supported(behavior) for behavior in REQUIRED_MANAGED_SESSION_BEHAVIORS
    )


def test_required_behaviors_match_acceptance_criteria() -> None:
    assert REQUIRED_MANAGED_SESSION_BEHAVIORS == (
        "launch",
        "turn_control",
        "interrupt",
        "reset_epoch",
        "resume",
        "terminate",
        "rate_limit",
        "no_progress",
        "session_state_checkpoint",
        "step_workspace_checkpoint_capture",
        "step_workspace_checkpoint_restore",
        "outbound_scan",
        "correlation",
    )


def test_codex_session_state_checkpoint_conforms_independently() -> None:
    capabilities = codex_managed_session_capabilities()

    report = evaluate_managed_session_conformance(capabilities)

    assert capabilities.runtime_id == "codex_cli"
    assert capabilities.control_actions == MANAGED_SESSION_CONTROL_ACTIONS
    assert report["sessionCapable"] is True
    assert report["claimTruthful"] is True
    assert report["result"] == "passed"
    assert report["reportSchemaVersion"] == MANAGED_SESSION_CONFORMANCE_REPORT_VERSION
    assert {gap["behavior"] for gap in report["capabilityGaps"]} == {
        "step_workspace_checkpoint_capture",
        "step_workspace_checkpoint_restore",
    }
    covered = {decision["behavior"] for decision in report["behaviorDecisions"]}
    assert covered == set(REQUIRED_MANAGED_SESSION_BEHAVIORS)
    session_checkpoint = capabilities.behavior("session_state_checkpoint")
    assert session_checkpoint is not None
    assert session_checkpoint.supported is True
    assert "latestCheckpointRef" in " ".join(session_checkpoint.evidence)


def test_codex_descriptor_declares_every_required_behavior_truthfully() -> None:
    capabilities = codex_managed_session_capabilities()

    for behavior in REQUIRED_MANAGED_SESSION_BEHAVIORS:
        support = capabilities.behavior(behavior)
        assert support is not None, behavior
        if support.supported:
            assert support.invocation
            assert support.evidence
        else:
            assert support.gap_reason


@pytest.mark.parametrize("runtime_id", ["claude", "claude_code"])
def test_unsupported_runtimes_report_capability_gaps_not_partial(
    runtime_id: str,
) -> None:
    capabilities = managed_session_capabilities_for_runtime(runtime_id)

    report = evaluate_managed_session_conformance(capabilities)

    # Truthful binary determination: not session-capable, and never "partial".
    assert report["sessionCapable"] is False
    assert report["sessionCapableClaim"] is False
    assert report["claimTruthful"] is True
    assert report["result"] == "passed"
    assert len(report["capabilityGaps"]) == len(REQUIRED_MANAGED_SESSION_BEHAVIORS) + 1
    gap_behaviors = {gap["behavior"] for gap in report["capabilityGaps"]}
    assert gap_behaviors == {
        *REQUIRED_MANAGED_SESSION_BEHAVIORS,
        "runtime_identity",
    }
    assert all(gap["reason"] for gap in report["capabilityGaps"])
    assert "partial" not in str(report).lower()


def test_partially_implemented_runtime_is_not_surfaced_as_session_capable() -> None:
    # A runtime that implements only a subset of behaviors must surface as
    # non-conforming with the exact gaps -- not as "partially" session-capable.
    behaviors: list[ManagedSessionBehaviorSupport] = []
    implemented = {"launch", "turn_control", "resume"}
    for behavior in REQUIRED_MANAGED_SESSION_BEHAVIORS:
        if behavior in implemented:
            behaviors.append(_supported(behavior))
        else:
            behaviors.append(
                ManagedSessionBehaviorSupport(
                    behavior=behavior,
                    supported=False,
                    gapReason=f"{behavior} not implemented yet",
                )
            )
    capabilities = ManagedSessionRuntimeCapabilities(
        runtimeId="codex_cli",
        runtimeFamily="codex",
        sessionCapableClaim=False,
        behaviors=tuple(behaviors),
    )

    report = evaluate_managed_session_conformance(capabilities)

    assert report["sessionCapable"] is False
    missing = set(REQUIRED_MANAGED_SESSION_BEHAVIORS) - implemented
    gap_behaviors = {gap["behavior"] for gap in report["capabilityGaps"]}
    assert gap_behaviors == missing


def test_runtime_falsely_claiming_session_capability_fails_truthfully() -> None:
    # claims session-capable but is missing the interrupt behavior entirely.
    behaviors = [
        _supported(behavior)
        for behavior in REQUIRED_MANAGED_SESSION_BEHAVIORS
        if behavior != "interrupt"
    ]
    capabilities = ManagedSessionRuntimeCapabilities(
        runtimeId="codex_cli",
        runtimeFamily="codex",
        sessionCapableClaim=True,
        behaviors=tuple(behaviors),
    )

    report = evaluate_managed_session_conformance(capabilities)

    assert report["sessionCapable"] is False
    assert report["claimTruthful"] is False
    assert report["result"] == "failed"
    assert {gap["behavior"] for gap in report["capabilityGaps"]} == {"interrupt"}


def test_non_canonical_runtime_cannot_be_session_capable_even_if_fully_declared() -> (
    None
):
    # Defense in depth: a runtime with no canonical managed-session id must never
    # be determined session-capable even if it declares every behavior.
    capabilities = ManagedSessionRuntimeCapabilities(
        runtimeId="future_runtime",
        runtimeFamily="future",
        sessionCapableClaim=True,
        behaviors=_fully_supported_behaviors(),
    )

    report = evaluate_managed_session_conformance(capabilities)

    assert report["canonicalRuntimeId"] is None
    assert report["sessionCapable"] is False
    assert report["result"] == "failed"
    assert any(
        gap["behavior"] == "runtime_identity" for gap in report["capabilityGaps"]
    )


def test_non_canonical_runtime_reports_identity_gap_alongside_checkpoint_gaps() -> None:
    behaviors = list(_fully_supported_behaviors())
    capture_index = next(
        index
        for index, support in enumerate(behaviors)
        if support.behavior == "step_workspace_checkpoint_capture"
    )
    behaviors[capture_index] = ManagedSessionBehaviorSupport(
        behavior="step_workspace_checkpoint_capture",
        supported=False,
        gapReason="workspace capture is not implemented",
    )
    capabilities = ManagedSessionRuntimeCapabilities(
        runtimeId="future_runtime",
        runtimeFamily="future",
        sessionCapableClaim=False,
        behaviors=tuple(behaviors),
    )

    report = evaluate_managed_session_conformance(capabilities)

    assert {gap["behavior"] for gap in report["capabilityGaps"]} == {
        "runtime_identity",
        "step_workspace_checkpoint_capture",
    }


def test_summary_only_surfaces_codex_as_session_capable() -> None:
    summary = build_managed_session_conformance_summary()

    assert summary["suite"] == MANAGED_SESSION_CONFORMANCE_SUITE_ID
    assert summary["overallResult"] == "passed"
    assert summary["requiredBehaviors"] == list(REQUIRED_MANAGED_SESSION_BEHAVIORS)
    assert summary["coverageIds"] == list(MM883_COVERAGE_IDS)
    assert summary["sessionCapableRuntimes"] == ["codex_cli"]
    assert summary["failedRuntimes"] == []
    reported_runtimes = {report["runtimeId"] for report in summary["reports"]}
    assert reported_runtimes == set(KNOWN_MANAGED_SESSION_RUNTIMES)


def test_summary_fails_when_a_runtime_misrepresents_session_capability() -> None:
    truthful_codex = codex_managed_session_capabilities()
    misrepresented = unsupported_runtime_managed_session_capabilities(
        "future_runtime"
    ).model_copy(update={"session_capable_claim": True})

    summary = build_managed_session_conformance_summary(
        capabilities=[truthful_codex, misrepresented]
    )

    assert summary["overallResult"] == "failed"
    assert "future_runtime" in summary["failedRuntimes"]
    assert summary["sessionCapableRuntimes"] == ["codex_cli"]


def test_run_managed_session_conformance_compact_result() -> None:
    result = run_managed_session_conformance()

    assert result["suite"] == MANAGED_SESSION_CONFORMANCE_SUITE_ID
    assert result["overallResult"] == "passed"
    assert result["sessionCapableRuntimes"] == ["codex_cli"]
    assert result["failedRuntimes"] == []
    # Non-session-capable runtimes report their gaps for operator action.
    assert set(result["capabilityGaps"]) == {"codex_cli", "claude", "claude_code"}


def test_behavior_support_requires_invocation_and_evidence_when_supported() -> None:
    with pytest.raises(ValidationError, match="declares no invocation contract"):
        ManagedSessionBehaviorSupport(
            behavior="launch", supported=True, evidence=("e",)
        )

    with pytest.raises(ValidationError, match="declares no evidence surfaces"):
        ManagedSessionBehaviorSupport(behavior="launch", supported=True, invocation="x")


def test_behavior_support_requires_gap_reason_when_unsupported() -> None:
    with pytest.raises(ValidationError, match="must declare an actionable gapReason"):
        ManagedSessionBehaviorSupport(behavior="launch", supported=False)


def test_capabilities_reject_duplicate_behaviors() -> None:
    with pytest.raises(ValidationError, match="behaviors must be unique"):
        ManagedSessionRuntimeCapabilities(
            runtimeId="codex_cli",
            sessionCapableClaim=True,
            behaviors=(_supported("launch"), _supported("launch")),
        )


def test_codex_workspace_capture_is_not_inferred_from_session_refs() -> None:
    capabilities = codex_managed_session_capabilities()

    session_state = capabilities.behavior("session_state_checkpoint")
    capture = capabilities.behavior("step_workspace_checkpoint_capture")

    assert session_state is not None and session_state.supported
    assert capture is not None and not capture.supported
    assert capture.invocation is None
    assert capture.checkpoint_kinds == ()


def test_capture_and_restore_cannot_reuse_one_invocation_or_evidence_claim() -> None:
    behaviors = list(_fully_supported_behaviors())
    capture_index = next(
        index
        for index, support in enumerate(behaviors)
        if support.behavior == "step_workspace_checkpoint_capture"
    )
    restore_index = next(
        index
        for index, support in enumerate(behaviors)
        if support.behavior == "step_workspace_checkpoint_restore"
    )
    shared_evidence = ("latestCheckpointRef",)
    behaviors[capture_index] = behaviors[capture_index].model_copy(
        update={"invocation": "WorkspaceCheckpointRequest", "evidence": shared_evidence}
    )
    behaviors[restore_index] = behaviors[restore_index].model_copy(
        update={"invocation": "WorkspaceCheckpointRequest", "evidence": shared_evidence}
    )

    with pytest.raises(ValidationError, match="distinct compatible invocations"):
        ManagedSessionRuntimeCapabilities(
            runtimeId="codex_cli",
            sessionCapableClaim=True,
            behaviors=tuple(behaviors),
        )


def test_supported_managed_capture_declares_managed_locator_authority() -> None:
    support = _supported("step_workspace_checkpoint_capture")

    assert support.workspace_authorities == ("managed_runtime",)
    assert support.checkpoint_kinds
    assert support.owner


def test_external_state_capture_does_not_imply_local_workspace_restore() -> None:
    behaviors = list(_fully_supported_behaviors())
    capture_index = next(
        index
        for index, support in enumerate(behaviors)
        if support.behavior == "step_workspace_checkpoint_capture"
    )
    restore_index = next(
        index
        for index, support in enumerate(behaviors)
        if support.behavior == "step_workspace_checkpoint_restore"
    )
    behaviors[capture_index] = behaviors[capture_index].model_copy(
        update={
            "workspace_authorities": ("external_provider",),
            "checkpoint_kinds": ("external_state_ref",),
        }
    )
    behaviors[restore_index] = ManagedSessionBehaviorSupport(
        behavior="step_workspace_checkpoint_restore",
        supported=False,
        gapReason="external provider state has no local materialization bridge",
    )
    capabilities = ManagedSessionRuntimeCapabilities(
        runtimeId="codex_cli",
        runtimeFamily="codex",
        sessionCapableClaim=True,
        behaviors=tuple(behaviors),
    )

    report = evaluate_managed_session_conformance(capabilities)

    decisions = {item["behavior"]: item for item in report["behaviorDecisions"]}
    assert decisions["step_workspace_checkpoint_capture"]["decision"] == "conforms"
    assert (
        decisions["step_workspace_checkpoint_restore"]["decision"] == "capability_gap"
    )


def test_unsupported_runtime_has_three_distinct_checkpoint_gaps() -> None:
    report = evaluate_managed_session_conformance(
        unsupported_runtime_managed_session_capabilities("claude")
    )

    checkpoint_gaps = {
        gap["behavior"]
        for gap in report["capabilityGaps"]
        if "checkpoint" in gap["behavior"]
    }
    assert checkpoint_gaps == {
        "session_state_checkpoint",
        "step_workspace_checkpoint_capture",
        "step_workspace_checkpoint_restore",
    }


def test_v1_report_migration_never_upgrades_generic_checkpoint_evidence() -> None:
    legacy = {
        "runtimeId": "codex_cli",
        "behaviorDecisions": [
            {
                "behavior": "checkpoint",
                "decision": "conforms",
                "supported": True,
                "invocation": "PublishCodexManagedSessionArtifactsRequest",
                "evidence": ["CodexManagedSessionSummary.latestCheckpointRef"],
                "gapReason": None,
            }
        ],
        "capabilityGaps": [],
    }

    migrated = migrate_managed_session_conformance_report(legacy)

    assert migrated["reportSchemaVersion"] == 2
    decisions = {item["behavior"]: item for item in migrated["behaviorDecisions"]}
    assert decisions["session_state_checkpoint"]["decision"] == "conforms"
    assert (
        decisions["step_workspace_checkpoint_capture"]["decision"] == "capability_gap"
    )
    assert (
        decisions["step_workspace_checkpoint_restore"]["decision"] == "capability_gap"
    )


def test_v1_summary_migration_updates_nested_reports_and_required_behaviors() -> None:
    legacy_report = {
        "runtimeId": "codex_cli",
        "behaviorDecisions": [
            {
                "behavior": "checkpoint",
                "decision": "conforms",
                "supported": True,
                "invocation": "PublishCodexManagedSessionArtifactsRequest",
                "evidence": ["latestCheckpointRef"],
                "gapReason": None,
            }
        ],
        "capabilityGaps": [],
    }
    legacy_summary = {
        "suite": MANAGED_SESSION_CONFORMANCE_SUITE_ID,
        "requiredBehaviors": ["launch", "checkpoint", "correlation"],
        "reports": [legacy_report],
    }

    migrated = migrate_managed_session_conformance_report(legacy_summary)

    assert migrated["reportSchemaVersion"] == 2
    assert migrated["requiredBehaviors"] == [
        "launch",
        "session_state_checkpoint",
        "step_workspace_checkpoint_capture",
        "step_workspace_checkpoint_restore",
        "correlation",
    ]
    nested = migrated["reports"][0]
    assert nested["reportSchemaVersion"] == 2
    assert len(nested["capabilityGaps"]) == 2


def test_v1_compact_result_migration_updates_runtime_gap_map() -> None:
    legacy = {
        "suite": MANAGED_SESSION_CONFORMANCE_SUITE_ID,
        "overallResult": "passed",
        "capabilityGaps": {
            "codex_cli": [
                {"behavior": "checkpoint", "reason": "legacy checkpoint gap"}
            ]
        },
    }

    migrated = migrate_managed_session_conformance_report(legacy)

    assert migrated["reportSchemaVersion"] == 2
    assert {
        gap["behavior"] for gap in migrated["capabilityGaps"]["codex_cli"]
    } == {
        "session_state_checkpoint",
        "step_workspace_checkpoint_capture",
        "step_workspace_checkpoint_restore",
    }


def test_checkpoint_claim_rejects_blank_checkpoint_kind() -> None:
    with pytest.raises(ValidationError, match="blank checkpointKind"):
        ManagedSessionBehaviorSupport(
            behavior="session_state_checkpoint",
            supported=True,
            invocation="SessionStateRequest",
            owner="session.activity",
            workspaceAuthorities=("managed_runtime",),
            checkpointKinds=("  ",),
            evidence=("sessionStateRef",),
            idempotency="stable request key",
            retryReplay="same invocation on retry and replay",
            securityBoundary="owning worker resolves local state",
            boundaryTest="test_session_state_boundary",
        )


def test_checkpoint_claim_requires_boundary_test_fixture() -> None:
    with pytest.raises(ValidationError, match="boundaryTest"):
        ManagedSessionBehaviorSupport(
            behavior="session_state_checkpoint",
            supported=True,
            invocation="SessionStateRequest",
            owner="session.activity",
            workspaceAuthorities=("managed_runtime",),
            checkpointKinds=("session_state_ref",),
            evidence=("sessionStateRef",),
            idempotency="stable request key",
            retryReplay="same request on retry",
            securityBoundary="managed runtime owner",
        )


def test_runtime_execution_capabilities_consume_precise_codex_results() -> None:
    capabilities = runtime_execution_capabilities_for_runtime("codex_cli")

    assert isinstance(capabilities, RuntimeExecutionCapabilities)
    assert capabilities.workspace_authority == "managed_runtime"
    assert capabilities.session_state_checkpoint == "conforms"
    assert capabilities.step_workspace_checkpoint_capture == "capability_gap"
    assert capabilities.step_workspace_checkpoint_restore == "capability_gap"
    assert capabilities.checkpoint_capture_activity is None
    assert capabilities.checkpoint_restore_activity is None
    assert capabilities.post_execution_checkpoint_criticality == "recoverability_only"
