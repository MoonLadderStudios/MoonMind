"""Secret-free launch spec and Docker Omnigent host launcher."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.host_classes import HostClass, LaunchPolicy
from moonmind.omnigent.host_ports import HostLaunchSpec, host_correlation_identity
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.omnigent.host_services.runtime_scripts import OmnigentRuntimeScriptService
from moonmind.security.egress import omnigent_proxy_env


class DockerOmnigentHostLauncher:
    def __init__(
        self,
        *,
        backend: DockerCommandBackend,
        runtime_scripts: OmnigentRuntimeScriptService,
        server_url: str | None = None,
        host_api_token: str | None = None,
    ) -> None:
        self._backend = backend
        self._scripts = runtime_scripts
        self._host_api_token = str(host_api_token or "")
        self._server_url = str(
            server_url or os.environ.get("MOONMIND_OMNIGENT_HOST_SERVER_URL") or ""
        ).strip()
        if not self._server_url:
            raise HarnessPlatformError(
                "MOONMIND_OMNIGENT_HOST_SERVER_URL is required for generic hosts",
                code=HarnessPlatformFailure.OMNIGENT_GENERIC_REALIZER_NOT_READY,
            )
        parsed = urlsplit(self._server_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise HarnessPlatformError(
                "generic host server URL must be a credential-free HTTP(S) origin",
                code=HarnessPlatformFailure.OMNIGENT_GENERIC_REALIZER_NOT_READY,
            )

    @property
    def server_url(self) -> str:
        return self._server_url

    def control_attachment(
        self,
        host_lease_ref: str,
        *,
        require_capability_mount: bool = False,
    ) -> dict[str, Any] | None:
        if not self._host_api_token and not require_capability_mount:
            return None
        digest = hashlib.sha256(host_lease_ref.encode("utf-8")).hexdigest()[:32]
        return {
            "kind": "volume",
            "sourceRef": f"mm-omnigent-control-{digest}",
            "targetPath": "/run/moonmind-host-auth",
            "accessMode": "read-only",
        }

    async def launch(
        self,
        *,
        spec: HostLaunchSpec,
        host_class: HostClass,
        launch_policy: LaunchPolicy,
        credential_handles: list[dict[str, Any]],
        runtime_environment: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        if spec.serverUrl != self._server_url:
            raise HarnessPlatformError(
                "HostLaunchSpec endpoint does not match launcher configuration",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        container_name = spec.correlationName
        state_volume = str(spec.stateAttachment["sourceRef"])
        control_volume = (
            str(spec.controlAttachment["sourceRef"])
            if spec.controlAttachment is not None
            else ""
        )
        supplied_runtime_environment = dict(runtime_environment or {})
        fanout_bearer = str(
            supplied_runtime_environment.pop(
                "MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN", ""
            )
            or ""
        ).strip()
        allowed_runtime_environment = {
            "MOONMIND_URL",
            "MOONMIND_AGENT_RUN_ID",
            "MOONMIND_TASK_WORKFLOW_ID",
            "MOONMIND_STEP_ID",
            "MOONMIND_RUNTIME_ID",
            "MOONMIND_REPOSITORY_CONNECTION_REF",
        }
        if set(supplied_runtime_environment) - allowed_runtime_environment:
            raise HarnessPlatformError(
                "generic host received unsupported runtime environment names",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        if fanout_bearer and not control_volume:
            raise HarnessPlatformError(
                "execution fan-out requires a lease-owned capability mount",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        await self._backend.run(
            [
                "docker",
                "volume",
                "create",
                "--label",
                "moonmind.owner=generic-omnigent-host",
                "--label",
                f"moonmind.host_lease_ref={spec.hostLeaseRef}",
                "--label",
                f"moonmind.host_lease_generation={spec.hostLeaseGeneration}",
                state_volume,
            ]
        )
        try:
            if control_volume:
                await self._backend.run(
                    [
                        "docker",
                        "volume",
                        "create",
                        "--label",
                        "moonmind.owner=generic-omnigent-host",
                        "--label",
                        f"moonmind.host_lease_ref={spec.hostLeaseRef}",
                        "--label",
                        f"moonmind.host_lease_generation={spec.hostLeaseGeneration}",
                        control_volume,
                    ]
                )
                if self._host_api_token:
                    await self._backend.run(
                        [
                            "docker",
                            "run",
                            "--rm",
                            "-i",
                            "--user",
                            "0:0",
                            "--network",
                            "none",
                            "--mount",
                            f"type=volume,src={control_volume},dst=/control",
                            "--entrypoint",
                            "/bin/sh",
                            host_class.imageRef,
                            "-ceu",
                            "umask 077; cat > /control/api-token; chown 1000:1000 /control/api-token; chmod 0400 /control/api-token",
                        ],
                        input_bytes=self._host_api_token.encode("utf-8"),
                    )
                if fanout_bearer:
                    await self._backend.run(
                        [
                            "docker",
                            "run",
                            "--rm",
                            "-i",
                            "--user",
                            "0:0",
                            "--network",
                            "none",
                            "--mount",
                            f"type=volume,src={control_volume},dst=/control",
                            "--entrypoint",
                            "/bin/sh",
                            host_class.imageRef,
                            "-ceu",
                            "umask 077; cat > /control/execution-fanout; chown 1000:1000 /control/execution-fanout; chmod 0400 /control/execution-fanout",
                        ],
                        input_bytes=fanout_bearer.encode("utf-8"),
                    )
            # Initialize the writable host-state volume before a read-only-root launch.
            await self._backend.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--user",
                    "0:0",
                    "--network",
                    "none",
                    "--mount",
                    f"type=volume,src={state_volume},dst=/state",
                    "--entrypoint",
                    "/bin/sh",
                    host_class.imageRef,
                    "-ceu",
                    "chown 1000:1000 /state; chmod 0700 /state",
                ]
            )
        except BaseException:
            if control_volume:
                await self._backend.run(
                    ["docker", "volume", "rm", control_volume], check=False
                )
            await self._backend.run(
                ["docker", "volume", "rm", state_volume], check=False
            )
            raise
        if fanout_bearer:
            supplied_runtime_environment[
                "MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN_FILE"
            ] = "/run/moonmind-host-auth/execution-fanout"
        script, runtime_environment = self._scripts.build_entrypoint(
            credential_handles=credential_handles,
            skill_attachment=spec.skillAttachment,
            step_execution_id=spec.stepExecutionId,
            tool_attachments=spec.toolAttachments,
            github_credential_attachment=spec.githubCredentialAttachment,
            control_attachment=spec.controlAttachment,
            control_credential_available=bool(self._host_api_token),
            enable_opencode_runtime=any(
                item.harnessId == "opencode-native"
                for item in host_class.declaredHarnessImplementations
            ),
            runtime_environment=supplied_runtime_environment,
        )
        command = [
            "docker",
            "create",
            "--name",
            container_name,
            "--hostname",
            container_name,
            "--network",
            spec.networkRef,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(launch_policy.limits["processes"]),
            "--memory",
            f"{launch_policy.limits['memoryMiB']}m",
            "--cpus",
            str(launch_policy.limits["cpuMillis"] / 1000),
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,nodev,size={launch_policy.limits['temporaryStorageMiB']}m",
            # Harness CLIs need a writable HOME (~/.local, ~/.config etc).
            # The image's /home/app is root-owned; a tmpfs here gives the
            # runtime user a writable base while credential and state
            # volumes mount over their exact subdirectories.
            "--tmpfs",
            f"/home/app:rw,nosuid,nodev,size=256m,uid={host_class.runtime.get('uid', 1000)},gid={host_class.runtime.get('gid', 1000)}",
            "--user",
            f"{host_class.runtime.get('uid', 1000)}:{host_class.runtime.get('gid', 1000)}",
        ]
        for key, value in sorted(spec.labels.items()):
            command.extend(["--label", f"{key}={value}"])
        environment = {
            **runtime_environment,
            "OMNIGENT_HOST_NAME": container_name,
        }
        # The upstream Omnigent CLI treats managed-host launches as
        # (host_id, host_name) pairs: setting only one crashes identity
        # loading. Use the pure spec identity when present; old specs
        # without it fall back to the legacy per-lease derivation so
        # in-flight hosts launched before the identity contract keep
        # their addressable ID across Activity retry.
        if getattr(spec, "expectedOmnigentHostId", None):
            environment["OMNIGENT_HOST_ID"] = str(spec.expectedOmnigentHostId)
        else:
            import uuid as _uuid

            environment["OMNIGENT_HOST_ID"] = str(
                _uuid.uuid5(_uuid.NAMESPACE_URL, spec.hostLeaseRef)
            )
        # The root filesystem is read-only; HOME must point at the image's
        # app home so both the Omnigent CLI (~/.omnigent backed by the state
        # volume) and harness credentials (~/.local/share/opencode/auth.json
        # backed by credential volumes) resolve inside writable mounts.
        # Without an explicit HOME the Python CLI resolves ~ to "/" and
        # crashes trying to mkdir /.omnigent on the read-only root.
        environment["HOME"] = "/home/app"
        for value in omnigent_proxy_env():
            key, _, item = value.partition("=")
            environment[key] = item
        for key, value in sorted(environment.items()):
            command.extend(["--env", f"{key}={value}"])
        attachments = [
            spec.workspaceAttachment,
            spec.skillAttachment,
            *spec.toolAttachments,
            *spec.credentialAttachments,
            *(
                [spec.githubCredentialAttachment]
                if spec.githubCredentialAttachment is not None
                else []
            ),
            *([spec.controlAttachment] if spec.controlAttachment is not None else []),
            spec.stateAttachment,
        ]
        for attachment in attachments:
            kind = str(attachment["kind"])
            source = str(attachment["sourceRef"])
            target = str(attachment["targetPath"])
            readonly = str(attachment.get("accessMode")) == "read-only"
            mount = f"type={kind},src={source},dst={target}"
            if readonly:
                mount += ",readonly"
            command.extend(["--mount", mount])
        command.extend(
            [
                "--entrypoint",
                "/bin/sh",
                host_class.imageRef,
                "-ceu",
                script,
                "--",
                spec.serverUrl,
            ]
        )
        try:
            _code, container_id, _err = await self._backend.run(command)
            await self._backend.run(["docker", "start", container_name])
        except BaseException:
            await self._backend.run(["docker", "rm", "-f", container_name], check=False)
            await self._backend.run(
                ["docker", "volume", "rm", state_volume], check=False
            )
            if control_volume:
                await self._backend.run(
                    ["docker", "volume", "rm", control_volume], check=False
                )
            raise
        return {
            "containerId": container_id.strip(),
            "containerName": container_name,
            "correlationName": container_name,
            "stateVolumeRef": state_volume,
            "hostCleanupRef": f"host-cleanup:{container_name}",
            "stateCleanupRef": f"state-cleanup:{state_volume}",
            "controlVolumeRef": control_volume or None,
        }


__all__ = [
    "DockerOmnigentHostLauncher",
    "HostLaunchSpec",
    "host_correlation_identity",
]
