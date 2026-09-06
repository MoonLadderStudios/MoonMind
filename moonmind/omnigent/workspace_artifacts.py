"""Runtime-neutral projection of durable inputs into an Omnigent workspace.

Implements the safe artifact/checkpoint import path from
``docs/RepositoryAccessAndWorkspaceDesign.md`` (``CONTRACT-011``/``INV-006``):
source-artifact admission through the actual artifact service, digest-verified
streaming under explicit resource budgets, extraction into a fresh
attempt-owned staging area, Git-authority neutralization, manifest verification,
and crash-safe promotion of one verified ready generation.

Tar path filtering alone is not a safe restore: every import is
resource-bounded, transaction-scoped to its staging generation, and bound to
its admitted source digest before any ready marker is recorded.
"""

from __future__ import annotations

import configparser
import hashlib
import io
import os
import shutil
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


MAX_INPUT_REFS = 64
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_CHECKPOINT_BYTES = MAX_TOTAL_BYTES
STREAM_CHUNK_BYTES = 1024 * 1024
RESTORE_PRINCIPAL = "service:omnigent_workspace_restore"
ATTACHMENT_PRINCIPAL = "service:omnigent_workspace_attachment"

# Explicit budgets for compressed-source expansion. Header claims are never
# trusted: actual streamed/expanded bytes are enforced during copy.
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_FILES = 20_000
MAX_ARCHIVE_FILE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_DEPTH = 64
MAX_RESTORE_SECONDS = 300.0

# Supported snapshot payload formats. Anything else is classified unsupported,
# never silently reinterpreted or replaced with an alternate source.
_SUPPORTED_TAR_MODES = ("r:gz", "r:bz2", "r:xz", "r:")

# Tar entry types that may enter a workspace. Device nodes, FIFOs, and sparse
# members are classified and rejected; privileged metadata is never honored.
_SUPPORTED_TAR_TYPES = frozenset(
    {
        tarfile.REGTYPE,
        tarfile.AREGTYPE,
        tarfile.DIRTYPE,
        tarfile.SYMTYPE,
        tarfile.LNKTYPE,
    }
)

# Credential directories removed from imported content per owning-workspace
# policy. These are copies inside the staging tree, never live authority.
_CREDENTIAL_DIR_NAMES = frozenset({".ssh", ".gnupg", ".aws", ".azure", ".docker"})
_CREDENTIAL_FILE_NAMES = frozenset(
    {".git-credentials", ".netrc", "_netrc", ".git_credentials"}
)

# Old session authority that must never revive as current authority.
_SESSION_AUTHORITY_NAMES = frozenset(
    {
        "session",
        "sessions",
        "leases",
        "approvals",
        "publication-evidence",
        "publish-evidence",
        "publish-record",
    }
)

# Git config keys/sections that would execute imported setup logic, reattach
# external credential paths, or redirect fetches on first use. History and
# safe content stay as data; only the executable/credential/redirecting
# mechanisms are neutralized.
_GIT_DROP_SECTIONS = frozenset({"credential", "alias"})
_GIT_DROP_KEYS = frozenset(
    {
        ("core", "hookspath"),
        ("core", "fsmonitor"),
        ("core", "sshcommand"),
        ("core", "askpass"),
        ("core", "editor"),
        ("core", "pager"),
        ("core", "worktree"),
        ("http", "extraheader"),
        ("http", "proxy"),
        ("http", "sslkey"),
        ("http", "sslcert"),
        ("http", "cookiefile"),
        ("diff", "command"),
        ("diff", "textconv"),
        ("filter", "clean"),
        ("filter", "smudge"),
        ("filter", "required"),
        ("filter", "process"),
        ("merge", "driver"),
        ("url", "insteadof"),
        ("url", "pushinsteadof"),
        ("include", "path"),
        ("includeif", None),
    }
)


