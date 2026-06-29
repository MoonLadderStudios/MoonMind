"""Unit tests for shared checkpoint policy resolution."""

from __future__ import annotations

from enum import Enum

from moonmind.workflows.temporal.checkpoint_policy import resolve_checkpoint_policy


class _Boundary(Enum):
    BEFORE_RECOVERY_RESTORATION = "before_recovery_restoration"


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
