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

CAPABILITY_SET_VERSION = "runtime-execution-capabilities-v2"
CheckpointResumePhase = Literal[
    "rerun_failed_step", "continue_to_gate", "continue_after_gate",
    "resume_publication", "retry_restoration",
]


class RuntimeCapabilityError(ValueError):
    """Raised when a runtime or capability/workspace combination is invalid."""


class RuntimeExecutionCapabilities(BaseModel):
    """Compact runtime-neutral policy snapshot recorded with an execution."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    capability_set_version: Literal[
        "runtime-execution-capabilities-v1", "runtime-execution-capabilities-v2"
    ] = Field(
        CAPABILITY_SET_VERSION, alias="capabilitySetVersion"
    )
    runtime_id: NonBlankStr = Field(..., alias="runtimeId")
    runtime_family: str | None = Field(None, alias="runtimeFamily")
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
            "after_execution": ("continue_to_gate",),
            "after_gate": ("continue_after_gate",),
            "before_publication": ("resume_publication",),
            "before_recovery_restoration": ("retry_restoration",),
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
        workspaceAuthority="external_provider",
        checkpointCaptureKinds=("external_state_ref",),
        checkpointRestoreKinds=("external_state_ref",),
        # This activity persists an already-produced external state reference;
        # it never resolves or reads an external provider workspace path.
        checkpointCaptureActivity="workspace.capture_checkpoint",
        checkpointRestoreActivity="integration.omnigent.execute",
        checkpointArtifactContractVersion="external-state-ref-v1",
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
    "RuntimeCapabilityError", "RuntimeExecutionCapabilities",
    "RuntimeExecutionCapabilityRegistry", "resolve_runtime_execution_capabilities",
    "validate_runtime_preflight", "workspace_authority_from_locator",
]
