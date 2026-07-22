"""Canonical, immutable execution capability descriptors for agent runtimes.

Workflow code consumes snapshots returned by this module.  It must not infer
workspace ownership or checkpoint behavior from runtime names.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.schemas._validation import NonBlankStr
from moonmind.workflows.executions.runtime_defaults import normalize_runtime_id

WorkspaceAuthority = Literal[
    "moonmind_sandbox", "managed_runtime", "external_provider", "none"
]
CheckpointCriticality = Literal["required", "recoverability_only", "unsupported"]

CAPABILITY_SET_VERSION = "runtime-execution-capabilities-v3"
CheckpointResumePhase = Literal[
    "rerun_failed_step", "continue_to_gate", "continue_after_gate",
    "resume_publication", "retry_restoration",
]


class RuntimeCapabilityError(ValueError):
    """Raised when a runtime or capability/workspace combination is invalid."""


class AgentIdentityCapability(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    agent_kind: Literal["managed", "external"] = Field(..., alias="agentKind")
    agent_id: NonBlankStr = Field(..., alias="agentId")
    harness: str | None = None


class SessionStateCapability(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    authority: WorkspaceAuthority
    checkpoint_kinds: tuple[str, ...] = Field(default=(), alias="checkpointKinds")
    capture_activity: str | None = Field(None, alias="captureActivity")
    restore_owner: str | None = Field(None, alias="restoreOwner")
    required_evidence: tuple[str, ...] = Field(default=(), alias="requiredEvidence")
    supports_live_reattach: bool = Field(False, alias="supportsLiveReattach")
    supports_cold_session: bool = Field(False, alias="supportsColdSession")

    @model_validator(mode="after")
    def _validate_capture(self) -> "SessionStateCapability":
        if bool(self.checkpoint_kinds) != bool(self.capture_activity):
            raise ValueError("session checkpoint kinds and capture activity must be declared together")
        if self.supports_cold_session and not self.restore_owner:
            raise ValueError("cold session support must name its restore owner")
        return self


class WorkspaceStateCapability(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    authority: WorkspaceAuthority
    locator_kinds: tuple[str, ...] = Field(default=(), alias="locatorKinds")
    checkpoint_kinds: tuple[str, ...] = Field(default=(), alias="checkpointKinds")
    restore_kinds: tuple[str, ...] = Field(default=(), alias="restoreKinds")
    capture_activity: str | None = Field(None, alias="captureActivity")
    restore_activity: str | None = Field(None, alias="restoreActivity")
    artifact_contract_version: str | None = Field(None, alias="artifactContractVersion")
    digest_required: bool = Field(False, alias="digestRequired")
    boundary_support: dict[str, tuple[CheckpointResumePhase, ...]] = Field(
        default_factory=dict, alias="boundarySupport"
    )

    @model_validator(mode="after")
    def _validate_checkpoint_contract(self) -> "WorkspaceStateCapability":
        if bool(self.checkpoint_kinds) != bool(self.capture_activity):
            raise ValueError("workspace checkpoint kinds and capture activity must be declared together")
        if bool(self.restore_kinds) != bool(self.restore_activity):
            raise ValueError("workspace restore kinds and activity must be declared together")
        if self.restore_kinds and (not self.artifact_contract_version or not self.boundary_support):
            raise ValueError("workspace restore support requires artifact contract and boundaries")
        if self.authority == "moonmind_sandbox" and self.locator_kinds != ("sandbox",):
            raise ValueError("MoonMind sandbox workspace authority requires sandbox locators")
        return self


class HostModeCapability(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    mode: NonBlankStr
    repository_mutation_isolated: bool = Field(False, alias="repositoryMutationIsolated")
    github_credentials_isolated: bool = Field(False, alias="githubCredentialsIsolated")


class HostRealizationCapability(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    owner: NonBlankStr
    mode_capabilities: tuple[HostModeCapability, ...] = Field(
        default=(), alias="modeCapabilities"
    )
    lease_owner: str | None = Field(None, alias="leaseOwner")
    cleanup_owner: str | None = Field(None, alias="cleanupOwner")
    workspace_mount_target: str | None = Field(None, alias="workspaceMountTarget")

    @model_validator(mode="after")
    def _validate_modes(self) -> "HostRealizationCapability":
        modes = tuple(item.mode for item in self.mode_capabilities)
        if len(modes) != len(set(modes)):
            raise ValueError("host realization modes must be unique")
        return self

    def require_mode(
        self, mode: str, *, repository_mutation: bool = False,
        github_credentials: bool = False,
    ) -> HostModeCapability:
        selected = next((item for item in self.mode_capabilities if item.mode == mode), None)
        if selected is None:
            raise RuntimeCapabilityError(f"unsupported host realization mode '{mode}'")
        if repository_mutation and not selected.repository_mutation_isolated:
            raise RuntimeCapabilityError(
                f"host realization mode '{mode}' does not isolate repository mutation"
            )
        if github_credentials and not selected.github_credentials_isolated:
            raise RuntimeCapabilityError(
                f"host realization mode '{mode}' does not isolate GitHub credentials"
            )
        return selected


class RuntimeExecutionCapabilities(BaseModel):
    """Compact runtime-neutral policy snapshot recorded with an execution."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    capability_set_version: Literal[
        "runtime-execution-capabilities-v1", "runtime-execution-capabilities-v2",
        "runtime-execution-capabilities-v3",
    ] = Field(
        "runtime-execution-capabilities-v2", alias="capabilitySetVersion"
    )
    runtime_id: NonBlankStr = Field(..., alias="runtimeId")
    runtime_family: str | None = Field(None, alias="runtimeFamily")
    agent_identity: AgentIdentityCapability | None = Field(None, alias="agentIdentity")
    session_state: SessionStateCapability | None = Field(None, alias="sessionState")
    workspace_state: WorkspaceStateCapability | None = Field(None, alias="workspaceState")
    host_realization: HostRealizationCapability | None = Field(None, alias="hostRealization")
    workspace_authority: WorkspaceAuthority = Field(..., alias="workspaceAuthority")
    checkpoint_capture_kinds: tuple[str, ...] = Field(
        default=(), alias="checkpointCaptureKinds"
    )
    checkpoint_restore_kinds: tuple[str, ...] = Field(
        default=(), alias="checkpointRestoreKinds"
    )
    checkpoint_capture_activity: str | None = Field(
        None, alias="checkpointCaptureActivity"
    )
    checkpoint_restore_activity: str | None = Field(
        None, alias="checkpointRestoreActivity"
    )
    checkpoint_artifact_contract_version: str | None = Field(
        None, alias="checkpointArtifactContractVersion"
    )
    checkpoint_boundary_support: dict[str, tuple[CheckpointResumePhase, ...]] = Field(
        default_factory=dict, alias="checkpointBoundarySupport"
    )
    supports_same_session_continuation: bool = Field(
        ..., alias="supportsSameSessionContinuation"
    )
    supports_active_command_introspection: bool = Field(
        False, alias="supportsActiveCommandIntrospection"
    )
    active_command_introspection_owner: str | None = Field(
        None, alias="activeCommandIntrospectionOwner"
    )
    terminal_contract_ids: tuple[str, ...] = Field(
        default=(), alias="terminalContractIds"
    )
    post_execution_checkpoint_criticality: CheckpointCriticality = Field(
        ..., alias="postExecutionCheckpointCriticality"
    )
    capability_digest: str = Field("", alias="capabilityDigest")

    @model_validator(mode="after")
    def _validate_claims(self) -> "RuntimeExecutionCapabilities":
        if self.capability_set_version == CAPABILITY_SET_VERSION:
            if not all((self.agent_identity, self.session_state, self.workspace_state, self.host_realization)):
                raise ValueError("v3 capability snapshots require identity, session, workspace, and host planes")
            assert self.workspace_state is not None
            if self.workspace_authority != self.workspace_state.authority:
                raise ValueError("workspace authority contradicts workspace state plane")
            if self.checkpoint_capture_kinds != self.workspace_state.checkpoint_kinds:
                raise ValueError("checkpoint capture kinds contradict workspace state plane")
            if self.checkpoint_restore_kinds != self.workspace_state.restore_kinds:
                raise ValueError("checkpoint restore kinds contradict workspace state plane")
            if self.checkpoint_capture_activity != self.workspace_state.capture_activity:
                raise ValueError("checkpoint capture activity contradicts workspace state plane")
            if self.checkpoint_restore_activity != self.workspace_state.restore_activity:
                raise ValueError("checkpoint restore activity contradicts workspace state plane")
        if bool(self.checkpoint_capture_kinds) != bool(self.checkpoint_capture_activity):
            raise ValueError("checkpoint capture kinds and activity must be declared together")
        if bool(self.checkpoint_restore_kinds) != bool(self.checkpoint_restore_activity):
            raise ValueError("checkpoint restore kinds and activity must be declared together")
        if (
            self.capability_set_version == "runtime-execution-capabilities-v2"
            and self.checkpoint_restore_kinds
            and not self.checkpoint_artifact_contract_version
        ):
            raise ValueError("checkpoint restore capability must name its artifact contract")
        if self.checkpoint_boundary_support and not self.checkpoint_restore_kinds:
            raise ValueError("checkpoint boundary support requires a restore capability")
        for activity in (self.checkpoint_capture_activity, self.checkpoint_restore_activity):
            if (
                activity
                and self.workspace_authority == "managed_runtime"
                and not activity.startswith("agent_runtime.")
            ):
                raise ValueError("managed checkpoint activities must use agent_runtime routes")
        if (
            self.post_execution_checkpoint_criticality == "required"
            and not self.checkpoint_capture_kinds
        ):
            raise ValueError("required post-execution checkpoint needs a capture capability")
        if self.supports_active_command_introspection != bool(
            self.active_command_introspection_owner
        ):
            raise ValueError("active command introspection must name its invocation owner")
        return self

    def with_digest(self) -> "RuntimeExecutionCapabilities":
        payload = self.model_dump(by_alias=True, mode="json", exclude={"capability_digest"})
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return self.model_copy(update={"capability_digest": digest})


