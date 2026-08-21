"""Concrete deployment adapters for the generic Omnigent realizer.

The application coordinator consumes only the protocols in ``host_runtime``.
This outer module owns the trusted filesystem, secret, database, Omnigent HTTP,
restricted-egress, and container-command boundaries needed to realize those
protocols in the normal product path.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from api_service.db.models import (
    OmnigentCredentialRuntimeRecord,
    OmnigentExecutionPlanRecord,
    OmnigentHostBindingRecordV2,
    OmnigentHostLeaseRecordV2,
    OmnigentRuntimeBindingRecord,
)
from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.host_classes import HostClass, LaunchPolicy
from moonmind.omnigent.harness_platform.materializers import (
    cleanup_opencode_auth,
    materialize_opencode_auth_json,
)
from moonmind.omnigent.settings import resolved_api_token, resolved_server_url
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.workspace_locator_models import SandboxWorkspaceLocator
from moonmind.security.egress import (
    OMNIGENT_EGRESS_PROFILE,
    attest_docker_egress,
    attest_docker_workload_egress,
    omnigent_proxy_env,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
from moonmind.workflows.temporal.runtime.command_runner import run_runtime_command
from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
    resolve_managed_api_key_reference,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecordStore,
    daemon_visible_workspace_path,
    resolve_sandbox_workspace_locator,
)
from moonmind.workloads.docker_launcher import structured_container_security_args


_COMMAND_TIMEOUT_SECONDS = 600.0
_COMMAND_OUTPUT_LIMIT_BYTES = 16 * 1024
_REGISTRATION_ATTEMPTS = 30
_REGISTRATION_INTERVAL_SECONDS = 2.0
_RUNTIME_DIR_NAME = "omnigent_generic_runtime"
_SCRIPTS = (
    "check-opencode-host.sh",
    "check-runner-projections.sh",
    "clear-stale-host-daemons.sh",
    "start-host-with-projections.sh",
    "start-opencode-host.sh",
)


def _digest(*values: object) -> str:
    encoded = json.dumps(values, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _artifact_ref(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        dict(payload), sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return "artifact:sha256:" + hashlib.sha256(canonical).hexdigest()


def _runtime_root() -> Path:
    workspace_root = Path(
        os.getenv("WORKFLOW_WORKSPACE_ROOT", "/work/agent_jobs")
    ).expanduser().resolve()
    configured = os.getenv("OMNIGENT_GENERIC_RUNTIME_ROOT", "").strip()
    root = (
        Path(configured).expanduser().resolve()
        if configured
        else (workspace_root / _RUNTIME_DIR_NAME).resolve()
    )
    if not root.is_relative_to(workspace_root):
        raise HarnessPlatformError(
            "generic Omnigent runtime root escapes WORKFLOW_WORKSPACE_ROOT",
            code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY,
        )
    return root


async def _run_docker(
    argv: Sequence[str], *, env: Mapping[str, str] | None = None
) -> tuple[int, bytes, bytes]:
    """Execute Docker only at the trusted deployment/worker boundary."""

    return await run_runtime_command(
        ("docker", *argv),
        env=env,
        timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
        output_limit_bytes=_COMMAND_OUTPUT_LIMIT_BYTES,
    )


def _safe_segment(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in "_.-" else "-"
        for character in value.strip()
    ).strip(".-")
    if not normalized:
        raise HarnessPlatformError(
            "generic host authority contains an empty unsafe identity",
            code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
        )
    return normalized[:120]


def _docker_object_missing(stderr: bytes) -> bool:
    diagnostic = stderr.decode(errors="replace").lower()
    return "no such object" in diagnostic or "not found" in diagnostic


@dataclass(slots=True)
class _DeploymentState:
    request: AgentExecutionRequest | None = None
    plan: OmnigentExecutionPlanEnvelope | None = None
    workspace_path: Path | None = None
    skill_path: Path | None = None
    skill_delivery_ref: str | None = None
    skill_attestation_ref: str | None = None
    host_binding_ref: str | None = None
    host_lease_ref: str | None = None
    host_lease_generation: int | None = None
    container_name: str | None = None
    omnigent_host_id: str | None = None
    egress_attestation: Any | None = None
    launched_at: datetime | None = None
    evidence: dict[str, str] = field(default_factory=dict)


class TrustedCredentialMaterializer:
    """Resolve leased SecretRefs and materialize run-owned credential files."""

    def __init__(self, *, session_factory: Any, runtime_root: Path | None = None) -> None:
        self._session_factory = session_factory
        self._root = (runtime_root or _runtime_root()).resolve() / "credentials"

    def _credential_root(self, credential_runtime_ref: str) -> Path:
        digest = credential_runtime_ref.rsplit(":", 1)[-1]
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise HarnessPlatformError(
                "credential runtime ref is not content-addressed",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
            )
        root = (self._root / digest).resolve()
        if not root.is_relative_to(self._root.resolve()):
            raise HarnessPlatformError(
                "credential runtime path escapes deployment authority",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
            )
        return root

    @staticmethod
    def _secret_ref(profile: Any, role: str) -> Any:
        refs = profile.secret_refs if isinstance(profile.secret_refs, Mapping) else {}
        value = refs.get(role)
        if value is None:
            raise HarnessPlatformError(
                f"Provider Profile {profile.profile_id} has no {role} SecretRef",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE,
            )
        return value

    async def materialize(
        self,
        *,
        profile: Any,
        binding: dict[str, Any],
        provider_lease_ref: str,
        credential_generation: int,
        execution_plan_ref: str,
        command_authority: dict[str, Any],
    ) -> dict[str, Any]:
        materializer_ref = str(binding.get("materializerRef") or "").strip()
        identity_digest = _digest(
            execution_plan_ref,
            profile.profile_id,
            provider_lease_ref,
            credential_generation,
            materializer_ref,
        )
        credential_runtime_ref = f"credential-runtime:sha256:{identity_digest}"
        root = self._credential_root(credential_runtime_ref)

        if materializer_ref != "opencode-auth-json@1":
            raise HarnessPlatformError(
                f"trusted materializer implementation {materializer_ref} is unavailable",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE,
            )
        api_key = await resolve_managed_api_key_reference(
            self._secret_ref(profile, "api_key"),
            field_name=f"Provider Profile {profile.profile_id} api_key SecretRef",
        )
        handle = await asyncio.to_thread(
            materialize_opencode_auth_json,
            api_key=api_key,
            provider_profile_ref=profile.profile_id,
            provider_lease_ref=provider_lease_ref,
            credential_generation=credential_generation,
            expected_generation=credential_generation,
            host_root=root,
        )
        handle["credentialRuntimeRef"] = credential_runtime_ref
        handle["cleanupRef"] = f"credential-cleanup:sha256:{identity_digest}"
        handle["attestationRef"] = _artifact_ref(
            {
                "credentialRuntimeRef": credential_runtime_ref,
                "providerProfileRef": profile.profile_id,
                "providerLeaseRef": provider_lease_ref,
                "credentialGeneration": credential_generation,
                "materializerRef": materializer_ref,
                "commandId": command_authority.get("commandId"),
            }
        )

        async with self._session_factory() as session:
            existing = await session.get(
                OmnigentCredentialRuntimeRecord, credential_runtime_ref
            )
            values = {
                "provider_profile_ref": profile.profile_id,
                "provider_lease_ref": provider_lease_ref,
                "credential_generation": credential_generation,
                "materializer_ref": materializer_ref,
                "target_path": str(handle["targetPath"]),
                "access_mode": str(handle["accessMode"]),
                "cleanup_ref": str(handle["cleanupRef"]),
                "attestation_ref": str(handle["attestationRef"]),
            }
            if existing is None:
                session.add(
                    OmnigentCredentialRuntimeRecord(
                        credential_runtime_ref=credential_runtime_ref, **values
                    )
                )
            elif any(getattr(existing, key) != value for key, value in values.items()):
                raise HarnessPlatformError(
                    "credential runtime ref conflicts with persisted authority",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            await session.commit()
        return handle

    def mount_source(self, handle: Mapping[str, Any]) -> tuple[Path, PurePosixPath]:
        root = self._credential_root(str(handle.get("credentialRuntimeRef") or ""))
        target = PurePosixPath(str(handle.get("targetPath") or ""))
        if not target.is_absolute() or ".." in target.parts:
            raise HarnessPlatformError(
                "credential target path is unsafe",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
            )
        source = root.joinpath(*target.parts[1:]).resolve()
        if not source.is_relative_to(root) or not source.is_file():
            raise HarnessPlatformError(
                "credential runtime file is unavailable",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
            )
        return source, target

    async def cleanup(
        self,
        handles: Sequence[Mapping[str, Any]],
        *,
        command_authority: Mapping[str, Any],
    ) -> None:
        if not command_authority.get("commandId"):
            raise HarnessPlatformError(
                "credential cleanup lacks canonical command authority",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        for handle in reversed(tuple(handles)):
            materializer_ref = str(handle.get("materializerRef") or "")
            if materializer_ref != "opencode-auth-json@1":
                raise HarnessPlatformError(
                    f"credential cleanup implementation {materializer_ref} is unavailable",
                    code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
                )
            root = self._credential_root(
                str(handle.get("credentialRuntimeRef") or "")
            )
            await asyncio.to_thread(
                cleanup_opencode_auth,
                host_root=root,
                provider_profile_ref=str(handle.get("providerProfileRef") or ""),
                credential_generation=int(handle.get("credentialGeneration") or 0),
            )
            try:
                root.rmdir()
            except OSError:
                # The materializer removes only owned credential state. A
                # non-empty root is retained for janitor inspection.
                pass


class DeploymentGenericHostServices:
    """Concrete implementation of every generic host runtime capability."""

    def __init__(
        self,
        *,
        session_factory: Any,
        artifact_gateway: Any,
        credential_materializer: TrustedCredentialMaterializer,
        runtime_root: Path | None = None,
        client: OmnigentHttpClient | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._artifact_gateway = artifact_gateway
        self._credential_materializer = credential_materializer
        self._workspace_root = Path(
            os.getenv("WORKFLOW_WORKSPACE_ROOT", "/work/agent_jobs")
        ).expanduser().resolve()
        self._runtime_root = (runtime_root or _runtime_root()).resolve()
        self._client = client or OmnigentHttpClient(
            base_url=resolved_server_url(), api_token=resolved_api_token()
        )
        self._states: dict[str, _DeploymentState] = {}

    @staticmethod
    def _state_key(authority: Mapping[str, Any]) -> str:
        key = str(authority.get("runtimeBindingRef") or "").strip()
        if not key.startswith("omnigent-runtime-binding:sha256:"):
            raise HarnessPlatformError(
                "deployment service lacks fenced runtime binding authority",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        return key

    def _state(self, authority: Mapping[str, Any]) -> _DeploymentState:
        return self._states.setdefault(self._state_key(authority), _DeploymentState())

    async def prepare_realization(
        self,
        *,
        request: AgentExecutionRequest,
        plan: OmnigentExecutionPlanEnvelope,
        authority: Mapping[str, Any],
    ) -> None:
        state = self._state(authority)
        if state.plan is not None and state.plan.planRef != plan.planRef:
            raise HarnessPlatformError(
                "runtime binding attempted to replace its immutable execution plan",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        state.request = request
        state.plan = plan

    async def _write_evidence(
        self, state: _DeploymentState, *, name: str, payload: Mapping[str, Any]
    ) -> str:
        if state.request is None:
            raise HarnessPlatformError(
                "deployment evidence lacks its execution request authority",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        return await self._artifact_gateway.write_json(
            request=state.request,
            name=name,
            payload=dict(payload),
            link_type="omnigent.generic_host.evidence",
        )

    async def materialize(
        self,
        value: AgentExecutionRequest | dict[str, Any],
        *,
        authority: dict[str, Any],
    ) -> dict[str, Any]:
        # This object implements both workspace and Skill protocols. The value
        # type selects the capability without provider or harness identity.
        if isinstance(value, AgentExecutionRequest):
            return await self._materialize_workspace(value, authority=authority)
        return await self._materialize_skills(value, authority=authority)

    async def _materialize_workspace(
        self, request: AgentExecutionRequest, *, authority: dict[str, Any]
    ) -> dict[str, Any]:
        raw_locator = (request.workspace_spec or {}).get("workspaceLocator")
        if not isinstance(raw_locator, Mapping):
            raise HarnessPlatformError(
                "generic host requires an authorized sandbox workspace locator",
                code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY,
            )
        locator = SandboxWorkspaceLocator.model_validate(raw_locator)
        workflow_id = (
            request.step_execution.workflow_id
            if request.step_execution is not None
            else request.correlation_id
        )
        step_execution_id = (
            request.step_execution.step_execution_id
            if request.step_execution is not None
            else request.idempotency_key
        )
        expected_id = hashlib.sha256(
            f"{workflow_id}:{step_execution_id}".encode("utf-8")
        ).hexdigest()[:24]
        owner_record = SandboxWorkspaceRecordStore(self._workspace_root).load(expected_id)
        if owner_record is None:
            raise HarnessPlatformError(
                "generic host workspace owner record is unavailable",
                code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY,
            )
        workspace = resolve_sandbox_workspace_locator(
            locator,
            workspace_root=self._workspace_root,
            expected_workspace_id=expected_id,
            owner_record=owner_record,
            expected_workflow_id=workflow_id,
            expected_step_execution_id=step_execution_id,
        )
        evidence = {
            "workspaceLocator": locator.model_dump(by_alias=True, mode="json"),
            "workflowId": workflow_id,
            "stepExecutionId": step_execution_id,
            "executionPlanRef": authority["executionPlanRef"],
        }
        state = self._state(authority)
        state.workspace_path = workspace
        evidence_ref = await self._write_evidence(
            state, name="generic-host.workspace-resolution.json", payload=evidence
        )
        return {
            "path": str(workspace),
            "resolutionRef": evidence_ref,
            "workspaceLocator": evidence["workspaceLocator"],
        }

    async def _materialize_skills(
        self, resolved_skills: dict[str, Any], *, authority: dict[str, Any]
    ) -> dict[str, Any]:
        state = self._state(authority)
        delivery_ref = str(resolved_skills.get("skillDeliveryRef") or "").strip()
        digest = str(resolved_skills.get("resolvedSkillSetDigest") or "").strip()
        resolved_ref = str(resolved_skills.get("resolvedSkillSetRef") or "").strip()
        if not delivery_ref or not digest:
            raise HarnessPlatformError(
                "resolved Skill delivery authority is incomplete",
                code=HarnessPlatformFailure.OMNIGENT_SKILL_SNAPSHOT_UNAVAILABLE,
            )

        projection = (self._runtime_root / "skills" / _digest(delivery_ref)).resolve()
        if not projection.is_relative_to(self._runtime_root):
            raise HarnessPlatformError(
                "Skill projection escapes runtime authority",
                code=HarnessPlatformFailure.OMNIGENT_SKILL_SNAPSHOT_UNAVAILABLE,
            )
        if resolved_ref:
            if state.request is None or state.request.resolved_skillset_ref != resolved_ref:
                raise HarnessPlatformError(
                    "request Skill snapshot differs from the admitted plan",
                    code=HarnessPlatformFailure.OMNIGENT_SKILL_DELIVERY_MISMATCH,
                )
            active = Path(os.getenv("MOONMIND_ACTIVE_SKILLS_DIR", "")).resolve()
            if not active.is_dir() or not (active / "_manifest.json").is_file():
                raise HarnessPlatformError(
                    "resolved active Skill snapshot is unavailable to the host owner",
                    code=HarnessPlatformFailure.OMNIGENT_SKILL_SNAPSHOT_UNAVAILABLE,
                )
            projection = active
        else:
            projection.mkdir(parents=True, exist_ok=True)
            manifest = projection / "_manifest.json"
            payload = {
                "schemaVersion": "moonmind.active-skill-set.v1",
                "skills": [],
                "resolvedSkillSetDigest": digest,
                "skillDeliveryRef": delivery_ref,
            }
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            if manifest.exists() and manifest.read_text(encoding="utf-8") != encoded:
                raise HarnessPlatformError(
                    "existing empty Skill projection conflicts with the plan",
                    code=HarnessPlatformFailure.OMNIGENT_SKILL_DELIVERY_MISMATCH,
                )
            manifest.write_text(encoded, encoding="utf-8")
        evidence = {
            "deliveryRef": delivery_ref,
            "resolvedSkillSetDigest": digest,
            "resolvedSkillSetRef": resolved_ref or None,
            "manifestPresent": True,
        }
        attestation_ref = await self._write_evidence(
            state, name="generic-host.skill-delivery.json", payload=evidence
        )
        state.skill_path = projection
        state.skill_delivery_ref = delivery_ref
        state.skill_attestation_ref = attestation_ref
        return {
            "path": str(projection),
            "deliveryRef": delivery_ref,
            "digest": digest,
            "attestationRef": attestation_ref,
        }

    def _prepare_scripts(self) -> Path:
        source = Path(__file__).resolve().parents[3] / "services" / "omnigent" / "scripts"
        target = (self._runtime_root / "scripts").resolve()
        target.mkdir(parents=True, exist_ok=True)
        for name in _SCRIPTS:
            source_file = source / name
            target_file = target / name
            if not source_file.is_file() or source_file.is_symlink():
                raise HarnessPlatformError(
                    f"generic host runtime script {name} is unavailable",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY,
                )
            payload = source_file.read_bytes()
            if target_file.exists() and target_file.read_bytes() != payload:
                target_file.unlink()
            if not target_file.exists():
                target_file.write_bytes(payload)
            target_file.chmod(0o555)
        return target

    async def launch(
        self,
        *,
        host_class: HostClass,
        launch_policy: LaunchPolicy,
        workspace_handle: Any,
        skill_handle: Any,
        credential_handles: list[dict[str, Any]],
        authority: dict[str, Any],
    ) -> dict[str, Any]:
        state = self._state(authority)
        if state.plan is None or state.request is None:
            raise HarnessPlatformError(
                "generic host launch was not prepared with immutable plan authority",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        binding_digest = _digest(
            authority["runtimeBindingRef"], host_class.ref, launch_policy.ref
        )
        binding_ref = f"omnigent-host-binding:sha256:{binding_digest}"
        lease_ref = f"omnigent-host-lease:sha256:{binding_digest}"
        container_name = "moonmind-omnigent-" + binding_digest[:24]
        generation = 1

        provider_refs = sorted(
            {
                str(binding.get("providerProfileRef") or "")
                for binding in state.plan.payload.credentialBindings.values()
                if binding.get("providerProfileRef")
            }
        )
        async with self._session_factory() as session:
            existing_binding = await session.get(
                OmnigentHostBindingRecordV2, binding_ref
            )
            binding_values = {
                "host_class_ref": host_class.ref,
                "launch_policy_ref": launch_policy.ref,
                "harness_id": state.plan.payload.harnessId,
                "harness_implementation_ref": (
                    state.plan.payload.harnessImplementationRef
                ),
                "execution_plan_ref": state.plan.planRef,
                "provider_profile_refs_json": provider_refs,
            }
            if existing_binding is None:
                session.add(
                    OmnigentHostBindingRecordV2(
                        binding_id=binding_ref, **binding_values
                    )
                )
            elif any(
                getattr(existing_binding, key) != value
                for key, value in binding_values.items()
            ):
                raise HarnessPlatformError(
                    "host binding ref conflicts with persisted authority",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            lease = await session.get(OmnigentHostLeaseRecordV2, lease_ref)
            if lease is None:
                lease = OmnigentHostLeaseRecordV2(
                    lease_id=lease_ref,
                    binding_id=binding_ref,
                    host_class_ref=host_class.ref,
                    generation=generation,
                    status="allocating",
                    host_lease_generation=generation,
                )
                session.add(lease)
            elif (
                lease.binding_id != binding_ref
                or lease.host_class_ref != host_class.ref
                or lease.host_lease_generation != generation
            ):
                raise HarnessPlatformError(
                    "host lease ref conflicts with persisted fencing authority",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            await session.commit()

        async def docker_runner(argv: Sequence[str]) -> tuple[int, bytes, bytes]:
            return await _run_docker(argv)

        egress_attestation = await attest_docker_egress(
            runner=docker_runner,
            profile=OMNIGENT_EGRESS_PROFILE,
            backend_ref="docker-cli/trusted-worker",
        )
        code, stdout, stderr = await _run_docker(
            (
                "inspect",
                "--format",
                '{{index .Config.Labels "moonmind.host_lease_id"}}',
                container_name,
            )
        )
        if code == 0:
            if stdout.decode(errors="replace").strip() != lease_ref:
                raise HarnessPlatformError(
                    "deterministic host name is owned by another lease",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
        else:
            if not _docker_object_missing(stderr):
                raise HarnessPlatformError(
                    "generic host ownership could not be inspected",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY,
                )
            workspace_source = daemon_visible_workspace_path(
                Path(str(workspace_handle["path"]))
            )
            skill_source = daemon_visible_workspace_path(
                Path(str(skill_handle["path"]))
            )
            scripts = daemon_visible_workspace_path(self._prepare_scripts())
            runtime = dict(host_class.runtime)
            uid = int(runtime.get("uid", 1000))
            gid = int(runtime.get("gid", 1000))
            home = str(runtime.get("home") or "/home/app")
            startup_script = _safe_segment(
                str(runtime.get("startupScript") or "start-host-with-projections.sh")
            )
            generation_env = str(runtime.get("credentialGenerationEnv") or "").strip()
            args: list[str] = [
                "run",
                "-d",
                "--platform",
                state.plan.payload.supportIdentity.architecture,
                "--name",
                container_name,
                "--hostname",
                container_name,
                "--user",
                f"{uid}:{gid}",
                "--workdir",
                home,
                "--network",
                OMNIGENT_EGRESS_PROFILE.network_ref,
                *structured_container_security_args(),
                "--cpus",
                str(launch_policy.limits["cpuMillis"] / 1000),
                "--memory",
                f"{launch_policy.limits['memoryMiB']}m",
                "--pids-limit",
                str(launch_policy.limits["processes"]),
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size="
                f"{launch_policy.limits['temporaryStorageMiB']}m",
                "--tmpfs",
                f"{home}/.omnigent:rw,noexec,nosuid,size=32m,uid={uid},gid={gid}",
                "--tmpfs",
                f"{home}/.cache:rw,noexec,nosuid,size=128m,uid={uid},gid={gid}",
                "--mount",
                f"type=bind,src={workspace_source},dst=/workspaces/run",
                "--mount",
                f"type=bind,src={skill_source},dst=/opt/moonmind-skills,readonly",
                "--mount",
                f"type=bind,src={scripts},dst=/opt/moonmind,readonly",
                "--label",
                "moonmind.kind=omnigent-generic-host",
                "--label",
                f"moonmind.host_binding_id={binding_ref}",
                "--label",
                f"moonmind.host_lease_id={lease_ref}",
                "--label",
                f"moonmind.execution_plan_ref={state.plan.planRef}",
                "--label",
                f"moonmind.runtime_binding_ref={authority['runtimeBindingRef']}",
                "--label",
                f"moonmind.host_lease_generation={generation}",
                "--env",
                f"HOME={home}",
                "--env",
                f"OMNIGENT_SERVER_URL={resolved_server_url()}",
                "--env",
                "MOONMIND_ACTIVE_SKILLS_DIR=/opt/moonmind-skills",
                "--env",
                f"MOONMIND_STEP_EXECUTION_ID={state.request.idempotency_key}",
            ]
            for item in omnigent_proxy_env():
                args.extend(("--env", item))
            generations = {
                int(handle.get("credentialGeneration") or 0)
                for handle in credential_handles
            }
            if generation_env:
                if len(generations) != 1 or min(generations) < 1:
                    raise HarnessPlatformError(
                        "host credential generation authority is ambiguous",
                        code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED,
                    )
                args.extend(("--env", f"{generation_env}={next(iter(generations))}"))
            for handle in credential_handles:
                source, target = self._credential_materializer.mount_source(handle)
                source = daemon_visible_workspace_path(source)
                args.extend(
                    (
                        "--mount",
                        f"type=bind,src={source},dst={target},readonly",
                    )
                )
            child_env = dict(os.environ)
            if resolved_api_token():
                child_env["OMNIGENT_API_TOKEN"] = resolved_api_token()
                args.extend(("--env", "OMNIGENT_API_TOKEN"))
            args.extend(
                (
                    "--entrypoint",
                    f"/opt/moonmind/{startup_script}",
                    host_class.imageRef,
                )
            )
            code, _, _ = await _run_docker(args, env=child_env)
            if code:
                raise HarnessPlatformError(
                    "digest-pinned generic Omnigent host launch failed",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY,
                )

        async with self._session_factory() as session:
            lease = await session.get(OmnigentHostLeaseRecordV2, lease_ref)
            if lease is None:
                raise HarnessPlatformError(
                    "persisted host lease disappeared after launch",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            lease.status = "registering"
            await session.commit()

        state.host_binding_ref = binding_ref
        state.host_lease_ref = lease_ref
        state.host_lease_generation = generation
        state.container_name = container_name
        state.egress_attestation = egress_attestation
        state.launched_at = datetime.now(UTC)
        return {
            "hostId": None,
            "expectedHostName": container_name,
            "hostBindingRef": binding_ref,
            "hostLeaseRef": lease_ref,
            "hostLeaseGeneration": generation,
        }

    @staticmethod
    def _host_id(host: Mapping[str, Any]) -> str:
        return str(host.get("id") or host.get("hostId") or host.get("host_id") or "")

    @staticmethod
    def _host_name(host: Mapping[str, Any]) -> str:
        return str(host.get("name") or host.get("hostname") or "")

    async def wait_for_registration(
        self,
        *,
        expected_host_id: str | None = None,
        authority: dict[str, Any],
    ) -> dict[str, Any]:
        state = self._state(authority)
        if state.plan is None or not state.container_name or not state.host_lease_ref:
            raise HarnessPlatformError(
                "host registration lacks persisted launch authority",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        exact: dict[str, Any] | None = None
        for attempt in range(_REGISTRATION_ATTEMPTS):
            hosts = await self._client.list_hosts()
            matches = [
                host
                for host in hosts
                if (
                    self._host_id(host) == expected_host_id
                    if expected_host_id
                    else self._host_name(host) == state.container_name
                )
                and str(host.get("status") or "online").lower() == "online"
            ]
            if len(matches) == 1:
                exact = dict(matches[0])
                break
            if len(matches) > 1:
                raise HarnessPlatformError(
                    "host registration selector is ambiguous",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            if attempt + 1 < _REGISTRATION_ATTEMPTS:
                await asyncio.sleep(_REGISTRATION_INTERVAL_SECONDS)
        if exact is None:
            raise HarnessPlatformError(
                "exact generic host did not register within the bounded window",
                code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY,
            )
        host_id = self._host_id(exact)
        metadata = exact.get("metadata") if isinstance(exact.get("metadata"), Mapping) else {}
        attestation = (
            exact.get("attestation")
            or exact.get("harnessAttestation")
            or metadata.get("attestation")
            or metadata.get("harnessAttestation")
        )
        if not host_id or not isinstance(attestation, Mapping):
            raise HarnessPlatformError(
                "exact host registration omitted identity or harness attestation",
                code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY,
            )

        model_response = await self._client.get_host_model_options(host_id)
        raw_options = (
            model_response.get("models")
            or model_response.get("options")
            or model_response.get("data")
            or []
        )
        if not isinstance(raw_options, list):
            raw_options = []
        planned_model = state.plan.payload.modelConfig.qualifiedId
        model_ids = {
            str(item.get("qualifiedId") or item.get("modelId") or item.get("id") or "")
            for item in raw_options
            if isinstance(item, Mapping)
        }
        available = planned_model is not None and planned_model in model_ids
        model_evidence = {
            "hostId": host_id,
            "modelId": planned_model,
            "available": available,
            "observedModelIds": sorted(item for item in model_ids if item),
            "executionPlanRef": state.plan.planRef,
        }
        model_evidence["attestationRef"] = await self._write_evidence(
            state,
            name="generic-host.model-options.json",
            payload=model_evidence,
        )
        capability_evidence = {
            "hostId": host_id,
            "required": list(state.plan.payload.classAdmissionDecision.get("required") or []),
            "observedCapabilities": dict(attestation.get("capabilities") or {}),
        }
        capability_ref = await self._write_evidence(
            state,
            name="generic-host.capability-decision.json",
            payload=capability_evidence,
        )
        async with self._session_factory() as session:
            lease = await session.get(
                OmnigentHostLeaseRecordV2, state.host_lease_ref
            )
            if lease is None or lease.host_lease_generation != state.host_lease_generation:
                raise HarnessPlatformError(
                    "host lease generation changed before registration",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            lease.omnigent_host_id = host_id
            lease.status = "running"
            await session.commit()
        state.omnigent_host_id = host_id
        state.evidence["capability"] = capability_ref
        state.evidence["model"] = str(model_evidence["attestationRef"])
        return {
            "hostId": host_id,
            "attestation": dict(attestation),
            "exactHostCapabilityDecisionRef": capability_ref,
            "modelOptionAttestation": model_evidence,
        }

    async def attest(
        self,
        value: str | LaunchPolicy,
        expected_image_ref: str | None = None,
        *,
        authority: dict[str, Any],
    ) -> dict[str, Any]:
        # This object implements image and egress attestors. Their protocol
        # argument shapes are disjoint and contain no provider identity.
        if isinstance(value, LaunchPolicy):
            return await self._attest_egress(value, authority=authority)
        return await self._attest_image(
            value, str(expected_image_ref or ""), authority=authority
        )

    async def _attest_image(
        self, host_id: str, expected_image_ref: str, *, authority: dict[str, Any]
    ) -> dict[str, Any]:
        state = self._state(authority)
        if state.omnigent_host_id != host_id or not state.container_name:
            raise HarnessPlatformError(
                "image attestation host differs from registered authority",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        code, stdout, _ = await _run_docker(
            (
                "inspect",
                "--format",
                '{{json .Config.Image}}',
                state.container_name,
            )
        )
        try:
            observed_ref = json.loads(stdout.decode("utf-8")) if code == 0 else ""
        except json.JSONDecodeError:
            observed_ref = ""
        if observed_ref != expected_image_ref:
            raise HarnessPlatformError(
                "running host image differs from the digest-pinned Host Class",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
            )
        evidence = {
            "hostId": host_id,
            "containerName": state.container_name,
            "observedImageRef": observed_ref,
            "hostLeaseRef": state.host_lease_ref,
            "hostLeaseGeneration": state.host_lease_generation,
        }
        evidence["attestationRef"] = await self._write_evidence(
            state, name="generic-host.image-attestation.json", payload=evidence
        )
        return evidence

    async def _attest_egress(
        self, launch_policy: LaunchPolicy, *, authority: dict[str, Any]
    ) -> dict[str, Any]:
        state = self._state(authority)
        support_identity = (
            state.plan.payload.supportIdentity if state.plan is not None else None
        )
        if (
            state.egress_attestation is None
            or not state.container_name
            or support_identity is None
            or launch_policy.network.get("egressPolicyRef")
            != "omnigent-restricted-egress@1"
        ):
            raise HarnessPlatformError(
                "generic host egress authority is unavailable or incompatible",
                code=HarnessPlatformFailure.OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE,
            )

        async def docker_runner(argv: Sequence[str]) -> tuple[int, bytes, bytes]:
            return await _run_docker(argv)

        observed = await attest_docker_workload_egress(
            runner=docker_runner,
            profile=OMNIGENT_EGRESS_PROFILE,
            attestation=state.egress_attestation,
            attachment_identity=state.container_name,
            expected_image_ref=support_identity.omnigentHostBuildRef,
            started_at=state.launched_at,
            finished_at=datetime.now(UTC),
        )
        evidence = {**observed, "enforced": True}
        evidence["attestationRef"] = await self._write_evidence(
            state, name="generic-host.egress-attestation.json", payload=evidence
        )
        return evidence

    async def cleanup(
        self,
        *,
        plan_ref: str,
        runtime_binding_ref: str | None,
        host_id: str | None,
        authority: dict[str, Any],
    ) -> None:
        state = self._state(authority)
        if runtime_binding_ref and not state.container_name:
            async with self._session_factory() as session:
                binding = await session.get(
                    OmnigentRuntimeBindingRecord, runtime_binding_ref
                )
                if binding is not None and binding.host_binding_ref:
                    state.host_binding_ref = binding.host_binding_ref
                    state.host_lease_ref = binding.host_lease_ref
                    state.host_lease_generation = binding.host_lease_generation
                    binding_digest = str(binding.host_binding_ref).rsplit(
                        "sha256:", 1
                    )[-1]
                    state.container_name = (
                        "moonmind-omnigent-" + binding_digest[:24]
                    )
                    if state.plan is None:
                        plan_record = await session.get(
                            OmnigentExecutionPlanRecord, binding.execution_plan_ref
                        )
                        if plan_record is not None:
                            state.plan = OmnigentExecutionPlanEnvelope.model_validate(
                                {
                                    "schemaVersion": plan_record.schema_version,
                                    "planRef": plan_record.plan_ref,
                                    "payload": plan_record.payload_json,
                                }
                            )
        if state.plan is not None and state.plan.planRef != plan_ref:
            raise HarnessPlatformError(
                "cleanup plan differs from persisted host authority",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        if not state.container_name or not state.host_lease_ref:
            return
        code, stdout, stderr = await _run_docker(
            (
                "inspect",
                "--format",
                '{{index .Config.Labels "moonmind.host_lease_id"}}',
                state.container_name,
            )
        )
        if code == 0:
            if stdout.decode(errors="replace").strip() != state.host_lease_ref:
                raise HarnessPlatformError(
                    "cleanup refused a container owned by another host lease",
                    code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
                )
            remove_code, _, _ = await _run_docker(("rm", "-f", state.container_name))
            if remove_code:
                raise HarnessPlatformError(
                    "generic host cleanup remains pending",
                    code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
                )
        elif not _docker_object_missing(stderr):
            raise HarnessPlatformError(
                "generic host cleanup could not inspect its owned container",
                code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
            )
        async with self._session_factory() as session:
            lease = await session.get(
                OmnigentHostLeaseRecordV2, state.host_lease_ref
            )
            if lease is not None:
                if lease.host_lease_generation != state.host_lease_generation:
                    raise HarnessPlatformError(
                        "cleanup host lease generation is stale",
                        code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
                    )
                lease.status = "cleaned"
                await session.commit()


__all__ = [
    "DeploymentGenericHostServices",
    "TrustedCredentialMaterializer",
]
