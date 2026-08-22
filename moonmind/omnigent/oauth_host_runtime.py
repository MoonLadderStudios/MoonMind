"""Static and on-demand runtime boundary for profile-bound OAuth hosts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from moonmind.config.settings import settings
from moonmind.omnigent.execution_profiles import validate_effective_launch_snapshot
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.mounted_tool_preflight import (
    MountedToolPreflightError,
    preflight_mounted_tools,
)
from moonmind.omnigent.oauth_hosts import (
    HostPreflightFailure,
    OmnigentOAuthHostError,
    deterministic_host_container_name,
    validate_preflight_result,
)
from moonmind.omnigent.settings import OMNIGENT_RUNTIME_ACTIVE_SKILLS_DIR
from moonmind.omnigent.repository_sources import (
    RepositorySourceError,
    normalize_repository_source,
)
from moonmind.publish.service import PublishService
from moonmind.repositories.lore_adapter import (
    LORE_UNSUPPORTED_RUNTIME_LANE,
    LoreRepositoryProviderAdapter,
    LoreWorkspaceError,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    OmnigentHostLease,
    OmnigentOAuthHostBinding,
)
from moonmind.schemas.container_job_models import OwnerIdentity
from moonmind.schemas.workspace_locator_models import (
    WORKSPACE_AUTHORITY_MISMATCH,
    WORKSPACE_LOCATOR_ADAPTER,
    WORKSPACE_LOCATOR_UNSUPPORTED,
    ExternalStateLocator,
    ManagedWorkspaceLocator,
    SandboxWorkspaceLocator,
    WorkspaceLocatorResolutionError,
)
from moonmind.security.container_job_capabilities import (
    mint_container_job_session_capability,
)
from moonmind.security.docker_networks import resolve_control_plane_network
from moonmind.security.egress import (
    OMNIGENT_EGRESS_PROFILE,
    EgressAttestation,
    attest_docker_egress,
    attest_docker_workload_egress,
    omnigent_proxy_env,
)
from moonmind.security.egress_conformance_evidence import (
    parse_and_verify_conformance_evidence,
    serialize_conformance_evidence,
)
from moonmind.security.execution_fanout_capabilities import (
    EXECUTION_FANOUT_REQUIRED_CAPABILITY,
    ExecutionFanoutCapabilityError,
    mint_execution_fanout_capability,
    require_execution_fanout_authorization,
)
from moonmind.utils.logging import redact_sensitive_text
from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
from moonmind.workflows.skills.run_projection import (
    load_resolved_skillset,
    materialize_run_skill_snapshot,
    verify_skill_projection,
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
from moonmind.workloads.docker_launcher import structured_container_security_args

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


@dataclass(frozen=True)
class OmnigentEgressEvidenceRequestIdentity:
    """Credential-free request identity sufficient for protected evidence I/O.

    Cleanup may run in a later janitor Activity where the original execution
    request is intentionally unavailable.  The bridge store persists these
    three immutable fields at launch so that cleanup can publish against the
    original evidence chain without reconstructing or inventing a request.
    """

    correlation_id: str
    idempotency_key: str
    remediation_workspace: dict[str, bool] | None = None

    @classmethod
    def from_request(
        cls, request: AgentExecutionRequest
    ) -> "OmnigentEgressEvidenceRequestIdentity":
        return cls(
            correlation_id=request.correlation_id,
            idempotency_key=request.idempotency_key,
            remediation_workspace=(
                {"authorized": True}
                if request.remediation_workspace is not None
                else None
            ),
        )

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any]
    ) -> "OmnigentEgressEvidenceRequestIdentity":
        correlation_id = str(value.get("correlationId") or "").strip()
        idempotency_key = str(value.get("idempotencyKey") or "").strip()
        if not correlation_id or not idempotency_key:
            raise OmnigentOAuthHostError(
                "durable egress cleanup authority is missing request identity",
                code="OMNIGENT_EGRESS_CLEANUP_AUTHORITY_INVALID",
            )
        return cls(
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            remediation_workspace=(
                {"authorized": True} if value.get("remediation") is True else None
            ),
        )

    def as_mapping(self) -> dict[str, Any]:
        return {
            "correlationId": self.correlation_id,
            "idempotencyKey": self.idempotency_key,
            "remediation": self.remediation_workspace is not None,
        }


_TOOLS_PATH = "/opt/moonmind-tools/bin"
_DEFAULT_HOST_PATH = (
    "/opt/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin"
)
_RUNNER_PROXY_ENV_NAMES = tuple(
    proxy_env.partition("=")[0] for proxy_env in omnigent_proxy_env()
)
_RUNNER_GITHUB_ENV_NAMES = (
    "XDG_CONFIG_HOME",
    "GH_PROMPT_DISABLED",
    "GH_NO_UPDATE_NOTIFIER",
    "GH_NO_EXTENSION_UPDATE_NOTIFIER",
)
# Omnigent deliberately filters the environment again when its runner launches
# a provider harness.  Codex tool shells are login shells, so this exact
# non-secret subset is also materialized through the lease-owned profile under
# ``/etc/profile.d``.  Keep credential-bearing values (notably the scoped
# container-job and execution-fan-out bearers) out of the generated file.
_RUNTIME_EXECUTION_PROFILE_ENV_NAMES = (
    "MOONMIND_URL",
    "MOONMIND_AGENT_RUN_ID",
    "MOONMIND_TASK_WORKFLOW_ID",
    "MOONMIND_STEP_ID",
    "MOONMIND_RUNTIME_ID",
    "MOONMIND_CONTAINER_JOBS_MCP_URL",
    "MOONMIND_CONTAINER_JOBS_SOURCE_KIND",
    "MOONMIND_CONTAINER_JOBS_SESSION_ID",
    "MOONMIND_CONTAINER_JOBS_WORKSPACE_KIND",
    "MOONMIND_CONTAINER_JOBS_WORKSPACE_ID",
    "MOONMIND_CONTAINER_JOBS_WORKSPACE_RELATIVE_PATH",
    "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN_FILE",
    "MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN_FILE",
)
_SECRET_RUNTIME_ENV_NAMES = frozenset(
    {
        "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN",
        "MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN",
    }
)
_RUNTIME_CAPABILITY_FILE_ENV = {
    "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN": (
        "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN_FILE",
        "container-jobs",
    ),
    "MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN": (
        "MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN_FILE",
        "execution-fanout",
    ),
}
_RUNTIME_CAPABILITY_MOUNT_ROOT = "/opt/moonmind/capabilities"
_DIGEST_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_PLACEHOLDER_DIGEST = "0" * 64
_SAFE_NETWORK = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_SAFE_VOLUME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,254}$")
_SAFE_STEP_EXECUTION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,510}[A-Za-z0-9]$")
# Bound restore-input materialization so a hostile or oversized artifact ref
# cannot exhaust the authorized workspace before the host launches. The limit is
# enforced both per ref and cumulatively across every accepted ref so the
# advertised hostile-input bound cannot be defeated by fanning bytes across many
# individually-legal refs.
_MAX_RESTORE_INPUT_REFS = 64
_MAX_RESTORE_INPUT_BYTES = 64 * 1024 * 1024
_MAX_RESTORE_TOTAL_BYTES = 256 * 1024 * 1024
# Stock Omnigent first publishes an offline catalog row, then replaces it with
# the live host identity after the runner tunnel is ready. Cold image and
# catalog startup has exceeded one minute on the supported local Compose path;
# keep the wait bounded but large enough for that authoritative online edge.
_HOST_REGISTRATION_ATTEMPTS = 91
_HOST_REGISTRATION_INTERVAL_SECONDS = 2
_HOST_EXEC_PREFLIGHT_ATTEMPTS = 11
_HOST_EXEC_PREFLIGHT_INTERVAL_SECONDS = 1
_DEFAULT_PUBLISH_GIT_USER_NAME = "MoonMind Worker"
_DEFAULT_PUBLISH_GIT_USER_EMAIL = "moonmind-worker@users.noreply.github.com"
# Restore payloads are read through the durable artifact contract as raw bytes,
# under a dedicated service principal (mirrors the checkpoint/remediation
# restorers, which never read restore material as an end user).
_RESTORE_ARTIFACT_PRINCIPAL = "service:omnigent_workspace_restore"
# Declared input attachments are a distinct input authority from checkpoint
# restore material, so they read under their own service principal. Conflating
# the two would let an attachment ref borrow the restore authority (or the
# reverse); keeping principals separate preserves the authority boundary.
_ATTACHMENT_ARTIFACT_PRINCIPAL = "service:omnigent_workspace_attachment"
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
        "login_command": ("codex", "login", "status"),
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
        "login_command": ("claude", "auth", "status"),
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
        lore_repository_adapter: LoreRepositoryProviderAdapter | None = None,
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
        self._network = (
            network
            or os.getenv("OMNIGENT_HOST_NETWORK")
            or resolve_control_plane_network()
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
        self._workspace_volume = os.getenv(
            "MOONMIND_AGENT_WORKSPACES_VOLUME_NAME", "agent_workspaces"
        ).strip()
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
        self._lore_repository_adapter = lore_repository_adapter
        # Bounded, non-sensitive evidence of the most recent workspace resolution,
        # surfaced through the preflight result for Workflow Detail. Never carries
        # credentials, raw daemon paths, or unbounded command output.
        self._last_workspace_evidence: dict[str, Any] = {}
        # Bounded, non-sensitive evidence of the most recent workspace-resolution
        # *denial*. Carries the failed authority class, stable reason code,
        # retryability, whether owned partial state was created, and the
        # reconciliation requirement. Never carries credentials or raw paths.
        self._last_workspace_denial_evidence: dict[str, Any] = {}

    @staticmethod
    def _deployment_compose_command() -> tuple[str, ...]:
        """Resolve Compose through the deployment-owned checkout and project."""

        local_root = Path(
            os.getenv("MOONMIND_DEPLOYMENT_LOCAL_PROJECT_DIR", "") or Path.cwd()
        ).expanduser()
        host_root_text = os.getenv("MOONMIND_DEPLOYMENT_PROJECT_DIR", "").strip()
        compose_text = os.getenv("MOONMIND_DEPLOYMENT_COMPOSE_FILE", "").strip()
        if compose_text:
            configured = Path(compose_text).expanduser()
            if configured.is_absolute() and not configured.exists():
                relative: Path | None = None
                if host_root_text:
                    try:
                        relative = configured.relative_to(
                            Path(host_root_text).expanduser()
                        )
                    except ValueError:
                        relative = None
                compose_path = local_root / (relative or configured.name)
            elif configured.is_absolute():
                compose_path = configured
            else:
                compose_path = local_root / configured
        else:
            compose_path = local_root / "docker-compose.yaml"
        project_name = (
            os.getenv("MOONMIND_DEPLOYMENT_PROJECT_NAME", "moonmind").strip()
            or "moonmind"
        )
        if not _SAFE_NETWORK.fullmatch(project_name):
            raise OmnigentOAuthHostError(
                "deployment Compose project identity is unsafe",
                code="OMNIGENT_DEPLOYMENT_COMPOSE_UNAVAILABLE",
            )
        if not compose_path.is_file():
            raise OmnigentOAuthHostError(
                "deployment Compose file is unavailable",
                code="OMNIGENT_DEPLOYMENT_COMPOSE_UNAVAILABLE",
            )
        return (
            "docker",
            "compose",
            "--project-name",
            project_name,
            "-f",
            str(compose_path.resolve()),
        )

    async def validate_credential_mount(
        self,
        *,
        binding: OmnigentOAuthHostBinding,
        host_lease: OmnigentHostLease,
        effective_launch: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Validate OAuth in the selected host image without launching execution."""

        launch = self._validate_effective_launch(
            binding=binding, effective_launch=effective_launch
        )
        adapter = self._runtime_adapter(binding)
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
        if await self._container_present(container_name):
            await self._assert_container_owned(container_name, host_lease.lease_id)
            await self._run("docker", "rm", "-f", container_name, check=False)
            if await self._container_present(container_name):
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
            (
                "type=volume,"
                f"src={auth_volume.volume_ref},dst={adapter['home']},readonly"
            ),
            "--env",
            f"PATH={self._prepend_tools_path(host_path)}",
            "--env",
            "HOME=/home/app",
            "--env",
            f"{adapter['generation_env']}={host_lease.credential_generation}",
        ]
        for runtime_env in adapter["env"]:
            args.extend(["--env", runtime_env])
        args.extend(["--entrypoint", "/usr/bin/env"])
        for key in _FORBIDDEN_ENV:
            args.extend(["-u", key])
        args.extend([host_image_ref, *adapter["login_command"]])
        try:
            result = await asyncio.wait_for(self._run(*args, check=False), timeout=60)
        except TimeoutError as exc:
            raise OmnigentOAuthHostError(
                "OAuth credential validation timed out",
                code=HostPreflightFailure.LOGIN_STATUS_FAILED.value,
            ) from exc
        if result[0] != 0:
            raise OmnigentOAuthHostError(
                "OAuth credential validation failed",
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
        evidence_request: AgentExecutionRequest | None = None,
        cleanup_authority_store: Any | None = None,
        target_repository: str = "",
        required_capabilities: tuple[str, ...] = (),
        execution_fanout_authorization: Mapping[str, Any] | None = None,
        github_token: str | None = None,
        github_mutation_required: bool = False,
        effective_launch: Mapping[str, Any] | None = None,
        repository_source: str = "",
        repository_provider: str = "",
        repository_connection_ref: str = "",
        repository_client_evidence: Mapping[str, str] | None = None,
        starting_branch: str | None = None,
        target_branch: str | None = None,
        checkout_commit: str | None = None,
        restore_input_refs: tuple[str, ...] = (),
        workspace_checkpoint_restore_ref: str | None = None,
        attachment_refs: tuple[str, ...] = (),
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
        normalized_capabilities = {
            str(item or "").strip().lower() for item in required_capabilities
        }
        try:
            require_execution_fanout_authorization(
                required_capabilities,
                execution_fanout_authorization,
            )
        except ExecutionFanoutCapabilityError as exc:
            raise OmnigentOAuthHostError(str(exc), code="authorization_denied") from exc
        if (
            EXECUTION_FANOUT_REQUIRED_CAPABILITY in normalized_capabilities
            and not binding.host_launch_profile_ref
        ):
            raise OmnigentOAuthHostError(
                "execution fan-out requires a run-dedicated Omnigent host",
                code="OMNIGENT_RUNTIME_CAPABILITY_UNSUPPORTED",
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
            repository_provider=repository_provider,
            repository_connection_ref=repository_connection_ref,
            repository_client_evidence=repository_client_evidence,
            starting_branch=starting_branch,
            target_branch=target_branch,
            checkout_commit=checkout_commit,
            restore_input_refs=restore_input_refs,
            workspace_checkpoint_restore_ref=workspace_checkpoint_restore_ref,
            attachment_refs=attachment_refs,
            github_token=github_token,
            artifact_gateway=artifact_gateway,
            omnigent_isolation_verified=(
                launch.get("hostMode") == "on_demand_docker"
                and "workspace" in set(launch.get("mountClasses") or ())
            ),
        )
        server_image_evidence = await self._attest_server_image(launch)
        await asyncio.to_thread(
            self._align_workspace_ownership,
            workspace_source,
            runtime_uid=int(launch["runtimeUid"]),
            runtime_gid=int(launch["runtimeGid"]),
        )
        daemon_workspace_root = await self._resolve_daemon_workspace_root()
        daemon_workspace_source = daemon_visible_workspace_path(
            workspace_source,
            daemon_root=daemon_workspace_root,
        )
        daemon_skill_projection = daemon_visible_workspace_path(
            skill_projection,
            daemon_root=daemon_workspace_root,
        )
        launched_container_name: str | None = None
        static_compose_env: Mapping[str, str] | None = None
        if binding.host_launch_profile_ref:
            container_job_environment = self._container_job_environment(
                binding=binding,
                host_lease=host_lease,
                workspace_locator=workspace_locator,
                current_workflow_id=current_workflow_id,
                current_step_execution_id=current_step_execution_id,
                timeout_seconds=int(launch["limits"]["timeoutSeconds"]),
                required_capabilities=required_capabilities,
                execution_fanout_authorization=execution_fanout_authorization,
            )
            daemon_runtime_scripts = self._prepare_daemon_runtime_scripts(
                host_lease.lease_id,
                current_step_execution_id=current_step_execution_id,
                runtime_environment=container_job_environment,
                daemon_workspace_root=daemon_workspace_root,
            )
            host_runtime_environment = self._host_runtime_environment(
                container_job_environment
            )
            if "gh" in {item.strip().lower() for item in required_capabilities}:
                await self._initialize_required_tools()
            container_name = (
                host_lease.container_name
                or deterministic_host_container_name(host_lease.lease_id)
            )
            launched_container_name = container_name
            await self._launch_on_demand(
                binding=binding,
                host_lease=host_lease,
                container_name=container_name,
                workspace_source=daemon_workspace_source,
                skill_projection=daemon_skill_projection,
                runtime_scripts=daemon_runtime_scripts,
                current_step_execution_id=current_step_execution_id,
                github_token=github_token,
                container_job_environment=host_runtime_environment,
                effective_launch=launch,
                egress_attestation=egress_attestation,
            )
        else:
            static_compose_env = await self._compose_static_check(
                binding=binding,
                workspace_source=daemon_workspace_source,
                skill_projection=daemon_skill_projection,
                effective_launch=launch,
                egress_attestation=egress_attestation,
            )

        launch_ref: str | None = None
        attachment_identity = launched_container_name or ""
        egress_evidence: dict[str, Any] = {
            **egress_attestation.model_dump(by_alias=True, mode="json"),
            **(
                {
                    "attachmentIdentity": attachment_identity,
                    "attachmentRef": f"container:{attachment_identity}",
                }
                if attachment_identity
                else {}
            ),
            "executionProfileRef": launch.get("executionProfileRef"),
            "launchPolicyRef": launch.get("launchPolicyRef"),
            "policyAuthority": launch.get("policyAuthority"),
            "serverImageRef": launch.get("serverImageRef"),
            "hostImageRef": launch.get("hostImageRef"),
            "releasedArchitectures": list(launch.get("architectures") or ()),
            "hostMode": launch.get("hostMode"),
            "runtimeProvenance": "omnigent",
            **server_image_evidence,
        }

        def prepared_host_evidence() -> dict[str, Any]:
            return {
                "status": "launched",
                "providerProfileId": binding.provider_profile_id,
                "runtimeId": binding.credential_mount_ref.auth_volume_ref.runtime_id,
                "credentialGeneration": host_lease.credential_generation,
                "workspacePath": "/workspaces/run",
                "egressAttestation": dict(egress_evidence),
                "egressEvidenceRef": launch_ref,
            }

        try:
            # Resolve the production-created container immediately. From this
            # point onward every fallible check has a bounded cleanup handoff.
            attachment_identity = await self._resolve_workload_attachment_identity(
                binding=binding,
                host_lease=host_lease,
                container_name=launched_container_name,
            )
            egress_evidence.update(
                {
                    "attachmentIdentity": attachment_identity,
                    "attachmentRef": f"container:{attachment_identity}",
                }
            )
            existing_authority = None
            if cleanup_authority_store is not None and hasattr(
                cleanup_authority_store, "get_egress_cleanup_authority"
            ):
                existing_authority = (
                    await cleanup_authority_store.get_egress_cleanup_authority(
                        host_lease_ref=host_lease.lease_id
                    )
                )
            if evidence_request is not None and artifact_gateway is not None:
                if isinstance(existing_authority, Mapping):
                    stored_launch = existing_authority.get("effectiveLaunch")
                    stored_evidence = existing_authority.get("egressEvidence")
                    if (
                        not isinstance(stored_launch, Mapping)
                        or stored_launch.get("snapshotRef") != launch.get("snapshotRef")
                        or not isinstance(stored_evidence, Mapping)
                        or stored_evidence.get("attachmentIdentity")
                        != attachment_identity
                    ):
                        raise OmnigentOAuthHostError(
                            "durable egress cleanup authority does not match the launch",
                            code="OMNIGENT_EGRESS_CLEANUP_AUTHORITY_INVALID",
                        )
                    launch_ref = (
                        str(existing_authority.get("launchEvidenceRef") or "").strip()
                        or None
                    )
                else:
                    provisional_launch = self._host_egress_evidence_payload(
                        binding=binding,
                        host_lease=host_lease,
                        launch=launch,
                        egress_evidence=egress_evidence,
                        evidence_request=evidence_request,
                        state="launched",
                        cleanup_result="pending",
                        reconciliation_result="not_required",
                    )
                    launch_ref = await self._publish_host_egress_evidence(
                        artifact_gateway=artifact_gateway,
                        evidence_request=evidence_request,
                        name=(
                            f"omnigent-{host_lease.lease_id}"
                            "-egress-launch-pending.json"
                        ),
                        payload=provisional_launch,
                    )
                    if cleanup_authority_store is not None:
                        await cleanup_authority_store.bind_egress_cleanup_authority(
                            request=evidence_request,
                            host_lease_ref=host_lease.lease_id,
                            egress_evidence=egress_evidence,
                            launch_evidence_ref=launch_ref,
                            phase="launched",
                        )

            observed_egress = await self._attest_launched_workload_egress(
                attestation=egress_attestation,
                attachment_identity=attachment_identity,
                expected_image_ref=str(launch["hostImageRef"]),
            )
            observed_egress["attachmentRef"] = f"container:{attachment_identity}"
            egress_evidence.update(observed_egress)
            if evidence_request is not None and artifact_gateway is not None:
                existing_phase = (
                    str(existing_authority.get("phase") or "attested")
                    if isinstance(existing_authority, Mapping)
                    else ""
                )
                if existing_phase == "attested":
                    launch_ref = self._validate_reused_cleanup_authority(
                        authority=existing_authority,
                        launch=launch,
                        egress_evidence=egress_evidence,
                    )
                else:
                    launch_evidence = self._host_egress_evidence_payload(
                        binding=binding,
                        host_lease=host_lease,
                        launch=launch,
                        egress_evidence=egress_evidence,
                        evidence_request=evidence_request,
                        state="launched",
                        cleanup_result="pending",
                        reconciliation_result="not_required",
                    )
                    launch_ref = await self._publish_host_egress_evidence(
                        artifact_gateway=artifact_gateway,
                        evidence_request=evidence_request,
                        name=f"omnigent-{host_lease.lease_id}-egress-launch.json",
                        payload=launch_evidence,
                    )
                    if cleanup_authority_store is not None:
                        await cleanup_authority_store.bind_egress_cleanup_authority(
                            request=evidence_request,
                            host_lease_ref=host_lease.lease_id,
                            egress_evidence=egress_evidence,
                            launch_evidence_ref=launch_ref,
                            phase="attested",
                        )

            # Only after full cleanup authority is durable may host login,
            # projection, registration, harness, or mounted-tool checks run.
            if binding.host_launch_profile_ref:
                await self._exec_check(attachment_identity)
                await self._exec_tools_check(attachment_identity)
            else:
                await self._compose_static_exec_check(
                    binding=binding,
                    env=static_compose_env,
                )
            host = await self._resolve_exact_host(
                binding=binding, host_lease=host_lease
            )
            host_id = str(
                host.get("id") or host.get("host_id") or host.get("hostId") or ""
            )
            if adapter["harness"] not in self._ready_host_harnesses(host):
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
                "egressAttestation": egress_evidence,
                "workspacePath": "/workspaces/run",
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
            validated["egressEvidenceRef"] = launch_ref
            return validated
        except (Exception, asyncio.CancelledError) as exc:
            evidence = prepared_host_evidence()
            try:
                exc.prepared_host_evidence = evidence  # type: ignore[attr-defined]
            except (AttributeError, TypeError):
                raise OmnigentOAuthHostError(
                    "Omnigent post-launch preflight failed with cleanup authority",
                    code="OMNIGENT_POST_LAUNCH_PREFLIGHT_FAILED",
                    egress_evidence_ref=launch_ref,
                    prepared_host_evidence=evidence,
                ) from exc
            raise

    @staticmethod
    async def _verified_no_commit_publication(
        *,
        run_command: Any,
        base_branch: str,
    ) -> dict[str, Any]:
        """Prove the unchanged local checkout is the exact remote base head."""

        normalized_base = str(base_branch or "").strip()
        if not normalized_base:
            raise OmnigentOAuthHostError(
                "no-commit publication requires an authoritative base branch",
                code="OMNIGENT_REPOSITORY_PUBLICATION_UNVERIFIED",
            )
        head_result = await run_command(["git", "rev-parse", "HEAD"])
        head_sha = str(head_result.stdout or "").strip().lower()
        remote_ref = f"refs/heads/{normalized_base}"
        remote_result = await run_command(
            ["git", "ls-remote", "--heads", "origin", remote_ref],
            check=False,
        )
        remote_heads = {
            fields[0].lower()
            for line in str(remote_result.stdout or "").splitlines()
            if len(fields := line.split()) >= 2 and fields[1] == remote_ref
        }
        if (
            getattr(remote_result, "returncode", 1) != 0
            or re.fullmatch(r"[0-9a-f]{40,64}", head_sha) is None
            or remote_heads != {head_sha}
        ):
            raise OmnigentOAuthHostError(
                "unchanged repository head did not match the exact remote base",
                code="OMNIGENT_REPOSITORY_PUBLICATION_UNVERIFIED",
            )
        return {
            "push_status": "no_commits",
            "push_branch": normalized_base,
            "push_base_branch": normalized_base,
            "push_head_sha": head_sha,
            "push_commit_count": 0,
            "remote_verified": True,
        }

    async def publish_workspace(
        self,
        *,
        workspace_locator: Mapping[str, Any],
        current_workflow_id: str,
        current_step_execution_id: str,
        publication_identity: str,
        publish_mode: str,
        base_branch: str | None,
        repository: str,
        github_token: str | None,
    ) -> dict[str, Any]:
        """Publish the exact authorized sandbox workspace through one service.

        Profile-bound Omnigent execution does not use the managed-run store, so
        its successful result cannot borrow ``agent_runtime.fetch_result``'s
        publication side effect.  This owning runtime already holds the typed
        workspace authority and resolved credential; it therefore resolves that
        same workspace again, then delegates commit/scan/push semantics to the
        canonical :class:`PublishService` before host cleanup removes access.
        """

        normalized_mode = str(publish_mode or "none").strip().lower()
        if normalized_mode not in {"branch", "pr"}:
            return {"push_status": "skipped"}
        locator = WORKSPACE_LOCATOR_ADAPTER.validate_python(workspace_locator)
        if not isinstance(locator, SandboxWorkspaceLocator):
            raise OmnigentOAuthHostError(
                "Omnigent repository publication requires sandbox workspace authority",
                code=WORKSPACE_LOCATOR_UNSUPPORTED,
            )
        expected_id = hashlib.sha256(
            f"{current_workflow_id}:{current_step_execution_id}".encode("utf-8")
        ).hexdigest()[:24]
        owner_record = SandboxWorkspaceRecordStore(self._workspace_root).load(
            locator.workspace_id
        )
        workspace = resolve_sandbox_workspace_locator(
            locator,
            workspace_root=self._workspace_root,
            expected_workspace_id=expected_id,
            owner_record=owner_record,
            expected_workflow_id=current_workflow_id,
            expected_step_execution_id=current_step_execution_id,
            must_exist=True,
        )
        safe_workspace = workspace.resolve(strict=True)
        token = str(github_token or "").strip()
        command_env = build_github_token_git_environment(
            token,
            base_env=os.environ,
        )
        git_user_name = (
            str(settings.workflow.git_user_name or "").strip()
            or _DEFAULT_PUBLISH_GIT_USER_NAME
        )
        git_user_email = (
            str(settings.workflow.git_user_email or "").strip()
            or _DEFAULT_PUBLISH_GIT_USER_EMAIL
        )
        command_env.update(
            {
                "GIT_AUTHOR_NAME": git_user_name,
                "GIT_COMMITTER_NAME": git_user_name,
                "GIT_AUTHOR_EMAIL": git_user_email,
                "GIT_COMMITTER_EMAIL": git_user_email,
            }
        )

        async def run_command(
            command: list[str],
            *,
            cwd: Path | None = None,
            check: bool = True,
            env: Mapping[str, str] | None = None,
            **_kwargs: Any,
        ) -> SimpleNamespace:
            del cwd
            authored = [str(part) for part in command]
            if authored and authored[0] == "git":
                authored[1:1] = [
                    "-c",
                    f"safe.directory={safe_workspace}",
                    "-C",
                    str(safe_workspace),
                ]
            selected_env = build_github_token_git_environment(
                token,
                base_env=(dict(env) if env is not None else command_env),
            )
            code, stdout, stderr = await self._run(
                *authored,
                env=selected_env,
                check=False,
            )
            if check and code != 0:
                detail = redact_sensitive_text(stderr or stdout or "git failed")
                raise OmnigentOAuthHostError(
                    f"repository publication command failed: {detail[:512]}",
                    code="OMNIGENT_REPOSITORY_PUBLICATION_FAILED",
                )
            return SimpleNamespace(
                stdout=stdout,
                stderr=stderr,
                returncode=code,
            )

        publisher = PublishService()
        published = await publisher.publish(
            job_id=uuid5(NAMESPACE_URL, publication_identity),
            instruction="Publish completed Omnigent repository work",
            # Pull-request creation remains owned by the parent workflow. This
            # boundary publishes and verifies the head branch it will consume.
            publish_mode="branch",
            publish_base_branch=str(base_branch or "main").strip() or "main",
            runtime_mode="omnigent",
            repo_dir=safe_workspace,
            run_command=run_command,
            repo=str(repository or "").strip() or None,
            github_token=token or None,
            publish_existing_commits=True,
            verify_remote=True,
        )
        if published is None:
            return {"push_status": "skipped"}
        if published.status == "skipped":
            return await self._verified_no_commit_publication(
                run_command=run_command,
                base_branch=(
                    str(published.base_branch or base_branch or "main").strip()
                    or "main"
                ),
            )
        if (
            published.status != "published"
            or not published.branch_pushed
            or not published.remote_verified
            or not published.head_sha
            or not published.branch_name
            or not published.base_branch
            or not published.commits_ahead_of_base
        ):
            raise OmnigentOAuthHostError(
                "repository publication did not produce authoritative remote evidence",
                code="OMNIGENT_REPOSITORY_PUBLICATION_UNVERIFIED",
            )
        result: dict[str, Any] = {
            "push_status": "pushed",
            "push_branch": published.branch_name,
            "push_base_branch": published.base_branch,
            "push_head_sha": published.head_sha,
            "push_commit_count": published.commits_ahead_of_base,
            "remote_verified": True,
            "pushRef": (
                f"git://{str(repository or 'repository').strip()}"
                f"/refs/heads/{published.branch_name}@{published.head_sha}"
            ),
        }
        if normalized_mode == "pr" and repository and token:
            pull_request = await GitHubService().resolve_pull_request_selector(
                repo=str(repository).strip(),
                selector=published.branch_name,
                github_token=token,
            )
            if pull_request.resolved and pull_request.pr_url:
                result["pull_request_url"] = pull_request.pr_url
        return result

    async def inspect_session_completion(self, session_id: str) -> dict[str, Any]:
        """Return bounded terminal-answer evidence for one Omnigent session.

        A native Codex turn can report an idle/completed status after a tool
        result without producing the assistant's final answer. Provider status
        alone is therefore not authoritative task-completion evidence. This
        check reads only item ordering and roles; prompt text, assistant text,
        tool arguments, and tool output never cross this boundary.
        """

        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise OmnigentOAuthHostError(
                "Omnigent session completion inspection requires a session id",
                code="OMNIGENT_SESSION_COMPLETION_EVIDENCE_MISSING",
            )
        snapshot = await self._client.get_session(normalized_session_id)
        raw_items = snapshot.get("items") if isinstance(snapshot, Mapping) else None
        items = raw_items if isinstance(raw_items, list) else []
        last_user_index = -1
        last_tool_index = -1
        terminal_assistant_index = -1
        assistant_message_count = 0
        tool_result_count = 0
        for index, raw_item in enumerate(items):
            if not isinstance(raw_item, Mapping):
                continue
            item_type = str(raw_item.get("type") or "").strip()
            data = raw_item.get("data")
            item_data = data if isinstance(data, Mapping) else {}
            if item_type == "message":
                role = str(item_data.get("role") or "").strip().lower()
                if role == "user":
                    last_user_index = index
                elif role == "assistant":
                    content = item_data.get("content")
                    has_text = bool(
                        isinstance(content, list)
                        and any(
                            isinstance(block, Mapping)
                            and str(block.get("text") or "").strip()
                            for block in content
                        )
                    )
                    if has_text:
                        assistant_message_count += 1
                        terminal_assistant_index = index
            elif item_type in {"function_call", "function_call_output"}:
                last_tool_index = index
                if item_type == "function_call_output":
                    tool_result_count += 1

        completion_boundary = max(last_user_index, last_tool_index)
        return {
            "sessionStatus": str(snapshot.get("status") or "").strip(),
            "itemCount": len(items),
            "assistantMessageCount": assistant_message_count,
            "toolResultCount": tool_result_count,
            "terminalAssistantAfterWork": terminal_assistant_index
            > completion_boundary,
        }

    async def _attest_egress(self, launch: Mapping[str, Any]):
        if launch.get("networkRef") != OMNIGENT_EGRESS_PROFILE.network_ref:
            raise OmnigentOAuthHostError(
                "launch egress profile does not map to supported backend state",
                code="OMNIGENT_LAUNCH_EGRESS_UNATTESTED",
            )

        async def runner(args):
            code, stdout, stderr = await self._run("docker", *args, check=False)
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

    async def _attest_server_image(self, launch: Mapping[str, Any]) -> dict[str, str]:
        """Bind the live Omnigent server container to its immutable image."""

        service = await self._run(
            *self._deployment_compose_command(),
            "ps",
            "-q",
            "omnigent",
            check=False,
        )
        container_ids = [
            item.strip() for item in service[1].splitlines() if item.strip()
        ]
        if service[0] != 0 or len(container_ids) != 1:
            raise OmnigentOAuthHostError(
                "live Omnigent server identity is unavailable",
                code="OMNIGENT_SERVER_IMAGE_UNATTESTED",
            )
        container_id = container_ids[0]
        configured = await self._run(
            "docker",
            "inspect",
            "--format",
            "{{json .Config.Image}}",
            container_id,
            check=False,
        )
        observed = await self._run(
            "docker",
            "inspect",
            "--format",
            "{{json .Image}}",
            container_id,
            check=False,
        )
        try:
            configured_ref = str(json.loads(configured[1])).strip()
            image_digest = str(json.loads(observed[1])).strip()
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OmnigentOAuthHostError(
                "live Omnigent server image metadata is invalid",
                code="OMNIGENT_SERVER_IMAGE_UNATTESTED",
            ) from exc
        declared_ref = str(launch.get("serverImageRef") or "").strip()
        if (
            configured[0] != 0
            or observed[0] != 0
            or not configured_ref
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest)
        ):
            raise OmnigentOAuthHostError(
                "live Omnigent server digest is unavailable",
                code="OMNIGENT_SERVER_IMAGE_UNATTESTED",
            )
        image_metadata_result = await self._run(
            "docker",
            "image",
            "inspect",
            "--format",
            (
                '{"repoDigests":{{json .RepoDigests}},'
                '"architecture":{{json .Architecture}}}'
            ),
            image_digest,
            check=False,
        )
        try:
            image_metadata = json.loads(image_metadata_result[1])
            repo_digests = {
                str(item).strip()
                for item in image_metadata["repoDigests"]
                if str(item).strip()
            }
            architecture = str(image_metadata["architecture"]).strip()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OmnigentOAuthHostError(
                "live Omnigent server image metadata is unavailable",
                code="OMNIGENT_SERVER_IMAGE_UNATTESTED",
            ) from exc
        if image_metadata_result[0] != 0 or declared_ref not in repo_digests:
            raise OmnigentOAuthHostError(
                "live Omnigent server image does not match launch authority",
                code="OMNIGENT_SERVER_IMAGE_MISMATCH",
            )
        released_architectures = set(launch.get("architectures") or ())
        if released_architectures and architecture not in released_architectures:
            raise OmnigentOAuthHostError(
                "live Omnigent server architecture is not released",
                code="OMNIGENT_SERVER_ARCHITECTURE_MISMATCH",
            )
        return {
            "serverAttachmentIdentity": container_id,
            "serverImageRefObserved": declared_ref,
            "serverImageDigest": image_digest,
            "serverArchitecture": architecture,
        }

    @staticmethod
    def _authority_version(ref: object) -> str:
        text = str(ref or "").strip()
        return text.rsplit("@", 1)[1] if "@" in text else text

    @staticmethod
    def _attestation_from_workload_evidence(
        evidence: Mapping[str, Any],
    ) -> EgressAttestation:
        """Recover only the immutable gateway-attestation fields."""

        aliases = (
            "profileRef",
            "profileDigest",
            "enforcerImplementation",
            "backendRef",
            "networkRef",
            "gatewayRef",
            "appliedRuleDigest",
            "configDigest",
            "gatewayImageDigest",
            "healthResult",
            "validatedAt",
            "validationResult",
            "deniedConnectionCount",
            "diagnostics",
        )
        return EgressAttestation.model_validate(
            {alias: evidence[alias] for alias in aliases if alias in evidence}
        )

    @staticmethod
    def _evidence_time(value: object) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    @staticmethod
    def _validate_reused_cleanup_authority(
        *,
        authority: Mapping[str, Any],
        launch: Mapping[str, Any],
        egress_evidence: Mapping[str, Any],
    ) -> str:
        stored_launch = authority.get("effectiveLaunch")
        stored_evidence = authority.get("egressEvidence")
        launch_ref = str(authority.get("launchEvidenceRef") or "").strip()
        if (
            not isinstance(stored_launch, Mapping)
            or not isinstance(stored_evidence, Mapping)
            or stored_launch.get("snapshotRef") != launch.get("snapshotRef")
            or not launch_ref
        ):
            raise OmnigentOAuthHostError(
                "durable egress cleanup authority does not match the launch",
                code="OMNIGENT_EGRESS_CLEANUP_AUTHORITY_INVALID",
            )
        immutable_fields = (
            "profileRef",
            "profileDigest",
            "enforcerImplementation",
            "backendRef",
            "networkRef",
            "gatewayRef",
            "appliedRuleDigest",
            "configDigest",
            "gatewayImageDigest",
            "attachmentIdentity",
            "networkIdentity",
            "endpointIdentity",
            "workloadImageDigest",
            "workloadImageRef",
            "architecture",
            "serverAttachmentIdentity",
            "serverImageRefObserved",
            "serverImageDigest",
            "serverArchitecture",
            "hostMode",
            "runtimeProvenance",
        )
        if any(
            stored_evidence.get(field) != egress_evidence.get(field)
            for field in immutable_fields
        ):
            raise OmnigentOAuthHostError(
                "live host identity changed from durable cleanup authority",
                code="OMNIGENT_EGRESS_CLEANUP_AUTHORITY_MISMATCH",
            )
        return launch_ref

    @classmethod
    def _host_egress_evidence_payload(
        cls,
        *,
        binding: OmnigentOAuthHostBinding,
        host_lease: OmnigentHostLease,
        launch: Mapping[str, Any],
        egress_evidence: Mapping[str, Any],
        evidence_request: AgentExecutionRequest | OmnigentEgressEvidenceRequestIdentity,
        state: str,
        cleanup_result: str,
        reconciliation_result: str,
        launch_evidence_ref: str | None = None,
    ) -> dict[str, Any]:
        host_mode = str(launch.get("hostMode") or "")
        remediation = evidence_request.remediation_workspace is not None
        conformance_row = f"remediation_{host_mode}" if remediation else host_mode
        policy_authority = launch.get("policyAuthority")
        payload = {
            "schemaVersion": 1,
            "kind": "restricted-egress-omnigent-conformance",
            "conformanceRow": conformance_row,
            "state": state,
            "workflowCorrelationId": evidence_request.correlation_id,
            "idempotencyKey": evidence_request.idempotency_key,
            "providerProfileId": binding.provider_profile_id,
            "providerLeaseRef": host_lease.provider_lease_id,
            "hostBindingRef": binding.binding_ref,
            "hostLeaseRef": host_lease.lease_id,
            "credentialGeneration": host_lease.credential_generation,
            "agentProfileRef": launch.get("executionProfileRef"),
            "agentProfileVersion": cls._authority_version(
                launch.get("executionProfileRef")
            ),
            "launchPolicyRef": launch.get("launchPolicyRef"),
            "launchPolicyVersion": cls._authority_version(
                launch.get("launchPolicyRef")
            ),
            "policyAuthority": (
                dict(policy_authority)
                if isinstance(policy_authority, Mapping)
                else policy_authority
            ),
            "egressProfileVersion": OMNIGENT_EGRESS_PROFILE.version,
            "securityPolicyRef": OMNIGENT_EGRESS_PROFILE.security_review_ref,
            "securityPolicyVersion": OMNIGENT_EGRESS_PROFILE.version,
            "hostMode": host_mode,
            "runtimeProvenance": "omnigent",
            "cleanupResult": cleanup_result,
            "reconciliationResult": reconciliation_result,
            "launchEvidenceRef": launch_evidence_ref,
            **dict(egress_evidence),
        }
        return payload

    @staticmethod
    async def _publish_host_egress_evidence(
        *,
        artifact_gateway: Any,
        evidence_request: AgentExecutionRequest | OmnigentEgressEvidenceRequestIdentity,
        name: str,
        payload: Mapping[str, Any],
    ) -> str:
        """Publish and immediately re-resolve one protected host evidence row."""

        data = serialize_conformance_evidence(
            payload,
            location=f"omnigent-host-egress:{name}",
        )
        ref: str | None = None
        resolved: bytes | str | Mapping[str, Any] | None = None
        write_bytes = getattr(artifact_gateway, "write_bytes", None)
        if callable(write_bytes):
            ref = await write_bytes(
                request=evidence_request,
                name=name,
                payload=data,
                link_type="security.egress.omnigent",
                content_type="application/json",
            )
            read_bytes = getattr(artifact_gateway, "read_bytes", None)
            if not callable(read_bytes):
                raise OmnigentOAuthHostError(
                    "Omnigent egress evidence cannot be independently resolved",
                    code="OMNIGENT_EGRESS_EVIDENCE_UNRESOLVABLE",
                )
            resolved = await read_bytes(ref)
        else:
            create = getattr(artifact_gateway, "create", None)
            write_complete = getattr(artifact_gateway, "write_complete", None)
            read = getattr(artifact_gateway, "read", None)
            if not (callable(create) and callable(write_complete) and callable(read)):
                raise OmnigentOAuthHostError(
                    "Omnigent egress evidence publisher is unavailable",
                    code="OMNIGENT_EGRESS_EVIDENCE_UNAVAILABLE",
                )
            principal = "system"
            artifact, _upload = await create(
                principal=principal,
                content_type="application/json",
                size_bytes=len(data),
                metadata_json={
                    "artifact_type": "security.egress.omnigent",
                    "name": name,
                    "workflow_id": evidence_request.correlation_id,
                    "idempotency_key": evidence_request.idempotency_key,
                },
            )
            ref = str(artifact.artifact_id)
            await write_complete(
                artifact_id=ref,
                principal=principal,
                payload=data,
                content_type="application/json",
            )
            _artifact, resolved = await read(
                artifact_id=ref,
                principal=principal,
            )
        parse_and_verify_conformance_evidence(
            resolved,
            location=f"omnigent-host-egress-resolved:{name}",
        )
        if not ref:
            raise OmnigentOAuthHostError(
                "Omnigent egress evidence publisher returned no reference",
                code="OMNIGENT_EGRESS_EVIDENCE_UNAVAILABLE",
            )
        return ref

    async def _attest_launched_workload_egress(
        self,
        *,
        attestation: EgressAttestation,
        attachment_identity: str,
        expected_image_ref: str,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> dict[str, object]:
        async def runner(args):
            code, stdout, stderr = await self._run("docker", *args, check=False)
            return code, stdout.encode(), stderr.encode()

        try:
            return await attest_docker_workload_egress(
                runner=runner,
                profile=OMNIGENT_EGRESS_PROFILE,
                attestation=attestation,
                attachment_identity=attachment_identity,
                expected_image_ref=expected_image_ref,
                started_at=started_at,
                finished_at=finished_at,
            )
        except RuntimeError as exc:
            raise OmnigentOAuthHostError(
                "launched host egress attachment attestation failed",
                code="OMNIGENT_LAUNCH_EGRESS_UNATTESTED",
            ) from exc

    async def _resolve_workload_attachment_identity(
        self,
        *,
        binding: OmnigentOAuthHostBinding,
        host_lease: OmnigentHostLease,
        container_name: str | None,
    ) -> str:
        """Resolve the actual host container selected by the production owner."""

        if binding.host_launch_profile_ref:
            identity = container_name or deterministic_host_container_name(
                host_lease.lease_id
            )
        else:
            adapter = self._runtime_adapter(binding)
            code, out, _err = await self._run(
                *self._deployment_compose_command(),
                "--profile",
                str(adapter["compose_profile"]),
                "ps",
                "-q",
                str(adapter["compose_service"]),
                check=False,
            )
            identity = (
                next((line.strip() for line in out.splitlines() if line.strip()), "")
                if code == 0
                else ""
            )
            if not identity:
                raise OmnigentOAuthHostError(
                    "static Omnigent host container could not be resolved for "
                    "attachment attestation",
                    code="OMNIGENT_LAUNCH_EGRESS_UNATTESTED",
                )
        return identity

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
        if not _DIGEST_IMAGE.fullmatch(str(launch.get("hostImageRef") or "")) or str(
            launch.get("hostImageRef")
        ).endswith(_PLACEHOLDER_DIGEST):
            raise OmnigentOAuthHostError(
                "host image must be an immutable sha256 reference",
                code="OMNIGENT_LAUNCH_IMAGE_UNREALIZABLE",
            )
        if not _DIGEST_IMAGE.fullmatch(str(launch.get("serverImageRef") or "")) or str(
            launch.get("serverImageRef")
        ).endswith(_PLACEHOLDER_DIGEST):
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
            "cpuMillis",
            "memoryMiB",
            "processes",
            "timeoutSeconds",
            "temporaryStorageMiB",
        }
        if (
            not isinstance(limits, Mapping)
            or set(limits) != required_limits
            or any(
                not isinstance(limits[key], int) or limits[key] <= 0
                for key in required_limits
            )
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
        resolved_skillset = await load_resolved_skillset(artifact_service, skillset_ref)
        digest = hashlib.sha256(workspace_key.encode("utf-8")).hexdigest()[:24]
        projection_root = (
            self._workspace_root / ".skill-projections" / digest
        ).resolve()
        active_snapshot = (
            projection_root
            / "runtime"
            / "skills_active"
            / resolved_skillset.snapshot_id
        )
        if active_snapshot.exists() or active_snapshot.is_symlink():
            if active_snapshot.is_symlink() or not active_snapshot.is_dir():
                raise OmnigentOAuthHostError(
                    "existing Omnigent Skill projection is not an owned directory",
                    code="OMNIGENT_SKILL_PROJECTION_UNAVAILABLE",
                )
            # Activity retries can reattach to the same live host. Replacing this
            # directory would leave Docker's existing bind mount attached to the
            # removed inode, so the host would observe a missing manifest even
            # though the replacement looks valid from the worker. A resolved
            # snapshot is immutable; verify and reuse its exact backing directory.
            await verify_skill_projection(
                materialization_metadata={"visiblePath": str(active_snapshot)},
                resolved_skillset=resolved_skillset,
            )
            return active_snapshot.resolve()
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

    def _prepare_runtime_scripts(
        self,
        owner_key: str,
        *,
        current_step_execution_id: str,
        runtime_environment: Mapping[str, str] | None = None,
    ) -> Path:
        """Snapshot MoonMind host scripts under the daemon-mapped run root."""

        source = self._scripts_dir.resolve()
        required_scripts = (
            "init-oauth-host.sh",
            "moonmind-tools.sh",
            "start-codex-oauth-host.sh",
            "start-claude-oauth-host.sh",
        )
        if not source.is_dir():
            raise OmnigentOAuthHostError(
                "Omnigent runtime script source is unavailable",
                code="OMNIGENT_RUNTIME_SCRIPTS_UNAVAILABLE",
            )
        if any(path.is_symlink() for path in source.rglob("*")):
            raise OmnigentOAuthHostError(
                "Omnigent runtime script source contains unsupported symlinks",
                code="OMNIGENT_RUNTIME_SCRIPTS_UNAVAILABLE",
            )
        if any(not (source / name).is_file() for name in required_scripts):
            raise OmnigentOAuthHostError(
                "Omnigent runtime script source is incomplete",
                code="OMNIGENT_RUNTIME_SCRIPTS_UNAVAILABLE",
            )

        execution_id = str(current_step_execution_id or "").strip()
        if not _SAFE_STEP_EXECUTION_ID.fullmatch(execution_id):
            raise OmnigentOAuthHostError(
                "Omnigent step execution identity is missing or unsafe",
                code="OMNIGENT_STEP_EXECUTION_ID_INVALID",
            )
        profile_environment = {
            "MOONMIND_STEP_EXECUTION_ID": execution_id,
            "MOONMIND_ACTIVE_SKILLS_DIR": "/opt/moonmind-skills",
        }
        supplied_environment = dict(runtime_environment or {})
        capability_files: dict[str, str] = {}
        for secret_name, (_, filename) in _RUNTIME_CAPABILITY_FILE_ENV.items():
            secret_value = str(supplied_environment.get(secret_name) or "").strip()
            if secret_value:
                capability_files[filename] = secret_value
        supplied_environment = self._host_runtime_environment(supplied_environment)
        for name in _RUNTIME_EXECUTION_PROFILE_ENV_NAMES:
            value = str(supplied_environment.get(name) or "").strip()
            if value:
                profile_environment[name] = value
        for value in profile_environment.values():
            if any(character in value for character in ("\0", "\r", "\n")):
                raise OmnigentOAuthHostError(
                    "Omnigent runtime execution profile contains an unsafe value",
                    code="OMNIGENT_RUNTIME_SCRIPTS_UNAVAILABLE",
                )

        def shell_single_quote(value: str) -> str:
            return "'" + value.replace("'", "'\"'\"'") + "'"

        execution_profile_payload = (
            "# Generated for one MoonMind-owned Omnigent host lease.\n"
            + "".join(
                f"export {name}={shell_single_quote(value)}\n"
                for name, value in profile_environment.items()
            )
        )

        target_parent, target = self._runtime_scripts_target(owner_key)
        if target.is_dir():
            if any(not (target / name).is_file() for name in required_scripts):
                raise OmnigentOAuthHostError(
                    "Omnigent runtime script snapshot is incomplete",
                    code="OMNIGENT_RUNTIME_SCRIPTS_UNAVAILABLE",
                )
            try:
                existing_execution_profile = (
                    target / "moonmind-execution.sh"
                ).read_text(encoding="utf-8")
            except OSError as exc:
                raise OmnigentOAuthHostError(
                    "Omnigent runtime execution profile is unavailable",
                    code="OMNIGENT_RUNTIME_SCRIPTS_UNAVAILABLE",
                ) from exc
            if existing_execution_profile != execution_profile_payload:
                raise OmnigentOAuthHostError(
                    "Omnigent runtime execution profile does not match its owner",
                    code="OMNIGENT_RUNTIME_SCRIPTS_UNAVAILABLE",
                )
            capability_dir = target / "capabilities"
            if capability_dir.is_dir() and any(
                path.is_symlink() for path in capability_dir.iterdir()
            ):
                raise OmnigentOAuthHostError(
                    "Omnigent runtime capability files do not match their owner",
                    code="OMNIGENT_RUNTIME_SCRIPTS_UNAVAILABLE",
                )
            existing_capability_files = (
                {path.name for path in capability_dir.iterdir() if path.is_file()}
                if capability_dir.is_dir()
                else set()
            )
            if existing_capability_files != set(capability_files):
                raise OmnigentOAuthHostError(
                    "Omnigent runtime capability files do not match their owner",
                    code="OMNIGENT_RUNTIME_SCRIPTS_UNAVAILABLE",
                )
            if capability_files:
                try:
                    capability_dir.chmod(0o700)
                    for filename, secret_value in capability_files.items():
                        descriptor, temporary_name = tempfile.mkstemp(
                            prefix=f".{filename}-",
                            dir=capability_dir,
                        )
                        os.close(descriptor)
                        temporary = Path(temporary_name)
                        try:
                            temporary.write_text(secret_value + "\n", encoding="utf-8")
                            temporary.chmod(0o444)
                            os.replace(temporary, capability_dir / filename)
                        finally:
                            if temporary.exists():
                                temporary.unlink()
                except OSError as exc:
                    raise OmnigentOAuthHostError(
                        "Omnigent runtime capability files could not be refreshed",
                        code="OMNIGENT_RUNTIME_SCRIPTS_UNAVAILABLE",
                    ) from exc
                finally:
                    capability_dir.chmod(0o555)
            return target

        target_parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(prefix=f".{target.name}-", dir=target_parent)
        ).resolve()
        try:
            shutil.copytree(source, staging, dirs_exist_ok=True)
            execution_profile = staging / "moonmind-execution.sh"
            execution_profile.write_text(
                execution_profile_payload,
                encoding="utf-8",
            )
            execution_profile.chmod(0o444)
            if capability_files:
                capability_dir = staging / "capabilities"
                capability_dir.mkdir(mode=0o700)
                for filename, secret_value in capability_files.items():
                    capability_file = capability_dir / filename
                    capability_file.write_text(secret_value + "\n", encoding="utf-8")
                    capability_file.chmod(0o444)
                capability_dir.chmod(0o555)
            try:
                os.replace(staging, target)
            except FileExistsError:
                if not target.is_dir():
                    raise
        finally:
            if staging.is_dir():
                shutil.rmtree(staging)

        if (
            any(not (target / name).is_file() for name in required_scripts)
            or not (target / "moonmind-execution.sh").is_file()
        ):
            raise OmnigentOAuthHostError(
                "Omnigent runtime script snapshot is incomplete",
                code="OMNIGENT_RUNTIME_SCRIPTS_UNAVAILABLE",
            )
        return target

    def _runtime_scripts_target(self, owner_key: str) -> tuple[Path, Path]:
        normalized_owner = str(owner_key or "").strip()
        if not normalized_owner:
            raise OmnigentOAuthHostError(
                "Omnigent runtime script owner is unavailable",
                code="OMNIGENT_RUNTIME_SCRIPTS_UNAVAILABLE",
            )
        digest = hashlib.sha256(normalized_owner.encode("utf-8")).hexdigest()[:24]
        target_parent = (self._workspace_root / ".omnigent-runtime-scripts").resolve()
        target = (target_parent / digest).resolve()
        if not target.is_relative_to(target_parent):
            raise OmnigentOAuthHostError(
                "Omnigent runtime script target escaped its owned root",
                code="OMNIGENT_RUNTIME_SCRIPTS_UNAVAILABLE",
            )
        return target_parent, target

    def _remove_runtime_scripts(self, owner_key: str) -> bool:
        """Remove one stopped lease's runtime scripts and capability files."""

        _, target = self._runtime_scripts_target(owner_key)
        if not target.exists():
            return True
        if target.is_symlink() or any(path.is_symlink() for path in target.rglob("*")):
            raise OmnigentOAuthHostError(
                "Omnigent runtime script cleanup found an unsupported symlink",
                code="OMNIGENT_HOST_CLEANUP_INCOMPLETE",
            )
        for path in sorted(target.rglob("*"), reverse=True):
            path.chmod(0o700 if path.is_dir() else 0o600)
        target.chmod(0o700)
        shutil.rmtree(target)
        return not target.exists()

    @staticmethod
    def _host_runtime_environment(
        runtime_environment: Mapping[str, str] | None,
    ) -> dict[str, str]:
        """Replace secret runtime values with lease-owned file selectors."""

        supplied = dict(runtime_environment or {})
        visible = {
            key: value
            for key, value in supplied.items()
            if key not in _SECRET_RUNTIME_ENV_NAMES
        }
        for secret_name, (
            file_env_name,
            filename,
        ) in _RUNTIME_CAPABILITY_FILE_ENV.items():
            if str(supplied.get(secret_name) or "").strip():
                visible[file_env_name] = f"{_RUNTIME_CAPABILITY_MOUNT_ROOT}/{filename}"
        return visible

    def _prepare_daemon_runtime_scripts(
        self,
        owner_key: str,
        *,
        current_step_execution_id: str,
        runtime_environment: Mapping[str, str] | None = None,
        daemon_workspace_root: Path | str | None = None,
    ) -> Path:
        return daemon_visible_workspace_path(
            self._prepare_runtime_scripts(
                owner_key,
                current_step_execution_id=current_step_execution_id,
                runtime_environment=runtime_environment,
            ),
            daemon_root=daemon_workspace_root,
        )

    async def _resolve_daemon_workspace_root(self) -> Path | None:
        """Resolve the named workspace volume in the selected Docker daemon."""
        from moonmind.omnigent.host_services.workspace import (
            resolve_daemon_workspace_root,
        )

        async def runner(argv: list[str]) -> tuple[int, str, str]:
            return await self._run(*argv, check=False)

        try:
            return await resolve_daemon_workspace_root(
                runner=runner,
                workspace_volume=self._workspace_volume,
            )
        except HarnessPlatformError as exc:
            raise OmnigentOAuthHostError(
                str(exc), code="OMNIGENT_DAEMON_WORKSPACE_UNAVAILABLE"
            ) from exc

    async def stop_host(
        self,
        *,
        binding: OmnigentOAuthHostBinding,
        host_lease: OmnigentHostLease,
        effective_launch: Mapping[str, Any] | None = None,
        egress_evidence: Mapping[str, Any] | None = None,
        launch_evidence_ref: str | None = None,
        evidence_request: (
            AgentExecutionRequest | OmnigentEgressEvidenceRequestIdentity | None
        ) = None,
        artifact_gateway: Any | None = None,
    ) -> dict[str, Any]:
        attachment_identity = str(
            (egress_evidence or {}).get("attachmentIdentity") or ""
        ).strip()
        terminal_egress_evidence = dict(egress_evidence or {})
        terminal_validation_error: Exception | None = None
        if egress_evidence is not None and attachment_identity:
            if await self.container_exists(attachment_identity):
                try:
                    observed = await self._attest_launched_workload_egress(
                        attestation=self._attestation_from_workload_evidence(
                            egress_evidence
                        ),
                        attachment_identity=attachment_identity,
                        expected_image_ref=str(
                            (effective_launch or {}).get("hostImageRef")
                            or egress_evidence.get("workloadImageRef")
                            or ""
                        ),
                        started_at=self._evidence_time(
                            egress_evidence.get("validatedAt")
                        ),
                        finished_at=datetime.now(UTC),
                    )
                    terminal_egress_evidence.update(observed)
                    terminal_egress_evidence["terminalValidationResult"] = "passed"
                except Exception as exc:  # cleanup and publish failure evidence
                    terminal_validation_error = exc
                    terminal_egress_evidence.update(
                        {
                            "terminalValidationResult": "failed",
                            "terminalValidationErrorCode": type(exc).__name__,
                        }
                    )
            else:
                terminal_egress_evidence["terminalValidationResult"] = (
                    "attachment_absent_before_cleanup"
                )
        if not binding.host_launch_profile_ref:
            container_name = host_lease.container_name
            if container_name and await self._container_present(container_name):
                await self._assert_container_owned(container_name, host_lease.lease_id)
                await self._run("docker", "rm", "-f", container_name, check=False)
            await self.stop_static_host(binding=binding)
            cleanup_result = "drained_owned_static_host"
            container_present = bool(
                container_name and await self._container_present(container_name)
            )
            cleanup_ok = not container_present and (
                not attachment_identity
                or not await self.container_exists(attachment_identity)
            )
            resource_cleanup = {
                "containerRunning": not cleanup_ok,
                "containerPresent": container_present,
                "mode": "static_drain",
            }
        else:
            container_name = (
                host_lease.container_name
                or deterministic_host_container_name(host_lease.lease_id)
            )
            attachment_identity = attachment_identity or container_name
            if await self.container_exists(container_name):
                await self._assert_container_owned(container_name, host_lease.lease_id)
            await self._run(
                "docker", "stop", "--time", "20", container_name, check=False
            )
            await self._run("docker", "rm", "-f", container_name, check=False)
            await self._run(
                "docker",
                "volume",
                "rm",
                "-f",
                f"{container_name}-state",
                check=False,
            )
            await self._run(
                "docker",
                "volume",
                "rm",
                "-f",
                f"{container_name}-artifacts",
                check=False,
            )
            await self._run(
                "docker",
                "volume",
                "rm",
                "-f",
                f"{container_name}-cache",
                check=False,
            )
            volume_names = (
                f"{container_name}-state",
                f"{container_name}-artifacts",
                f"{container_name}-cache",
            )
            container_present = await self._container_present(container_name)
            remaining_volumes = [
                name for name in volume_names if await self._volume_present(name)
            ]
            cleanup_ok = not container_present and not remaining_volumes
            runtime_files_removed = False
            if cleanup_ok:
                try:
                    runtime_files_removed = self._remove_runtime_scripts(
                        host_lease.lease_id
                    )
                except (OSError, OmnigentOAuthHostError):
                    runtime_files_removed = False
                cleanup_ok = runtime_files_removed
            cleanup_result = "succeeded" if cleanup_ok else "failed"
            resource_cleanup = {
                "containerPresent": container_present,
                "remainingOwnedVolumes": remaining_volumes,
                "runtimeCapabilityFilesRemoved": runtime_files_removed,
                "mode": "on_demand_remove",
            }

        result: dict[str, Any] = {
            "cleanupResult": cleanup_result if cleanup_ok else "failed",
            "reconciliationResult": "succeeded" if cleanup_ok else "required",
            "cleanupValidatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "attachmentIdentity": attachment_identity,
            "launchEvidenceRef": launch_evidence_ref,
            "resourceCleanup": resource_cleanup,
        }
        if (
            effective_launch is not None
            and egress_evidence is not None
            and evidence_request is not None
            and artifact_gateway is not None
        ):
            terminal_payload = self._host_egress_evidence_payload(
                binding=binding,
                host_lease=host_lease,
                launch=effective_launch,
                egress_evidence=terminal_egress_evidence,
                evidence_request=evidence_request,
                state="terminal",
                cleanup_result=str(result["cleanupResult"]),
                reconciliation_result=str(result["reconciliationResult"]),
                launch_evidence_ref=launch_evidence_ref,
            )
            terminal_payload["cleanupValidatedAt"] = result["cleanupValidatedAt"]
            terminal_payload["resourceCleanup"] = resource_cleanup
            terminal_ref = await self._publish_host_egress_evidence(
                artifact_gateway=artifact_gateway,
                evidence_request=evidence_request,
                name=f"omnigent-{host_lease.lease_id}-egress-terminal.json",
                payload=terminal_payload,
            )
            result["evidenceRef"] = terminal_ref
        if not cleanup_ok:
            raise OmnigentOAuthHostError(
                "Omnigent host cleanup could not be reconciled",
                code="OMNIGENT_HOST_CLEANUP_INCOMPLETE",
                egress_evidence_ref=str(result.get("evidenceRef") or "") or None,
                cleanup_evidence=result,
            )
        if terminal_validation_error is not None:
            raise OmnigentOAuthHostError(
                "Omnigent host terminal egress evidence could not be validated",
                code="OMNIGENT_TERMINAL_EGRESS_UNATTESTED",
                egress_evidence_ref=str(result.get("evidenceRef") or "") or None,
                cleanup_evidence=result,
            ) from terminal_validation_error
        return result

    async def stop_static_host(
        self, *, binding: OmnigentOAuthHostBinding | None = None
    ) -> None:
        """Stop the static credential consumer even when no host lease is active."""

        adapter = (
            self._runtime_adapter(binding)
            if binding is not None
            else _RUNTIME_ADAPTERS["codex_cli"]
        )
        result = await self._run(
            *self._deployment_compose_command(),
            "--profile",
            str(adapter["compose_profile"]),
            "stop",
            str(adapter["compose_service"]),
            check=False,
        )
        if result[0] != 0:
            raise OmnigentOAuthHostError(
                "static Omnigent host could not be stopped",
                code="OMNIGENT_HOST_CLEANUP_INCOMPLETE",
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

    async def _container_present(self, container_name: str) -> bool:
        result = await self._run(
            "docker",
            "inspect",
            "--format",
            "{{.Id}}",
            container_name,
            check=False,
        )
        return result[0] == 0 and bool(result[1].strip())

    async def _volume_present(self, volume_name: str) -> bool:
        result = await self._run(
            "docker",
            "volume",
            "inspect",
            "--format",
            "{{.Name}}",
            volume_name,
            check=False,
        )
        return result[0] == 0 and bool(result[1].strip())

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

    async def managed_container_host_lease_ref(self, container_name: str) -> str | None:
        """Return the durable lease identity carried by a managed container."""

        result = await self._run(
            "docker",
            "inspect",
            "--format",
            (
                '{{index .Config.Labels "moonmind.kind"}}|'
                '{{index .Config.Labels "moonmind.host_lease_id"}}'
            ),
            container_name,
            check=False,
        )
        if result[0] != 0:
            return None
        kind, separator, lease_ref = result[1].strip().partition("|")
        if not separator or kind != "omnigent-oauth-host":
            raise OmnigentOAuthHostError(
                "refusing to inspect a container outside Omnigent ownership",
                code="OMNIGENT_HOST_OWNERSHIP_MISMATCH",
            )
        return lease_ref.strip() or None

    async def remove_container(self, container_name: str) -> None:
        # Janitor discovery is label-scoped; never accept an arbitrary name.
        result = await self._run(
            "docker",
            "inspect",
            "--format",
            '{{index .Config.Labels "moonmind.kind"}}',
            container_name,
            check=False,
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
        remaining_container = await self._container_present(container_name)
        remaining_volumes = [
            name
            for name in (
                f"{container_name}-state",
                f"{container_name}-artifacts",
                f"{container_name}-cache",
            )
            if await self._volume_present(name)
        ]
        if remaining_container or remaining_volumes:
            raise OmnigentOAuthHostError(
                "orphaned Omnigent host cleanup could not be reconciled",
                code="OMNIGENT_HOST_CLEANUP_INCOMPLETE",
            )

    async def _launch_on_demand(
        self,
        *,
        binding: OmnigentOAuthHostBinding,
        host_lease: OmnigentHostLease,
        container_name: str,
        workspace_source: Path,
        skill_projection: Path,
        runtime_scripts: Path,
        current_step_execution_id: str,
        github_token: str | None = None,
        container_job_environment: Mapping[str, str] | None = None,
        effective_launch: Mapping[str, Any],
        egress_attestation: EgressAttestation,
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
            '{{index .Config.Labels "moonmind.host_lease_id"}}',
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
            "--network",
            "none",
            "--privileged=false",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "CHOWN",
            "--cap-add",
            "FOWNER",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "--mount",
            f"type=volume,src={mount.auth_volume_ref.volume_ref},dst={adapter['home']}",
            "--mount",
            f"type=volume,src={state_volume},dst=/home/app/.omnigent",
            "--mount",
            f"type=volume,src={artifacts_volume},dst=/artifacts",
            "--mount",
            f"type=volume,src={cache_volume},dst=/home/app/.cache",
            "--mount",
            f"type=bind,src={runtime_scripts},dst=/opt/moonmind,readonly",
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
            "moonmind.capture_required": str(
                effective_launch["capture"]["required"]
            ).lower(),
            "moonmind.capture_retention_days": str(
                effective_launch["capture"]["retentionDays"]
            ),
            "moonmind.cleanup_mode": str(effective_launch["cleanup"]["mode"]),
            "moonmind.control_capabilities": ",".join(
                effective_launch["controlCapabilities"]
            ),
            "moonmind.timeout_seconds": str(
                effective_launch["limits"]["timeoutSeconds"]
            ),
            "moonmind.egress.profile": egress_attestation.profile_ref,
            "moonmind.egress.profile_digest": egress_attestation.profile_digest,
            "moonmind.egress.applied_rule_digest": (
                egress_attestation.applied_rule_digest
            ),
        }
        args = [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "--hostname",
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
            f"type=bind,src={runtime_scripts},dst=/opt/moonmind,readonly",
            "--mount",
            "type=bind,"
            f"src={runtime_scripts / 'moonmind-tools.sh'},"
            "dst=/etc/profile.d/moonmind-tools.sh,readonly",
            "--mount",
            "type=bind,"
            f"src={runtime_scripts / 'moonmind-execution.sh'},"
            "dst=/etc/profile.d/moonmind-execution.sh,readonly",
            "--mount",
            f"type=volume,src={self._tool_bundle_volume},dst=/opt/moonmind-tools,readonly",
            "--mount",
            f"type=bind,src={workspace_source},dst=/workspaces/run",
            "--mount",
            "type=bind,"
            f"src={skill_projection},"
            f"dst={OMNIGENT_RUNTIME_ACTIVE_SKILLS_DIR},readonly",
            "--env",
            f"PATH={self._prepend_tools_path(host_path)}",
            "--env",
            "HOME=/home/app",
            "--env",
            f"{adapter['generation_env']}={host_lease.credential_generation}",
            "--env",
            f"OMNIGENT_SERVER_URL={self._server_url}",
            "--env",
            "MOONMIND_ACTIVE_SKILLS_DIR=" f"{OMNIGENT_RUNTIME_ACTIVE_SKILLS_DIR}",
            "--env",
            f"MOONMIND_STEP_EXECUTION_ID={current_step_execution_id}",
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
        runner_env_passthrough = [
            *_RUNNER_PROXY_ENV_NAMES,
            "MOONMIND_ACTIVE_SKILLS_DIR",
            "MOONMIND_STEP_EXECUTION_ID",
        ]
        token = os.getenv("OMNIGENT_HOST_TOKEN", "")
        child_env = dict(os.environ)
        if token:
            child_env["OMNIGENT_API_TOKEN"] = token
            args.extend(["--env", "OMNIGENT_API_TOKEN"])
        if github_token:
            child_env["GH_TOKEN"] = github_token
            runner_env_passthrough.extend(_RUNNER_GITHUB_ENV_NAMES)
            args.extend(
                [
                    "--env",
                    "GH_TOKEN",
                    "--env",
                    "XDG_CONFIG_HOME=/home/app/.cache/moonmind-xdg",
                    "--env",
                    "GH_PROMPT_DISABLED=1",
                    "--env",
                    "GH_NO_UPDATE_NOTIFIER=1",
                    "--env",
                    "GH_NO_EXTENSION_UPDATE_NOTIFIER=1",
                ]
            )
        for key, value in sorted(dict(container_job_environment or {}).items()):
            if key in _SECRET_RUNTIME_ENV_NAMES:
                raise OmnigentOAuthHostError(
                    "Omnigent host launch received a raw runtime capability",
                    code="OMNIGENT_RUNTIME_CAPABILITY_EXPOSURE",
                )
            runner_env_passthrough.append(key)
            args.extend(["--env", f"{key}={value}"])
        args.extend(
            [
                "--env",
                "OMNIGENT_RUNNER_ENV_PASSTHROUGH=" + ",".join(runner_env_passthrough),
            ]
        )
        for key, value in labels.items():
            args.extend(["--label", f"{key}={value}"])
        # Docker stops parsing run options at the image reference. Keep env's
        # ``-u`` flags after that boundary; before it, Docker interprets each
        # one as a container ``--user`` override.
        args.extend(["--entrypoint", "/usr/bin/env", host_image_ref])
        for key in _FORBIDDEN_ENV:
            args.extend(["-u", key])
        args.append(str(adapter["start_script"]))
        await self._run(*args, env=child_env)

    def _container_job_environment(
        self,
        *,
        binding: OmnigentOAuthHostBinding,
        host_lease: OmnigentHostLease,
        workspace_locator: Mapping[str, Any],
        current_workflow_id: str,
        current_step_execution_id: str,
        timeout_seconds: int,
        required_capabilities: Sequence[str] = (),
        execution_fanout_authorization: Mapping[str, Any] | None = None,
    ) -> dict[str, str]:
        """Materialize only the runtime capabilities declared for this host lease."""

        locator = WORKSPACE_LOCATOR_ADAPTER.validate_python(workspace_locator)
        if not isinstance(locator, SandboxWorkspaceLocator):
            raise OmnigentOAuthHostError(
                "Omnigent container jobs require sandbox workspace authority",
                code=WORKSPACE_LOCATOR_UNSUPPORTED,
            )
        # Omnigent does not own a ManagedRunRecord. The Step Execution identity
        # is the exact durable agent-run authority carried into this Activity and
        # is also the owner of the sandbox workspace for this host lease.
        agent_run_id = str(current_step_execution_id or "").strip()
        if not agent_run_id:
            raise OmnigentOAuthHostError(
                "Omnigent container jobs require an agent run identity",
                code="OMNIGENT_CONTAINER_JOB_AUTHORITY_UNAVAILABLE",
            )
        moonmind_url = str(os.environ.get("MOONMIND_URL") or "http://api:8000").strip()
        if not moonmind_url:
            raise OmnigentOAuthHostError(
                "MoonMind API URL is unavailable for Omnigent container jobs",
                code="OMNIGENT_CONTAINER_JOB_AUTHORITY_UNAVAILABLE",
            )
        runtime_id = binding.credential_mount_ref.auth_volume_ref.runtime_id
        session_scope = host_lease.lease_id
        normalized_capabilities = {
            str(item or "").strip().lower() for item in required_capabilities
        }
        try:
            require_execution_fanout_authorization(
                required_capabilities,
                execution_fanout_authorization,
            )
        except ExecutionFanoutCapabilityError as exc:
            raise OmnigentOAuthHostError(str(exc), code="authorization_denied") from exc
        environment = {
            "MOONMIND_URL": moonmind_url,
            "MOONMIND_AGENT_RUN_ID": agent_run_id,
            "MOONMIND_TASK_WORKFLOW_ID": current_workflow_id,
            "MOONMIND_STEP_ID": current_step_execution_id,
            "MOONMIND_RUNTIME_ID": runtime_id,
        }
        if "docker" in normalized_capabilities:
            environment.update(
                {
                    "MOONMIND_CONTAINER_JOBS_MCP_URL": (
                        moonmind_url.rstrip("/") + "/mcp/container"
                    ),
                    "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN": (
                        mint_container_job_session_capability(
                            secret=str(settings.security.JWT_SECRET_KEY or ""),
                            owner=OwnerIdentity(
                                principalId=agent_run_id,
                                principalType="service",
                            ),
                            agent_run_id=agent_run_id,
                            workflow_id=current_workflow_id,
                            step_id=current_step_execution_id,
                            session_id=session_scope,
                            runtime_id=runtime_id,
                            source_kind="omnigent",
                            workspace_kind="sandbox",
                            workspace_id=locator.workspace_id,
                            workspace_relative_path=locator.relative_path,
                            lifetime_seconds=timeout_seconds,
                        )
                    ),
                    "MOONMIND_CONTAINER_JOBS_SOURCE_KIND": "omnigent",
                    "MOONMIND_CONTAINER_JOBS_SESSION_ID": session_scope,
                    "MOONMIND_CONTAINER_JOBS_WORKSPACE_KIND": "sandbox",
                    "MOONMIND_CONTAINER_JOBS_WORKSPACE_ID": locator.workspace_id,
                    "MOONMIND_CONTAINER_JOBS_WORKSPACE_RELATIVE_PATH": (
                        locator.relative_path
                    ),
                }
            )
        if EXECUTION_FANOUT_REQUIRED_CAPABILITY in normalized_capabilities:
            environment["MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN"] = (
                mint_execution_fanout_capability(
                    secret=str(settings.security.JWT_SECRET_KEY or ""),
                    parent_workflow_id=current_workflow_id,
                    agent_run_id=agent_run_id,
                    step_id=current_step_execution_id,
                    session_id=session_scope,
                    runtime_id=runtime_id,
                    source_kind="omnigent",
                    lifetime_seconds=timeout_seconds,
                )
            )
        return environment

    async def _assert_container_owned(self, container_name: str, lease_id: str) -> None:
        result = await self._run(
            "docker",
            "inspect",
            "--format",
            '{{index .Config.Labels "moonmind.host_lease_id"}}',
            container_name,
            check=False,
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
        repository_provider: str = "",
        repository_connection_ref: str = "",
        repository_client_evidence: Mapping[str, str] | None = None,
        starting_branch: str | None = None,
        target_branch: str | None = None,
        checkout_commit: str | None = None,
        restore_input_refs: tuple[str, ...] = (),
        workspace_checkpoint_restore_ref: str | None = None,
        attachment_refs: tuple[str, ...] = (),
        github_token: str | None = None,
        artifact_gateway: Any | None = None,
        omnigent_isolation_verified: bool = False,
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
        if str(repository_provider or "").strip().lower() == "lore":
            if self._lore_repository_adapter is None:
                raise OmnigentOAuthHostError(
                    "Lore repository work requires the configured provider adapter",
                    code=WORKSPACE_LOCATOR_UNSUPPORTED,
                )
            if not omnigent_isolation_verified:
                raise LoreWorkspaceError(
                    LORE_UNSUPPORTED_RUNTIME_LANE,
                    "Lore workspace isolation is not verified for this runtime lane",
                )
            repository = str(repository_source or "").strip()
            branch = str(starting_branch or target_branch or "").strip()
            revision = str(checkout_commit or "").strip()
            if workspace.exists():
                prepared = await asyncio.to_thread(
                    self._lore_repository_adapter.load_prepared_workspace,
                    locator=locator,
                    authority_path=workspace,
                )
                if repository and (
                    prepared.repository,
                    prepared.branch,
                    prepared.revision_signature,
                ) != (repository, branch, revision):
                    raise OmnigentOAuthHostError(
                        "existing Lore workspace does not match the authored target",
                        code=WORKSPACE_AUTHORITY_MISMATCH,
                    )
            else:
                if not repository or not branch or not revision:
                    raise OmnigentOAuthHostError(
                        "fresh Lore workspaces require repository, branch, and revision",
                        code=WORKSPACE_LOCATOR_UNSUPPORTED,
                    )
                prepared = await asyncio.to_thread(
                    self._lore_repository_adapter.prepare_workspace,
                    repository=repository,
                    branch=branch,
                    revision_signature=revision,
                    locator=locator,
                    authority_path=workspace,
                    connection_ref=repository_connection_ref,
                    client_evidence=dict(repository_client_evidence or {}),
                )
            binding = self._lore_repository_adapter.bind_workspace(
                prepared,
                runtime_lane="omnigent",
                omnigent_mount_path="/workspaces/run",
                omnigent_isolation_verified=omnigent_isolation_verified,
            )
            if binding.authority_locator != locator:
                raise OmnigentOAuthHostError(
                    "Lore workspace binding changed sandbox authority",
                    code=WORKSPACE_AUTHORITY_MISMATCH,
                )
            self._last_workspace_evidence = self._workspace_resolution_evidence(
                locator=locator,
                expected_id=expected_id,
                materialization={
                    "action": "reused_lore_authority",
                    "revisionSignature": prepared.revision_signature,
                },
            )
            return workspace
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
        self._last_workspace_denial_evidence = {}
        materialization: dict[str, Any] = {"action": "reused_pre_materialized"}
        if not already_materialized:
            # Every mutation from here to the durable completion marker is owned by
            # this run. If any step fails, bounded reconciliation evidence records
            # whether owned partial state was created and that a retry must rebuild
            # it, because the absent completion marker forces a rebuild rather than
            # reuse of a partial directory.
            try:
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
                if workspace_checkpoint_restore_ref:
                    await self._apply_workspace_checkpoint_restore(
                        workspace,
                        artifact_ref=workspace_checkpoint_restore_ref,
                        artifact_gateway=artifact_gateway,
                    )
                if restore_evidence:
                    materialization["restoreInputs"] = restore_evidence
                attachment_evidence = await self._materialize_attachments(
                    workspace,
                    attachment_refs=attachment_refs,
                    artifact_gateway=artifact_gateway,
                    workflow_id=current_workflow_id,
                )
                if attachment_evidence:
                    materialization["attachments"] = attachment_evidence
                record_store.mark_materialized(locator.workspace_id)
            except (Exception, asyncio.CancelledError) as exc:
                denial = self._workspace_denial_evidence(
                    locator=locator,
                    expected_id=expected_id,
                    error=exc,
                    owned_partial_state_created=workspace.exists(),
                )
                self._last_workspace_denial_evidence = denial
                # Attach the bounded reconciliation evidence to the failure so the
                # owning caller can persist it without re-deriving authority state.
                exc.workspace_denial_evidence = denial  # type: ignore[attr-defined]
                raise
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

    def _align_workspace_ownership(
        self,
        workspace: Path,
        *,
        runtime_uid: int,
        runtime_gid: int,
    ) -> None:
        """Make the isolated host UID the owner of its exact run workspace."""

        resolved_workspace = workspace.resolve(strict=True)
        if not resolved_workspace.is_relative_to(self._workspace_root):
            raise OmnigentOAuthHostError(
                "workspace ownership target escapes the configured workspace root",
                code="OMNIGENT_WORKSPACE_OWNERSHIP_FAILED",
            )
        try:
            os.chown(
                resolved_workspace,
                runtime_uid,
                runtime_gid,
                follow_symlinks=False,
            )
            for current_root, directory_names, file_names in os.walk(
                resolved_workspace,
                followlinks=False,
            ):
                current = Path(current_root)
                for name in (*directory_names, *file_names):
                    os.chown(
                        current / name,
                        runtime_uid,
                        runtime_gid,
                        follow_symlinks=False,
                    )
        except OSError as exc:
            raise OmnigentOAuthHostError(
                "unable to align the run workspace with the isolated host identity",
                code="OMNIGENT_WORKSPACE_OWNERSHIP_FAILED",
            ) from exc

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
                "git",
                "-C",
                str(workspace),
                "checkout",
                "--detach",
                commit,
                env=git_env,
                check=False,
            )
            if code != 0:
                raise OmnigentOAuthHostError(
                    f"workspace commit checkout failed: {err.strip()[:200]}",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                )
            checked_out = commit
        elif start and source_kind == "local":
            code, _out, _err = await self._run(
                "git",
                "-C",
                str(workspace),
                "checkout",
                start,
                env=git_env,
                check=False,
            )
            if code != 0:
                await self._run(
                    "git",
                    "-C",
                    str(workspace),
                    "checkout",
                    "-B",
                    start,
                    f"origin/{start}",
                    env=git_env,
                    check=False,
                )

        output_branch = None
        if target and target != checked_out:
            # Honor the authored output branch without discarding the checked-out
            # working tree, matching normal MoonMind repository semantics.
            code, _out, err = await self._run(
                "git",
                "-C",
                str(workspace),
                "checkout",
                "-B",
                target,
                env=git_env,
                check=False,
            )
            if code != 0:
                raise OmnigentOAuthHostError(
                    f"workspace output branch selection failed: {err.strip()[:200]}",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                )
            output_branch = target

        # ``checkedOut`` records the authored selection (a branch name for the
        # normal path), which is a movable ref. Resolve the immutable revision that
        # was actually checked out so durable authority evidence proves which source
        # state executed and cannot drift after the branch advances.
        resolved_commit: str | None = None
        code, out, _err = await self._run(
            "git",
            "-C",
            str(workspace),
            "rev-parse",
            "HEAD",
            env=git_env,
            check=False,
        )
        if code == 0:
            resolved_commit = str(out or "").strip() or None

        return {
            "action": "materialized",
            "sourceKind": source_kind,
            "startingBranch": start,
            "checkedOut": checked_out,
            "resolvedCommit": resolved_commit,
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

        return await self._materialize_input_bundle(
            workspace,
            refs=restore_input_refs,
            artifact_gateway=artifact_gateway,
            subdir="restore",
            principal=_RESTORE_ARTIFACT_PRINCIPAL,
            noun="restore inputs",
        )

    async def _apply_workspace_checkpoint_restore(
        self,
        workspace: Path,
        *,
        artifact_ref: str,
        artifact_gateway: Any | None,
    ) -> None:
        """Apply a typed worktree archive at the owning workspace boundary."""

        if not artifact_ref.startswith("artifact://"):
            raise OmnigentOAuthHostError(
                "workspace checkpoint restore requires a durable artifact ref",
                code=WORKSPACE_LOCATOR_UNSUPPORTED,
            )
        service = self._as_artifact_service(artifact_gateway)
        if service is None:
            raise OmnigentOAuthHostError(
                "workspace checkpoint restore requires an artifact service",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        artifact_id = artifact_ref[len("artifact://") :]
        _metadata, payload = await service.read(
            artifact_id=artifact_id,
            principal=_RESTORE_ARTIFACT_PRINCIPAL,
            allow_restricted_raw=True,
        )
        if len(payload) > _MAX_RESTORE_INPUT_BYTES:
            raise OmnigentOAuthHostError(
                "workspace checkpoint exceeds the authorized workspace bound",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        try:
            with tarfile.open(
                fileobj=__import__("io").BytesIO(payload), mode="r:gz"
            ) as archive:
                for member in archive.getmembers():
                    target = (workspace / member.name).resolve()
                    if not target.is_relative_to(workspace.resolve()) or member.isdev():
                        raise OmnigentOAuthHostError(
                            "workspace checkpoint contains an unsafe archive member",
                            code=WORKSPACE_AUTHORITY_MISMATCH,
                        )
                    if member.issym() or member.islnk():
                        link_target = (target.parent / member.linkname).resolve()
                        if not link_target.is_relative_to(workspace.resolve()):
                            raise OmnigentOAuthHostError(
                                "workspace checkpoint symlink escapes workspace",
                                code=WORKSPACE_AUTHORITY_MISMATCH,
                            )
                archive.extractall(workspace, filter="data")
        except (tarfile.TarError, OSError) as exc:
            raise OmnigentOAuthHostError(
                "workspace checkpoint archive could not be applied",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            ) from exc

    async def _materialize_attachments(
        self,
        workspace: Path,
        *,
        attachment_refs: tuple[str, ...],
        artifact_gateway: Any | None,
        workflow_id: str,
    ) -> list[dict[str, Any]]:
        """Materialize declared input attachments into the workspace.

        Attachments are a distinct declared-input authority from checkpoint restore
        material, so they route through the same canonical owning-worker boundary
        but read under their own service principal and land under a bounded
        ``.moonmind/attachments`` area. Like restore inputs they must be durable
        ``artifact://`` refs; a ref that looks like a local path is rejected so an
        attachment can never be conflated with a filesystem path.
        """

        self._exclude_materialized_attachments(workspace)
        return await self._materialize_input_bundle(
            workspace,
            refs=attachment_refs,
            artifact_gateway=artifact_gateway,
            subdir="attachments",
            principal=_ATTACHMENT_ARTIFACT_PRINCIPAL,
            noun="attachments",
            required_workflow_id=workflow_id,
        )

    @staticmethod
    def _exclude_materialized_attachments(workspace: Path) -> None:
        """Keep restricted runtime inputs out of repository publication."""
        info_dir = workspace / ".git" / "info"
        if not info_dir.is_dir():
            return
        exclude_path = info_dir / "exclude"
        rule = "/.moonmind/attachments/"
        existing = (
            exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
        )
        if rule not in existing.splitlines():
            with exclude_path.open("a", encoding="utf-8") as stream:
                if existing and not existing.endswith("\n"):
                    stream.write("\n")
                stream.write(f"{rule}\n")

    async def _materialize_input_bundle(
        self,
        workspace: Path,
        *,
        refs: tuple[str, ...],
        artifact_gateway: Any | None,
        subdir: str,
        principal: str,
        noun: str,
        required_workflow_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Materialize a bounded bundle of durable input artifact refs.

        Shared by restore-input and attachment materialization so every declared
        input class is dereferenced through one canonical path: artifact-ref-only
        (never a local path), per-ref and cumulative byte bounds, containment under
        the already-validated workspace, and a dedicated read principal per class.
        """

        cleaned = [str(ref).strip() for ref in refs if str(ref).strip()]
        if not cleaned:
            return []
        if len(cleaned) > _MAX_RESTORE_INPUT_REFS:
            raise OmnigentOAuthHostError(
                f"too many {noun} for the authorized workspace",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        artifact_service = self._as_artifact_service(artifact_gateway)
        if artifact_service is None:
            raise OmnigentOAuthHostError(
                f"{noun} require an artifact service to resolve refs",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        bundle_root = (workspace / ".moonmind" / subdir).resolve()
        if not bundle_root.is_relative_to(workspace.resolve()):  # pragma: no cover
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                f"{noun} materialization escaped the authorized workspace",
            )
        bundle_root.mkdir(parents=True, exist_ok=True)
        evidence: list[dict[str, Any]] = []
        total_bytes = 0
        for ref in cleaned:
            if not ref.startswith("artifact://"):
                raise OmnigentOAuthHostError(
                    f"{noun} must be durable artifact refs, not local paths",
                    code=WORKSPACE_LOCATOR_UNSUPPORTED,
                )
            # The durable artifact contract addresses artifacts by id; the
            # canonical ``artifact://`` scheme must be stripped before lookup.
            artifact_id = ref[len("artifact://") :]
            # Enforce the per-ref bound and the single cumulative budget together,
            # so many individually-legal refs cannot aggregate past the advertised
            # hostile-input bound.
            per_ref_budget = min(
                _MAX_RESTORE_INPUT_BYTES, _MAX_RESTORE_TOTAL_BYTES - total_bytes
            )
            if per_ref_budget <= 0:
                raise OmnigentOAuthHostError(
                    f"{noun} exceed the cumulative authorized workspace bound",
                    code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                )
            await self._reject_oversized_restore_metadata(
                artifact_service,
                artifact_id,
                per_ref_budget,
                principal,
                required_workflow_id=required_workflow_id,
            )
            digest = hashlib.sha256(ref.encode("utf-8")).hexdigest()[:24]
            target = bundle_root / digest
            if target.is_symlink():
                raise WorkspaceLocatorResolutionError(
                    WORKSPACE_AUTHORITY_MISMATCH,
                    f"{noun} target must not be a symlink",
                )
            written = await self._write_restore_payload(
                artifact_service,
                artifact_id=artifact_id,
                target=target,
                budget_bytes=per_ref_budget,
                principal=principal,
            )
            total_bytes += written
            evidence.append({"ref": ref, "bytes": written})
        return evidence

    @staticmethod
    async def _reject_oversized_restore_metadata(
        artifact_service: Any,
        artifact_id: str,
        budget_bytes: int,
        principal: str = _RESTORE_ARTIFACT_PRINCIPAL,
        required_workflow_id: str | None = None,
    ) -> None:
        """Reject an oversized restore artifact from metadata before reading bytes.

        When the service can report artifact size cheaply, an oversized payload is
        rejected before any bytes are allocated or written.
        """
        get_metadata = getattr(artifact_service, "get_metadata", None)
        if get_metadata is None:
            if required_workflow_id is not None:
                raise OmnigentOAuthHostError(
                    "attachment authorization requires linked artifact metadata",
                    code=WORKSPACE_AUTHORITY_MISMATCH,
                )
            return
        try:
            metadata = await get_metadata(
                artifact_id=artifact_id,
                principal=principal,
            )
        except TypeError:
            if required_workflow_id is not None:
                raise OmnigentOAuthHostError(
                    "attachment authorization metadata is incompatible",
                    code=WORKSPACE_AUTHORITY_MISMATCH,
                )
            return
        artifact = metadata[0] if isinstance(metadata, tuple) else metadata
        if required_workflow_id is not None:
            links = (
                metadata[1] if isinstance(metadata, tuple) and len(metadata) > 1 else ()
            )
            workflow_family_prefix = f"{required_workflow_id}:"
            if not any(
                (
                    str(getattr(link, "workflow_id", "")) == required_workflow_id
                    or str(getattr(link, "workflow_id", "")).startswith(
                        workflow_family_prefix
                    )
                )
                for link in links
            ):
                raise OmnigentOAuthHostError(
                    "attachment artifact is not linked to the current workflow family",
                    code=WORKSPACE_AUTHORITY_MISMATCH,
                )
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
        principal: str = _RESTORE_ARTIFACT_PRINCIPAL,
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
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                _artifact, chunks = await read_chunks(
                    artifact_id=artifact_id,
                    principal=principal,
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
            principal=principal,
            allow_restricted_raw=True,
        )
        if len(payload) > budget_bytes:
            raise OmnigentOAuthHostError(
                "restore input exceeds the authorized workspace bound",
                code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
            )
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        return len(payload)

    @staticmethod
    def _as_artifact_service(artifact_gateway: Any | None) -> Any | None:
        if artifact_gateway is None:
            return None
        # A durable artifact service exposes the by-id ``read``/``read_chunks``
        # contract directly; only a bare byte gateway needs the ref adapter.
        if hasattr(artifact_gateway, "read") or hasattr(
            artifact_gateway, "read_chunks"
        ):
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

        try:
            return normalize_repository_source(repository_source)
        except RepositorySourceError as exc:
            raise OmnigentOAuthHostError(
                str(exc), code="OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED"
            ) from exc

    def _authorize_local_repository_source(self, source: str) -> None:
        """Reject a local repository source outside the authorized source root.

        Local sources are attacker-influenced (they come from the workflow-authored
        ``workspaceSpec``/parameters). Without containment they could clone any Git
        repository the trusted worker can read, including another run under the
        workspace authority, crossing workspace isolation boundaries.
        """
        raw_path = source[len("file://") :] if source.startswith("file://") else source
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
                    normalized = normalized[len(prefix) :]
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

    @staticmethod
    def _workspace_denial_evidence(
        *,
        locator: SandboxWorkspaceLocator,
        expected_id: str,
        error: BaseException,
        owned_partial_state_created: bool,
    ) -> dict[str, Any]:
        """Assemble bounded, credential-free workspace-denial evidence.

        Records the failed authority class, a stable reason code, retryability, the
        locator refs (never a raw path), whether owned partial state was created,
        and the reconciliation requirement. Because the durable completion marker is
        only written after a full materialization, a partially built workspace is
        rebuilt on the next retry rather than reused.
        """

        code = str(getattr(error, "code", None) or type(error).__name__)[:96]
        permanent_codes = {
            WORKSPACE_AUTHORITY_MISMATCH,
            WORKSPACE_LOCATOR_UNSUPPORTED,
            "OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
        }
        return {
            "failedAuthorityClass": "workspace_materialization",
            "locatorKind": locator.kind,
            "workspaceId": locator.workspace_id,
            "relativePath": locator.relative_path,
            "identityVerified": locator.workspace_id == expected_id,
            "reasonCode": code,
            "retryable": code not in permanent_codes,
            "ownedPartialStateCreated": bool(owned_partial_state_created),
            "reconciliation": (
                "rebuild_owned_workspace_on_retry"
                if owned_partial_state_created
                else "none"
            ),
        }

    async def _initialize_required_tools(self) -> None:
        expected_version = os.getenv("OMNIGENT_GH_VERSION", "2.76.2")
        return_code, stdout, _stderr = await self._run(
            "docker",
            "run",
            "--rm",
            "--volume",
            f"{self._tool_bundle_volume}:/opt/moonmind-tools:ro",
            "--entrypoint",
            "/opt/moonmind-tools/bin/gh",
            self._image,
            "--version",
            check=False,
        )
        first_line = stdout.splitlines()[0] if stdout.splitlines() else ""
        if return_code != 0 or f" {expected_version} " not in f" {first_line} ":
            raise MountedToolPreflightError(
                "The deployment-owned Omnigent tool bundle is not ready",
                code="tool_bundle_unavailable",
                evidence={
                    "tool": "gh",
                    "phase": "deployment_initialization",
                    "bundleVolume": self._tool_bundle_volume,
                    "expectedVersion": expected_version,
                },
            )

    async def _compose_static_check(
        self,
        *,
        binding: OmnigentOAuthHostBinding | None = None,
        workspace_source: Path,
        skill_projection: Path | None = None,
        effective_launch: Mapping[str, Any] | None = None,
        egress_attestation: EgressAttestation | None = None,
    ) -> dict[str, str]:
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
                    "OMNIGENT_EFFECTIVE_LAUNCH_REF": str(
                        effective_launch["snapshotRef"]
                    ),
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
        if egress_attestation is not None:
            child_env.update(
                {
                    "OMNIGENT_EGRESS_PROFILE_REF": egress_attestation.profile_ref,
                    "OMNIGENT_EGRESS_PROFILE_DIGEST": (
                        egress_attestation.profile_digest
                    ),
                    "OMNIGENT_EGRESS_APPLIED_RULE_DIGEST": (
                        egress_attestation.applied_rule_digest
                    ),
                }
            )
        adapter = (
            self._runtime_adapter(binding)
            if binding is not None
            else _RUNTIME_ADAPTERS["codex_cli"]
        )
        await self._run(
            *self._deployment_compose_command(),
            "--profile",
            str(adapter["compose_profile"]),
            "up",
            "-d",
            str(adapter["compose_service"]),
            env=child_env,
        )
        return child_env

    async def _compose_static_exec_check(
        self,
        *,
        binding: OmnigentOAuthHostBinding | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        adapter = (
            self._runtime_adapter(binding)
            if binding is not None
            else _RUNTIME_ADAPTERS["codex_cli"]
        )
        await self._run(
            *self._deployment_compose_command(),
            "--profile",
            str(adapter["compose_profile"]),
            "exec",
            "-T",
            str(adapter["compose_service"]),
            "/opt/moonmind/check-runner-projections.sh",
            env=dict(env or os.environ),
        )

    async def _exec_check(self, container_name: str) -> None:
        # ``docker run -d`` returns before the container is guaranteed to
        # accept ``docker exec``. Keep this readiness check local to the live
        # container so a short startup race does not tear down an otherwise
        # valid host and consume the activity-level retry budget.
        for attempt in range(_HOST_EXEC_PREFLIGHT_ATTEMPTS):
            projections = await self._run(
                "docker",
                "exec",
                container_name,
                "/opt/moonmind/check-runner-projections.sh",
                check=False,
            )
            if projections[0] == 0:
                workspace = await self._run(
                    "docker",
                    "exec",
                    container_name,
                    "git",
                    "-C",
                    "/workspaces/run",
                    "status",
                    "--porcelain",
                    check=False,
                )
                if workspace[0] == 0:
                    return
            if attempt + 1 < _HOST_EXEC_PREFLIGHT_ATTEMPTS:
                await asyncio.sleep(_HOST_EXEC_PREFLIGHT_INTERVAL_SECONDS)
        raise OmnigentOAuthHostError(
            "OAuth host runtime command failed",
            code=HostPreflightFailure.LOGIN_STATUS_FAILED.value,
        )

    async def _resolve_exact_host(
        self,
        *,
        binding: OmnigentOAuthHostBinding,
        host_lease: OmnigentHostLease,
    ) -> dict[str, Any]:
        expected_id = binding.static_host_id or host_lease.omnigent_host_id
        expected_name = host_lease.container_name or deterministic_host_container_name(
            host_lease.lease_id
        )
        adapter = self._runtime_adapter(binding)
        for attempt in range(_HOST_REGISTRATION_ATTEMPTS):
            hosts = await self._client.list_hosts()
            if expected_id:
                matches = [
                    host
                    for host in hosts
                    if str(host.get("id") or host.get("hostId") or host.get("host_id"))
                    == expected_id
                ]
            else:
                matches = [
                    host
                    for host in hosts
                    if str(host.get("name") or host.get("hostname") or "")
                    in {expected_name, str(adapter["compose_service"])}
                ]
            online = [
                host
                for host in matches
                if str(host.get("status", "online")) == "online"
            ]
            if len(online) == 1:
                return dict(online[0])
            if len(online) > 1:
                break
            if attempt + 1 < _HOST_REGISTRATION_ATTEMPTS:
                await asyncio.sleep(_HOST_REGISTRATION_INTERVAL_SECONDS)
        raise OmnigentOAuthHostError(
            "expected exactly one compatible online host after bounded registration wait",
            code=HostPreflightFailure.HOST_NOT_REGISTERED.value,
        )

    @staticmethod
    def _ready_host_harnesses(host: Mapping[str, Any]) -> set[str]:
        capabilities = (
            host.get("harnesses")
            or host.get("capabilities")
            or host.get("configured_harnesses")
            or []
        )
        if not isinstance(capabilities, Mapping):
            return {str(value) for value in capabilities}
        ready_values = {"true", "ready", "available", "authenticated"}
        return {
            str(name)
            for name, readiness in capabilities.items()
            if readiness is True or str(readiness).strip().lower() in ready_values
        }

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