def _descriptor(**values: Any) -> RuntimeExecutionCapabilities:
    values.setdefault("capabilitySetVersion", CAPABILITY_SET_VERSION)
    runtime_id = values["runtimeId"]
    authority = values["workspaceAuthority"]
    capture_kinds = tuple(values.get("checkpointCaptureKinds", ()))
    restore_kinds = tuple(values.get("checkpointRestoreKinds", ()))
    capture_activity = values.get("checkpointCaptureActivity")
    restore_activity = values.get("checkpointRestoreActivity")
    contract_version = values.get("checkpointArtifactContractVersion")
    boundaries = values.get("checkpointBoundarySupport", {})
    values.setdefault("agentIdentity", {
        "agentKind": "external" if values.get("runtimeFamily") == "external_provider" else "managed",
        "agentId": runtime_id,
    })
    values.setdefault("sessionState", {
        "authority": authority,
        "supportsLiveReattach": values.get("supportsSameSessionContinuation", False),
    })
    values.setdefault("workspaceState", {
        "authority": authority,
        "locatorKinds": ({"moonmind_sandbox": ("sandbox",), "managed_runtime": ("managed_runtime",), "external_provider": ("external_state",)}.get(authority, ())),
        "checkpointKinds": capture_kinds,
        "restoreKinds": restore_kinds,
        "captureActivity": capture_activity,
        "restoreActivity": restore_activity,
        "artifactContractVersion": contract_version,
        "digestRequired": bool(restore_kinds),
        "boundarySupport": boundaries or ({"before_execution": ("rerun_failed_step",)} if restore_kinds else {}),
    })
    values.setdefault("hostRealization", {"owner": "moonmind" if authority != "external_provider" else "external_provider"})
    return RuntimeExecutionCapabilities(**values).with_digest()


