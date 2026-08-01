"""Lore workspace preparation, binding, inspection, and checkpoint boundaries.

The adapter owns provider semantics while callers supply the pinned Lore client
implementation.  Workflow state carries only typed metadata and sandbox locators.

Implements the remaining workspace-boundary scope of Jira MM-1220.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, Mapping, Protocol, Sequence

from moonmind.schemas.workspace_locator_models import SandboxWorkspaceLocator

LORE_CHECKPOINT_TOO_LARGE = "LORE_CHECKPOINT_TOO_LARGE"
LORE_EXTERNAL_SCAN_FAILED = "LORE_EXTERNAL_SCAN_FAILED"
LORE_UNSUPPORTED_RUNTIME_LANE = "LORE_UNSUPPORTED_RUNTIME_LANE"
LORE_WORKSPACE_INVALID = "LORE_WORKSPACE_INVALID"

_PRIVATE_PARTS = frozenset({".lore", ".credentials", ".locks", ".journals"})


class LoreWorkspaceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class LoreClient(Protocol):
    """Machine-readable operations supplied by the pinned Lore tool bundle."""

    def materialize(
        self, *, repository: str, revision: str, destination: Path
    ) -> Mapping[str, object]: ...
    def scan_external_changes(self, *, workspace: Path) -> Mapping[str, object]: ...
    def status(self, *, workspace: Path) -> Mapping[str, object]: ...
    def stage_paths(self, *, workspace: Path, paths: Sequence[str]) -> None: ...


@dataclass(frozen=True)
class LorePreparedWorkspace:
    authority_locator: SandboxWorkspaceLocator
    authority_path: Path
    repository: str
    branch: str
    revision_signature: str
    metadata_path: Path


@dataclass(frozen=True)
class LoreWorkspaceBinding:
    authority_locator: SandboxWorkspaceLocator
    runtime_lane: Literal["managed_runtime", "omnigent"]
    runtime_visible_path: str
    mount_mode: Literal["direct_path", "bind_mount"]
    read_only: bool


@dataclass(frozen=True)
class LoreDeltaCheckpoint:
    base_revision: str
    changed_paths: tuple[str, ...]
    staged_paths: tuple[str, ...]
    files: Mapping[str, bytes | None]
    total_bytes: int
    digest: str
    lock_state: Literal["not_captured"] = "not_captured"


class LoreRepositoryProviderAdapter:
    """Prepare exactly one authoritative workspace and bind it to runtimes."""

    def __init__(
        self, client: LoreClient, *, checkpoint_limit_bytes: int = 64 * 1024 * 1024
    ) -> None:
        if checkpoint_limit_bytes <= 0:
            raise ValueError("checkpoint_limit_bytes must be positive")
        self._client = client
        self._checkpoint_limit = checkpoint_limit_bytes

    def prepare_workspace(
        self,
        *,
        repository: str,
        branch: str,
        revision_signature: str,
        locator: SandboxWorkspaceLocator,
        authority_path: Path,
        connection_ref: str,
        client_evidence: Mapping[str, str],
    ) -> LorePreparedWorkspace:
        root = self._validate_authority_path(authority_path, must_exist=False)
        if root.exists() and any(root.iterdir()):
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID, "workspace must be empty before preparation"
            )
        root.mkdir(parents=True, exist_ok=True)
        result = self._client.materialize(
            repository=repository, revision=revision_signature, destination=root
        )
        if (
            result.get("revisionSignature") != revision_signature
            or result.get("completeTree") is not True
        ):
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID,
                "client did not prove the exact complete revision",
            )
        self._validate_tree(root)
        metadata = {
            "schemaVersion": "moonmind.lore-workspace.v1",
            "repository": repository,
            "branch": branch,
            "revisionSignature": revision_signature,
            "connectionRef": connection_ref,
            "clientEvidence": dict(client_evidence),
        }
        metadata_path = root.parent / f".{root.name}.lore-workspace.json"
        metadata_path.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
        return LorePreparedWorkspace(
            locator, root, repository, branch, revision_signature, metadata_path
        )

    def bind_workspace(
        self,
        prepared: LorePreparedWorkspace,
        *,
        runtime_lane: str,
        omnigent_mount_path: str = "/workspaces/run",
        read_only: bool = False,
        omnigent_isolation_verified: bool = True,
    ) -> LoreWorkspaceBinding:
        self._validate_tree(prepared.authority_path)
        if runtime_lane == "managed_runtime":
            return LoreWorkspaceBinding(
                prepared.authority_locator,
                runtime_lane,
                str(prepared.authority_path),
                "direct_path",
                read_only,
            )
        if (
            runtime_lane == "omnigent"
            and omnigent_isolation_verified
            and PurePosixPath(omnigent_mount_path).is_absolute()
        ):
            return LoreWorkspaceBinding(
                prepared.authority_locator,
                runtime_lane,
                omnigent_mount_path,
                "bind_mount",
                read_only,
            )
        raise LoreWorkspaceError(
            LORE_UNSUPPORTED_RUNTIME_LANE,
            f"cannot bind authoritative Lore workspace to {runtime_lane!r}",
        )

    def inspect_workspace(
        self, prepared: LorePreparedWorkspace
    ) -> Mapping[str, object]:
        self._scan(prepared.authority_path)
        return self._client.status(workspace=prepared.authority_path)

    def capture_checkpoint(
        self, prepared: LorePreparedWorkspace
    ) -> LoreDeltaCheckpoint:
        self._scan(prepared.authority_path)
        status = self._client.status(workspace=prepared.authority_path)
        changed = self._paths(status.get("changedPaths", ()))
        staged = self._paths(status.get("stagedPaths", ()))
        if not set(staged).issubset(changed):
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID, "staged paths must be changed paths"
            )
        files: dict[str, bytes | None] = {}
        total = 0
        digest = hashlib.sha256()
        for relative in changed:
            path = self._checked_path(prepared.authority_path, relative)
            data = None if not path.exists() else path.read_bytes()
            total += len(data or b"")
            if total > self._checkpoint_limit:
                raise LoreWorkspaceError(
                    LORE_CHECKPOINT_TOO_LARGE,
                    f"dirty delta exceeds {self._checkpoint_limit} bytes",
                )
            files[relative] = data
            digest.update(relative.encode())
            digest.update(b"\0")
            digest.update(data or b"<deleted>")
        return LoreDeltaCheckpoint(
            prepared.revision_signature,
            changed,
            staged,
            files,
            total,
            f"sha256:{digest.hexdigest()}",
        )

    def restore_checkpoint(
        self,
        checkpoint: LoreDeltaCheckpoint,
        *,
        repository: str,
        branch: str,
        locator: SandboxWorkspaceLocator,
        authority_path: Path,
        connection_ref: str,
        client_evidence: Mapping[str, str],
    ) -> LorePreparedWorkspace:
        if checkpoint.total_bytes > self._checkpoint_limit:
            raise LoreWorkspaceError(
                LORE_CHECKPOINT_TOO_LARGE, "checkpoint exceeds restore policy"
            )
        prepared = self.prepare_workspace(
            repository=repository,
            branch=branch,
            revision_signature=checkpoint.base_revision,
            locator=locator,
            authority_path=authority_path,
            connection_ref=connection_ref,
            client_evidence=client_evidence,
        )
        for relative, data in checkpoint.files.items():
            path = self._checked_path(prepared.authority_path, relative)
            if data is None:
                if path.exists():
                    path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        self._validate_tree(prepared.authority_path)
        self._scan(prepared.authority_path)
        self._client.stage_paths(
            workspace=prepared.authority_path, paths=checkpoint.staged_paths
        )
        return prepared

    def _scan(self, root: Path) -> None:
        result = self._client.scan_external_changes(workspace=root)
        if result.get("success") is not True:
            raise LoreWorkspaceError(
                LORE_EXTERNAL_SCAN_FAILED, "external-change scan did not succeed"
            )

    @staticmethod
    def _paths(values: object) -> tuple[str, ...]:
        if not isinstance(values, (list, tuple)):
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID, "client paths must be a list"
            )
        result = tuple(dict.fromkeys(str(value).replace("\\", "/") for value in values))
        for value in result:
            LoreRepositoryProviderAdapter._validate_relative(value)
        return result

    @staticmethod
    def _validate_relative(value: str) -> None:
        path = PurePosixPath(value)
        if (
            not value
            or path.is_absolute()
            or any(
                part in {"", ".", ".."} or part in _PRIVATE_PARTS for part in path.parts
            )
        ):
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID, f"unsafe or private checkpoint path: {value!r}"
            )

    @classmethod
    def _checked_path(cls, root: Path, relative: str) -> Path:
        cls._validate_relative(relative)
        candidate = root.joinpath(*PurePosixPath(relative).parts)
        current = root
        for part in PurePosixPath(relative).parts:
            current = current / part
            if current.is_symlink():
                raise LoreWorkspaceError(
                    LORE_WORKSPACE_INVALID, f"symlink in checkpoint path: {relative}"
                )
        resolved_root, resolved = root.resolve(), candidate.resolve(strict=False)
        if not resolved.is_relative_to(resolved_root):
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID, "checkpoint path escapes workspace"
            )
        return candidate

    @staticmethod
    def _validate_authority_path(path: Path, *, must_exist: bool) -> Path:
        if not path.is_absolute():
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID, "authority path must be absolute"
            )
        root = path.resolve(strict=must_exist)
        if root == Path(root.anchor):
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID, "filesystem root cannot be workspace authority"
            )
        return root

    @classmethod
    def _validate_tree(cls, root: Path) -> None:
        root = cls._validate_authority_path(root, must_exist=True)
        for directory, names, files in os.walk(root, followlinks=False):
            base = Path(directory)
            for name in names + files:
                path = base / name
                if path.is_symlink():
                    target = path.resolve(strict=False)
                    if not target.is_relative_to(root):
                        raise LoreWorkspaceError(
                            LORE_WORKSPACE_INVALID,
                            f"workspace symlink escapes authority: {path.relative_to(root)}",
                        )
                elif path.exists() and not (path.is_file() or path.is_dir()):
                    mode = stat.S_IFMT(path.stat().st_mode)
                    raise LoreWorkspaceError(
                        LORE_WORKSPACE_INVALID,
                        f"unsupported workspace entry mode {mode}",
                    )
