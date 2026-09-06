"""Owner-side resolution of managed-runtime workspace locators."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from moonmind.schemas.workspace_locator_models import (
    ManagedWorkspaceLocator,
    SandboxWorkspaceLocator,
    WORKSPACE_AUTHORITY_MISMATCH,
    WORKSPACE_IDENTITY_MISMATCH,
    WorkspaceLocatorResolutionError,
)


@dataclass(frozen=True)
class ExistingWorkspaceGrant:
    """Server-issued grant authorizing use of an existing workspace directory."""

    workspace_id: str
    granted_workflow_id: str
    granted_step_execution_id: str
    read_only: bool = False
    expected_generation: str | None = None
    expires_at: str | None = None


def parse_existing_workspace_grant(
    payload: Any,
    *,
    expected_workflow_id: str,
    expected_step_execution_id: str,
    expected_workspace_id: str,
) -> ExistingWorkspaceGrant:
    """Validate a server-issued existing-workspace use grant.

    Root containment alone does not establish permission to use a sibling
    workflow's directory; the caller must present a grant issued to the current
    owner identity. Stale (expired), wrong-owner, or mismatched-workspace
    grants fail closed.
    """

    if not isinstance(payload, dict):
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH,
            "existing workspace use requires a server-issued ownership grant",
        )
    workspace_id = str(payload.get("workspaceId") or "").strip()
    granted_workflow_id = str(
        payload.get("grantedWorkflowId") or payload.get("workflowId") or ""
    ).strip()
    granted_step_execution_id = str(
        payload.get("grantedStepExecutionId")
        or payload.get("stepExecutionId")
        or ""
    ).strip()
    if not workspace_id or not granted_workflow_id or not granted_step_execution_id:
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH,
            "existing workspace grant is missing owner identity",
        )
    if workspace_id != expected_workspace_id:
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_IDENTITY_MISMATCH,
            "existing workspace grant does not match the requested workspace",
        )
    if (
        granted_workflow_id != expected_workflow_id
        or granted_step_execution_id != expected_step_execution_id
    ):
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_IDENTITY_MISMATCH,
            "existing workspace grant was not issued to the current execution",
        )
    expires_at = payload.get("expiresAt")
    expires_text = str(expires_at or "").strip() or None
    if expires_text:
        try:
            deadline = datetime.fromisoformat(expires_text.replace("Z", "+00:00"))
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=UTC)
            if deadline <= datetime.now(tz=UTC):
                raise WorkspaceLocatorResolutionError(
                    WORKSPACE_AUTHORITY_MISMATCH,
                    "existing workspace grant has expired",
                )
        except WorkspaceLocatorResolutionError:
            raise
        except (ValueError, TypeError) as exc:
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "existing workspace grant expiry is invalid",
            ) from exc
    expected_generation = payload.get("expectedGeneration")
    return ExistingWorkspaceGrant(
        workspace_id=workspace_id,
        granted_workflow_id=granted_workflow_id,
        granted_step_execution_id=granted_step_execution_id,
        read_only=bool(payload.get("readOnly", False)),
        expected_generation=(
            str(expected_generation).strip() if expected_generation else None
        ),
        expires_at=expires_text,
    )


def build_materialization_fingerprint(
    *,
    source_kind: str,
    source_digest: str | None,
    restore_contract: str | None,
    restore_version: str | None,
    input_manifest_digest: str | None,
    owner_workflow_id: str,
    owner_step_execution_id: str,
    workspace_id: str,
) -> dict[str, str]:
    """Build the canonical fingerprint binding a ready marker to its inputs."""

    payload = {
        "sourceKind": str(source_kind or "").strip(),
        "sourceDigest": str(source_digest or "").strip(),
        "restoreContract": str(restore_contract or "").strip(),
        "restoreVersion": str(restore_version or "").strip(),
        "inputManifestDigest": str(input_manifest_digest or "").strip(),
        "ownerWorkflowId": str(owner_workflow_id or "").strip(),
        "ownerStepExecutionId": str(owner_step_execution_id or "").strip(),
        "workspaceId": str(workspace_id or "").strip(),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["fingerprint"] = (
        "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    )
    return payload


WORKSPACE_READY_MARKER_VERSION = "materialized-v3"


@dataclass(frozen=True)
class SandboxWorkspaceRecord:
    """Durable owner evidence for a sandbox workspace identity."""

    workspace_id: str
    workflow_id: str
    step_execution_id: str
    relative_path: str


class SandboxWorkspaceRecordStore:
    """Filesystem-backed owner records kept outside materialized workspaces."""

    def __init__(self, workspace_root: Path) -> None:
        self._authority = (workspace_root / "temporal_sandbox").resolve()
        self.store_root = self._authority / ".workspace_records"

    def _record_path(self, workspace_id: str) -> Path:
        candidate = (self.store_root / f"{workspace_id}.json").resolve()
        if candidate.parent != self.store_root.resolve():
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "sandbox workspace record escapes its authority",
            )
        return candidate

    def _completion_marker_path(self, workspace_id: str) -> Path:
        candidate = (self.store_root / f"{workspace_id}.materialized").resolve()
        if candidate.parent != self.store_root.resolve():
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "sandbox workspace completion marker escapes its authority",
            )
        return candidate

    def is_materialized_for(
        self, workspace_id: str, fingerprint: dict[str, str] | None
    ) -> bool:
        """Return whether the ready marker matches the admitted source/attempt.

        The marker binds source digest, restore contract/version,
        input-manifest digest, target owner, and attempt. A directory left by a
        prior clone, a stale v2 marker, or a marker for different inputs does
        not authorize reuse: changed inputs require an explicit new import and
        a retry reconciles the same generation.
        """

        path = self._completion_marker_path(workspace_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(payload, dict):
            return False
        if payload.get("version") != WORKSPACE_READY_MARKER_VERSION:
            return False
        if fingerprint is None:
            return False
        expected = dict(fingerprint)
        recorded = payload.get("fingerprint")
        if not isinstance(recorded, dict):
            return False
        for key in (
            "sourceKind",
            "sourceDigest",
            "restoreContract",
            "restoreVersion",
            "inputManifestDigest",
            "ownerWorkflowId",
            "ownerStepExecutionId",
            "workspaceId",
            "fingerprint",
        ):
            if str(recorded.get(key) or "") != str(expected.get(key) or ""):
                return False
        return str(recorded.get("workspaceId") or "") == str(workspace_id)

    def mark_materialized_for(
        self, workspace_id: str, fingerprint: dict[str, str] | None
    ) -> None:
        """Record durable evidence that the fingerprinted generation is ready."""

        if fingerprint is None:
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "ready marker requires the admitted source fingerprint",
            )
        self.store_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.is_materialized_for(workspace_id, fingerprint):
            return
        path = self._completion_marker_path(workspace_id)
        payload = json.dumps(
            {"version": WORKSPACE_READY_MARKER_VERSION, "fingerprint": dict(fingerprint)},
            sort_keys=True,
        )
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)

    def load(self, workspace_id: str) -> SandboxWorkspaceRecord | None:
        path = self._record_path(workspace_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return SandboxWorkspaceRecord(**payload)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "sandbox workspace owner record is invalid",
            ) from exc

    def ensure(self, record: SandboxWorkspaceRecord) -> None:
        existing = self.load(record.workspace_id)
        if existing is not None:
            if existing != record:
                raise WorkspaceLocatorResolutionError(
                    WORKSPACE_IDENTITY_MISMATCH,
                    "sandbox workspace owner record does not match the current execution",
                )
            return
        self.store_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self._record_path(record.workspace_id)
        payload = json.dumps(asdict(record), sort_keys=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            concurrent = self.load(record.workspace_id)
            if concurrent != record:
                raise WorkspaceLocatorResolutionError(
                    WORKSPACE_IDENTITY_MISMATCH,
                    "sandbox workspace owner record changed during persistence",
                )
            return
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)


def resolve_sandbox_workspace_locator(
    locator: SandboxWorkspaceLocator,
    *,
    workspace_root: Path,
    expected_workspace_id: str,
    owner_record: SandboxWorkspaceRecord | None = None,
    expected_workflow_id: str | None = None,
    expected_step_execution_id: str | None = None,
    must_exist: bool = True,
) -> Path:
    """Resolve a sandbox locator at its owning worker boundary."""
    if locator.workspace_id != expected_workspace_id:
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_IDENTITY_MISMATCH,
            "sandbox locator does not match the current execution identity",
        )
    if owner_record is not None:
        if (
            owner_record.workspace_id != locator.workspace_id
            or owner_record.relative_path != locator.relative_path
            or owner_record.workflow_id != expected_workflow_id
            or owner_record.step_execution_id != expected_step_execution_id
        ):
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_IDENTITY_MISMATCH,
                "sandbox workspace owner record does not match the locator",
            )
    authority = (workspace_root / "temporal_sandbox").resolve()
    owned_root = (authority / locator.workspace_id).resolve()
    if owned_root.parent != authority:
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH, "sandbox workspace identity escapes its authority"
        )
    workspace = (owned_root / locator.relative_path).resolve()
    if not workspace.is_relative_to(owned_root):
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH, "sandbox relative path escapes its workspace"
        )
    if must_exist and not workspace.is_dir():
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH, "authorized sandbox workspace is unavailable"
        )
    return workspace


def daemon_visible_workspace_path(
    path: Path,
    *,
    daemon_root: Path | str | None = None,
) -> Path:
    """Translate a worker path to a daemon-visible bind path at the trusted boundary.

    The translation contract is deployment-selected and deterministic:

    - ``local`` (the default when no daemon root is configured): the Docker daemon
      shares the worker filesystem, so the worker path is already daemon-visible and
      is returned unchanged. Configuring a daemon root remap in this mode is a
      contradiction and fails closed.
    - ``remote``: the daemon runs against a distinct filesystem view, so the worker
      path is rebased from ``WORKFLOW_WORKSPACE_ROOT`` onto
      ``WORKFLOW_WORKSPACE_DAEMON_ROOT`` after a containment check. A remote
      selection without a configured daemon root cannot produce a valid bind path
      and fails closed rather than leaking a worker-only path to the daemon.

    ``WORKFLOW_DOCKER_DAEMON_MODE`` selects the contract explicitly; when unset it is
    inferred from whether a daemon root is configured, preserving the prior
    behavior. Translation only ever runs at this trusted worker/runtime boundary,
    after authorization and materialization.
    """
    worker_root_text = os.getenv("WORKFLOW_WORKSPACE_ROOT", "").strip()
    daemon_root_text = str(
        daemon_root
        if daemon_root is not None
        else os.getenv("WORKFLOW_WORKSPACE_DAEMON_ROOT", "")
    ).strip()
    resolved = path.resolve()

    mode = os.getenv("WORKFLOW_DOCKER_DAEMON_MODE", "").strip().lower()
    if not mode:
        mode = "remote" if daemon_root_text else "local"
    if mode not in {"local", "remote"}:
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH,
            "WORKFLOW_DOCKER_DAEMON_MODE must be 'local' or 'remote'",
        )

    if mode == "local":
        if daemon_root_text:
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "local daemon mode must not configure a daemon root remap",
            )
        return resolved

    # Remote daemon translation contract.
    if not daemon_root_text:
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH,
            "remote daemon mode requires WORKFLOW_WORKSPACE_DAEMON_ROOT",
        )
    if not worker_root_text:
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH,
            "daemon workspace mapping requires WORKFLOW_WORKSPACE_ROOT",
        )
    worker_root = Path(worker_root_text).resolve()
    if not resolved.is_relative_to(worker_root):
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH, "workspace is outside the daemon mapping authority"
        )
    return Path(daemon_root_text).resolve() / resolved.relative_to(worker_root)


class ManagedRunRecord(Protocol):
    run_id: str
    runtime_id: str
    workspace_path: str


class ManagedRunRecordStore(Protocol):
    store_root: Path

    def load(self, run_id: str) -> ManagedRunRecord | None:
        """Load the managed run record identified by ``run_id``."""


def resolve_managed_workspace_locator(
    locator: ManagedWorkspaceLocator,
    *,
    store: ManagedRunRecordStore,
    current_agent_run_id: str,
    current_runtime_id: str,
) -> Path:
    """Resolve a locator only after caller, record, and filesystem authority agree."""
    if locator.agent_run_id != current_agent_run_id or locator.runtime_id != current_runtime_id:
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_IDENTITY_MISMATCH, "managed locator does not match the current run identity"
        )
    record = store.load(locator.agent_run_id)
    if record is None:
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_IDENTITY_MISMATCH, "managed run record was not found"
        )
    if record.run_id != locator.agent_run_id or record.runtime_id != locator.runtime_id:
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_IDENTITY_MISMATCH, "managed run record does not match the locator"
        )
    workspace_root = Path(record.workspace_path).resolve()
    store_authority = store.store_root.resolve().parent
    if not workspace_root.is_relative_to(store_authority):
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH, "managed workspace is outside the configured store"
        )
    workspace = (
        workspace_root
        if locator.relative_path == "."
        or (locator.relative_path == "repo" and workspace_root.name == "repo")
        else (workspace_root / locator.relative_path).resolve()
    )
    if not workspace.is_relative_to(workspace_root):
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH, "managed relative path escapes its workspace"
        )
    return workspace