_DESCRIPTORS = (
    _descriptor(
        runtimeId="codex_cli", runtimeFamily="managed_cli",
        workspaceAuthority="managed_runtime",
        checkpointCaptureKinds=("worktree_archive",),
        checkpointRestoreKinds=("worktree_archive",),
        checkpointCaptureActivity="agent_runtime.capture_workspace_checkpoint",
        checkpointRestoreActivity="agent_runtime.restore_workspace_checkpoint",
        checkpointArtifactContractVersion="managed-worktree-archive-v1",
        checkpointBoundarySupport={
            "before_execution": ("rerun_failed_step",),
        },
        supportsSameSessionContinuation=True,
        supportsActiveCommandIntrospection=True,
        activeCommandIntrospectionOwner="managed_agent.command_status",
        terminalContractIds=("codex_managed_session_summary_v1",),
        postExecutionCheckpointCriticality="recoverability_only",
    ),
    _descriptor(
        runtimeId="claude_code", runtimeFamily="managed_cli",
        workspaceAuthority="managed_runtime", checkpointCaptureKinds=(),
        checkpointRestoreKinds=(), supportsSameSessionContinuation=False,
        terminalContractIds=("managed_agent_execution_result_v1",),
        postExecutionCheckpointCriticality="recoverability_only",
    ),
    _descriptor(
        runtimeId="jules", runtimeFamily="external_provider",
        workspaceAuthority="external_provider", checkpointCaptureKinds=(),
        checkpointRestoreKinds=(), supportsSameSessionContinuation=True,
        terminalContractIds=("jules_fetch_result_v1",),
        postExecutionCheckpointCriticality="unsupported",
    ),
    _descriptor(
        runtimeId="codex_cloud", runtimeFamily="external_provider",
        workspaceAuthority="external_provider", checkpointCaptureKinds=(),
        checkpointRestoreKinds=(), supportsSameSessionContinuation=False,
        terminalContractIds=("codex_cloud_fetch_result_v1",),
        postExecutionCheckpointCriticality="unsupported",
    ),
    _descriptor(
        runtimeId="omnigent", runtimeFamily="external_provider",
        agentIdentity={"agentKind": "external", "agentId": "omnigent", "harness": "codex-native"},
        sessionState={
            "authority": "external_provider",
            "checkpointKinds": ("external_state_ref",),
            "captureActivity": "workspace.capture_checkpoint",
            "restoreOwner": "integration.omnigent.profile_bound_execute",
            "requiredEvidence": ("externalStateRef", "runtimeSessionId", "firstMessageEvidenceRef"),
            "supportsLiveReattach": True,
            "supportsColdSession": True,
        },
        workspaceAuthority="moonmind_sandbox",
        checkpointCaptureKinds=("worktree_archive",),
        checkpointRestoreKinds=("worktree_archive",),
        checkpointCaptureActivity="workspace.capture_checkpoint",
        checkpointRestoreActivity="workspace.apply_checkpoint",
        checkpointArtifactContractVersion="workspace-checkpoint-v1",
        checkpointBoundarySupport={
            "after_execution": ("continue_to_gate",),
            "after_gate": ("continue_after_gate",),
            "before_publication": ("resume_publication",),
            "before_execution": ("rerun_failed_step",),
        },
        workspaceState={
            "authority": "moonmind_sandbox", "locatorKinds": ("sandbox",),
            "checkpointKinds": ("worktree_archive",), "restoreKinds": ("worktree_archive",),
            "captureActivity": "workspace.capture_checkpoint",
            "restoreActivity": "workspace.apply_checkpoint",
            "artifactContractVersion": "workspace-checkpoint-v1", "digestRequired": True,
            "boundarySupport": {
                "after_execution": ("continue_to_gate",), "after_gate": ("continue_after_gate",),
                "before_publication": ("resume_publication",), "before_execution": ("rerun_failed_step",),
            },
        },
        hostRealization={
            "owner": "moonmind", "modeCapabilities": (
                {
                    "mode": "static_compose",
                    "repositoryMutationIsolated": False,
                    "githubCredentialsIsolated": False,
                },
                {
                    "mode": "on_demand_docker",
                    "repositoryMutationIsolated": True,
                    "githubCredentialsIsolated": True,
                },
            ),
            "leaseOwner": "integration.omnigent.profile_bound_execute",
            "cleanupOwner": "integration.omnigent.profile_bound_execute",
            "workspaceMountTarget": "/workspaces/run",
        },
        supportsSameSessionContinuation=True,
        terminalContractIds=("omnigent_execution_terminal_v1",),
        postExecutionCheckpointCriticality="required",
    ),
    _descriptor(
        runtimeId="openclaw", runtimeFamily="external_provider",
        workspaceAuthority="external_provider", checkpointCaptureKinds=(),
        checkpointRestoreKinds=(), supportsSameSessionContinuation=False,
        terminalContractIds=("openclaw_execution_result_v1",),
        postExecutionCheckpointCriticality="unsupported",
    ),
)