class WorkspaceArtifactProjectionError(RuntimeError):
    """A durable workspace input could not be projected safely."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SourceAdmission:
    """Owner/grant-carrying admission established through the artifact service."""

    artifact_id: str
    owner: str
    digest: str
    size_bytes: int | None
    redaction: str
    quarantined: bool
    expires_at: Any | None


class WorkspaceArtifactProjector:
    """Apply checkpoints and declared inputs through one bounded data plane."""

    def __init__(self, artifact_service: Any | None) -> None:
        self._service = artifact_service

    async def project(
        self,
        workspace: Path,
        *,
        checkpoint_ref: str | None = None,
        restore_refs: tuple[str, ...] = (),
        attachment_refs: tuple[str, ...] = (),
        workflow_id: str,
        runtime_uid: int,
        runtime_gid: int,
        # New safe-source inputs. When a compiled source is supplied it is
        # authoritative for digest, contract, overlay, generation, and grant.
        source: Any | None = None,
        expected_checkpoint_digest: str | None = None,
        restore_contract: str | None = None,
        overlay: str | None = None,
        input_manifest_digest: str | None = None,
        attempt_id: str | None = None,
        generation: int = 1,
        grant: Any | None = None,
        allow_restricted: bool = False,
        target_owner: str | None = None,
    ) -> dict[str, Any]:
        """Project every authored input before the workspace is mounted."""

        from moonmind.omnigent.workspace_sources import (
            ARTIFACT_IMPORT_CONTRACT_V1,
            OVERLAY_ADDITIVE,
            OVERLAY_AUTHORITATIVE,
            WORKSPACE_RESTORE_CONTRACT_V1,
            normalize_digest,
        )

        effective_grant = getattr(source, "grant", None) or grant
        effective_digest = (
            getattr(source, "expected_digest", None) or expected_checkpoint_digest
        )
        source_kind = getattr(source, "kind", None)
        default_contract = (
            ARTIFACT_IMPORT_CONTRACT_V1
            if source_kind == "artifact"
            else WORKSPACE_RESTORE_CONTRACT_V1
        )
        effective_contract = (
            getattr(source, "restore_contract", None)
            or restore_contract
            or default_contract
        )
        effective_overlay = (
            getattr(source, "overlay", None) or overlay or OVERLAY_AUTHORITATIVE
        )
        effective_manifest = (
            getattr(source, "input_manifest_digest", None) or input_manifest_digest
        )
        effective_attempt = getattr(source, "attempt_id", None) or attempt_id
        effective_generation = getattr(source, "generation", None) or generation
        if effective_digest is not None:
            effective_digest = normalize_digest(effective_digest)
        if effective_manifest is not None:
            effective_manifest = normalize_digest(effective_manifest)

        evidence: dict[str, Any] = {}
        if source is not None and getattr(source, "kind", None) in {
            "artifact",
            "checkpoint",
        }:
            # A compiled artifact/checkpoint source is authoritative for the
            # snapshot import even when the legacy flat ref alias is absent.
            checkpoint_ref = checkpoint_ref or getattr(
                source, "checkpoint_ref", None
            ) or getattr(source, "artifact_ref", None)
        if checkpoint_ref:
            if not _contract_supported_for_kind(effective_contract, source_kind):
                raise WorkspaceArtifactProjectionError(
                    f"unsupported workspace-restore contract {effective_contract!r}",
                    code="WORKSPACE_LOCATOR_UNSUPPORTED",
                )
            checkpoint_evidence = await self._apply_checkpoint(
                workspace,
                checkpoint_ref,
                expected_digest=effective_digest,
                restore_contract=effective_contract,
                overlay=effective_overlay,
                input_manifest_digest=effective_manifest,
                attempt_id=effective_attempt,
                generation=int(effective_generation or 1),
                target_workflow_id=workflow_id,
                target_owner=target_owner or workflow_id,
                grant=effective_grant,
                allow_restricted=allow_restricted,
                runtime_uid=runtime_uid,
                runtime_gid=runtime_gid,
            )
            evidence["checkpointRestoreRef"] = checkpoint_ref
            evidence["checkpointRestore"] = checkpoint_evidence
        # Checkpoints preserve repository work, not the prior step's injected
        # context. Remove both runtime-owned input directories after extraction
        # so each step sees only its explicitly authorized refs. Project the
        # current restore inputs after the checkpoint so stale archived files
        # cannot overwrite current authority.
        self._clear_runtime_inputs(workspace)
        restore_evidence = await self._materialize_bundle(
            workspace,
            refs=restore_refs,
            subdir="restore",
            principal=RESTORE_PRINCIPAL,
            noun="restore inputs",
            runtime_uid=runtime_uid,
            runtime_gid=runtime_gid,
            target_workflow_id=workflow_id,
            grant=effective_grant,
        )
        if restore_evidence:
            evidence["restoreInputs"] = restore_evidence
        attachment_evidence = await self._materialize_bundle(
            workspace,
            refs=attachment_refs,
            subdir="attachments",
            principal=ATTACHMENT_PRINCIPAL,
            noun="attachments",
            required_workflow_id=workflow_id,
            runtime_uid=runtime_uid,
            runtime_gid=runtime_gid,
            target_workflow_id=workflow_id,
            grant=effective_grant,
        )
        if attachment_evidence:
            self._exclude_attachments_from_git(workspace)
            evidence["attachments"] = attachment_evidence
        if overlay is not None and overlay not in {
            OVERLAY_ADDITIVE,
            OVERLAY_AUTHORITATIVE,
        }:
            raise WorkspaceArtifactProjectionError(
                "workspace overlay policy must be authoritative|additive",
                code="WORKSPACE_LOCATOR_UNSUPPORTED",
            )
        return evidence

    @staticmethod
    def _clear_runtime_inputs(workspace: Path) -> None:
        """Delete checkpoint-carried inputs without following archived links."""

        workspace_root = workspace.resolve()
        for subdir in ("restore", "attachments"):
            target = workspace / ".moonmind" / subdir
            if not target.is_symlink() and not target.exists():
                continue
            resolved_parent = target.parent.resolve()
            if not resolved_parent.is_relative_to(workspace_root):
                raise WorkspaceArtifactProjectionError(
                    "runtime input cleanup escaped the authorized workspace",
                    code="WORKSPACE_AUTHORITY_MISMATCH",
                )
            try:
                if target.is_symlink() or not target.is_dir():
                    target.unlink()
                else:
                    shutil.rmtree(target)
            except OSError as exc:
                raise WorkspaceArtifactProjectionError(
                    "stale runtime inputs could not be cleared",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                ) from exc

    async def _apply_checkpoint(
        self,
        workspace: Path,
        artifact_ref: str,
        *,
        expected_digest: str | None,
        restore_contract: str,
        overlay: str,
        input_manifest_digest: str | None,
        attempt_id: str | None,
        generation: int,
        target_workflow_id: str,
        target_owner: str,
        grant: Any | None,
        allow_restricted: bool,
        runtime_uid: int,
        runtime_gid: int,
    ) -> dict[str, Any]:
        from moonmind.omnigent.workspace_sources import OVERLAY_AUTHORITATIVE

        artifact_id = self._artifact_id(artifact_ref, noun="workspace checkpoint")
        service = self._require_service("workspace checkpoint")
        admission = await self._admit_source(
            service,
            artifact_id=artifact_id,
            expected_digest=expected_digest,
            budget_bytes=MAX_CHECKPOINT_BYTES,
            principal=RESTORE_PRINCIPAL,
            noun="workspace checkpoint",
            target_workflow_id=target_workflow_id,
            grant=grant,
            allow_restricted=allow_restricted,
        )
        token = str(attempt_id or "adhoc")
        staging = self._staging_path(workspace, token=token, generation=generation)
        backup = self._backup_path(workspace, token=token, generation=generation)
        lock = self._lock_path(workspace)
        self._acquire_import_lock(lock, token=token)
        try:
            self._reconcile_staging(staging, backup)
            archive_path = await self._download_verified(
                service,
                admission=admission,
                principal=RESTORE_PRINCIPAL,
                budget_bytes=MAX_CHECKPOINT_BYTES,
                noun="workspace checkpoint",
                allow_restricted=allow_restricted,
                staging_dir=staging.parent,
            )
            try:
                manifest = self._extract_archive(
                    archive_path,
                    staging,
                    noun="workspace checkpoint",
                    runtime_uid=runtime_uid,
                    runtime_gid=runtime_gid,
                )
                self._verify_staging_manifest(staging, manifest)
                # Completeness is assessed before neutralization: an external
                # object store, thin baseline, submodule, or LFS dependency
                # is independently-admitted-or-rejected evidence, and
                # neutralization must not erase that signal first.
                completeness = self._assess_completeness(staging)
                if completeness:
                    raise WorkspaceArtifactProjectionError(
                        "workspace checkpoint is incomplete: "
                        + "; ".join(completeness),
                        code="WORKSPACE_RESTORE_INCOMPLETE",
                    )
                self._neutralize_imported_git_authority(staging)
                self._verify_staging_manifest(staging, manifest)
            finally:
                archive_path.unlink(missing_ok=True)
            if overlay == OVERLAY_AUTHORITATIVE:
                self._promote_authoritative(workspace, staging, backup)
            else:
                self._promote_additive(workspace, staging)
            self._remove_tree(staging)
            self._remove_tree(backup)
        finally:
            self._release_import_lock(lock, token=token)
        return {
            "artifactId": artifact_id,
            "sourceDigest": admission.digest,
            "restoreContract": restore_contract,
            "restoreContractVersion": 1,
            "inputManifestDigest": input_manifest_digest,
            "targetOwner": target_owner,
            "attemptId": attempt_id,
            "generation": generation,
            "overlay": overlay,
            "manifest": manifest,
        }

    # -- admission through the actual artifact service ---------------------

    def _require_service(self, noun: str) -> Any:
        if self._service is None:
            raise WorkspaceArtifactProjectionError(
                f"{noun} require an artifact service to resolve refs",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        return self._service

    @staticmethod
    async def _admit_source(
        service: Any,
        *,
        artifact_id: str,
        expected_digest: str | None,
        budget_bytes: int,
        principal: str,
        noun: str,
        target_workflow_id: str | None,
        grant: Any | None,
        allow_restricted: bool,
    ) -> SourceAdmission:
        """Establish permission, status, completeness, digest, lifetime.

        Uses the actual artifact service: metadata is required (a
        read-bytes-only adapter cannot establish owner/digest/quarantine
        permission), the artifact must be COMPLETE and unexpired, the admitted
        digest must match, the target workflow must be linked or explicitly
        granted, and restricted/quarantined bytes need their own explicit
        policy — generic restore never releases protected raw content.
        """

        from moonmind.omnigent.workspace_sources import normalize_digest

        get_metadata = getattr(service, "get_metadata", None)
        if get_metadata is None:
            raise WorkspaceArtifactProjectionError(
                f"{noun} require linked artifact metadata; a read-bytes-only "
                "adapter cannot establish ownership or permission",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        try:
            metadata = await get_metadata(artifact_id=artifact_id, principal=principal)
        except Exception as exc:
            raise WorkspaceArtifactProjectionError(
                f"{noun} metadata is unavailable",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            ) from exc
        if metadata is None:
            raise WorkspaceArtifactProjectionError(
                f"{noun} is missing artifact metadata",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        artifact = metadata[0] if isinstance(metadata, tuple) else metadata
        links: Any = ()
        if isinstance(metadata, tuple) and len(metadata) > 1:
            links = metadata[1] or ()
        if artifact is None:
            raise WorkspaceArtifactProjectionError(
                f"{noun} is missing artifact metadata",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        status = str(getattr(artifact, "status", "") or "")
        if "COMPLETE" not in status.upper():
            raise WorkspaceArtifactProjectionError(
                f"{noun} is not a complete artifact (status {status or 'unknown'})",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        expires_at = getattr(artifact, "expires_at", None)
        if expires_at is not None:
            try:
                from datetime import datetime

                now = datetime.now(tz=expires_at.tzinfo) if expires_at.tzinfo else datetime.now()
                expired = expires_at <= now
            except Exception:
                expired = False
            if expired:
                raise WorkspaceArtifactProjectionError(
                    f"{noun} artifact lifetime has expired",
                    code="WORKSPACE_AUTHORITY_MISMATCH",
                )
        size = getattr(artifact, "size_bytes", None)
        if isinstance(size, int) and size > budget_bytes:
            raise WorkspaceArtifactProjectionError(
                f"{noun} exceed the authorized workspace bound",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        row_digest_raw = getattr(artifact, "sha256", None)
        row_digest = None
        if isinstance(row_digest_raw, str) and row_digest_raw.strip():
            try:
                row_digest = normalize_digest(row_digest_raw)
            except ValueError as exc:
                raise WorkspaceArtifactProjectionError(
                    f"{noun} carries an invalid recorded digest",
                    code="WORKSPACE_AUTHORITY_MISMATCH",
                ) from exc
        admitted_digest = None
        if expected_digest is not None:
            admitted_digest = normalize_digest(expected_digest)
            if row_digest is not None and row_digest != admitted_digest:
                raise WorkspaceArtifactProjectionError(
                    f"{noun} digest does not match the admitted source digest",
                    code="WORKSPACE_DIGEST_MISMATCH",
                )
        elif row_digest is not None:
            admitted_digest = row_digest
        else:
            raise WorkspaceArtifactProjectionError(
                f"{noun} names no verifiable content digest",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        owner = (
            getattr(artifact, "created_by_principal", None)
            or getattr(artifact, "owner", None)
            or ""
        )
        owner = str(owner or "").strip()
        # Authorization: exact workflow linkage, or an explicit grant carrying
        # the admitted owner/source grant across the service boundary. A
        # family-prefix link alone is forgeable and never sufficient.
        if target_workflow_id:
            authorized = any(
                str(getattr(link, "workflow_id", "") or "") == target_workflow_id
                for link in links or ()
            )
            if not authorized and grant is not None:
                grant_owner = str(
                    getattr(grant, "owner_workflow_id", "") or ""
                ).strip()
                grant_grantee = str(
                    getattr(grant, "grantee_workflow_id", "") or ""
                ).strip()
                owner_linked = any(
                    str(getattr(link, "workflow_id", "") or "") == grant_owner
                    for link in links or ()
                )
                authorized = bool(
                    grant_owner and owner_linked and grant_grantee == target_workflow_id
                )
            if not authorized:
                raise WorkspaceArtifactProjectionError(
                    f"{noun} is not linked to the target workflow",
                    code="WORKSPACE_AUTHORITY_MISMATCH",
                )
        redaction = str(
            getattr(getattr(artifact, "redaction_level", ""), "value", None)
            or getattr(artifact, "redaction_level", "")
            or "NONE"
        ).upper()
        quarantined = WorkspaceArtifactProjector._is_quarantined(artifact)
        if redaction == "RESTRICTED" and not allow_restricted:
            raise WorkspaceArtifactProjectionError(
                f"{noun} is restricted and requires an explicit restricted-source policy",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        if quarantined:
            raise WorkspaceArtifactProjectionError(
                f"{noun} is quarantined and cannot enter an agent workspace",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        return SourceAdmission(
            artifact_id=artifact_id,
            owner=owner,
            digest=admitted_digest,
            size_bytes=size if isinstance(size, int) else None,
            redaction=redaction,
            quarantined=quarantined,
            expires_at=expires_at,
        )

    @staticmethod
    def _is_quarantined(artifact: Any) -> bool:
        for attr in ("quarantine", "quarantined"):
            if bool(getattr(artifact, attr, False)):
                return True
        metadata_json = getattr(artifact, "metadata_json", None)
        if isinstance(metadata_json, dict) and bool(metadata_json.get("quarantine")):
            return True
        return False

    # -- verified download -------------------------------------------------

    async def _download_verified(
        self,
        service: Any,
        *,
        admission: SourceAdmission,
        principal: str,
        budget_bytes: int,
        noun: str,
        allow_restricted: bool,
        staging_dir: Path,
    ) -> Path:
        read_chunks = getattr(service, "read_chunks", None)
        if read_chunks is None and not hasattr(service, "read"):
            raise WorkspaceArtifactProjectionError(
                f"{noun} artifact service cannot stream source bytes",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".moonmind-source-",
            suffix=".bin",
            dir=staging_dir,
        )
        os.close(descriptor)
        target = Path(temporary_name)
        hasher = hashlib.sha256()
        written = 0
        try:
            if read_chunks is not None:
                pending = read_chunks(
                    artifact_id=admission.artifact_id,
                    principal=principal,
                    allow_restricted_raw=allow_restricted,
                    chunk_size=STREAM_CHUNK_BYTES,
                )
                if hasattr(pending, "__await__"):
                    pending = await pending
                _artifact, chunks = pending
                fd = os.open(
                    target,
                    os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                    0o600,
                )
                with os.fdopen(fd, "wb") as stream:
                    for chunk in chunks:
                        if not isinstance(chunk, (bytes, bytearray)):
                            raise WorkspaceArtifactProjectionError(
                                f"{noun} source stream is malformed",
                                code="WORKSPACE_ARCHIVE_MALFORMED",
                            )
                        written += len(chunk)
                        if written > budget_bytes:
                            raise WorkspaceArtifactProjectionError(
                                f"{noun} exceed the authorized workspace bound",
                                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                            )
                        hasher.update(chunk)
                        stream.write(chunk)
            else:
                pending = service.read(
                    artifact_id=admission.artifact_id,
                    principal=principal,
                    allow_restricted_raw=allow_restricted,
                )
                if hasattr(pending, "__await__"):
                    pending = await pending
                _artifact, payload = pending
                if len(payload) > budget_bytes:
                    raise WorkspaceArtifactProjectionError(
                        f"{noun} exceed the authorized workspace bound",
                        code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                    )
                hasher.update(payload)
                written = len(payload)
                fd = os.open(
                    target,
                    os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                    0o600,
                )
                with os.fdopen(fd, "wb") as stream:
                    stream.write(payload)
        except WorkspaceArtifactProjectionError:
            target.unlink(missing_ok=True)
            raise
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise WorkspaceArtifactProjectionError(
                f"{noun} source bytes are unavailable",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            ) from exc
        actual = f"sha256:{hasher.hexdigest()}"
        if actual != admission.digest:
            target.unlink(missing_ok=True)
            raise WorkspaceArtifactProjectionError(
                f"{noun} bytes do not match the admitted source digest",
                code="WORKSPACE_DIGEST_MISMATCH",
            )
        return target

    @staticmethod
    def _staging_path(workspace: Path, *, token: str, generation: int) -> Path:
        safe_token = "".join(
            ch if ch.isalnum() or ch in {"-", "_"} else "-"
            for ch in str(token or "adhoc")
        )[:64] or "adhoc"
        return workspace.parent / f".staging-{workspace.name}-g{generation}-{safe_token}"

    @staticmethod
    def _backup_path(workspace: Path, *, token: str, generation: int) -> Path:
        safe_token = "".join(
            ch if ch.isalnum() or ch in {"-", "_"} else "-"
            for ch in str(token or "adhoc")
        )[:64] or "adhoc"
        return workspace.parent / f".backup-{workspace.name}-g{generation}-{safe_token}"

    @staticmethod
    def _lock_path(workspace: Path) -> Path:
        return workspace.parent / f".import-{workspace.name}.lock"

    @staticmethod
    def _acquire_import_lock(lock: Path, *, token: str) -> None:
        # No concurrent agent may mutate the extraction tree: the lock is held
        # from staging creation through promotion. A retry reconciles the same
        # generation by taking over its own token; a foreign token fails
        # closed instead of mutating another attempt's tree.
        try:
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            try:
                recorded = lock.read_text(encoding="utf-8")
            except OSError:
                recorded = ""
            if recorded.strip() == str(token):
                return
            raise WorkspaceArtifactProjectionError(
                "another workspace import is already in flight",
                code="WORKSPACE_IMPORT_CONFLICT",
            ) from exc
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(str(token))

    @staticmethod
    def _release_import_lock(lock: Path, *, token: str) -> None:
        try:
            if lock.read_text(encoding="utf-8").strip() == str(token):
                lock.unlink()
        except OSError:
            pass

    @staticmethod
    def _reconcile_staging(staging: Path, backup: Path) -> None:
        # Retry reconciles the same generation: residue owned by this import
        # is removed before a fresh attempt. Only the import-owned staging
        # generation is ever deleted here, never a live authorized workspace.
        for path in (staging, backup):
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise WorkspaceArtifactProjectionError(
                    "import staging path is obstructed",
                    code="WORKSPACE_IMPORT_CONFLICT",
                )
            if path.is_dir():
                shutil.rmtree(path)
        staging.mkdir(parents=True, exist_ok=False)

    @staticmethod
    def _remove_tree(path: Path) -> None:
        try:
            if path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise WorkspaceArtifactProjectionError(
                "import staging cleanup failed",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            ) from exc

    def _extract_archive(
        self,
        archive_path: Path,
        staging: Path,
        *,
        noun: str,
        runtime_uid: int,
        runtime_gid: int,
    ) -> dict[str, Any]:
        """Stream members into staging with sequential link/overwrite safety."""

        staging_root = staging.resolve()
        deadline = time.monotonic() + MAX_RESTORE_SECONDS
        seen: set[str] = set()
        seen_folded: set[str] = set()
        hardlink_sources: set[str] = set()
        file_count = 0
        expanded_bytes = 0
        try:
            archive = tarfile.open(archive_path, mode="r:*")
        except EOFError as exc:
            raise WorkspaceArtifactProjectionError(
                f"{noun} archive is truncated",
                code="WORKSPACE_ARCHIVE_MALFORMED",
            ) from exc
        except (tarfile.TarError, OSError) as exc:
            raise WorkspaceArtifactProjectionError(
                f"{noun} archive format is unsupported",
                code="WORKSPACE_ARCHIVE_UNSUPPORTED",
            ) from exc
        with archive:
            while True:
                if time.monotonic() > deadline:
                    raise WorkspaceArtifactProjectionError(
                        f"{noun} archive exceeded the processing-time budget",
                        code="WORKSPACE_ARCHIVE_OVERSIZED",
                    )
                try:
                    member = archive.next()
                except (tarfile.TarError, OSError, EOFError) as exc:
                    raise WorkspaceArtifactProjectionError(
                        f"{noun} archive is malformed",
                        code="WORKSPACE_ARCHIVE_MALFORMED",
                    ) from exc
                if member is None:
                    break
                file_count += 1
                if file_count > MAX_ARCHIVE_FILES:
                    raise WorkspaceArtifactProjectionError(
                        f"{noun} archive exceeds the file-count bound",
                        code="WORKSPACE_ARCHIVE_OVERSIZED",
                    )
                rel = self._checked_member_path(
                    member.name, noun=noun, staging_root=staging_root
                )
                if rel in seen:
                    raise WorkspaceArtifactProjectionError(
                        f"{noun} archive contains a duplicate entry",
                        code="WORKSPACE_ARCHIVE_CONFLICT",
                    )
                folded = rel.casefold()
                if folded in seen_folded:
                    raise WorkspaceArtifactProjectionError(
                        f"{noun} archive contains conflicting filesystem names",
                        code="WORKSPACE_ARCHIVE_CONFLICT",
                    )
                seen.add(rel)
                seen_folded.add(folded)
                if member.type not in _SUPPORTED_TAR_TYPES:
                    raise WorkspaceArtifactProjectionError(
                        f"{noun} archive contains an unsupported entry type",
                        code="WORKSPACE_ARCHIVE_UNSUPPORTED",
                    )
                if getattr(member, "sparse", None):
                    raise WorkspaceArtifactProjectionError(
                        f"{noun} archive contains a sparse member",
                        code="WORKSPACE_ARCHIVE_UNSUPPORTED",
                    )
                if member.size > MAX_ARCHIVE_FILE_BYTES:
                    raise WorkspaceArtifactProjectionError(
                        f"{noun} archive member exceeds the per-file bound",
                        code="WORKSPACE_ARCHIVE_OVERSIZED",
                    )
                if member.isdir():
                    self._mkdir_staging(staging_root, rel, member, noun=noun)
                    continue
                if member.issym():
                    self._write_symlink(staging_root, rel, member, noun=noun)
                    continue
                if member.islnk():
                    self._write_hardlink(
                        staging_root,
                        rel,
                        member,
                        noun=noun,
                        hardlink_sources=hardlink_sources,
                    )
                    continue
                # Regular file: stream the member payload with actual expanded
                # byte accounting rather than trusting the header claim.
                expanded_bytes = self._write_regular_file(
                    archive,
                    staging_root,
                    rel,
                    member,
                    noun=noun,
                    expanded_bytes=expanded_bytes,
                )
                hardlink_sources.add(rel)
        return {
            "files": file_count,
            "expandedBytes": expanded_bytes,
            "entries": sorted(seen),
        }

    @staticmethod
    def _checked_member_path(name: str, *, noun: str, staging_root: Path) -> str:
        raw = str(name or "")
        if not raw or raw.startswith("/") or "\x00" in raw:
            raise WorkspaceArtifactProjectionError(
                f"{noun} archive contains an absolute or empty path",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        pure = PurePosixPath(raw)
        parts = [p for p in pure.parts if p not in {"", "."}]
        if not parts or any(p == ".." for p in parts):
            raise WorkspaceArtifactProjectionError(
                f"{noun} archive contains an escaping path",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        if len(parts) > MAX_ARCHIVE_DEPTH:
            raise WorkspaceArtifactProjectionError(
                f"{noun} archive exceeds the path-depth bound",
                code="WORKSPACE_ARCHIVE_OVERSIZED",
            )
        return "/".join(parts)

    @staticmethod
    def _staging_target(staging_root: Path, rel: str, *, noun: str) -> Path:
        target = staging_root / Path(*rel.split("/"))
        resolved_parent = target.parent.resolve()
        if resolved_parent != staging_root and not resolved_parent.is_relative_to(
            staging_root
        ):
            raise WorkspaceArtifactProjectionError(
                f"{noun} archive member escapes the staging area",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        # A pre-existing symlink at an ancestor would redirect this write
        # outside staging; every ancestor must be a real directory.
        probe = staging_root
        for part in rel.split("/")[:-1]:
            probe = probe / part
            if probe.is_symlink() or (probe.exists() and not probe.is_dir()):
                raise WorkspaceArtifactProjectionError(
                    f"{noun} archive member is shadowed by an earlier entry",
                    code="WORKSPACE_ARCHIVE_CONFLICT",
                )
        return target

    def _mkdir_staging(
        self, staging_root: Path, rel: str, member: tarfile.TarInfo, *, noun: str
    ) -> None:
        target = self._staging_target(staging_root, rel, noun=noun)
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise WorkspaceArtifactProjectionError(
                f"{noun} archive contains conflicting entries",
                code="WORKSPACE_ARCHIVE_CONFLICT",
            )
        target.mkdir(parents=True, exist_ok=True)
        self._normalize_mode(target, member, is_dir=True)

    def _write_symlink(
        self,
        staging_root: Path,
        rel: str,
        member: tarfile.TarInfo,
        *,
        noun: str,
    ) -> None:
        linkname = str(member.linkname or "")
        if not linkname or linkname.startswith("/") or "\x00" in linkname:
            raise WorkspaceArtifactProjectionError(
                f"{noun} archive contains an absolute symlink",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        # Lexical containment: resolve the link against its parent purely
        # lexically so ordering cannot smuggle an escape. A final sweep after
        # all writes re-verifies every link against the materialized tree.
        parent_parts = rel.split("/")[:-1]
        target_parts: list[str] = []
        for part in (*parent_parts, *linkname.split("/")):
            if part in {"", "."}:
                continue
            if part == "..":
                if not target_parts:
                    raise WorkspaceArtifactProjectionError(
                        f"{noun} archive symlink escapes the workspace",
                        code="WORKSPACE_AUTHORITY_MISMATCH",
                    )
                target_parts.pop()
            else:
                target_parts.append(part)
        target = self._staging_target(staging_root, rel, noun=noun)
        if target.is_symlink() or target.exists():
            raise WorkspaceArtifactProjectionError(
                f"{noun} archive contains conflicting entries",
                code="WORKSPACE_ARCHIVE_CONFLICT",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.symlink_to(linkname)
        except OSError as exc:
            raise WorkspaceArtifactProjectionError(
                f"{noun} archive symlink could not be created",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            ) from exc

    def _write_hardlink(
        self,
        staging_root: Path,
        rel: str,
        member: tarfile.TarInfo,
        *,
        noun: str,
        hardlink_sources: set[str],
    ) -> None:
        linkname = str(member.linkname or "").lstrip("./")
        if not linkname or linkname.startswith("/") or ".." in PurePosixPath(
            linkname
        ).parts:
            raise WorkspaceArtifactProjectionError(
                f"{noun} archive hardlink escapes the workspace",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        # Sequential safety: a hardlink may only reference an already-written
        # regular file. A forward reference (link-order attack) fails closed
        # instead of assuming preflight resolution proves later writes safe.
        if linkname not in hardlink_sources:
            raise WorkspaceArtifactProjectionError(
                f"{noun} archive hardlink references an unavailable entry",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        target = self._staging_target(staging_root, rel, noun=noun)
        if target.is_symlink() or target.exists():
            raise WorkspaceArtifactProjectionError(
                f"{noun} archive contains conflicting entries",
                code="WORKSPACE_ARCHIVE_CONFLICT",
            )
        source = staging_root / Path(*linkname.split("/"))
        if source.is_symlink() or not source.is_file():
            raise WorkspaceArtifactProjectionError(
                f"{noun} archive hardlink source is unsafe",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, target)
        except OSError as exc:
            raise WorkspaceArtifactProjectionError(
                f"{noun} archive hardlink could not be created",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            ) from exc

    def _write_regular_file(
        self,
        archive: tarfile.TarFile,
        staging_root: Path,
        rel: str,
        member: tarfile.TarInfo,
        *,
        noun: str,
        expanded_bytes: int,
    ) -> int:
        target = self._staging_target(staging_root, rel, noun=noun)
        if target.is_symlink() or target.exists():
            raise WorkspaceArtifactProjectionError(
                f"{noun} archive contains conflicting entries",
                code="WORKSPACE_ARCHIVE_CONFLICT",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        reader = archive.extractfile(member)
        if reader is None:
            raise WorkspaceArtifactProjectionError(
                f"{noun} archive member has no payload",
                code="WORKSPACE_ARCHIVE_MALFORMED",
            )
        written = 0
        try:
            fd = os.open(
                target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
        except FileExistsError as exc:
            raise WorkspaceArtifactProjectionError(
                f"{noun} archive member collided with the target filesystem",
                code="WORKSPACE_ARCHIVE_CONFLICT",
            ) from exc
        try:
            with os.fdopen(fd, "wb") as stream:
                while True:
                    chunk = reader.read(STREAM_CHUNK_BYTES)
                    if not chunk:
                        break
                    written += len(chunk)
                    expanded_bytes += len(chunk)
                    if written > MAX_ARCHIVE_FILE_BYTES:
                        raise WorkspaceArtifactProjectionError(
                            f"{noun} archive member exceeds the per-file bound",
                            code="WORKSPACE_ARCHIVE_OVERSIZED",
                        )
                    if expanded_bytes > MAX_EXPANDED_BYTES:
                        raise WorkspaceArtifactProjectionError(
                            f"{noun} archive exceeds the expanded-bytes bound",
                            code="WORKSPACE_ARCHIVE_OVERSIZED",
                        )
                    stream.write(chunk)
        except WorkspaceArtifactProjectionError:
            target.unlink(missing_ok=True)
            raise
        except (OSError, EOFError, tarfile.TarError) as exc:
            target.unlink(missing_ok=True)
            raise WorkspaceArtifactProjectionError(
                f"{noun} archive is truncated or malformed",
                code="WORKSPACE_ARCHIVE_MALFORMED",
            ) from exc
        if written != member.size:
            target.unlink(missing_ok=True)
            raise WorkspaceArtifactProjectionError(
                f"{noun} archive member size does not match its header",
                code="WORKSPACE_ARCHIVE_MALFORMED",
            )
        self._normalize_mode(target, member, is_dir=False)
        return expanded_bytes

    @staticmethod
    def _normalize_mode(target: Path, member: tarfile.TarInfo, *, is_dir: bool) -> None:
        # Normalize allowed permissions: drop setuid/setgid/sticky and
        # privileged metadata, keep the owner-executable bit as data. UIDs,
        # GIDs, and device semantics from the archive are never honored.
        if is_dir:
            mode = 0o755
        elif int(getattr(member, "mode", 0) or 0) & 0o100:
            mode = 0o755
        else:
            mode = 0o644
        try:
            os.chmod(target, mode, follow_symlinks=False)
        except OSError:
            pass

    def _verify_staging_manifest(self, staging: Path, manifest: dict[str, Any]) -> None:
        """Re-verify every staged link against the materialized tree."""

        staging_root = staging.resolve()
        for rel in manifest.get("entries", ()):
            target = staging_root / Path(*str(rel).split("/"))
            if target.is_symlink():
                try:
                    resolved = target.resolve()
                except OSError:
                    raise WorkspaceArtifactProjectionError(
                        "workspace checkpoint symlink is unresolvable",
                        code="WORKSPACE_AUTHORITY_MISMATCH",
                    )
                if resolved != staging_root and not resolved.is_relative_to(
                    staging_root
                ):
                    raise WorkspaceArtifactProjectionError(
                        "workspace checkpoint symlink escapes workspace",
                        code="WORKSPACE_AUTHORITY_MISMATCH",
                    )

    def _promote_authoritative(self, workspace: Path, staging: Path, backup: Path) -> None:
        """Promote staging as the one verified ready generation.

        Crash-safe swap: the previous destination (a prior clone, failed
        attempt residue, or an older generation) moves to an import-owned
        backup name, staging renames into place, and only then is the backup
        removed. A crash before the swap leaves the destination untouched; a
        crash after the swap leaves a complete promoted tree plus
        attempt-owned residue that a retry reconciles.
        """

        parent = workspace.parent
        if workspace.is_symlink():
            raise WorkspaceArtifactProjectionError(
                "authorized workspace must not be a symlink",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        if backup.exists() or backup.is_symlink():
            raise WorkspaceArtifactProjectionError(
                "import backup path is obstructed",
                code="WORKSPACE_IMPORT_CONFLICT",
            )
        try:
            if workspace.exists():
                os.rename(workspace, backup)
            os.rename(staging, workspace)
        except OSError as exc:
            # Reconcile: never leave the authorized path missing when a
            # backup exists. A crash between the two renames is recovered by
            # restoring the backup before reporting the failure.
            try:
                if not workspace.exists() and backup.exists():
                    os.rename(backup, workspace)
            except OSError:
                pass
            raise WorkspaceArtifactProjectionError(
                "workspace checkpoint promotion failed",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            ) from exc
        self._remove_tree(backup)

    def _promote_additive(self, workspace: Path, staging: Path) -> None:
        """Overlay staging entries additively without clearing the destination."""

        workspace.mkdir(parents=True, exist_ok=True)
        for entry in sorted(staging.iterdir()):
            target = workspace / entry.name
            if target.is_symlink() or target.exists():
                raise WorkspaceArtifactProjectionError(
                    "workspace overlay collides with destination content",
                    code="WORKSPACE_ARCHIVE_CONFLICT",
                )
            os.rename(entry, target)

    # -- completeness: truthful, never silently skipped ---------------------

    def _assess_completeness(self, staging: Path) -> list[str]:
        """Report incomplete restores that need independent admission."""

        reasons: list[str] = []
        git_dir = staging / ".git"
        # An unresolved large-file pointer is a content stub wherever it
        # appears; truthful completeness does not depend on `.git` presence.
        reasons.extend(self._find_unresolved_lfs(staging, git_dir))
        if git_dir.is_dir() and not git_dir.is_symlink():
            alternates = git_dir / "objects" / "info" / "alternates"
            if alternates.is_file() and not alternates.is_symlink():
                reasons.append("external object alternates require admission")
            if list(git_dir.glob("objects/pack/*.thinpack")):
                reasons.append("thin pack requires its external baseline")
            gitmodules = staging / ".gitmodules"
            if gitmodules.is_file():
                try:
                    parser = configparser.ConfigParser(interpolation=None)
                    parser.read(gitmodules, encoding="utf-8")
                except (configparser.Error, OSError, UnicodeDecodeError):
                    reasons.append("submodule configuration is unreadable")
                    parser = None
                if parser is not None:
                    modules_dir = git_dir / "modules"
                    for section in parser.sections():
                        try:
                            sub_path = parser[section]["path"]
                        except KeyError:
                            reasons.append(
                                f"submodule {section!r} path requires admission"
                            )
                            break
                        worktree = staging / sub_path
                        populated = worktree.is_dir() and any(
                            worktree.iterdir()
                        )
                        admitted = (modules_dir / section).is_dir()
                        if not populated and not admitted:
                            reasons.append(
                                f"submodule {section!r} content requires admission"
                            )
                            break
        return list(dict.fromkeys(reasons))

    @staticmethod
    def _find_unresolved_lfs(staging: Path, git_dir: Path) -> list[str]:
        pointers: list[str] = []
        for dirpath, dirnames, filenames in os.walk(staging, followlinks=False):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for filename in filenames:
                candidate = Path(dirpath) / filename
                try:
                    if candidate.stat().st_size > 1024:
                        continue
                    head = candidate.read_bytes()[:256]
                except OSError:
                    continue
                if b"version https://git-lfs.github.com/spec/" in head:
                    pointers.append(os.path.relpath(candidate, staging))
                    if len(pointers) >= 4:
                        break
            if len(pointers) >= 4:
                break
        if not pointers:
            return []
        objects_dir = git_dir / "lfs" / "objects"
        if objects_dir.is_dir() and any(objects_dir.iterdir()):
            return []
        return ["large-file objects require independent admission"]

    # -- Git-authority neutralization ----------------------------------------

    def _neutralize_imported_git_authority(self, staging: Path) -> None:
        """Neutralize hooks/helpers/credentials/session authority pre-launch.

        Imported configuration that would execute on the first Git command or
        runtime launch is neutralized according to the owning workspace
        policy: hooks, exec/credential/redirect config, external gitdir and
        worktree administration, alternates, include paths, submodule exec
        configuration and filter drivers, credential directories, and old
        session authority. Safe content and history stay as data — Git history
        and ordinary executables are never rejected wholesale.
        """

        git_dir = staging / ".git"
        git_file = staging / ".git"
        if git_file.is_file() and not git_file.is_symlink():
            # A gitdir pointer to an external directory is outside the
            # import authority and cannot be restored safely.
            try:
                pointer = git_file.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise WorkspaceArtifactProjectionError(
                    "imported gitdir pointer is unreadable",
                    code="WORKSPACE_AUTHORITY_MISMATCH",
                ) from exc
            if not pointer.startswith("gitdir:"):
                raise WorkspaceArtifactProjectionError(
                    "imported gitdir pointer is malformed",
                    code="WORKSPACE_AUTHORITY_MISMATCH",
                )
            external = (staging / pointer[len("gitdir:"):].strip()).resolve()
            if external != staging.resolve() / ".git" and not external.is_relative_to(
                staging.resolve()
            ):
                raise WorkspaceArtifactProjectionError(
                    "imported external git directory requires admission",
                    code="WORKSPACE_AUTHORITY_MISMATCH",
                )
        if git_dir.is_dir() and not git_dir.is_symlink():
            self._sanitize_git_dir(git_dir, staging)
        # Nested submodule/worktree git pointers: each must resolve inside
        # the staging tree or the import fails closed.
        staging_root = staging.resolve()
        for dirpath, _dirnames, filenames in os.walk(staging, followlinks=False):
            if ".git" in filenames:
                pointer_file = Path(dirpath) / ".git"
                if pointer_file.is_symlink() or not pointer_file.is_file():
                    continue
                try:
                    pointer = pointer_file.read_text(encoding="utf-8").strip()
                except OSError:
                    continue
                if pointer.startswith("gitdir:"):
                    external = (
                        Path(dirpath) / pointer[len("gitdir:"):].strip()
                    ).resolve()
                    if not external.is_relative_to(staging_root):
                        raise WorkspaceArtifactProjectionError(
                            "imported nested git directory escapes the workspace",
                            code="WORKSPACE_AUTHORITY_MISMATCH",
                        )
        self._remove_credential_copies(staging)
        self._remove_stale_session_authority(staging)

    def _sanitize_git_dir(self, git_dir: Path, staging: Path) -> None:
        staging_root = staging.resolve()
        # Hooks execute on the next Git command: remove them, keep history.
        hooks = git_dir / "hooks"
        if hooks.is_symlink() or (hooks.exists() and not hooks.is_dir()):
            raise WorkspaceArtifactProjectionError(
                "imported git hooks path is unsafe",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        if hooks.is_dir():
            shutil.rmtree(hooks)
            hooks.mkdir(mode=0o755)
        # Worktree administration points at external checkouts: not restored.
        worktrees = git_dir / "worktrees"
        if worktrees.is_symlink():
            raise WorkspaceArtifactProjectionError(
                "imported git worktree path is unsafe",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        if worktrees.is_dir():
            shutil.rmtree(worktrees)
        # Alternates hand object resolution to an external directory.
        alternates = git_dir / "objects" / "info" / "alternates"
        if alternates.is_symlink() or alternates.is_file():
            try:
                alternates.unlink()
            except OSError as exc:
                raise WorkspaceArtifactProjectionError(
                    "imported object alternates could not be neutralized",
                    code="WORKSPACE_AUTHORITY_MISMATCH",
                ) from exc
        # Commondir/gitdir pointers escaping staging fail in the caller; a
        # contained commondir pointer is data and stays.
        for pointer_name in ("commondir", "gitdir"):
            pointer = git_dir / pointer_name
            if pointer.is_file() and not pointer.is_symlink():
                try:
                    content = pointer.read_text(encoding="utf-8").strip()
                except OSError:
                    continue
                if pointer_name == "commondir":
                    external = (git_dir / content).resolve()
                    if not external.is_relative_to(staging_root):
                        raise WorkspaceArtifactProjectionError(
                            "imported git commondir escapes the workspace",
                            code="WORKSPACE_AUTHORITY_MISMATCH",
                        )
        self._sanitize_git_config(git_dir / "config")

    def _sanitize_git_config(self, config_path: Path) -> None:
        if config_path.is_symlink() or not config_path.is_file():
            return
        try:
            text = config_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise WorkspaceArtifactProjectionError(
                "imported git configuration is unreadable",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(text)
        except configparser.Error as exc:
            raise WorkspaceArtifactProjectionError(
                "imported git configuration is malformed",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            ) from exc
        for section in list(parser.sections()):
            lowered_section = section.lower()
            base = lowered_section.split('"')[0].strip()
            if base in _GIT_DROP_SECTIONS:
                del parser[section]
                continue
            for key in list(parser[section].keys()):
                lowered_key = key.lower()
                if (base, lowered_key) in _GIT_DROP_KEYS or (
                    base,
                    None,
                ) in _GIT_DROP_KEYS:
                    del parser[section][key]
                elif base == "submodule" and lowered_key == "update":
                    if str(parser[section][key]).strip().startswith("!"):
                        del parser[section][key]
                elif base == "core" and lowered_key == "repositoryformatversion":
                    continue
        with io.StringIO() as stream:
            parser.write(stream)
            sanitized = stream.getvalue()
        try:
            config_path.write_text(sanitized, encoding="utf-8")
        except OSError as exc:
            raise WorkspaceArtifactProjectionError(
                "imported git configuration could not be neutralized",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            ) from exc

    def _remove_credential_copies(self, staging: Path) -> None:
        for name in sorted(_CREDENTIAL_DIR_NAMES):
            target = staging / name
            if target.is_symlink() or (target.exists() and not target.is_dir()):
                try:
                    if target.is_symlink() or target.is_file():
                        target.unlink()
                    else:
                        shutil.rmtree(target)
                except OSError as exc:
                    raise WorkspaceArtifactProjectionError(
                        "imported credential path could not be neutralized",
                        code="WORKSPACE_AUTHORITY_MISMATCH",
                    ) from exc
            elif target.is_dir():
                shutil.rmtree(target, ignore_errors=False)
        for name in sorted(_CREDENTIAL_FILE_NAMES):
            target = staging / name
            try:
                if target.is_symlink() or target.is_file():
                    target.unlink()
            except OSError as exc:
                raise WorkspaceArtifactProjectionError(
                    "imported credential file could not be neutralized",
                    code="WORKSPACE_AUTHORITY_MISMATCH",
                ) from exc

    def _remove_stale_session_authority(self, staging: Path) -> None:
        state_dir = staging / ".moonmind"
        if not state_dir.is_dir() or state_dir.is_symlink():
            return
        for entry in state_dir.iterdir():
            if entry.name.lower() in _SESSION_AUTHORITY_NAMES:
                try:
                    if entry.is_symlink() or entry.is_file():
                        entry.unlink()
                    elif entry.is_dir():
                        shutil.rmtree(entry)
                except OSError as exc:
                    raise WorkspaceArtifactProjectionError(
                        "imported session authority could not be neutralized",
                        code="WORKSPACE_AUTHORITY_MISMATCH",
                    ) from exc

    # -- additive input bundles --------------------------------------------

    async def _materialize_bundle(
        self,
        workspace: Path,
        *,
        refs: tuple[str, ...],
        subdir: str,
        principal: str,
        noun: str,
        required_workflow_id: str | None = None,
        runtime_uid: int,
        runtime_gid: int,
        target_workflow_id: str | None = None,
        grant: Any | None = None,
    ) -> list[dict[str, Any]]:
        cleaned = tuple(
            dict.fromkeys(str(ref).strip() for ref in refs if str(ref).strip())
        )
        if not cleaned:
            return []
        if len(cleaned) > MAX_INPUT_REFS:
            raise WorkspaceArtifactProjectionError(
                f"too many {noun} for the authorized workspace",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        service = self._require_service(noun)
        root = (workspace / ".moonmind" / subdir).resolve()
        if not root.is_relative_to(workspace.resolve()):
            raise WorkspaceArtifactProjectionError(
                f"{noun} materialization escaped the authorized workspace",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        root.mkdir(parents=True, exist_ok=True)
        evidence: list[dict[str, Any]] = []
        total_bytes = 0
        for ref in cleaned:
            artifact_id = self._artifact_id(ref, noun=noun)
            budget = min(MAX_INPUT_BYTES, MAX_TOTAL_BYTES - total_bytes)
            if budget <= 0:
                raise WorkspaceArtifactProjectionError(
                    f"{noun} exceed the cumulative authorized workspace bound",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                )
            admission = await self._admit_source(
                service,
                artifact_id=artifact_id,
                expected_digest=None,
                budget_bytes=budget,
                principal=principal,
                noun=noun,
                target_workflow_id=required_workflow_id or target_workflow_id,
                grant=grant,
                allow_restricted=False,
            )
            target = root / hashlib.sha256(ref.encode("utf-8")).hexdigest()[:24]
            if target.is_symlink():
                raise WorkspaceArtifactProjectionError(
                    f"{noun} target must not be a symlink",
                    code="WORKSPACE_AUTHORITY_MISMATCH",
                )
            written = await self._write_payload(
                service,
                admission=admission,
                target=target,
                budget_bytes=budget,
                principal=principal,
                noun=noun,
                allow_restricted=False,
            )
            self._make_runtime_readable(
                target,
                runtime_uid=runtime_uid,
                runtime_gid=runtime_gid,
                noun=noun,
            )
            total_bytes += written
            evidence.append(
                {"ref": ref, "bytes": written, "sourceDigest": admission.digest}
            )
        return evidence

    @staticmethod
    def _make_runtime_readable(
        target: Path,
        *,
        runtime_uid: int,
        runtime_gid: int,
        noun: str,
    ) -> None:
        """Give only the selected runtime identity read access to an input."""

        if runtime_uid < 0 or runtime_gid < 0:
            target.unlink(missing_ok=True)
            raise WorkspaceArtifactProjectionError(
                f"{noun} runtime identity is invalid",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        try:
            current = target.stat(follow_symlinks=False)
            if current.st_uid != runtime_uid or current.st_gid != runtime_gid:
                os.chown(
                    target,
                    runtime_uid,
                    runtime_gid,
                    follow_symlinks=False,
                )
            os.chmod(target, 0o400, follow_symlinks=False)
        except OSError as exc:
            target.unlink(missing_ok=True)
            raise WorkspaceArtifactProjectionError(
                f"{noun} could not be assigned to the selected runtime identity",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            ) from exc

    @staticmethod
    def _artifact_id(ref: str, *, noun: str) -> str:
        value = str(ref or "").strip()
        if not value.startswith("artifact://") or not value[len("artifact://"):]:
            raise WorkspaceArtifactProjectionError(
                f"{noun} must be durable artifact refs, not local paths",
                code="WORKSPACE_LOCATOR_UNSUPPORTED",
            )
        return value[len("artifact://"):]

    @staticmethod
    async def _validate_metadata(
        service: Any,
        *,
        artifact_id: str,
        budget_bytes: int,
        principal: str,
        required_workflow_id: str | None = None,
    ) -> None:
        """Legacy metadata probe, now fail-closed through source admission."""

        await WorkspaceArtifactProjector._admit_source(
            service,
            artifact_id=artifact_id,
            expected_digest=None,
            budget_bytes=budget_bytes,
            principal=principal,
            noun="workspace input",
            target_workflow_id=required_workflow_id,
            grant=None,
            allow_restricted=False,
        )

    @staticmethod
    async def _write_payload(
        service: Any,
        *,
        admission: SourceAdmission,
        target: Path,
        budget_bytes: int,
        principal: str,
        noun: str = "restore input",
        allow_restricted: bool = False,
    ) -> int:
        """Stream exact source bytes and verify against the admitted digest."""

        read_chunks = getattr(service, "read_chunks", None)
        hasher = hashlib.sha256()
        if read_chunks is not None:
            result = await read_chunks(
                artifact_id=admission.artifact_id,
                principal=principal,
                allow_restricted_raw=allow_restricted,
                chunk_size=STREAM_CHUNK_BYTES,
            )
            _artifact, chunks = result
            written = 0
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                for chunk in chunks:
                    written += len(chunk)
                    if written > budget_bytes:
                        stream.close()
                        target.unlink(missing_ok=True)
                        raise WorkspaceArtifactProjectionError(
                            f"{noun} exceed the authorized workspace bound",
                            code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                        )
                    hasher.update(chunk)
                    stream.write(chunk)
            actual = f"sha256:{hasher.hexdigest()}"
            if actual != admission.digest:
                target.unlink(missing_ok=True)
                raise WorkspaceArtifactProjectionError(
                    f"{noun} bytes do not match the admitted source digest",
                    code="WORKSPACE_DIGEST_MISMATCH",
                )
            return written
        _artifact, payload = await service.read(
            artifact_id=admission.artifact_id,
            principal=principal,
            allow_restricted_raw=allow_restricted,
        )
        if len(payload) > budget_bytes:
            raise WorkspaceArtifactProjectionError(
                f"{noun} exceed the authorized workspace bound",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        hasher.update(payload)
        actual = f"sha256:{hasher.hexdigest()}"
        if actual != admission.digest:
            raise WorkspaceArtifactProjectionError(
                f"{noun} bytes do not match the admitted source digest",
                code="WORKSPACE_DIGEST_MISMATCH",
            )
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        return len(payload)

    @staticmethod
    def _exclude_attachments_from_git(workspace: Path) -> None:
        info = workspace / ".git" / "info"
        if not info.is_dir():
            return
        exclude = info / "exclude"
        rule = "/.moonmind/attachments/"
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if rule not in existing.splitlines():
            with exclude.open("a", encoding="utf-8") as stream:
                if existing and not existing.endswith("\n"):
                    stream.write("\n")
                stream.write(f"{rule}\n")


def _contract_supported_for_kind(contract: str | None, kind: str | None) -> bool:
    from moonmind.omnigent.workspace_sources import (
        ARTIFACT_IMPORT_CONTRACT_V1,
        SUPPORTED_RESTORE_CONTRACTS,
    )

    if kind == "artifact":
        return contract == ARTIFACT_IMPORT_CONTRACT_V1
    return contract in SUPPORTED_RESTORE_CONTRACTS


def _supported_contracts() -> frozenset[str]:
    from moonmind.omnigent.workspace_sources import SUPPORTED_RESTORE_CONTRACTS

    return SUPPORTED_RESTORE_CONTRACTS


__all__ = [
    "SourceAdmission",
    "WorkspaceArtifactProjectionError",
    "WorkspaceArtifactProjector",
]
