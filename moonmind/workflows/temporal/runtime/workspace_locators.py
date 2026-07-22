"""Owner-side resolution of managed-runtime workspace locators."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from moonmind.schemas.workspace_locator_models import (
    ManagedWorkspaceLocator,
    SandboxWorkspaceLocator,
    WORKSPACE_AUTHORITY_MISMATCH,
    WORKSPACE_IDENTITY_MISMATCH,
    WorkspaceLocatorResolutionError,
)


def resolve_sandbox_workspace_locator(
    locator: SandboxWorkspaceLocator,
    *,
    workspace_root: Path,
    expected_workspace_id: str,
    must_exist: bool = True,
) -> Path:
    """Resolve a sandbox locator at its owning worker boundary."""
    if locator.workspace_id != expected_workspace_id:
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_IDENTITY_MISMATCH,
            "sandbox locator does not match the current execution identity",
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


def daemon_visible_workspace_path(path: Path) -> Path:
    """Translate a worker path through the deployment-owned daemon root mapping."""
    worker_root_text = os.getenv("WORKFLOW_WORKSPACE_ROOT", "").strip()
    daemon_root_text = os.getenv("WORKFLOW_WORKSPACE_DAEMON_ROOT", "").strip()
    resolved = path.resolve()
    if not daemon_root_text:
        return resolved
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
        if locator.relative_path == "repo" and workspace_root.name == "repo"
        else (workspace_root / locator.relative_path).resolve()
    )
    if not workspace.is_relative_to(workspace_root):
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH, "managed relative path escapes its workspace"
        )
    return workspace
