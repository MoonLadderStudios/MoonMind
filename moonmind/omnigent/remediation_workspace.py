"""Admission contract for cumulative Omnigent remediation workspaces.

Implements the workspace authority boundary from MoonLadderStudios/MoonMind#3474.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.schemas.workspace_locator_models import SandboxWorkspaceLocator
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecordStore,
    resolve_sandbox_workspace_locator,
)


class RemediationWorkspaceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class RemediationWorkspaceBinding(BaseModel):
    """Frozen loop-head authority passed to one semantic attempt."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["v1"] = Field("v1", alias="schemaVersion")
    loop_id: str = Field(alias="loopId", min_length=1)
    branch_ref: str = Field(alias="branchRef", min_length=1)
    attempt_ordinal: int = Field(alias="attemptOrdinal", ge=1)
    workflow_id: str = Field(alias="workflowId", min_length=1)
    step_execution_id: str = Field(alias="stepExecutionId", min_length=1)
    base_checkpoint_ref: str = Field(alias="baseCheckpointRef", min_length=1)
    base_workspace_digest: str = Field(
        alias="baseWorkspaceDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    expected_head_version: int = Field(alias="expectedHeadVersion", ge=0)
    current_head_checkpoint_ref: str = Field(
        alias="currentHeadCheckpointRef", min_length=1
    )
    current_head_workspace_digest: str = Field(
        alias="currentHeadWorkspaceDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    current_head_version: int = Field(alias="currentHeadVersion", ge=0)
    destination_workspace_locator: SandboxWorkspaceLocator = Field(
        alias="destinationWorkspaceLocator"
    )
    workspace_policy: Literal["continue_from_loop_head"] = Field(
        "continue_from_loop_head", alias="workspacePolicy"
    )
    restore_evidence_ref: str = Field(alias="restoreEvidenceRef", min_length=1)
    restore_manifest_ref: str = Field(alias="restoreManifestRef", min_length=1)
    restore_base_commit: str = Field(alias="restoreBaseCommit", min_length=1)
    reuse_live_workspace: bool = Field(False, alias="reuseLiveWorkspace")

    @model_validator(mode="after")
    def validate_frozen_head(self) -> "RemediationWorkspaceBinding":
        if self.base_checkpoint_ref != self.current_head_checkpoint_ref:
            raise ValueError("base checkpoint does not match the current loop head")
        if self.base_workspace_digest != self.current_head_workspace_digest:
            raise ValueError(
                "base workspace digest does not match the current loop head"
            )
        if self.expected_head_version != self.current_head_version:
            raise ValueError(
                "expected head version does not match the current loop head"
            )
        expected_workspace_id = hashlib.sha256(
            f"{self.workflow_id}:{self.step_execution_id}".encode()
        ).hexdigest()[:24]
        if self.destination_workspace_locator.workspace_id != expected_workspace_id:
            raise ValueError(
                "destination workspace identity does not match the current attempt"
            )
        return self


class RemediationWorkspaceOwner(Protocol):
    async def admit_and_resolve(
        self,
        *,
        binding: RemediationWorkspaceBinding,
        workflow_id: str,
        step_execution_id: str,
    ) -> Mapping[str, Any]: ...


class SandboxRemediationWorkspaceOwner:
    """Validate owner-persisted restore evidence and resolve its destination."""

    def __init__(self, workspace_root: str | Path) -> None:
        self.root = Path(workspace_root).resolve()
        self.records = SandboxWorkspaceRecordStore(self.root)

    async def admit_and_resolve(
        self,
        *,
        binding: RemediationWorkspaceBinding,
        workflow_id: str,
        step_execution_id: str,
    ) -> Mapping[str, Any]:
        if binding.step_execution_id != step_execution_id:
            raise RemediationWorkspaceError(
                "REMEDIATION_WORKSPACE_OWNER_MISMATCH",
                "binding belongs to a different Step Execution",
            )
        if binding.workflow_id != workflow_id:
            raise RemediationWorkspaceError(
                "REMEDIATION_WORKSPACE_OWNER_MISMATCH",
                "binding belongs to a different workflow",
            )
        locator = binding.destination_workspace_locator
        record = self.records.load(locator.workspace_id)
        if record is None:
            raise RemediationWorkspaceError(
                "REMEDIATION_WORKSPACE_RESERVATION_MISSING",
                "destination workspace has not been reserved by its owner",
            )
        try:
            path = resolve_sandbox_workspace_locator(
                locator,
                workspace_root=self.root,
                expected_workspace_id=locator.workspace_id,
                owner_record=record,
                expected_workflow_id=workflow_id,
                expected_step_execution_id=step_execution_id,
            )
        except ValueError as exc:
            raise RemediationWorkspaceError(
                "REMEDIATION_WORKSPACE_OWNER_MISMATCH", str(exc)
            ) from exc
        evidence_path = self.records.store_root / f"{locator.workspace_id}.restore.json"
        try:
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RemediationWorkspaceError(
                "REMEDIATION_WORKSPACE_RESTORE_UNVERIFIED",
                "owner restore evidence is unavailable",
            ) from exc
        expected = {
            "loopId": binding.loop_id,
            "branchRef": binding.branch_ref,
            "stepExecutionId": step_execution_id,
            "checkpointRef": binding.base_checkpoint_ref,
            "workspaceDigest": binding.base_workspace_digest,
            "headVersion": binding.expected_head_version,
            "restoreEvidenceRef": binding.restore_evidence_ref,
            "restoreManifestRef": binding.restore_manifest_ref,
            "baseCommit": binding.restore_base_commit,
        }
        if any(evidence.get(key) != value for key, value in expected.items()):
            raise RemediationWorkspaceError(
                "REMEDIATION_WORKSPACE_RESTORE_MISMATCH",
                "owner restore evidence does not match the frozen loop head",
            )
        digest = "sha256:" + hashlib.sha256(
            json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return {
            "workspaceLocator": locator.model_dump(by_alias=True, mode="json"),
            "workspacePath": str(path),
            "restoreEvidenceRef": binding.restore_evidence_ref,
            "restoreEvidenceDigest": digest,
            "workspaceState": (
                "live_reused" if binding.reuse_live_workspace else "cold_restored"
            ),
        }


__all__ = [
    "RemediationWorkspaceBinding",
    "RemediationWorkspaceError",
    "RemediationWorkspaceOwner",
    "SandboxRemediationWorkspaceOwner",
]
