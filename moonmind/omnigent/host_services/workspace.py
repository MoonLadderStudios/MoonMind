"""Authoritative workspace attachment resolution and preparation."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Awaitable, Protocol

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.workspace_artifacts import (
    RESTORE_CONTRACT_VERSION,
    SUPPORTED_RESTORE_CONTRACTS,
    WorkspaceArtifactProjectionError,
    WorkspaceArtifactProjector,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.workspace_locator_models import SandboxWorkspaceLocator
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
    build_materialization_fingerprint,
    daemon_visible_workspace_path,
    parse_existing_workspace_grant,
    resolve_sandbox_workspace_locator,
)

_SAFE_VOLUME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")

SUPPORTED_SOURCE_KINDS = frozenset(
    {"scratch", "repository", "artifact", "checkpoint", "existing_workspace"}
)


class DaemonCommandRunner(Protocol):
    def __call__(
        self,
        argv: list[str],
        input_bytes: bytes | None = None,
    ) -> Awaitable[tuple[int, str, str]]:
        pass


async def resolve_daemon_workspace_root(
    *,
    runner: DaemonCommandRunner,
    workspace_volume: str,
) -> Path | None:
    """Resolve the authoritative workspace volume in local or remote mode."""

    mode = os.getenv("WORKFLOW_DOCKER_DAEMON_MODE", "").strip().lower()
    if mode in {"", "local"}:
        return None
    if mode != "remote" or not _SAFE_VOLUME.fullmatch(workspace_volume):
        raise HarnessPlatformError(
            "Docker daemon workspace mapping is unavailable or unsafe",
            code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
        )
    code, stdout, _stderr = await runner(
        ["docker", "volume", "inspect", "--format", "{{.Mountpoint}}", workspace_volume]
    )
    mountpoint = stdout.strip() if code == 0 else ""
    if not mountpoint or not Path(mountpoint).is_absolute():
        raise HarnessPlatformError(
            "agent workspace volume mountpoint is unavailable from the Docker daemon",
            code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
        )
    return Path(mountpoint).resolve()


def normalize_github_clone_source(repo_ref: str) -> str | None:
    """Return an HTTPS clone URL for owner/repo or GitHub remote forms."""

    cleaned = str(repo_ref or "").strip().rstrip("/")
    if not cleaned:
        return None
    owner_repo = re.fullmatch(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", cleaned)
    if owner_repo:
        return f"https://github.com/{owner_repo.group(1)}/{owner_repo.group(2)}.git"
    lowered = cleaned.lower()
    if lowered.startswith("https://github.com/") and cleaned.endswith(".git"):
        return cleaned
    if lowered.startswith("https://github.com/"):
        return f"{cleaned}.git"
    return None


def compile_workspace_source(spec: dict[str, Any]) -> dict[str, Any]:
    """Compile exactly one workspace source plus an explicit overlay policy.

    Artifact names authorized immutable content and its expected digest;
    checkpoint names a supported workspace-restore contract; existing_workspace
    names a verified locator/use grant. Raw-path precedence is not retained as
    a normal alternate authoring route: a lone historical ``workspacePath`` is
    decoded explicitly as an ``existing_workspace`` source that still requires
    a server-issued grant, and any conflicting source alias fails closed before
    reads or filesystem mutation.
    """

    if not isinstance(spec, dict):
        raise HarnessPlatformError(
            "workspace source is unavailable",
            code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
        )
    authored_source = spec.get("workspaceSource")
    raw_path = str(spec.get("workspacePath") or spec.get("path") or "").strip()
    locator = spec.get("workspaceLocator")
    checkpoint_ref = str(spec.get("workspaceCheckpointRestoreRef") or "").strip()
    repository_present = any(
        str(spec.get(key) or "").strip()
        for key in ("repository", "repo", "startingBranch", "branch", "headBranch")
    ) or isinstance(spec.get("repositoryTarget"), dict)
    artifact_ref = str(
        spec.get("artifactRef") or spec.get("sourceArtifactRef") or ""
    ).strip()

    if isinstance(authored_source, dict):
        kind = str(authored_source.get("kind") or "").strip()
        if kind not in SUPPORTED_SOURCE_KINDS:
            raise HarnessPlatformError(
                "workspace source kind is unsupported",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        # New-write producers use one canonical contract and reject historical
        # aliases; conflicting source aliases fail before external access.
        if raw_path or checkpoint_ref or repository_present or artifact_ref:
            raise HarnessPlatformError(
                "conflicting workspace source aliases are not supported",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        compiled: dict[str, Any] = {"kind": kind}
        if kind == "artifact":
            ref = str(
                authored_source.get("artifactRef")
                or authored_source.get("ref")
                or ""
            ).strip()
            digest = str(
                authored_source.get("digest")
                or authored_source.get("expectedDigest")
                or ""
            ).strip()
            if not ref or not digest:
                raise HarnessPlatformError(
                    "artifact source requires an authorized ref and expected digest",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
                )
            compiled["artifactRef"] = ref
            compiled["digest"] = digest
        elif kind == "checkpoint":
            ref = str(
                authored_source.get("checkpointRef")
                or authored_source.get("ref")
                or ""
            ).strip()
            contract = str(
                authored_source.get("contract")
                or authored_source.get("restoreContract")
                or RESTORE_CONTRACT_VERSION
            ).strip()
            version = str(
                authored_source.get("version")
                or authored_source.get("restoreVersion")
                or RESTORE_CONTRACT_VERSION
            ).strip()
            if not ref:
                raise HarnessPlatformError(
                    "checkpoint source requires a checkpoint ref",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
                )
            if contract not in SUPPORTED_RESTORE_CONTRACTS:
                raise HarnessPlatformError(
                    "workspace-restore contract is unsupported",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
                )
            compiled["checkpointRef"] = ref
            compiled["contract"] = contract
            compiled["version"] = version
            digest = str(
                authored_source.get("digest")
                or authored_source.get("expectedDigest")
                or ""
            ).strip()
            if digest:
                compiled["digest"] = digest
        elif kind == "existing_workspace":
            grant = authored_source.get("grant")
            if not isinstance(grant, dict):
                grant = spec.get("existingWorkspaceGrant")
            if not isinstance(grant, dict):
                raise HarnessPlatformError(
                    "existing workspace source requires a server-issued use grant",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
                )
            compiled["grant"] = grant
        elif kind == "repository":
            compiled["repository"] = authored_source.get("repository")
            compiled["branch"] = authored_source.get("branch")
        overlay = authored_source.get("overlay")
        compiled["overlay"] = dict(overlay) if isinstance(overlay, dict) else {}
        return compiled

    # Historical decoding: exactly one legacy source shape is admitted.
    legacy_kinds: list[str] = []
    if raw_path:
        legacy_kinds.append("existing_workspace")
    if isinstance(locator, dict):
        legacy_kinds.append("locator")
    if checkpoint_ref:
        legacy_kinds.append("checkpoint")
    if artifact_ref:
        legacy_kinds.append("artifact")
    if repository_present:
        legacy_kinds.append("repository")
    if raw_path and isinstance(locator, dict):
        raise HarnessPlatformError(
            "conflicting workspace source aliases are not supported",
            code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
        )
    if raw_path:
        grant = spec.get("existingWorkspaceGrant")
        if not isinstance(grant, dict):
            raise HarnessPlatformError(
                "existing workspace use requires a server-issued ownership grant",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        return {"kind": "existing_workspace", "rawPath": raw_path, "grant": grant}
    if not isinstance(locator, dict):
        raise HarnessPlatformError(
            "generic Omnigent execution requires an authoritative workspace locator",
            code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
        )
    if artifact_ref:
        digest = str(
            spec.get("artifactDigest")
            or spec.get("expectedDigest")
            or spec.get("sourceDigest")
            or ""
        ).strip()
        if not digest:
            raise HarnessPlatformError(
                "artifact source requires an authorized ref and expected digest",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        return {"kind": "artifact", "artifactRef": artifact_ref, "digest": digest}
    if checkpoint_ref and not repository_present:
        return {
            "kind": "checkpoint",
            "checkpointRef": checkpoint_ref,
            "contract": str(
                spec.get("restoreContract") or RESTORE_CONTRACT_VERSION
            ).strip(),
            "version": str(
                spec.get("restoreVersion") or RESTORE_CONTRACT_VERSION
            ).strip(),
            "digest": str(
                spec.get("artifactDigest")
                or spec.get("expectedDigest")
                or spec.get("sourceDigest")
                or ""
            ).strip() or None,
        }
    if repository_present:
        return {"kind": "repository"}
    return {"kind": "scratch"}


class OmnigentWorkspaceMaterializer:
    def __init__(
        self,
        *,
        command_runner: DaemonCommandRunner,
        workspace_root: str | Path | None = None,
        workspace_volume: str | None = None,
        artifact_service: Any | None = None,
    ) -> None:
        self._runner = command_runner
        self._root = Path(
            workspace_root
            or os.environ.get("WORKFLOW_WORKSPACE_ROOT", "/work/agent_jobs")
        ).resolve()
        self._workspace_volume = str(
            workspace_volume
            or os.getenv("MOONMIND_AGENT_WORKSPACES_VOLUME_NAME")
            or "agent_workspaces"
        ).strip()
        self._artifact_projector = WorkspaceArtifactProjector(artifact_service)

    async def materialize(
        self,
        request: AgentExecutionRequest,
        *,
        mutation: str = "allowed",
        runtime_uid: int = 1000,
        runtime_gid: int = 1000,
    ) -> dict[str, Any]:
        if mutation not in {"allowed", "read_only", "checkpoint_branch"}:
            raise HarnessPlatformError(
                "workspace mutation policy is unsupported",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        spec = (
            request.workspace_spec if isinstance(request.workspace_spec, dict) else {}
        )
        source = compile_workspace_source(spec)
        source_kind = str(source.get("kind") or "")
        step_execution = getattr(request, "step_execution", None)
        owner_workflow_id = str(
            getattr(step_execution, "workflow_id", None)
            or getattr(request, "correlation_id", "")
        ).strip()
        owner_step_execution_id = str(
            getattr(step_execution, "step_execution_id", None)
            or getattr(request, "idempotency_key", "")
        ).strip()
        if not owner_workflow_id or not owner_step_execution_id:
            raise HarnessPlatformError(
                "sandbox workspace owner identity is unavailable",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        record_store: SandboxWorkspaceRecordStore | None = None
        workspace_id: str | None = None
        grant_info: Any | None = None
        if source_kind == "existing_workspace" and source.get("rawPath"):
            # Historical decoding: a lone raw path is an existing-workspace
            # source that still requires a server-issued grant. Root
            # containment alone never authorizes a sibling directory: the raw
            # path must live under the sandbox authority for the granted
            # workspace identity, so a grant for one workspace cannot
            # authorize an unrelated host directory.
            raw = Path(str(source["rawPath"])).resolve()
            grant_payload = source.get("grant")
            try:
                rel = raw.relative_to(self._root)
            except ValueError as exc:
                raise HarnessPlatformError(
                    "workspace attachment escapes the configured workspace root",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
                ) from exc
            if (
                len(rel.parts) < 2
                or rel.parts[0] != "temporal_sandbox"
                or not rel.parts[1]
            ):
                raise HarnessPlatformError(
                    "historical workspace paths require sandbox authority",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
                )
            try:
                grant_info = parse_existing_workspace_grant(
                    grant_payload,
                    expected_workflow_id=owner_workflow_id,
                    expected_step_execution_id=owner_step_execution_id,
                    expected_workspace_id=rel.parts[1],
                )
            except Exception as exc:
                raise HarnessPlatformError(
                    str(exc),
                    code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
                ) from exc
            candidate = raw
            self._enforce_grant_access(grant_info, mutation=mutation)
        else:
            locator = spec.get("workspaceLocator")
            if not isinstance(locator, dict):
                raise HarnessPlatformError(
                    "generic Omnigent execution requires an authoritative workspace locator",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
                )
            try:
                sandbox_locator = SandboxWorkspaceLocator.model_validate(locator)
            except ValueError as exc:
                raise HarnessPlatformError(
                    "sandbox workspace locator is invalid",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
                ) from exc
            expected_workspace_id = hashlib.sha256(
                f"{owner_workflow_id}:{owner_step_execution_id}".encode("utf-8")
            ).hexdigest()[:24]
            candidate = resolve_sandbox_workspace_locator(
                sandbox_locator,
                workspace_root=self._root,
                expected_workspace_id=expected_workspace_id,
                must_exist=False,
            )
            owner_record = SandboxWorkspaceRecord(
                workspace_id=sandbox_locator.workspace_id,
                workflow_id=owner_workflow_id,
                step_execution_id=owner_step_execution_id,
                relative_path=sandbox_locator.relative_path,
            )
            record_store = SandboxWorkspaceRecordStore(self._root)
            workspace_id = sandbox_locator.workspace_id
            record_store.ensure(owner_record)
            resolve_sandbox_workspace_locator(
                sandbox_locator,
                workspace_root=self._root,
                expected_workspace_id=expected_workspace_id,
                owner_record=owner_record,
                expected_workflow_id=owner_workflow_id,
                expected_step_execution_id=owner_step_execution_id,
                must_exist=False,
            )
            if source_kind == "existing_workspace":
                grant_payload = source.get("grant")
                if grant_payload is None:
                    grant_payload = spec.get("existingWorkspaceGrant")
                try:
                    grant_info = parse_existing_workspace_grant(
                        grant_payload,
                        expected_workflow_id=owner_workflow_id,
                        expected_step_execution_id=owner_step_execution_id,
                        expected_workspace_id=sandbox_locator.workspace_id,
                    )
                except Exception as exc:
                    raise HarnessPlatformError(
                        str(exc),
                        code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
                    ) from exc
                self._enforce_grant_access(grant_info, mutation=mutation)
                self._check_generation_pin(
                    record_store, sandbox_locator.workspace_id, grant_info
                )
        if candidate == self._root or not candidate.is_relative_to(self._root):
            raise HarnessPlatformError(
                "workspace attachment escapes the configured workspace root",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        if source_kind == "existing_workspace":
            # An advanced selected directory is not permission for arbitrary
            # host mounts: the grant names one owned workspace, which must
            # already exist. Nothing is implicitly created or cloned here.
            if not candidate.is_dir() or candidate.is_symlink():
                raise HarnessPlatformError(
                    "authorized existing workspace is unavailable",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
                )
        elif source_kind in {"artifact", "checkpoint", "scratch"}:
            # Self-contained restores must not clone or reacquire source
            # credentials as a prerequisite: create the empty sandbox dir
            # locally and project the admitted snapshot into it.
            if not candidate.exists():
                candidate.mkdir(parents=True, exist_ok=True)
            elif not candidate.is_dir() or candidate.is_symlink():
                raise HarnessPlatformError(
                    "authoritative workspace is unavailable or unsafe",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
                )
        else:
            if not candidate.exists():
                await self._prepare_sandbox_workspace(
                    candidate,
                    spec=spec,
                    runtime_uid=runtime_uid,
                    runtime_gid=runtime_gid,
                )
            if not candidate.is_dir() or candidate.is_symlink():
                raise HarnessPlatformError(
                    "authoritative workspace is unavailable or unsafe",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
                )
        fingerprint = self._source_fingerprint(
            source=source,
            spec=spec,
            request=request,
            source_kind=source_kind,
            owner_workflow_id=owner_workflow_id,
            owner_step_execution_id=owner_step_execution_id,
            workspace_id=workspace_id or candidate.name,
        )
        materialization_complete = bool(
            record_store is not None
            and workspace_id is not None
            and record_store.is_materialized_for(workspace_id, fingerprint)
        )
        # Existing-workspace grants reuse an externally owned directory: the
        # ready marker is the grant generation pin, not a materialization
        # marker owned by this attempt. Additive overlays below still apply.
        if source_kind == "existing_workspace":
            materialization_complete = candidate.is_dir()
        if not materialization_complete:
            try:
                evidence = await self._project_source(
                    source=source,
                    spec=spec,
                    request=request,
                    candidate=candidate,
                    owner_workflow_id=owner_workflow_id,
                    runtime_uid=runtime_uid,
                    runtime_gid=runtime_gid,
                )
            except WorkspaceArtifactProjectionError as exc:
                raise HarnessPlatformError(str(exc), code=exc.code) from exc
            if record_store is not None and workspace_id is not None:
                if source_kind != "existing_workspace":
                    record_store.mark_materialized_for(workspace_id, fingerprint)
                else:
                    self._record_generation_pin(
                        record_store, workspace_id, grant_info
                    )
            _ = evidence
        daemon_root = await resolve_daemon_workspace_root(
            runner=self._runner,
            workspace_volume=self._workspace_volume,
        )
        try:
            daemon_candidate = daemon_visible_workspace_path(
                candidate, daemon_root=daemon_root
            )
        except Exception as exc:
            raise HarnessPlatformError(
                "workspace cannot be translated to the selected Docker daemon",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            ) from exc
        return {
            "kind": "bind",
            "sourceRef": str(daemon_candidate),
            "targetPath": "/workspaces/run",
            "accessMode": "read-only" if mutation == "read_only" else "read-write",
            "cleanupRef": None,
        }

    @staticmethod
    def _enforce_grant_access(grant_info: Any, *, mutation: str) -> None:
        """Enforce the exclusive-or-read-only sharing declared by the grant."""

        if grant_info is None:
            return
        if bool(getattr(grant_info, "read_only", False)) and mutation != "read_only":
            raise HarnessPlatformError(
                "existing workspace grant authorizes read-only sharing only",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )

    @staticmethod
    def _generation_path(
        record_store: SandboxWorkspaceRecordStore, workspace_id: str
    ) -> Path:
        candidate = (
            record_store.store_root / f"{workspace_id}.generation"
        ).resolve()
        if candidate.parent != record_store.store_root.resolve():
            raise HarnessPlatformError(
                "workspace generation escapes its authority",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        return candidate

    def _check_generation_pin(
        self,
        record_store: SandboxWorkspaceRecordStore,
        workspace_id: str,
        grant_info: Any,
    ) -> None:
        expected = str(getattr(grant_info, "expected_generation", "") or "").strip()
        if not expected:
            return
        path = self._generation_path(record_store, workspace_id)
        try:
            recorded = path.read_text(encoding="utf-8").strip()
        except OSError:
            recorded = ""
        if recorded and recorded != expected:
            raise HarnessPlatformError(
                "existing workspace generation does not match the grant",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )

    def _record_generation_pin(
        self,
        record_store: SandboxWorkspaceRecordStore,
        workspace_id: str,
        grant_info: Any,
    ) -> None:
        expected = str(
            getattr(grant_info, "expected_generation", "") or ""
        ).strip()
        if not expected:
            return
        path = self._generation_path(record_store, workspace_id)
        try:
            recorded = path.read_text(encoding="utf-8").strip()
        except OSError:
            recorded = ""
        if recorded and recorded != expected:
            raise HarnessPlatformError(
                "existing workspace generation does not match the grant",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        if not recorded:
            record_store.store_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(expected)

    @staticmethod
    def _source_fingerprint(
        *,
        source: dict[str, Any],
        spec: dict[str, Any],
        request: Any,
        source_kind: str,
        owner_workflow_id: str,
        owner_step_execution_id: str,
        workspace_id: str,
    ) -> dict[str, str]:
        import hashlib as _hashlib
        import json as _json

        source_digest = str(source.get("digest") or "").strip()
        if not source_digest and source_kind == "repository":
            repository_target = (
                spec.get("repositoryTarget")
                if isinstance(spec.get("repositoryTarget"), dict)
                else {}
            )
            revision = repository_target.get("revision") if isinstance(
                repository_target, dict
            ) else None
            commit = ""
            if isinstance(revision, dict):
                commit = str(
                    revision.get("commitSha") or revision.get("revisionSignature") or ""
                ).strip()
            if not commit:
                commit = str(
                    spec.get("checkoutCommit") or spec.get("baseCommit") or ""
                ).strip()
            if not commit:
                commit = str(
                    spec.get("startingBranch") or spec.get("branch") or ""
                ).strip()
            source_digest = commit
        restore_contract = str(
            source.get("contract") or spec.get("restoreContract") or ""
        ).strip()
        restore_version = str(
            source.get("version") or spec.get("restoreVersion") or ""
        ).strip()
        restore_refs = spec.get("restoreInputRefs")
        if not isinstance(restore_refs, (list, tuple)):
            restore_refs = ()
        attachment_refs = getattr(request, "input_refs", ())
        if not isinstance(attachment_refs, (list, tuple)):
            attachment_refs = ()
        overlay = source.get("overlay")
        overlay_refs: list[str] = []
        if isinstance(overlay, dict):
            for key in ("restoreInputRefs", "attachmentRefs", "inputRefs"):
                values = overlay.get(key)
                if isinstance(values, (list, tuple)):
                    overlay_refs.extend(str(v) for v in values)
        manifest_parts = [
            str(r) for r in list(restore_refs) + list(attachment_refs) + overlay_refs
        ]
        expected_digests = spec.get("expectedDigests")
        if isinstance(expected_digests, dict):
            for ref in sorted(str(k) for k in expected_digests):
                manifest_parts.append(f"{ref}={expected_digests[ref]}")
        manifest_digest = "sha256:" + _hashlib.sha256(
            _json.dumps(sorted(manifest_parts), sort_keys=True).encode("utf-8")
        ).hexdigest()
        return build_materialization_fingerprint(
            source_kind=source_kind,
            source_digest=source_digest or None,
            restore_contract=restore_contract or None,
            restore_version=restore_version or None,
            input_manifest_digest=manifest_digest,
            owner_workflow_id=owner_workflow_id,
            owner_step_execution_id=owner_step_execution_id,
            workspace_id=workspace_id,
        )

    async def _project_source(
        self,
        *,
        source: dict[str, Any],
        spec: dict[str, Any],
        request: Any,
        candidate: Path,
        owner_workflow_id: str,
        runtime_uid: int,
        runtime_gid: int,
    ) -> dict[str, Any]:
        import hashlib as _hashlib

        source_kind = str(source.get("kind") or "")
        restore_refs = spec.get("restoreInputRefs")
        if not isinstance(restore_refs, (list, tuple)):
            restore_refs = ()
        attachment_refs = getattr(request, "input_refs", ())
        if not isinstance(attachment_refs, (list, tuple)):
            attachment_refs = ()
        overlay = source.get("overlay")
        if isinstance(overlay, dict):
            extra_restore = overlay.get("restoreInputRefs")
            if isinstance(extra_restore, (list, tuple)):
                restore_refs = tuple(restore_refs) + tuple(extra_restore)
            extra_attach = overlay.get("attachmentRefs", overlay.get("inputRefs"))
            if isinstance(extra_attach, (list, tuple)):
                attachment_refs = tuple(attachment_refs) + tuple(extra_attach)
        checkpoint_ref: str | None = None
        checkpoint_contract: str | None = None
        checkpoint_version: str | None = None
        expected_digests: dict[str, str] = {}
        raw_digests = spec.get("expectedDigests")
        if isinstance(raw_digests, dict):
            for key, value in raw_digests.items():
                expected_digests[str(key)] = str(value)
        allow_restricted = bool(spec.get("allowRestrictedContent", False))
        # Non-Git content needs no synthetic commits or repository IDs; thin
        # bundles and missing LFS/submodule objects stay incomplete unless
        # independently admitted and resolved.
        attempt_id = _hashlib.sha256(
            f"{owner_workflow_id}:{candidate.name}".encode("utf-8")
        ).hexdigest()[:12]
        if source_kind == "artifact":
            ref = str(source.get("artifactRef") or "").strip()
            digest = str(source.get("digest") or "").strip()
            if ref and digest:
                expected_digests.setdefault(ref, digest)
            # An artifact source is a full snapshot import through the same
            # bounded checkpoint path, not a second workspace hierarchy.
            checkpoint_ref, checkpoint_contract, checkpoint_version = (
                ref,
                RESTORE_CONTRACT_VERSION,
                RESTORE_CONTRACT_VERSION,
            )
        elif source_kind == "checkpoint":
            checkpoint_ref = str(
                source.get("checkpointRef")
                or spec.get("workspaceCheckpointRestoreRef")
                or ""
            ).strip() or None
            checkpoint_contract = str(
                source.get("contract") or spec.get("restoreContract") or ""
            ).strip() or None
            checkpoint_version = str(
                source.get("version") or spec.get("restoreVersion") or ""
            ).strip() or None
            digest = str(source.get("digest") or "").strip()
            if checkpoint_ref and digest:
                expected_digests.setdefault(checkpoint_ref, digest)
        elif source_kind in {"repository", "scratch"}:
            checkpoint_ref = str(
                spec.get("workspaceCheckpointRestoreRef") or ""
            ).strip() or None
            if checkpoint_ref:
                checkpoint_contract = str(
                    spec.get("restoreContract") or RESTORE_CONTRACT_VERSION
                ).strip()
                checkpoint_version = str(
                    spec.get("restoreVersion") or RESTORE_CONTRACT_VERSION
                ).strip()
        elif source_kind == "existing_workspace":
            # Existing workspaces receive additive attachment overlays only;
            # a full snapshot restore never runs against another owner's live
            # directory from this path.
            checkpoint_ref = None
        try:
            return await self._artifact_projector.project(
                candidate,
                checkpoint_ref=checkpoint_ref,
                restore_refs=tuple(str(ref) for ref in restore_refs),
                attachment_refs=tuple(str(ref) for ref in attachment_refs),
                workflow_id=owner_workflow_id,
                runtime_uid=runtime_uid,
                runtime_gid=runtime_gid,
                checkpoint_contract=checkpoint_contract,
                checkpoint_version=checkpoint_version,
                expected_digests=expected_digests or None,
                allow_restricted=allow_restricted,
                attempt_id=attempt_id,
                authoritative_restore=source_kind in {"artifact", "checkpoint"},
            )
        except WorkspaceArtifactProjectionError as exc:
            raise HarnessPlatformError(str(exc), code=exc.code) from exc

    async def _prepare_sandbox_workspace(
        self,
        candidate: Path,
        *,
        spec: dict[str, Any],
        runtime_uid: int,
        runtime_gid: int,
    ) -> None:
        """Clone the requested repository branch into a fresh sandbox dir.

        The directory is created only inside the validated root and only for
        sandbox locators, so an operator-authored absolute path can never be
        materialized implicitly.
        """

        repository_target = (
            spec.get("repositoryTarget")
            if isinstance(spec.get("repositoryTarget"), dict)
            else {}
        )
        repo_ref = str(
            repository_target.get("repository", {}).get("name")
            if isinstance(repository_target.get("repository"), dict)
            else ""
        ) or str(spec.get("repository") or spec.get("repo") or "")
        branch = str(
            (repository_target.get("branch") or {}).get("name")
            if isinstance(repository_target.get("branch"), dict)
            else ""
        ) or str(
            spec.get("startingBranch")
            or spec.get("branch")
            or spec.get("headBranch")
            or ""
        ).strip()
        clone_source = normalize_github_clone_source(repo_ref)
        if clone_source is None or not branch or len(branch) > 400:
            raise HarnessPlatformError(
                "sandbox workspace preparation needs a GitHub repository and safe branch ref",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        rel = candidate.relative_to(self._root)
        await self._clone_into_volume(
            rel=rel,
            source=clone_source,
            branch=branch,
            runtime_uid=runtime_uid,
            runtime_gid=runtime_gid,
        )

    async def _clone_into_volume(
        self,
        *,
        rel: Path,
        source: str,
        branch: str,
        runtime_uid: int,
        runtime_gid: int,
    ) -> None:
        """Clone into the agent-workspaces volume through the Docker daemon.

        The worker image deliberately ships without a host ``git``; the same
        trusted Docker command boundary that mounts workspaces for hosts also
        performs the authenticated clone inside a disposable container.
        """

        from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
            resolve_github_token_for_launch,
        )

        token = await resolve_github_token_for_launch()
        if not token:
            raise HarnessPlatformError(
                "sandbox workspace clone requires GitHub credentials",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        image = os.getenv("MOONMIND_WORKSPACE_GIT_IMAGE", "alpine/git:v2.43.0")
        argv = build_daemon_git_clone_argv(
            volume=self._workspace_volume,
            target_in_volume=rel.as_posix(),
            source=source,
            branch=branch,
            image=image,
        )
        # The one-shot container reads the token on stdin and exposes it to Git
        # through an ephemeral credential helper. The clean source URL is the
        # only remote persisted in the authoritative workspace; credentials do
        # not enter Docker argv, container environment, or ``.git/config``.
        code, _stdout, stderr = await self._runner(argv, token.encode("utf-8"))
        if code != 0:
            detail = (stderr or "").strip()[-300:]
            raise HarnessPlatformError(
                "sandbox workspace clone failed for the requested branch"
                + (f": {detail}" if detail else ""),
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        ownership_argv = build_daemon_workspace_chown_argv(
            volume=self._workspace_volume,
            target_in_volume=rel.as_posix(),
            runtime_uid=runtime_uid,
            runtime_gid=runtime_gid,
            image=image,
        )
        code, _stdout, stderr = await self._runner(ownership_argv)
        if code != 0:
            detail = (stderr or "").strip()[-300:]
            raise HarnessPlatformError(
                "sandbox workspace ownership handoff failed"
                + (f": {detail}" if detail else ""),
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )


def build_daemon_git_clone_argv(
    *,
    volume: str,
    target_in_volume: str,
    source: str,
    branch: str,
    image: str,
) -> list[str]:
    """Build a stdin-authenticated Docker argv for an in-volume git clone."""

    if not _SAFE_VOLUME.fullmatch(volume):
        raise HarnessPlatformError(
            "agent workspace volume name is unavailable or unsafe",
            code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
        )
    if normalize_github_clone_source(source) != source:
        raise HarnessPlatformError(
            "sandbox workspace clone source is unavailable or unsafe",
            code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
        )
    script = (
        "set -eu; umask 077; token_file=$(mktemp); "
        "trap 'rm -f \"$token_file\"' EXIT HUP INT TERM; "
        "cat > \"$token_file\"; "
        "git check-ref-format --branch \"$1\" >/dev/null; "
        "credential_helper='!f() { test \"$1\" = get || exit 0; "
        "printf \"username=x-access-token\\npassword=\"; "
        "cat \"$MM_GIT_TOKEN_FILE\"; printf \"\\n\"; }; f'; "
        "MM_GIT_TOKEN_FILE=\"$token_file\" git "
        "-c \"credential.helper=$credential_helper\" clone "
        "--branch \"$1\" --single-branch -- \"$2\" \"$3\""
    )
    return [
        "docker",
        "run",
        "--rm",
        "-i",
        "-v",
        f"{volume}:/work",
        "--entrypoint",
        "/bin/sh",
        image,
        "-ceu",
        script,
        "--",
        branch,
        source,
        "/work/" + target_in_volume.lstrip("/"),
    ]


def build_daemon_workspace_chown_argv(
    *,
    volume: str,
    target_in_volume: str,
    runtime_uid: int,
    runtime_gid: int,
    image: str,
) -> list[str]:
    """Build the bounded ownership handoff for a daemon-created checkout."""

    if not _SAFE_VOLUME.fullmatch(volume):
        raise HarnessPlatformError(
            "agent workspace volume name is unavailable or unsafe",
            code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
        )
    target = Path(target_in_volume)
    if (
        target.is_absolute()
        or not target.parts
        or ".." in target.parts
        or runtime_uid <= 0
        or runtime_gid <= 0
    ):
        raise HarnessPlatformError(
            "sandbox workspace ownership target is unavailable or unsafe",
            code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
        )
    return [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{volume}:/work",
        "--entrypoint",
        "/bin/chown",
        image,
        "-R",
        "--",
        f"{runtime_uid}:{runtime_gid}",
        "/work/" + target.as_posix(),
    ]


def _rmdir_if_empty(path: Path) -> None:
    try:
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    except OSError:
        # Best-effort cleanup of an empty workspace directory. A concurrent
        # writer, a race with another cleanup, or a read-only mount must not
        # fail the caller: the directory is left in place for the next sweep.
        pass


__all__ = [
    "OmnigentWorkspaceMaterializer",
    "SUPPORTED_SOURCE_KINDS",
    "build_daemon_git_clone_argv",
    "build_daemon_workspace_chown_argv",
    "compile_workspace_source",
    "normalize_github_clone_source",
    "resolve_daemon_workspace_root",
]
