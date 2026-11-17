"""Credential-only OAuth validation in the installed host image.

Credential maintenance proves a Provider Profile's OAuth home volume is still
signed in without launching an execution host: a network-less, read-only
container from the selected host image runs the harness's login-status command
against the mounted volume. ``codex login status`` only checks that tokens are
present, so the Codex login-session probe reads the same volume to prove a
ChatGPT login has ended, which only a new sign-in can fix.

The validator container carries the validation lease's labels, so cleanup is
ownership-scoped through :class:`LegacyOmnigentHostContainerService`.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from typing import Any

from moonmind.omnigent.codex_login import CODEX_LOGIN_SESSION_PROBE
from moonmind.omnigent.execution_profiles import validate_effective_launch_snapshot
from moonmind.omnigent.host_failures import OmnigentOAuthHostError
from moonmind.omnigent.host_services.legacy_host_containers import (
    LegacyOmnigentHostContainerService,
    RuntimeCommand,
)
from moonmind.omnigent.oauth_hosts import (
    HostPreflightFailure,
    deterministic_host_container_name,
)
from moonmind.schemas.agent_runtime_models import (
    OmnigentHostLease,
    OmnigentOAuthHostBinding,
)
from moonmind.workflows.temporal.runtime.command_runner import run_runtime_command
from moonmind.workloads.docker_launcher import structured_container_security_args

logger = logging.getLogger(__name__)

# Credential variables the validator must never inherit from the image.
_FORBIDDEN_ENV = (
    "OPENAI_API_KEY",
    "CODEX_ACCESS_TOKEN",
    "OPENAI_BASE_URL",
    "MINIMAX_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
)
_RUNTIME_ADAPTERS: dict[str, dict[str, Any]] = {
    "codex_cli": {
        "harness": "codex-native",
        "home": "/home/app/.codex",
        "generation_env": "CODEX_CREDENTIAL_GENERATION",
        "login_command": ("codex", "login", "status"),
        "env": (
            "MOONMIND_OMNIGENT_RUNTIME_PACK_REF=codex-native-pack@1",
            "MOONMIND_OMNIGENT_CREDENTIAL_MATERIALIZER_REF=codex-oauth-home@1",
            "CODEX_HOME=/home/app/.codex",
            "CODEX_CONFIG_HOME=/home/app/.codex",
            "CODEX_CONFIG_PATH=/home/app/.codex/config.toml",
            "CODEX_VOLUME_PATH=/home/app/.codex",
        ),
    },
    "claude_code": {
        "harness": "claude-native",
        "home": "/home/app/.claude",
        "generation_env": "CLAUDE_CREDENTIAL_GENERATION",
        "login_command": ("claude", "auth", "status"),
        "env": (
            "MOONMIND_OMNIGENT_RUNTIME_PACK_REF=claude-native-pack@1",
            "MOONMIND_OMNIGENT_CREDENTIAL_MATERIALIZER_REF=claude-oauth-home@1",
            "CLAUDE_HOME=/home/app/.claude",
            "CLAUDE_CONFIG_DIR=/home/app/.claude",
            "CLAUDE_VOLUME_PATH=/home/app/.claude",
        ),
    },
}
_DIGEST_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_PLACEHOLDER_DIGEST = "0" * 64
_SAFE_NETWORK = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_TOOLS_PATH = "/opt/moonmind-tools/bin"
_DEFAULT_HOST_PATH = (
    "/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin"
)
_REQUIRED_LIMITS = frozenset(
    {"cpuMillis", "memoryMiB", "processes", "timeoutSeconds", "temporaryStorageMiB"}
)
_ALLOWED_MOUNTS = frozenset(
    {"workspace", "oauth_home", "omnigent_state", "skills_tools", "artifacts", "cache"}
)


async def run_host_command(
    *args: str,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    input_bytes: bytes | None = None,
) -> tuple[int, str, str]:
    """Run one bounded host command and decode its output."""

    return_code, stdout, stderr = await run_runtime_command(
        args,
        env=env,
        input_bytes=input_bytes,
        timeout_seconds=600,
        output_limit_bytes=4096,
    )
    output = stdout.decode("utf-8", errors="replace")
    error = stderr.decode("utf-8", errors="replace")
    if check and return_code != 0:
        detail = (error or output).strip()[:350]
        raise OmnigentOAuthHostError(
            f"OAuth host command failed (exit {return_code})"
            + (f": {detail}" if detail else ""),
            code=HostPreflightFailure.LOGIN_STATUS_FAILED.value,
        )
    return return_code, output, error


def runtime_adapter(binding: OmnigentOAuthHostBinding) -> Mapping[str, Any]:
    runtime_id = binding.credential_mount_ref.auth_volume_ref.runtime_id
    adapter = _RUNTIME_ADAPTERS.get(runtime_id)
    if adapter is None or adapter["harness"] != binding.harness:
        raise OmnigentOAuthHostError(
            "OAuth runtime and harness binding are incompatible",
            code=HostPreflightFailure.BINDING_MISMATCH.value,
        )
    return adapter


def validate_effective_launch(
    *,
    binding: OmnigentOAuthHostBinding,
    effective_launch: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return the binding's launch policy once it is realizable on demand."""

    if not binding.host_launch_profile_ref:
        # Static-connected Compose hosts were removed, so a binding still in
        # that mode has no host image to validate in until it migrates.
        raise OmnigentOAuthHostError(
            "static-connected Omnigent hosts were removed; this OAuth host "
            "binding moves to an on-demand host when its Provider Profile "
            "is idle",
            code="OMNIGENT_STATIC_HOST_RETIRED",
        )
    if not isinstance(effective_launch, Mapping):
        raise OmnigentOAuthHostError(
            "effective launch policy is required before host mutation",
            code="OMNIGENT_EFFECTIVE_LAUNCH_REQUIRED",
        )
    launch = dict(effective_launch)
    validate_effective_launch_snapshot(launch)
    if launch.get("hostMode") != "on_demand_docker":
        raise OmnigentOAuthHostError(
            "effective launch host mode conflicts with the durable binding",
            code="OMNIGENT_LAUNCH_POLICY_BINDING_CONFLICT",
        )
    if not str(launch.get("snapshotRef") or "").startswith("omnigent-launch:sha256:"):
        raise OmnigentOAuthHostError(
            "effective launch snapshot reference is invalid",
            code="OMNIGENT_EFFECTIVE_LAUNCH_INVALID",
        )
    for field in ("hostImageRef", "serverImageRef"):
        image_ref = str(launch.get(field) or "")
        if not _DIGEST_IMAGE.fullmatch(image_ref) or image_ref.endswith(
            _PLACEHOLDER_DIGEST
        ):
            raise OmnigentOAuthHostError(
                f"{'host' if field == 'hostImageRef' else 'server'} image must "
                "be an immutable sha256 reference",
                code="OMNIGENT_LAUNCH_IMAGE_UNREALIZABLE",
            )
    if not _SAFE_NETWORK.fullmatch(str(launch.get("networkRef") or "")):
        raise OmnigentOAuthHostError(
            "network must be a named deployment network",
            code="OMNIGENT_LAUNCH_NETWORK_UNREALIZABLE",
        )
    limits = launch.get("limits")
    if (
        not isinstance(limits, Mapping)
        or set(limits) != _REQUIRED_LIMITS
        or any(
            not isinstance(limits[key], int) or limits[key] <= 0
            for key in _REQUIRED_LIMITS
        )
    ):
        raise OmnigentOAuthHostError(
            "launch resource limits are incomplete or invalid",
            code="OMNIGENT_LAUNCH_RESOURCES_UNREALIZABLE",
        )
    mount_classes = set(launch.get("mountClasses") or ())
    if not mount_classes <= _ALLOWED_MOUNTS or "oauth_home" not in mount_classes:
        raise OmnigentOAuthHostError(
            "launch mount classes cannot be realized by the OAuth host",
            code="OMNIGENT_LAUNCH_MOUNTS_UNREALIZABLE",
        )
    if not launch.get("enforcedEgress"):
        raise OmnigentOAuthHostError(
            "launch policy must enforce egress",
            code="OMNIGENT_LAUNCH_EGRESS_UNREALIZABLE",
        )
    if launch.get("runtimeUid") != 1000 or launch.get("runtimeGid") != 1000:
        raise OmnigentOAuthHostError(
            "OAuth host UID/GID policy is unrealizable",
            code="OMNIGENT_LAUNCH_IDENTITY_UNREALIZABLE",
        )
    if launch.get("readOnlyRoot") is not True:
        raise OmnigentOAuthHostError(
            "OAuth host policy must require a read-only root filesystem",
            code="OMNIGENT_LAUNCH_ROOT_UNREALIZABLE",
        )
    capture = launch.get("capture")
    if (
        not isinstance(capture, Mapping)
        or capture.get("required") is not True
        or not isinstance(capture.get("retentionDays"), int)
        or capture["retentionDays"] <= 0
    ):
        raise OmnigentOAuthHostError(
            "launch capture and retention policy is unrealizable",
            code="OMNIGENT_LAUNCH_CAPTURE_UNREALIZABLE",
        )
    cleanup = launch.get("cleanup")
    if (
        not isinstance(cleanup, Mapping)
        or cleanup.get("mode") != "remove"
        or cleanup.get("janitor") is not True
    ):
        raise OmnigentOAuthHostError(
            "launch cleanup and janitor policy is unrealizable",
            code="OMNIGENT_LAUNCH_CLEANUP_UNREALIZABLE",
        )
    if set(launch.get("controlCapabilities") or ()) != {
        "interrupt",
        "terminate",
        "clear_context",
    }:
        raise OmnigentOAuthHostError(
            "launch control capabilities are unrealizable",
            code="OMNIGENT_LAUNCH_CONTROLS_UNREALIZABLE",
        )
    return launch


