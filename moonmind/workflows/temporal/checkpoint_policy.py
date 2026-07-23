"""Capability-driven checkpoint policy selection for Step Execution recovery."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from moonmind.schemas.temporal_models import WorkspacePolicy
from moonmind.workflows.executions.runtime_capabilities import (
    RuntimeExecutionCapabilities,
    WorkspaceAuthority,
    resolve_runtime_execution_capabilities,
    validate_runtime_preflight,
    workspace_authority_from_locator,
)

_LEGACY_SANDBOX_CAPABILITIES = RuntimeExecutionCapabilities(
    capabilitySetVersion="runtime-execution-capabilities-v2",
    runtimeId="legacy_sandbox",
    runtimeFamily="moonmind_sandbox",
    workspaceAuthority="moonmind_sandbox",
    checkpointCaptureKinds=("git_patch", "worktree_archive"),
    checkpointRestoreKinds=("git_patch", "worktree_archive"),
    checkpointCaptureActivity="workspace.capture_checkpoint",
    checkpointRestoreActivity="workspace.apply_checkpoint",
    checkpointArtifactContractVersion="legacy-workspace-checkpoint-v1",
    supportsSameSessionContinuation=False,
    terminalContractIds=("legacy_step_execution_result_v1",),
    postExecutionCheckpointCriticality="recoverability_only",
).with_digest()
_LEGACY_MANAGED_CAPABILITIES = RuntimeExecutionCapabilities(
    capabilitySetVersion="runtime-execution-capabilities-v2",
    runtimeId="legacy_managed_runtime",
    runtimeFamily="managed_cli",
    workspaceAuthority="managed_runtime",
    supportsSameSessionContinuation=False,
    terminalContractIds=("legacy_managed_execution_result_v1",),
    postExecutionCheckpointCriticality="recoverability_only",
).with_digest()


@dataclass(frozen=True)
class ResolvedCheckpointPolicy:
    workspace_policy: WorkspacePolicy
    checkpoint_kind: str | None
    resumable: bool
    required_evidence: tuple[str, ...]
    capture_authority: WorkspaceAuthority
    capture_activity: str | None = None
    criticality: str = "unsupported"
    supported_checkpoint_kinds: tuple[str, ...] = ()
    session: "CheckpointPlaneDecision | None" = None
    workspace: "CheckpointPlaneDecision | None" = None


@dataclass(frozen=True)
class CheckpointPlaneDecision:
    plane: Literal["session", "workspace"]
    checkpoint_kind: str | None
    resumable: bool
    required_evidence: tuple[str, ...]
    authority: WorkspaceAuthority
    capture_activity: str | None
    restore_activity: str | None
    supported_checkpoint_kinds: tuple[str, ...]


_EXTERNAL_STATE_BOUNDARIES = frozenset(
    {"after_prepare", "before_execution", "after_execution", "after_gate", "before_publication"}
)


def _boundary_token(boundary: Any) -> str:
    value = boundary.value if hasattr(boundary, "value") else boundary
    return str(value or "").strip()


def _workspace_policy_from_recovery_source(
    recovery_source: Mapping[str, Any] | None,
) -> WorkspacePolicy:
    workspace: Mapping[str, Any] = {}
    if isinstance(recovery_source, Mapping):
        candidate = recovery_source.get("recoveryWorkspace")
        if not isinstance(candidate, Mapping):
            candidate = recovery_source.get("recovery_workspace")
        if isinstance(candidate, Mapping):
            workspace = candidate
    raw = str(
        workspace.get("workspacePolicy")
        or workspace.get("workspace_policy")
        or "restore_pre_execution"
    ).strip()
    return (
        "start_from_last_passed_commit"
        if raw == "start_from_last_passed_commit"
        else "restore_pre_execution"
    )


def _external_evidence(boundary: str) -> tuple[str, ...]:
    if boundary == "before_execution":
        return ("externalStateRef", "idempotencyKey", "runtimeSessionId")
    if boundary == "after_execution":
        return ("externalStateRef", "diagnosticsRef", "runtimeSessionId")
    return ("externalStateRef", "runtimeSessionId")


def resolve_checkpoint_policy(
    *,
    boundary: str,
    capabilities: RuntimeExecutionCapabilities | None = None,
    workspace_authority: WorkspaceAuthority | None = None,
    workspace_locator: Mapping[str, Any] | None = None,
    recovery_source: Mapping[str, Any] | None = None,
    # Transitional input only: aliases are canonicalized by the registry before
    # policy is selected. No identity-specific policy exists here.
    runtime_kind: str | None = None,
    external_agent_id: str | None = None,
    agent_kind: str | None = None,
    requested_state: Literal["session", "workspace", "both"] = "workspace",
) -> ResolvedCheckpointPolicy:
    """Select checkpoint behavior solely from a recorded capability snapshot."""

    if capabilities is None:
        runtime_id = external_agent_id or runtime_kind
        capabilities = (
            resolve_runtime_execution_capabilities(runtime_id)
            if runtime_id
            else (
                _LEGACY_MANAGED_CAPABILITIES
                if workspace_authority == "managed_runtime"
                else _LEGACY_SANDBOX_CAPABILITIES
            )
        )

    authority = (
        workspace_authority_from_locator(workspace_locator)
        or workspace_authority
        or capabilities.workspace_authority
    )
    # ``agent_kind`` is intentionally ignored: ownership comes from capability
    # data, not the workflow's managed/external dispatch classification.
    del agent_kind
    validate_runtime_preflight(capabilities, workspace_authority=authority)
    token = _boundary_token(boundary)

    session_capability = capabilities.session_state
    session_kind = None
    if session_capability and session_capability.checkpoint_kinds and token in _EXTERNAL_STATE_BOUNDARIES:
        session_kind = session_capability.checkpoint_kinds[0]
    session_decision = CheckpointPlaneDecision(
        plane="session",
        checkpoint_kind=session_kind,
        resumable=bool(
            session_kind and session_capability
            and (session_capability.supports_live_reattach or session_capability.supports_cold_session)
        ),
        required_evidence=(
            session_capability.required_evidence if session_kind and session_capability else ()
        ),
        authority=session_capability.authority if session_capability else authority,
        capture_activity=session_capability.capture_activity if session_capability else None,
        restore_activity=session_capability.restore_owner if session_capability else None,
        supported_checkpoint_kinds=session_capability.checkpoint_kinds if session_capability else (),
    )

    if requested_state == "session":
        return ResolvedCheckpointPolicy(
            workspace_policy="continue_from_previous_execution",
            checkpoint_kind=session_decision.checkpoint_kind,
            resumable=session_decision.resumable,
            required_evidence=session_decision.required_evidence,
            capture_authority=session_decision.authority,
            capture_activity=session_decision.capture_activity,
            criticality=capabilities.post_execution_checkpoint_criticality,
            supported_checkpoint_kinds=session_decision.supported_checkpoint_kinds,
            session=session_decision,
        )

    if "external_state_ref" in capabilities.checkpoint_capture_kinds:
        if token not in _EXTERNAL_STATE_BOUNDARIES:
            return ResolvedCheckpointPolicy(
                workspace_policy="restore_pre_execution",
                checkpoint_kind=None,
                resumable=False,
                required_evidence=(),
                capture_authority=authority,
                criticality="unsupported",
                supported_checkpoint_kinds=capabilities.checkpoint_capture_kinds,
                session=session_decision if requested_state == "both" else None,
            )
        return ResolvedCheckpointPolicy(
            workspace_policy="continue_from_previous_execution",
            checkpoint_kind="external_state_ref",
            resumable="external_state_ref" in capabilities.checkpoint_restore_kinds,
            required_evidence=_external_evidence(token),
            capture_authority=authority,
            capture_activity=capabilities.checkpoint_capture_activity,
            criticality=capabilities.post_execution_checkpoint_criticality,
            supported_checkpoint_kinds=capabilities.checkpoint_capture_kinds,
            session=session_decision if requested_state == "both" else None,
        )

    if not capabilities.checkpoint_capture_kinds:
        workspace_decision = CheckpointPlaneDecision(
            plane="workspace", checkpoint_kind=None, resumable=False,
            required_evidence=(), authority=authority, capture_activity=None,
            restore_activity=None, supported_checkpoint_kinds=(),
        )
        return ResolvedCheckpointPolicy(
            workspace_policy=(
                _workspace_policy_from_recovery_source(recovery_source)
                if token == "before_recovery_restoration"
                else "restore_pre_execution"
            ),
            checkpoint_kind=None,
            resumable=False,
            required_evidence=(),
            capture_authority=authority,
            criticality=capabilities.post_execution_checkpoint_criticality,
            supported_checkpoint_kinds=capabilities.checkpoint_capture_kinds,
            session=session_decision if requested_state == "both" else None,
            workspace=workspace_decision,
        )

    preferred_kind = (
        "git_patch" if token == "after_execution" else "worktree_archive"
    )
    kind = (
        preferred_kind
        if preferred_kind in capabilities.checkpoint_capture_kinds
        else capabilities.checkpoint_capture_kinds[0]
    )
    validate_runtime_preflight(
        capabilities,
        workspace_authority=authority,
        checkpoint_kind=kind,
        restore_required=token == "before_recovery_restoration",
    )
    required_evidence = (
        ("patchRef", "manifestRef")
        if kind == "git_patch"
        else ("archiveRef", "manifestRef")
    )
    workspace_decision = CheckpointPlaneDecision(
        plane="workspace", checkpoint_kind=kind,
        resumable=kind in capabilities.checkpoint_restore_kinds,
        required_evidence=required_evidence, authority=authority,
        capture_activity=capabilities.checkpoint_capture_activity,
        restore_activity=capabilities.checkpoint_restore_activity,
        supported_checkpoint_kinds=capabilities.checkpoint_capture_kinds,
    )
    return ResolvedCheckpointPolicy(
        workspace_policy=(
            _workspace_policy_from_recovery_source(recovery_source)
            if token == "before_recovery_restoration"
            else "restore_pre_execution"
        ),
        checkpoint_kind=kind,
        resumable=kind in capabilities.checkpoint_restore_kinds,
        required_evidence=required_evidence,
        capture_authority=authority,
        capture_activity=capabilities.checkpoint_capture_activity,
        criticality=capabilities.post_execution_checkpoint_criticality,
        supported_checkpoint_kinds=capabilities.checkpoint_capture_kinds,
        session=session_decision if requested_state == "both" else None,
        workspace=workspace_decision,
    )
