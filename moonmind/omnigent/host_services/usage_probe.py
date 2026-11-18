"""Bounded idle usage probe in the approved runtime image.

An admitted usage observation runs one short-lived container from the
installed host image with the profile's enrolled credential home mounted at
its exact target path: read-only unless the adapter's native recovery action
must persist rotated tokens. The container joins the restricted Omnigent
egress network, carries the observation lease's labels, and kills itself
before the lease lifetime ends, so a lost worker cannot leave it reading or
rotating credentials. It is not an agent turn or an execution session.

Unlike :mod:`oauth_credential_validation` (network-less, read-only), this
probe needs provider egress, so it is a separate operation.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

from moonmind.omnigent.credential_materializers import ProfileCredentialHome
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.provider_profiles.usage_collection import (
    MAX_PROBE_OUTPUT_BYTES,
    UsageProbePlan,
)
from moonmind.security.egress import (
    OMNIGENT_EGRESS_PROFILE,
    attest_docker_egress,
    omnigent_proxy_env,
)
from moonmind.workloads.docker_launcher import structured_container_security_args

PROBE_KIND_LABEL = "moonmind.kind=provider-account-usage-probe"
_DIGEST_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_SAFE_PATH = re.compile(r"^/[A-Za-z0-9._/-]{1,255}$")
_SAFE_VOLUME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
# Credential variables the probe must never inherit from the image.
_FORBIDDEN_ENV = (
    "OPENAI_API_KEY",
    "CODEX_ACCESS_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
)


def probe_container_name(lease_id: str) -> str:
    """Deterministic per-grant name, so retries and cleanup find one container."""

    digest = hashlib.sha256(lease_id.encode("utf-8")).hexdigest()[:24]
    return f"mm-usage-probe-{digest}"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise HarnessPlatformError(
            message,
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE,
        )


class UsageProbeRunner:
    def __init__(self, backend: DockerCommandBackend | None = None) -> None:
        self._backend = backend or DockerCommandBackend()

    async def run(
        self,
        *,
        lease_id: str,
        profile_id: str,
        credential_generation: int,
        launch: Mapping[str, Any],
        home: ProfileCredentialHome,
        plan: UsageProbePlan,
        exclusive_grant: bool,
    ) -> tuple[int, bytes]:
        """Run one probe and return its exit code and bounded stdout."""

        image = str(launch.get("hostImageRef") or "")
        limits = launch.get("limits") or {}
        _require(
            bool(_DIGEST_IMAGE.fullmatch(image)), "probe image must be digest-pinned"
        )
        _require(
            launch.get("runtimeUid") == 1000 and launch.get("runtimeGid") == 1000,
            "probe runtime identity is unrealizable",
        )
        _require(
            all(
                isinstance(limits.get(key), int) and limits[key] > 0
                for key in (
                    "cpuMillis",
                    "memoryMiB",
                    "processes",
                    "temporaryStorageMiB",
                )
            ),
            "probe resource limits are incomplete",
        )
        _require(
            bool(_SAFE_VOLUME.fullmatch(home.volume_ref)), "credential home is invalid"
        )
        _require(
            bool(_SAFE_PATH.fullmatch(home.target_path)), "credential path is invalid"
        )
        # A writable home is authority to rotate tokens; only the exclusive
        # idle grant carries it.
        _require(
            not plan.writes_credentials or exclusive_grant,
            "credential recovery requires the exclusive idle grant",
        )

        async def egress_runner(args):
            code, out, err = await self._backend.run(["docker", *args], check=False)
            return code, out.encode(), err.encode()

        try:
            await attest_docker_egress(
                runner=egress_runner,
                profile=OMNIGENT_EGRESS_PROFILE,
                backend_ref="provider-account-usage-probe",
            )
        except RuntimeError as exc:
            raise HarnessPlatformError(
                f"restricted-egress backend attestation failed: {exc}",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            ) from exc

        mount = f"type=volume,src={home.volume_ref},dst={home.target_path}"
        if not plan.writes_credentials:
            mount += ",readonly"
        argv = [
            "docker",
            "run",
            "--rm",
            "--name",
            probe_container_name(lease_id),
            "--label",
            PROBE_KIND_LABEL,
            "--label",
            f"moonmind.provider_profile_id={profile_id}",
            "--label",
            f"moonmind.credential_generation={credential_generation}",
            "--user",
            "1000:1000",
            "--workdir",
            "/home/app",
            "--network",
            OMNIGENT_EGRESS_PROFILE.network_ref,
            *structured_container_security_args(),
            "--cpus",
            str(int(limits["cpuMillis"]) / 1000),
            "--memory",
            f"{limits['memoryMiB']}m",
            "--pids-limit",
            str(limits["processes"]),
            "--read-only",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,nodev,size={limits['temporaryStorageMiB']}m",
            "--tmpfs",
            "/home/app:rw,nosuid,nodev,uid=1000,gid=1000",
            "--mount",
            mount,
            "--env",
            "HOME=/home/app",
        ]
        for value in (*omnigent_proxy_env(), *plan.env):
            argv.extend(["--env", value])
        argv.extend(["--entrypoint", "/usr/bin/env", image])
        for key in _FORBIDDEN_ENV:
            argv.extend(["-u", key])
        # Killed from inside, so a lost worker cannot leave it running.
        argv.extend(["timeout", "-s", "KILL", str(plan.timeout_seconds), *plan.argv])
        code, stdout, _stderr = await self._backend.run(
            argv,
            timeout_seconds=plan.timeout_seconds + 15,
            check=False,
            output_limit_bytes=MAX_PROBE_OUTPUT_BYTES,
        )
        return code, stdout.encode("utf-8")

    async def cleanup(self, lease_id: str) -> dict[str, Any]:
        """Remove the grant's probe container and prove it is gone."""

        name = probe_container_name(lease_id)
        await self._backend.run(
            ["docker", "rm", "-f", name],
            check=False,
            timeout_seconds=60,
            failure_code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
        )
        code, out, _err = await self._backend.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"name=^{name}$",
                "--format",
                "{{.Names}}",
            ],
            check=False,
            timeout_seconds=60,
            failure_code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
        )
        if code != 0 or name in out.split():
            raise HarnessPlatformError(
                "usage probe container cleanup could not be verified",
                code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
            )
        return {"containerName": name, "removed": True}


__all__ = ["PROBE_KIND_LABEL", "UsageProbeRunner", "probe_container_name"]
