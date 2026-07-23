"""Owner-side admission and restoration for cumulative Omnigent workspaces."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.schemas.workspace_locator_models import SandboxWorkspaceLocator
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
    resolve_sandbox_workspace_locator,
)


class RemediationWorkspaceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class RemediationWorkspaceBinding(BaseModel):
    """Frozen, path-free intent compiled for one semantic remediation attempt."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["v1"] = Field("v1", alias="schemaVersion")
    loop_id: str = Field(alias="loopId", min_length=1)
    branch_ref: str = Field(alias="branchRef", min_length=1)
    attempt_ordinal: int = Field(alias="attemptOrdinal", ge=1)
    workflow_id: str = Field(alias="workflowId", min_length=1)
    step_execution_id: str = Field(alias="stepExecutionId", min_length=1)
    base_checkpoint_ref: str = Field(alias="baseCheckpointRef", min_length=1)
    base_workspace_digest: str = Field(alias="baseWorkspaceDigest", pattern=r"^sha256:[0-9a-f]{64}$")
    expected_head_version: int = Field(alias="expectedHeadVersion", ge=0)
    head_authority_ref: str = Field(alias="headAuthorityRef", min_length=1)
    destination_workspace_locator: SandboxWorkspaceLocator = Field(alias="destinationWorkspaceLocator")
    workspace_policy: Literal["continue_from_loop_head"] = Field(
        "continue_from_loop_head", alias="workspacePolicy"
    )
    execution_profile_ref: str = Field(alias="executionProfileRef", min_length=1)
    host_profile_ref: str = Field(alias="hostProfileRef", min_length=1)
    launch_policy_ref: str = Field(alias="launchPolicyRef", min_length=1)
    workspace_capability_snapshot: dict[str, Any] = Field(alias="workspaceCapabilitySnapshot")

    @model_validator(mode="after")
    def validate_attempt_destination(self) -> "RemediationWorkspaceBinding":
        expected = hashlib.sha256(
            f"{self.workflow_id}:{self.step_execution_id}".encode()
        ).hexdigest()[:24]
        if self.destination_workspace_locator.workspace_id != expected:
            raise ValueError("destination workspace identity does not match the current attempt")
        snapshot = self.workspace_capability_snapshot
        if snapshot.get("locatorKind") != "sandbox" or snapshot.get("restore") is not True:
            raise ValueError("workspace capability snapshot does not authorize sandbox restore")
        return self


@dataclass(frozen=True)
class RemediationLoopHead:
    loop_id: str
    branch_ref: str
    checkpoint_ref: str
    workspace_digest: str
    head_version: int
    base_commit: str
    manifest_ref: str
    restore_request: dict[str, Any] | None = None


@dataclass(frozen=True)
class RemediationLiveWorkspace:
    loop_id: str
    branch_ref: str
    checkpoint_ref: str
    workspace_digest: str
    head_version: int
    workspace_id: str
    workflow_id: str
    step_execution_id: str
    mutation_authority: Literal["available", "held"] = "available"
    containment_valid: bool = True
    invalidated: bool = False


