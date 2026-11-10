"""Agent-runtime-owned, restart-safe cold restore data plane."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from moonmind.schemas.checkpoint_restore_models import (
    RESTORATION_EVIDENCE_CONTENT_TYPE,
    CheckpointRestoreError,
    ManagedWorkspaceRestoreRequest,
    ManagedWorkspaceRestoreResult,
    SandboxRestoreDestination,
)
from moonmind.schemas.workspace_locator_models import (
    ManagedWorkspaceLocator,
    SandboxWorkspaceLocator,
)
from moonmind.workflows.temporal.artifacts import ArtifactAccessContext

from .git_auth import build_github_token_git_environment
from .managed_api_key_resolve import AdmittedGitRead, resolve_admitted_git_read

# Checkpoint archive/manifest artifacts are written by the capture path under the
# ``system`` owner. The artifact service only grants cross-owner reads to
# callers carrying an admitted scope, so each restore read carries the capture
# owner explicitly instead of relying on a bare service-principal grant.
_CHECKPOINT_RESTORE_PRINCIPAL = "service:checkpoint_restore"
_CHECKPOINT_CAPTURE_PRINCIPAL = "system"


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


class ManagedCheckpointRestoreService:
    def __init__(
        self,
        *,
        authority_root: str | Path,
        artifact_service: Any = None,
        artifact_store: Any = None,
        repository_source_root: str | Path | None = None,
        run_store: Any = None,
    ) -> None:
        self.root = Path(authority_root).resolve()
        self.artifact_service = artifact_service
        self.artifact_store = artifact_store
        self.repository_source_root = (
            Path(repository_source_root).resolve() if repository_source_root else None
        )
        self.run_store = run_store
        self._locks: dict[str, asyncio.Lock] = {}

    def _destination(self, req):
        if isinstance(req.destination, SandboxRestoreDestination):
            from .workspace_locators import (
                SandboxWorkspaceRecordStore,
                resolve_sandbox_workspace_locator,
            )

            destination = req.destination
            expected = hashlib.sha256(f"{req.recovery_identity.workflow_id}:{destination.step_execution_id}".encode()).hexdigest()[:24]
            locator = SandboxWorkspaceLocator(workspaceId=destination.workspace_id, relativePath=destination.relative_path)
            owner = SandboxWorkspaceRecordStore(self.root).load(expected)
            if owner is None:
                raise CheckpointRestoreError("CHECKPOINT_DESTINATION_IDENTITY_MISMATCH", "sandbox destination has no durable owner")
            return resolve_sandbox_workspace_locator(
                locator, workspace_root=self.root, expected_workspace_id=expected, owner_record=owner,
                expected_workflow_id=req.recovery_identity.workflow_id,
                expected_step_execution_id=destination.step_execution_id, must_exist=False,
            ), locator
        locator = ManagedWorkspaceLocator(runtimeId="codex_cli", agentRunId=req.destination.agent_run_id, relativePath="repo")
        workspace = self.root / req.destination.agent_run_id / "repo"
        if not workspace.resolve().is_relative_to(self.root):
            raise CheckpointRestoreError("CHECKPOINT_DESTINATION_IDENTITY_MISMATCH", "destination escaped managed authority")
        return workspace, locator

    async def _read(
        self,
        ref: str,
        *,
        content_types: set[str],
        access: ArtifactAccessContext,
    ) -> bytes:
        try:
            if self.artifact_service is not None:
                artifact, payload = await self.artifact_service.read(
                    artifact_id=ref.removeprefix("artifact:").removeprefix("//"),
                    access=access,
                )
                content_type = str(getattr(artifact, "content_type", ""))
                from moonmind.workflows.temporal.saved_work_dependencies import (
                    verify_saved_work_dependencies,
                )

                await verify_saved_work_dependencies(
                    self.artifact_service,
                    artifact=artifact,
                    payload=payload,
                    access=access,
                )
            else:
                payload = self.artifact_store.get_bytes(ref)
                artifact = getattr(self.artifact_store, "_meta", {}).get(ref)
                content_type = str(getattr(artifact, "content_type", ""))
            if content_type not in content_types:
                raise CheckpointRestoreError(
                    "CHECKPOINT_ARTIFACT_CONTENT_TYPE_MISMATCH",
                    "artifact has an incompatible content type",
                )
            return payload
        except CheckpointRestoreError:
            raise
        except Exception as exc:
            name = type(exc).__name__.lower()
            code = (
                "CHECKPOINT_ARTIFACT_UNAUTHORIZED"
                if "author" in name or "validation" in name
                else "CHECKPOINT_ARTIFACT_MISSING"
            )
            raise CheckpointRestoreError(code, f"cannot read artifact {ref}") from exc

    async def _write(self, payload: bytes) -> str:
        if self.artifact_service is not None:
            artifact, _ = await self.artifact_service.create(
                principal=_CHECKPOINT_RESTORE_PRINCIPAL,
                content_type=RESTORATION_EVIDENCE_CONTENT_TYPE,
                metadata_json={"artifact_kind": "managed_workspace_restoration"},
            )
            completed = await self.artifact_service.write_complete(
                artifact_id=artifact.artifact_id,
                principal=_CHECKPOINT_RESTORE_PRINCIPAL,
                payload=payload,
                content_type=RESTORATION_EVIDENCE_CONTENT_TYPE,
            )
            return completed.artifact_id
        return self.artifact_store.put_bytes(
            payload,
            content_type=RESTORATION_EVIDENCE_CONTENT_TYPE,
            metadata={"artifact_kind": "managed_workspace_restoration"},
        ).artifact_ref

    @staticmethod
    def _safe_name(name: str) -> PurePosixPath:
        path = PurePosixPath(name)
        if (
            not name
            or path.is_absolute()
            or any(p in {"", ".", ".."} for p in path.parts)
        ):
            raise CheckpointRestoreError(
                "CHECKPOINT_PATH_ESCAPE", "archive path is not normalized and relative"
            )
        if ".git" in path.parts:
            raise CheckpointRestoreError(
                "CHECKPOINT_PATH_ESCAPE", ".git content cannot be restored"
            )
        return path

    @staticmethod
    def _expected_mode(item: Mapping[str, Any]) -> int:
        """Parse a manifest entry's octal file mode, failing closed if malformed."""
        try:
            return int(str(item.get("mode", "0644")), 8) & 0o777
        except (TypeError, ValueError) as exc:
            raise CheckpointRestoreError(
                "CHECKPOINT_MANIFEST_CORRUPTED", "invalid file mode in manifest"
            ) from exc

    @staticmethod
    def _clear_existing(target: Path) -> None:
        """Remove any existing filesystem entry at ``target`` without following it."""
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)

    def _extract(
        self, archive: bytes, staging: Path, manifest: Mapping[str, Any], *,
        max_entries: int = 100_000, max_bytes: int = 2 * 1024 * 1024 * 1024,
    ) -> tuple[int, int]:
        expected = {entry["path"]: entry for entry in manifest.get("entries", [])}
        seen: set[str] = set()
        total = 0
        with tarfile.open(fileobj=BytesIO(archive), mode="r:*") as tar:
            for member in tar:
                if len(seen) >= max_entries:
                    raise CheckpointRestoreError(
                        "CHECKPOINT_RESTORE_LIMIT_EXCEEDED", "archive entry limit exceeded"
                    )
                path = self._safe_name(member.name)
                name = str(path)
                if name in seen or name not in expected:
                    raise CheckpointRestoreError(
                        "CHECKPOINT_ENTRY_DIGEST_MISMATCH",
                        "archive has duplicate or extra entry",
                    )
                seen.add(name)
                target = staging.joinpath(*path.parts)
                for parent in target.parents:
                    if parent == staging:
                        break
                    if parent.is_symlink():
                        raise CheckpointRestoreError(
                            "CHECKPOINT_SYMLINK_ESCAPE",
                            "archive traverses a symlink parent",
                        )
                target.parent.mkdir(parents=True, exist_ok=True)
                item = expected[name]
                if member.isreg():
                    if item.get("type") != "file":
                        raise CheckpointRestoreError(
                            "CHECKPOINT_ENTRY_DIGEST_MISMATCH", "entry type mismatch"
                        )
                    if member.size < 0 or member.size > max_bytes - total:
                        raise CheckpointRestoreError(
                            "CHECKPOINT_RESTORE_LIMIT_EXCEEDED", "archive byte limit exceeded"
                        )
                    stream = tar.extractfile(member)
                    payload = stream.read(member.size + 1)  # type: ignore[union-attr]
                    if len(payload) != member.size:
                        raise CheckpointRestoreError(
                            "CHECKPOINT_ENTRY_DIGEST_MISMATCH", "archive member size mismatch"
                        )
                    total += len(payload)
                    if _digest(payload) != item.get("digest"):
                        raise CheckpointRestoreError(
                            "CHECKPOINT_ENTRY_DIGEST_MISMATCH", "file digest mismatch"
                        )
                    expected_mode = self._expected_mode(item)
                    if member.mode & 0o777 != expected_mode:
                        raise CheckpointRestoreError(
                            "CHECKPOINT_ENTRY_DIGEST_MISMATCH", "archive mode mismatch"
                        )
                    # Drop any entry the base checkout left here first. Writing over
                    # a checked-out symlink would follow it and could materialize
                    # (and chmod) a file outside the staging tree.
                    self._clear_existing(target)
                    target.write_bytes(payload)
                    os.chmod(target, expected_mode)
                elif member.issym():
                    if item.get("type") != "symlink" or member.linkname != item.get(
                        "target"
                    ):
                        raise CheckpointRestoreError(
                            "CHECKPOINT_ENTRY_DIGEST_MISMATCH", "symlink mismatch"
                        )
                    resolved = (target.parent / member.linkname).resolve(strict=False)
                    if not resolved.is_relative_to(staging):
                        raise CheckpointRestoreError(
                            "CHECKPOINT_SYMLINK_ESCAPE",
                            "symlink target escapes workspace",
                        )
                    self._clear_existing(target)
                    target.symlink_to(member.linkname)
                else:
                    raise CheckpointRestoreError(
                        "CHECKPOINT_SPECIAL_FILE_UNSUPPORTED",
                        "archive contains unsupported entry type",
                    )
        if seen != set(expected):
            raise CheckpointRestoreError(
                "CHECKPOINT_ENTRY_DIGEST_MISMATCH",
                "manifest entry missing from archive",
            )
        return len(seen), total

    @staticmethod
    def _normalize_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
        """Project both durable capture formats identically on restore and retry."""
        normalized = dict(manifest)
        if manifest.get("contentType") == (
            "application/vnd.moonmind.managed-workspace-checkpoint-manifest+json;version=1"
        ):
            normalized["entries"] = [
                {
                    **entry,
                    "digest": (
                        "sha256:" + str(entry.get("sha256"))
                        if entry.get("sha256")
                        and not str(entry.get("sha256")).startswith("sha256:")
                        else entry.get("sha256")
                    ),
                    "target": entry.get("linkTarget"),
                }
                for entry in manifest.get("entries", [])
                if isinstance(entry, Mapping)
            ]
        return normalized

    def _materialized_paths(
        self, staging: Path, manifest: Mapping[str, Any], *, selected_only=False
    ) -> set[str]:
        selected = None
        if selected_only:
            from .checkpoint_submodules import checkpoint_worktrees

            selected, _repositories, _exclusions = checkpoint_worktrees(
                staging,
                include_untracked=True,
                include_ignored=False,
            )
        # A symlink is a leaf entry even when it points at a directory, so exclude
        # only real directories; ``is_dir()`` alone would follow directory symlinks
        # and drop them from the set, spuriously failing valid restores.
        return {
            str(path.relative_to(staging))
            for path in staging.rglob("*")
            if ".git" not in path.relative_to(staging).parts
            and (selected is None or path.relative_to(staging).as_posix() in selected)
            and not self._excluded_from_capture(
                path.relative_to(staging).as_posix(), manifest
            )
            and not (path.is_dir() and not path.is_symlink())
        }

    def _verify_materialized(
        self, staging: Path, manifest: Mapping[str, Any], *, selected_only=False
    ) -> None:
        expected = {entry["path"]: entry for entry in manifest.get("entries", [])}
        if self._materialized_paths(
            staging, manifest, selected_only=selected_only
        ) != set(expected):
            raise CheckpointRestoreError(
                "CHECKPOINT_ENTRY_DIGEST_MISMATCH", "materialized path set mismatch"
            )
        self._verify_manifest_entries(staging, expected)

    def _verify_manifest_entries(
        self, staging: Path, expected: Mapping[str, Any]
    ) -> None:
        for name, item in expected.items():
            path = staging.joinpath(*PurePosixPath(name).parts)
            if item.get("type") == "symlink":
                if not path.is_symlink() or os.readlink(path) != item.get("target"):
                    raise CheckpointRestoreError("CHECKPOINT_ENTRY_DIGEST_MISMATCH", "symlink mismatch")
            elif path.is_symlink() or not path.is_file():
                raise CheckpointRestoreError("CHECKPOINT_ENTRY_DIGEST_MISMATCH", "file type mismatch")
            else:
                if _digest(path.read_bytes()) != item.get("digest"):
                    raise CheckpointRestoreError("CHECKPOINT_ENTRY_DIGEST_MISMATCH", "file digest mismatch")
                if path.stat().st_mode & 0o777 != self._expected_mode(item):
                    raise CheckpointRestoreError("CHECKPOINT_ENTRY_DIGEST_MISMATCH", "file mode mismatch")

    @staticmethod
    def _input_projection_entries(req, projection) -> dict[str, Mapping[str, Any]]:
        """Accept only the sandbox owner's exact, durable input description."""

        def invalid(message):
            return CheckpointRestoreError(
                "CHECKPOINT_DESTINATION_IDENTITY_MISMATCH", message
            )

        if (
            not isinstance(req.destination, SandboxRestoreDestination)
            or req.workspace_policy != "restore_step_input"
            or not isinstance(projection, Mapping)
            or projection.get("version") != "input-projection-v1"
            or not isinstance(projection.get("entries"), list)
        ):
            raise invalid("checkpoint input projection is invalid")
        entries = {}
        for item in projection["entries"]:
            if not isinstance(item, Mapping):
                raise invalid("checkpoint input projection entry is invalid")
            name, ref = item.get("path"), item.get("ref")
            if not isinstance(name, str) or not isinstance(ref, str) or not ref.strip():
                raise invalid("checkpoint input projection identity is invalid")
            suffix = hashlib.sha256(ref.strip().encode()).hexdigest()[:24]
            if name not in {
                f".moonmind/{kind}/{suffix}" for kind in ("restore", "attachments")
            }:
                raise invalid("checkpoint input projection path is not deterministic")
            if (
                name in entries
                or not isinstance(item.get("digest"), str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", item["digest"])
                or any(
                    not isinstance(item.get(key), int)
                    or isinstance(item[key], bool)
                    or item[key] < 0
                    for key in ("bytes", "mode", "uid", "gid")
                )
                or item["mode"] != 0o400
            ):
                raise invalid("checkpoint input projection metadata is invalid")
            entries[name] = item
        return entries

    def _validate_input_projection_paths(
        self, workspace, manifest, entries
    ) -> frozenset[str]:
        # HEAD includes paths deleted from the index; ls-files includes newly
        # staged paths. Neither can be replaced by a current worktree scan.
        saved_entries = {entry["path"]: entry for entry in manifest.get("entries", [])}
        baseline = set(saved_entries)
        reused = set()
        for args in (
            ["ls-files", "--cached", "-z"],
            ["ls-tree", "-r", "--name-only", "-z", "HEAD"],
        ):
            baseline.update(
                os.fsdecode(name)
                for name in self._git_bytes(args, workspace).split(b"\0")
                if name
            )
        for name, item in entries.items():
            saved_entry = saved_entries.get(name)
            saved_mode = None
            if saved_entry is not None:
                self._expected_mode(
                    saved_entry
                )  # Keep canonical malformed-mode errors.
                saved_mode = int(str(saved_entry.get("mode", "0644")), 8)
            if saved_entry is not None and (
                saved_entry.get("type") == "file"
                and saved_entry.get("digest") == item["digest"]
                and saved_mode == item["mode"]
                and all(
                    key not in saved_entry
                    or (
                        isinstance(saved_entry[key], int)
                        and not isinstance(saved_entry[key], bool)
                        and saved_entry[key] == item["bytes"]
                    )
                    for key in ("bytes", "size")
                )
            ):
                reused.add(name)
            if any(
                (name == saved and name not in reused)
                or name.startswith(saved + "/")
                or saved.startswith(name + "/")
                for saved in baseline
            ):
                raise CheckpointRestoreError(
                    "CHECKPOINT_ENTRY_DIGEST_MISMATCH",
                    "input projection collides with saved work",
                )
            target = workspace / name
            parent = workspace
            for component in PurePosixPath(name).parts[:-1]:
                parent = parent / component
                try:
                    mode = parent.lstat().st_mode
                except FileNotFoundError:
                    break
                if not stat.S_ISDIR(mode):
                    raise CheckpointRestoreError(
                        "CHECKPOINT_ENTRY_DIGEST_MISMATCH",
                        "input projection parent is not a directory",
                    )
            try:
                mode = target.lstat().st_mode
            except FileNotFoundError:
                if name not in reused:
                    continue
                raise CheckpointRestoreError(
                    "CHECKPOINT_ENTRY_DIGEST_MISMATCH",
                    "saved input projection is missing",
                ) from None
            if not stat.S_ISREG(mode):
                raise CheckpointRestoreError(
                    "CHECKPOINT_ENTRY_DIGEST_MISMATCH",
                    "input projection target is not a regular file",
                )
            if name in reused:
                # Matching descriptor metadata alone is not proof of reuse.
                # Pin and verify the actual saved leaf before any input write.
                self._verify_projection_file(target, item, baseline_owned=True)
        return frozenset(reused)

    async def validate_input_projection(
        self, raw, *, access, admitted_projection
    ) -> frozenset[str]:
        """Check collisions and identify saved leaves before input writes."""
        req = (
            raw
            if isinstance(raw, ManagedWorkspaceRestoreRequest)
            else ManagedWorkspaceRestoreRequest.model_validate(raw)
        )
        entries = self._input_projection_entries(req, admitted_projection)
        workspace, _locator = self._destination(req)
        manifest = await self._read_retained_manifest(req, access=access)
        return self._validate_input_projection_paths(workspace, manifest, entries)

    async def _read_retained_manifest(self, req, *, access):
        payload = await self._read(
            req.checkpoint.manifest_ref,
            content_types={
                "application/json",
                "application/vnd.moonmind.managed-workspace-checkpoint-manifest+json;version=1",
            },
            access=access,
        )
        if _digest(payload) != req.checkpoint.manifest_digest:
            raise CheckpointRestoreError(
                "CHECKPOINT_MANIFEST_CORRUPTED", "saved manifest bytes changed"
            )
        return self._normalize_manifest(json.loads(payload))

    @staticmethod
    def _verify_projection_file(path, item, *, baseline_owned=False) -> bool:
        from moonmind.utils.workspace_paths import open_regular_file

        try:
            # Pin every parent and the leaf. A rename after the collision
            # check must not redirect this read outside the workspace.
            with open_regular_file(path) as stream:
                metadata = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or stat.S_IMODE(metadata.st_mode) != item["mode"]
                    or (
                        not baseline_owned
                        and (
                            metadata.st_uid != item["uid"]
                            or metadata.st_gid != item["gid"]
                        )
                    )
                    or metadata.st_size != item["bytes"]
                    or _digest(stream.read(item["bytes"] + 1)) != item["digest"]
                ):
                    raise CheckpointRestoreError(
                        "CHECKPOINT_ENTRY_DIGEST_MISMATCH",
                        "projected input content or metadata changed",
                    )
        except FileNotFoundError:
            if baseline_owned:
                raise CheckpointRestoreError(
                    "CHECKPOINT_ENTRY_DIGEST_MISMATCH",
                    "saved input projection is missing",
                ) from None
            return False  # Interrupted projection may have promoted a subset.
        except OSError as exc:
            raise CheckpointRestoreError(
                "CHECKPOINT_ENTRY_DIGEST_MISMATCH", "cannot verify projected input"
            ) from exc
        return True

    def _verify_projected_candidate(
        self, workspace, manifest, entries
    ) -> frozenset[str]:
        reused = self._validate_input_projection_paths(workspace, manifest, entries)
        present = {
            name
            for name, item in entries.items()
            if name not in reused
            and self._verify_projection_file(workspace / name, item)
        }
        expected = {entry["path"]: entry for entry in manifest.get("entries", [])}
        if self._materialized_paths(workspace, manifest) != set(expected) | present:
            raise CheckpointRestoreError(
                "CHECKPOINT_ENTRY_DIGEST_MISMATCH", "materialized path set mismatch"
            )
        # Reused leaves already matched saved manifest metadata and pinned bytes.
        # They remain baseline-owned: unlike additions, their raw Git status and
        # index entries are never removed from checkpoint identity checks.
        self._verify_manifest_entries(
            workspace,
            {name: item for name, item in expected.items() if name not in reused},
        )
        return frozenset(present)

    @staticmethod
    def _checkpoint_status(
        status: bytes, verified_projection_paths: frozenset[str]
    ) -> bytes:
        if not verified_projection_paths:
            return status
        removable = {b"?? " + os.fsencode(name) for name in verified_projection_paths}
        records = status.split(b"\0")
        kept = []
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if record not in removable:
                kept.append(record)
            # Porcelain -z rename/copy records have a second path, not a status
            # record, even if its filename itself begins with "?? ".
            if b"R" in record[:2] or b"C" in record[:2]:
                if index < len(records):
                    kept.append(records[index])
                    index += 1
        return b"\0".join(kept)

    def _saved_sandbox_branch(self, manifest: Mapping[str, Any]) -> str | None:
        # Managed manifests historically recorded branch as provenance while
        # restoring detached. Only the sandbox format newly captures restore
        # intent here; old absent fields retain their original interpretation.
        if manifest.get("kind") != "worktree_archive" or manifest.get("contentType") == (
            "application/vnd.moonmind.managed-workspace-checkpoint-manifest+json;version=1"
        ):
            return None
        git = manifest.get("git", {})
        if "branch" not in git:
            return None
        branch = git["branch"]
        if not isinstance(branch, str):
            raise CheckpointRestoreError("CHECKPOINT_MANIFEST_CORRUPTED", "saved Git branch is invalid")
        if branch and self._git(
            ["check-ref-format", "--branch", branch],
            code="CHECKPOINT_MANIFEST_CORRUPTED",
        ) != branch:
            raise CheckpointRestoreError("CHECKPOINT_MANIFEST_CORRUPTED", "saved Git branch is invalid")
        return branch

    def _verify_git_candidate(
        self,
        workspace,
        manifest,
        base,
        *,
        admitted_target_branch=None,
        verified_projection_paths=frozenset(),
    ):
        from .checkpoint_history import capture_git_index_patch
        if self._git(["rev-parse", "HEAD"], workspace) != base:
            raise CheckpointRestoreError("CHECKPOINT_BASE_COMMIT_MISMATCH", "saved destination HEAD changed")
        branch = self._saved_sandbox_branch(manifest)
        if branch is not None and self._git(["branch", "--show-current"], workspace) not in {
            branch, admitted_target_branch,
        }:
            raise CheckpointRestoreError("CHECKPOINT_ENTRY_DIGEST_MISMATCH", "saved destination Git branch changed")
        index = manifest.get("git", {}).get("indexPatch")
        # This compares already-authorized immutable content; it does not grant
        # export authority or re-scan a checkpoint under a different policy.
        actual_patch, actual_paths = capture_git_index_patch(workspace, base, high_security_mode=False)
        if index is not None:
            if _digest(actual_patch) != index.get("digest") or actual_paths != index.get("paths"):
                raise CheckpointRestoreError("CHECKPOINT_ENTRY_DIGEST_MISMATCH", "saved destination Git index changed")
        elif manifest.get("git", {}).get("stagedPaths") or actual_patch:
            # Historical manifests only named staged paths. They cannot prove
            # the original bytes of a partially staged file, so never synthesize
            # its index from worktree content during recovery.
            raise CheckpointRestoreError("CHECKPOINT_INDEX_EVIDENCE_UNAVAILABLE", "historical checkpoint lacks immutable index content")
        status = self._git_bytes(["status", "--porcelain=v1", "-z", "--untracked-files=all"], workspace)
        status = self._checkpoint_status(status, verified_projection_paths)
        expected = manifest.get("gitStatusDigest") or manifest.get("git", {}).get("statusDigest")
        if expected and _digest(status) != expected:
            raise CheckpointRestoreError("CHECKPOINT_ENTRY_DIGEST_MISMATCH", "Git status digest mismatch")
        for module in manifest.get("git", {}).get("submodules", []):
            name = self._safe_name(module["path"])
            target = workspace.joinpath(*name.parts)
            if target.resolve() != target or not (target / ".git").exists():
                raise CheckpointRestoreError(
                    "CHECKPOINT_ENTRY_DIGEST_MISMATCH",
                    "saved submodule is missing or replaced",
                )
            self._verify_git_candidate(target, module, module["baseCommit"])
        return _digest(status)

    async def _restore_submodules(self, workspace, manifest, *, access):
        parents = {"": workspace}
        modules = manifest.get("git", {}).get("submodules", [])
        for module in sorted(
            modules,
            key=lambda item: (len(PurePosixPath(item["path"]).parts), item["path"]),
        ):
            name = self._safe_name(module["path"])
            target = workspace.joinpath(*name.parts)
            if target.resolve() != target:
                raise CheckpointRestoreError(
                    "CHECKPOINT_PATH_ESCAPE", "submodule directory is replaced"
                )
            parent_name = max(
                (p for p in parents if not p or str(name).startswith(p + "/")), key=len
            )
            relative = (
                str(name).removeprefix(parent_name + "/") if parent_name else str(name)
            )
            if not self._git(
                ["ls-files", "--stage", "--", relative], parents[parent_name]
            ).startswith("160000 "):
                raise CheckpointRestoreError(
                    "CHECKPOINT_MANIFEST_CORRUPTED",
                    "submodule is not a selected gitlink",
                )
            history = module["gitHistory"]
            if (
                history.get("contentType") != "application/x-git-pack"
                or history.get("headCommit") != module["baseCommit"]
            ):
                raise CheckpointRestoreError(
                    "CHECKPOINT_MANIFEST_CORRUPTED",
                    "submodule history identity mismatch",
                )
            payload = await self._read(
                history["ref"],
                content_types={"application/x-git-pack"},
                access=access,
            )
            if _digest(payload) != history.get("digest"):
                raise CheckpointRestoreError(
                    "CHECKPOINT_ARCHIVE_CORRUPTED", "submodule history digest mismatch"
                )
            self._git(["init", str(target)])
            shallow = history.get("shallowCommits", [])
            if any(
                not isinstance(sha, str)
                or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha)
                for sha in shallow
            ):
                raise CheckpointRestoreError(
                    "CHECKPOINT_MANIFEST_CORRUPTED",
                    "invalid submodule history boundary",
                )
            if shallow:
                (target / ".git" / "shallow").write_text("\n".join(shallow) + "\n")
            self._git_bytes(
                ["index-pack", "--stdin", "--strict"], target, input_bytes=payload
            )
            self._git(["checkout", "--detach", module["baseCommit"]], target)
            await self._restore_index(target, module, access=access)
            parents[str(name)] = target

    async def _restore_index(self, workspace, manifest, *, access):
        index = manifest.get("git", {}).get("indexPatch")
        if index is None:
            return  # Historical clean-index checkpoints remain restorable.
        for name in index.get("paths", []):
            self._safe_name(name)
        payload = (
            await self._read(
                index["ref"],
                content_types={"application/vnd.moonmind.git-index-patch"},
                access=access,
            )
            if index.get("ref")
            else b""
        )
        if _digest(payload) != index.get("digest"):
            raise CheckpointRestoreError("CHECKPOINT_ARCHIVE_CORRUPTED", "Git index patch digest mismatch")
        if payload:
            patch = workspace / ".git" / "checkpoint-index.patch"
            patch.write_bytes(payload)
            try:
                self._git(["apply", "--cached", "--binary", str(patch)], workspace)
            finally:
                patch.unlink(missing_ok=True)

    @staticmethod
    def _excluded_from_capture(name, manifest):
        excluded = manifest.get("excludedPaths", [])
        return any(name == path or name.startswith(path + "/") for path in excluded)

    def _replay_deletions(self, staging: Path, manifest: Mapping[str, Any]) -> None:
        """Drop worktree entries the checkpoint deleted relative to ``baseCommit``.

        The base commit is checked out before the archive is overlaid, so a file
        that existed at base but was removed in the checkpointed worktree stays on
        disk and is absent from the manifest. Left in place it would fail
        ``_verify_materialized`` as an extra path, so remove any non-``.git`` entry
        that the manifest does not list. A symlink (even one pointing at a
        directory) is treated as a leaf, matching ``_verify_materialized``.
        """
        expected = {entry["path"] for entry in manifest.get("entries", [])}
        for path in staging.rglob("*"):
            relative = path.relative_to(staging)
            if ".git" in relative.parts:
                continue
            if path.is_dir() and not path.is_symlink():
                continue
            if str(relative) not in expected and not self._excluded_from_capture(relative.as_posix(), manifest):
                self._clear_existing(path)

    async def _admitted_git_read(self, req) -> AdmittedGitRead:
        try:
            return await resolve_admitted_git_read(
                req.source.workflow_id, repository=req.destination.repository
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise CheckpointRestoreError(
                "CHECKPOINT_REPOSITORY_UNAVAILABLE",
                f"source run's admitted repository access is unavailable: {exc}"[:300],
            ) from exc

    def _git(
        self,
        args: list[str],
        cwd: Path | None = None,
        code: str = "CHECKPOINT_BASE_COMMIT_UNAVAILABLE",
        *,
        env: Mapping[str, str] | None = None,
    ) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            env=dict(env) if env is not None else None,
        )
        if result.returncode:
            raise CheckpointRestoreError(
                code, result.stderr.strip()[:300] or "git operation failed"
            )
        return result.stdout.strip()

    def _git_bytes(
        self, args: list[str], cwd: Path, *, input_bytes: bytes | None = None
    ) -> bytes:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            check=False,
            input=input_bytes,
            timeout=120,
        )
        if result.returncode:
            raise CheckpointRestoreError(
                "CHECKPOINT_BASE_COMMIT_UNAVAILABLE",
                result.stderr.decode(errors="replace")[:300],
            )
        return result.stdout

    async def restore(
        self,
        raw: Mapping[str, Any] | ManagedWorkspaceRestoreRequest,
        *,
        access: ArtifactAccessContext,
        admitted_target_branch: str | None = None,
        admitted_projection: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        req = (
            raw
            if isinstance(raw, ManagedWorkspaceRestoreRequest)
            else ManagedWorkspaceRestoreRequest.model_validate(raw)
        )
        # The checkpoint-branch owner may already have renamed a verified
        # candidate before its readiness commit was interrupted. This internal
        # authority is not authored restore data and does not change retained
        # request/receipt digests. It never changes the branch during restore.
        if admitted_target_branch == "":
            admitted_target_branch = None
        if admitted_target_branch is not None:
            if (
                not isinstance(req.destination, SandboxRestoreDestination)
                or req.workspace_policy != "restore_step_input"
                or not isinstance(admitted_target_branch, str)
                or not admitted_target_branch
                or self._git(
                    ["check-ref-format", "--branch", admitted_target_branch],
                    code="CHECKPOINT_DESTINATION_IDENTITY_MISMATCH",
                ) != admitted_target_branch
            ):
                raise CheckpointRestoreError(
                    "CHECKPOINT_DESTINATION_IDENTITY_MISMATCH",
                    "checkpoint branch override is invalid",
                )
        # Only the sandbox readiness owner supplies this internal description
        # after durably binding its projection. It is never wire request data or
        # part of the canonical request/receipt digest.
        if admitted_projection is not None:
            self._input_projection_entries(req, admitted_projection)
        lock_dir = self.root / "managed_restores"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / (
            hashlib.sha256(req.idempotency_key.encode()).hexdigest() + ".lock"
        )
        local_lock = self._locks.setdefault(req.idempotency_key, asyncio.Lock())
        async with local_lock:
            with lock_path.open("a+") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    return await self._restore_locked(
                        req,
                        access=access,
                        admitted_target_branch=admitted_target_branch,
                        admitted_projection=admitted_projection,
                    )
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    async def _restore_locked(
        self,
        req: ManagedWorkspaceRestoreRequest,
        *,
        access: ArtifactAccessContext,
        admitted_target_branch: str | None = None,
        admitted_projection: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        record_dir = self.root / "managed_restores"
        record_dir.mkdir(parents=True, exist_ok=True)
        key_hash = hashlib.sha256(req.idempotency_key.encode()).hexdigest()
        record_path = record_dir / f"{key_hash}.json"
        immutable = _digest(_canonical(req.model_dump(by_alias=True, mode="json")))
        workspace, locator = self._destination(req)
        if record_path.exists():
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                record = None
            if not isinstance(record, dict):
                record = None
            if record is not None:
                if record.get("immutableDigest") != immutable:
                    raise CheckpointRestoreError(
                        "CHECKPOINT_RESTORE_IDEMPOTENCY_CONFLICT",
                        "idempotency key input drift",
                    )
                if record.get("status") == "ready" and workspace.is_dir():
                    if (
                        self._git(["rev-parse", "HEAD"], workspace)
                        != record["result"]["baseCommit"]
                    ):
                        raise CheckpointRestoreError(
                            "CHECKPOINT_RESTORE_IDEMPOTENCY_CONFLICT",
                            "restored candidate was subsequently changed",
                        )
                    status = self._git_bytes(
                        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
                        workspace,
                    )
                    if (
                        admitted_projection is None
                        and _digest(status) != record["result"]["gitStatusDigest"]
                    ):
                        raise CheckpointRestoreError(
                            "CHECKPOINT_RESTORE_IDEMPOTENCY_CONFLICT",
                            "restored candidate was subsequently changed",
                        )
                    manifest = await self._read_retained_manifest(req, access=access)
                    verified_paths = frozenset()
                    if admitted_projection is not None:
                        verified_paths = self._verify_projected_candidate(
                            workspace,
                            manifest,
                            self._input_projection_entries(req, admitted_projection),
                        )
                    else:
                        self._verify_materialized(
                            workspace,
                            manifest,
                            selected_only=req.restores_original_owner,
                        )
                    if (
                        _digest(self._checkpoint_status(status, verified_paths))
                        != record["result"]["gitStatusDigest"]
                    ):
                        raise CheckpointRestoreError(
                            "CHECKPOINT_RESTORE_IDEMPOTENCY_CONFLICT",
                            "restored candidate was subsequently changed",
                        )
                    self._verify_git_candidate(
                        workspace,
                        manifest,
                        req.checkpoint.base_commit,
                        admitted_target_branch=admitted_target_branch,
                        verified_projection_paths=verified_paths,
                    )
                    if self._git(["rev-parse", "HEAD"], workspace) != req.checkpoint.base_commit:
                        raise CheckpointRestoreError("CHECKPOINT_BASE_COMMIT_MISMATCH", "saved destination HEAD changed")
                    status = self._git_bytes(["status", "--porcelain=v1", "-z", "--untracked-files=all"], workspace)
                    if (
                        _digest(self._checkpoint_status(status, verified_paths))
                        != record["result"]["gitStatusDigest"]
                    ):
                        raise CheckpointRestoreError("CHECKPOINT_ENTRY_DIGEST_MISMATCH", "saved destination Git status changed")
                    return record["result"]

        # Lost candidates and new canonical generations still use the strict
        # cold restore below. A projection description never relaxes extraction.
        workspace_parent = workspace.parent
        if not workspace_parent.resolve().is_relative_to(self.root):
            raise CheckpointRestoreError(
                "CHECKPOINT_DESTINATION_IDENTITY_MISMATCH",
                "destination escaped managed authority",
            )
        if self.run_store is not None and isinstance(locator, ManagedWorkspaceLocator):
            managed_run = self.run_store.load(req.destination.agent_run_id)
            # A cold restore is expected to run *before* ``agent_runtime.launch``
            # creates the managed run record, so a missing record is normal and
            # must not block the restore. Identity is bound at launch time by
            # ``assert_ready_for_launch``. Only enforce the binding when a record
            # already exists (e.g. an idempotent re-run after launch).
            if managed_run is not None and (
                managed_run.runtime_id != "codex_cli"
                or managed_run.workflow_id != req.recovery_identity.workflow_id
                or not managed_run.workspace_path
                or Path(managed_run.workspace_path).resolve() != workspace.resolve()
            ):
                raise CheckpointRestoreError(
                    "CHECKPOINT_DESTINATION_IDENTITY_MISMATCH",
                    "destination is not bound to the authoritative managed run record",
                )
        record = {
            "status": "preparing_restore",
            "immutableDigest": immutable,
            "workspaceLocator": locator.model_dump(by_alias=True, mode="json"),
            **({"agentRunId": locator.agent_run_id} if isinstance(locator, ManagedWorkspaceLocator) else {}),
            "capabilityDigest": req.capability_digest,
        }
        tmp = record_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
        os.replace(tmp, record_path)

        checkpoint_bytes = await self._read(
            req.source.checkpoint_ref,
            content_types={
                "application/vnd.moonmind.step-execution-checkpoint+json;version=1"
            },
            access=access,
        )
        try:
            checkpoint = json.loads(checkpoint_bytes)
        except Exception as exc:
            raise CheckpointRestoreError(
                "CHECKPOINT_MANIFEST_CORRUPTED", "checkpoint is not valid JSON"
            ) from exc
        if not isinstance(checkpoint, dict):
            raise CheckpointRestoreError(
                "CHECKPOINT_MANIFEST_CORRUPTED", "checkpoint must be a JSON object"
            )
        workspace_evidence = checkpoint.get("workspace", {})
        if checkpoint.get("contentType") != "application/vnd.moonmind.step-execution-checkpoint+json;version=1":
            raise CheckpointRestoreError("CHECKPOINT_ARTIFACT_CONTENT_TYPE_MISMATCH", "checkpoint schema content type mismatch")
        if workspace_evidence.get("kind") != "worktree_archive":
            raise CheckpointRestoreError("CHECKPOINT_KIND_UNSUPPORTED", "checkpoint is not a worktree archive")
        source_identity = checkpoint.get("source", {})
        expected_source = req.source.model_dump(
            by_alias=True, mode="json", exclude_none=True
        )
        for key in ("workflowId", "runId", "logicalStepId", "executionOrdinal"):
            if source_identity.get(key) != expected_source.get(key):
                raise CheckpointRestoreError(
                    "CHECKPOINT_SOURCE_IDENTITY_MISMATCH",
                    f"checkpoint source {key} mismatch",
                )
        if checkpoint.get("boundary") != req.source.checkpoint_boundary:
            raise CheckpointRestoreError(
                "CHECKPOINT_SOURCE_IDENTITY_MISMATCH", "checkpoint boundary mismatch"
            )
        for key, supplied in (
            ("archiveRef", req.checkpoint.archive_ref),
            ("manifestRef", req.checkpoint.manifest_ref),
            ("archiveDigest", req.checkpoint.archive_digest),
            ("manifestDigest", req.checkpoint.manifest_digest),
            ("baseCommit", req.checkpoint.base_commit),
        ):
            if workspace_evidence.get(key) != supplied:
                raise CheckpointRestoreError(
                    "CHECKPOINT_SOURCE_IDENTITY_MISMATCH", f"checkpoint {key} mismatch"
                )
        archive = await self._read(
            req.checkpoint.archive_ref,
            content_types={"application/vnd.moonmind.worktree-archive"},
            access=access,
        )
        manifest_bytes = await self._read(
            req.checkpoint.manifest_ref,
            content_types={
                "application/json",
                "application/vnd.moonmind.managed-workspace-checkpoint-manifest+json;version=1",
            },
            access=access,
        )
        if _digest(archive) != req.checkpoint.archive_digest:
            raise CheckpointRestoreError(
                "CHECKPOINT_ARCHIVE_CORRUPTED", "archive digest mismatch"
            )
        if _digest(manifest_bytes) != req.checkpoint.manifest_digest:
            raise CheckpointRestoreError(
                "CHECKPOINT_MANIFEST_CORRUPTED", "manifest digest mismatch"
            )
        try:
            manifest = json.loads(manifest_bytes)
        except Exception as exc:
            raise CheckpointRestoreError(
                "CHECKPOINT_MANIFEST_CORRUPTED", "manifest is not valid JSON"
            ) from exc
        managed_manifest = manifest.get("contentType") == (
            "application/vnd.moonmind.managed-workspace-checkpoint-manifest+json;version=1"
        )
        git_manifest = manifest.get("git", {}) if managed_manifest else manifest
        archive_manifest = manifest.get("archive", {}) if managed_manifest else manifest
        manifest = self._normalize_manifest(manifest)
        if git_manifest.get("baseCommit") != req.checkpoint.base_commit:
            raise CheckpointRestoreError(
                "CHECKPOINT_BASE_COMMIT_MISMATCH", "manifest base commit mismatch"
            )
        if manifest.get("schemaVersion") != "v1" or (
            not managed_manifest and manifest.get("kind") != "worktree_archive"
        ):
            raise CheckpointRestoreError("CHECKPOINT_MANIFEST_CORRUPTED", "manifest schema or kind mismatch")
        if archive_manifest.get("ref", archive_manifest.get("archiveRef")) != req.checkpoint.archive_ref or (
            "sha256:" + str(archive_manifest.get("sha256"))
            if managed_manifest and not str(archive_manifest.get("sha256", "")).startswith("sha256:")
            else archive_manifest.get("sha256", archive_manifest.get("archiveDigest"))
        ) != req.checkpoint.archive_digest:
            raise CheckpointRestoreError("CHECKPOINT_MANIFEST_CORRUPTED", "manifest archive identity mismatch")
        entries = manifest.get("entries")
        if not isinstance(entries, list) or (
            not managed_manifest and manifest.get("pathCount") != len(entries)
        ):
            raise CheckpointRestoreError("CHECKPOINT_MANIFEST_CORRUPTED", "manifest entry count mismatch")

        if req.restores_original_owner and workspace.exists():
            # A surviving directory is only a candidate. Adopt it after the
            # same immutable file, HEAD, index and status checks as cold restore.
            self._verify_materialized(workspace, manifest, selected_only=True)
            status_digest = self._verify_git_candidate(workspace, manifest, req.checkpoint.base_commit)
            count = len(entries)
            size = sum(entry.get("bytes", 0) for entry in entries)
        else:
            workspace_parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=workspace_parent))
            try:
                if self.repository_source_root is not None:
                    repo_url = str(self.repository_source_root)
                elif (
                    isinstance(req.destination, SandboxRestoreDestination)
                    and req.destination.repository_identity is not None
                ):
                    from moonmind.omnigent.host_services.github_credentials import (
                        github_clone_source_from_identity,
                    )

                    try:
                        repo_url = github_clone_source_from_identity(
                            req.destination.repository_identity
                        )
                    except ValueError as exc:
                        raise CheckpointRestoreError(
                            "CHECKPOINT_REPOSITORY_UNAVAILABLE",
                            "admitted repository remote is unsafe",
                        ) from exc
                elif req.destination.repository:
                    repo_url = f"https://github.com/{req.destination.repository}.git"
                else:
                    repo_url = None
                history = manifest.get("gitHistory")
                if history and history.get("requiresSourceObjects") is False:
                    # The bundle carries every object: no clone, so no
                    # repository credential is looked up for this restore.
                    self._git(["init", str(staging)])
                    if repo_url is not None:
                        self._git(["remote", "add", "origin", repo_url], staging)
                else:
                    if repo_url is None:
                        raise CheckpointRestoreError(
                            "CHECKPOINT_REPOSITORY_UNAVAILABLE",
                            "checkpoint needs its admitted origin repository",
                        )
                    clone_env: Mapping[str, str] | None = None
                    if self.repository_source_root is None:
                        # Origin objects come through the repository access the
                        # source run admitted (its selected connection or explicit
                        # anonymous intent), never another connection or an
                        # ambient token. The credential reaches Git only through
                        # an in-memory helper, not disk or argv.
                        admitted = await self._admitted_git_read(req)
                        if (
                            isinstance(req.destination, SandboxRestoreDestination)
                            and req.destination.repository_identity is not None
                            and admitted.remote.casefold() != repo_url.casefold()
                        ):
                            raise CheckpointRestoreError(
                                "CHECKPOINT_REPOSITORY_UNAVAILABLE",
                                "source access conflicts with the admitted repository identity",
                            )
                        repo_url = admitted.remote
                        if admitted.token:
                            clone_env = build_github_token_git_environment(
                                admitted.token,
                                base_env=dict(os.environ),
                                host=admitted.host,
                            )
                    self._git(
                        ["clone", "--no-checkout", repo_url, str(staging)],
                        code="CHECKPOINT_REPOSITORY_UNAVAILABLE",
                        env=clone_env,
                    )
                if history and history.get("ref"):
                    bundle = await self._read(
                        history["ref"],
                        content_types={"application/x-git-bundle"},
                        access=access,
                    )
                    if (
                        _digest(bundle) != history.get("digest")
                        or history.get("headCommit") != req.checkpoint.base_commit
                    ):
                        raise CheckpointRestoreError(
                            "CHECKPOINT_ARCHIVE_CORRUPTED",
                            "selected history identity mismatch",
                        )
                    bundle_path = staging / ".git" / "checkpoint.bundle"
                    bundle_path.write_bytes(bundle)
                    try:
                        self._git(["bundle", "verify", str(bundle_path)], staging)
                        self._git(
                            ["fetch", "--no-tags", str(bundle_path), "HEAD"], staging
                        )
                    finally:
                        bundle_path.unlink(missing_ok=True)
                self._git(
                    ["cat-file", "-e", f"{req.checkpoint.base_commit}^{{commit}}"], staging
                )
                branch = self._saved_sandbox_branch(manifest)
                if branch:
                    self._git(["checkout", "-B", branch, req.checkpoint.base_commit], staging)
                else:
                    self._git(["checkout", "--detach", req.checkpoint.base_commit], staging)
                await self._restore_index(staging, manifest, access=access)
                await self._restore_submodules(staging, manifest, access=access)
                count, size = self._extract(
                    archive, staging, manifest,
                    max_entries=req.max_entry_count, max_bytes=req.max_restored_bytes,
                )
                # The base checkout materializes files the checkpoint deleted; replay
                # those deletions so the tree matches the manifest before verifying.
                self._replay_deletions(staging, manifest)
                self._verify_materialized(staging, manifest)
                actual = self._git(
                    ["rev-parse", "HEAD"], staging, "CHECKPOINT_BASE_COMMIT_MISMATCH"
                )
                if actual != req.checkpoint.base_commit:
                    raise CheckpointRestoreError(
                        "CHECKPOINT_BASE_COMMIT_MISMATCH",
                        "restored repository base mismatch",
                    )
                status_payload = self._git_bytes(
                    ["status", "--porcelain=v1", "-z", "--untracked-files=all"], staging
                )
                status_digest = _digest(status_payload)
                self._verify_git_candidate(staging, manifest, req.checkpoint.base_commit)
                expected_status = manifest.get("gitStatusDigest") or manifest.get("git", {}).get("statusDigest")
                if expected_status and status_digest != expected_status:
                    raise CheckpointRestoreError(
                        "CHECKPOINT_ENTRY_DIGEST_MISMATCH", "Git status digest mismatch"
                    )
                backup = workspace_parent / ".restore-previous"
                if backup.exists():
                    shutil.rmtree(backup)
                if workspace.exists():
                    if req.restores_original_owner:
                        raise CheckpointRestoreError("CHECKPOINT_DESTINATION_IDENTITY_MISMATCH", "original workspace appeared during cold restore")
                    os.replace(workspace, backup)
                try:
                    os.replace(staging, workspace)
                except BaseException:
                    if backup.exists() and not workspace.exists():
                        os.replace(backup, workspace)
                    raise
                shutil.rmtree(backup, ignore_errors=True)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise

        evidence = {
            "schemaVersion": "v1",
            "contentType": RESTORATION_EVIDENCE_CONTENT_TYPE,
            "source": {
                **req.source.model_dump(by_alias=True, mode="json", exclude_none=True),
                **req.checkpoint.model_dump(by_alias=True, mode="json"),
            },
            "destination": {
                **req.recovery_identity.model_dump(by_alias=True, mode="json"),
                "workspaceLocator": locator.model_dump(by_alias=True),
            },
            "policy": {
                "workspacePolicy": req.workspace_policy,
                "resumePhase": req.resume_phase,
                "capabilitySetVersion": req.capability_set_version,
                "capabilityDigest": req.capability_digest,
            },
            "verification": {
                "archiveDigestVerified": True,
                "manifestDigestVerified": True,
                "entryDigestsVerified": True,
                "pathContainmentVerified": True,
                "baseCommitVerified": True,
                "gitStatusDigest": status_digest,
                "entryCount": count,
                "restoredBytes": size,
            },
            "createdAt": datetime.now(UTC).isoformat(),
        }
        evidence_bytes = _canonical(evidence)
        evidence_ref = await self._write(evidence_bytes)
        result = ManagedWorkspaceRestoreResult(
            checkpointRef=req.source.checkpoint_ref,
            destinationWorkspaceLocator=locator,
            restorationEvidenceRef=evidence_ref,
            restorationEvidenceDigest=_digest(evidence_bytes),
            baseCommit=req.checkpoint.base_commit,
            restoredEntryCount=count,
            restoredBytes=size,
            gitStatusDigest=status_digest,
            idempotencyKey=req.idempotency_key,
        ).model_dump(by_alias=True, mode="json")
        record.update(status="ready", evidenceRef=evidence_ref, result=result)
        tmp = record_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
        os.replace(tmp, record_path)
        return result

    def assert_ready_for_launch(
        self, *, agent_run_id: str, checkpoint_ref: str, capability_digest: str
    ) -> None:
        for path in (self.root / "managed_restores").glob("*.json"):
            # A partially written, empty, or otherwise corrupted record must not
            # crash the launch gate for every other run; skip it and keep looking
            # for a valid ready restoration.
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            result = record.get("result")
            if not isinstance(result, dict):
                result = {}
            if (
                record.get("status") == "ready"
                and record.get("agentRunId") == agent_run_id
                and result.get("checkpointRef") == checkpoint_ref
                and record.get("capabilityDigest") == capability_digest
            ):
                return
        raise CheckpointRestoreError(
            "CHECKPOINT_DESTINATION_IDENTITY_MISMATCH",
            "verified ready restoration is required before launch",
        )
