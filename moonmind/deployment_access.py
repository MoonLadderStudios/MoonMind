"""Read-only access preservation gate shared by deployment entrypoints.

Uses only the standard library so host update scripts can run this file directly.
Never prints rendered environment variables or Docker inspect payloads.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence


class DeploymentAccessError(RuntimeError):
    """An update cannot prove preservation of the installed access boundary."""


ACCESS_SETTINGS = (
    "AUTH_PROVIDER",
    "MOONMIND_API_PUBLISH_HOST",
    "MOONMIND_TRUSTED_INGRESS",
    "MOONMIND_PUBLIC_BASE_URL",
    "MOONMIND_TRUSTED_PROXIES",
    "MOONMIND_CORS_ALLOWED_ORIGINS",
)


def _bindings(rows: Sequence[tuple[str, str, str]]) -> set[tuple[str, str, str]]:
    # Empty HostIp means Docker chooses the published interfaces. Keep it
    # distinct from an explicit IPv4 bind: collapsing them can drop IPv6.
    return {(host or "*", port, target) for host, port, target in rows}


def validate_access(candidate: Mapping, containers: Sequence[Mapping]) -> None:
    """Compare rendered Compose with installed containers, including stopped ones."""
    api = candidate.get("services", {}).get("api")
    for container in containers:
        if api is None:
            raise DeploymentAccessError(
                "The candidate removes the installed API service."
            )
        previous = _bindings(
            [
                (binding.get("HostIp", ""), str(binding["HostPort"]), target)
                for target, bindings in (
                    container["HostConfig"]["PortBindings"] or {}
                ).items()
                for binding in (bindings or [])
            ]
        )
        proposed = _bindings(
            [
                (
                    port.get("host_ip", ""),
                    str(port.get("published", "")),
                    f"{port['target']}/{port.get('protocol', 'tcp')}",
                )
                for port in api.get("ports", [])
            ]
        )
        changed = []
        if previous != proposed:
            changed.append("published interfaces/ports")
        old_env = dict(
            entry.split("=", 1) for entry in (container["Config"]["Env"] or [])
        )
        new_env = api.get("environment") or {}
        for name in ACCESS_SETTINGS:
            if (old_env.get(name) or "") != (new_env.get(name) or ""):
                changed.append(name)
        if changed:
            raise DeploymentAccessError(
                "Update would change installed API access: "
                + ", ".join(changed)
                + ". Preserve the running bindings and access settings in the deployment-owned "
                ".env/override, then rerun. An intentional access migration must be applied "
                "separately and verified through the operator URL before updating."
            )


def check_compose_access(
    compose: Sequence[str],
    *,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    """Inspect the same Docker context and Compose inputs used by the updater."""

    def run(command: Sequence[str]) -> str:
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DeploymentAccessError(
                "Could not inspect deployment access; API replacement stopped."
            ) from exc
        if result.returncode:
            raise DeploymentAccessError(
                "Could not inspect deployment access; API replacement stopped."
            )
        return result.stdout

    try:
        candidate = json.loads(run([*compose, "config", "--format", "json"]))
        project = candidate["name"]
        if not isinstance(project, str) or not project.strip():
            raise ValueError("Missing Compose project identity")
        ids = run(
            [
                "docker",
                "ps",
                "-a",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--filter",
                "label=com.docker.compose.service=api",
                "--filter",
                "label=com.docker.compose.oneoff=False",
            ]
        ).split()
        if ids:
            containers = json.loads(run(["docker", "inspect", *ids]))
            if not isinstance(containers, list) or len(containers) != len(ids):
                raise ValueError("Incomplete container inspection")
            validate_access(candidate, containers)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise DeploymentAccessError(
            "Invalid deployment access evidence; API replacement stopped."
        ) from exc


if __name__ == "__main__":
    try:
        check_compose_access(sys.argv[1:] or ["docker", "compose"], env=os.environ)
    except DeploymentAccessError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print("Deployment API access preflight passed.")
