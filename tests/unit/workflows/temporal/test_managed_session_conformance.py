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
    MM883_COVERAGE_IDS,
    REQUIRED_MANAGED_SESSION_BEHAVIORS,
    ManagedSessionBehaviorSupport,
    ManagedSessionRuntimeCapabilities,
    build_managed_session_conformance_summary,
    codex_managed_session_capabilities,
    evaluate_managed_session_conformance,
    managed_session_capabilities_for_runtime,
    run_managed_session_conformance,
    unsupported_runtime_managed_session_capabilities,
)


def _supported(behavior: str) -> ManagedSessionBehaviorSupport:
    return ManagedSessionBehaviorSupport(
        behavior=behavior,
        supported=True,
        invocation=f"{behavior}-invocation",
        evidence=(f"{behavior}-evidence",),
    )


def _fully_supported_behaviors() -> tuple[ManagedSessionBehaviorSupport, ...]:
    return tuple(_supported(behavior) for behavior in REQUIRED_MANAGED_SESSION_BEHAVIORS)


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
        "checkpoint",
        "outbound_scan",
        "correlation",
    )


def test_codex_descriptor_conforms_and_is_truthfully_session_capable() -> None:
    capabilities = codex_managed_session_capabilities()

    report = evaluate_managed_session_conformance(capabilities)

    assert capabilities.runtime_id == "codex_cli"
    assert capabilities.control_actions == MANAGED_SESSION_CONTROL_ACTIONS
    assert report["sessionCapable"] is True
    assert report["claimTruthful"] is True
    assert report["result"] == "passed"
    assert report["capabilityGaps"] == []
    covered = {decision["behavior"] for decision in report["behaviorDecisions"]}
    assert covered == set(REQUIRED_MANAGED_SESSION_BEHAVIORS)
    assert all(
        decision["decision"] == "conforms"
        for decision in report["behaviorDecisions"]
    )


def test_codex_descriptor_declares_every_required_behavior_with_evidence() -> None:
    capabilities = codex_managed_session_capabilities()

    for behavior in REQUIRED_MANAGED_SESSION_BEHAVIORS:
        support = capabilities.behavior(behavior)
        assert support is not None, behavior
        assert support.supported is True
        assert support.invocation
        assert support.evidence


@pytest.mark.parametrize("runtime_id", ["claude", "claude_code"])
def test_unsupported_runtimes_report_capability_gaps_not_partial(runtime_id: str) -> None:
    capabilities = managed_session_capabilities_for_runtime(runtime_id)

    report = evaluate_managed_session_conformance(capabilities)

    # Truthful binary determination: not session-capable, and never "partial".
    assert report["sessionCapable"] is False
    assert report["sessionCapableClaim"] is False
    assert report["claimTruthful"] is True
    assert report["result"] == "passed"
    assert len(report["capabilityGaps"]) == len(REQUIRED_MANAGED_SESSION_BEHAVIORS)
    gap_behaviors = {gap["behavior"] for gap in report["capabilityGaps"]}
    assert gap_behaviors == set(REQUIRED_MANAGED_SESSION_BEHAVIORS)
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


def test_non_canonical_runtime_cannot_be_session_capable_even_if_fully_declared() -> None:
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
    misrepresented = unsupported_runtime_managed_session_capabilities("future_runtime").model_copy(
        update={"session_capable_claim": True}
    )

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
    assert set(result["capabilityGaps"]) == {"claude", "claude_code"}


def test_behavior_support_requires_invocation_and_evidence_when_supported() -> None:
    with pytest.raises(ValidationError, match="declares no invocation contract"):
        ManagedSessionBehaviorSupport(behavior="launch", supported=True, evidence=("e",))

    with pytest.raises(ValidationError, match="declares no evidence surfaces"):
        ManagedSessionBehaviorSupport(
            behavior="launch", supported=True, invocation="x"
        )


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
