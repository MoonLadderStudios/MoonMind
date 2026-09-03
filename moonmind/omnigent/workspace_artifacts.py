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
    ) -> dict[str, Any]:
        """Project every authored input before the workspace is mounted."""

        evidence: dict[str, Any] = {}
        if checkpoint_ref:
            await self._apply_checkpoint(workspace, checkpoint_ref)
            evidence["checkpointRestoreRef"] = checkpoint_ref
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

    async def _apply_checkpoint(self, workspace: Path, artifact_ref: str) -> None:
        artifact_id = self._artifact_id(artifact_ref, noun="workspace checkpoint")
        service = self._require_service("workspace checkpoint")
        await self._validate_metadata(
            service,
            artifact_id=artifact_id,
            budget_bytes=MAX_CHECKPOINT_BYTES,
            principal=RESTORE_PRINCIPAL,
        )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".moonmind-checkpoint-",
            suffix=".tar.gz",
            dir=workspace.parent,
        )
        os.close(descriptor)
        archive_path = Path(temporary_name)
        try:
            await self._write_payload(
                service,
                artifact_id=artifact_id,
                target=archive_path,
                budget_bytes=MAX_CHECKPOINT_BYTES,
                principal=RESTORE_PRINCIPAL,
            )
            workspace_root = workspace.resolve()
            with tarfile.open(archive_path, mode="r:gz") as archive:
                for member in archive.getmembers():
                    target = (workspace / member.name).resolve()
                    if not target.is_relative_to(workspace_root) or member.isdev():
                        raise WorkspaceArtifactProjectionError(
                            "workspace checkpoint contains an unsafe archive member",
                            code="WORKSPACE_AUTHORITY_MISMATCH",
                        )
                    if member.issym() or member.islnk():
                        link_target = (target.parent / member.linkname).resolve()
                        if not link_target.is_relative_to(workspace_root):
                            raise WorkspaceArtifactProjectionError(
                                "workspace checkpoint symlink escapes workspace",
                                code="WORKSPACE_AUTHORITY_MISMATCH",
                            )
                archive.extractall(workspace, filter="data")
        except WorkspaceArtifactProjectionError:
            raise
        except (tarfile.TarError, OSError) as exc:
            raise WorkspaceArtifactProjectionError(
                "workspace checkpoint archive could not be applied",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            ) from exc
        finally:
            archive_path.unlink(missing_ok=True)

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
            await self._validate_metadata(
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
    ) -> None:
        get_metadata = getattr(service, "get_metadata", None)
        if get_metadata is None:
            if required_workflow_id is not None:
                raise WorkspaceArtifactProjectionError(
                    "attachment authorization requires linked artifact metadata",
                    code="WORKSPACE_AUTHORITY_MISMATCH",
                )
            return
        metadata = await get_metadata(artifact_id=artifact_id, principal=principal)
        artifact = metadata[0] if isinstance(metadata, tuple) else metadata
        if required_workflow_id is not None:
            links = metadata[1] if isinstance(metadata, tuple) and len(metadata) > 1 else ()
            family = f"{required_workflow_id}:"
            if not any(
                str(getattr(link, "workflow_id", "")) == required_workflow_id
                or str(getattr(link, "workflow_id", "")).startswith(family)
                for link in links
            ):
                raise WorkspaceArtifactProjectionError(
                    "attachment artifact is not linked to the current workflow family",
                    code="WORKSPACE_AUTHORITY_MISMATCH",
                )
        size = getattr(artifact, "size_bytes", None)
        if isinstance(size, int) and size > budget_bytes:
            raise WorkspaceArtifactProjectionError(
                "restore input exceeds the authorized workspace bound",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )

    @staticmethod
    async def _write_payload(
        service: Any,
        *,
        artifact_id: str,
        target: Path,
        budget_bytes: int,
        principal: str,
    ) -> int:
        read_chunks = getattr(service, "read_chunks", None)
        if read_chunks is not None:
            _artifact, chunks = await read_chunks(
                artifact_id=artifact_id,
                principal=principal,
                allow_restricted_raw=True,
                chunk_size=STREAM_CHUNK_BYTES,
            )
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
                            "restore input exceeds the authorized workspace bound",
                            code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                        )
                    stream.write(chunk)
            return written
        _artifact, payload = await service.read(
            artifact_id=artifact_id,
            principal=principal,
            allow_restricted_raw=True,
        )
        if len(payload) > budget_bytes:
            raise WorkspaceArtifactProjectionError(
                "restore input exceeds the authorized workspace bound",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
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
            async def read(self, *, artifact_id: str, **_kwargs: Any):
                payload = await gateway.read_bytes(f"artifact://{artifact_id}")
                return {}, payload

        return _GatewayAdapter()


__all__ = [
    "WorkspaceArtifactProjectionError",
    "WorkspaceArtifactProjector",
]
