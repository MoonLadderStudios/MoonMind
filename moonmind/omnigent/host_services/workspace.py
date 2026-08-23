"""Authoritative workspace attachment resolution and preparation."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Awaitable, Callable

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.runtime.workspace_locators import (
    daemon_visible_workspace_path,
)

_SAFE_VOLUME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_SAFE_CLONE_REF = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/@+-]{0,253}(?<!\.lock)$"
)
DaemonCommandRunner = Callable[[list[str]], Awaitable[tuple[int, str, str]]]


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

    async def materialize(
        self,
        request: AgentExecutionRequest,
        *,
        mutation: str = "allowed",
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
        authored = str(spec.get("workspacePath") or spec.get("path") or "").strip()
        if authored:
            candidate = Path(authored).resolve()
            preexisting_authority = True
        elif isinstance(locator, dict):
            workspace_id = str(locator.get("workspaceId") or "").strip()
            relative = str(
                locator.get("relativePath")
                or ("repo" if workspace_id else "")
            ).strip()
            if not relative:
                raise HarnessPlatformError(
                    "sandbox workspace locator has no durable relative path",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
                )
            # Sandbox workspaces are scoped by their durable workspace id so
            # concurrent runs never share one checkout directory.
            candidate = (
                (self._root / workspace_id / relative)
                if workspace_id
                else (self._root / relative)
            ).resolve()
            # Sandbox workspaces are runtime-owned: when the authoritative
            # checkout does not exist yet, this lifecycle materializes it by
            # cloning the requested repository branch with launch-time auth.
            preexisting_authority = False
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
            if preexisting_authority:
                raise HarnessPlatformError(
                    "authoritative workspace path does not exist",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
                )
            await self._prepare_sandbox_workspace(candidate, spec=spec)
        if not candidate.is_dir() or candidate.is_symlink():
            raise HarnessPlatformError(
                "authoritative workspace is unavailable or unsafe",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
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

    async def _prepare_sandbox_workspace(
        self, candidate: Path, *, spec: dict[str, Any]
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
        if clone_source is None or not branch or not _SAFE_CLONE_REF.fullmatch(branch):
            raise HarnessPlatformError(
                "sandbox workspace preparation needs a GitHub repository and safe branch ref",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        rel = candidate.relative_to(self._root)
        await self._clone_into_volume(
            rel=rel, source=clone_source, branch=branch
        )

    async def _clone_into_volume(self, *, rel: Path, source: str, branch: str) -> None:
        """Clone into the agent-workspaces volume through the Docker daemon.

        The worker image deliberately ships without a host ``git``; the same
        trusted Docker command boundary that mounts workspaces for hosts also
        performs the authenticated clone inside a disposable container.
        """

        from moonmind.workflows.temporal.runtime.git_auth import (
            build_github_token_git_environment,
        )
        from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
            resolve_github_token_for_launch,
        )

        token = await resolve_github_token_for_launch()
        env = build_github_token_git_environment(os.environ)
        argv = build_daemon_git_clone_argv(
            volume=self._workspace_volume,
            target_in_volume=rel.as_posix(),
            source=source,
            branch=branch,
            env_keys=tuple(sorted(env)),
        )
        code, _stdout, stderr = await self._runner(argv)
        if code != 0:
            detail = (stderr or "").strip()[-300:]
            raise HarnessPlatformError(
                "sandbox workspace clone failed for the requested branch"
                + (f": {detail}" if detail else ""),
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )


def build_daemon_git_clone_argv(
    *,
    volume: str,
    target_in_volume: str,
    source: str,
    branch: str,
    env_keys: tuple[str, ...],
) -> list[str]:
    """Build the docker argv for an in-volume authenticated git clone.

    Environment values are forwarded by name (``-e KEY``), so the resolved
    token never appears in process arguments or logs.
    """

    if not _SAFE_VOLUME.fullmatch(volume):
        raise HarnessPlatformError(
            "agent workspace volume name is unavailable or unsafe",
            code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
        )
    image = os.getenv("MOONMIND_WORKSPACE_GIT_IMAGE", "alpine/git:v2.43.0")
    argv: list[str] = ["docker", "run", "--rm", "-v", f"{volume}:/work"]
    for key in env_keys:
        if _SAFE_ENV_KEY.fullmatch(key):
            argv.extend(["-e", key])
    target = "/work/" + target_in_volume.lstrip("/")
    argv.extend(
        [image, "clone", "--branch", branch, "--single-branch", "--", source, target]
    )
    return argv


_SAFE_ENV_KEY = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")


def _rmdir_if_empty(path: Path) -> None:
    try:
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    except OSError:
        pass


__all__ = [
    "OmnigentWorkspaceMaterializer",
    "build_daemon_git_clone_argv",
    "normalize_github_clone_source",
    "resolve_daemon_workspace_root",
]
