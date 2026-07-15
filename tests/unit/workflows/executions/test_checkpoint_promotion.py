import pytest
from pydantic import ValidationError
from moonmind.workflows.executions.checkpoint_promotion import (
    CheckpointPromotionHealth,
    FrozenGenerationUsage,
    ShadowRestoreRequest,
    bounded_checkpoint_metric_tags,
    evaluate_automatic_pause,
    evaluate_worker_drain,
)


def test_shadow_restore_requires_disposable_source_independent_destination() -> None:
    with pytest.raises(ValidationError, match="source-independent"):
        ShadowRestoreRequest(
            checkpointRef="artifact://checkpoint/1",
            checkpointDigest="sha256:abc",
            deploymentGeneration="generation-1",
            disposableWorkspace=True,
            sourceWorkspaceMounted=True,
            deleteWorkspaceAfterValidation=True,
        )


def test_critical_health_pauses_only_new_admissions() -> None:
    decision = evaluate_automatic_pause(
        CheckpointPromotionHealth(
            integrityFailures=1,
            restoreAttempts=10,
            restoreFailures=0,
        ),
        maximum_restore_failure_ratio=.05,
    )
    assert decision.pause_new_admissions is True
    assert decision.reason_code == "checkpoint_integrity_failure"
    assert decision.permit_in_flight_reconciliation is True


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("credentialInclusions", "credential_inclusion"),
        ("managedWorkspaceSandboxRoutes", "managed_workspace_sandbox_route"),
        ("sourceWorkspaceDependencies", "source_workspace_dependency"),
        ("routeReadinessDivergences", "route_readiness_divergence"),
        ("capabilityGenerationMismatches", "capability_generation_mismatch"),
        ("requiredJourneyMissing", "required_journey_missing"),
    ],
)
def test_all_zero_tolerance_conditions_pause_new_admissions(
    field: str, reason: str
) -> None:
    decision = evaluate_automatic_pause(
        CheckpointPromotionHealth.model_validate({field: 1}),
        maximum_restore_failure_ratio=.05,
    )
    assert decision.pause_new_admissions is True
    assert decision.reason_code == reason
    assert decision.permit_in_flight_reconciliation is True


def test_worker_generation_is_retained_until_frozen_histories_drain() -> None:
    blocked = evaluate_worker_drain(
        FrozenGenerationUsage(
            deploymentGeneration="generation-1",
            openRecoveryHistories=2,
            pendingRestorations=1,
        )
    )
    assert blocked.may_remove_worker_routes is False
    assert blocked.required_action == "retain_routes"
    drained = evaluate_worker_drain(
        FrozenGenerationUsage(
            deploymentGeneration="generation-1",
            openRecoveryHistories=0,
            pendingRestorations=0,
        )
    )
    assert drained.may_remove_worker_routes is True


def test_metric_tags_are_bounded_and_exclude_identifiers() -> None:
    tags = bounded_checkpoint_metric_tags(
        runtime_id="codex_cli", generation="generation-1", outcome="eligible"
    )
    assert tags == {
        "runtime": "codex_cli",
        "generation": "generation-1",
        "outcome": "eligible",
    }
    assert not ({"repository", "owner_id", "artifact_ref"} & tags.keys())


def test_metric_catalog_covers_required_checkpoint_series() -> None:
    from moonmind.workflows.executions.checkpoint_promotion import CHECKPOINT_METRIC_NAMES

    assert {
        "managed_checkpoint.capture_total",
        "managed_checkpoint.capture_success_total",
        "managed_checkpoint.capture_failure_total",
        "managed_checkpoint.restore_total",
        "managed_checkpoint.restore_success_total",
        "managed_checkpoint.restore_failure_total",
        "managed_checkpoint.restore_integrity_failure_total",
        "checkpoint_resume.false_positive_eligibility_total",
        "checkpoint_resume.duplicate_side_effect_total",
    } <= CHECKPOINT_METRIC_NAMES


def test_observability_rules_cover_every_zero_tolerance_pause_condition() -> None:
    from pathlib import Path

    rules = Path("deploy/observability/prometheus/rules.yaml").read_text()
    assert {
        "MoonMindCheckpointResumeIntegrityFailure",
        "MoonMindCheckpointResumeFalseEligibility",
        "MoonMindCheckpointResumeDuplicateSideEffect",
        "MoonMindCheckpointRestoreFailureRate",
        "MoonMindCheckpointCredentialInclusion",
        "MoonMindCheckpointSandboxAuthorityViolation",
        "MoonMindCheckpointSourceStateDependency",
        "MoonMindCheckpointRouteReadinessDivergence",
        "MoonMindCheckpointCapabilityGenerationMismatch",
        "MoonMindCheckpointRequiredJourneyMissing",
    } <= {line.split(":", 1)[1].strip() for line in rules.splitlines() if "- alert:" in line}
