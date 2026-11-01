"""Fenced Docker host cleanup."""

from __future__ import annotations

from typing import Any

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.omnigent.host_services.launcher import clear_runtime_capability_files

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

    def __init__(self, backend: DockerCommandBackend, *, static_hosts=None) -> None:
        self._backend = backend
        self._static_hosts = static_hosts

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

    async def cleanup(
        self,
        *,
        container_name: str,
        host_lease_ref: str,
        host_lease_generation: int,
        state_volume_ref: str,
        control_volume_ref: str | None = None,
        static_host_id: str | None = None,
        static_host_image_ref: str | None = None,
        static_previous_container_id: str | None = None,
    ) -> dict[str, Any]:
        host_logs: dict[str, Any] = {}
        ownership_format = '{{ index .Config.Labels "moonmind.host_lease_ref" }}|{{ index .Config.Labels "moonmind.host_lease_generation" }}'
        if static_host_id and static_previous_container_id:
            ownership_format = "{{.Id}}|" + ownership_format
        code, out, inspect_err = await self._backend.run(
            [
                "docker",
                "container",
                "inspect",
                "--format",
                ownership_format,
                container_name,
            ],
            check=False,
        )
        if code == 0:
            owner = out.strip()
            previous_unchanged = False
            if static_host_id and static_previous_container_id:
                container_id, _, owner = owner.partition("|")
                previous_unchanged = container_id == static_previous_container_id
            launch_not_observed = owner != f"{host_lease_ref}|{host_lease_generation}"
            # A failed launch may leave the prior resident in place. Only its
            # exact pre-launch identity can settle this unlaunched allocation;
            # mismatching successor labels and IDs still reject stale cleanup.
            if launch_not_observed and not previous_unchanged:
                raise HarnessPlatformError(
                    "stale owner cannot clean a newer Omnigent host",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            if static_host_id:
                if self._static_hosts is None:
                    raise HarnessPlatformError(
                        "static host drain is unavailable",
                        code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
                    )
                await self._static_hosts.assert_quiescent(static_host_id)
                if control_volume_ref:
                    _code, image, _error = await self._backend.run(
                        ["docker", "inspect", "--format", "{{.Image}}", container_name]
                    )
                    await clear_runtime_capability_files(
                        self._backend,
                        control_volume=control_volume_ref,
                        image_ref=image.strip(),
                    )
                return {
                    "daemonObserved": True,
                    "containerRemoved": False,
                    "stateVolumeRemoved": False,
                    "controlVolumeRemoved": False,
                    "staticHostDrained": True,
                    "staticHostLaunchNotObserved": launch_not_observed,
                    "hostLeaseRef": host_lease_ref,
                    "hostLeaseGeneration": host_lease_generation,
                    **await self._capture_host_logs(container_name),
                }
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
        if static_host_id:
            await clear_runtime_capability_files(
                self._backend,
                control_volume=control_volume_ref,
                image_ref=static_host_image_ref,
            )
            # No container means no credential consumer. Persistent state and
            # its control mount belong to this physical service, not the run.
            return {
                "daemonObserved": True,
                "containerRemoved": False,
                "stateVolumeRemoved": False,
                "staticHostDrained": True,
                "alreadyAbsent": True,
                "hostLeaseRef": host_lease_ref,
            }
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
