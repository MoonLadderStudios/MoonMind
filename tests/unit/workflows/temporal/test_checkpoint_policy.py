"""Unit tests for shared checkpoint policy resolution."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from moonmind.schemas.temporal_models import StepExecutionIdentityModel
from moonmind.workflows.executions.runtime_capabilities import (
    resolve_runtime_execution_capabilities,
)
from moonmind.workflows.temporal.checkpoint_policy import resolve_checkpoint_policy
from moonmind.workflows.temporal.step_checkpoints import (
    build_step_checkpoint_payload,
    validate_step_checkpoint_payload,
)


class _Boundary(Enum):
    BEFORE_RECOVERY_RESTORATION = "before_recovery_restoration"
    BEFORE_EXECUTION = "before_execution"
    AFTER_EXECUTION = "after_execution"


def _identity() -> StepExecutionIdentityModel:
    return StepExecutionIdentityModel(
        workflowId="workflow-1",
        runId="run-1",
        logicalStepId="checkpoint-story",
        executionOrdinal=1,
    )


def test_resolve_checkpoint_policy_uses_enum_value() -> None:
    policy = resolve_checkpoint_policy(
        boundary=_Boundary.BEFORE_RECOVERY_RESTORATION,
        recovery_source={
            "recoveryWorkspace": {
                "workspacePolicy": "start_from_last_passed_commit",
            }
        },
    )

    assert policy.checkpoint_kind == "worktree_archive"
    assert policy.workspace_policy == "start_from_last_passed_commit"


def test_resolve_checkpoint_policy_separates_omnigent_planes_after_execution() -> None:
    policy = resolve_checkpoint_policy(
        boundary=_Boundary.AFTER_EXECUTION,
        capabilities=resolve_runtime_execution_capabilities("omnigent"), requested_state="both",
    )

    assert policy.workspace_policy == "restore_pre_execution"
    assert policy.checkpoint_kind == "worktree_archive"
    assert policy.resumable is True
    assert policy.required_evidence == ("archiveRef", "manifestRef")
    assert policy.session.checkpoint_kind == "external_state_ref"
    assert policy.workspace.checkpoint_kind == "worktree_archive"


def test_resolve_checkpoint_policy_can_request_omnigent_session_state() -> None:
    policy = resolve_checkpoint_policy(
        boundary=_Boundary.BEFORE_EXECUTION,
        capabilities=resolve_runtime_execution_capabilities("omnigent"), requested_state="session",
    )

    assert policy.workspace_policy == "continue_from_previous_execution"
    assert policy.checkpoint_kind == "external_state_ref"
    assert policy.required_evidence == (
        "externalStateRef",
        "runtimeSessionId",
        "firstMessageEvidenceRef",
    )


def test_resolve_checkpoint_policy_uses_canonical_external_capabilities() -> None:
    policy = resolve_checkpoint_policy(
        boundary=_Boundary.BEFORE_EXECUTION,
        capabilities=resolve_runtime_execution_capabilities("omnigent"), requested_state="session",
    )
    control = resolve_checkpoint_policy(
        boundary=_Boundary.BEFORE_EXECUTION,
        capabilities=resolve_runtime_execution_capabilities("jules"),
    )

    assert policy.workspace_policy == "continue_from_previous_execution"
    assert policy.checkpoint_kind == "external_state_ref"
    assert control.workspace_policy == "restore_pre_execution"
    assert control.checkpoint_kind is None


def test_resolve_checkpoint_policy_routes_managed_runtime_to_owner_capture() -> None:
    policy = resolve_checkpoint_policy(
        boundary="after_execution",
        runtime_kind="codex_cli",
    )

    assert policy.workspace_policy == "restore_pre_execution"
    assert policy.checkpoint_kind == "worktree_archive"
    assert policy.required_evidence == ("archiveRef", "manifestRef")
    assert policy.capture_activity == "agent_runtime.capture_workspace_checkpoint"


def test_resolve_checkpoint_policy_assigns_managed_runtime_authority() -> None:
    policy = resolve_checkpoint_policy(
        boundary="after_execution",
        runtime_kind="codex_cli",
        agent_kind="managed",
    )

    assert policy.capture_authority == "managed_runtime"
    assert policy.checkpoint_kind == "worktree_archive"
    assert policy.resumable is True
    assert policy.required_evidence == ("archiveRef", "manifestRef")


def test_omnigent_unsupported_recovery_boundary_does_not_invent_local_capture() -> None:
    policy = resolve_checkpoint_policy(
        boundary=_Boundary.BEFORE_RECOVERY_RESTORATION,
        runtime_kind="omnigent",
        recovery_source={
            "recoveryWorkspace": {
                "workspacePolicy": "start_from_last_passed_commit",
            }
        },
    )

    assert policy.checkpoint_kind == "worktree_archive"
    assert policy.workspace_policy == "start_from_last_passed_commit"


def test_external_state_ref_never_satisfies_workspace_restore_preflight() -> None:
    capability = resolve_runtime_execution_capabilities("omnigent")
    policy = resolve_checkpoint_policy(
        boundary="before_recovery_restoration", capabilities=capability,
        requested_state="workspace",
    )

    assert policy.checkpoint_kind == "worktree_archive"
    assert "external_state_ref" not in policy.supported_checkpoint_kinds


def test_external_state_checkpoint_is_continue_only() -> None:
    identity = _identity()
    checkpoint = build_step_checkpoint_payload(
        identity=identity,
        boundary="after_execution",
        task_input_snapshot_ref="artifact-input",
        plan_digest="sha256:plan",
        workspace={
            "kind": "external_state_ref",
            "externalStateRef": "artifact-omnigent-state",
        },
        step_outputs={
            "diagnosticsRef": "artifact-diagnostics",
            "resultRef": "artifact-result",
        },
        created_at=datetime(2026, 6, 30, 12, 0, tzinfo=UTC),
    )

    valid = validate_step_checkpoint_payload(
        checkpoint,
        expected_source=identity,
        expected_task_input_snapshot_ref="artifact-input",
        expected_plan_digest="sha256:plan",
        workspace_policy="continue_from_previous_execution",
        required_artifact_refs=[
            "artifact-omnigent-state",
            "artifact-diagnostics",
            "artifact-result",
        ],
    )
    rejected = validate_step_checkpoint_payload(
        checkpoint,
        expected_source=identity,
        expected_task_input_snapshot_ref="artifact-input",
        expected_plan_digest="sha256:plan",
        workspace_policy="restore_pre_execution",
    )

    assert valid.valid is True
    assert rejected.valid is False
    assert rejected.failure_code == "policy_incompatible"
