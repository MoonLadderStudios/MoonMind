"""Fenced Docker host cleanup."""

from __future__ import annotations

from typing import Any

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend


class DockerOmnigentHostCleanupService:
    def __init__(self, backend: DockerCommandBackend) -> None:
        self._backend = backend

    async def cleanup(
        self,
        *,
        container_name: str,
        host_lease_ref: str,
        host_lease_generation: int,
        state_volume_ref: str,
        control_volume_ref: str | None = None,
    ) -> dict[str, Any]:
        code, out, inspect_err = await self._backend.run(
            [
                "docker",
                "inspect",
                "--format",
                '{{ index .Config.Labels "moonmind.host_lease_ref" }}|{{ index .Config.Labels "moonmind.host_lease_generation" }}',
                container_name,
            ],
            check=False,
        )
        if code == 0:
            if out.strip() != f"{host_lease_ref}|{host_lease_generation}":
                raise HarnessPlatformError(
                    "stale owner cannot clean a newer Omnigent host",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            code, _out, _err = await self._backend.run(
                ["docker", "rm", "-f", container_name], check=False
            )
            if code != 0:
                raise HarnessPlatformError(
                    "host container cleanup is deferred",
                    code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
                )
        elif "no such" not in inspect_err.lower():
            raise HarnessPlatformError(
                "host container cleanup inspection is deferred",
                code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
            )
        code, out, inspect_err = await self._backend.run(
            [
                "docker",
                "volume",
                "inspect",
                "--format",
                '{{ index .Labels "moonmind.host_lease_ref" }}|{{ index .Labels "moonmind.host_lease_generation" }}',
                state_volume_ref,
            ],
            check=False,
        )
        if code == 0:
            if out.strip() != f"{host_lease_ref}|{host_lease_generation}":
                raise HarnessPlatformError(
                    "stale owner cannot clean newer Omnigent host state",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            code, _out, _err = await self._backend.run(
                ["docker", "volume", "rm", state_volume_ref], check=False
            )
            if code != 0:
                raise HarnessPlatformError(
                    "host state cleanup is deferred",
                    code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
                )
        elif "no such" not in inspect_err.lower():
            raise HarnessPlatformError(
                "host state cleanup inspection is deferred",
                code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
            )
        if control_volume_ref:
            code, out, inspect_err = await self._backend.run(
                [
                    "docker",
                    "volume",
                    "inspect",
                    "--format",
                    '{{ index .Labels "moonmind.host_lease_ref" }}|{{ index .Labels "moonmind.host_lease_generation" }}',
                    control_volume_ref,
                ],
                check=False,
            )
            if code == 0:
                if out.strip() != f"{host_lease_ref}|{host_lease_generation}":
                    raise HarnessPlatformError(
                        "stale owner cannot clean newer Omnigent host control state",
                        code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                    )
                code, _out, _err = await self._backend.run(
                    ["docker", "volume", "rm", control_volume_ref], check=False
                )
                if code != 0:
                    raise HarnessPlatformError(
                        "host control-state cleanup is deferred",
                        code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
                    )
            elif "no such" not in inspect_err.lower():
                raise HarnessPlatformError(
                    "host control-state cleanup inspection is deferred",
                    code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
                )
        return {
            "containerRemoved": True,
            "stateVolumeRemoved": True,
            "controlVolumeRemoved": bool(control_volume_ref),
            "hostLeaseRef": host_lease_ref,
            "hostLeaseGeneration": host_lease_generation,
        }


__all__ = ["DockerOmnigentHostCleanupService"]
