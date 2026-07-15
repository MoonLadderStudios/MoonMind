"""Static and on-demand runtime boundary for profile-bound OAuth hosts."""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from moonmind.omnigent.oauth_hosts import (
    HostPreflightFailure,
    OmnigentOAuthHostError,
    deterministic_host_container_name,
    validate_preflight_result,
)
from moonmind.schemas.agent_runtime_models import (
    OmnigentOAuthHostBinding,
    OmnigentHostLease,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

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


class OmnigentOAuthHostRuntime:
    """Launch/check/stop hosts using server-resolved resources only."""

    def __init__(
        self,
        *,
        client: OmnigentHttpClient,
        image: str | None = None,
        network: str | None = None,
        server_url: str | None = None,
        scripts_dir: Path | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        self._client = client
        if image:
            self._image = image
        else:
            base_image = os.getenv(
                "OMNIGENT_HOST_IMAGE", "ghcr.io/omnigent-ai/omnigent-host"
            )
            if "@" in base_image or ":" in base_image.rsplit("/", 1)[-1]:
                self._image = base_image
            else:
                tag = os.getenv("OMNIGENT_HOST_IMAGE_TAG", "latest")
                self._image = f"{base_image}:{tag}"
        self._network = network or os.getenv(
            "OMNIGENT_HOST_NETWORK", "moonmind_local-network"
        )
        self._server_url = server_url or os.getenv(
            "OMNIGENT_SERVER_INTERNAL_URL", "http://omnigent:8000"
        )
        self._scripts_dir = scripts_dir or (
            Path(__file__).resolve().parents[2] / "services" / "omnigent" / "scripts"
        )
        self._workspace_root = (
            workspace_root
            or Path(os.getenv("OMNIGENT_WORKSPACE_ROOT", "omnigent_workspaces"))
        ).resolve()

    async def prepare_host(
        self,
        *,
        binding: OmnigentOAuthHostBinding,
        host_lease: OmnigentHostLease,
        workspace_key: str,
        repository_url: str | None = None,
    ) -> dict[str, Any]:
        workspace_source = await self._prepare_workspace(
            workspace_key=workspace_key,
            repository_url=repository_url,
        )
        if binding.host_launch_profile_ref:
            container_name = (
                host_lease.container_name
                or deterministic_host_container_name(host_lease.lease_id)
            )
            await self._launch_on_demand(
                binding=binding,
                host_lease=host_lease,
                container_name=container_name,
                workspace_source=workspace_source,
            )
            await self._exec_check(container_name)
        else:
            await self._compose_static_check()

        host = await self._resolve_exact_host(binding=binding, host_lease=host_lease)
        host_id = str(host.get("id") or host.get("host_id") or host.get("hostId") or "")
        capabilities = host.get("harnesses") or host.get("capabilities") or []
        if isinstance(capabilities, Mapping):
            capabilities = list(capabilities)
        if "codex-native" not in {str(value) for value in capabilities}:
            raise OmnigentOAuthHostError(
                "registered host does not advertise codex-native",
                code=HostPreflightFailure.HARNESS_UNAVAILABLE.value,
            )
        result = {
            "status": "ready",
            "providerProfileId": binding.provider_profile_id,
            "runtimeId": "codex_cli",
            "providerId": "openai",
            "credentialGeneration": host_lease.credential_generation,
            "mountPath": "/home/app/.codex",
            "runtimeUid": 1000,
            "runtimeGid": 1000,
            "loginStatus": "authenticated",
            "hostId": host_id,
            "harness": "codex-native",
            "competingCredentialsPresent": False,
            "workspacePath": (
                "/workspaces/run"
                if binding.host_launch_profile_ref
                else f"/workspaces/{workspace_source.name}"
            ),
        }
        validated = validate_preflight_result(
            result=result,
            binding=binding,
            host_lease=host_lease.model_copy(update={"omnigent_host_id": host_id}),
        )
        validated["workspacePath"] = result["workspacePath"]
        return validated

    async def stop_host(
        self, *, binding: OmnigentOAuthHostBinding, host_lease: OmnigentHostLease
    ) -> None:
        if not binding.host_launch_profile_ref:
            await self.stop_static_host()
            return
        container_name = host_lease.container_name or deterministic_host_container_name(
            host_lease.lease_id
        )
        await self._run("docker", "stop", "--time", "20", container_name, check=False)
        await self._run("docker", "rm", "-f", container_name, check=False)
        await self._run(
            "docker", "volume", "rm", "-f", f"{container_name}-state", check=False
        )

    async def stop_static_host(self) -> None:
        """Stop the static credential consumer even when no host lease is active."""

        await self._run(
            "docker",
            "compose",
            "-f",
            "docker-compose.yaml",
            "--profile",
            "omnigent-host-codex",
            "stop",
            "omnigent-host-codex",
            check=False,
        )

    async def container_exists(self, container_name: str) -> bool:
        result = await self._run(
            "docker",
            "inspect",
            "--format",
            "{{.State.Running}}",
            container_name,
            check=False,
        )
        return result[0] == 0 and result[1].strip() == "true"

    async def list_managed_containers(self) -> list[str]:
        result = await self._run(
            "docker",
            "ps",
            "-a",
            "--filter",
            "label=moonmind.kind=omnigent-oauth-host",
            "--format",
            "{{.Names}}",
            check=False,
        )
        if result[0] != 0:
            return []
        return [line.strip() for line in result[1].splitlines() if line.strip()]

    async def remove_container(self, container_name: str) -> None:
        await self._run("docker", "rm", "-f", container_name, check=False)
        await self._run(
            "docker", "volume", "rm", "-f", f"{container_name}-state", check=False
        )

    async def _launch_on_demand(
        self,
        *,
        binding: OmnigentOAuthHostBinding,
        host_lease: OmnigentHostLease,
        container_name: str,
        workspace_source: Path,
    ) -> None:
        if await self.container_exists(container_name):
            return
        mount = binding.credential_mount_ref
        state_volume = f"{container_name}-state"
        # A retry may find a stopped container with this deterministic name.
        # Remove only that lease-owned container, then initialize the dedicated
        # state volume as root before the actual host drops to UID/GID 1000.
        await self._run("docker", "rm", "-f", container_name, check=False)
        await self._run(
            "docker",
            "run",
            "--rm",
            "--user",
            "0:0",
            "--mount",
            f"type=volume,src={mount.auth_volume_ref.volume_ref},dst=/home/app/.codex",
            "--mount",
            f"type=volume,src={state_volume},dst=/home/app/.omnigent",
            "--mount",
            f"type=bind,src={self._scripts_dir},dst=/opt/moonmind,readonly",
            "--entrypoint",
            "/opt/moonmind/init-codex-oauth-host.sh",
            self._image,
        )
        labels = {
            "moonmind.kind": "omnigent-oauth-host",
            "moonmind.provider_profile_id": binding.provider_profile_id,
            "moonmind.provider_lease_id": host_lease.provider_lease_id,
            "moonmind.host_lease_id": host_lease.lease_id,
            "moonmind.workflow_id": "activity-owned",
            "moonmind.credential_generation": str(host_lease.credential_generation),
            "moonmind.expires_at": host_lease.expires_at.isoformat(),
        }
        args = [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "--user",
            "1000:1000",
            "--network",
            self._network,
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=256m",
            "--mount",
            f"type=volume,src={mount.auth_volume_ref.volume_ref},dst=/home/app/.codex",
            "--mount",
            f"type=volume,src={state_volume},dst=/home/app/.omnigent",
            "--mount",
            f"type=bind,src={self._scripts_dir},dst=/opt/moonmind,readonly",
            "--mount",
            f"type=bind,src={workspace_source},dst=/workspaces/run",
            "--env",
            "HOME=/home/app",
            "--env",
            "CODEX_HOME=/home/app/.codex",
            "--env",
            "CODEX_CONFIG_HOME=/home/app/.codex",
            "--env",
            "CODEX_CONFIG_PATH=/home/app/.codex/config.toml",
            "--env",
            "CODEX_VOLUME_PATH=/home/app/.codex",
            "--env",
            f"CODEX_CREDENTIAL_GENERATION={host_lease.credential_generation}",
            "--env",
            f"OMNIGENT_SERVER_URL={self._server_url}",
        ]
        token = os.getenv("OMNIGENT_HOST_TOKEN", "")
        child_env = dict(os.environ)
        if token:
            child_env["OMNIGENT_API_TOKEN"] = token
            args.extend(["--env", "OMNIGENT_API_TOKEN"])
        for key, value in labels.items():
            args.extend(["--label", f"{key}={value}"])
        args.extend(["--entrypoint", "/usr/bin/env"])
        for key in _FORBIDDEN_ENV:
            args.extend(["-u", key])
        args.extend([self._image, "/opt/moonmind/start-codex-oauth-host.sh"])
        await self._run(*args, env=child_env)

    async def _prepare_workspace(
        self, *, workspace_key: str, repository_url: str | None
    ) -> Path:
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(workspace_key.encode("utf-8")).hexdigest()[:24]
        workspace = (self._workspace_root / digest).resolve()
        if self._workspace_root not in workspace.parents:
            raise OmnigentOAuthHostError("workspace escaped configured root")
        workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
        if repository_url and not any(workspace.iterdir()):
            await self._run("git", "clone", "--", repository_url, str(workspace))
        return workspace

    async def _compose_static_check(self) -> None:
        await self._run(
            "docker",
            "compose",
            "-f",
            "docker-compose.yaml",
            "--profile",
            "omnigent-host-codex",
            "up",
            "-d",
            "omnigent-host-codex",
        )
        await self._run(
            "docker",
            "compose",
            "-f",
            "docker-compose.yaml",
            "--profile",
            "omnigent-host-codex",
            "exec",
            "-T",
            "omnigent-host-codex",
            "/opt/moonmind/check-codex-oauth-host.sh",
        )

    async def _exec_check(self, container_name: str) -> None:
        await self._run(
            "docker", "exec", container_name, "/opt/moonmind/check-codex-oauth-host.sh"
        )

    async def _resolve_exact_host(
        self,
        *,
        binding: OmnigentOAuthHostBinding,
        host_lease: OmnigentHostLease,
    ) -> dict[str, Any]:
        hosts = await self._client.list_hosts()
        expected_id = binding.static_host_id or host_lease.omnigent_host_id
        if expected_id:
            matches = [
                host
                for host in hosts
                if str(host.get("id") or host.get("hostId") or host.get("host_id"))
                == expected_id
            ]
        else:
            expected_name = deterministic_host_container_name(host_lease.lease_id)
            matches = [
                host
                for host in hosts
                if str(host.get("name") or host.get("hostname") or "")
                in {expected_name, "omnigent-host-codex"}
            ]
        online = [
            host for host in matches if str(host.get("status", "online")) == "online"
        ]
        if len(online) != 1:
            raise OmnigentOAuthHostError(
                "expected exactly one compatible online host",
                code=HostPreflightFailure.HOST_NOT_REGISTERED.value,
            )
        return dict(online[0])

    @staticmethod
    async def _run(
        *args: str,
        env: Mapping[str, str] | None = None,
        check: bool = True,
    ) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=dict(env) if env is not None else None,
        )
        stdout, stderr = await process.communicate()
        output = stdout.decode("utf-8", errors="replace")[:4096]
        error = stderr.decode("utf-8", errors="replace")[:4096]
        if check and process.returncode != 0:
            raise OmnigentOAuthHostError(
                "OAuth host runtime command failed",
                code=HostPreflightFailure.LOGIN_STATUS_FAILED.value,
            )
        return process.returncode or 0, output, error


__all__ = ["OmnigentOAuthHostRuntime"]
