"""Runtime-neutral projection of durable inputs into an Omnigent workspace.

Safe artifact / checkpoint / authorized existing-workspace sources for
MoonLadderStudios/MoonMind#4014 (CONTRACT-002, CONTRACT-011, INV-005, INV-006):

- Source-artifact permission, status, completeness, digest, and bounded
  lifetime are established through the actual artifact service. Missing
  metadata, forged workflow-family links, and restricted/quarantined bytes
  without an explicit policy fail closed; a service principal or a
  read-bytes-only adapter never implies owner/digest/quarantine permission.
- Exact source bytes are streamed and verified against the admitted digest
  under explicit compressed/download, expanded, per-file, file-count,
  path-depth, and processing-time budgets. Truncated, malformed,
  sparse/oversized, and unsupported archives are classified without a
  completed marker or an automatic alternate source.
- Archives extract into a fresh, attempt-owned staging area with sequential
  symlink/hardlink/overwrite validation, then the materialized manifest is
  verified before an authoritative restore promotes a ready generation.
  Only the import-owned staging generation is ever deleted, never a live
  authorized workspace.
- Imported Git configuration/hooks/helpers, credential directories, and old
  session authority are neutralized before any Git command or runtime launch,
  while safe content/history stay usable as data.
"""

from __future__ import annotations

import configparser
import hashlib
import os
import shutil
import stat
import tarfile
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from moonmind.omnigent.workspace_sources import (
    MAX_COMPRESSED_BYTES,
    MAX_EXPANDED_BYTES,
    MAX_FILE_BYTES,
    MAX_FILE_COUNT,
    MAX_PATH_DEPTH,
    MAX_PROCESSING_SECONDS,
    OVERLAY_ADDITIVE,
)


MAX_INPUT_REFS = 64
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_CHECKPOINT_BYTES = MAX_TOTAL_BYTES
STREAM_CHUNK_BYTES = 1024 * 1024
RESTORE_PRINCIPAL = "service:omnigent_workspace_restore"
ATTACHMENT_PRINCIPAL = "service:omnigent_workspace_attachment"

STAGING_PREFIX = ".moonmind-import-"

# Imported credential directories and session-authority files are never
# restored as current authority. Content/history stays; these go.
_NEUTRALIZED_DIR_NAMES: tuple[str, ...] = (
    ".aws",
    ".ssh",
    ".gnupg",
    ".config/gh",
    ".config/git/credentialstore",
)
_NEUTRALIZED_FILE_NAMES: tuple[str, ...] = (
    ".netrc",
    "_netrc",
    ".git-credentials",
    ".git-credential-cache",
)


