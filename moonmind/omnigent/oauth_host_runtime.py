"""Static and on-demand runtime boundary for profile-bound OAuth hosts."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from moonmind.omnigent.oauth_hosts import (
    HostPreflightFailure,
    OmnigentOAuthHostError,
    deterministic_host_container_name,
    validate_preflight_result,
)
from moonmind.omnigent.execution_profiles import validate_effective_launch_snapshot
from moonmind.security.egress import (
    OMNIGENT_EGRESS_PROFILE,
    attest_docker_egress,
    omnigent_proxy_env,
)
from moonmind.workloads.docker_launcher import structured_container_security_args
from moonmind.omnigent.mounted_tool_preflight import (
    MountedToolPreflightError,
    preflight_mounted_tools,
)
from moonmind.schemas.agent_runtime_models import (
    OmnigentOAuthHostBinding,
    OmnigentHostLease,
)
from moonmind.schemas.workspace_locator_models import (
    ExternalStateLocator,
    ManagedWorkspaceLocator,
    SandboxWorkspaceLocator,
    WORKSPACE_AUTHORITY_MISMATCH,
    WORKSPACE_LOCATOR_ADAPTER,
    WORKSPACE_LOCATOR_UNSUPPORTED,
    WorkspaceLocatorResolutionError,
)
from moonmind.workflows.temporal.runtime.command_runner import run_runtime_command
from moonmind.workflows.temporal.runtime.git_auth import (
    build_github_token_git_environment,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
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
_GITHUB_SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# Bound restore-input materialization so a hostile or oversized artifact ref
# cannot exhaust the authorized workspace before the host launches. The limit is
# enforced both per ref and cumulatively across every accepted ref so the
# advertised hostile-input bound cannot be defeated by fanning bytes across many
# individually-legal refs.
_MAX_RESTORE_INPUT_REFS = 64
_MAX_RESTORE_INPUT_BYTES = 64 * 1024 * 1024
_MAX_RESTORE_TOTAL_BYTES = 256 * 1024 * 1024
# Restore payloads are read through the durable artifact contract as raw bytes,
# under a dedicated service principal (mirrors the checkpoint/remediation
# restorers, which never read restore material as an end user).
_RESTORE_ARTIFACT_PRINCIPAL = "service:omnigent_workspace_restore"
# Stream restore bytes from the artifact service in bounded chunks so an oversized
# payload is rejected mid-stream instead of after full in-memory materialization.
_RESTORE_STREAM_CHUNK_BYTES = 1024 * 1024

_RUNTIME_ADAPTERS = {
    "codex_cli": {
        "harness": "codex-native",
        "provider": "openai",
        "home": "/home/app/.codex",
        "compose_profile": "omnigent-host-codex",
        "compose_service": "omnigent-host-codex",
        "start_script": "/opt/moonmind/start-codex-oauth-host.sh",
        "generation_env": "CODEX_CREDENTIAL_GENERATION",
        "env": (
            "CODEX_HOME=/home/app/.codex",
            "CODEX_CONFIG_HOME=/home/app/.codex",
            "CODEX_CONFIG_PATH=/home/app/.codex/config.toml",
            "CODEX_VOLUME_PATH=/home/app/.codex",
        ),
    },
    "claude_code": {
        "harness": "claude-native",
        "provider": "anthropic",
        "home": "/home/app/.claude",
        "compose_profile": "omnigent-host-claude",
        "compose_service": "omnigent-host-claude",
        "start_script": "/opt/moonmind/start-claude-oauth-host.sh",
        "generation_env": "CLAUDE_CREDENTIAL_GENERATION",
        "env": (
            "CLAUDE_HOME=/home/app/.claude",
            "CLAUDE_CONFIG_DIR=/home/app/.claude",
            "CLAUDE_VOLUME_PATH=/home/app/.claude",
        ),
    },
}


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
        repository_source_root: Path | str | None = None,
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
            or Path(os.getenv("WORKFLOW_WORKSPACE_ROOT", "/work/agent_jobs"))
        ).resolve()
        self._tool_bundle_volume = os.getenv(
            "OMNIGENT_TOOL_BUNDLE_VOLUME", "moonmind-omnigent-tools-gh-2.76.2"
        )
        # Local (on-disk) repository sources are only clonable when they are
        # contained within an explicitly authorized per-run source root. Without
        # this root, an authored ``workspaceSpec`` could clone any repository the
        # trusted worker can read (including another run under the workspace
        # authority), crossing workspace isolation boundaries.
        source_root = repository_source_root or os.getenv(
            "OMNIGENT_REPOSITORY_SOURCE_ROOT", ""
        )
        source_root_text = str(source_root or "").strip()
        self._repository_source_root = (
            Path(source_root_text).resolve() if source_root_text else None
        )
        # Bounded, non-sensitive evidence of the most recent workspace resolution,
        # surfaced through the preflight result for Workflow Detail. Never carries
        # credentials, raw daemon paths, or unbounded command output.
        self._last_workspace_evidence: dict[str, Any] = {}

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
        repository_source: str = "",
        starting_branch: str | None = None,
        target_branch: str | None = None,
        checkout_commit: str | None = None,
        restore_input_refs: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        # Validate the complete product-owned decision before materializing skills,
        # creating volumes, or starting a container.
        launch = self._validate_effective_launch(
            binding=binding, effective_launch=effective_launch
        )
        adapter = self._runtime_adapter(binding)
        binding_runtime = binding.credential_mount_ref.auth_volume_ref.runtime_id
        if (
            launch.get("providerRuntime") not in {None, binding_runtime}
            or launch.get("harness") != adapter["harness"]
        ):
            raise OmnigentOAuthHostError(
                "effective launch provider does not match the OAuth host binding",
                code=HostPreflightFailure.BINDING_MISMATCH.value,
            )
        skill_projection = await self._prepare_skill_projection(
            workspace_key=workspace_key,
            resolved_skillset_ref=resolved_skillset_ref,
            artifact_gateway=artifact_gateway,
        )
        egress_attestation = await self._attest_egress(launch)
        workspace_source = await self._prepare_workspace(
            workspace_locator=workspace_locator,
            current_workflow_id=current_workflow_id,
            current_step_execution_id=current_step_execution_id,
            repository_source=repository_source,
            starting_branch=starting_branch,
            target_branch=target_branch,
            checkout_commit=checkout_commit,
            restore_input_refs=restore_input_refs,
            github_token=github_token,
            artifact_gateway=artifact_gateway,
        )
        daemon_workspace_source = daemon_visible_workspace_path(workspace_source)
        daemon_skill_projection = daemon_visible_workspace_path(skill_projection)
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
                skill_projection=daemon_skill_projection,
                github_token=github_token,
                effective_launch=launch,
            )
            await self._exec_check(container_name)
            await self._exec_tools_check(container_name)
        else:
            await self._compose_static_check(
                binding=binding,
                workspace_source=daemon_workspace_source,
                skill_projection=daemon_skill_projection, effective_launch=launch
            )

        host = await self._resolve_exact_host(binding=binding, host_lease=host_lease)
        host_id = str(host.get("id") or host.get("host_id") or host.get("hostId") or "")
        capabilities = host.get("harnesses") or host.get("capabilities") or []
        if isinstance(capabilities, Mapping):
            capabilities = list(capabilities)
        if adapter["harness"] not in {str(value) for value in capabilities}:
            raise OmnigentOAuthHostError(
                f"registered host does not advertise {adapter['harness']}",
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
            "runtimeId": binding.credential_mount_ref.auth_volume_ref.runtime_id,
            "providerId": adapter["provider"],
            "credentialGeneration": host_lease.credential_generation,
            "mountPath": adapter["home"],
            "runtimeUid": 1000,
            "runtimeGid": 1000,
            "loginStatus": "authenticated",
            "hostId": host_id,
            "harness": adapter["harness"],
            "competingCredentialsPresent": False,
            "mountedTools": mounted_tool_evidence,
            "egressAttestation": egress_attestation.model_dump(
                by_alias=True, mode="json"
            ),
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
        validated["egressAttestation"] = result["egressAttestation"]
        validated["workspaceResolution"] = dict(self._last_workspace_evidence)
        return validated

    async def _attest_egress(self, launch: Mapping[str, Any]):
        if launch.get("networkRef") != OMNIGENT_EGRESS_PROFILE.network_ref:
            raise OmnigentOAuthHostError(
                "launch egress profile does not map to supported backend state",
                code="OMNIGENT_LAUNCH_EGRESS_UNATTESTED",
            )

        async def runner(args):
            code, stdout, stderr = await self._run(*args, check=False)
            return code, stdout.encode(), stderr.encode()

        try:
            return await attest_docker_egress(
                runner=runner,
                profile=OMNIGENT_EGRESS_PROFILE,
                backend_ref="omnigent-host-runtime",
            )
        except RuntimeError as exc:
            raise OmnigentOAuthHostError(
                "launch egress backend attestation failed",
                code="OMNIGENT_LAUNCH_EGRESS_UNATTESTED",
            ) from exc

    @staticmethod
    def _runtime_adapter(binding: OmnigentOAuthHostBinding) -> Mapping[str, Any]:
        runtime_id = binding.credential_mount_ref.auth_volume_ref.runtime_id
        adapter = _RUNTIME_ADAPTERS.get(runtime_id)
        if adapter is None or adapter["harness"] != binding.harness:
            raise OmnigentOAuthHostError(
                "OAuth runtime and harness binding are incompatible",
                code=HostPreflightFailure.BINDING_MISMATCH.value,
            )
        return adapter

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
        allowed_mounts = {
            "workspace",
            "oauth_home",
            "omnigent_state",
            "skills_tools",
            "artifacts",
            "cache",
        }
        mount_classes = set(launch.get("mountClasses") or ())
        if not mount_classes <= allowed_mounts or "oauth_home" not in mount_classes:
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
            await self.stop_static_host(binding=binding)
            return
        container_name = host_lease.container_name or deterministic_host_container_name(
            host_lease.lease_id
        )
        if await self.container_exists(container_name):
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

    async def stop_static_host(
        self, *, binding: OmnigentOAuthHostBinding | None = None
    ) -> None:
        """Stop the static credential consumer even when no host lease is active."""

        adapter = (
            self._runtime_adapter(binding)
            if binding is not None
            else _RUNTIME_ADAPTERS["codex_cli"]
        )
        await self._run(
            "docker",
            "compose",
            "-f",
            "docker-compose.yaml",
            "--profile",
            str(adapter["compose_profile"]),
            "stop",
            str(adapter["compose_service"]),
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
        adapter = self._runtime_adapter(binding)
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
            f"type=volume,src={mount.auth_volume_ref.volume_ref},dst={adapter['home']}",
            "--mount",
            f"type=volume,src={state_volume},dst=/home/app/.omnigent",
            "--mount",
            f"type=volume,src={artifacts_volume},dst=/artifacts",
            "--mount",
            f"type=volume,src={cache_volume},dst=/home/app/.cache",
            "--mount",
            f"type=bind,src={self._scripts_dir},dst=/opt/moonmind,readonly",
            "--env",
            f"OAUTH_HOME={adapter['home']}",
            "--entrypoint",
            "/opt/moonmind/init-oauth-host.sh",
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
            *structured_container_security_args(),
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
            f"type=volume,src={mount.auth_volume_ref.volume_ref},dst={adapter['home']}",
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
            f"{adapter['generation_env']}={host_lease.credential_generation}",
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
        for runtime_env in adapter["env"]:
            args.extend(["--env", runtime_env])
        for proxy_env in omnigent_proxy_env():
            args.extend(["--env", proxy_env])
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
        args.extend([host_image_ref, str(adapter["start_script"])])
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
        repository_source: str = "",
        starting_branch: str | None = None,
        target_branch: str | None = None,
        checkout_commit: str | None = None,
        restore_input_refs: tuple[str, ...] = (),
        github_token: str | None = None,
        artifact_gateway: Any | None = None,
    ) -> Path:
        """Resolve, and when required materialize, the authoritative workspace.

        Every supported locator kind is routed through this single owning-worker
        boundary. Profile-bound Omnigent workspace authority is the sandbox plane,
        so managed-runtime and external-state locators fail closed here rather than
        silently substituting a different workspace, and an external-state artifact
        ref is never conflated with a local filesystem path.
        """

        locator = WORKSPACE_LOCATOR_ADAPTER.validate_python(workspace_locator)
        if isinstance(locator, ExternalStateLocator):
            # External state proves session/provider continuity only. It is an
            # artifact reference, not a local workspace, and cannot satisfy host
            # workspace materialization; treating it as a path would conflate the
            # two authorities. Its refs are materialized as restore *inputs* into
            # a sandbox workspace instead (see ``restore_input_refs``).
            raise OmnigentOAuthHostError(
                "external-state locators are session-continuity refs, not a host "
                "workspace; supply a sandbox locator with restore input refs",
                code=WORKSPACE_LOCATOR_UNSUPPORTED,
            )
        if isinstance(locator, ManagedWorkspaceLocator):
            raise OmnigentOAuthHostError(
                "profile-bound Omnigent workspace authority is the sandbox plane; "
                "managed-runtime locators are not a valid host workspace",
                code=WORKSPACE_LOCATOR_UNSUPPORTED,
            )
        if not isinstance(locator, SandboxWorkspaceLocator):  # pragma: no cover
            raise OmnigentOAuthHostError(
                "Omnigent repository work requires a sandbox WorkspaceLocator",
                code=WORKSPACE_LOCATOR_UNSUPPORTED,
            )

        expected_id = hashlib.sha256(
            f"{current_workflow_id}:{current_step_execution_id}".encode("utf-8")
        ).hexdigest()[:24]
        # 1. Validate the workflow-derived identity, containment, traversal, and
        #    symlink behavior before any host mutation, without yet requiring the
        #    directory to exist.
        workspace = resolve_sandbox_workspace_locator(
            locator,
            workspace_root=self._workspace_root,
            expected_workspace_id=expected_id,
            must_exist=False,
        )
        source = str(repository_source or "").strip()
        # 2. Establish or load the durable owner record and validate its binding
        #    before mutating the filesystem, so a retry or a foreign record can
        #    never author a second workspace under this identity.
        record_store = SandboxWorkspaceRecordStore(self._workspace_root)
        authoritative_record = record_store.load(locator.workspace_id)
        if authoritative_record is None:
            authoritative_record = SandboxWorkspaceRecord(
                workspace_id=locator.workspace_id,
                workflow_id=current_workflow_id,
                step_execution_id=current_step_execution_id,
                relative_path=locator.relative_path,
            )
            record_store.ensure(authoritative_record)
        resolve_sandbox_workspace_locator(
            locator,
            workspace_root=self._workspace_root,
            expected_workspace_id=expected_id,
            owner_record=authoritative_record,
            expected_workflow_id=current_workflow_id,
            expected_step_execution_id=current_step_execution_id,
            must_exist=False,
        )
        # 3. Decide whether this run may skip materialization. When a repository
        #    source is authored, THIS runtime owns materialization and directory
        #    existence alone is not completion evidence: an earlier attempt that
        #    cloned but then failed during checkout or restore leaves a partially
        #    built directory behind. Only a durable completion marker written after
        #    the full clone/checkout/restore proves the workspace is safe to reuse,
        #    so an existing-but-incomplete directory is torn down and rebuilt
        #    rather than launched from the wrong revision or a partial restore.
        #    When no source is authored, the workspace must have been
        #    pre-materialized by its external owner (for example a remediation
        #    workspace) and is reused as-is.
        already_materialized = workspace.is_dir()
        if (
            source
            and already_materialized
            and not record_store.is_materialized(locator.workspace_id)
        ):
            shutil.rmtree(workspace)
            already_materialized = False
        # 4. A workspace that is neither present nor accompanied by an authored
        #    repository source cannot be created here; reject before running any
        #    command.
        if not already_materialized and not source:
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "authorized sandbox workspace is unavailable and no repository "
                "source was authored to materialize it",
            )
        # 5. Materialize the authored repository/branch and restore inputs exactly
        #    once, then record durable completion. Retries observe the completed
        #    workspace and skip all git and archive mutation.
        materialization: dict[str, Any] = {"action": "reused_pre_materialized"}
        if not already_materialized:
            materialization = await self._materialize_repository(
                workspace,
                repository_source=source,
                starting_branch=starting_branch,
                target_branch=target_branch,
                checkout_commit=checkout_commit,
                github_token=github_token,
            )
            restore_evidence = await self._materialize_restore_inputs(
                workspace,
                restore_input_refs=restore_input_refs,
                artifact_gateway=artifact_gateway,
            )
            if restore_evidence:
                materialization["restoreInputs"] = restore_evidence
            record_store.mark_materialized(locator.workspace_id)
        # 6. Final containment-checked resolution; the directory must now exist.
        workspace = resolve_sandbox_workspace_locator(
            locator,
            workspace_root=self._workspace_root,
            expected_workspace_id=expected_id,
            owner_record=authoritative_record,
            expected_workflow_id=current_workflow_id,
            expected_step_execution_id=current_step_execution_id,
            must_exist=True,
        )
        self._last_workspace_evidence = self._workspace_resolution_evidence(
            locator=locator,
            expected_id=expected_id,
            materialization=materialization,
        )
        return workspace

    async def _materialize_repository(
        self,
        workspace: Path,
        *,
        repository_source: str,
        starting_branch: str | None,
        target_branch: str | None,
        checkout_commit: str | None,
        github_token: str | None,
    ) -> dict[str, Any]:
        """Clone and check out the authored repository state deterministically.

        Uses the shared bounded/redacted/cancellation-aware command runner. The
        GitHub token, when present, is injected only through an in-memory git
        credential helper (never argv or on-disk config) and only for GitHub HTTPS
        sources, honoring declared-capability credential injection policy.
        """

        source, source_kind = self._normalize_repository_source(repository_source)
        if source_kind == "local":
            self._authorize_local_repository_source(source)
        start = self._normalize_branch(starting_branch)
        target = self._normalize_branch(target_branch)
        commit = str(checkout_commit or "").strip()
        workspace.parent.mkdir(parents=True, exist_ok=True)

        git_env = dict(os.environ)
        if source_kind == "github_https" and github_token:
            git_env = build_github_token_git_environment(
                github_token, base_env=os.environ
            )

        clone_args = ["git", "clone"]
        # A remote clone can fetch just the authored branch; a local source keeps
        # every ref so a subsequent checkout can select it.
        if start and source_kind != "local":
            clone_args.extend(["--branch", start, "--single-branch"])
        clone_args.extend(["--", source, str(workspace)])
        code, _out, err = await self._run(*clone_args, env=git_env, check=False)
        if code != 0:
            raise OmnigentOAuthHostError(
                f"workspace repository materialization failed: {err.strip()[:200]}",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )

        checked_out = start or None
        if commit:
            code, _out, err = await self._run(
                "git", "-C", str(workspace), "checkout", "--detach", commit,
                env=git_env, check=False,
            )
            if code != 0:
                raise OmnigentOAuthHostError(
                    f"workspace commit checkout failed: {err.strip()[:200]}",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                )
            checked_out = commit
        elif start and source_kind == "local":
            code, _out, _err = await self._run(
                "git", "-C", str(workspace), "checkout", start,
                env=git_env, check=False,
            )
            if code != 0:
                await self._run(
                    "git", "-C", str(workspace), "checkout", "-B", start,
                    f"origin/{start}", env=git_env, check=False,
                )

        output_branch = None
        if target and target != checked_out:
            # Honor the authored output branch without discarding the checked-out
            # working tree, matching normal MoonMind repository semantics.
            code, _out, err = await self._run(
                "git", "-C", str(workspace), "checkout", "-B", target,
                env=git_env, check=False,
            )
            if code != 0:
                raise OmnigentOAuthHostError(
                    f"workspace output branch selection failed: {err.strip()[:200]}",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                )
            output_branch = target

        return {
            "action": "materialized",
            "sourceKind": source_kind,
            "startingBranch": start,
            "checkedOut": checked_out,
            "outputBranch": output_branch,
            "commit": commit or None,
        }

    async def _materialize_restore_inputs(
        self,
        workspace: Path,
        *,
        restore_input_refs: tuple[str, ...],
        artifact_gateway: Any | None,
    ) -> list[dict[str, Any]]:
        """Materialize checkpoint/external-state restore inputs into the workspace.

        Restore inputs are durable ``artifact://`` references, resolved through the
        artifact owner. A ref that looks like a local path is rejected so an
        artifact ref can never be conflated with a filesystem path. Materialized
        bytes are written under a bounded ``.moonmind/restore`` area inside the
        already-containment-checked workspace.
        """

        refs = [str(ref).strip() for ref in restore_input_refs if str(ref).strip()]
        if not refs:
            return []
        if len(refs) > _MAX_RESTORE_INPUT_REFS:
            raise OmnigentOAuthHostError(
                "too many restore input refs for the authorized workspace",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        artifact_service = self._as_artifact_service(artifact_gateway)
        if artifact_service is None:
            raise OmnigentOAuthHostError(
                "restore inputs require an artifact service to resolve refs",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        restore_root = (workspace / ".moonmind" / "restore").resolve()
        if not restore_root.is_relative_to(workspace.resolve()):  # pragma: no cover
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "restore materialization escaped the authorized workspace",
            )
        restore_root.mkdir(parents=True, exist_ok=True)
        evidence: list[dict[str, Any]] = []
        total_bytes = 0
        for ref in refs:
            if not ref.startswith("artifact://"):
                raise OmnigentOAuthHostError(
                    "restore inputs must be durable artifact refs, not local paths",
                    code=WORKSPACE_LOCATOR_UNSUPPORTED,
                )
            # The durable artifact contract addresses artifacts by id; the
            # canonical ``artifact://`` scheme must be stripped before lookup.
            artifact_id = ref[len("artifact://"):]
            # Enforce the per-ref bound and the single cumulative restore budget
            # together, so many individually-legal refs cannot aggregate past the
            # advertised hostile-input bound.
            per_ref_budget = min(
                _MAX_RESTORE_INPUT_BYTES, _MAX_RESTORE_TOTAL_BYTES - total_bytes
            )
            if per_ref_budget <= 0:
                raise OmnigentOAuthHostError(
                    "restore inputs exceed the cumulative authorized workspace bound",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                )
            await self._reject_oversized_restore_metadata(
                artifact_service, artifact_id, per_ref_budget
            )
            digest = hashlib.sha256(ref.encode("utf-8")).hexdigest()[:24]
            written = await self._write_restore_payload(
                artifact_service,
                artifact_id=artifact_id,
                target=restore_root / digest,
                budget_bytes=per_ref_budget,
            )
            total_bytes += written
            evidence.append({"ref": ref, "bytes": written})
        return evidence

    @staticmethod
    async def _reject_oversized_restore_metadata(
        artifact_service: Any, artifact_id: str, budget_bytes: int
    ) -> None:
        """Reject an oversized restore artifact from metadata before reading bytes.

        When the service can report artifact size cheaply, an oversized payload is
        rejected before any bytes are allocated or written.
        """
        get_metadata = getattr(artifact_service, "get_metadata", None)
        if get_metadata is None:
            return
        try:
            metadata = await get_metadata(
                artifact_id=artifact_id,
                principal=_RESTORE_ARTIFACT_PRINCIPAL,
            )
        except TypeError:
            return
        artifact = metadata[0] if isinstance(metadata, tuple) else metadata
        size_bytes = getattr(artifact, "size_bytes", None)
        if isinstance(size_bytes, int) and size_bytes > budget_bytes:
            raise OmnigentOAuthHostError(
                "restore input exceeds the authorized workspace bound",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )

    @staticmethod
    async def _write_restore_payload(
        artifact_service: Any,
        *,
        artifact_id: str,
        target: Path,
        budget_bytes: int,
    ) -> int:
        """Stream a restore payload to disk under a hard byte budget.

        Streaming through the artifact service's chunked reader rejects an
        oversized payload mid-stream, so it is never fully materialized in worker
        memory. Services that expose only a whole-payload ``read`` are still bound
        after the read completes.
        """
        read_chunks = getattr(artifact_service, "read_chunks", None)
        if read_chunks is not None:
            written = 0
            with target.open("wb") as stream:
                _artifact, chunks = await read_chunks(
                    artifact_id=artifact_id,
                    principal=_RESTORE_ARTIFACT_PRINCIPAL,
                    allow_restricted_raw=True,
                    chunk_size=_RESTORE_STREAM_CHUNK_BYTES,
                )
                for chunk in chunks:
                    written += len(chunk)
                    if written > budget_bytes:
                        stream.close()
                        target.unlink(missing_ok=True)
                        raise OmnigentOAuthHostError(
                            "restore input exceeds the authorized workspace bound",
                            code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                        )
                    stream.write(chunk)
            return written
        _metadata, payload = await artifact_service.read(
            artifact_id=artifact_id,
            principal=_RESTORE_ARTIFACT_PRINCIPAL,
            allow_restricted_raw=True,
        )
        if len(payload) > budget_bytes:
            raise OmnigentOAuthHostError(
                "restore input exceeds the authorized workspace bound",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        target.write_bytes(payload)
        return len(payload)

    @staticmethod
    def _as_artifact_service(artifact_gateway: Any | None) -> Any | None:
        if artifact_gateway is None:
            return None
        # A durable artifact service exposes the by-id ``read``/``read_chunks``
        # contract directly; only a bare byte gateway needs the ref adapter.
        if hasattr(artifact_gateway, "read") or hasattr(artifact_gateway, "read_chunks"):
            return artifact_gateway

        class _GatewayArtifactService:
            async def read(self, *, artifact_id: str, **_kwargs: Any):
                # ``read_bytes`` addresses artifacts by their canonical
                # ``artifact://`` ref, while the durable service contract passes a
                # scheme-stripped id; restore the scheme for the gateway.
                ref = (
                    artifact_id
                    if artifact_id.startswith("artifact://")
                    else f"artifact://{artifact_id}"
                )
                payload = await artifact_gateway.read_bytes(ref)
                return {}, payload

        return _GatewayArtifactService()

    @staticmethod
    def _normalize_repository_source(repository_source: str) -> tuple[str, str]:
        """Resolve an authored repository identity to a clone source and kind."""

        value = str(repository_source or "").strip()
        if not value:
            raise OmnigentOAuthHostError(
                "repository source is required to materialize the workspace",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        # A ``file://`` URL is still a local on-disk read and must be authorized
        # like any other local path, not treated as a trusted remote.
        if value.startswith("file://"):
            return value, "local"
        if value.startswith(("http://", "https://", "git@", "ssh://")):
            kind = "remote"
            if value.startswith(("http://", "https://")):
                # Only inject GitHub credentials when the URL host is exactly
                # github.com. A substring check would misclassify hosts such as
                # ``evil.com/github.com`` or ``github.com.evil.com`` as GitHub and
                # leak the token to an attacker-controlled origin.
                host = (urlsplit(value).hostname or "").lower()
                if host == "github.com":
                    kind = "github_https"
            return value, kind
        if value.startswith(("/", "./", "../")) or Path(value).is_absolute():
            return value, "local"
        if _GITHUB_SLUG.fullmatch(value):
            suffix = "" if value.endswith(".git") else ".git"
            return f"https://github.com/{value}{suffix}", "github_https"
        raise OmnigentOAuthHostError(
            "unsupported repository source; expected owner/repo, URL, or path",
            code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
        )

    def _authorize_local_repository_source(self, source: str) -> None:
        """Reject a local repository source outside the authorized source root.

        Local sources are attacker-influenced (they come from the workflow-authored
        ``workspaceSpec``/parameters). Without containment they could clone any Git
        repository the trusted worker can read, including another run under the
        workspace authority, crossing workspace isolation boundaries.
        """
        raw_path = source[len("file://"):] if source.startswith("file://") else source
        resolved = Path(raw_path).resolve()
        root = self._repository_source_root
        if root is None or not resolved.is_relative_to(root):
            raise OmnigentOAuthHostError(
                "local repository sources are only permitted within an authorized "
                "per-run source root",
                code=WORKSPACE_LOCATOR_UNSUPPORTED,
            )

    @staticmethod
    def _normalize_branch(branch: str | None) -> str | None:
        normalized = str(branch or "").strip()
        while normalized:
            prior = normalized
            for prefix in ("refs/remotes/origin/", "refs/heads/", "origin/"):
                if normalized.startswith(prefix):
                    normalized = normalized[len(prefix):]
            if prior == normalized:
                break
        return normalized or None

    @staticmethod
    def _workspace_resolution_evidence(
        *,
        locator: SandboxWorkspaceLocator,
        expected_id: str,
        materialization: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Assemble bounded, credential-free workspace-resolution evidence."""

        return {
            "locatorKind": locator.kind,
            "workspaceId": locator.workspace_id,
            "relativePath": locator.relative_path,
            "identityVerified": locator.workspace_id == expected_id,
            "materialization": dict(materialization),
        }

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
        binding: OmnigentOAuthHostBinding | None = None,
        workspace_source: Path,
        skill_projection: Path | None = None,
        effective_launch: Mapping[str, Any] | None = None,
    ) -> None:
        child_env = dict(os.environ)
        child_env["OMNIGENT_RUN_WORKSPACE"] = str(workspace_source)
        if binding is not None:
            generation = (
                binding.credential_mount_ref.auth_volume_ref.credential_generation
            )
            adapter = self._runtime_adapter(binding)
            child_env[str(adapter["generation_env"])] = str(generation)
            if binding.credential_mount_ref.auth_volume_ref.runtime_id == "claude_code":
                child_env["CLAUDE_VOLUME_NAME"] = (
                    binding.credential_mount_ref.auth_volume_ref.volume_ref
                )
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
        adapter = (
            self._runtime_adapter(binding)
            if binding is not None
            else _RUNTIME_ADAPTERS["codex_cli"]
        )
        await self._run(
            "docker",
            "compose",
            "-f",
            "docker-compose.yaml",
            "--profile",
            str(adapter["compose_profile"]),
            "up",
            "-d",
            str(adapter["compose_service"]),
            env=child_env,
        )
        await self._run(
            "docker",
            "compose",
            "-f",
            "docker-compose.yaml",
            "--profile",
            str(adapter["compose_profile"]),
            "exec",
            "-T",
            str(adapter["compose_service"]),
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
            adapter = self._runtime_adapter(binding)
            matches = [
                host
                for host in hosts
                if str(host.get("name") or host.get("hostname") or "")
                in {expected_name, str(adapter["compose_service"])}
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
        return_code, stdout, stderr = await run_runtime_command(
            args,
            env=env,
            timeout_seconds=600,
            output_limit_bytes=4096,
        )
        output = stdout.decode("utf-8", errors="replace")
        error = stderr.decode("utf-8", errors="replace")
        if check and return_code != 0:
            raise OmnigentOAuthHostError(
                "OAuth host runtime command failed",
                code=HostPreflightFailure.LOGIN_STATUS_FAILED.value,
            )
        return return_code, output, error


__all__ = ["OmnigentOAuthHostRuntime"]