class OmnigentOAuthCredentialValidator:
    """Validate an OAuth home volume in its host image without launching work."""

    def __init__(self, *, run_command: RuntimeCommand = run_host_command) -> None:
        self._run = run_command
        self._containers = LegacyOmnigentHostContainerService(run_command=run_command)

    async def validate_credential_mount(
        self,
        *,
        binding: OmnigentOAuthHostBinding,
        host_lease: OmnigentHostLease,
        effective_launch: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Validate OAuth in the selected host image without launching execution."""

        launch = validate_effective_launch(
            binding=binding, effective_launch=effective_launch
        )
        adapter = runtime_adapter(binding)
        auth_volume = binding.credential_mount_ref.auth_volume_ref
        if (
            host_lease.provider_profile_id != binding.provider_profile_id
            or host_lease.binding_ref != binding.binding_ref
            or host_lease.credential_generation != auth_volume.credential_generation
            or launch.get("providerRuntime") not in {None, auth_volume.runtime_id}
            or launch.get("harness") != adapter["harness"]
        ):
            raise OmnigentOAuthHostError(
                "credential validation authority does not match the OAuth host binding",
                code=HostPreflightFailure.BINDING_MISMATCH.value,
            )

        container_name = host_lease.container_name or deterministic_host_container_name(
            host_lease.lease_id
        )
        if await self._containers.container_present(container_name):
            await self._containers.assert_container_owned(
                container_name=container_name, lease_id=host_lease.lease_id
            )
            await self._run("docker", "rm", "-f", container_name, check=False)
            if await self._containers.container_present(container_name):
                raise OmnigentOAuthHostError(
                    "prior OAuth credential validator could not be removed",
                    code="OMNIGENT_HOST_CLEANUP_INCOMPLETE",
                )

        host_image_ref = str(launch["hostImageRef"])
        host_path = await self._discover_upstream_path(host_image_ref)
        args = [
            "docker",
            "run",
            "--name",
            container_name,
            "--label",
            "moonmind.kind=omnigent-oauth-credential-validator",
            "--label",
            f"moonmind.provider_profile_id={binding.provider_profile_id}",
            "--label",
            f"moonmind.host_lease_id={host_lease.lease_id}",
            "--label",
            f"moonmind.credential_generation={host_lease.credential_generation}",
            "--user",
            f"{launch['runtimeUid']}:{launch['runtimeGid']}",
            "--workdir",
            "/home/app",
            "--network",
            "none",
            *structured_container_security_args(),
            "--cpus",
            str(int(launch["limits"]["cpuMillis"]) / 1000),
            "--memory",
            f"{launch['limits']['memoryMiB']}m",
            "--pids-limit",
            str(launch["limits"]["processes"]),
            "--read-only",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,size={launch['limits']['temporaryStorageMiB']}m",
            "--mount",
            f"type=volume,src={auth_volume.volume_ref},dst={adapter['home']},readonly",
            "--env",
            f"PATH={_prepend_tools_path(host_path)}",
            "--env",
            "HOME=/home/app",
            "--env",
            f"{adapter['generation_env']}={host_lease.credential_generation}",
        ]
        for runtime_env in adapter["env"]:
            args.extend(["--env", runtime_env])
        # Docker options end at the image. The following -u flags belong to
        # /usr/bin/env, where they remove inherited credential variables.
        args.extend(["--entrypoint", "/usr/bin/env", host_image_ref])
        for key in _FORBIDDEN_ENV:
            args.extend(["-u", key])
        args.extend(adapter["login_command"])
        try:
            result = await asyncio.wait_for(self._run(*args, check=False), timeout=60)
        except TimeoutError as exc:
            raise OmnigentOAuthHostError(
                "OAuth credential validation timed out",
                code=HostPreflightFailure.VALIDATION_UNAVAILABLE.value,
            ) from exc
        if result[0] != 0:
            logger.warning(
                "OAuth credential preflight exited %d for profile %s",
                result[0],
                binding.provider_profile_id,
            )
            raise OmnigentOAuthHostError(
                (
                    "OAuth credential validation failed"
                    if result[0] == 1
                    else "OAuth credential validation could not run"
                ),
                code=(
                    HostPreflightFailure.LOGIN_STATUS_FAILED.value
                    if result[0] == 1
                    else HostPreflightFailure.VALIDATION_UNAVAILABLE.value
                ),
            )
        if (
            await self.probe_login_session(binding=binding, effective_launch=launch)
            == "ended"
        ):
            raise OmnigentOAuthHostError(
                "The stored ChatGPT login has ended and cannot be refreshed; "
                "re-authenticate the Provider Profile.",
                code=HostPreflightFailure.LOGIN_STATUS_FAILED.value,
            )
        return {
            "status": "ready",
            "providerProfileId": binding.provider_profile_id,
            "runtimeId": auth_volume.runtime_id,
            "credentialGeneration": host_lease.credential_generation,
            "loginStatus": "authenticated",
            "validationMode": "credential_only",
        }

    async def probe_login_session(
        self,
        *,
        binding: OmnigentOAuthHostBinding,
        effective_launch: Mapping[str, Any] | None,
    ) -> str:
        """Report whether the profile's stored Codex login session has ended.

        An access token that merely aged out proves nothing (Codex may refresh
        it once started), but OpenAI refreshing an ended session mints a token
        with no lifetime, which is provider-issued proof that only a new
        sign-in can help. Returns ``ended``, ``active``, or ``unknown``; the
        network-less, read-only container prints only that verdict, never
        token material.
        """

        if binding.credential_mount_ref.auth_volume_ref.runtime_id != "codex_cli":
            return "unknown"
        try:
            launch = validate_effective_launch(
                binding=binding, effective_launch=effective_launch
            )
            adapter = runtime_adapter(binding)
            volume = binding.credential_mount_ref.auth_volume_ref.volume_ref
            returncode, stdout, _stderr = await asyncio.wait_for(
                self._run(
                    "docker",
                    "run",
                    "--rm",
                    "--user",
                    f"{launch['runtimeUid']}:{launch['runtimeGid']}",
                    "--network",
                    "none",
                    *structured_container_security_args(),
                    "--read-only",
                    "--mount",
                    f"type=volume,src={volume},dst={adapter['home']},readonly",
                    "--entrypoint",
                    "python3",
                    str(launch["hostImageRef"]),
                    "-c",
                    CODEX_LOGIN_SESSION_PROBE,
                    str(adapter["home"]),
                    check=False,
                ),
                timeout=60,
            )
        except Exception:
            # Best effort: an unavailable probe must not replace the login
            # status result with a credential verdict it could not prove.
            logger.warning(
                "Codex login session probe unavailable for profile %s",
                binding.provider_profile_id,
                exc_info=True,
            )
            return "unknown"
        verdict = stdout.strip()
        if returncode != 0 or verdict not in {"ended", "active"}:
            return "unknown"
        return verdict

    async def stop_host(
        self, *, binding: Any, host_lease: OmnigentHostLease
    ) -> dict[str, Any]:
        """Remove the validator container owned by ``host_lease``."""

        return await self._containers.stop_host(binding=binding, host_lease=host_lease)

    async def _discover_upstream_path(self, image_ref: str) -> str:
        """Read the selected image's PATH without replacing image-specific entries."""

        result = await self._run(
            "docker",
            "image",
            "inspect",
            "--format",
            "{{range .Config.Env}}{{println .}}{{end}}",
            image_ref,
            check=False,
        )
        if result[0] == 0:
            for line in result[1].splitlines():
                if line.startswith("PATH="):
                    return line.removeprefix("PATH=") or _DEFAULT_HOST_PATH
        return _DEFAULT_HOST_PATH


def _prepend_tools_path(upstream_path: str) -> str:
    entries = [entry for entry in upstream_path.split(":") if entry]
    return ":".join([_TOOLS_PATH, *(e for e in entries if e != _TOOLS_PATH)])


__all__ = [
    "OmnigentOAuthCredentialValidator",
    "run_host_command",
    "runtime_adapter",
    "validate_effective_launch",
]
