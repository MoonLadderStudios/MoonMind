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
    WorkspaceArtifactProjectionError,
    WorkspaceArtifactProjector,
    cleanup_import_staging,
)
from moonmind.omnigent.workspace_sources import (
    CompiledWorkspaceSource,
    ExistingWorkspaceGrantLedger,
    WorkspaceSourceError,
    check_source_backend_supported,
    compile_workspace_source,
    resolve_workspace_backend,
    verify_existing_workspace_grant,
    verify_existing_workspace_grant_signature,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.workspace_locator_models import SandboxWorkspaceLocator
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
    daemon_visible_workspace_path,
    resolve_sandbox_workspace_locator,
)

_SAFE_VOLUME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")


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
        if not isinstance(runtime_uid, int) or not isinstance(runtime_gid, int):
            raise HarnessPlatformError(
                "workspace runtime identity is invalid",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        if runtime_uid < 0 or runtime_gid < 0:
            raise HarnessPlatformError(
                "workspace runtime identity is invalid",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        spec = (
            request.workspace_spec if isinstance(request.workspace_spec, dict) else {}
        )
        step_execution = getattr(request, "step_execution", None)
        owner_workflow_id = str(
            getattr(step_execution, "workflow_id", None)
            or getattr(request, "correlation_id", "")
        ).strip()
        owner_step_execution_id = str(
            getattr(step_execution, "step_execution_id", None)
            or getattr(request, "idempotency_key", "")
        ).strip()
        # Compile exactly one source plus its explicit overlay/input policy
        # before any read or filesystem mutation. Raw workspacePath/path
        # aliases are rejected here as a normal route; conflicting aliases
        # fail before the locator, clone, or projection boundaries run.
        try:
            source = compile_workspace_source(
                spec,
                workflow_id=owner_workflow_id,
                step_execution_id=owner_step_execution_id,
                runtime="omnigent",
            )
        except WorkspaceSourceError as exc:
            raise HarnessPlatformError(
                str(exc),
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            ) from exc
        # Unsupported backend/platform/source combinations fail before launch:
        # an exclusive writable grant cannot be honored on a remote daemon
        # view that cannot fence the owner's live checkout.
        try:
            check_source_backend_supported(
                source.kind,
                resolve_workspace_backend(),
                grant_mode=(
                    source.existing_grant.mode
                    if source.existing_grant is not None
                    else None
                ),
            )
        except WorkspaceSourceError as exc:
            raise HarnessPlatformError(
                str(exc),
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            ) from exc
        record_store: SandboxWorkspaceRecordStore | None = None
        workspace_id: str | None = None
        candidate, record_store, workspace_id = await self._resolve_source_directory(
            source,
            spec=spec,
            owner_workflow_id=owner_workflow_id,
            owner_step_execution_id=owner_step_execution_id,
            runtime_uid=runtime_uid,
            runtime_gid=runtime_gid,
        )
        if candidate == self._root or not candidate.is_relative_to(self._root):
            raise HarnessPlatformError(
                "workspace attachment escapes the configured workspace root",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        if not candidate.is_dir() or candidate.is_symlink():
            raise HarnessPlatformError(
                "authoritative workspace is unavailable or unsafe",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        fingerprint = source.readiness_fingerprint(
            target_owner_workflow_id=owner_workflow_id,
            attempt_id=owner_step_execution_id,
        )
        materialization_complete = bool(
            record_store is not None
            and workspace_id is not None
            and record_store.is_ready(workspace_id, fingerprint)
        )
        if not materialization_complete:
            # A retry reconciles the same generation; changed inputs are an
            # explicit new import that is fully re-admitted, staged, and
            # verified before the ready generation is replaced. A previous
            # ready marker never authorizes changed inputs.
            restore_refs = spec.get("restoreInputRefs")
            if not isinstance(restore_refs, (list, tuple)):
                restore_refs = ()
            attachment_refs = getattr(request, "input_refs", ())
            if not isinstance(attachment_refs, (list, tuple)):
                attachment_refs = ()
            checkpoint_ref = source.checkpoint_ref or source.artifact_ref
            try:
                await self._artifact_projector.project(
                    candidate,
                    checkpoint_ref=checkpoint_ref,
                    checkpoint_digest=source.artifact_digest,
                    checkpoint_contract=source.restore_contract,
                    restore_refs=tuple(str(ref) for ref in restore_refs),
                    attachment_refs=tuple(str(ref) for ref in attachment_refs),
                    workflow_id=owner_workflow_id,
                    runtime_uid=runtime_uid,
                    runtime_gid=runtime_gid,
                    strict_admission=not source.historical
                    and source.kind in {"artifact", "checkpoint"},
                    overlay_policy=source.overlay_policy,
                )
            except WorkspaceArtifactProjectionError as exc:
                raise HarnessPlatformError(str(exc), code=exc.code) from exc
            if record_store is not None and workspace_id is not None:
                record_store.mark_ready(workspace_id, fingerprint)
        # An advanced selected directory is never permission for an arbitrary
        # host mount: the qualified locator/daemon mapping below is the only
        # path from a worker path to a daemon-visible bind path.
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
        access_mode = "read-only" if mutation == "read_only" else "read-write"
        if source.kind == "existing_workspace" and source.existing_grant is not None:
            if source.existing_grant.mode == "read_only":
                access_mode = "read-only"
            elif mutation == "read_only":
                pass
        return {
            "kind": "bind",
            "sourceRef": str(daemon_candidate),
            "targetPath": "/workspaces/run",
            "accessMode": access_mode,
            "cleanupRef": None,
        }

    async def _resolve_source_directory(
        self,
        source: CompiledWorkspaceSource,
        *,
        spec: dict[str, Any],
        owner_workflow_id: str,
        owner_step_execution_id: str,
        runtime_uid: int,
        runtime_gid: int,
    ) -> tuple[Path, SandboxWorkspaceRecordStore | None, str | None]:
        """Resolve the attempt-owned directory for the single compiled source."""

        if source.kind == "existing_workspace":
            return await self._resolve_granted_workspace(
                source,
                target_workflow_id=owner_workflow_id,
                target_step_execution_id=owner_step_execution_id,
            )
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
        if not owner_workflow_id or not owner_step_execution_id:
            raise HarnessPlatformError(
                "sandbox workspace owner identity is unavailable",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        expected_workspace_id = hashlib.sha256(
            f"{owner_workflow_id}:{owner_step_execution_id}".encode("utf-8")
        ).hexdigest()[:24]
        try:
            candidate = resolve_sandbox_workspace_locator(
                sandbox_locator,
                workspace_root=self._root,
                expected_workspace_id=expected_workspace_id,
                must_exist=False,
            )
        except Exception as exc:
            raise HarnessPlatformError(
                f"sandbox workspace locator cannot be resolved: {exc}",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            ) from exc
        owner_record = SandboxWorkspaceRecord(
            workspace_id=sandbox_locator.workspace_id,
            workflow_id=owner_workflow_id,
            step_execution_id=owner_step_execution_id,
            relative_path=sandbox_locator.relative_path,
        )
        record_store = SandboxWorkspaceRecordStore(self._root)
        try:
            record_store.ensure(owner_record)
            candidate = resolve_sandbox_workspace_locator(
                sandbox_locator,
                workspace_root=self._root,
                expected_workspace_id=expected_workspace_id,
                owner_record=owner_record,
                expected_workflow_id=owner_workflow_id,
                expected_step_execution_id=owner_step_execution_id,
                must_exist=False,
            )
        except Exception as exc:
            raise HarnessPlatformError(
                f"sandbox workspace owner identity cannot be verified: {exc}",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            ) from exc
        if not candidate.exists():
            if source.kind == "repository" or (
                source.kind in {"artifact", "checkpoint"}
                and source.repository_ref
                and source.repository_branch
            ):
                # A repository source clones; a content source with an
                # admitted repository base clones that base before the
                # overlay is projected. Self-contained content sources (no
                # base) never clone or reacquire source credentials as a
                # prerequisite for restoring saved work.
                clone_spec = dict(spec)
                if source.kind in {"artifact", "checkpoint"}:
                    clone_spec["repository"] = source.repository_ref
                    clone_spec["branch"] = source.repository_branch
                await self._prepare_sandbox_workspace(
                    candidate,
                    spec=clone_spec,
                    source=source,
                    runtime_uid=runtime_uid,
                    runtime_gid=runtime_gid,
                )
            else:
                candidate.mkdir(mode=0o700, parents=True, exist_ok=True)
        else:
            # Reclaim leftover import-owned staging from a crashed attempt.
            # This touches only `.moonmind-import-*` generations, never the
            # live authorized workspace or another owner's content.
            cleanup_import_staging(candidate.parent)
        return candidate, record_store, sandbox_locator.workspace_id

    async def _resolve_granted_workspace(
        self,
        source: CompiledWorkspaceSource,
        *,
        target_workflow_id: str,
        target_step_execution_id: str,
    ) -> tuple[Path, SandboxWorkspaceRecordStore | None, str | None]:
        """Resolve an explicitly granted existing workspace, never a raw path."""

        grant = source.existing_grant
        if grant is None:  # pragma: no cover - compiler guarantees presence
            raise HarnessPlatformError(
                "existing workspace use requires a server-issued grant",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        record_store = SandboxWorkspaceRecordStore(self._root)
        try:
            record = record_store.load(grant.workspace_id)
        except Exception as exc:
            raise HarnessPlatformError(
                "existing workspace grant cannot be verified",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            ) from exc
        if record is None:
            raise HarnessPlatformError(
                "existing workspace grant names an unknown workspace",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        try:
            verify_existing_workspace_grant(
                grant,
                target_workflow_id=target_workflow_id,
                target_step_execution_id=target_step_execution_id,
                record_owner_workflow_id=record.workflow_id,
            )
        except WorkspaceSourceError as exc:
            raise HarnessPlatformError(
                str(exc),
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            ) from exc
        if grant.mode == "exclusive" and grant.owner_workflow_id != target_workflow_id:
            raise HarnessPlatformError(
                "existing workspace grant does not permit exclusive use by "
                "another workflow",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        # Stale generations fail before any filesystem use; the ledger lives
        # beside the owner records, never inside a materialized workspace.
        try:
            ExistingWorkspaceGrantLedger(record_store.store_root).admit(grant)
        except WorkspaceSourceError as exc:
            raise HarnessPlatformError(
                str(exc),
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            ) from exc
        # A signed grant carries issuance authenticity: forged signatures fail
        # closed here. Historical unsigned grants keep the owner/generation /
        # expiry checks above.
        try:
            verify_existing_workspace_grant_signature(
                grant, grantee_workflow_id=target_workflow_id
            )
        except WorkspaceSourceError as exc:
            raise HarnessPlatformError(
                str(exc),
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            ) from exc
        # Fence exclusive use: an exclusive claim conflicts with any other
        # active claim, while read-only claims coexist only with read-only
        # claims. Reclaiming the same grant is idempotent for retries.
        try:
            record_store.claim_existing_workspace(grant.workspace_id, grant)
        except Exception as exc:
            raise HarnessPlatformError(
                f"existing workspace is already granted elsewhere: {exc}",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            ) from exc
        authority = (self._root / "temporal_sandbox").resolve()
        owned_root = (authority / grant.workspace_id).resolve()
        if owned_root.parent != authority:
            raise HarnessPlatformError(
                "existing workspace grant escapes its authority",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        # The granted workspace is the recorded owner-relative path, resolved
        # under the same authority as sandbox locators. An advanced selected
        # directory never becomes permission for an arbitrary host mount.
        candidate = (owned_root / record.relative_path).resolve()
        if not candidate.is_relative_to(owned_root):
            raise HarnessPlatformError(
                "existing workspace grant escapes its authority",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        if not candidate.exists():
            raise HarnessPlatformError(
                "granted existing workspace is unavailable",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        return candidate, record_store, grant.workspace_id

    async def _prepare_sandbox_workspace(
        self,
        candidate: Path,
        *,
        spec: dict[str, Any],
        source: CompiledWorkspaceSource,
        runtime_uid: int,
        runtime_gid: int,
    ) -> None:
        """Clone the requested repository branch into a fresh sandbox dir.

        Only a repository source — or a content source with an admitted
        repository base — clones. The directory is created only inside
        the validated root and only for sandbox locators, so an
        operator-authored absolute path can never be materialized implicitly.
        """

        if source.kind not in {"repository", "artifact", "checkpoint"} or (
            source.kind in {"artifact", "checkpoint"}
            and not (source.repository_ref and source.repository_branch)
        ):
            # Self-contained artifact/checkpoint restores and scratch sources
            # prepare an empty attempt-owned directory with no clone and no
            # source-credential lookup.
            candidate.mkdir(mode=0o700, parents=True, exist_ok=True)
            return

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
    "build_daemon_git_clone_argv",
    "build_daemon_workspace_chown_argv",
    "normalize_github_clone_source",
    "resolve_daemon_workspace_root",
]
