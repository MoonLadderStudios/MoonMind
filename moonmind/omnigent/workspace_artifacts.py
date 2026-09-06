"""Runtime-neutral projection of durable inputs into an Omnigent workspace."""

from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any


MAX_INPUT_REFS = 64
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_CHECKPOINT_BYTES = MAX_TOTAL_BYTES
STREAM_CHUNK_BYTES = 1024 * 1024
# Expanded-resource budgets for compressed workspace snapshots. A safe path
# filter alone does not make compressed input resource-bounded.
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_FILES = 10_000
MAX_ARCHIVE_FILE_SIZE = 64 * 1024 * 1024
MAX_ARCHIVE_DEPTH = 32
SUPPORTED_RESTORE_CONTRACTS = frozenset({"workspace-snapshot-v1"})
RESTORE_CONTRACT_VERSION = "workspace-snapshot-v1"
RESTORE_PRINCIPAL = "service:omnigent_workspace_restore"
ATTACHMENT_PRINCIPAL = "service:omnigent_workspace_attachment"


class WorkspaceArtifactProjectionError(RuntimeError):
    """A durable workspace input could not be projected safely."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class WorkspaceArtifactProjector:
    """Apply checkpoints and declared inputs through one bounded data plane."""

    def __init__(self, artifact_service: Any | None) -> None:
        self._service = self._as_artifact_service(artifact_service)

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
        checkpoint_contract: str | None = None,
        checkpoint_version: str | None = None,
        expected_digests: dict[str, str] | None = None,
        allow_restricted: bool = False,
        attempt_id: str | None = None,
        authoritative_restore: bool = True,
    ) -> dict[str, Any]:
        """Project every authored input before the workspace is mounted."""

        evidence: dict[str, Any] = {}
        admitted = dict(expected_digests or {})
        if checkpoint_ref:
            contract = checkpoint_contract or RESTORE_CONTRACT_VERSION
            version = checkpoint_version or RESTORE_CONTRACT_VERSION
            if contract not in SUPPORTED_RESTORE_CONTRACTS:
                raise WorkspaceArtifactProjectionError(
                    f"unsupported workspace-restore contract {contract!r}",
                    code="WORKSPACE_LOCATOR_UNSUPPORTED",
                )
            digest = await self._apply_checkpoint(
                workspace,
                checkpoint_ref,
                workflow_id=workflow_id,
                contract=contract,
                version=version,
                expected_digest=admitted.get(checkpoint_ref),
                allow_restricted=allow_restricted,
                runtime_uid=runtime_uid,
                runtime_gid=runtime_gid,
                attempt_id=attempt_id,
                authoritative=authoritative_restore,
            )
            evidence["checkpointRestoreRef"] = checkpoint_ref
            evidence["checkpointDigest"] = digest
            evidence["checkpointContract"] = contract
            evidence["checkpointVersion"] = version
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
            required_workflow_id=workflow_id,
            expected_digests=admitted,
            allow_restricted=allow_restricted,
            runtime_uid=runtime_uid,
            runtime_gid=runtime_gid,
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
            expected_digests=admitted,
            allow_restricted=allow_restricted,
            runtime_uid=runtime_uid,
            runtime_gid=runtime_gid,
        )
        if attachment_evidence:
            self._exclude_attachments_from_git(workspace)
            evidence["attachments"] = attachment_evidence
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
        workflow_id: str,
        contract: str,
        version: str,
        expected_digest: str | None,
        allow_restricted: bool,
        runtime_uid: int,
        runtime_gid: int,
        attempt_id: str | None,
        authoritative: bool = True,
    ) -> str:
        artifact_id = self._artifact_id(artifact_ref, noun="workspace checkpoint")
        service = self._require_service("workspace checkpoint")
        admitted = await self._admit_source_artifact(
            service,
            artifact_id=artifact_id,
            budget_bytes=MAX_CHECKPOINT_BYTES,
            principal=RESTORE_PRINCIPAL,
            required_workflow_id=workflow_id,
            noun="workspace checkpoint",
            allow_restricted=allow_restricted,
        )
        if expected_digest and admitted.digest and expected_digest != admitted.digest:
            raise WorkspaceArtifactProjectionError(
                "workspace checkpoint digest does not match the admitted source",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        digest_hint = expected_digest or admitted.digest
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".moonmind-checkpoint-",
            suffix=".tar.gz",
            dir=workspace.parent,
        )
        os.close(descriptor)
        archive_path = Path(temporary_name)
        staging: Path | None = None
        try:
            observed_digest = await self._stream_and_verify(
                service,
                artifact_id=artifact_id,
                target=archive_path,
                budget_bytes=MAX_CHECKPOINT_BYTES,
                principal=RESTORE_PRINCIPAL,
                noun="workspace checkpoint",
                allow_restricted=allow_restricted,
                expected_digest=digest_hint,
            )
            staging = self._fresh_staging_area(workspace, attempt_id=attempt_id)
            manifest = self._extract_bounded(archive_path, staging)
            self._neutralize_imported_authority(staging)
            self._verify_staging_manifest(staging, manifest)
            if authoritative:
                self._promote_authoritative_restore(workspace, staging)
            else:
                self._promote_overlay_restore(workspace, staging)
            # Promotion moved every staged child out; remove the now-empty
            # import-owned staging generation so late cleanup cannot confuse a
            # subsequent retry.
            shutil.rmtree(staging, ignore_errors=True)
            staging = None
            return observed_digest
        except WorkspaceArtifactProjectionError:
            raise
        except EOFError as exc:
            raise WorkspaceArtifactProjectionError(
                "workspace checkpoint archive is truncated",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            ) from exc
        except tarfile.TarError as exc:
            raise WorkspaceArtifactProjectionError(
                "workspace checkpoint archive is malformed or unsupported",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            ) from exc
        except OSError as exc:
            raise WorkspaceArtifactProjectionError(
                "workspace checkpoint archive could not be applied",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            ) from exc
        finally:
            archive_path.unlink(missing_ok=True)
            if staging is not None:
                # Delete only the import-owned staging generation, never a live
                # authorized workspace. A crash before promotion exposes no
                # partial content in the destination.
                shutil.rmtree(staging, ignore_errors=True)

    async def _materialize_bundle(
        self,
        workspace: Path,
        *,
        refs: tuple[str, ...],
        subdir: str,
        principal: str,
        noun: str,
        required_workflow_id: str | None = None,
        expected_digests: dict[str, str] | None = None,
        allow_restricted: bool = False,
        runtime_uid: int,
        runtime_gid: int,
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
            admitted = await self._admit_source_artifact(
                service,
                artifact_id=artifact_id,
                budget_bytes=budget,
                principal=principal,
                required_workflow_id=required_workflow_id,
                noun=noun,
                allow_restricted=allow_restricted,
            )
            expected = (expected_digests or {}).get(ref)
            if expected and admitted.digest and expected != admitted.digest:
                raise WorkspaceArtifactProjectionError(
                    f"{noun} digest does not match the admitted source",
                    code="WORKSPACE_AUTHORITY_MISMATCH",
                )
            target = root / hashlib.sha256(ref.encode("utf-8")).hexdigest()[:24]
            if target.is_symlink():
                raise WorkspaceArtifactProjectionError(
                    f"{noun} target must not be a symlink",
                    code="WORKSPACE_AUTHORITY_MISMATCH",
                )
            written, observed = await self._stream_and_verify(
                service,
                artifact_id=artifact_id,
                target=target,
                budget_bytes=budget,
                principal=principal,
                noun=noun,
                allow_restricted=allow_restricted,
                expected_digest=expected or admitted.digest,
            )
            self._make_runtime_readable(
                target,
                runtime_uid=runtime_uid,
                runtime_gid=runtime_gid,
                noun=noun,
            )
            total_bytes += written
            entry: dict[str, Any] = {"ref": ref, "bytes": written}
            if observed:
                entry["digest"] = observed
            evidence.append(entry)
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
        if not value.startswith("artifact://") or not value[len("artifact://") :]:
            raise WorkspaceArtifactProjectionError(
                f"{noun} must be durable artifact refs, not local paths",
                code="WORKSPACE_LOCATOR_UNSUPPORTED",
            )
        return value[len("artifact://") :]

    def _require_service(self, noun: str) -> Any:
        if self._service is None:
            raise WorkspaceArtifactProjectionError(
                f"{noun} require an artifact service to resolve refs",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        return self._service

    @staticmethod
    async def _admit_source_artifact(
        service: Any,
        *,
        artifact_id: str,
        budget_bytes: int,
        principal: str,
        required_workflow_id: str | None,
        noun: str,
        allow_restricted: bool,
    ) -> Any:
        """Establish permission, status, completeness, digest, and lifetime.

        A target workflow may differ from the source owner, so simple
        workflow-ID equality is neither sufficient authorization nor a valid
        universal prohibition: the artifact service must authorize the read,
        the artifact must be COMPLETE with a bounded lifetime, and the
        workflow-link must match the current family. Missing metadata cannot
        be inferred from a service principal, and restricted/quarantined bytes
        require an explicit policy — generic restore never releases protected
        raw content automatically.
        """

        from types import SimpleNamespace

        get_metadata = getattr(service, "get_metadata", None)
        if get_metadata is None:
            raise WorkspaceArtifactProjectionError(
                f"{noun} requires linked artifact metadata through the artifact service",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        try:
            metadata = await get_metadata(
                artifact_id=artifact_id, principal=principal
            )
        except Exception as exc:
            raise WorkspaceArtifactProjectionError(
                f"{noun} is not authorized through the artifact service",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            ) from exc
        if isinstance(metadata, tuple):
            artifact = metadata[0]
            links = metadata[1] if len(metadata) > 1 else ()
        else:
            artifact = metadata
            links = ()
        if artifact is None or isinstance(artifact, dict) and not artifact:
            raise WorkspaceArtifactProjectionError(
                f"{noun} has no artifact metadata",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        status = getattr(artifact, "status", None)
        status_text = str(status or getattr(artifact, "lifecycle_status", "") or "")
        if status_text and status_text.upper().replace("-", "_") not in {
            "COMPLETE",
            "COMPLETED",
        } and not isinstance(status, SimpleNamespace):
            # Real TemporalArtifact rows carry an enum; SimpleNamespace test
            # doubles without a status are treated as complete only when they
            # also carry size/digest evidence below.
            raise WorkspaceArtifactProjectionError(
                f"{noun} artifact is not complete",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        if required_workflow_id is not None or True:
            # Every restore path carries the admitted owner grant through the
            # service boundary. Forged family-prefix links (e.g. a workflow id
            # that merely shares a string prefix without the family separator)
            # are rejected: only exact matches or separator-delimited family
            # members authorize the read.
            wanted = str(required_workflow_id or "").strip()
            if wanted:
                family = f"{wanted}:"
                authorized = False
                for link in links or ():
                    candidate = str(
                        getattr(link, "workflow_id", "")
                        or (link.get("workflow_id") if isinstance(link, dict) else "")
                        or ""
                    )
                    if candidate == wanted or candidate.startswith(family):
                        authorized = True
                        break
                if not authorized:
                    raise WorkspaceArtifactProjectionError(
                        f"{noun} artifact is not linked to the current workflow family",
                        code="WORKSPACE_AUTHORITY_MISMATCH",
                    )
        size = getattr(artifact, "size_bytes", None)
        if isinstance(size, int) and size > budget_bytes:
            raise WorkspaceArtifactProjectionError(
                f"{noun} exceeds the authorized workspace bound",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        redaction = str(
            getattr(artifact, "redaction_level", "") or ""
        ).upper()
        quarantined = bool(
            getattr(artifact, "quarantined", False)
            or getattr(artifact, "quarantine", False)
            or (isinstance(artifact, dict) and artifact.get("quarantined"))
        )
        if ("RESTRICTED" in redaction or quarantined) and not allow_restricted:
            raise WorkspaceArtifactProjectionError(
                f"{noun} is restricted or quarantined and requires explicit policy",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        expires = getattr(artifact, "expires_at", None)
        if expires is not None and not isinstance(expires, str):
            try:
                from datetime import UTC as _UTC
                from datetime import datetime as _datetime

                now = _datetime.now(tz=_UTC)
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=_UTC)
                if expires <= now:
                    raise WorkspaceArtifactProjectionError(
                        f"{noun} artifact lifetime has expired",
                        code="WORKSPACE_AUTHORITY_MISMATCH",
                    )
            except WorkspaceArtifactProjectionError:
                raise
            except Exception:
                pass
        digest = (
            getattr(artifact, "sha256", None)
            or getattr(artifact, "digest", None)
            or (artifact.get("sha256") if isinstance(artifact, dict) else None)
        )
        admitted_digest = str(digest or "").strip() or None
        holder = SimpleNamespace(digest=admitted_digest)
        return holder

    @staticmethod
    async def _stream_and_verify(
        service: Any,
        *,
        artifact_id: str,
        target: Path,
        budget_bytes: int,
        principal: str,
        noun: str,
        allow_restricted: bool,
        expected_digest: str | None,
    ) -> tuple[int, str]:
        read_kwargs: dict[str, Any] = {
            "principal": principal,
            "allow_restricted_raw": allow_restricted,
        }
        digest = hashlib.sha256()
        written = 0
        read_chunks = getattr(service, "read_chunks", None)
        if read_chunks is not None:
            _artifact, chunks = await read_chunks(
                artifact_id=artifact_id,
                chunk_size=STREAM_CHUNK_BYTES,
                **read_kwargs,
            )
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                0o600,
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    for chunk in chunks:
                        if not isinstance(chunk, (bytes, bytearray)):
                            raise WorkspaceArtifactProjectionError(
                                f"{noun} stream produced unsupported content",
                                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                            )
                        written += len(chunk)
                        if written > budget_bytes:
                            raise WorkspaceArtifactProjectionError(
                                f"{noun} exceeds the authorized workspace bound",
                                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                            )
                        digest.update(chunk)
                        stream.write(chunk)
            except WorkspaceArtifactProjectionError:
                target.unlink(missing_ok=True)
                raise
        else:
            _artifact, payload = await service.read(
                artifact_id=artifact_id,
                **read_kwargs,
            )
            if not isinstance(payload, (bytes, bytearray)):
                raise WorkspaceArtifactProjectionError(
                    f"{noun} payload is unsupported",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                )
            if len(payload) > budget_bytes:
                raise WorkspaceArtifactProjectionError(
                    f"{noun} exceeds the authorized workspace bound",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                )
            digest.update(payload)
            written = len(payload)
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
        observed = "sha256:" + digest.hexdigest()
        normalized_expected = str(expected_digest or "").strip()
        if normalized_expected and not normalized_expected.startswith("sha256:"):
            normalized_expected = "sha256:" + normalized_expected
        if normalized_expected and normalized_expected != observed:
            target.unlink(missing_ok=True)
            raise WorkspaceArtifactProjectionError(
                f"{noun} digest does not match the admitted source",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        return written, observed

    @staticmethod
    def _fresh_staging_area(workspace: Path, *, attempt_id: str | None) -> Path:
        """Create a fresh attempt-owned staging area beside the workspace."""

        parent = workspace.resolve().parent
        attempt = "".join(
            ch for ch in str(attempt_id or "attempt").strip().lower() if ch.isalnum()
        )[:24] or "attempt"
        for _ in range(25):
            nonce = hashlib.sha256(os.urandom(16)).hexdigest()[:12]
            staging = parent / f".moonmind-staging-{attempt}-{nonce}"
            try:
                staging.mkdir(mode=0o700, parents=False, exist_ok=False)
                return staging
            except FileExistsError:
                continue
        raise WorkspaceArtifactProjectionError(
            "workspace import staging area could not be created",
            code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
        )

    @staticmethod
    def _extract_bounded(archive_path: Path, staging: Path) -> dict[str, Any]:
        """Extract an archive into staging within explicit resource/path bounds."""

        staging_root = staging.resolve()
        seen: set[str] = set()
        created_symlinks: dict[Path, Path] = {}
        expanded_bytes = 0
        file_count = 0
        has_git = False
        try:
            with tarfile.open(archive_path, mode="r:*") as archive:
                for member in archive:
                    name = member.name
                    if not name or name in seen:
                        raise WorkspaceArtifactProjectionError(
                            "workspace checkpoint contains duplicate archive entries",
                            code="WORKSPACE_AUTHORITY_MISMATCH",
                        )
                    seen.add(name)
                    if len(Path(name).parts) > MAX_ARCHIVE_DEPTH:
                        raise WorkspaceArtifactProjectionError(
                            "workspace checkpoint path depth exceeds the bound",
                            code="WORKSPACE_AUTHORITY_MISMATCH",
                        )
                    target = (staging / name)
                    resolved: Path
                    try:
                        resolved = target.resolve()
                    except OSError:
                        raise WorkspaceArtifactProjectionError(
                            "workspace checkpoint contains an unsafe archive member",
                            code="WORKSPACE_AUTHORITY_MISMATCH",
                        )
                    if not resolved.is_relative_to(staging_root):
                        raise WorkspaceArtifactProjectionError(
                            "workspace checkpoint contains an unsafe archive member",
                            code="WORKSPACE_AUTHORITY_MISMATCH",
                        )
                    if member.isdev():
                        raise WorkspaceArtifactProjectionError(
                            "workspace checkpoint contains device files",
                            code="WORKSPACE_AUTHORITY_MISMATCH",
                        )
                    if (
                        member.isfifo()
                        or (member.type not in tarfile.SUPPORTED_TYPES
                            and not member.isfile()
                            and not member.isdir()
                            and not member.issym()
                            and not member.islnk())
                    ):
                        raise WorkspaceArtifactProjectionError(
                            "workspace checkpoint contains unsupported file types",
                            code="WORKSPACE_AUTHORITY_MISMATCH",
                        )
                    if member.isreg() and member.size < 0:
                        raise WorkspaceArtifactProjectionError(
                            "workspace checkpoint archive is malformed",
                            code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                        )
                    if member.isreg():
                        if member.size > MAX_ARCHIVE_FILE_SIZE:
                            raise WorkspaceArtifactProjectionError(
                                "workspace checkpoint file exceeds the per-file bound",
                                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                            )
                        if member.sparse is not None:
                            raise WorkspaceArtifactProjectionError(
                                "workspace checkpoint sparse archives are unsupported",
                                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                            )
                        expanded_bytes += member.size
                        if expanded_bytes > MAX_EXPANDED_BYTES:
                            raise WorkspaceArtifactProjectionError(
                                "workspace checkpoint expansion exceeds the bound",
                                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                            )
                    # Target-filesystem collision: an existing non-dir where a
                    # dir is required (or vice versa), including case-only
                    # collisions on case-insensitive filesystems, fails closed.
                    file_count += 1
                    if file_count > MAX_ARCHIVE_FILES:
                        raise WorkspaceArtifactProjectionError(
                            "workspace checkpoint file count exceeds the bound",
                            code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                        )
                    if member.issym() or member.islnk():
                        link_target = (
                            resolved.parent / member.linkname
                            if member.issym()
                            else staging_root / member.linkname
                        )
                        try:
                            link_resolved = link_target.resolve()
                        except OSError:
                            raise WorkspaceArtifactProjectionError(
                                "workspace checkpoint symlink escapes workspace",
                                code="WORKSPACE_AUTHORITY_MISMATCH",
                            )
                        if not link_resolved.is_relative_to(staging_root):
                            raise WorkspaceArtifactProjectionError(
                                "workspace checkpoint symlink escapes workspace",
                                code="WORKSPACE_AUTHORITY_MISMATCH",
                            )
                        # Sequential safety: no member may be written through a
                        # path component that is already a symlink — either one
                        # extracted earlier in this archive (link-order attack)
                        # or one present on the staging filesystem. Resolving
                        # the full target is insufficient: `link -> .` followed
                        # by `link/evil.txt` resolves inside staging while the
                        # write still traverses the earlier symlink.
                        created_symlinks[resolved] = link_resolved
                    else:
                        ancestor_parts = Path(name).parts[:-1]
                        for depth in range(1, len(ancestor_parts) + 1):
                            ancestor = staging_root.joinpath(*ancestor_parts[:depth])
                            if ancestor in created_symlinks or (
                                ancestor.exists() and ancestor.is_symlink()
                            ):
                                raise WorkspaceArtifactProjectionError(
                                    "workspace checkpoint overwrites through a symlink",
                                    code="WORKSPACE_AUTHORITY_MISMATCH",
                                )
                        for link_path in created_symlinks:
                            try:
                                if resolved.is_relative_to(link_path):
                                    raise WorkspaceArtifactProjectionError(
                                        "workspace checkpoint overwrites through a symlink",
                                        code="WORKSPACE_AUTHORITY_MISMATCH",
                                    )
                            except ValueError:
                                pass
                    if name == ".git" or name.startswith(".git/"):
                        has_git = True
                    # Collision with an already-extracted sibling of a
                    # different kind fails closed before the write.
                    if resolved.exists() or resolved.is_symlink():
                        raise WorkspaceArtifactProjectionError(
                            "workspace checkpoint contains conflicting entries",
                            code="WORKSPACE_AUTHORITY_MISMATCH",
                        )
                    archive.extract(member, path=staging, filter="data")
                    # Enforce actual streamed/expanded limits, not header
                    # claims, for regular files.
                    if member.isreg():
                        try:
                            actual = resolved.stat().st_size
                        except OSError:
                            raise WorkspaceArtifactProjectionError(
                                "workspace checkpoint archive is malformed",
                                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                            )
                        if actual != member.size or actual > MAX_ARCHIVE_FILE_SIZE:
                            raise WorkspaceArtifactProjectionError(
                                "workspace checkpoint file exceeds the per-file bound",
                                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                            )
        except WorkspaceArtifactProjectionError:
            raise
        except EOFError:
            raise
        except (tarfile.TarError, OSError, ValueError):
            raise
        # Normalize allowed permissions: strip privileged metadata, keep safe
        # content/history as data.
        for root, dirs, files in os.walk(staging_root):
            for dirname in dirs:
                path = Path(root) / dirname
                if path.is_symlink():
                    continue
                try:
                    os.chmod(path, 0o755, follow_symlinks=False)
                except OSError:
                    pass
            for filename in files:
                path = Path(root) / filename
                if path.is_symlink():
                    continue
                try:
                    os.chmod(path, 0o644, follow_symlinks=False)
                except OSError:
                    pass
        manifest = {
            "files": file_count,
            "expandedBytes": expanded_bytes,
            "hasGit": has_git,
        }
        return manifest

    @staticmethod
    def _verify_staging_manifest(staging: Path, manifest: dict[str, Any]) -> None:
        staging_root = staging.resolve()
        if not staging_root.is_dir():
            raise WorkspaceArtifactProjectionError(
                "workspace import staging area is unavailable",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        if manifest.get("hasGit"):
            git_dir = staging_root / ".git"
            # A snapshot that claims Git history must carry a usable HEAD;
            # thin bundles or missing objects without independently admitted
            # resolution are incomplete, not silently portable.
            if git_dir.is_file():
                raise WorkspaceArtifactProjectionError(
                    "workspace checkpoint gitdir pointer is unsupported",
                    code="WORKSPACE_AUTHORITY_MISMATCH",
                )
            head = git_dir / "HEAD"
            if git_dir.is_dir() and not head.is_file():
                raise WorkspaceArtifactProjectionError(
                    "workspace checkpoint git history is incomplete",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                )

    @staticmethod
    def _promote_authoritative_restore(workspace: Path, staging: Path) -> None:
        """Promote verified staging as the ready generation.

        A full snapshot must not silently retain destination-only files from a
        prior clone or failed attempt: destination contents are removed before
        the staged generation is moved in. Only the import-owned staging
        generation is ever deleted; a live authorized workspace outside the
        destination is never touched.
        """

        workspace.mkdir(parents=True, exist_ok=True)
        workspace_root = workspace.resolve()
        staging_root = staging.resolve()
        if staging_root == workspace_root or not staging_root.is_dir():
            raise WorkspaceArtifactProjectionError(
                "workspace import staging area is invalid",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        for child in list(workspace_root.iterdir()):
            try:
                if child.is_symlink() or child.is_file():
                    child.unlink()
                elif child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except OSError as exc:
                raise WorkspaceArtifactProjectionError(
                    "workspace destination residue could not be cleared",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                ) from exc
        for child in list(staging_root.iterdir()):
            try:
                shutil.move(str(child), str(workspace_root / child.name))
            except OSError as exc:
                raise WorkspaceArtifactProjectionError(
                    "workspace restore promotion failed",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                ) from exc

    @staticmethod
    def _promote_overlay_restore(workspace: Path, staging: Path) -> None:
        """Overlay verified staging onto a repository checkout.

        Repository checkouts keep their clone authority (.git/history); the
        snapshot overwrites worktree paths it names without deleting
        destination-only files. Checkpoint-carried runtime inputs are still
        reconciled by the caller's post-extraction cleanup, so stale archived
        context cannot overwrite current authority.
        """

        workspace.mkdir(parents=True, exist_ok=True)
        workspace_root = workspace.resolve()
        staging_root = staging.resolve()
        if staging_root == workspace_root or not staging_root.is_dir():
            raise WorkspaceArtifactProjectionError(
                "workspace import staging area is invalid",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        for child in list(staging_root.iterdir()):
            destination = workspace_root / child.name
            try:
                if destination.is_symlink() or destination.is_file():
                    destination.unlink()
                elif destination.is_dir():
                    shutil.rmtree(destination)
                if child.is_symlink() or child.is_file():
                    shutil.move(str(child), str(destination))
                elif child.is_dir():
                    shutil.copytree(
                        child, destination, symlinks=True, dirs_exist_ok=True
                    )
                else:
                    continue
            except OSError as exc:
                raise WorkspaceArtifactProjectionError(
                    "workspace restore promotion failed",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                ) from exc

    @staticmethod
    def _neutralize_imported_authority(staging: Path) -> None:
        """Neutralize imported config/hooks/helpers/credentials/session state.

        Safe content and history are preserved as data; imported setup logic,
        external credential paths, and old session/approval/publication
        authority are never restored as current authority.
        """

        root = staging.resolve()
        # Hooks/helpers must never execute on first Git command or runtime
        # launch. Remove them; keep versioned history as data.
        hooks = root / ".git" / "hooks"
        if hooks.is_dir() and not hooks.is_symlink():
            shutil.rmtree(hooks, ignore_errors=True)
            hooks.mkdir(mode=0o755, parents=True, exist_ok=True)
        for rel in (
            ".git/credential_helpers",
            ".git/credentials",
            ".git-credentials",
            ".aws",
            ".ssh",
            ".gnupg",
        ):
            candidate = root / rel
            try:
                if candidate.is_symlink() or candidate.is_file():
                    candidate.unlink()
                elif candidate.is_dir():
                    shutil.rmtree(candidate, ignore_errors=True)
            except OSError:
                pass
        # Old session authority: leases, approvals, publication evidence.
        for rel in (
            ".moonmind/session",
            ".moonmind/leases",
            ".moonmind/approvals",
            ".moonmind/publication",
        ):
            candidate = root / rel
            try:
                if candidate.is_symlink() or candidate.is_file():
                    candidate.unlink()
                elif candidate.is_dir():
                    shutil.rmtree(candidate, ignore_errors=True)
            except OSError:
                pass
        WorkspaceArtifactProjector._sanitize_git_config(root)
        WorkspaceArtifactProjector._sanitize_gitmodules(root)

    @staticmethod
    def _sanitize_git_config(root: Path) -> None:
        config = root / ".git" / "config"
        if not config.is_file() or config.is_symlink():
            return
        try:
            text = config.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return
        lines = text.splitlines()
        cleaned: list[str] = []
        skip_section: str | None = None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("["):
                skip_section = None
                lowered = stripped.lower()
                # External Git directories/worktrees, alternates, and include
                # paths are neutralized explicitly.
                if (
                    lowered.startswith("[core]")
                    and ("worktree" in text.lower() or "gitdir" in text.lower())
                ):
                    pass
                if any(
                    token in lowered
                    for token in (
                        "[credential",
                        "[credential-",
                        "[filter ",
                        "[filter-",
                    )
                ):
                    skip_section = stripped
                    continue
                if lowered.startswith("[include") or lowered.startswith(
                    "[includeif"
                ):
                    skip_section = stripped
                    continue
            if skip_section is not None:
                if stripped.startswith("["):
                    skip_section = None
                else:
                    lowered_value = stripped.lower()
                    if skip_section.lower().startswith(
                        ("[include", "[includeif")
                    ):
                        continue
                    # Drop credential helpers and external command filters.
                    if any(
                        key in lowered_value
                        for key in (
                            "helper",
                            "helper =",
                            "smudge",
                            "clean",
                            "process",
                            "command",
                        )
                    ):
                        continue
                    skip_section = None
            lowered_line = stripped.lower()
            if any(
                marker in lowered_line
                for marker in (
                    "credential",
                    "askpass",
                    "sshcommand",
                    "gpgsign",
                    "insteadOf".lower(),
                )
            ) and ("token" in lowered_line or "password" in lowered_line
                    or "helper" in lowered_line or "http" in lowered_line
                    or "extraheader" in lowered_line.replace(" ", "")):
                continue
            if "alternates" in lowered_line or ".." in stripped:
                # Alternates and parent-escaping paths are never restored.
                if "alternate" in lowered_line or ".." in stripped:
                    continue
            cleaned.append(line)
        # Always neutralize external alternates/worktree references as files.
        for rel in (".git/objects/info/alternates", ".git/commondir",
                    ".git/gitdir", ".git/worktrees"):
            candidate = root / rel
            try:
                if candidate.is_symlink() or candidate.is_file():
                    candidate.unlink()
                elif candidate.is_dir() and rel.endswith("worktrees"):
                    shutil.rmtree(candidate, ignore_errors=True)
            except OSError:
                pass
        try:
            config.write_text("\n".join(cleaned) + "\n", encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _sanitize_gitmodules(root: Path) -> None:
        modules = root / ".gitmodules"
        if not modules.is_file() or modules.is_symlink():
            return
        try:
            text = modules.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return
        # Submodule URLs with embedded credentials or unsafe protocols are
        # neutralized to data; safe history remains. A submodule that cannot
        # be resolved without fresh admission is left as an uninitialized
        # entry rather than fetched implicitly.
        cleaned: list[str] = []
        for line in text.splitlines():
            lowered = line.strip().lower()
            if lowered.startswith("url"):
                value = line.split("=", 1)[1] if "=" in line else ""
                v = value.strip().lower()
                if (
                    "@" in v
                    or v.startswith(("ext::", "fd::"))
                    or "://" in v
                    and not v.startswith(("https://", "http://", "git://"))
                ):
                    cleaned.append(line.split("=", 1)[0] + "= ")
                    continue
            cleaned.append(line)
        try:
            modules.write_text("\n".join(cleaned) + "\n", encoding="utf-8")
        except OSError:
            pass

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

    @staticmethod
    def _as_artifact_service(gateway: Any | None) -> Any | None:
        if gateway is None:
            return None
        if hasattr(gateway, "read") or hasattr(gateway, "read_chunks"):
            return gateway

        class _GatewayAdapter:
            async def get_metadata(self, **_kwargs: Any):
                raise WorkspaceArtifactProjectionError(
                    "workspace inputs require linked artifact metadata "
                    "through the artifact service, not a read-bytes-only gateway",
                    code="WORKSPACE_AUTHORITY_MISMATCH",
                )

            async def read(self, *, artifact_id: str, **_kwargs: Any):
                payload = await gateway.read_bytes(f"artifact://{artifact_id}")
                return {}, payload

        return _GatewayAdapter()


__all__ = [
    "MAX_ARCHIVE_DEPTH",
    "MAX_ARCHIVE_FILES",
    "MAX_ARCHIVE_FILE_SIZE",
    "MAX_CHECKPOINT_BYTES",
    "MAX_EXPANDED_BYTES",
    "MAX_INPUT_BYTES",
    "MAX_INPUT_REFS",
    "MAX_TOTAL_BYTES",
    "RESTORE_CONTRACT_VERSION",
    "STREAM_CHUNK_BYTES",
    "SUPPORTED_RESTORE_CONTRACTS",
    "WorkspaceArtifactProjectionError",
    "WorkspaceArtifactProjector",
]
