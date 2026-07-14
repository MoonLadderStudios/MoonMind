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
    ) -> None:
        self.root = Path(authority_root).resolve()
        self.artifact_service = artifact_service
        self.artifact_store = artifact_store
        self.repository_source_root = (
            Path(repository_source_root).resolve() if repository_source_root else None
        )
        self._locks: dict[str, asyncio.Lock] = {}

    async def _read(self, ref: str) -> bytes:
        try:
            if self.artifact_service is not None:
                _artifact, payload = await self.artifact_service.read(
                    artifact_id=ref,
                    principal="system:checkpoint_restore",
                    allow_restricted_raw=True,
                )
                return payload
            return self.artifact_store.get_bytes(ref)
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
                principal="system:checkpoint_restore",
                content_type=RESTORATION_EVIDENCE_CONTENT_TYPE,
                metadata_json={"artifact_kind": "managed_workspace_restoration"},
            )
            completed = await self.artifact_service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="system:checkpoint_restore",
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

    def _extract(
        self, archive: bytes, staging: Path, manifest: Mapping[str, Any]
    ) -> tuple[int, int]:
        expected = {entry["path"]: entry for entry in manifest.get("entries", [])}
        seen: set[str] = set()
        total = 0
        with tarfile.open(fileobj=BytesIO(archive), mode="r:*") as tar:
            for member in tar:
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
                    payload = tar.extractfile(member).read()  # type: ignore[union-attr]
                    total += len(payload)
                    if _digest(payload) != item.get("digest"):
                        raise CheckpointRestoreError(
                            "CHECKPOINT_ENTRY_DIGEST_MISMATCH", "file digest mismatch"
                        )
                    target.write_bytes(payload)
                    os.chmod(target, int(item["mode"], 8) & 0o777)
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

    def _git(
        self,
        args: list[str],
        cwd: Path | None = None,
        code: str = "CHECKPOINT_BASE_COMMIT_UNAVAILABLE",
    ) -> str:
        result = subprocess.run(
            ["git", *args], cwd=cwd, text=True, capture_output=True, check=False
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
            record = json.loads(record_path.read_text())
            if record["immutableDigest"] != immutable:
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
        record = {
            "status": "preparing_restore",
            "immutableDigest": immutable,
            "agentRunId": req.destination.agent_run_id,
            "capabilityDigest": req.capability_digest,
        }
        record_path.write_text(json.dumps(record, sort_keys=True))

        checkpoint_bytes = await self._read(req.source.checkpoint_ref)
        try:
            checkpoint = json.loads(checkpoint_bytes)
        except Exception as exc:
            raise CheckpointRestoreError(
                "CHECKPOINT_MANIFEST_CORRUPTED", "checkpoint is not valid JSON"
            ) from exc
        workspace_evidence = checkpoint.get("workspace", {})
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
        ):
            if workspace_evidence.get(key) != supplied:
                raise CheckpointRestoreError(
                    "CHECKPOINT_SOURCE_IDENTITY_MISMATCH", f"checkpoint {key} mismatch"
                )
        archive = await self._read(req.checkpoint.archive_ref)
        manifest_bytes = await self._read(req.checkpoint.manifest_ref)
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
        if manifest.get("baseCommit") != req.checkpoint.base_commit:
            raise CheckpointRestoreError(
                "CHECKPOINT_BASE_COMMIT_MISMATCH", "manifest base commit mismatch"
            )

        workspace_parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=workspace_parent))
        try:
            repo_url = (
                str(self.repository_source_root)
                if self.repository_source_root is not None
                else f"https://github.com/{req.destination.repository}.git"
            )
            self._git(["clone", "--no-checkout", repo_url, str(staging)])
            self._git(
                ["cat-file", "-e", f"{req.checkpoint.base_commit}^{{commit}}"], staging
            )
            self._git(["checkout", "--detach", req.checkpoint.base_commit], staging)
            count, size = self._extract(archive, staging, manifest)
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
            expected_status = manifest.get("gitStatusDigest")
            if expected_status and status_digest != expected_status:
                raise CheckpointRestoreError(
                    "CHECKPOINT_ENTRY_DIGEST_MISMATCH", "Git status digest mismatch"
                )
            if workspace.exists():
                shutil.rmtree(workspace)
            os.replace(staging, workspace)
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
        tmp.write_text(json.dumps(record, sort_keys=True))
        os.replace(tmp, record_path)
        return result

    def assert_ready_for_launch(
        self, *, agent_run_id: str, checkpoint_ref: str, capability_digest: str
    ) -> None:
        for path in (self.root / "managed_restores").glob("*.json"):
            record = json.loads(path.read_text())
            result = record.get("result", {})
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
