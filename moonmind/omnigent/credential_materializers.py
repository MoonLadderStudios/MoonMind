"""Side-effecting credential materializer implementations.

Descriptor compatibility remains in ``harness_platform.materializers``.  This
module owns secret resolution-adjacent runtime mutations and returns only
secret-free handles.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from moonmind.omnigent.bridge_artifacts import OmnigentArtifactGateway
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.materializers import (
    OPENCODE_AUTH_GID,
    OPENCODE_AUTH_UID,
    build_opencode_auth_json_bytes,
)
from moonmind.omnigent.provider_leases import AcquiredProviderLease
from moonmind.omnigent.secret_resolution import ScopedSecretBundle
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.runtime.command_runner import run_runtime_command


class CredentialAttachment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    sourceRef: str = Field(alias="sourceRef")
    targetPath: str = Field(alias="targetPath")
    accessMode: str = Field(alias="accessMode")


class CredentialRuntimeHandle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    credentialRuntimeRef: str = Field(alias="credentialRuntimeRef")
    providerProfileRef: str = Field(alias="providerProfileRef")
    providerLeaseRef: str = Field(alias="providerLeaseRef")
    credentialGeneration: int = Field(alias="credentialGeneration", ge=1)
    materializerRef: str = Field(alias="materializerRef")
    attachments: tuple[CredentialAttachment, ...] = ()
    runtimeEnvironment: dict[str, str] = Field(
        default_factory=dict, alias="runtimeEnvironment"
    )
    cleanupRef: str = Field(alias="cleanupRef")
    attestationRef: str | None = Field(None, alias="attestationRef")
    hostOwned: bool = Field(False, alias="hostOwned")


class CredentialCleanupResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cleanupRef: str = Field(alias="cleanupRef")
    removed: bool
    deferred: bool = False
    evidence: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class CredentialMaterializationContext:
    request: AgentExecutionRequest
    acquired: AcquiredProviderLease
    secrets: ScopedSecretBundle
    writer_image_ref: str
    artifact_gateway: OmnigentArtifactGateway
    model_qualified_id: str = ""
    provider_route_ref: str = ""


class DockerCommandBackend(Protocol):
    async def run(
        self,
        argv: list[str],
        *,
        input_bytes: bytes | None = None,
        timeout_seconds: float = 60.0,
    ) -> tuple[int, bytes, bytes]: ...


class LocalDockerCommandBackend:
    async def run(
        self,
        argv: list[str],
        *,
        input_bytes: bytes | None = None,
        timeout_seconds: float = 60.0,
    ) -> tuple[int, bytes, bytes]:
        return await run_runtime_command(
            argv,
            input_bytes=input_bytes,
            timeout_seconds=timeout_seconds,
            output_limit_bytes=16_384,
        )


class CredentialMaterializerImplementation(Protocol):
    ref: str

    async def materialize(
        self, context: CredentialMaterializationContext
    ) -> CredentialRuntimeHandle: ...

    async def attest(self, handle: CredentialRuntimeHandle) -> dict[str, Any]: ...

    async def cleanup(
        self,
        handle: CredentialRuntimeHandle,
        expected_generation: int,
    ) -> CredentialCleanupResult: ...


def credential_runtime_identity(
    acquired: AcquiredProviderLease, materializer_ref: str
) -> tuple[str, str]:
    canonical = "\0".join(
        (
            acquired.provider_profile_ref,
            acquired.provider_lease_ref,
            str(acquired.credential_generation),
            materializer_ref,
        )
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"credential-runtime:sha256:{digest}", digest


def anticipated_credential_handle(
    acquired: AcquiredProviderLease,
    materializer_ref: str,
) -> CredentialRuntimeHandle:
    runtime_ref, digest = credential_runtime_identity(acquired, materializer_ref)
    attachments: list[dict[str, Any]] = []
    runtime_environment: dict[str, str] = {}
    host_owned = materializer_ref == "host-owned-auth@1"
    if materializer_ref == "opencode-auth-json@1":
        attachments.append(
            {
                "kind": "volume",
                "sourceRef": f"mm-omnigent-credential-{digest[:32]}",
                "targetPath": "/home/app/.local/share/opencode",
                "accessMode": "read-only",
            }
        )
    elif materializer_ref == "omnigent-provider-config@1":
        target = "/home/app/.moonmind-provider-config"
        attachments.append(
            {
                "kind": "volume",
                "sourceRef": f"mm-omnigent-credential-{digest[:32]}",
                "targetPath": target,
                "accessMode": "read-only",
            }
        )
        runtime_environment["OMNIGENT_CONFIG_HOME"] = target
    elif materializer_ref not in {"none@1", "host-owned-auth@1"}:
        raise HarnessPlatformError(
            f"credential materializer implementation {materializer_ref} is unavailable",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE,
        )
    return CredentialRuntimeHandle.model_validate(
        {
            "credentialRuntimeRef": runtime_ref,
            "providerProfileRef": acquired.provider_profile_ref,
            "providerLeaseRef": acquired.provider_lease_ref,
            "credentialGeneration": acquired.credential_generation,
            "materializerRef": materializer_ref,
            "attachments": attachments,
            "runtimeEnvironment": runtime_environment,
            "cleanupRef": f"credential-cleanup:sha256:{digest}",
            "attestationRef": None,
            "hostOwned": host_owned,
        }
    )


class DockerOpencodeAuthJsonMaterializer:
    ref = "opencode-auth-json@1"

    def __init__(self, backend: DockerCommandBackend | None = None) -> None:
        self._backend = backend or LocalDockerCommandBackend()

    async def _run(
        self,
        argv: list[str],
        *,
        input_bytes: bytes | None = None,
        failure: str,
    ) -> bytes:
        code, stdout, stderr = await self._backend.run(argv, input_bytes=input_bytes)
        if code != 0:
            detail = (stderr or stdout).decode("utf-8", errors="replace")[:512]
            raise HarnessPlatformError(
                f"{failure}: {detail}",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
            )
        return stdout

    async def materialize(
        self, context: CredentialMaterializationContext
    ) -> CredentialRuntimeHandle:
        acquired = context.acquired
        anticipated = anticipated_credential_handle(acquired, self.ref)
        runtime_ref, digest = credential_runtime_identity(acquired, self.ref)
        volume_name = f"mm-omnigent-credential-{digest[:32]}"
        payload = build_opencode_auth_json_bytes(
            api_key=context.secrets.require("opencode_api_key")
        )
        await self._run(
            [
                "docker",
                "volume",
                "create",
                "--label",
                "moonmind.owner=generic-omnigent-host",
                "--label",
                f"moonmind.credential_runtime_ref={runtime_ref}",
                "--label",
                f"moonmind.credential_generation={acquired.credential_generation}",
                volume_name,
            ],
            failure="credential volume creation failed",
        )
        writer_script = (
            "set -eu; "
            "install -d -m 0700 -o 1000 -g 1000 /credential; "
            'tmp=/credential/.auth.json.tmp; cat > "$tmp"; '
            'chown 1000:1000 "$tmp"; chmod 0600 "$tmp"; '
            'mv "$tmp" /credential/auth.json; '
            "printf '%s\\n' \"$1\" > /credential/.moonmind-generation; "
            "chown 1000:1000 /credential/.moonmind-generation; "
            "chmod 0600 /credential/.moonmind-generation"
        )
        try:
            await self._run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-i",
                    "--network",
                    "none",
                    "--read-only",
                    "--mount",
                    f"type=volume,src={volume_name},dst=/credential",
                    "--entrypoint",
                    "/bin/sh",
                    context.writer_image_ref,
                    "-ceu",
                    writer_script,
                    "--",
                    str(acquired.credential_generation),
                ],
                input_bytes=payload,
                failure="credential writer failed",
            )
            verify_script = (
                "set -eu; "
                'test "$(stat -c %u:%g /credential)" = 1000:1000; '
                'test "$(stat -c %a /credential)" = 700; '
                'test "$(stat -c %u:%g /credential/auth.json)" = 1000:1000; '
                'test "$(stat -c %a /credential/auth.json)" = 600; '
                'test "$(cat /credential/.moonmind-generation)" = "$1"; '
                'python3 -c \'import json; d=json.load(open("/credential/auth.json")); '
                'assert set(d)=={"opencode-go"}; assert d["opencode-go"]["type"]=="api"; '
                'assert isinstance(d["opencode-go"]["key"],str) and d["opencode-go"]["key"]\''
            )
            await self._run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--read-only",
                    "--mount",
                    f"type=volume,src={volume_name},dst=/credential,readonly",
                    "--entrypoint",
                    "/bin/sh",
                    context.writer_image_ref,
                    "-ceu",
                    verify_script,
                    "--",
                    str(acquired.credential_generation),
                ],
                failure="credential volume attestation failed",
            )
        except BaseException:
            await self._backend.run(["docker", "volume", "rm", volume_name])
            raise
        finally:
            # Drop Python references immediately after the writer consumes stdin.
            payload = b""
            context.secrets.clear()
        evidence = {
            "schemaVersion": "moonmind.credential-materialization-attestation.v1",
            "credentialRuntimeRef": runtime_ref,
            "providerProfileRef": acquired.provider_profile_ref,
            "providerLeaseRef": acquired.provider_lease_ref,
            "credentialGeneration": acquired.credential_generation,
            "materializerRef": self.ref,
            "attachment": {
                "kind": "volume",
                "sourceRef": volume_name,
                "targetPath": "/home/app/.local/share/opencode",
                "accessMode": "read-only",
            },
            "owner": f"{OPENCODE_AUTH_UID}:{OPENCODE_AUTH_GID}",
            "directoryMode": "0700",
            "fileMode": "0600",
            "secretPresent": True,
            "secretValueRecorded": False,
        }
        try:
            attestation_ref = await context.artifact_gateway.write_json(
                request=context.request,
                name=f"credential-{digest[:16]}-attestation.json",
                payload=evidence,
                link_type="evidence.credential_materialization",
            )
        except BaseException:
            await self.cleanup(anticipated, anticipated.credentialGeneration)
            raise
        return CredentialRuntimeHandle.model_validate(
            {
                "credentialRuntimeRef": runtime_ref,
                "providerProfileRef": acquired.provider_profile_ref,
                "providerLeaseRef": acquired.provider_lease_ref,
                "credentialGeneration": acquired.credential_generation,
                "materializerRef": self.ref,
                "attachments": [evidence["attachment"]],
                "cleanupRef": f"credential-cleanup:sha256:{digest}",
                "attestationRef": attestation_ref,
            }
        )

    async def attest(self, handle: CredentialRuntimeHandle) -> dict[str, Any]:
        if not handle.attachments:
            raise HarnessPlatformError(
                "OpenCode credential runtime has no volume attachment",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
            )
        return {
            "credentialRuntimeRef": handle.credentialRuntimeRef,
            "credentialGeneration": handle.credentialGeneration,
            "attestationRef": handle.attestationRef,
        }

    async def cleanup(
        self,
        handle: CredentialRuntimeHandle,
        expected_generation: int,
    ) -> CredentialCleanupResult:
        if handle.credentialGeneration != expected_generation:
            raise HarnessPlatformError(
                "credential cleanup generation is fenced",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED,
            )
        volume = handle.attachments[0].sourceRef
        code, stdout, stderr = await self._backend.run(
            [
                "docker",
                "volume",
                "inspect",
                "--format",
                '{{ index .Labels "moonmind.credential_runtime_ref" }}|{{ index .Labels "moonmind.credential_generation" }}',
                volume,
            ]
        )
        if code != 0:
            detail = (stderr or stdout).decode("utf-8", errors="replace").lower()
            if "no such volume" in detail or "no such object" in detail:
                # Already absent is idempotent cleanup success.
                return CredentialCleanupResult(
                    cleanupRef=handle.cleanupRef,
                    removed=True,
                    evidence={"alreadyAbsent": True},
                )
            raise HarnessPlatformError(
                "credential volume cleanup inspection is deferred",
                code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
            )
        expected = f"{handle.credentialRuntimeRef}|{expected_generation}"
        if stdout.decode("utf-8", errors="replace").strip() != expected:
            raise HarnessPlatformError(
                "credential volume ownership or generation fence mismatch",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED,
            )
        code, _stdout, stderr = await self._backend.run(
            ["docker", "volume", "rm", volume]
        )
        if code != 0:
            raise HarnessPlatformError(
                "credential volume cleanup is deferred",
                code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
            )
        return CredentialCleanupResult(
            cleanupRef=handle.cleanupRef,
            removed=True,
            evidence={"volumeRef": volume},
        )


class DockerOmnigentProviderConfigMaterializer(DockerOpencodeAuthJsonMaterializer):
    """Write one run-scoped Omnigent provider config for ``pi-native``.

    The config is mounted separately from mutable Omnigent host state and the
    host receives only ``OMNIGENT_CONFIG_HOME=<mount path>``. JSON is emitted
    because it is a valid YAML document and avoids a writer-image dependency on
    a YAML library.
    """

    ref = "omnigent-provider-config@1"
    _TARGET = "/home/app/.moonmind-provider-config"
    _ROUTES = {
        "anthropic": ("anthropic", "https://api.anthropic.com"),
        "openai": ("openai", "https://api.openai.com/v1"),
    }

    async def materialize(
        self, context: CredentialMaterializationContext
    ) -> CredentialRuntimeHandle:
        acquired = context.acquired
        anticipated = anticipated_credential_handle(acquired, self.ref)
        runtime_ref, digest = credential_runtime_identity(acquired, self.ref)
        volume_name = f"mm-omnigent-credential-{digest[:32]}"
        route = context.provider_route_ref.strip()
        route_config = self._ROUTES.get(route)
        qualified_model = context.model_qualified_id.strip()
        if route_config is None or not qualified_model.startswith(f"{route}/"):
            context.secrets.clear()
            raise HarnessPlatformError(
                f"Omnigent provider config route {route!r} is unsupported",
                code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
            )
        family, base_url = route_config
        model_id = qualified_model.split("/", 1)[1]
        payload = json.dumps(
            {
                "providers": {
                    "moonmind": {
                        "kind": "key",
                        "default": [family, "pi"],
                        family: {
                            "base_url": base_url,
                            "api_key": context.secrets.require("api_key"),
                            "models": {"default": model_id},
                        },
                    }
                }
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        await self._run(
            [
                "docker",
                "volume",
                "create",
                "--label",
                "moonmind.owner=generic-omnigent-host",
                "--label",
                f"moonmind.credential_runtime_ref={runtime_ref}",
                "--label",
                f"moonmind.credential_generation={acquired.credential_generation}",
                volume_name,
            ],
            failure="credential volume creation failed",
        )
        writer_script = (
            "set -eu; install -d -m 0700 -o 1000 -g 1000 /credential; "
            'tmp=/credential/.config.yaml.tmp; cat > "$tmp"; '
            'chown 1000:1000 "$tmp"; chmod 0600 "$tmp"; '
            'mv "$tmp" /credential/config.yaml; '
            "printf '%s\\n' \"$1\" > /credential/.moonmind-generation; "
            "chown 1000:1000 /credential/.moonmind-generation; "
            "chmod 0600 /credential/.moonmind-generation"
        )
        try:
            await self._run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-i",
                    "--network",
                    "none",
                    "--read-only",
                    "--mount",
                    f"type=volume,src={volume_name},dst=/credential",
                    "--entrypoint",
                    "/bin/sh",
                    context.writer_image_ref,
                    "-ceu",
                    writer_script,
                    "--",
                    str(acquired.credential_generation),
                ],
                input_bytes=payload,
                failure="provider config writer failed",
            )
            await self._run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--read-only",
                    "--mount",
                    f"type=volume,src={volume_name},dst=/credential,readonly",
                    "--entrypoint",
                    "/bin/sh",
                    context.writer_image_ref,
                    "-ceu",
                    'set -eu; test "$(stat -c %u:%g /credential)" = 1000:1000; '
                    'test "$(stat -c %a /credential)" = 700; '
                    'test "$(stat -c %a /credential/config.yaml)" = 600; '
                    'test "$(cat /credential/.moonmind-generation)" = "$1"; '
                    'python3 -c \'import json; d=json.load(open("/credential/config.yaml")); '
                    'assert list(d)==["providers"]; assert list(d["providers"]); '
                    'assert d["providers"]["moonmind"]["kind"]=="key"\'',
                    "--",
                    str(acquired.credential_generation),
                ],
                failure="provider config volume attestation failed",
            )
        except BaseException:
            await self._backend.run(["docker", "volume", "rm", volume_name])
            raise
        finally:
            payload = b""
            context.secrets.clear()
        attachment = {
            "kind": "volume",
            "sourceRef": volume_name,
            "targetPath": self._TARGET,
            "accessMode": "read-only",
        }
        evidence = {
            "schemaVersion": "moonmind.credential-materialization-attestation.v1",
            "credentialRuntimeRef": runtime_ref,
            "providerProfileRef": acquired.provider_profile_ref,
            "providerLeaseRef": acquired.provider_lease_ref,
            "credentialGeneration": acquired.credential_generation,
            "materializerRef": self.ref,
            "providerRouteRef": route,
            "modelQualifiedId": qualified_model,
            "attachment": attachment,
            "owner": "1000:1000",
            "directoryMode": "0700",
            "fileMode": "0600",
            "secretPresent": True,
            "secretValueRecorded": False,
        }
        try:
            attestation_ref = await context.artifact_gateway.write_json(
                request=context.request,
                name=f"credential-{digest[:16]}-attestation.json",
                payload=evidence,
                link_type="evidence.credential_materialization",
            )
        except BaseException:
            await self.cleanup(anticipated, anticipated.credentialGeneration)
            raise
        return CredentialRuntimeHandle.model_validate(
            {
                "credentialRuntimeRef": runtime_ref,
                "providerProfileRef": acquired.provider_profile_ref,
                "providerLeaseRef": acquired.provider_lease_ref,
                "credentialGeneration": acquired.credential_generation,
                "materializerRef": self.ref,
                "attachments": [attachment],
                "runtimeEnvironment": {"OMNIGENT_CONFIG_HOME": self._TARGET},
                "cleanupRef": f"credential-cleanup:sha256:{digest}",
                "attestationRef": attestation_ref,
            }
        )


class NoopCredentialMaterializer:
    def __init__(self, ref: str, *, host_owned: bool = False) -> None:
        self.ref = ref
        self._host_owned = host_owned

    async def materialize(
        self, context: CredentialMaterializationContext
    ) -> CredentialRuntimeHandle:
        runtime_ref, digest = credential_runtime_identity(context.acquired, self.ref)
        context.secrets.clear()
        attestation_ref = await context.artifact_gateway.write_json(
            request=context.request,
            name=f"credential-{digest[:16]}-attestation.json",
            payload={
                "credentialRuntimeRef": runtime_ref,
                "materializerRef": self.ref,
                "hostOwned": self._host_owned,
                "secretCopied": False,
            },
            link_type="evidence.credential_materialization",
        )
        return CredentialRuntimeHandle.model_validate(
            {
                "credentialRuntimeRef": runtime_ref,
                "providerProfileRef": context.acquired.provider_profile_ref,
                "providerLeaseRef": context.acquired.provider_lease_ref,
                "credentialGeneration": context.acquired.credential_generation,
                "materializerRef": self.ref,
                "attachments": [],
                "cleanupRef": f"credential-cleanup:sha256:{digest}",
                "attestationRef": attestation_ref,
                "hostOwned": self._host_owned,
            }
        )

    async def attest(self, handle: CredentialRuntimeHandle) -> dict[str, Any]:
        return {"attestationRef": handle.attestationRef, "hostOwned": handle.hostOwned}

    async def cleanup(
        self,
        handle: CredentialRuntimeHandle,
        expected_generation: int,
    ) -> CredentialCleanupResult:
        if handle.credentialGeneration != expected_generation:
            raise HarnessPlatformError(
                "credential cleanup generation is fenced",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED,
            )
        return CredentialCleanupResult(
            cleanupRef=handle.cleanupRef,
            removed=False,
            evidence={"hostOwned": handle.hostOwned, "noRuntimeState": True},
        )


class CredentialMaterializerImplementationRegistry:
    def __init__(
        self, implementations: list[CredentialMaterializerImplementation]
    ) -> None:
        self._implementations = {item.ref: item for item in implementations}
        if len(self._implementations) != len(implementations):
            raise ValueError(
                "credential materializer implementation refs must be unique"
            )

    def require(self, ref: str) -> CredentialMaterializerImplementation:
        implementation = self._implementations.get(ref)
        if implementation is None:
            raise HarnessPlatformError(
                f"credential materializer implementation {ref} is unavailable",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE,
            )
        return implementation


def build_default_credential_materializer_registry(
    *, backend: DockerCommandBackend | None = None
) -> CredentialMaterializerImplementationRegistry:
    return CredentialMaterializerImplementationRegistry(
        [
            DockerOpencodeAuthJsonMaterializer(backend),
            DockerOmnigentProviderConfigMaterializer(backend),
            NoopCredentialMaterializer("none@1"),
            NoopCredentialMaterializer("host-owned-auth@1", host_owned=True),
        ]
    )


class OmnigentCredentialProvisioningService:
    """Resolve allowed roles, materialize, and durably record each handle."""

    def __init__(
        self,
        *,
        session_factory: Any,
        secret_resolution_service: Any,
        registry: CredentialMaterializerImplementationRegistry,
        artifact_gateway: OmnigentArtifactGateway,
    ) -> None:
        self._session_factory = session_factory
        self._secrets = secret_resolution_service
        self._registry = registry
        self._artifacts = artifact_gateway

    async def _persist_handle(
        self,
        handle: CredentialRuntimeHandle,
        *,
        cleanup_state: str,
    ) -> None:
        from api_service.db.models import OmnigentCredentialRuntimeRecord

        attachment = handle.attachments[0] if handle.attachments else None
        immutable_values = {
            "provider_profile_ref": handle.providerProfileRef,
            "provider_lease_ref": handle.providerLeaseRef,
            "credential_generation": handle.credentialGeneration,
            "materializer_ref": handle.materializerRef,
            "target_path": attachment.targetPath if attachment else "",
            "access_mode": attachment.accessMode if attachment else "none",
            "cleanup_ref": handle.cleanupRef,
            "attachments_json": [
                item.model_dump(by_alias=True, mode="json")
                for item in handle.attachments
            ],
        }
        async with self._session_factory() as session:
            existing = await session.get(
                OmnigentCredentialRuntimeRecord, handle.credentialRuntimeRef
            )
            if existing is None:
                session.add(
                    OmnigentCredentialRuntimeRecord(
                        credential_runtime_ref=handle.credentialRuntimeRef,
                        attestation_ref=handle.attestationRef,
                        cleanup_state=cleanup_state,
                        **immutable_values,
                    )
                )
            else:
                if any(
                    getattr(existing, key) != value
                    for key, value in immutable_values.items()
                ):
                    raise HarnessPlatformError(
                        "credential runtime idempotency conflict",
                        code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                    )
                if existing.cleanup_state == "cleaned" and cleanup_state != "cleaned":
                    raise HarnessPlatformError(
                        "cleaned credential runtime authority cannot be reused",
                        code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                    )
                if handle.attestationRef:
                    existing.attestation_ref = handle.attestationRef
                if existing.cleanup_state != "cleaned":
                    existing.cleanup_state = cleanup_state
            await session.commit()

    async def materialize_all(
        self,
        *,
        request: AgentExecutionRequest,
        plan: Any,
        acquired_leases: tuple[AcquiredProviderLease, ...],
        writer_image_ref: str,
    ) -> tuple[CredentialRuntimeHandle, ...]:
        from moonmind.omnigent.harness_platform.materializers import get_materializer

        by_slot = {item.slot: item for item in acquired_leases}
        handles: list[CredentialRuntimeHandle] = []
        materialized: dict[tuple[str, str], CredentialRuntimeHandle] = {}
        try:
            for slot in sorted(plan.payload.credentialBindings):
                binding = plan.payload.credentialBindings[slot]
                acquired = by_slot.get(slot)
                if acquired is None:
                    raise HarnessPlatformError(
                        f"Provider Profile lease for credential slot {slot} is missing",
                        code=HarnessPlatformFailure.OMNIGENT_PROVIDER_LEASE_UNAVAILABLE,
                    )
                cache_key = (acquired.provider_lease_ref, binding["materializerRef"])
                handle = materialized.get(cache_key)
                if handle is None:
                    anticipated = anticipated_credential_handle(
                        acquired, binding["materializerRef"]
                    )
                    await self._persist_handle(
                        anticipated, cleanup_state="materializing"
                    )
                    handle_index = len(handles)
                    handles.append(anticipated)
                    descriptor = get_materializer(binding["materializerRef"])
                    secrets = await self._secrets.resolve(
                        acquired=acquired,
                        allowed_secret_roles=descriptor.requiredSecretRoles,
                    )
                    implementation = self._registry.require(binding["materializerRef"])
                    try:
                        handle = await implementation.materialize(
                            CredentialMaterializationContext(
                                request=request,
                                acquired=acquired,
                                secrets=secrets,
                                writer_image_ref=writer_image_ref,
                                artifact_gateway=self._artifacts,
                                model_qualified_id=plan.payload.modelConfig.qualifiedId,
                                provider_route_ref=plan.payload.modelConfig.routeRef,
                            )
                        )
                    finally:
                        secrets.clear()
                    if (
                        handle.credentialRuntimeRef != anticipated.credentialRuntimeRef
                        or handle.cleanupRef != anticipated.cleanupRef
                        or handle.attachments != anticipated.attachments
                    ):
                        raise HarnessPlatformError(
                            "credential materializer changed its anticipated cleanup authority",
                            code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                        )
                    handles[handle_index] = handle
                    await self._persist_handle(handle, cleanup_state="active")
                    materialized[cache_key] = handle
        except BaseException:
            if handles:
                await self.cleanup_all(handles)
            raise
        return tuple(handles)

    async def load_cleanup_handles(
        self,
        provider_leases: dict[str, dict[str, Any]],
        committed_handles: dict[str, dict[str, Any]],
    ) -> tuple[CredentialRuntimeHandle, ...]:
        """Rehydrate cleanup authority without resolving a SecretRef."""

        from api_service.db.models import OmnigentCredentialRuntimeRecord

        handles: dict[str, CredentialRuntimeHandle] = {}
        for value in committed_handles.values():
            handle = CredentialRuntimeHandle.model_validate(value)
            handles[handle.credentialRuntimeRef] = handle
        runtime_refs = (
            {
                str(value.get("credentialRuntimeRef") or "").strip()
                for value in provider_leases.values()
            }
            - {"", "pending"}
            - set(handles)
        )
        if runtime_refs:
            async with self._session_factory() as session:
                from sqlalchemy import select

                rows = (
                    (
                        await session.execute(
                            select(OmnigentCredentialRuntimeRecord).where(
                                OmnigentCredentialRuntimeRecord.credential_runtime_ref.in_(
                                    tuple(runtime_refs)
                                )
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            for row in rows:
                runtime_environment = (
                    {"OMNIGENT_CONFIG_HOME": "/home/app/.moonmind-provider-config"}
                    if row.materializer_ref == "omnigent-provider-config@1"
                    else {}
                )
                handle = CredentialRuntimeHandle.model_validate(
                    {
                        "credentialRuntimeRef": row.credential_runtime_ref,
                        "providerProfileRef": row.provider_profile_ref,
                        "providerLeaseRef": row.provider_lease_ref,
                        "credentialGeneration": row.credential_generation,
                        "materializerRef": row.materializer_ref,
                        "attachments": list(row.attachments_json or []),
                        "runtimeEnvironment": runtime_environment,
                        "cleanupRef": row.cleanup_ref,
                        "attestationRef": row.attestation_ref,
                        "hostOwned": row.materializer_ref == "host-owned-auth@1",
                    }
                )
                handles[handle.credentialRuntimeRef] = handle
        return tuple(handles[key] for key in sorted(handles))

    async def cleanup_all(
        self,
        handles: tuple[CredentialRuntimeHandle, ...] | list[CredentialRuntimeHandle],
    ) -> tuple[CredentialCleanupResult, ...]:
        results: list[CredentialCleanupResult] = []
        for handle in reversed(tuple(handles)):
            result = await self._registry.require(handle.materializerRef).cleanup(
                handle, handle.credentialGeneration
            )
            async with self._session_factory() as session:
                from api_service.db.models import OmnigentCredentialRuntimeRecord

                row = await session.get(
                    OmnigentCredentialRuntimeRecord, handle.credentialRuntimeRef
                )
                if row is not None:
                    row.cleanup_state = "cleaned" if not result.deferred else "deferred"
                    row.cleanup_evidence_json = result.model_dump(
                        by_alias=True, mode="json"
                    )
                    await session.commit()
            results.append(result)
        return tuple(results)


__all__ = [
    "CredentialCleanupResult",
    "CredentialMaterializationContext",
    "CredentialMaterializerImplementation",
    "CredentialMaterializerImplementationRegistry",
    "CredentialRuntimeHandle",
    "DockerOpencodeAuthJsonMaterializer",
    "DockerOmnigentProviderConfigMaterializer",
    "LocalDockerCommandBackend",
    "NoopCredentialMaterializer",
    "OmnigentCredentialProvisioningService",
    "anticipated_credential_handle",
    "build_default_credential_materializer_registry",
    "credential_runtime_identity",
]
