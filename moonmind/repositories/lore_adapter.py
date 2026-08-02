"""Lore workspace preparation, binding, inspection, and checkpoint boundaries.

The adapter owns provider semantics while callers supply the pinned Lore client
implementation.  Workflow state carries only typed metadata and sandbox locators.

Implements the remaining workspace-boundary scope of Jira MM-1220.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import stat
import tempfile
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
        self,
        *,
        repository: str,
        revision: str,
        destination: Path,
        connection_ref: str,
        client_evidence: Mapping[str, str],
    ) -> Mapping[str, object]:
        ...

    def scan_external_changes(self, *, workspace: Path) -> Mapping[str, object]:
        ...

    def status(self, *, workspace: Path) -> Mapping[str, object]:
        ...

    def stage_paths(self, *, workspace: Path, paths: Sequence[str]) -> None:
        ...


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


class LoreImmutableObjectCache:
    """Trusted, content-addressed Lore object cache; never workspace state."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        if self._root == Path(self._root.anchor):
            raise ValueError("cache root cannot be the filesystem root")

    @staticmethod
    def _digest(content: bytes) -> str:
        return f"sha256:{hashlib.sha256(content).hexdigest()}"

    @staticmethod
    def _validate_identity(*values: str) -> None:
        for value in values:
            parts = PurePosixPath(str(value).replace("\\", "/")).parts
            if not value or any(part in _PRIVATE_PARTS for part in parts):
                raise LoreWorkspaceError(
                    LORE_WORKSPACE_INVALID, "private cache identity is forbidden"
                )

    def _path(
        self,
        *,
        endpoint: str,
        repository: str,
        client_compatibility: str,
        object_digest: str,
    ) -> Path:
        self._validate_identity(endpoint, repository, client_compatibility)
        if not object_digest.startswith("sha256:") or len(object_digest) != 71:
            raise LoreWorkspaceError(LORE_WORKSPACE_INVALID, "invalid cache digest")
        try:
            int(object_digest[7:], 16)
        except ValueError as exc:
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID, "invalid cache digest"
            ) from exc
        namespace = hashlib.sha256(
            "\0".join((endpoint, repository, client_compatibility)).encode()
        ).hexdigest()
        return (
            self._root / "objects" / namespace / object_digest[7:9] / object_digest[7:]
        )

    def publish(
        self,
        *,
        endpoint: str,
        repository: str,
        client_compatibility: str,
        object_digest: str,
        content: bytes,
    ) -> Path:
        """Atomically publish a verified immutable object from a trusted boundary."""

        if self._digest(content) != object_digest:
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID, "cache object digest mismatch"
            )
        target = self._path(
            endpoint=endpoint,
            repository=repository,
            client_compatibility=client_compatibility,
            object_digest=object_digest,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self.read(
                endpoint=endpoint,
                repository=repository,
                client_compatibility=client_compatibility,
                object_digest=object_digest,
            )
            return target
        fd, temporary_name = tempfile.mkstemp(prefix=".publish-", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o444)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def read(
        self,
        *,
        endpoint: str,
        repository: str,
        client_compatibility: str,
        object_digest: str,
    ) -> bytes:
        target = self._path(
            endpoint=endpoint,
            repository=repository,
            client_compatibility=client_compatibility,
            object_digest=object_digest,
        )
        try:
            content = target.read_bytes()
        except FileNotFoundError as exc:
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID, "cache object is unavailable"
            ) from exc
        if self._digest(content) != object_digest:
            target.unlink(missing_ok=True)
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID,
                "cache object failed digest verification and was evicted",
            )
        return content


