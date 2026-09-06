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
)
from moonmind.omnigent.workspace_sources import (
    check_source_backend_supported,
    compile_workspace_source,
    verify_existing_workspace_grant,
    WorkspaceSourceCompilationError,
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
        spec = (
            request.workspace_spec if isinstance(request.workspace_spec, dict) else {}
        )
        locator = spec.get("workspaceLocator")
        step_execution = getattr(request, "step_execution", None)
        owner_workflow_id = str(
            getattr(step_execution, "workflow_id", None)
            or getattr(request, "correlation_id", "")
        ).strip()
        owner_step_execution_id = str(
            getattr(step_execution, "step_execution_id", None)
            or getattr(request, "idempotency_key", "")
        ).strip()
        # Compile exactly one workspace source plus its overlay/input policy
        # before any read or filesystem mutation. Conflicting aliases fail
        # here; raw workspacePath/path aliases are not a normal authoring
        # route and fail unless explicitly decoded as recorded history.
        try:
            compiled_source = compile_workspace_source(spec)
        except WorkspaceSourceCompilationError as exc:
            raise HarnessPlatformError(
                f"workspace source is unavailable or unsafe: {exc}",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            ) from exc
        try:
            check_source_backend_supported(
                compiled_source.kind,
                _workspace_backend(),
                grant_mode=(
                    compiled_source.grant.mode if compiled_source.grant else None
                ),
            )
        except WorkspaceSourceCompilationError as exc:
            raise HarnessPlatformError(
                f"workspace source is unsupported on this runtime: {exc}",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            ) from exc
        if compiled_source.kind == "existing_workspace":
            return await self._materialize_existing_workspace(
                compiled_source,
                spec=spec,
                owner_workflow_id=owner_workflow_id,
                owner_step_execution_id=owner_step_execution_id,
                mutation=mutation,
                runtime_uid=runtime_uid,
                runtime_gid=runtime_gid,
            )
        record_store: SandboxWorkspaceRecordStore | None = None
        workspace_id: str | None = None
        if isinstance(locator, dict):
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
        else:
            raise HarnessPlatformError(
                "generic Omnigent execution requires an authoritative workspace locator",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        if candidate == self._root or not candidate.is_relative_to(self._root):
            raise HarnessPlatformError(
                "workspace attachment escapes the configured workspace root",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        if not candidate.exists():
            if compiled_source.kind == "repository":
                await self._prepare_sandbox_workspace(
                    candidate,
                    spec=spec,
                    runtime_uid=runtime_uid,
                    runtime_gid=runtime_gid,
                )
            elif compiled_source.kind in {"scratch", "artifact", "checkpoint"}:
                # Self-contained sources never clone and never reacquire
                # source credentials as a prerequisite: the target starts as
                # an empty contained directory and the snapshot import below
                # provides the content.
                candidate.mkdir(parents=True, exist_ok=True)
            else:  # pragma: no cover - compiler exhausts kinds above
                raise HarnessPlatformError(
                    "workspace source is unsupported",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
                )
        if not candidate.is_dir() or candidate.is_symlink():
            raise HarnessPlatformError(
                "authoritative workspace is unavailable or unsafe",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        ready_binding = _ready_binding_for_source(
            compiled_source,
            spec=spec,
            target_owner=f"{owner_workflow_id}:{owner_step_execution_id}",
        )
        materialization_complete = bool(
            record_store is not None
            and workspace_id is not None
            and ready_binding is not None
            and record_store.is_materialized_for(workspace_id, ready_binding)
        )
        if not materialization_complete:
            restore_refs = spec.get("restoreInputRefs")
            if not isinstance(restore_refs, (list, tuple)):
                restore_refs = ()
            attachment_refs = getattr(request, "input_refs", ())
            if not isinstance(attachment_refs, (list, tuple)):
                attachment_refs = ()
            checkpoint_ref = str(
                spec.get("workspaceCheckpointRestoreRef") or ""
            ).strip()
            if compiled_source.kind in {"artifact", "checkpoint"} and not checkpoint_ref:
                checkpoint_ref = str(
                    compiled_source.checkpoint_ref
                    or compiled_source.artifact_ref
                    or ""
                )
            try:
                evidence = await self._artifact_projector.project(
                    candidate,
                    checkpoint_ref=checkpoint_ref or None,
                    restore_refs=tuple(str(ref) for ref in restore_refs),
                    attachment_refs=tuple(str(ref) for ref in attachment_refs),
                    workflow_id=owner_workflow_id,
                    runtime_uid=runtime_uid,
                    runtime_gid=runtime_gid,
                    source=compiled_source,
                    target_owner=f"{owner_workflow_id}:{owner_step_execution_id}",
                )
            except WorkspaceArtifactProjectionError as exc:
                raise HarnessPlatformError(str(exc), code=exc.code) from exc
            if ready_binding is None:
                # A checkpoint restore without a declared digest binds the
                # observed, digest-verified artifact digest after download.
                observed = (evidence.get("checkpointRestore") or {}).get(
                    "sourceDigest"
                )
                ready_binding = _ready_binding_for_source(
                    compiled_source,
                    spec=spec,
                    target_owner=f"{owner_workflow_id}:{owner_step_execution_id}",
                    observed_digest=str(observed or ""),
                )
            if (
                record_store is not None
                and workspace_id is not None
                and ready_binding is not None
            ):
                record_store.mark_materialized_for(workspace_id, ready_binding)
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

    async def _materialize_existing_workspace(
        self,
        compiled_source: Any,
        *,
        spec: dict[str, Any],
        owner_workflow_id: str,
        owner_step_execution_id: str,
        mutation: str,
        runtime_uid: int,
        runtime_gid: int,
    ) -> dict[str, Any]:
        """Bind an explicitly granted existing workspace without copying it.

        Root containment alone never establishes permission to use a sibling
        workflow's directory: use requires a server-issued ownership/use
        grant naming this workflow as grantee, a matching owner record for
        the source workspace, and a grant claim that fences exclusive use.
        No clone, no source credential lookup, and no new workspace
        hierarchy are involved.
        """

        from moonmind.schemas.workspace_locator_models import (
            WorkspaceLocatorResolutionError,
        )

        grant = compiled_source.grant
        if grant is None:
            raise HarnessPlatformError(
                "existing-workspace source requires a server-issued grant",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        if not owner_workflow_id:
            raise HarnessPlatformError(
                "existing-workspace use requires a grantee workflow identity",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        try:
            verify_existing_workspace_grant(
                grant, grantee_workflow_id=owner_workflow_id
            )
        except WorkspaceSourceCompilationError as exc:
            raise HarnessPlatformError(
                f"existing-workspace grant is invalid: {exc}",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            ) from exc
        record_store = SandboxWorkspaceRecordStore(self._root)
        try:
            source_record = record_store.load(grant.source_workspace_id)
        except WorkspaceLocatorResolutionError as exc:
            raise HarnessPlatformError(
                f"existing-workspace source is unavailable: {exc}",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            ) from exc
        if (
            source_record is None
            or source_record.workflow_id != grant.owner_workflow_id
            or source_record.step_execution_id != grant.owner_step_execution_id
        ):
            raise HarnessPlatformError(
                "existing-workspace grant does not match the source owner record",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        authority = (self._root / "temporal_sandbox").resolve()
        candidate = (
            authority / grant.source_workspace_id / source_record.relative_path
        ).resolve()
        if candidate == self._root or not candidate.is_relative_to(self._root):
            raise HarnessPlatformError(
                "existing workspace escapes the configured workspace root",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        if not candidate.is_dir() or candidate.is_symlink():
            raise HarnessPlatformError(
                "granted existing workspace is unavailable or unsafe",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        try:
            record_store.claim_existing_workspace(grant.source_workspace_id, grant)
        except WorkspaceLocatorResolutionError as exc:
            raise HarnessPlatformError(
                f"existing workspace is already granted elsewhere: {exc}",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            ) from exc
        target_owner = f"{owner_workflow_id}:{owner_step_execution_id}"
        binding = {
            "sourceKind": "existing_workspace",
            "sourceDigest": "sha256:"
            + hashlib.sha256(f"grant:{grant.grant_id}".encode()).hexdigest(),
            "restoreContract": None,
            "restoreContractVersion": 1,
            "inputManifestDigest": compiled_source.input_manifest_digest,
            "targetOwner": target_owner,
            "attemptId": compiled_source.attempt_id,
            "generation": grant.expected_generation,
            "overlay": compiled_source.overlay,
            "grantId": grant.grant_id,
        }
        record_store.mark_materialized_for(grant.source_workspace_id, binding)
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
        read_only = grant.mode == "read_only" or mutation == "read_only"
        return {
            "kind": "bind",
            "sourceRef": str(daemon_candidate),
            "targetPath": "/workspaces/run",
            "accessMode": "read-only" if read_only else "read-write",
            "cleanupRef": None,
            "grantId": grant.grant_id,
        }

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


def _workspace_backend() -> str:
    """Map the daemon selection to the workspace-source support matrix."""

    mode = os.getenv("WORKFLOW_DOCKER_DAEMON_MODE", "").strip().lower()
    if mode == "remote":
        return "docker_remote"
    return "docker_local"


def _ready_binding_for_source(
    compiled_source: Any,
    *,
    spec: dict[str, Any],
    target_owner: str,
    observed_digest: str | None = None,
) -> dict[str, Any] | None:
    """Bind the ready marker to source/digest/contract/inputs/owner/attempt.

    Returns None when the digest cannot be known before projection (a
    checkpoint restore without a declared digest binds the observed artifact
    digest after verified download instead).
    """

    from moonmind.omnigent.workspace_sources import normalize_digest

    kind = str(compiled_source.kind)
    if kind == "existing_workspace":
        return None
    digest = observed_digest or compiled_source.expected_digest
    if kind == "repository":
        repo = str(
            spec.get("repository") or spec.get("repo") or "repository"
        ).strip()
        branch = str(spec.get("branch") or spec.get("startingBranch") or "").strip()
        digest = "sha256:" + hashlib.sha256(
            f"repository:{repo}|{branch}".encode()
        ).hexdigest()
    elif kind == "scratch":
        digest = "sha256:" + hashlib.sha256(b"scratch").hexdigest()
    if digest is None:
        return None
    return {
        "sourceKind": kind,
        "sourceDigest": normalize_digest(digest),
        "restoreContract": compiled_source.restore_contract,
        "restoreContractVersion": 1,
        "inputManifestDigest": compiled_source.input_manifest_digest,
        "targetOwner": str(target_owner or "").strip(),
        "attemptId": compiled_source.attempt_id,
        "generation": compiled_source.generation,
        "overlay": compiled_source.overlay,
    }


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
