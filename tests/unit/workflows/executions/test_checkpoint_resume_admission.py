from datetime import UTC, datetime

from moonmind.workflows.executions.checkpoint_resume_admission import (
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
        capabilityDigest=capabilities.capability_digest, checkedAt=datetime.now(UTC),
    )


def _policy(**updates):
    values = dict(
        promotionState="internal", captureEnabled=True, shadowRestoreEnabled=True,
        actionExposureEnabled=True, executionAdmissionEnabled=True,
        allowedRuntimeIds={"codex_cli"}, allowedDeploymentGenerations={"generation-1"},
        maxArchiveBytes=1024, requiredGatesPassed=True, liveCanaryPassed=True,
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
    assert decision.runtime_capabilities.capability_set_version.endswith("v2")
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


def test_only_codex_cli_declares_managed_checkpoint_support() -> None:
    for runtime_id in RUNTIME_EXECUTION_CAPABILITIES.runtime_ids:
        descriptor = resolve_runtime_execution_capabilities(runtime_id)
        if runtime_id == "codex_cli":
            assert descriptor.checkpoint_restore_kinds == ("worktree_archive",)
        elif descriptor.runtime_family == "managed_cli":
            assert descriptor.checkpoint_restore_kinds == ()
