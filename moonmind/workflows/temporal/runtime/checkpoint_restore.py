"""Agent-runtime-owned, restart-safe cold restore data plane."""

from __future__ import annotations

import asyncio
import hashlib
import fcntl
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from moonmind.schemas.checkpoint_restore_models import (
    CheckpointRestoreError,
    ManagedWorkspaceRestoreRequest,
    ManagedWorkspaceRestoreResult,
    RESTORATION_EVIDENCE_CONTENT_TYPE,
)
from moonmind.schemas.workspace_locator_models import ManagedWorkspaceLocator

from .git_auth import build_github_token_git_environment
from .managed_api_key_resolve import resolve_github_token_for_launch


# Checkpoint archive/manifest artifacts are written by the capture path under the
# ``system`` owner. The artifact service only grants cross-owner reads to
# ``service:``-prefixed principals, so the restore reader must use one.
_CHECKPOINT_RESTORE_PRINCIPAL = "service:checkpoint_restore"


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

    async def _read(self, ref: str, *, content_types: set[str]) -> bytes:
        try:
            if self.artifact_service is not None:
                artifact, payload = await self.artifact_service.read(
                    artifact_id=ref,
                    principal=_CHECKPOINT_RESTORE_PRINCIPAL,
                    allow_restricted_raw=True,
                )
                content_type = str(getattr(artifact, "content_type", ""))
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
        if path.parts[0] == ".git":
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

    def _verify_materialized(self, staging: Path, manifest: Mapping[str, Any]) -> None:
        expected = {entry["path"]: entry for entry in manifest.get("entries", [])}
        # A symlink is a leaf entry even when it points at a directory, so exclude
        # only real directories; ``is_dir()`` alone would follow directory symlinks
        # and drop them from the set, spuriously failing valid restores.
        actual = {
            str(path.relative_to(staging))
            for path in staging.rglob("*")
            if ".git" not in path.relative_to(staging).parts
            and not (path.is_dir() and not path.is_symlink())
        }
        if actual != set(expected):
            raise CheckpointRestoreError(
                "CHECKPOINT_ENTRY_DIGEST_MISMATCH", "materialized path set mismatch"
            )
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
            if str(relative) not in expected:
                self._clear_existing(path)

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

    def _git_bytes(self, args: list[str], cwd: Path) -> bytes:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, check=False
        )
        if result.returncode:
            raise CheckpointRestoreError(
                "CHECKPOINT_BASE_COMMIT_UNAVAILABLE",
                result.stderr.decode(errors="replace")[:300],
            )
        return result.stdout

    async def restore(
        self, raw: Mapping[str, Any] | ManagedWorkspaceRestoreRequest
    ) -> dict[str, Any]:
        req = (
            raw
            if isinstance(raw, ManagedWorkspaceRestoreRequest)
            else ManagedWorkspaceRestoreRequest.model_validate(raw)
        )
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
                    return await self._restore_locked(req)
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    async def _restore_locked(
        self, req: ManagedWorkspaceRestoreRequest
    ) -> dict[str, Any]:
        record_dir = self.root / "managed_restores"
        record_dir.mkdir(parents=True, exist_ok=True)
        key_hash = hashlib.sha256(req.idempotency_key.encode()).hexdigest()
        record_path = record_dir / f"{key_hash}.json"
        immutable = _digest(_canonical(req.model_dump(by_alias=True, mode="json")))
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
                if record.get("status") == "ready":
                    return record["result"]

        workspace_parent = self.root / req.destination.agent_run_id
        workspace = workspace_parent / "repo"
        if not workspace_parent.resolve().is_relative_to(self.root):
            raise CheckpointRestoreError(
                "CHECKPOINT_DESTINATION_IDENTITY_MISMATCH",
                "destination escaped managed authority",
            )
        if self.run_store is not None:
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
            "agentRunId": req.destination.agent_run_id,
            "capabilityDigest": req.capability_digest,
        }
        tmp = record_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
        os.replace(tmp, record_path)

        checkpoint_bytes = await self._read(
            req.source.checkpoint_ref,
            content_types={"application/vnd.moonmind.step-execution-checkpoint+json;version=1"},
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
        archive = await self._read(req.checkpoint.archive_ref, content_types={"application/vnd.moonmind.worktree-archive"})
        manifest_bytes = await self._read(
            req.checkpoint.manifest_ref,
            content_types={
                "application/json",
                "application/vnd.moonmind.managed-workspace-checkpoint-manifest+json;version=1",
            },
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
        if managed_manifest:
            manifest = dict(manifest)
            manifest["entries"] = [
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

        workspace_parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=workspace_parent))
        try:
            if self.repository_source_root is not None:
                repo_url = str(self.repository_source_root)
                clone_env: Mapping[str, str] | None = None
            else:
                repo_url = f"https://github.com/{req.destination.repository}.git"
                # A private-repo checkpoint that the original run could clone must
                # also be cloneable here. Reuse the launcher's authenticated git
                # environment (in-memory credential helper, no token on disk or in
                # argv) so the cold restore does not fail before extraction.
                token = await resolve_github_token_for_launch()
                clone_env = (
                    build_github_token_git_environment(
                        token, base_env=dict(os.environ)
                    )
                    if token
                    else None
                )
            self._git(
                ["clone", "--no-checkout", repo_url, str(staging)],
                code="CHECKPOINT_REPOSITORY_UNAVAILABLE",
                env=clone_env,
            )
            self._git(
                ["cat-file", "-e", f"{req.checkpoint.base_commit}^{{commit}}"], staging
            )
            self._git(["checkout", "--detach", req.checkpoint.base_commit], staging)
            count, size = self._extract(
                archive, staging, manifest,
                max_entries=req.max_entry_count, max_bytes=req.max_restored_bytes,
            )
            # The base checkout materializes files the checkpoint deleted; replay
            # those deletions so the tree matches the manifest before verifying.
            self._replay_deletions(staging, manifest)
            self._verify_materialized(staging, manifest)
            staged_paths = manifest.get("git", {}).get("stagedPaths", [])
            if isinstance(staged_paths, list) and staged_paths:
                self._git(["add", "-A", "--", *map(str, staged_paths)], staging)
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
            expected_status = manifest.get("gitStatusDigest") or manifest.get("git", {}).get("statusDigest")
            if expected_status and status_digest != expected_status:
                raise CheckpointRestoreError(
                    "CHECKPOINT_ENTRY_DIGEST_MISMATCH", "Git status digest mismatch"
                )
            backup = workspace_parent / ".restore-previous"
            if backup.exists():
                shutil.rmtree(backup)
            if workspace.exists():
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

        locator = ManagedWorkspaceLocator(
            runtimeId="codex_cli",
            agentRunId=req.destination.agent_run_id,
            relativePath="repo",
        )
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