class RuntimeExecutionCapabilityRegistry:
    """Validated canonical runtime registry with no unknown-runtime fallback."""

    def __init__(self, descriptors: Iterable[RuntimeExecutionCapabilities]) -> None:
        self._descriptors: dict[str, RuntimeExecutionCapabilities] = {}
        for descriptor in descriptors:
            runtime_id = normalize_runtime_id(descriptor.runtime_id)
            if runtime_id != descriptor.runtime_id:
                raise RuntimeCapabilityError(
                    f"runtime descriptor must use canonical id '{runtime_id}'"
                )
            if runtime_id in self._descriptors:
                raise RuntimeCapabilityError(f"duplicate runtime capability '{runtime_id}'")
            self._descriptors[runtime_id] = descriptor

    @property
    def runtime_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._descriptors))

    def resolve(self, runtime_id: str) -> RuntimeExecutionCapabilities:
        canonical_id = normalize_runtime_id(runtime_id)
        try:
            return self._descriptors[canonical_id]
        except KeyError as exc:
            raise RuntimeCapabilityError(
                f"unknown agent runtime capability '{canonical_id}'"
            ) from exc


RUNTIME_EXECUTION_CAPABILITIES = RuntimeExecutionCapabilityRegistry(_DESCRIPTORS)


def resolve_runtime_execution_capabilities(runtime_id: str) -> RuntimeExecutionCapabilities:
    """Resolve aliases once and return the immutable capability snapshot."""

    return RUNTIME_EXECUTION_CAPABILITIES.resolve(runtime_id)


