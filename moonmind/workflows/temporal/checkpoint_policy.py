"""Shared checkpoint policy selection for Step Execution recovery."""

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


_OMNIGENT_EXTERNAL_STATE_BOUNDARIES = frozenset(
    {
        "after_prepare",
        "before_execution",
        "after_execution",
        "after_gate",
        "before_publication",
    }
)


def _boundary_token(boundary: Any) -> str:
    boundary_value = boundary.value if hasattr(boundary, "value") else boundary
    return str(boundary_value or "").strip()


def _is_omnigent_runtime(runtime_kind: str | None) -> bool:
    """Return True for Omnigent aliases without making the contract alias-heavy."""

    return "omnigent" in str(runtime_kind or "").lower()


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


def _omnigent_required_evidence(boundary_token: str) -> tuple[str, ...]:
    if boundary_token == "before_execution":
        return ("externalStateRef", "idempotencyKey", "omnigentSessionId")
    if boundary_token == "after_execution":
        return ("externalStateRef", "diagnosticsRef", "omnigentSessionId")
    return ("externalStateRef", "omnigentSessionId")


def _omnigent_checkpoint_policy(
    boundary_token: str,
) -> ResolvedCheckpointPolicy | None:
    if boundary_token not in _OMNIGENT_EXTERNAL_STATE_BOUNDARIES:
        return None
    return ResolvedCheckpointPolicy(
        workspace_policy="continue_from_previous_execution",
        checkpoint_kind="external_state_ref",
        resumable=True,
        required_evidence=_omnigent_required_evidence(boundary_token),
    )


def resolve_checkpoint_policy(
    *,
    boundary: str,
    recovery_source: Mapping[str, Any] | None = None,
    runtime_kind: str | None = None,
    external_agent_id: str | None = None,
) -> ResolvedCheckpointPolicy:
    """Return the shared policy used for capture, manifests, and recovery apply."""

    boundary_token = _boundary_token(boundary)
    is_omnigent_external_agent = str(external_agent_id or "").strip() == "omnigent"
    if is_omnigent_external_agent or _is_omnigent_runtime(runtime_kind):
        external_policy = _omnigent_checkpoint_policy(boundary_token)
        if external_policy is not None:
            return external_policy

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
