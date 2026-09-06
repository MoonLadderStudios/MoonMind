"""Owner-side resolution of managed-runtime workspace locators."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
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

    def is_materialized(self, workspace_id: str) -> bool:
        """Return whether workspace materialization durably completed.

        A completion marker is written only after the full clone, checkout, and
        restore-input materialization succeeded, so a retry can distinguish a
        finished workspace from a partially built directory left by a prior
        attempt that failed mid-materialization.
        """
        path = self._completion_marker_path(workspace_id)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return False
        if text == "materialized-v2":
            return True
        try:
            payload = json.loads(text)
        except (ValueError, json.JSONDecodeError):
            return False
        return (
            isinstance(payload, dict) and payload.get("format") == "materialized-v3"
        )

    def mark_materialized(self, workspace_id: str) -> None:
        """Record durable evidence that materialization completed."""
        self.mark_materialized_for(workspace_id, binding=None)

    def mark_materialized_for(
        self, workspace_id: str, binding: dict[str, Any] | None
    ) -> None:
        """Record completion bound to source digest, contract, owner, attempt.

        The ready marker binds the admitted source/digest, the restore
        contract/version, the input-manifest digest, the target owner, and the
        attempt/generation — not just a directory or a previous marker. A retry
        reconciles the same generation; changed inputs require an explicit new
        import with a different binding.
        """
        self.store_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self._completion_marker_path(workspace_id)
        if self.is_materialized(workspace_id) and binding is None:
            return
        if binding is not None and self.is_materialized_for(workspace_id, binding):
            return
        payload = json.dumps(
            {"format": "materialized-v3", "binding": binding or {"legacy": True}},
            sort_keys=True,
        )
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)

    def load_materialized_binding(self, workspace_id: str) -> dict[str, Any] | None:
        """Return the bound ready marker, or None when absent/legacy/unparseable."""
        path = self._completion_marker_path(workspace_id)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        if text == "materialized-v2":
            return None
        try:
            payload = json.loads(text)
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("format") != "materialized-v3":
            return None
        binding = payload.get("binding")
        return binding if isinstance(binding, dict) else None

    def is_materialized_for(self, workspace_id: str, binding: dict[str, Any]) -> bool:
        """Return whether the recorded ready marker matches this exact binding."""
        recorded = self.load_materialized_binding(workspace_id)
        if recorded is None:
            return False
        if recorded.get("legacy"):
            return False
        for key in (
            "sourceKind",
            "sourceDigest",
            "restoreContract",
            "inputManifestDigest",
            "targetOwner",
            "attemptId",
            "generation",
            "overlay",
        ):
            if recorded.get(key) != binding.get(key):
                return False
        return True

    def _claims_dir(self, workspace_id: str) -> Path:
        candidate = (self.store_root / f"{workspace_id}.grants").resolve()
        if candidate.parent != self.store_root.resolve():
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "sandbox workspace grant registry escapes its authority",
            )
        return candidate

    def claim_existing_workspace(self, workspace_id: str, grant: Any) -> None:
        """Record exclusive/read-only use of another workflow's workspace.

        Existing-workspace grants declare exclusive writable use or explicitly
        supported read-only sharing. An exclusive claim conflicts with any
        other active claim; read-only claims coexist only with read-only
        claims. Reclaiming the same grant is idempotent for retries.
        """

        claims = self._claims_dir(workspace_id)
        claims.mkdir(mode=0o700, parents=True, exist_ok=True)
        grant_id = str(getattr(grant, "grant_id", "") or "")
        mode = str(getattr(grant, "mode", "") or "")
        if not grant_id or mode not in {"exclusive", "read_only"}:
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "existing-workspace grant claim is invalid",
            )
        for existing_path in sorted(claims.glob("*.json")):
            if existing_path.name == f"{grant_id}.json":
                continue
            try:
                existing = json.loads(existing_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(existing, dict):
                continue
            if mode == "exclusive" or existing.get("mode") == "exclusive":
                raise WorkspaceLocatorResolutionError(
                    WORKSPACE_IDENTITY_MISMATCH,
                    "existing workspace is already granted to another execution",
                )
        claim_path = claims / f"{grant_id}.json"
        payload = json.dumps(
            {
                "grantId": grant_id,
                "mode": mode,
                "granteeWorkflowId": str(
                    getattr(grant, "grantee_workflow_id", "") or ""
                ),
                "expectedGeneration": int(
                    getattr(grant, "expected_generation", 0) or 0
                ),
            },
            sort_keys=True,
        )
        try:
            descriptor = os.open(
                claim_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
        except FileExistsError:
            return
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)

    def release_existing_workspace(self, workspace_id: str, grant_id: str) -> None:
        """Release ownership of a previously claimed existing workspace."""

        claim_path = self._claims_dir(workspace_id) / f"{grant_id}.json"
        try:
            claim_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "existing-workspace grant release failed",
            ) from exc

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
