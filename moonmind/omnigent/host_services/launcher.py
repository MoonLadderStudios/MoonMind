"""Secret-free launch spec and Docker Omnigent host launcher."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.host_classes import HostClass, LaunchPolicy
from moonmind.omnigent.host_ports import HostLaunchSpec, host_correlation_identity
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.omnigent.host_services.mounted_tools import classify_tool_attachment
from moonmind.omnigent.host_services.runtime_scripts import OmnigentRuntimeScriptService
from moonmind.security.egress import omnigent_proxy_env


def docker_attachment_mount(attachment: Mapping[str, Any]) -> str:
    """Render the admitted attachment without inventing a daemon host path."""

    kind = str(attachment["kind"])
    mount = f"type={kind},src={attachment['sourceRef']},dst={attachment['targetPath']}"
    subpath = str(attachment.get("subPath") or "").strip()
    if subpath:
        if (
            kind != "volume"
            or subpath.startswith("/")
            or ".." in PurePosixPath(subpath).parts
            or "," in subpath
        ):
            raise HarnessPlatformError(
                "attachment volume subpath is unsupported",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        mount += f",volume-subpath={subpath}"
    if str(attachment.get("accessMode")) == "read-only":
        mount += ",readonly"
    return mount


def _image_owned_executables(
    tool_attachments: tuple[dict[str, Any], ...] | list[dict[str, Any]],
) -> tuple[str, ...]:
    """Return the executables the plan expects the selected image to own.

    Paths are derived exactly as exact-host attestation derives them, so the
    launch-time choice and the attestation judge the same files.
    """

    paths: set[str] = set()
    for attachment in tool_attachments:
        if classify_tool_attachment(attachment) != "image":
            continue
        root = str(attachment.get("targetPath") or "").rstrip("/")
        for tool in attachment.get("tools") or []:
            relative = str((tool or {}).get("path") or "").lstrip("/")
            if root and relative:
                paths.add(f"{root}/{relative}")
    return tuple(sorted(paths))


# Lease-scoped bearer capabilities delivered as files in the control volume.
_CAPABILITY_BEARER_FILES = {
    "MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN": "execution-fanout",
    "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN": "container-jobs",
}


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

    async def _write_capability_file(
        self,
        *,
        control_volume: str,
        image_ref: str,
        filename: str,
        bearer: str,
    ) -> None:
        # Write beside the target and rename so a running host never reads a
        # truncated capability while it is being replaced.
        staged = f"/control/.{filename}.next"
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
                image_ref,
                "-ceu",
                f"umask 077; cat > {staged}; chown 1000:1000 {staged}; "
                f"chmod 0400 {staged}; mv -f {staged} /control/{filename}",
            ],
            input_bytes=bearer.encode("utf-8"),
        )

    async def renew_capability_files(
        self,
        *,
        container_name: str,
        control_volume: str,
        runtime_environment: Mapping[str, str],
    ) -> tuple[str, ...]:
        """Replace a running host's capability files with freshly minted ones.

        The writer runs from the image the host itself was created from, so
        renewal never pulls or resolves a different image mid-session.
        """

        bearers = {
            filename: str(runtime_environment.get(key) or "").strip()
            for key, filename in _CAPABILITY_BEARER_FILES.items()
        }
        bearers = {filename: bearer for filename, bearer in bearers.items() if bearer}
        if not bearers:
            return ()
        _code, image_id, _stderr = await self._backend.run(
            ["docker", "inspect", "--format", "{{.Image}}", container_name]
        )
        image_ref = str(image_id or "").strip()
        if not image_ref:
            raise HarnessPlatformError(
                "generic host image is unavailable for capability renewal",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        for filename, bearer in bearers.items():
            await self._write_capability_file(
                control_volume=control_volume,
                image_ref=image_ref,
                filename=filename,
                bearer=bearer,
            )
        return tuple(bearers)

    async def _image_present(self, image_ref: str) -> bool:
        try:
            code, _, _ = await self._backend.run(
                ["docker", "image", "inspect", image_ref, "--format", "{{.Id}}"],
                check=False,
            )
        except Exception:
            return False
        return code == 0

    async def _image_owns_tools(
        self,
        image_ref: str,
        executables: tuple[str, ...],
        host_class: HostClass | None,
    ) -> bool:
        """Probe, offline and credential-free, that ``image_ref`` owns the tools."""

        runtime = host_class.runtime if host_class is not None else {}
        try:
            code, _, _ = await self._backend.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--user",
                    f"{runtime.get('uid', 1000)}:{runtime.get('gid', 1000)}",
                    "--entrypoint",
                    "/bin/sh",
                    image_ref,
                    "-ceu",
                    'for path in "$@"; do test -x "$path"; done',
                    "--",
                    *executables,
                ],
                timeout_seconds=60.0,
                check=False,
            )
        except Exception:
            return False
        return code == 0

    async def _resolve_launch_image(
        self,
        requested_ref: str,
        host_class: HostClass | None = None,
        *,
        required_executables: tuple[str, ...] = (),
    ) -> str:
        """Return the image to launch: exact when usable, else qualified local.

        Rebuilt host images change SHA/patch while keeping major.minor. When
        the plan-pinned digest is absent locally, reuse a qualified
        same-repository digest (digest-pinned, deployment-observed or
        operator-pinned, same admitted series) if present instead of forcing a
        7GB exact pull or failing. Qualification happens before any bearer or
        credential reaches the fallback image; attestation re-verifies the
        series with live probes before any session starts.

        A plan compiled before an update may pin an image that is still cached
        but predates the image-owned tools the plan requires. Launching it can
        only fail exact-host attestation, so when the deployment records a
        different qualified image that owns those tools, launch that instead.
        The planned image stays authoritative whenever it satisfies the plan,
        and nothing is probed unless such an alternative exists.
        """

        requested = str(requested_ref or "").strip()
        if not requested:
            return requested
        requested_present = await self._image_present(requested)
        if requested_present and not required_executables:
            return requested
        if "@sha256:" not in requested:
            return requested
        try:
            from moonmind.omnigent.host_image_drift import (
                compatible_deployed_fallback,
            )

            fallback = compatible_deployed_fallback(
                requested,
                expected_omnigent_version=host_class.omnigentVersion
                if host_class is not None
                else "",
            )
        except Exception:
            fallback = None
        if fallback is None:
            return requested
        if requested_present and await self._image_owns_tools(
            requested, required_executables, host_class
        ):
            return requested
        if not await self._image_present(fallback):
            return requested
        if requested_present and not await self._image_owns_tools(
            fallback, required_executables, host_class
        ):
            return requested
        import logging

        logging.getLogger(__name__).info(
            "host launch drift: reusing compatible local image for same "
            "repository (requested=%s fallback=%s reason=%s)",
            requested[:80],
            fallback[:80],
            (
                "planned image lacks image-owned tools"
                if requested_present
                else "planned image absent"
            ),
        )
        return fallback

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
        cpu_millis = int(launch_policy.limits["cpuMillis"])
        if cpu_millis < 1:
            raise HarnessPlatformError(
                "launch policy requires a positive explicit CPU limit",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        cpu_args = ["--cpus", str(cpu_millis / 1000)]
        state_volume = str(spec.stateAttachment["sourceRef"])
        control_volume = (
            str(spec.controlAttachment["sourceRef"])
            if spec.controlAttachment is not None
            else ""
        )
        supplied_runtime_environment = dict(runtime_environment or {})
        capability_bearers = {
            key: (
                filename,
                str(supplied_runtime_environment.pop(key, "") or "").strip(),
            )
            for key, filename in _CAPABILITY_BEARER_FILES.items()
        }
        allowed_runtime_environment = {
            "MOONMIND_URL",
            "MOONMIND_AGENT_RUN_ID",
            "MOONMIND_TASK_WORKFLOW_ID",
            "MOONMIND_STEP_ID",
            "MOONMIND_RUNTIME_ID",
            "MOONMIND_REPOSITORY_CONNECTION_REF",
            "MOONMIND_CONTAINER_JOBS_MCP_URL",
            "MOONMIND_CONTAINER_JOBS_SOURCE_KIND",
            "MOONMIND_CONTAINER_JOBS_SESSION_ID",
            "MOONMIND_CONTAINER_JOBS_WORKSPACE_KIND",
            "MOONMIND_CONTAINER_JOBS_WORKSPACE_ID",
            "MOONMIND_CONTAINER_JOBS_WORKSPACE_RELATIVE_PATH",
        }
        if set(supplied_runtime_environment) - allowed_runtime_environment:
            raise HarnessPlatformError(
                "generic host received unsupported runtime environment names",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        if (
            any(bearer for _, bearer in capability_bearers.values())
            and not control_volume
        ):
            raise HarnessPlatformError(
                "runtime capabilities require a lease-owned capability mount",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        # Resolve SHA drift once so every container creation below uses the
        # same effective image. Exact digest when present; otherwise the
        # deployment's current same-repository digest when present locally.
        launch_image = await self._resolve_launch_image(
            host_class.imageRef,
            host_class,
            required_executables=_image_owned_executables(spec.toolAttachments),
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
                            launch_image,
                            "-ceu",
                            "umask 077; cat > /control/api-token; chown 1000:1000 /control/api-token; chmod 0400 /control/api-token",
                        ],
                        input_bytes=self._host_api_token.encode("utf-8"),
                    )
                for _key, (filename, bearer) in capability_bearers.items():
                    if bearer:
                        await self._write_capability_file(
                            control_volume=control_volume,
                            image_ref=launch_image,
                            filename=filename,
                            bearer=bearer,
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
                    launch_image,
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
        for key, (filename, bearer) in capability_bearers.items():
            if bearer:
                supplied_runtime_environment[key + "_FILE"] = (
                    f"/run/moonmind-host-auth/{filename}"
                )
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
            enable_claude_runtime=any(
                item.harnessId == "claude-native"
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
        command.extend(cpu_args)
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
            if kind == "image":
                # Image-owned tools (MoonLadderStudios/MoonMind#4558): the
                # selected image already carries /opt/moonmind-tools, so no
                # mount overlay is created. Mounting anything over that path
                # would hide the image-owned executables. PATH projection and
                # exact-host probes still resolve them. Bind and volume
                # attachments (workspace, skills, credentials, state) must
                # still become Docker mounts: in local-daemon mode the
                # workspace and skill projections are bind mounts, and
                # skipping them would leave the host without its workspace.
                continue
            command.extend(["--mount", docker_attachment_mount(attachment)])
        command.extend(
            [
                "--entrypoint",
                "/bin/sh",
                launch_image,
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
            "launchImageRef": launch_image,
        }


__all__ = [
    "DockerOmnigentHostLauncher",
    "docker_attachment_mount",
    "HostLaunchSpec",
    "host_correlation_identity",
]