def workspace_authority_from_locator(
    workspace_locator: Mapping[str, Any] | None,
) -> WorkspaceAuthority | None:
    """Read authority from the future locator contract without resolving paths."""

    if workspace_locator is None:
        return None
    kind = str(workspace_locator.get("kind") or "").strip()
    authorities: dict[str, WorkspaceAuthority] = {
        "sandbox": "moonmind_sandbox",
        "managed_runtime": "managed_runtime",
        "external_state": "external_provider",
    }
    if kind not in authorities:
        raise RuntimeCapabilityError(f"unknown workspace locator kind '{kind}'")
    return authorities[kind]


def validate_runtime_preflight(
    capabilities: RuntimeExecutionCapabilities,
    *,
    workspace_authority: WorkspaceAuthority | None = None,
    workspace_locator: Mapping[str, Any] | None = None,
    checkpoint_kind: str | None = None,
    restore_required: bool = False,
    same_session_continuation: bool = False,
) -> None:
    """Reject contradictory requests before agent execution starts."""

    locator_authority = workspace_authority_from_locator(workspace_locator)
    requested_authority = locator_authority or workspace_authority
    if requested_authority and requested_authority != capabilities.workspace_authority:
        raise RuntimeCapabilityError(
            f"runtime '{capabilities.runtime_id}' owns '{capabilities.workspace_authority}' "
            f"workspaces, not '{requested_authority}'"
        )
    if workspace_locator and capabilities.workspace_state:
        locator_kind = str(workspace_locator.get("kind") or "").strip()
        if locator_kind not in capabilities.workspace_state.locator_kinds:
            raise RuntimeCapabilityError(
                f"runtime '{capabilities.runtime_id}' does not accept workspace locator kind '{locator_kind}'"
            )
    if checkpoint_kind and checkpoint_kind not in capabilities.checkpoint_capture_kinds:
        raise RuntimeCapabilityError(
            f"runtime '{capabilities.runtime_id}' cannot capture checkpoint kind "
            f"'{checkpoint_kind}'"
        )
    if restore_required and checkpoint_kind not in capabilities.checkpoint_restore_kinds:
        raise RuntimeCapabilityError(
            f"runtime '{capabilities.runtime_id}' cannot restore checkpoint kind "
            f"'{checkpoint_kind}'"
        )
    if same_session_continuation and not capabilities.supports_same_session_continuation:
        raise RuntimeCapabilityError(
            f"runtime '{capabilities.runtime_id}' does not support same-session continuation"
        )


__all__ = [
    "CAPABILITY_SET_VERSION", "RUNTIME_EXECUTION_CAPABILITIES",
    "AgentIdentityCapability", "HostRealizationCapability", "RuntimeCapabilityError",
    "RuntimeExecutionCapabilities", "SessionStateCapability", "WorkspaceStateCapability",
    "RuntimeExecutionCapabilityRegistry", "resolve_runtime_execution_capabilities",
    "validate_runtime_preflight", "workspace_authority_from_locator",
]
