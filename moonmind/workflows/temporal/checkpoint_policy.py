"""Shared checkpoint policy selection for MM-996 failed-step recovery."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from moonmind.schemas.temporal_models import WorkspacePolicy


@dataclass(frozen=True)
class ResolvedCheckpointPolicy:
    workspace_policy: WorkspacePolicy
    checkpoint_kind: str
    resumable: bool
    required_evidence: tuple[str, ...]


def _workspace_policy_from_recovery_source(
    recovery_source: Mapping[str, Any] | None,
) -> WorkspacePolicy:
    workspace = {}
    if isinstance(recovery_source, Mapping):
        candidate = recovery_source.get("recoveryWorkspace")
        if not isinstance(candidate, Mapping):
            candidate = recovery_source.get("recovery_workspace")
        if isinstance(candidate, Mapping):
            workspace = candidate
    raw_policy = str(
        workspace.get("workspacePolicy")
        or workspace.get("workspace_policy")
        or "restore_pre_execution"
    ).strip()
    if raw_policy == "start_from_last_passed_commit":
        return "start_from_last_passed_commit"
    return "restore_pre_execution"


def resolve_checkpoint_policy(
    *,
    boundary: str,
    recovery_source: Mapping[str, Any] | None = None,
    runtime_kind: str | None = None,
) -> ResolvedCheckpointPolicy:
    """Return the shared policy used for capture, manifests, and recovery apply."""

    del runtime_kind
    boundary_token = str(boundary or "").strip()
    if boundary_token == "before_recovery_restoration":
        return ResolvedCheckpointPolicy(
            workspace_policy=_workspace_policy_from_recovery_source(recovery_source),
            checkpoint_kind="worktree_archive",
            resumable=True,
            required_evidence=("checkpointRef", "workspacePolicy"),
        )
    if boundary_token == "after_execution":
        return ResolvedCheckpointPolicy(
            workspace_policy="restore_pre_execution",
            checkpoint_kind="git_patch",
            resumable=True,
            required_evidence=("patchRef", "manifestRef"),
        )
    return ResolvedCheckpointPolicy(
        workspace_policy="restore_pre_execution",
        checkpoint_kind="worktree_archive",
        resumable=True,
        required_evidence=("archiveRef", "manifestRef"),
    )
