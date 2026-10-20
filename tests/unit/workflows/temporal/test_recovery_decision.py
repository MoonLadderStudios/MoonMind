from __future__ import annotations

import pytest

from moonmind.workflows.executions.runtime_capabilities import (
    RuntimeExecutionCapabilities,
    resolve_runtime_execution_capabilities,
)
from moonmind.workflows.temporal.recovery_decision import (
    decide_checkpoint_recovery,
    decide_same_session_recovery,
)


def test_same_session_continuation_requires_live_capable_session() -> None:
    capabilities = resolve_runtime_execution_capabilities("codex_cli")
    eligible = decide_same_session_recovery(
        live_session_id="session-1",
        session_reachable=True,
        capabilities=capabilities,
    )
    dead = decide_same_session_recovery(
        live_session_id="session-1",
        session_reachable=False,
        capabilities=capabilities,
    )
    unsupported = decide_same_session_recovery(
        live_session_id="session-1",
        session_reachable=True,
        capabilities=resolve_runtime_execution_capabilities("claude_code"),
    )

    assert eligible.eligible is True
    assert eligible.default_action == "continue_same_session"
    assert eligible.checkpoint_ref is None
    assert dead.disabled_reason_code == "SAME_SESSION_UNREACHABLE"
    assert unsupported.disabled_reason_code == "SAME_SESSION_CONTINUATION_UNSUPPORTED"


def _decision(**overrides):
    values = {
        "checkpoint_ref": "artifact://checkpoint",
        "checkpoint_boundary": "before_execution",
        "checkpoint_kind": "worktree_archive",
        "capabilities": resolve_runtime_execution_capabilities("omnigent"),
        "restore_route_registered": True,
        "artifact_valid": True,
        "side_effect_safe": True,
    }
    values.update(overrides)
    return decide_checkpoint_recovery(**values)


def test_checkpoint_resume_requires_complete_restore_proof() -> None:
    decision = _decision()

    assert decision.eligible is True
    assert decision.requested_action == "resume_from_workspace_checkpoint"
    assert decision.capability_digest


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"restore_route_registered": False}, "CHECKPOINT_RESTORE_ROUTE_MISSING"),
        ({"checkpoint_kind": "external_state_ref"}, "CHECKPOINT_KIND_INCOMPATIBLE"),
        ({"checkpoint_boundary": "after_prepare"}, "CHECKPOINT_BOUNDARY_INCOMPATIBLE"),
        ({"side_effect_safe": False}, "CHECKPOINT_SIDE_EFFECT_UNSAFE"),
        ({"capabilities": None}, "CHECKPOINT_CAPABILITY_SNAPSHOT_MISSING"),
    ],
)
def test_checkpoint_resume_fails_closed(overrides, reason) -> None:
    decision = _decision(**overrides)

    assert decision.eligible is False
    assert decision.default_action == "full_retry"
    assert decision.disabled_reason_code == reason


def test_valid_checkpoint_without_recovery_target_has_target_reason() -> None:
    decision = _decision(recovery_target_available=False)

    assert decision.eligible is False
    assert decision.disabled_reason_code == "RECOVERY_TARGET_UNAVAILABLE"
    assert decision.checkpoint_ref == "artifact://checkpoint"


def test_codex_worktree_archive_restore_is_eligible() -> None:
    decision = _decision(
        capabilities=resolve_runtime_execution_capabilities("codex_cli"),
        checkpoint_kind="worktree_archive",
    )

    assert decision.eligible is True
    assert (
        decision.restore_activity
        == "agent_runtime.restore_workspace_checkpoint"
    )


def test_omnigent_partial_evidence_distinguishes_session_from_workspace() -> None:
    decision = _decision(
        checkpoint_kind="external_state_ref",
        live_session_id="session-1",
        session_reachable=True,
    )

    assert decision.eligible is False
    assert decision.session_recoverable is True
    assert decision.workspace_recoverable is False
    assert decision.authoritative_workspace_checkpoint_kind is None
    assert "workspace checkpoint evidence" in decision.partial_recovery_reason


def test_workspace_decision_does_not_infer_session_evidence_from_capability() -> None:
    decision = _decision()

    assert decision.workspace_recoverable is True
    assert decision.session_recoverable is False


def test_same_session_decision_does_not_infer_workspace_evidence_from_capability() -> None:
    decision = decide_same_session_recovery(
        live_session_id="session-1",
        session_reachable=True,
        capabilities=resolve_runtime_execution_capabilities("omnigent"),
    )

    assert decision.session_recoverable is True
    assert decision.workspace_recoverable is False


def test_branch_availability_requires_runtime_restore_capability() -> None:
    # A valid workspace checkpoint on a runtime with a restore kind and route can branch.
    supported = decide_same_session_recovery(
        live_session_id="session-1",
        session_reachable=True,
        workspace_checkpoint_valid=True,
        capabilities=resolve_runtime_execution_capabilities("omnigent"),
    )
    # claude_code advertises no checkpoint restore kind, so branching is unavailable
    # even though the workspace checkpoint itself is valid.
    unsupported = decide_same_session_recovery(
        live_session_id="session-1",
        session_reachable=True,
        workspace_checkpoint_valid=True,
        capabilities=resolve_runtime_execution_capabilities("claude_code"),
    )
    missing_caps = decide_same_session_recovery(
        live_session_id="session-1",
        session_reachable=True,
        workspace_checkpoint_valid=True,
        capabilities=None,
    )

    assert supported.branch_creation_available is True
    assert supported.branch_creation_reason is None
    assert unsupported.branch_creation_available is False
    assert unsupported.branch_creation_reason == "CHECKPOINT_RESTORE_UNSUPPORTED"
    assert missing_caps.branch_creation_available is False
    assert missing_caps.branch_creation_reason == "CHECKPOINT_RESTORE_UNSUPPORTED"


def test_checkpoint_decision_reports_unsupported_live_reattach_explicitly() -> None:
    # A reachable live session on a runtime that cannot live-reattach must surface the
    # machine-readable unsupported-capability reason, not a null or unreachable reason.
    decision = _decision(
        capabilities=resolve_runtime_execution_capabilities("claude_code"),
        checkpoint_kind="worktree_archive",
        live_session_id="session-1",
        session_reachable=True,
    )

    assert decision.session_recoverable is False
    assert decision.live_reattach_reason == "SAME_SESSION_CONTINUATION_UNSUPPORTED"

    unreachable = _decision(
        live_session_id=None,
        session_reachable=False,
    )

    assert unreachable.live_reattach_reason == "SAME_SESSION_UNREACHABLE"


def test_restore_kinds_without_activity_are_rejected_by_capability_schema() -> None:
    with pytest.raises(ValueError, match="declared together"):
        RuntimeExecutionCapabilities(
            runtimeId="broken",
            workspaceAuthority="managed_runtime",
            checkpointRestoreKinds=("worktree_archive",),
            supportsSameSessionContinuation=False,
            postExecutionCheckpointCriticality="unsupported",
        )
