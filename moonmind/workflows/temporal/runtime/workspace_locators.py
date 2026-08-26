"""Owner-side resolution of managed-runtime workspace locators."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Protocol, TYPE_CHECKING

from moonmind.schemas.workspace_locator_models import (
    ManagedWorkspaceLocator,
    SandboxWorkspaceLocator,
    WORKSPACE_AUTHORITY_MISMATCH,
    WORKSPACE_IDENTITY_MISMATCH,
    WORKSPACE_LOCATOR_UNSUPPORTED,
    WorkspaceLocatorResolutionError,
)

if TYPE_CHECKING:
    from moonmind.workloads.unrestricted_container_tool import (
        UnrestrictedContainerWorkspace,
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
            return path.read_text(encoding="utf-8") == "materialized-v2"
        except OSError:
            return False

    def mark_materialized(self, workspace_id: str) -> None:
        """Record durable evidence that materialization completed."""
        self.store_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self._completion_marker_path(workspace_id)
        if self.is_materialized(workspace_id):
            return
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("materialized-v2")

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


def resolve_unrestricted_container_workspace(
    execution: Mapping[str, object],
    *,
    store: ManagedRunRecordStore,
) -> "UnrestrictedContainerWorkspace":
    """Resolve the current authorized workspace for one unrestricted container.

    The workflow injects the same managed-runtime locator ``container.run_job``
    uses, so both container surfaces resolve a workspace through one authority.
    MoonMind derives the repo, artifacts, and run-owned scratch directories from
    it; the caller never supplies a host path.
    """

    from moonmind.workloads.unrestricted_container_tool import (
        UnrestrictedContainerWorkspace,
    )

    raw_locator = execution.get("workspaceRef")
    if not isinstance(raw_locator, Mapping):
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_LOCATOR_UNSUPPORTED,
            "unrestricted container execution requires a workspace locator",
        )
    kind = str(raw_locator.get("kind") or "").strip()
    if kind != "managed_runtime":
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_LOCATOR_UNSUPPORTED,
            f"unsupported unrestricted container workspace locator: {kind!r}",
        )
    locator = ManagedWorkspaceLocator.model_validate(dict(raw_locator))
    repo_dir = resolve_managed_workspace_locator(
        locator,
        store=store,
        current_agent_run_id=locator.agent_run_id,
        current_runtime_id=locator.runtime_id,
    )
    run_root = repo_dir.parent if repo_dir.name == "repo" else repo_dir
    step_segment = _workspace_path_segment(execution.get("stepId"))
    artifacts_dir = (run_root / "artifacts" / step_segment).resolve()
    scratch_dir = (run_root / "scratch" / step_segment).resolve()
    for path in (artifacts_dir, scratch_dir):
        if not path.is_relative_to(run_root):
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "unrestricted container workspace escapes its authority",
            )
        path.mkdir(parents=True, exist_ok=True)
    return UnrestrictedContainerWorkspace(
        repo_dir=str(repo_dir),
        artifacts_dir=str(artifacts_dir),
        scratch_dir=str(scratch_dir),
    )


def _workspace_path_segment(value: object) -> str:
    """Return a traversal-free single path segment for a logical step id."""

    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip("-.")
    safe = safe.replace("..", "-")
    return safe[:120] or "step"