class LoreRepositoryProviderAdapter:
    """Prepare exactly one authoritative workspace and bind it to runtimes."""

    def __init__(
        self,
        client: LoreClient,
        *,
        checkpoint_limit_bytes: int = 64 * 1024 * 1024,
        immutable_cache: LoreImmutableObjectCache | None = None,
    ) -> None:
        if checkpoint_limit_bytes <= 0:
            raise ValueError("checkpoint_limit_bytes must be positive")
        self._client = client
        self._checkpoint_limit = checkpoint_limit_bytes
        self._immutable_cache = immutable_cache

    def publish_cache_object(self, **kwargs: object) -> Path:
        if self._immutable_cache is None:
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID, "immutable Lore cache is not configured"
            )
        return self._immutable_cache.publish(**kwargs)  # type: ignore[arg-type]

    def read_cache_object(self, **kwargs: str) -> bytes:
        if self._immutable_cache is None:
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID, "immutable Lore cache is not configured"
            )
        return self._immutable_cache.read(**kwargs)

    def encode_checkpoint(self, checkpoint: LoreDeltaCheckpoint) -> bytes:
        """Encode the provider checkpoint for the durable artifact boundary."""

        self._validate_checkpoint(checkpoint)
        payload = {
            "schemaVersion": "moonmind.repository-checkpoint.v1",
            "provider": "lore",
            "checkpointKind": "repository_delta",
            "baseRevision": checkpoint.base_revision,
            "changedPaths": list(checkpoint.changed_paths),
            "stagedPaths": list(checkpoint.staged_paths),
            "files": {
                path: None if data is None else base64.b64encode(data).decode("ascii")
                for path, data in checkpoint.files.items()
            },
            "totalBytes": checkpoint.total_bytes,
            "digest": checkpoint.digest,
            "lockState": checkpoint.lock_state,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    def decode_checkpoint(self, payload: bytes) -> LoreDeltaCheckpoint:
        """Decode and validate an untrusted durable checkpoint artifact."""

        try:
            data = json.loads(payload)
            if (
                data.get("schemaVersion") != "moonmind.repository-checkpoint.v1"
                or data.get("provider") != "lore"
                or data.get("checkpointKind") != "repository_delta"
                or data.get("lockState") != "not_captured"
                or not isinstance(data.get("files"), dict)
            ):
                raise ValueError("invalid contract")
            files = {
                str(path): (
                    None if value is None else base64.b64decode(value, validate=True)
                )
                for path, value in data["files"].items()
            }
            checkpoint = LoreDeltaCheckpoint(
                base_revision=str(data["baseRevision"]),
                changed_paths=tuple(data["changedPaths"]),
                staged_paths=tuple(data["stagedPaths"]),
                files=files,
                total_bytes=int(data["totalBytes"]),
                digest=str(data["digest"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID, "invalid durable Lore checkpoint"
            ) from exc
        self._validate_checkpoint(checkpoint)
        return checkpoint

    def load_prepared_workspace(
        self,
        *,
        locator: SandboxWorkspaceLocator,
        authority_path: Path,
    ) -> LorePreparedWorkspace:
        """Rebind an already-prepared authority without a second materialization."""

        root = self._validate_authority_path(authority_path, must_exist=True)
        self._validate_tree(root)
        metadata_path = root.parent / f".{root.name}.lore-workspace.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID, "Lore workspace metadata is unavailable"
            ) from exc
        if metadata.get("schemaVersion") != "moonmind.lore-workspace.v1":
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID, "Lore workspace metadata is invalid"
            )
        required = ("repository", "branch", "revisionSignature")
        if any(
            not isinstance(metadata.get(key), str) or not metadata[key]
            for key in required
        ):
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID, "Lore workspace metadata is incomplete"
            )
        return LorePreparedWorkspace(
            locator,
            root,
            metadata["repository"],
            metadata["branch"],
            metadata["revisionSignature"],
            metadata_path,
        )

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
        root.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{root.name}.materializing-", dir=root.parent)
        )
        try:
            result = self._client.materialize(
                repository=repository,
                revision=revision_signature,
                destination=temporary,
                connection_ref=connection_ref,
                client_evidence=client_evidence,
            )
            if (
                result.get("revisionSignature") != revision_signature
                or result.get("completeTree") is not True
            ):
                raise LoreWorkspaceError(
                    LORE_WORKSPACE_INVALID,
                    "client did not prove the exact complete revision",
                )
            self._validate_tree(temporary)
            if root.exists():
                root.rmdir()
            os.replace(temporary, root)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
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
        self._validate_checkpoint(checkpoint)
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

    def _validate_checkpoint(self, checkpoint: LoreDeltaCheckpoint) -> None:
        changed = self._paths(checkpoint.changed_paths)
        staged = self._paths(checkpoint.staged_paths)
        file_paths = self._paths(tuple(checkpoint.files))
        if set(file_paths) != set(changed) or not set(staged).issubset(changed):
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID,
                "checkpoint paths, files, and staged paths are inconsistent",
            )
        actual_total, actual_digest = self._checkpoint_integrity(
            changed, checkpoint.files
        )
        if actual_total > self._checkpoint_limit:
            raise LoreWorkspaceError(
                LORE_CHECKPOINT_TOO_LARGE, "checkpoint exceeds restore policy"
            )
        if checkpoint.total_bytes != actual_total or checkpoint.digest != actual_digest:
            raise LoreWorkspaceError(
                LORE_WORKSPACE_INVALID, "checkpoint size or digest is invalid"
            )

    @staticmethod
    def _checkpoint_integrity(
        changed: Sequence[str], files: Mapping[str, bytes | None]
    ) -> tuple[int, str]:
        total = 0
        digest = hashlib.sha256()
        for relative in changed:
            data = files[relative]
            if data is not None and not isinstance(data, bytes):
                raise LoreWorkspaceError(
                    LORE_WORKSPACE_INVALID, "checkpoint file payload must be bytes"
                )
            total += len(data or b"")
            digest.update(relative.encode())
            digest.update(b"\0")
            digest.update(data or b"<deleted>")
        return total, f"sha256:{digest.hexdigest()}"

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
