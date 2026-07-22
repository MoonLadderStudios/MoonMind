"""Static and on-demand runtime boundary for profile-bound OAuth hosts."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from moonmind.omnigent.oauth_hosts import (
    HostPreflightFailure,
    OmnigentOAuthHostError,
    deterministic_host_container_name,
    validate_preflight_result,
)
from moonmind.omnigent.execution_profiles import validate_effective_launch_snapshot
from moonmind.omnigent.mounted_tool_preflight import (
    MountedToolPreflightError,
    preflight_mounted_tools,
)
from moonmind.schemas.agent_runtime_models import (
    OmnigentOAuthHostBinding,
    OmnigentHostLease,
)
from moonmind.schemas.workspace_locator_models import (
    SandboxWorkspaceLocator,
    WORKSPACE_LOCATOR_ADAPTER,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecordStore,
    daemon_visible_workspace_path,
    resolve_sandbox_workspace_locator,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
from moonmind.workflows.skills.run_projection import (
    load_resolved_skillset,
    materialize_run_skill_snapshot,
    verify_skill_projection,
)

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

_TOOLS_PATH = "/opt/moonmind-tools/bin"
_DEFAULT_HOST_PATH = (
    "/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin"
)
_DIGEST_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_PLACEHOLDER_DIGEST = "0" * 64
_SAFE_NETWORK = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")


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
            "OMNIGENT_HOST_NETWORK", "local-network"
        )
        self._server_url = server_url or os.getenv(
            "OMNIGENT_SERVER_INTERNAL_URL", "http://omnigent:8000"
        )
        self._scripts_dir = scripts_dir or (
            Path(__file__).resolve().parents[2] / "services" / "omnigent" / "scripts"
        )
        self._workspace_root = (
            workspace_root
            or Path(os.getenv("WORKFLOW_WORKSPACE_ROOT", "/work"))
        ).resolve()
        self._tool_bundle_volume = os.getenv(
            "OMNIGENT_TOOL_BUNDLE_VOLUME", "moonmind-omnigent-tools-gh-2.76.2"
        )

    async def prepare_host(
        self,
        *,
        binding: OmnigentOAuthHostBinding,
        host_lease: OmnigentHostLease,
        workspace_key: str,
        workspace_locator: Mapping[str, Any],
        current_workflow_id: str,
        current_step_execution_id: str,
        resolved_skillset_ref: str | None = None,
        artifact_gateway: Any | None = None,
        target_repository: str = "",
        required_capabilities: tuple[str, ...] = (),
        github_token: str | None = None,
        github_mutation_required: bool = False,
        effective_launch: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Validate the complete product-owned decision before materializing skills,
        # creating volumes, or starting a container.
        launch = self._validate_effective_launch(
            binding=binding, effective_launch=effective_launch
        )
        skill_projection = await self._prepare_skill_projection(
            workspace_key=workspace_key,
            resolved_skillset_ref=resolved_skillset_ref,
            artifact_gateway=artifact_gateway,
        )
        workspace_source = await self._prepare_workspace(
            workspace_locator=workspace_locator,
            current_workflow_id=current_workflow_id,
            current_step_execution_id=current_step_execution_id,
        )
        daemon_workspace_source = daemon_visible_workspace_path(workspace_source)
        if binding.host_launch_profile_ref:
            if "gh" in {item.strip().lower() for item in required_capabilities}:
                await self._initialize_required_tools()
            container_name = (
                host_lease.container_name
                or deterministic_host_container_name(host_lease.lease_id)
            )
            await self._launch_on_demand(
                binding=binding,
                host_lease=host_lease,
                container_name=container_name,
                workspace_source=daemon_workspace_source,
                skill_projection=skill_projection,
                github_token=github_token,
                effective_launch=launch,
            )
            await self._exec_check(container_name)
            await self._exec_tools_check(container_name)
        else:
            await self._compose_static_check(
                workspace_source=daemon_workspace_source,
                skill_projection=skill_projection, effective_launch=launch
            )

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
        mounted_tool_evidence = await self._preflight_mounted_tools(
            binding=binding,
            host_lease=host_lease,
            required_capabilities=required_capabilities,
            repository=target_repository,
            mutation_required=github_mutation_required,
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
            "mountedTools": mounted_tool_evidence,
            "workspacePath": (
                "/workspaces/run"
                if binding.host_launch_profile_ref
                else "/workspaces/run"
            ),
        }
        validated = validate_preflight_result(
            result=result,
            binding=binding,
            host_lease=host_lease.model_copy(update={"omnigent_host_id": host_id}),
        )
        validated["workspacePath"] = result["workspacePath"]
        validated["activeSkillsPath"] = str(skill_projection)
        validated["mountedTools"] = mounted_tool_evidence
        return validated

    @staticmethod
    def _validate_effective_launch(
        *,
        binding: OmnigentOAuthHostBinding,
        effective_launch: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(effective_launch, Mapping):
            raise OmnigentOAuthHostError(
                "effective launch policy is required before host mutation",
                code="OMNIGENT_EFFECTIVE_LAUNCH_REQUIRED",
            )
        launch = dict(effective_launch)
        validate_effective_launch_snapshot(launch)
        expected_mode = (
            "on_demand_docker" if binding.host_launch_profile_ref else "static_compose"
        )
        if launch.get("hostMode") != expected_mode:
            raise OmnigentOAuthHostError(
                "effective launch host mode conflicts with the durable binding",
                code="OMNIGENT_LAUNCH_POLICY_BINDING_CONFLICT",
            )
        if not str(launch.get("snapshotRef") or "").startswith(
            "omnigent-launch:sha256:"
        ):
            raise OmnigentOAuthHostError(
                "effective launch snapshot reference is invalid",
                code="OMNIGENT_EFFECTIVE_LAUNCH_INVALID",
            )
        if (not _DIGEST_IMAGE.fullmatch(str(launch.get("hostImageRef") or ""))
                or str(launch.get("hostImageRef")).endswith(_PLACEHOLDER_DIGEST)):
            raise OmnigentOAuthHostError(
                "host image must be an immutable sha256 reference",
                code="OMNIGENT_LAUNCH_IMAGE_UNREALIZABLE",
            )
        if (not _DIGEST_IMAGE.fullmatch(str(launch.get("serverImageRef") or ""))
                or str(launch.get("serverImageRef")).endswith(_PLACEHOLDER_DIGEST)):
            raise OmnigentOAuthHostError(
                "server image must be an immutable sha256 reference",
                code="OMNIGENT_LAUNCH_IMAGE_UNREALIZABLE",
            )
        network = str(launch.get("networkRef") or "")
        if not _SAFE_NETWORK.fullmatch(network):
            raise OmnigentOAuthHostError(
                "network must be a named deployment network",
                code="OMNIGENT_LAUNCH_NETWORK_UNREALIZABLE",
            )
        limits = launch.get("limits")
        required_limits = {
            "cpuMillis", "memoryMiB", "processes", "timeoutSeconds",
            "temporaryStorageMiB",
        }
        if not isinstance(limits, Mapping) or set(limits) != required_limits or any(
            not isinstance(limits[key], int) or limits[key] <= 0
            for key in required_limits
        ):
            raise OmnigentOAuthHostError(
                "launch resource limits are incomplete or invalid",
                code="OMNIGENT_LAUNCH_RESOURCES_UNREALIZABLE",
            )
        required_mounts = {
            "workspace",
            "oauth_home",
            "omnigent_state",
            "skills_tools",
            "artifacts",
            "cache",
        }
        if set(launch.get("mountClasses") or ()) != required_mounts:
            raise OmnigentOAuthHostError(
                "launch mount classes cannot be realized by the Codex host",
                code="OMNIGENT_LAUNCH_MOUNTS_UNREALIZABLE",
            )
        if not launch.get("enforcedEgress"):
            raise OmnigentOAuthHostError(
                "launch policy must enforce egress",
                code="OMNIGENT_LAUNCH_EGRESS_UNREALIZABLE",
            )
        if launch.get("runtimeUid") != 1000 or launch.get("runtimeGid") != 1000:
            raise OmnigentOAuthHostError(
                "Codex host UID/GID policy is unrealizable",
                code="OMNIGENT_LAUNCH_IDENTITY_UNREALIZABLE",
            )
        if launch.get("readOnlyRoot") is not True:
            raise OmnigentOAuthHostError(
                "Codex host policy must require a read-only root filesystem",
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
        expected_cleanup = "remove" if expected_mode == "on_demand_docker" else "drain"
        if (
            not isinstance(cleanup, Mapping)
            or cleanup.get("mode") != expected_cleanup
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

    async def _prepare_skill_projection(
        self,
        *,
        workspace_key: str,
        resolved_skillset_ref: str | None,
        artifact_gateway: Any | None,
    ) -> Path:
        """Materialize and verify the run snapshot before workspace/host mutation."""

        skillset_ref = str(resolved_skillset_ref or "").strip()
        if not skillset_ref or artifact_gateway is None:
            raise OmnigentOAuthHostError(
                "resolved Skill projection is required before Omnigent host mutation",
                code="OMNIGENT_SKILL_PROJECTION_UNAVAILABLE",
            )

        if hasattr(artifact_gateway, "read"):
            artifact_service = artifact_gateway
        else:
            class _GatewayArtifactService:
                async def read(self, *, artifact_id: str, **_kwargs: Any):
                    payload = await artifact_gateway.read_bytes(artifact_id)
                    return {}, payload

            artifact_service = _GatewayArtifactService()
        resolved_skillset = await load_resolved_skillset(
            artifact_service, skillset_ref
        )
        digest = hashlib.sha256(workspace_key.encode("utf-8")).hexdigest()[:24]
        projection_root = (self._workspace_root / ".skill-projections" / digest).resolve()
        metadata = await materialize_run_skill_snapshot(
            workspace_path=projection_root,
            run_root=projection_root,
            runtime_id="omnigent",
            resolved_skillset=resolved_skillset,
            artifact_service=artifact_service,
            project_adapter_aliases=False,
        )
        await verify_skill_projection(
            materialization_metadata=metadata,
            resolved_skillset=resolved_skillset,
        )
        return Path(str(metadata["visiblePath"])).resolve()

    async def stop_host(
        self, *, binding: OmnigentOAuthHostBinding, host_lease: OmnigentHostLease
    ) -> None:
        if not binding.host_launch_profile_ref:
            await self.stop_static_host()
            return
        container_name = host_lease.container_name or deterministic_host_container_name(
            host_lease.lease_id
        )
        await self._assert_container_owned(container_name, host_lease.lease_id)
        await self._run("docker", "stop", "--time", "20", container_name, check=False)
        await self._run("docker", "rm", "-f", container_name, check=False)
        await self._run(
            "docker", "volume", "rm", "-f", f"{container_name}-state", check=False
        )
        await self._run(
            "docker", "volume", "rm", "-f", f"{container_name}-artifacts", check=False
        )
        await self._run(
            "docker", "volume", "rm", "-f", f"{container_name}-cache", check=False
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
        # Janitor discovery is label-scoped; never accept an arbitrary name.
        result = await self._run(
            "docker", "inspect", "--format",
            "{{index .Config.Labels \"moonmind.kind\"}}", container_name, check=False,
        )
        if result[0] != 0:
            return
        if result[1].strip() != "omnigent-oauth-host":
            raise OmnigentOAuthHostError(
                "refusing to remove a container outside Omnigent ownership",
                code="OMNIGENT_HOST_OWNERSHIP_MISMATCH",
            )
        await self._run("docker", "rm", "-f", container_name, check=False)
        await self._run(
            "docker", "volume", "rm", "-f", f"{container_name}-state", check=False
        )
        await self._run(
            "docker", "volume", "rm", "-f", f"{container_name}-artifacts", check=False
        )
        await self._run(
            "docker", "volume", "rm", "-f", f"{container_name}-cache", check=False
        )

    async def _launch_on_demand(
        self,
        *,
        binding: OmnigentOAuthHostBinding,
        host_lease: OmnigentHostLease,
        container_name: str,
        workspace_source: Path,
        skill_projection: Path,
        github_token: str | None = None,
        effective_launch: Mapping[str, Any],
    ) -> None:
        if await self.container_exists(container_name):
            await self._assert_container_owned(container_name, host_lease.lease_id)
            return
        mount = binding.credential_mount_ref
        state_volume = f"{container_name}-state"
        artifacts_volume = f"{container_name}-artifacts"
        cache_volume = f"{container_name}-cache"
        host_image_ref = str(effective_launch["hostImageRef"])
        host_path = await self._discover_upstream_path(host_image_ref)
        # A retry may find a stopped container with this deterministic name.
        # Inspect its lease before removal; a deterministic name is not itself
        # ownership authority. An absent container needs no reconciliation.
        stopped = await self._run(
            "docker",
            "inspect",
            "--format",
            "{{index .Config.Labels \"moonmind.host_lease_id\"}}",
            container_name,
            check=False,
        )
        if stopped[0] == 0:
            if stopped[1].strip() != host_lease.lease_id:
                raise OmnigentOAuthHostError(
                    "container does not belong to the current host lease",
                    code="OMNIGENT_HOST_OWNERSHIP_MISMATCH",
                )
            await self._run("docker", "rm", "-f", container_name, check=False)
        # Initialize the dedicated state volume as root before the actual host
        # drops to UID/GID 1000.
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
            f"type=volume,src={artifacts_volume},dst=/artifacts",
            "--mount",
            f"type=volume,src={cache_volume},dst=/home/app/.cache",
            "--mount",
            f"type=bind,src={self._scripts_dir},dst=/opt/moonmind,readonly",
            "--entrypoint",
            "/opt/moonmind/init-codex-oauth-host.sh",
            host_image_ref,
        )
        labels = {
            "moonmind.kind": "omnigent-oauth-host",
            "moonmind.provider_profile_id": binding.provider_profile_id,
            "moonmind.provider_lease_id": host_lease.provider_lease_id,
            "moonmind.host_lease_id": host_lease.lease_id,
            "moonmind.workflow_id": "activity-owned",
            "moonmind.credential_generation": str(host_lease.credential_generation),
            "moonmind.expires_at": host_lease.expires_at.isoformat(),
            "moonmind.effective_launch_ref": str(effective_launch["snapshotRef"]),
            "moonmind.capture_required": str(effective_launch["capture"]["required"]).lower(),
            "moonmind.capture_retention_days": str(effective_launch["capture"]["retentionDays"]),
            "moonmind.cleanup_mode": str(effective_launch["cleanup"]["mode"]),
            "moonmind.control_capabilities": ",".join(effective_launch["controlCapabilities"]),
            "moonmind.timeout_seconds": str(effective_launch["limits"]["timeoutSeconds"]),
        }
        args = [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "--user",
            f"{effective_launch['runtimeUid']}:{effective_launch['runtimeGid']}",
            "--workdir",
            "/home/app",
            "--network",
            str(effective_launch["networkRef"]),
            "--cpus",
            str(int(effective_launch["limits"]["cpuMillis"]) / 1000),
            "--memory",
            f"{effective_launch['limits']['memoryMiB']}m",
            "--pids-limit",
            str(effective_launch["limits"]["processes"]),
            "--stop-timeout",
            "20",
            "--read-only",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,size={effective_launch['limits']['temporaryStorageMiB']}m",
            "--mount",
            f"type=volume,src={mount.auth_volume_ref.volume_ref},dst=/home/app/.codex",
            "--mount",
            f"type=volume,src={state_volume},dst=/home/app/.omnigent",
            "--mount",
            f"type=volume,src={artifacts_volume},dst=/artifacts",
            "--mount",
            f"type=volume,src={cache_volume},dst=/home/app/.cache",
            "--mount",
            f"type=bind,src={self._scripts_dir},dst=/opt/moonmind,readonly",
            "--mount",
            f"type=volume,src={self._tool_bundle_volume},dst=/opt/moonmind-tools,readonly",
            "--mount",
            f"type=bind,src={workspace_source},dst=/workspaces/run",
            "--mount",
            f"type=bind,src={skill_projection},dst=/opt/moonmind-skills,readonly",
            "--env",
            f"PATH={self._prepend_tools_path(host_path)}",
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
            "--env",
            "MOONMIND_ACTIVE_SKILLS_DIR=/opt/moonmind-skills",
            "--env",
            f"OMNIGENT_EXECUTION_TIMEOUT_SECONDS={effective_launch['limits']['timeoutSeconds']}",
            "--env",
            "OMNIGENT_EXECUTION_TIMEOUT_OWNER=temporal_workflow",
            "--env",
            "OMNIGENT_CAPTURE_OWNER=moonmind_bridge",
            "--env",
            f"OMNIGENT_CAPTURE_RETENTION_DAYS={effective_launch['capture']['retentionDays']}",
            "--env",
            "PATH=/opt/moonmind-tools/bin:/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin",
        ]
        token = os.getenv("OMNIGENT_HOST_TOKEN", "")
        child_env = dict(os.environ)
        if token:
            child_env["OMNIGENT_API_TOKEN"] = token
            args.extend(["--env", "OMNIGENT_API_TOKEN"])
        if github_token:
            child_env["GH_TOKEN"] = github_token
            child_env["GIT_TOKEN"] = github_token
            args.extend(
                [
                    "--env",
                    "GH_TOKEN",
                    "--env",
                    "GIT_TOKEN",
                    "--env",
                    "GIT_USERNAME=x-access-token",
                    "--env",
                    "GH_CONFIG_DIR=/workspaces/run/.config/gh",
                    "--env",
                    "GH_PROMPT_DISABLED=1",
                    "--env",
                    "GH_NO_UPDATE_NOTIFIER=1",
                    "--env",
                    "GH_NO_EXTENSION_UPDATE_NOTIFIER=1",
                    "--env",
                    "OMNIGENT_RUNNER_ENV_PASSTHROUGH=GH_TOKEN,GH_CONFIG_DIR,"
                    + "GH_PROMPT_DISABLED,GH_NO_UPDATE_NOTIFIER,"
                    + "GH_NO_EXTENSION_UPDATE_NOTIFIER",
                ]
            )
        for key, value in labels.items():
            args.extend(["--label", f"{key}={value}"])
        args.extend(["--entrypoint", "/usr/bin/env"])
        for key in _FORBIDDEN_ENV:
            args.extend(["-u", key])
        args.extend([host_image_ref, "/opt/moonmind/start-codex-oauth-host.sh"])
        await self._run(*args, env=child_env)

    async def _assert_container_owned(self, container_name: str, lease_id: str) -> None:
        result = await self._run(
            "docker", "inspect", "--format",
            "{{index .Config.Labels \"moonmind.host_lease_id\"}}",
            container_name, check=False,
        )
        if result[0] != 0 or result[1].strip() != lease_id:
            raise OmnigentOAuthHostError(
                "container does not belong to the current host lease",
                code="OMNIGENT_HOST_OWNERSHIP_MISMATCH",
            )

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

    @staticmethod
    def _prepend_tools_path(upstream_path: str) -> str:
        entries = [entry for entry in upstream_path.split(":") if entry]
        return ":".join([_TOOLS_PATH, *(e for e in entries if e != _TOOLS_PATH)])

    async def _exec_tools_check(self, container_name: str) -> None:
        """Verify the mounted bundle through the host's login-shell boundary."""

        await self._run(
            "docker",
            "exec",
            container_name,
            "bash",
            "-lc",
            "test -f /opt/moonmind-tools/manifest.json "
            "&& command -v gh >/dev/null && gh --version >/dev/null",
        )

    async def _preflight_mounted_tools(
        self,
        *,
        binding: OmnigentOAuthHostBinding,
        host_lease: OmnigentHostLease,
        required_capabilities: tuple[str, ...],
        repository: str,
        mutation_required: bool,
    ) -> dict[str, Any]:
        if "gh" not in {item.strip().lower() for item in required_capabilities}:
            return {"status": "not_required", "boundaries": []}
        if not binding.host_launch_profile_ref:
            raise MountedToolPreflightError(
                "Required gh credentials cannot be isolated on a reusable static host",
                code="github_auth_unavailable",
                evidence={"tool": "gh", "phase": "host_launch", "hostMode": "static"},
            )
        container_name = host_lease.container_name or deterministic_host_container_name(
            host_lease.lease_id
        )

        async def host_runner(command: str) -> tuple[int, str, str]:
            return await self._run(
                "docker", "exec", container_name, "bash", "-lc", command, check=False
            )

        async def runner_runner(command: str) -> tuple[int, str, str]:
            # Execute with the stock host's authoritative runner environment
            # constructor.  Importing this function from the installed Omnigent
            # build keeps the pre-session proof on the exact path that
            # HostConnect._handle_launch uses immediately before Popen.
            runner_probe = (
                "import os, subprocess, sys; "
                "from omnigent.host.connect import _build_runner_env; "
                "env = _build_runner_env(os.environ, server_url=os.environ.get("
                "'OMNIGENT_SERVER_URL', ''), runner_id='preflight', "
                "binding_token='preflight', workspace='/workspaces/run', "
                "parent_pid=os.getpid()); "
                "result = subprocess.run(['bash', '-lc', sys.argv[1]], env=env, "
                "text=True); raise SystemExit(result.returncode)"
            )
            return await self._run(
                "docker",
                "exec",
                container_name,
                "python",
                "-c",
                runner_probe,
                command,
                check=False,
            )

        return await preflight_mounted_tools(
            required_capabilities=required_capabilities,
            repository=repository,
            mutation_required=mutation_required,
            host_runner=host_runner,
            runner_runner=runner_runner,
        )

    async def _prepare_workspace(
        self,
        *,
        workspace_locator: Mapping[str, Any],
        current_workflow_id: str,
        current_step_execution_id: str,
    ) -> Path:
        locator = WORKSPACE_LOCATOR_ADAPTER.validate_python(workspace_locator)
        if not isinstance(locator, SandboxWorkspaceLocator):
            raise OmnigentOAuthHostError(
                "Omnigent repository work requires a sandbox WorkspaceLocator",
                code="WORKSPACE_LOCATOR_UNSUPPORTED",
            )
        expected_id = hashlib.sha256(
            f"{current_workflow_id}:{current_step_execution_id}".encode("utf-8")
        ).hexdigest()[:24]
        # Validate the workflow-derived identity and containment before writing
        # even the owner record.
        resolve_sandbox_workspace_locator(
            locator,
            workspace_root=self._workspace_root,
            expected_workspace_id=expected_id,
            must_exist=True,
        )
        record_store = SandboxWorkspaceRecordStore(self._workspace_root)
        authoritative_record = record_store.load(locator.workspace_id)
        if authoritative_record is None:
            raise OmnigentOAuthHostError(
                "authorized sandbox workspace must be materialized before host preparation",
                code="WORKSPACE_IDENTITY_MISMATCH",
            )
        workspace = resolve_sandbox_workspace_locator(
            locator,
            workspace_root=self._workspace_root,
            expected_workspace_id=expected_id,
            owner_record=authoritative_record,
            expected_workflow_id=current_workflow_id,
            expected_step_execution_id=current_step_execution_id,
            must_exist=True,
        )
        return workspace

    async def _initialize_required_tools(self) -> None:
        await self._run(
            "docker",
            "compose",
            "-f",
            "docker-compose.yaml",
            "--profile",
            "omnigent-host-codex",
            "run",
            "--rm",
            "omnigent-tools-init",
        )

    async def _compose_static_check(
        self,
        *,
        workspace_source: Path,
        skill_projection: Path | None = None,
        effective_launch: Mapping[str, Any] | None = None,
    ) -> None:
        child_env = dict(os.environ)
        child_env["OMNIGENT_RUN_WORKSPACE"] = str(workspace_source)
        if skill_projection is not None:
            child_env["OMNIGENT_ACTIVE_SKILLS_DIR"] = str(skill_projection)
        if effective_launch is not None:
            child_env.update(
                {
                    "OMNIGENT_HOST_IMAGE_REF": str(effective_launch["hostImageRef"]),
                    "OMNIGENT_IMAGE_REF": str(effective_launch["serverImageRef"]),
                    "OMNIGENT_EFFECTIVE_LAUNCH_REF": str(effective_launch["snapshotRef"]),
                    "OMNIGENT_HOST_CPU_LIMIT": str(
                        int(effective_launch["limits"]["cpuMillis"]) / 1000
                    ),
                    "OMNIGENT_HOST_MEMORY_LIMIT": (
                        f"{effective_launch['limits']['memoryMiB']}m"
                    ),
                    "OMNIGENT_HOST_PIDS_LIMIT": str(
                        effective_launch["limits"]["processes"]
                    ),
                    "OMNIGENT_HOST_TMPFS_LIMIT": (
                        f"{effective_launch['limits']['temporaryStorageMiB']}m"
                    ),
                    "OMNIGENT_HOST_TIMEOUT_SECONDS": str(
                        effective_launch["limits"]["timeoutSeconds"]
                    ),
                    "OMNIGENT_CAPTURE_RETENTION_DAYS": str(
                        effective_launch["capture"]["retentionDays"]
                    ),
                    "OMNIGENT_CAPTURE_OWNER": "moonmind_bridge",
                    "OMNIGENT_EXECUTION_TIMEOUT_OWNER": "temporal_workflow",
                    "OMNIGENT_CONTROL_CAPABILITIES": ",".join(
                        effective_launch["controlCapabilities"]
                    ),
                }
            )
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
            env=child_env,
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
            "/opt/moonmind/check-runner-projections.sh",
            env=child_env,
        )

    async def _exec_check(self, container_name: str) -> None:
        await self._run(
            "docker", "exec", container_name, "/opt/moonmind/check-runner-projections.sh"
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
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=600)
        except (TimeoutError, asyncio.CancelledError):
            process.kill()
            await process.wait()
            raise
        output = stdout.decode("utf-8", errors="replace")[:4096]
        error = stderr.decode("utf-8", errors="replace")[:4096]
        if check and process.returncode != 0:
            raise OmnigentOAuthHostError(
                "OAuth host runtime command failed",
                code=HostPreflightFailure.LOGIN_STATUS_FAILED.value,
            )
        return process.returncode or 0, output, error


__all__ = ["OmnigentOAuthHostRuntime"]
