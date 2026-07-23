from datetime import datetime, timezone

from moonmind.workflows.executions.checkpoint_resume_admission import (
    CheckpointPromotionEvidence,
    CheckpointResumeReadiness,
    CheckpointResumeRolloutPolicy,
    evaluate_checkpoint_resume_admission,
)
from moonmind.workflows.executions.runtime_capabilities import (
    RUNTIME_EXECUTION_CAPABILITIES,
    resolve_runtime_execution_capabilities,
)


def _readiness():
    capabilities = resolve_runtime_execution_capabilities("codex_cli")
    return CheckpointResumeReadiness(
        runtimeId="codex_cli", deploymentGeneration="generation-1",
        captureRouteReady=True, restoreRouteReady=True, artifactStoreReady=True,
        managedRunStoreReady=True,
        capabilitySetVersion=capabilities.capability_set_version,
        capabilityDigest=capabilities.capability_digest, checkedAt=datetime.now(timezone.utc),
    )


def _policy(**updates):
    evidence = CheckpointPromotionEvidence(
        deploymentGeneration="generation-1", coldResumeCiPassed=True,
        shadowRestoreSamples=100, shadowRestoreSuccesses=100,
        captureSamples=100, sourceDestroyingRestoreSamples=50,
        internalResumeSamples=20,
        integrityFailures=0, duplicateSideEffects=0, liveCanaryPassed=True,
        recordedAt=datetime.now(timezone.utc),
    )
    values = dict(
        promotionState="internal", captureEnabled=True, shadowRestoreEnabled=True,
        actionExposureEnabled=True, executionAdmissionEnabled=True,
        allowedRuntimeIds={"codex_cli"}, allowedDeploymentGenerations={"generation-1"},
        maxArchiveBytes=1024, requiredGatesPassed=True, liveCanaryPassed=True,
        promotionEvidence=evidence, minimumShadowSamples=50,
        minimumShadowSuccessRatio=.99,
        reason="internal promotion evidence accepted",
    )
    values.update(updates)
    return CheckpointResumeRolloutPolicy(**values)


def _decision(policy=None):
    return evaluate_checkpoint_resume_admission(
        capabilities=resolve_runtime_execution_capabilities("codex_cli"),
        policy=policy or _policy(), readiness=_readiness(),
        checkpoint_kind="worktree_archive", checkpoint_boundary="before_execution",
        resume_phase="rerun_failed_step", archive_bytes=100,
    )


def test_codex_capability_and_admitted_decision_are_frozen() -> None:
    decision = _decision()
    assert decision.admitted is True
    assert decision.restore_activity == "agent_runtime.restore_workspace_checkpoint"
    assert decision.runtime_capabilities.capability_set_version.endswith("v3")
    assert decision.runtime_capabilities.checkpoint_artifact_contract_version


def test_shadow_and_paused_states_never_expose_resume() -> None:
    for state in ("disabled", "shadow_capture", "shadow_restore", "paused"):
        decision = _decision(_policy(promotionState=state))
        assert decision.admitted is False
        assert decision.reason_code == "rollout_action_hidden"


def test_readiness_gates_and_controls_fail_closed() -> None:
    assert _decision(_policy(executionAdmissionEnabled=False)).reason_code == "rollout_admission_disabled"
    assert _decision(_policy(requiredGatesPassed=False)).reason_code == "promotion_evidence_missing"
    assert _decision(_policy(maxArchiveBytes=10)).reason_code == "checkpoint_archive_limit_exceeded"
    assert _decision(_policy(captureEnabled=False)).reason_code == "checkpoint_routes_disabled"
    assert _decision(_policy(shadowRestoreEnabled=False)).reason_code == "checkpoint_routes_disabled"
    assert _decision(_policy(maxArchiveBytes=0)).admitted is True


def test_unknown_archive_size_fails_closed() -> None:
    decision = evaluate_checkpoint_resume_admission(
        capabilities=resolve_runtime_execution_capabilities("codex_cli"),
        policy=_policy(), readiness=_readiness(), checkpoint_kind="worktree_archive",
        checkpoint_boundary="before_execution", resume_phase="rerun_failed_step",
        archive_bytes=-1,
    )
    assert decision.admitted is False
    assert decision.reason_code == "checkpoint_archive_size_unknown"


def test_promotion_evidence_is_generation_bound_and_objective() -> None:
    mismatched = _policy(
        promotionEvidence={
            "deploymentGeneration": "generation-2",
            "coldResumeCiPassed": True,
            "shadowRestoreSamples": 100,
            "shadowRestoreSuccesses": 100,
            "captureSamples": 100,
            "sourceDestroyingRestoreSamples": 50,
            "internalResumeSamples": 20,
            "integrityFailures": 0,
            "duplicateSideEffects": 0,
            "liveCanaryPassed": True,
            "recordedAt": datetime.now(timezone.utc),
        }
    )
    assert _decision(mismatched).reason_code == "promotion_evidence_missing"


def test_promotion_requires_capture_destructive_restore_and_resume_samples() -> None:
    for field in (
        "captureSamples",
        "sourceDestroyingRestoreSamples",
        "internalResumeSamples",
    ):
        evidence = _policy().promotion_evidence.model_dump(by_alias=True)
        evidence[field] = 0
        assert (
            _decision(_policy(promotionEvidence=evidence)).reason_code
            == "promotion_evidence_missing"
        )


def test_only_codex_cli_declares_managed_checkpoint_support() -> None:
    for runtime_id in RUNTIME_EXECUTION_CAPABILITIES.runtime_ids:
        descriptor = resolve_runtime_execution_capabilities(runtime_id)
        if runtime_id == "codex_cli":
            assert descriptor.checkpoint_restore_kinds == ("worktree_archive",)
        elif descriptor.runtime_family == "managed_cli":
            assert descriptor.checkpoint_restore_kinds == ()
