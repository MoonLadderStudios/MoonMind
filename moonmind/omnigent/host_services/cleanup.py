"""Fenced Docker host cleanup."""

from __future__ import annotations

from typing import Any

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend

# The ephemeral host container is the only place the runner and harness log
# why a turn was dropped, rejected, or crashed. Capture a bounded tail before
# the container is removed so a provider-side failure stays diagnosable.
# ``docker logs --tail`` bounds the read by line count and the backend redacts
# credential-shaped text; the byte bound is applied here, from the *end*, so
# the retained text is always the container's newest output. A head-truncating
# read limit must not be applied upstream or the tail would be cut off.
_HOST_LOG_TAIL_LINES = 2000
_HOST_LOG_RETAINED_BYTES = 65_536
_HOST_LOG_TIMEOUT_SECONDS = 30.0
_HOST_LOG_TRUNCATION_MARKER = "[moonmind: earlier host log output truncated]\n"


class DockerOmnigentHostCleanupService:

    def __init__(self, backend: DockerCommandBackend) -> None:
        self._backend = backend

    async def _capture_host_logs(self, container_name: str) -> dict[str, Any]:
        """Return the bounded, redacted stdout/stderr tail of one host.

        Capture is evidence only: any failure is recorded in the returned
        mapping and never blocks or fails the removal that follows.
        """

        try:
            code, out, err = await self._backend.run(
                [
                    "docker",
                    "logs",
                    "--tail",
                    str(_HOST_LOG_TAIL_LINES),
                    container_name,
                ],
                check=False,
                timeout_seconds=_HOST_LOG_TIMEOUT_SECONDS,
                output_limit_bytes=None,
            )
        except (
            Exception
        ) as exc:  # noqa: BLE001 - evidence capture must not block cleanup
            return {"hostLogsCaptureError": f"{type(exc).__name__}: {exc}"[:512]}
        if code != 0:
            return {
                "hostLogsCaptureError": (
                    f"docker logs exited {code}: {(err or out).strip()[:512]}"
                )
            }
        # ``docker logs`` interleaves the container's stdout and stderr on the
        # matching CLI streams; keep both so harness stderr is not lost.
        text = (
            out
            if not err
            else f"{out}{'' if out.endswith(chr(10)) or not out else chr(10)}{err}"
        )
        encoded = text.encode("utf-8", errors="replace")
        truncated = len(encoded) > _HOST_LOG_RETAINED_BYTES
        if truncated:
            tail = encoded[-_HOST_LOG_RETAINED_BYTES:].decode("utf-8", errors="replace")
            text = _HOST_LOG_TRUNCATION_MARKER + tail
        return {
            "hostLogs": text,
            "hostLogsTruncated": truncated,
            "hostLogsTailLines": _HOST_LOG_TAIL_LINES,
        }

    async def _remove_unstarted_volume_users(self, volume_ref: str) -> None:
        """Remove never-started containers that still mount a lease volume.

        A ``docker run --rm`` writer killed by its timeout between create and
        start (for example during a host disk stall) stays Created forever:
        ``--rm`` acts only after a container exits. It carries no lease label,
        yet Docker refuses ``volume rm`` while it exists, so every cleanup
        attempt would defer and the lease would hold host capacity
        indefinitely. A never-started container runs no process, so removing
        it releases nothing live; plain ``docker rm`` refuses one that started
        in the meantime, which defers cleanup instead.
        """

        code, out, _err = await self._backend.run(
            [
                "docker",
                "ps",
                "--all",
                "--quiet",
                "--no-trunc",
                "--filter",
                f"volume={volume_ref}",
                "--filter",
                "status=created",
            ],
            check=False,
        )
        if code != 0:
            raise HarnessPlatformError(
                "host volume user inspection is deferred",
                code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
            )
        containers = out.split()
        if not containers:
            return
        code, _out, _err = await self._backend.run(
            ["docker", "rm", *containers], check=False
        )
        if code != 0:
            raise HarnessPlatformError(
                "host volume user cleanup is deferred",
                code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
            )

    async def cleanup(
        self,
        *,
        container_name: str,
        host_lease_ref: str,
        host_lease_generation: int,
        state_volume_ref: str,
        control_volume_ref: str | None = None,
    ) -> dict[str, Any]:
        host_logs: dict[str, Any] = {}
        code, out, inspect_err = await self._backend.run(
            [
                "docker",
                "container",
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
            host_logs = await self._capture_host_logs(container_name)
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
            await self._remove_unstarted_volume_users(state_volume_ref)
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
                await self._remove_unstarted_volume_users(control_volume_ref)
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
            # MoonLadderStudios/MoonMind#3881: capacity accounting releases on
            # observed evidence only. Reaching here means every probe above
            # answered, so the backend was genuinely readable; a deferred or
            # failed teardown raises instead and releases nothing.
            "daemonObserved": True,
            "containerRemoved": True,
            "stateVolumeRemoved": True,
            "controlVolumeRemoved": bool(control_volume_ref),
            "hostLeaseRef": host_lease_ref,
            "hostLeaseGeneration": host_lease_generation,
            **host_logs,
        }


__all__ = ["DockerOmnigentHostCleanupService"]
