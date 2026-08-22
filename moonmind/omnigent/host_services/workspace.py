"""Authoritative workspace attachment resolution."""

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
        elif isinstance(locator, dict):
            relative = str(
                locator.get("relativePath") or locator.get("workspaceId") or ""
            ).strip()
            if not relative:
                raise HarnessPlatformError(
                    "sandbox workspace locator has no durable relative path",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
                )
            candidate = (self._root / relative).resolve()
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


__all__ = ["OmnigentWorkspaceMaterializer", "resolve_daemon_workspace_root"]