class RemediationCheckpointRestorer(Protocol):
    async def restore(
        self, *, head: RemediationLoopHead, destination: Path,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        raise NotImplementedError


class RemediationHeadLoader(Protocol):
    async def load(self, ref: str) -> Mapping[str, Any]:
        raise NotImplementedError


class ArtifactRemediationHeadLoader:
    def __init__(self, artifact_service: Any) -> None:
        self.artifact_service = artifact_service

    async def load(self, ref: str) -> Mapping[str, Any]:
        try:
            _artifact, payload = await self.artifact_service.read(
                artifact_id=ref,
                principal="service:remediation_workspace",
                allow_restricted_raw=True,
            )
            value = json.loads(payload)
        except Exception as exc:
            raise RemediationWorkspaceError(
                "REMEDIATION_LOOP_HEAD_MISSING",
                "durable loop-head artifact is unavailable",
            ) from exc
        if not isinstance(value, Mapping):
            raise RemediationWorkspaceError(
                "REMEDIATION_WORKSPACE_AUTHORITY_INVALID",
                "durable loop-head artifact is invalid",
            )
        return value


class ManagedServiceRemediationRestorer:
    """Adapt the canonical checkpoint restore data plane to sandbox destinations."""

    def __init__(self, service: Any) -> None:
        self.service = service

    async def restore(
        self,
        *,
        head: RemediationLoopHead,
        destination: Path,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        if not isinstance(head.restore_request, Mapping):
            raise RemediationWorkspaceError(
                "REMEDIATION_RESTORE_REQUEST_MISSING",
                "durable loop head has no canonical checkpoint restore request",
            )
        request = dict(head.restore_request)
        destination_request = dict(request.get("destination") or {})
        destination_request.update(
            {
                "runtimeId": "codex_cli",
                "agentRunId": destination.parent.name,
                "relativePath": "repo",
            }
        )
        request.update(
            {
                "destination": destination_request,
                "idempotencyKey": idempotency_key,
            }
        )
        result = await self.service.restore(request)
        return {
            "checkpointRef": result.get("checkpointRef"),
            "workspaceDigest": head.workspace_digest,
            "baseCommit": result.get("baseCommit"),
            "manifestRef": head.manifest_ref,
            "restoreEvidenceRef": result.get("restorationEvidenceRef"),
        }


class RemediationWorkspaceOwner(Protocol):
    async def admit_and_resolve(
        self, *, binding: RemediationWorkspaceBinding, workflow_id: str,
        step_execution_id: str,
    ) -> Mapping[str, Any]:
        raise NotImplementedError


class SandboxRemediationWorkspaceOwner:
    """Durable head authority with explicit live-reuse and cold-restore paths."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        restorer: RemediationCheckpointRestorer | None = None,
        head_loader: RemediationHeadLoader | None = None,
    ) -> None:
        self.root = Path(workspace_root).resolve()
        self.records = SandboxWorkspaceRecordStore(self.root)
        self.restorer = restorer
        self.head_loader = head_loader
        self.state_root = self.records.store_root / "remediation"

    def _state_path(self, kind: str, loop_id: str, branch_ref: str) -> Path:
        key = hashlib.sha256(f"{loop_id}\0{branch_ref}".encode()).hexdigest()
        return self.state_root / f"{kind}-{key}.json"

    @staticmethod
    def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)

    def record_loop_head(self, head: RemediationLoopHead) -> None:
        """Persist controller-owned loop-head authority before compiling an attempt."""
        self._atomic_json(self._state_path("head", head.loop_id, head.branch_ref), asdict(head))

    def record_live_workspace(self, workspace: RemediationLiveWorkspace) -> None:
        self._atomic_json(
            self._state_path("live", workspace.loop_id, workspace.branch_ref),
            asdict(workspace),
        )

    def _load(self, kind: str, binding: RemediationWorkspaceBinding) -> Mapping[str, Any] | None:
        path = self._state_path(kind, binding.loop_id, binding.branch_ref)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise RemediationWorkspaceError("REMEDIATION_WORKSPACE_AUTHORITY_INVALID", "durable owner state is invalid") from exc
        if not isinstance(value, Mapping):
            raise RemediationWorkspaceError("REMEDIATION_WORKSPACE_AUTHORITY_INVALID", "durable owner state is invalid")
        return value

    async def _authoritative_head(self, binding: RemediationWorkspaceBinding) -> RemediationLoopHead:
        value = (
            await self.head_loader.load(binding.head_authority_ref)
            if self.head_loader is not None
            else self._load("head", binding)
        )
        if value is None:
            raise RemediationWorkspaceError("REMEDIATION_LOOP_HEAD_MISSING", "durable loop head was not found")
        head = RemediationLoopHead(
            loop_id=str(value.get("loop_id") or value.get("loopId") or ""),
            branch_ref=str(value.get("branch_ref") or value.get("branchRef") or ""),
            checkpoint_ref=str(
                value.get("checkpoint_ref") or value.get("checkpointRef") or ""
            ),
            workspace_digest=str(
                value.get("workspace_digest") or value.get("workspaceDigest") or ""
            ),
            head_version=int(
                value.get("head_version")
                if value.get("head_version") is not None
                else value.get("headVersion", -1)
            ),
            base_commit=str(value.get("base_commit") or value.get("baseCommit") or ""),
            manifest_ref=str(
                value.get("manifest_ref") or value.get("manifestRef") or ""
            ),
            restore_request=(
                dict(value.get("restore_request") or value.get("restoreRequest"))
                if isinstance(
                    value.get("restore_request") or value.get("restoreRequest"),
                    Mapping,
                )
                else None
            ),
        )
        if (
            head.loop_id != binding.loop_id
            or head.branch_ref != binding.branch_ref
            or head.checkpoint_ref != binding.base_checkpoint_ref
            or head.workspace_digest != binding.base_workspace_digest
            or head.head_version != binding.expected_head_version
        ):
            raise RemediationWorkspaceError("REMEDIATION_LOOP_HEAD_MISMATCH", "requested base is not the durable loop head")
        return head

    def _try_live_reuse(self, binding: RemediationWorkspaceBinding, head: RemediationLoopHead) -> tuple[Path, SandboxWorkspaceLocator] | None:
        value = self._load("live", binding)
        if value is None:
            return None
        live = RemediationLiveWorkspace(**value)
        if (
            live.loop_id != head.loop_id or live.branch_ref != head.branch_ref
            or live.checkpoint_ref != head.checkpoint_ref
            or live.workspace_digest != head.workspace_digest
            or live.head_version != head.head_version
            or live.mutation_authority != "available"
            or not live.containment_valid or live.invalidated
        ):
            return None
        record = self.records.load(live.workspace_id)
        if record is None:
            return None
        try:
            locator = SandboxWorkspaceLocator(kind="sandbox", workspaceId=live.workspace_id, relativePath="repo")
            path = resolve_sandbox_workspace_locator(
                locator,
                workspace_root=self.root, expected_workspace_id=live.workspace_id,
                owner_record=record, expected_workflow_id=live.workflow_id,
                expected_step_execution_id=live.step_execution_id,
            )
            return path, locator
        except ValueError:
            return None

    async def admit_and_resolve(
        self, *, binding: RemediationWorkspaceBinding, workflow_id: str,
        step_execution_id: str,
    ) -> Mapping[str, Any]:
        if binding.workflow_id != workflow_id or binding.step_execution_id != step_execution_id:
            raise RemediationWorkspaceError("REMEDIATION_WORKSPACE_OWNER_MISMATCH", "binding belongs to a different execution")
        head = await self._authoritative_head(binding)
        live_resolution = self._try_live_reuse(binding, head)
        if live_resolution is not None:
            live_path, _live_locator = live_resolution
            locator = binding.destination_workspace_locator
            record = SandboxWorkspaceRecord(
                workspace_id=locator.workspace_id,
                workflow_id=workflow_id,
                step_execution_id=step_execution_id,
                relative_path=locator.relative_path,
            )
            self.records.ensure(record)
            destination = resolve_sandbox_workspace_locator(
                locator,
                workspace_root=self.root,
                expected_workspace_id=locator.workspace_id,
                owner_record=record,
                expected_workflow_id=workflow_id,
                expected_step_execution_id=step_execution_id,
                must_exist=False,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if destination.resolve() != live_path.resolve():
                    raise RemediationWorkspaceError(
                        "REMEDIATION_WORKSPACE_DESTINATION_CONFLICT",
                        "attempt destination already contains unrelated state",
                    )
            else:
                # Transfer the validated branch workspace into attempt-local
                # ownership. Host cleanup still owns neither location.
                os.replace(live_path, destination)
            return {
                "workspaceLocator": locator.model_dump(by_alias=True, mode="json"),
                "workspacePath": str(destination), "workspaceState": "live_reused",
                "restoreEvidenceRef": None,
            }

        locator = binding.destination_workspace_locator
        record = SandboxWorkspaceRecord(
            workspace_id=locator.workspace_id, workflow_id=workflow_id,
            step_execution_id=step_execution_id, relative_path=locator.relative_path,
        )
        self.records.ensure(record)
        destination = resolve_sandbox_workspace_locator(
            locator, workspace_root=self.root, expected_workspace_id=locator.workspace_id,
            owner_record=record, expected_workflow_id=workflow_id,
            expected_step_execution_id=step_execution_id, must_exist=False,
        )
        if self.restorer is None:
            raise RemediationWorkspaceError("REMEDIATION_WORKSPACE_RESTORE_UNAVAILABLE", "checkpoint restore boundary is not configured")
        result = await self.restorer.restore(
            head=head, destination=destination,
            idempotency_key=f"{workflow_id}:{step_execution_id}:restore",
        )
        expected = {
            "checkpointRef": head.checkpoint_ref, "workspaceDigest": head.workspace_digest,
            "baseCommit": head.base_commit, "manifestRef": head.manifest_ref,
        }
        if any(result.get(key) != value for key, value in expected.items()) or not result.get("restoreEvidenceRef"):
            raise RemediationWorkspaceError("REMEDIATION_WORKSPACE_RESTORE_MISMATCH", "restore result does not match the durable loop head")
        resolved = resolve_sandbox_workspace_locator(
            locator, workspace_root=self.root, expected_workspace_id=locator.workspace_id,
            owner_record=record, expected_workflow_id=workflow_id,
            expected_step_execution_id=step_execution_id,
        )
        evidence = {**expected, "restoreEvidenceRef": result["restoreEvidenceRef"], "loopId": head.loop_id, "branchRef": head.branch_ref, "headVersion": head.head_version}
        self._atomic_json(self.records.store_root / f"{locator.workspace_id}.restore.json", evidence)
        return {
            "workspaceLocator": locator.model_dump(by_alias=True, mode="json"),
            "workspacePath": str(resolved), "workspaceState": "cold_restored",
            "restoreEvidenceRef": result["restoreEvidenceRef"],
        }


__all__ = [
    "ArtifactRemediationHeadLoader", "ManagedServiceRemediationRestorer",
    "RemediationCheckpointRestorer", "RemediationHeadLoader",
    "RemediationLiveWorkspace", "RemediationLoopHead",
    "RemediationWorkspaceBinding", "RemediationWorkspaceError",
    "RemediationWorkspaceOwner", "SandboxRemediationWorkspaceOwner",
]