class WorkspaceArtifactProjectionError(RuntimeError):
    """A durable workspace input could not be projected safely."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AdmittedSource:
    """Owner/digest/quarantine permission carried through the service boundary."""

    artifact_id: str
    digest: str | None
    size_bytes: int | None
    redaction_level: str | None


def _metadata_tuple(metadata: Any) -> tuple[Any, tuple[Any, ...]]:
    if isinstance(metadata, tuple):
        artifact = metadata[0] if len(metadata) > 0 else None
        links: Any = metadata[1] if len(metadata) > 1 else ()
        if links is None:
            links = ()
        return artifact, tuple(links)
    return metadata, ()


def _status_is_complete(artifact: Any) -> bool:
    status = getattr(artifact, "status", None)
    if status is None:
        return True
    name = str(getattr(status, "value", status)).upper()
    return name == "COMPLETE"


def _is_expired(artifact: Any) -> bool:
    expires_at = getattr(artifact, "expires_at", None)
    if expires_at is None:
        metadata = getattr(artifact, "metadata_json", None)
        if isinstance(metadata, dict):
            expires_at = metadata.get("expires_at")
    if expires_at is None:
        return False
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return False
    if isinstance(expires_at, datetime):
        moment = expires_at
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment <= datetime.now(tz=UTC)
    return False


def _redaction_level(artifact: Any) -> str | None:
    level = getattr(artifact, "redaction_level", None)
    if level is None:
        metadata = getattr(artifact, "metadata_json", None)
        if isinstance(metadata, dict):
            level = metadata.get("redaction_level") or metadata.get("quarantine")
    if level is None:
        return None
    return str(getattr(level, "value", level)).upper()


def _is_quarantined(artifact: Any) -> bool:
    """Return whether the artifact is explicitly quarantined.

    A top-level quarantine flag is distinct from the redaction level: either
    one fail-closes a generic restore, which never releases protected raw
    content into an agent workspace without its own explicit policy.
    """

    for attr in ("quarantine", "quarantined"):
        value = getattr(artifact, attr, None)
        if isinstance(value, str):
            if value.strip().lower() in {"true", "yes", "1", "quarantined"}:
                return True
        elif bool(value):
            return True
    metadata = getattr(artifact, "metadata_json", None)
    if isinstance(metadata, dict) and bool(metadata.get("quarantine")):
        return True
    return False


def _artifact_digest(artifact: Any) -> str | None:
    for attr in ("sha256", "digest", "content_digest"):
        value = getattr(artifact, attr, None)
        if value:
            text = str(value).strip().lower()
            if len(text) == 64 and all(c in "0123456789abcdef" for c in text):
                return "sha256:" + text
            if text.startswith("sha256:"):
                return text
    metadata = getattr(artifact, "metadata_json", None)
    if isinstance(metadata, dict):
        for key in ("sha256", "digest", "archive_digest", "contentDigest"):
            value = metadata.get(key)
            if value:
                text = str(value).strip().lower()
                if text.startswith("sha256:"):
                    return text
                if len(text) == 64:
                    return "sha256:" + text
    return None


def _check_workflow_family_link(
    links: tuple[Any, ...], required_workflow_id: str
) -> None:
    family = f"{required_workflow_id}:"
    for link in links:
        candidate = str(getattr(link, "workflow_id", "") or "").strip()
        if not candidate:
            continue
        if candidate == required_workflow_id:
            return
        # A family-prefixed link is only valid with a non-empty suffix; a
        # bare "workflow-id:" prefix is a forged link, not a grant.
        if candidate.startswith(family) and len(candidate) > len(family):
            return
    raise WorkspaceArtifactProjectionError(
        "attachment artifact is not linked to the current workflow family",
        code="WORKSPACE_AUTHORITY_MISMATCH",
    )


def cleanup_import_staging(parent: Path) -> int:
    """Delete leftover import-owned staging generations, never live workspaces.

    Only directories named ``.moonmind-import-*`` directly under ``parent``
    are removed. Returns the number of staging generations reclaimed.
    """

    reclaimed = 0
    try:
        entries = list(parent.iterdir())
    except OSError:
        return 0
    for entry in entries:
        if entry.name.startswith(STAGING_PREFIX):
            try:
                if entry.is_symlink() or not entry.is_dir():
                    entry.unlink(missing_ok=True)
                else:
                    shutil.rmtree(entry, ignore_errors=False)
                reclaimed += 1
            except OSError:
                continue
    return reclaimed


class WorkspaceArtifactProjector:
    """Apply checkpoints and declared inputs through one bounded data plane."""

    def __init__(self, artifact_service: Any | None) -> None:
        self._service = self._as_artifact_service(artifact_service)

    async def project(
        self,
        workspace: Path,
        *,
        checkpoint_ref: str | None = None,
        checkpoint_digest: str | None = None,
        checkpoint_contract: str | None = None,
        restore_refs: tuple[str, ...] = (),
        attachment_refs: tuple[str, ...] = (),
        workflow_id: str,
        runtime_uid: int,
        runtime_gid: int,
        strict_admission: bool = False,
        overlay_policy: str | None = None,
    ) -> dict[str, Any]:
        """Project every authored input before the workspace is mounted.

        ``strict_admission`` selects the new CONTRACT-002/CONTRACT-011 path:
        checkpoint admission requires linked artifact metadata for the owning
        workflow (missing metadata, forged family links, and
        restricted/quarantined bytes fail closed). Historical alias payloads
        keep the legacy-tolerant decoding so in-flight runs retain
        deterministic meaning. ``overlay_policy`` selects an authoritative
        full-snapshot restore (destination-only files are not retained) or
        an additive overlay over an admitted base.
        """

        evidence: dict[str, Any] = {}
        if checkpoint_ref:
            if overlay_policy is None:
                overlay_policy = (
                    "authoritative_restore" if strict_admission else OVERLAY_ADDITIVE
                )
            manifest = await self._apply_checkpoint(
                workspace,
                checkpoint_ref,
                expected_digest=checkpoint_digest,
                required_workflow_id=workflow_id if strict_admission else None,
                strict_metadata=strict_admission,
                overlay_policy=overlay_policy,
            )
            evidence["checkpointRestoreRef"] = checkpoint_ref
            evidence["checkpointManifest"] = manifest
            if checkpoint_contract:
                evidence["checkpointContract"] = checkpoint_contract
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
        expected_digest: str | None = None,
        required_workflow_id: str | None = None,
        strict_metadata: bool = False,
        overlay_policy: str = "authoritative_restore",
    ) -> dict[str, Any]:
        artifact_id = self._artifact_id(artifact_ref, noun="workspace checkpoint")
        service = self._require_service("workspace checkpoint")
        # Historical alias payloads without an authored workspaceSource pass
        # required_workflow_id=None and keep the legacy-tolerant admission so
        # in-flight runs retain deterministic meaning; the authored path
        # carries the owning workflow and fails closed on missing metadata.
        admitted = await self._validate_metadata(
            service,
            artifact_id=artifact_id,
            budget_bytes=min(MAX_CHECKPOINT_BYTES, MAX_COMPRESSED_BYTES),
            principal=RESTORE_PRINCIPAL,
            required_workflow_id=required_workflow_id,
            expected_digest=expected_digest,
            strict_metadata=strict_metadata,
        )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".moonmind-checkpoint-",
            suffix=".tar.gz",
            dir=workspace.parent,
        )
        os.close(descriptor)
        archive_path = Path(temporary_name)
        staging = workspace.parent / f"{STAGING_PREFIX}{uuid.uuid4().hex}"
        # No concurrent agent may mutate the extraction tree: the import lock
        # is held from staging creation through promotion. A retry reconciles
        # the same import token; a foreign in-flight import fails closed
        # instead of mutating another attempt's tree.
        import_token = f"{artifact_ref}:{expected_digest or ''}"
        import_lock = self._import_lock_path(workspace)
        try:
            self._acquire_import_lock(import_lock, token=import_token)
        except WorkspaceArtifactProjectionError:
            archive_path.unlink(missing_ok=True)
            raise
        try:
            await self._write_payload(
                service,
                artifact_id=artifact_id,
                target=archive_path,
                budget_bytes=min(MAX_CHECKPOINT_BYTES, MAX_COMPRESSED_BYTES),
                principal=RESTORE_PRINCIPAL,
                expected_digest=admitted.digest or expected_digest,
            )
            staging.mkdir(mode=0o700, parents=False, exist_ok=False)
            expanded = self._extract_archive_safely(
                archive_path, staging, noun="workspace checkpoint"
            )
            # The staged tree must match the archive before neutralization;
            # neutralization legitimately removes imported authority, so the
            # promoted evidence manifest is built after it runs.
            self._verify_materialized_manifest(staging, expanded=expanded)
            neutralized = self._neutralize_imported_workspace(staging)
            # Thin bundles, missing LFS/submodule objects, and external
            # baselines are incomplete unless independently admitted and
            # resolved: fail closed with no ready marker rather than
            # restoring a silently partial tree.
            incomplete = self._assess_completeness(staging)
            if incomplete:
                raise WorkspaceArtifactProjectionError(
                    "workspace checkpoint is incomplete: " + "; ".join(incomplete),
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                )
            manifest = self._verify_materialized_manifest(staging)
            # Re-verify every staged link against the materialized tree: a
            # link that was safe at write time must still resolve inside the
            # staging area after neutralization removed entries around it.
            self._verify_staged_links(staging)
            if overlay_policy == OVERLAY_ADDITIVE:
                self._promote_additive_overlay(workspace, staging)
            else:
                self._promote_authoritative_restore(workspace, staging)
            manifest["overlayPolicy"] = overlay_policy
            return {**manifest, "neutralized": neutralized}
        except WorkspaceArtifactProjectionError:
            raise
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise WorkspaceArtifactProjectionError(
                "workspace checkpoint archive could not be applied",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            ) from exc
        finally:
            self._release_import_lock(import_lock, token=import_token)
            archive_path.unlink(missing_ok=True)
            # Late cleanup removes only the import-owned staging generation;
            # a crash before/after promotion can never expose partial content
            # in the live workspace or delete another owner's content.
            try:
                if staging.is_symlink() or staging.is_file():
                    staging.unlink(missing_ok=True)
                elif staging.is_dir():
                    shutil.rmtree(staging, ignore_errors=True)
            except OSError:
                pass

    @staticmethod
    def _import_lock_path(workspace: Path) -> Path:
        return workspace.parent / f".moonmind-import-{workspace.name}.lock"

    @staticmethod
    def _acquire_import_lock(lock: Path, *, token: str) -> None:
        try:
            descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
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
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(str(token))

    @staticmethod
    def _release_import_lock(lock: Path, *, token: str) -> None:
        try:
            if lock.read_text(encoding="utf-8").strip() == str(token):
                lock.unlink()
        except OSError:
            pass

    @staticmethod
    def _assess_completeness(staging: Path) -> list[str]:
        """Report incomplete restores that need independent admission."""

        reasons: list[str] = []
        git_dir = staging / ".git"
        # An unresolved large-file pointer is a content stub wherever it
        # appears; truthful completeness does not depend on `.git` presence.
        reasons.extend(
            WorkspaceArtifactProjector._find_unresolved_lfs(staging, git_dir)
        )
        if git_dir.is_dir() and not git_dir.is_symlink():
            alternates = git_dir / "objects" / "info" / "alternates"
            if alternates.is_file() and not alternates.is_symlink():
                reasons.append("external object alternates require admission")
            if list(git_dir.glob("objects/pack/*.thinpack")):
                reasons.append("thin pack requires its external baseline")
            gitmodules = staging / ".gitmodules"
            if gitmodules.is_file() and not gitmodules.is_symlink():
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
                        populated = worktree.is_dir() and any(worktree.iterdir())
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

    @staticmethod
    def _verify_staged_links(staging: Path) -> None:
        """Re-verify every staged link against the materialized tree."""

        staging_root = staging.resolve()
        for dirpath, dirnames, filenames in os.walk(staging, followlinks=False):
            for name in (*dirnames, *filenames):
                target = Path(dirpath) / name
                if not target.is_symlink():
                    continue
                try:
                    resolved = target.resolve()
                except OSError as exc:
                    raise WorkspaceArtifactProjectionError(
                        "workspace checkpoint symlink is unresolvable",
                        code="WORKSPACE_AUTHORITY_MISMATCH",
                    ) from exc
                if resolved != staging_root and not resolved.is_relative_to(
                    staging_root
                ):
                    raise WorkspaceArtifactProjectionError(
                        "workspace checkpoint symlink escapes workspace",
                        code="WORKSPACE_AUTHORITY_MISMATCH",
                    )

    @staticmethod
    def _extract_archive_safely(
        archive_path: Path, staging: Path, *, noun: str
    ) -> dict[str, Any]:
        """Stream-extract with explicit resource/path budgets, no preflight gap.

        Members are validated *and* written sequentially: each symlink,
        hardlink, and overwrite is resolved against the staging tree as it
        accumulates, so a later member cannot invalidate an earlier check.
        No unbounded member-list allocation is performed.
        """

        staging_root = staging.resolve()
        deadline = time.monotonic() + MAX_PROCESSING_SECONDS
        seen: set[str] = set()
        # Lower-cased sibling index per directory for filesystem collisions.
        dir_children: dict[str, set[str]] = {}
        file_count = 0
        manifest_entries = 0
        expanded_bytes = 0

        def _fail(message: str, code: str) -> WorkspaceArtifactProjectionError:
            return WorkspaceArtifactProjectionError(f"{noun} {message}", code=code)

        try:
            archive = tarfile.open(archive_path, mode="r:gz")
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise _fail(
                "archive is truncated, malformed, or unsupported",
                "OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            ) from exc

        with archive:
            while True:
                if time.monotonic() > deadline:
                    raise _fail(
                        "archive exceeded the processing-time budget",
                        "OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                    )
                try:
                    member = archive.next()
                except (tarfile.TarError, OSError, EOFError) as exc:
                    raise _fail(
                        "archive is truncated, malformed, or unsupported",
                        "OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                    ) from exc
                if member is None:
                    break
                name = (member.name or "").strip()
                if not name or name in seen:
                    raise _fail(
                        "archive has duplicate or conflicting entries",
                        "WORKSPACE_AUTHORITY_MISMATCH",
                    )
                seen.add(name)
                if name.startswith("/") or name.startswith("\\"):
                    raise _fail(
                        "archive contains an absolute path",
                        "WORKSPACE_AUTHORITY_MISMATCH",
                    )
                parts = [
                    p for p in name.replace("\\", "/").split("/") if p not in ("",)
                ]
                if not parts or any(p in {".", ".."} for p in parts):
                    raise _fail(
                        "archive contains an escaping path",
                        "WORKSPACE_AUTHORITY_MISMATCH",
                    )
                if len(parts) > MAX_PATH_DEPTH:
                    raise _fail(
                        "archive exceeds the path-depth budget",
                        "OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                    )
                if member.isdev():
                    raise _fail(
                        "archive contains an unsupported device file",
                        "WORKSPACE_AUTHORITY_MISMATCH",
                    )
                if member.isfifo() or member.ischr() or member.isblk():
                    raise _fail(
                        "archive contains an unsupported file type",
                        "WORKSPACE_AUTHORITY_MISMATCH",
                    )
                target = staging_root
                for part in parts:
                    target = target / part
                try:
                    resolved_parent = target.parent.resolve()
                except OSError as exc:
                    raise _fail(
                        "archive target could not be resolved",
                        "WORKSPACE_AUTHORITY_MISMATCH",
                    ) from exc
                if (
                    resolved_parent != staging_root
                    and not resolved_parent.is_relative_to(staging_root)
                ):
                    raise _fail(
                        "archive contains an escaping path",
                        "WORKSPACE_AUTHORITY_MISMATCH",
                    )
                # Filesystem name-collision check: an entry that would land on
                # an already-materialized sibling (including case collisions)
                # is a conflicting entry, not an overwrite.
                parent_key = resolved_parent.as_posix().lower()
                lowered = parts[-1].lower()
                siblings = dir_children.setdefault(parent_key, set())
                if lowered in siblings or os.path.lexists(target):
                    raise _fail(
                        "archive has duplicate or conflicting entries",
                        "WORKSPACE_AUTHORITY_MISMATCH",
                    )
                siblings.add(lowered)

                claimed = int(member.size or 0)
                if claimed < 0:
                    raise _fail(
                        "archive is malformed",
                        "OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                    )
                if claimed > MAX_FILE_BYTES:
                    raise _fail(
                        "archive member exceeds the per-file budget",
                        "OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                    )
                if member.isfile():
                    file_count += 1
                    manifest_entries += 1
                    if file_count > MAX_FILE_COUNT:
                        raise _fail(
                            "archive exceeds the file-count budget",
                            "OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                        )
                    if expanded_bytes + claimed > MAX_EXPANDED_BYTES:
                        raise _fail(
                            "archive exceeds the expanded-bytes budget",
                            "OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                        )
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        source = archive.extractfile(member)
                    except (tarfile.TarError, OSError, EOFError) as exc:
                        raise _fail(
                            "archive is truncated, malformed, or unsupported",
                            "OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                        ) from exc
                    if source is None:
                        raise _fail(
                            "archive member could not be read",
                            "OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                        )
                    written = 0
                    descriptor = os.open(
                        target,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                    )
                    try:
                        with os.fdopen(descriptor, "wb") as stream:
                            while True:
                                chunk = source.read(STREAM_CHUNK_BYTES)
                                if not chunk:
                                    break
                                written += len(chunk)
                                expanded_bytes += len(chunk)
                                stream.write(chunk)
                                if (
                                    written > MAX_FILE_BYTES
                                    or expanded_bytes > MAX_EXPANDED_BYTES
                                ):
                                    raise _fail(
                                        "archive exceeds the streamed expanded-bytes "
                                        "budget",
                                        "OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                                    )
                    except WorkspaceArtifactProjectionError:
                        target.unlink(missing_ok=True)
                        raise
                    if written != claimed and claimed != 0:
                        # Sparse/oversized members whose headers understate the
                        # stream are classified, not silently accepted.
                        target.unlink(missing_ok=True)
                        raise _fail(
                            "archive member is sparse, truncated, or oversized",
                            "OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                        )
                    # Normalize privileged metadata; content stays as data.
                    try:
                        os.chmod(target, 0o600, follow_symlinks=False)
                    except OSError:
                        pass
                elif member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.issym() or member.islnk():
                    link_name = (member.linkname or "").strip()
                    if not link_name:
                        raise _fail(
                            "archive link has no target",
                            "WORKSPACE_AUTHORITY_MISMATCH",
                        )
                    manifest_entries += 1
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if member.issym():
                        base = target.parent
                        candidate = (base / link_name).resolve()
                        # Sequential check against the accumulating tree: an
                        # earlier symlink must not redirect this resolution.
                        if candidate != staging_root and not candidate.is_relative_to(
                            staging_root
                        ):
                            raise _fail(
                                "archive symlink escapes the workspace",
                                "WORKSPACE_AUTHORITY_MISMATCH",
                            )
                        try:
                            target.symlink_to(link_name)
                        except OSError as exc:
                            raise _fail(
                                "archive symlink could not be created",
                                "OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                            ) from exc
                    else:
                        candidate = (staging_root / link_name).resolve()
                        if candidate != staging_root and not candidate.is_relative_to(
                            staging_root
                        ):
                            raise _fail(
                                "archive hardlink escapes the workspace",
                                "WORKSPACE_AUTHORITY_MISMATCH",
                            )
                        if not candidate.is_file() or candidate.is_symlink():
                            raise _fail(
                                "archive hardlink target is unavailable",
                                "WORKSPACE_AUTHORITY_MISMATCH",
                            )
                        try:
                            os.link(candidate, target)
                        except OSError as exc:
                            raise _fail(
                                "archive hardlink could not be created",
                                "OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                            ) from exc
                else:
                    raise _fail(
                        "archive contains an unsupported file type",
                        "WORKSPACE_AUTHORITY_MISMATCH",
                    )
        return {
            "fileCount": file_count,
            "manifestEntries": manifest_entries,
            "expandedBytes": expanded_bytes,
        }

    @staticmethod
    def _neutralize_imported_workspace(staging: Path) -> list[str]:
        """Neutralize imported setup/credential/session authority as data.

        Removes imported hooks, credential directories/files, Git alternates
        and external worktree/gitdir bindings, include paths escaping the
        workspace, credential-bearing submodule URLs, and old session
        authority (leases, approvals, prior publication evidence). Safe
        content, history, and ordinary executable source files are preserved.
        """

        staging_root = staging.resolve()
        neutralized: list[str] = []

        def _record(path: Path) -> None:
            try:
                neutralized.append(path.relative_to(staging_root).as_posix())
            except ValueError:
                neutralized.append(path.name)

        def _remove_file(path: Path) -> None:
            try:
                if path.is_symlink() or path.is_file():
                    path.unlink()
                    _record(path)
            except OSError:
                pass

        def _remove_tree(path: Path) -> None:
            try:
                if path.is_symlink():
                    path.unlink()
                    _record(path)
                elif path.is_dir() and not path.is_mount():
                    shutil.rmtree(path, ignore_errors=True)
                    _record(path)
            except OSError:
                pass

        git_dir = staging / ".git"
        if git_dir.is_file() and not git_dir.is_symlink():
            # A gitdir pointer must stay inside the imported tree; an
            # external Git directory/worktree is rejected, not followed.
            try:
                pointer = git_dir.read_text(encoding="utf-8").strip()
            except OSError:
                pointer = ""
            external = False
            if pointer.startswith("gitdir:"):
                raw = pointer[len("gitdir:"):].strip()
                candidate = (staging / raw).resolve() if raw else staging_root
                if candidate != staging_root and not candidate.is_relative_to(
                    staging_root
                ):
                    external = True
            else:
                external = True
            if external:
                raise WorkspaceArtifactProjectionError(
                    "workspace checkpoint binds an external Git directory",
                    code="WORKSPACE_AUTHORITY_MISMATCH",
                )
        if git_dir.is_dir() and not git_dir.is_symlink():
            # Hooks/helpers must never execute on first Git command or launch.
            hooks = git_dir / "hooks"
            if hooks.is_dir() and not hooks.is_symlink():
                for child in list(hooks.iterdir()):
                    if child.is_symlink() or child.is_file():
                        _remove_file(child)
                    elif child.is_dir():
                        _remove_tree(child)
            # Alternates reference external object baselines: an unmaterialized
            # dependency is incomplete unless independently admitted.
            alternates = git_dir / "objects" / "info" / "alternates"
            if alternates.is_file() and not alternates.is_symlink():
                try:
                    content = alternates.read_text(encoding="utf-8").strip()
                except OSError:
                    content = ""
                if content:
                    raise WorkspaceArtifactProjectionError(
                        "workspace checkpoint needs an external object baseline",
                        code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                    )
            # commondir outside the tree is an external worktree binding.
            commondir = git_dir / "commondir"
            if commondir.is_file() and not commondir.is_symlink():
                try:
                    content = commondir.read_text(encoding="utf-8").strip()
                except OSError:
                    content = ""
                if content:
                    candidate = (git_dir / content).resolve()
                    if candidate != staging_root and not candidate.is_relative_to(
                        staging_root
                    ):
                        raise WorkspaceArtifactProjectionError(
                            "workspace checkpoint binds an external worktree",
                            code="WORKSPACE_AUTHORITY_MISMATCH",
                        )
            config = git_dir / "config"
            if config.is_file() and not config.is_symlink():
                WorkspaceArtifactProjector._scrub_git_config(config, staging_root)
                _record(config)

        # Credential-bearing submodule configuration is rejected, not restored.
        gitmodules = staging / ".gitmodules"
        if gitmodules.is_file() and not gitmodules.is_symlink():
            try:
                content = gitmodules.read_text(encoding="utf-8")
            except OSError:
                content = ""
            if "@" in content and "://" in content:
                import re as _re

                if _re.search(r"://[^/\s@]*@[^/\s]*", content):
                    raise WorkspaceArtifactProjectionError(
                        "workspace checkpoint carries credential-bearing "
                        "submodule configuration",
                        code="WORKSPACE_AUTHORITY_MISMATCH",
                    )

        for name in _NEUTRALIZED_DIR_NAMES:
            _remove_tree(staging / name)
        for name in _NEUTRALIZED_FILE_NAMES:
            _remove_file(staging / name)

        # Old session authority never revives: leases, approvals, prior
        # publication evidence, and previous session directories.
        moonmind_dir = staging / ".moonmind"
        if moonmind_dir.is_dir() and not moonmind_dir.is_symlink():
            for child in list(moonmind_dir.iterdir()):
                lowered = child.name.lower()
                if lowered.startswith(
                    ("session", "lease", "approval", "publication-evidence")
                ):
                    if child.is_dir() and not child.is_symlink():
                        _remove_tree(child)
                    else:
                        _remove_file(child)

        # Normalize privileged metadata on regular files; ordinary executable
        # source files keep a bounded executable bit, hooks do not exist.
        for root, _dirs, files in os.walk(staging):
            for filename in files:
                path = Path(root) / filename
                try:
                    if path.is_symlink():
                        continue
                    mode = path.stat().st_mode
                    cleaned = mode & ~(
                        stat.S_ISUID | stat.S_ISGID | stat.S_IWOTH
                    )
                    if cleaned != mode:
                        os.chmod(path, cleaned, follow_symlinks=False)
                        _record(path)
                except OSError:
                    continue
        return sorted(set(neutralized))

    @staticmethod
    def _scrub_git_config(config: Path, staging_root: Path) -> None:
        """Strip imported credential/include/helper bindings from git config."""

        try:
            lines = config.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        kept: list[str] = []
        section = ""
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped.lower()
                kept.append(line)
                continue
            lowered = stripped.lower()
            drop = False
            if section.startswith("[core]") and lowered.startswith(
                ("hookspath", "fsmonitor", "sshcommand")
            ):
                drop = True
            elif section.startswith("[credential") or lowered.startswith(
                "credential.helper"
            ):
                drop = True
            elif section.startswith("[filter ") and "process" in lowered:
                drop = True
            elif lowered.startswith("insteadof") or lowered.startswith("instead-of"):
                drop = True
            elif section.startswith("[include") and "path" in lowered:
                # Keep includes that resolve inside the imported tree; an
                # include path escaping it is an external binding, not data.
                _, _, raw = stripped.partition("=")
                candidate = (staging_root / raw.strip()).resolve()
                if candidate != staging_root and not candidate.is_relative_to(
                    staging_root
                ):
                    drop = True
            if not drop:
                kept.append(line)
        try:
            config.write_text("\n".join(kept) + "\n", encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _verify_materialized_manifest(
        staging: Path, *, expanded: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Verify the staged manifest before any promotion to ready.

        When ``expanded`` archive counts are given, the staged tree must match
        the archive exactly. Without them, the current staged tree is
        described for evidence (used after neutralization has legitimately
        removed imported authority).
        """

        digest = hashlib.sha256()
        count = 0
        link_count = 0
        total = 0
        staging_root = staging.resolve()
        for root, _dirs, files in os.walk(staging):
            for filename in sorted(files):
                path = Path(root) / filename
                try:
                    relative = path.relative_to(staging_root).as_posix()
                except ValueError as exc:
                    raise WorkspaceArtifactProjectionError(
                        "workspace checkpoint manifest escapes the staging area",
                        code="WORKSPACE_AUTHORITY_MISMATCH",
                    ) from exc
                try:
                    if path.is_symlink():
                        digest.update(f"link:{relative}\n".encode())
                        link_count += 1
                        continue
                    size = path.stat().st_size
                except OSError as exc:
                    raise WorkspaceArtifactProjectionError(
                        "workspace checkpoint manifest could not be verified",
                        code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                    ) from exc
                digest.update(f"file:{relative}:{size}\n".encode())
                count += 1
                total += size
        if expanded is not None and count + link_count != int(
            expanded.get("manifestEntries", count + link_count)
        ):
            raise WorkspaceArtifactProjectionError(
                "workspace checkpoint manifest does not match the archive",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        return {
            "fileCount": count,
            "symlinkCount": link_count,
            "expandedBytes": total,
            "manifestDigest": "sha256:" + digest.hexdigest(),
        }

    @staticmethod
    def _promote_additive_overlay(workspace: Path, staging: Path) -> None:
        """Merge a verified staging generation over an admitted base.

        Staged files overwrite same-type destination files; new entries are
        created; type conflicts (file over directory and vice versa) fail
        closed instead of destroying live content. Destination entries are
        unlinked with ``lstat`` semantics first so a pre-existing destination
        symlink can never redirect a staged write outside the workspace.
        """

        workspace.mkdir(parents=True, exist_ok=True)
        workspace_root = workspace.resolve()
        for child in list(staging.iterdir()):
            destination = workspace / child.name
            if os.path.lexists(destination):
                try:
                    dest_is_dir = destination.is_dir() and not destination.is_symlink()
                except OSError as exc:
                    raise WorkspaceArtifactProjectionError(
                        "additive overlay could not inspect the destination",
                        code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                    ) from exc
                staged_is_dir = child.is_dir() and not child.is_symlink()
                if dest_is_dir != staged_is_dir:
                    raise WorkspaceArtifactProjectionError(
                        "additive overlay type conflict with destination content",
                        code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                    )
                if staged_is_dir:
                    WorkspaceArtifactProjector._merge_tree(child, destination)
                    continue
                try:
                    if destination.is_symlink() or destination.is_file():
                        destination.unlink(missing_ok=True)
                    else:  # pragma: no cover - inspected above
                        raise WorkspaceArtifactProjectionError(
                            "additive overlay type conflict with destination content",
                            code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                        )
                except WorkspaceArtifactProjectionError:
                    raise
                except OSError as exc:
                    raise WorkspaceArtifactProjectionError(
                        "additive overlay could not reconcile the destination",
                        code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                    ) from exc
            try:
                child.rename(destination)
            except OSError as exc:
                raise WorkspaceArtifactProjectionError(
                    "additive overlay promotion failed",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                ) from exc
            try:
                if not destination.resolve().is_relative_to(workspace_root):
                    raise WorkspaceArtifactProjectionError(
                        "additive overlay escaped the authorized workspace",
                        code="WORKSPACE_AUTHORITY_MISMATCH",
                    )
            except OSError:
                pass

    @staticmethod
    def _merge_tree(staged: Path, destination: Path) -> None:
        """Recursively merge one staged directory into its destination peer."""

        for child in list(staged.iterdir()):
            target = destination / child.name
            if not os.path.lexists(target):
                try:
                    child.rename(target)
                except OSError as exc:
                    raise WorkspaceArtifactProjectionError(
                        "additive overlay promotion failed",
                        code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                    ) from exc
                continue
            try:
                target_is_dir = target.is_dir() and not target.is_symlink()
            except OSError as exc:
                raise WorkspaceArtifactProjectionError(
                    "additive overlay could not inspect the destination",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                ) from exc
            staged_is_dir = child.is_dir() and not child.is_symlink()
            if target_is_dir and staged_is_dir:
                WorkspaceArtifactProjector._merge_tree(child, target)
                continue
            if target_is_dir != staged_is_dir:
                raise WorkspaceArtifactProjectionError(
                    "additive overlay type conflict with destination content",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                )
            try:
                target.unlink(missing_ok=True)
                child.rename(target)
            except OSError as exc:
                raise WorkspaceArtifactProjectionError(
                    "additive overlay could not reconcile the destination",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                ) from exc

    @staticmethod
    def _promote_authoritative_restore(workspace: Path, staging: Path) -> None:
        """Promote the verified staging generation over the destination.

        A full snapshot is authoritative: destination-only files from a prior
        clone or failed attempt are not silently retained. Only the
        import-owned staging generation is moved; the         live workspace directory
        itself is never deleted, and nothing outside it is touched.
        """

        if workspace.is_symlink():
            raise WorkspaceArtifactProjectionError(
                "authorized workspace must not be a symlink",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        workspace.mkdir(parents=True, exist_ok=True)
        # Remove destination-only files first so a retry reconciles the same
        # generation instead of layering a new snapshot over stale content.
        for child in list(workspace.iterdir()):
            if child.name.startswith(STAGING_PREFIX):
                continue
            try:
                if child.is_symlink() or child.is_file():
                    child.unlink(missing_ok=True)
                elif child.is_dir():
                    # Never follow a destination symlink into another tree.
                    if child.is_symlink():
                        child.unlink(missing_ok=True)
                    else:
                        shutil.rmtree(child, ignore_errors=False)
            except OSError as exc:
                raise WorkspaceArtifactProjectionError(
                    "authoritative restore could not reconcile the destination",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                ) from exc
        for child in list(staging.iterdir()):
            destination = workspace / child.name
            if os.path.lexists(destination):
                raise WorkspaceArtifactProjectionError(
                    "authoritative restore promotion conflict",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                )
            try:
                child.rename(destination)
            except OSError as exc:
                raise WorkspaceArtifactProjectionError(
                    "authoritative restore promotion failed",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                ) from exc

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
            admitted = await self._validate_metadata(
                service,
                artifact_id=artifact_id,
                budget_bytes=budget,
                principal=principal,
                required_workflow_id=required_workflow_id,
            )
            target = root / hashlib.sha256(ref.encode("utf-8")).hexdigest()[:24]
            if target.is_symlink():
                raise WorkspaceArtifactProjectionError(
                    f"{noun} target must not be a symlink",
                    code="WORKSPACE_AUTHORITY_MISMATCH",
                )
            written = await self._write_payload(
                service,
                artifact_id=artifact_id,
                target=target,
                budget_bytes=budget,
                principal=principal,
                expected_digest=admitted.digest,
            )
            self._make_runtime_readable(
                target,
                runtime_uid=runtime_uid,
                runtime_gid=runtime_gid,
                noun=noun,
            )
            total_bytes += written
            evidence.append({"ref": ref, "bytes": written})
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
    async def _validate_metadata(
        service: Any,
        *,
        artifact_id: str,
        budget_bytes: int,
        principal: str,
        required_workflow_id: str | None = None,
        expected_digest: str | None = None,
        strict_metadata: bool = False,
    ) -> AdmittedSource:
        get_metadata = getattr(service, "get_metadata", None)
        if get_metadata is None:
            # A read-bytes-only adapter cannot establish owner, digest, or
            # quarantine permission; the authored path fails instead of
            # inferring authority from a service principal or permissive read.
            if (
                strict_metadata
                or getattr(service, "_moonmind_metadata_less", False)
                or required_workflow_id is not None
            ):
                raise WorkspaceArtifactProjectionError(
                    "source-artifact authorization requires linked artifact "
                    "metadata",
                    code="WORKSPACE_AUTHORITY_MISMATCH",
                )
            # Explicit historical decoding only: see _apply_checkpoint.
            return AdmittedSource(
                artifact_id=artifact_id,
                digest=expected_digest,
                size_bytes=None,
                redaction_level=None,
            )
        try:
            metadata = await get_metadata(artifact_id=artifact_id, principal=principal)
        except WorkspaceArtifactProjectionError:
            raise
        except Exception as exc:
            raise WorkspaceArtifactProjectionError(
                "source artifact metadata is unavailable",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            ) from exc
        artifact, links = _metadata_tuple(metadata)
        if artifact is None:
            raise WorkspaceArtifactProjectionError(
                "source artifact metadata is missing",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        if not _status_is_complete(artifact):
            raise WorkspaceArtifactProjectionError(
                "source artifact is not complete",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        if _is_expired(artifact):
            raise WorkspaceArtifactProjectionError(
                "source artifact lifetime has expired",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        # Restricted/quarantined bytes need their own explicit policy; a
        # generic restore never releases protected raw content.
        redaction = _redaction_level(artifact)
        if redaction is not None and redaction not in {"NONE", "UNCLASSIFIED", ""}:
            raise WorkspaceArtifactProjectionError(
                "source artifact is restricted and needs an explicit release policy",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        if _is_quarantined(artifact):
            raise WorkspaceArtifactProjectionError(
                "source artifact is quarantined and cannot enter an agent workspace",
                code="WORKSPACE_AUTHORITY_MISMATCH",
            )
        digest = _artifact_digest(artifact)
        if expected_digest is not None:
            want = expected_digest.strip().lower()
            if digest is None or digest.lower() != want:
                raise WorkspaceArtifactProjectionError(
                    "source artifact digest does not match the admitted digest",
                    code="WORKSPACE_AUTHORITY_MISMATCH",
                )
        if required_workflow_id is not None:
            _check_workflow_family_link(links, required_workflow_id)
        size = getattr(artifact, "size_bytes", None)
        if isinstance(size, int) and size > budget_bytes:
            raise WorkspaceArtifactProjectionError(
                "restore input exceeds the authorized workspace bound",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        size_int = size if isinstance(size, int) else None
        return AdmittedSource(
            artifact_id=artifact_id,
            digest=digest or expected_digest,
            size_bytes=size_int,
            redaction_level=redaction,
        )

    @staticmethod
    async def _write_payload(
        service: Any,
        *,
        artifact_id: str,
        target: Path,
        budget_bytes: int,
        principal: str,
        expected_digest: str | None = None,
    ) -> int:
        want = expected_digest.strip().lower() if expected_digest else None
        read_chunks = getattr(service, "read_chunks", None)
        if read_chunks is not None:
            try:
                _artifact, chunks = await read_chunks(
                    artifact_id=artifact_id,
                    principal=principal,
                    allow_restricted_raw=True,
                    chunk_size=STREAM_CHUNK_BYTES,
                )
            except WorkspaceArtifactProjectionError:
                raise
            except Exception as exc:
                raise WorkspaceArtifactProjectionError(
                    "restore input could not be read",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                ) from exc
            digest = hashlib.sha256()
            written = 0
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
                                "restore input chunk is malformed",
                                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                            )
                        written += len(chunk)
                        if written > budget_bytes:
                            raise WorkspaceArtifactProjectionError(
                                "restore input exceeds the authorized workspace bound",
                                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                            )
                        digest.update(chunk)
                        stream.write(chunk)
            except WorkspaceArtifactProjectionError:
                target.unlink(missing_ok=True)
                raise
            except OSError as exc:
                target.unlink(missing_ok=True)
                raise WorkspaceArtifactProjectionError(
                    "restore input could not be written",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                ) from exc
            if want is not None and f"sha256:{digest.hexdigest()}" != want:
                target.unlink(missing_ok=True)
                raise WorkspaceArtifactProjectionError(
                    "source bytes do not match the admitted digest",
                    code="WORKSPACE_AUTHORITY_MISMATCH",
                )
            return written
        try:
            _artifact, payload = await service.read(
                artifact_id=artifact_id,
                principal=principal,
                allow_restricted_raw=True,
            )
        except WorkspaceArtifactProjectionError:
            raise
        except Exception as exc:
            raise WorkspaceArtifactProjectionError(
                "restore input could not be read",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            ) from exc
        if len(payload) > budget_bytes:
            raise WorkspaceArtifactProjectionError(
                "restore input exceeds the authorized workspace bound",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        if want is not None:
            observed = f"sha256:{hashlib.sha256(bytes(payload)).hexdigest()}"
            if observed != want:
                raise WorkspaceArtifactProjectionError(
                    "source bytes do not match the admitted digest",
                    code="WORKSPACE_AUTHORITY_MISMATCH",
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

    @staticmethod
    def _as_artifact_service(gateway: Any | None) -> Any | None:
        if gateway is None:
            return None
        if hasattr(gateway, "read") or hasattr(gateway, "read_chunks"):
            return gateway

        class _GatewayAdapter:
            _moonmind_metadata_less = True

            async def read(self, *, artifact_id: str, **_kwargs: Any):
                payload = await gateway.read_bytes(f"artifact://{artifact_id}")
                return {}, payload

        return _GatewayAdapter()


__all__ = [
    "AdmittedSource",
    "WorkspaceArtifactProjectionError",
    "WorkspaceArtifactProjector",
    "cleanup_import_staging",
]
