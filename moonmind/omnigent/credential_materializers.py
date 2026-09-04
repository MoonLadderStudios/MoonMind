"""Side-effecting credential materializer implementations.

Descriptor compatibility remains in ``harness_platform.materializers``.  This
module owns secret resolution-adjacent runtime mutations and returns only
secret-free handles.
"""

from __future__ import annotations

import hashlib
import json
import re
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
class ProfileCredentialHome:
    """One enrollment-owned durable credential home (profile-owned state).

    ``volume_ref`` names the Docker volume the OAuth enrollment populates and
    owns; ``target_path`` is the exact in-image credential-home path the
    materializer is allowed to attach it at.
    """

    volume_ref: str
    target_path: str


@dataclass(frozen=True)
class CredentialMaterializationContext:
    request: AgentExecutionRequest
    acquired: AcquiredProviderLease
    secrets: ScopedSecretBundle
    writer_image_ref: str
    artifact_gateway: OmnigentArtifactGateway
    model_qualified_id: str = ""
    provider_route_ref: str = ""
    profile_credential_home: ProfileCredentialHome | None = None


class DockerCommandBackend(Protocol):
    async def run(
        self,
        argv: list[str],
        *,
        input_bytes: bytes | None = None,
        timeout_seconds: float = 60.0,
    ) -> tuple[int, bytes, bytes]:
        raise NotImplementedError


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


class _DockerMaterializerBackendMixin:
    """Shared bounded Docker command helpers for credential materializers."""

    ref: str

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

    async def _resolve_writer_ref(self, ref: str) -> str:
        # Fail closed on digest-pinned writers: the selected Host Class image
        # is immutable launch authority, and a mutable-tag fallback would grant
        # a different image read-write access to persistent OAuth credentials.
        # Return the exact ref so a missing local image fails with an actionable
        # Docker error instead of silently substituting another image.
        code, _, _ = await self._backend.run(
            ["docker", "image", "inspect", ref, "--format", "{{.Id}}"]
        )
        if code == 0:
            return ref
        if "@sha256:" in ref:
            raise HarnessPlatformError(
                f"digest-pinned writer image is not present locally: {ref}; "
                "pull the selected Host Class image instead of substituting "
                "a mutable tag",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
            )
        return ref

    async def _volume_presence(self, volume: str, *, failure: str) -> bool:
        """Return whether a volume exists, without echoing its name.

        ``run_runtime_command`` redacts credential-shaped output, and a
        credential volume name caught by that redactor would corrupt
        comparisons. Ask Docker's trusted template evaluator for one bounded
        token instead.
        """

        code, stdout, _stderr = await self._backend.run(
            [
                "docker",
                "volume",
                "ls",
                "--filter",
                f"name=^{volume}$",
                "--format",
                f"{{{{if eq .Name {json.dumps(volume)}}}}}present{{{{end}}}}",
            ]
        )
        if code != 0:
            raise HarnessPlatformError(
                failure,
                code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
            )
        return bool(stdout.decode("utf-8", errors="replace").strip())


class CredentialMaterializerImplementation(Protocol):
    ref: str

    async def materialize(
        self, context: CredentialMaterializationContext
    ) -> CredentialRuntimeHandle:
        raise NotImplementedError

    async def attest(self, handle: CredentialRuntimeHandle) -> dict[str, Any]:
        raise NotImplementedError

    async def cleanup(
        self,
        handle: CredentialRuntimeHandle,
        expected_generation: int,
    ) -> CredentialCleanupResult:
        raise NotImplementedError


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


#: Exact in-image credential-home targets for profile-owned OAuth materializers.
#: Kept in one place so the descriptor, anticipated handle, and launched mount
#: cannot drift apart.
OAUTH_HOME_MATERIALIZER_TARGETS = {
    "codex-oauth-home@1": "/home/app/.codex",
    "claude-oauth-home@1": "/home/app/.claude",
}

_OAUTH_HOME_MATERIALIZER_TARGETS = OAUTH_HOME_MATERIALIZER_TARGETS


# Safe Docker volume name (enrollment-owned credential homes reuse the same
# contract as Compose named volumes).
_CREDENTIAL_VOLUME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def anticipated_credential_handle(
    acquired: AcquiredProviderLease,
    materializer_ref: str,
    *,
    profile_credential_home: ProfileCredentialHome | None = None,
) -> CredentialRuntimeHandle:
    runtime_ref, digest = credential_runtime_identity(acquired, materializer_ref)
    attachments: list[dict[str, Any]] = []
    runtime_environment: dict[str, str] = {}
    host_owned = materializer_ref == "host-owned-auth@1"
    if materializer_ref in _OAUTH_HOME_MATERIALIZER_TARGETS:
        if profile_credential_home is None:
            raise HarnessPlatformError(
                f"materializer {materializer_ref} requires an enrollment-owned "
                "credential home on the Provider Profile",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE,
            )
        target = _OAUTH_HOME_MATERIALIZER_TARGETS[materializer_ref]
        if profile_credential_home.target_path.rstrip("/") != target.rstrip("/"):
            raise HarnessPlatformError(
                "credential home target does not match the materializer layout",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE,
            )
        if not _CREDENTIAL_VOLUME_RE.fullmatch(profile_credential_home.volume_ref):
            raise HarnessPlatformError(
                "enrollment-owned credential home volume name is invalid",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE,
            )
        # Profile-owned OAuth homes are attached read-write so vendor token
        # refresh persists in the enrollment-owned volume; the volume survives
        # run cleanup (detach-only cleanup).
        attachments.append(
            {
                "kind": "volume",
                "sourceRef": profile_credential_home.volume_ref,
                "targetPath": target,
                "accessMode": "read-write",
            }
        )
    elif materializer_ref == "opencode-auth-json@1":
        attachments.append(
            {
                "kind": "volume",
                "sourceRef": f"mm-omnigent-credential-{digest[:32]}",
                "targetPath": "/run/mm-credentials/opencode",
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


class DockerOpencodeAuthJsonMaterializer(_DockerMaterializerBackendMixin):
    ref = "opencode-auth-json@1"

    async def materialize(
        self, context: CredentialMaterializationContext
    ) -> CredentialRuntimeHandle:
        acquired = context.acquired
        anticipated = anticipated_credential_handle(acquired, self.ref)
        runtime_ref, digest = credential_runtime_identity(acquired, self.ref)
        volume_name = f"mm-omnigent-credential-{digest[:32]}"
        provider_route_ref = str(context.provider_route_ref or "").strip()
        payload = build_opencode_auth_json_bytes(
            api_key=context.secrets.require("opencode_api_key"),
            provider_key=provider_route_ref,
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
        writer_ref = await self._resolve_writer_ref(context.writer_image_ref)
        try:
            await self._run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-i",
                    # Credential writers must own the mounted volume's initial
                    # permissions.  The selected host image intentionally runs
                    # workloads as UID 1000, so make this narrow setup process
                    # explicitly root instead of inheriting the host default.
                    "--user",
                    "0:0",
                    "--network",
                    "none",
                    "--read-only",
                    "--mount",
                    f"type=volume,src={volume_name},dst=/credential",
                    "--entrypoint",
                    "/bin/sh",
                    writer_ref,
                    "-ceu",
                    writer_script,
                    "--",
                    str(acquired.credential_generation),
                ],
                input_bytes=payload,
                failure="credential writer failed",
            )
            verify_script = "".join(
                (
                    "set -eu; ",
                    'test "$(stat -c %u:%g /credential)" = 1000:1000; ',
                    'test "$(stat -c %a /credential)" = 700; ',
                    'test "$(stat -c %u:%g /credential/auth.json)" = 1000:1000; ',
                    'test "$(stat -c %a /credential/auth.json)" = 600; ',
                    'test "$(cat /credential/.moonmind-generation)" = "$1"; ',
                    'python3 -c \'import json,sys; d=json.load(open("/credential/auth.json")); '
                    'assert list(d)==[sys.argv[1]]; v=d[sys.argv[1]]; '
                    'assert v.get("type")=="api" and isinstance(v.get("key"),str) and v.get("key")\' "$2"',
                )
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
                    writer_ref,
                    "-ceu",
                    verify_script,
                    "--",
                    str(acquired.credential_generation),
                    provider_route_ref,
                ],
                failure="credential volume attestation failed",
            )
        except BaseException:
            await self._backend.run(["docker", "volume", "rm", volume_name])
            raise
        finally:
            # Drop Python references immediately after the writer consumes stdin.
            del payload
            context.secrets.clear()
        evidence = {
            "schemaVersion": "moonmind.credential-materialization-attestation.v1",
            "credentialRuntimeRef": runtime_ref,
            "providerProfileRef": acquired.provider_profile_ref,
            "providerLeaseRef": acquired.provider_lease_ref,
            "credentialGeneration": acquired.credential_generation,
            "materializerRef": self.ref,
            "providerRouteRef": provider_route_ref,
            "attachment": {
                "kind": "volume",
                "sourceRef": volume_name,
                "targetPath": "/run/mm-credentials/opencode",
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
        # ``run_runtime_command`` redacts secret-shaped command output before
        # returning it to callers.  The credential runtime label and volume name
        # are non-secret authority refs, but both are intentionally caught by
        # that generic redactor.  Returning either raw value (including in an
        # inspect error) therefore creates false generation fences and prevents
        # an already-absent volume from being recognized as idempotent success.
        # Ask Docker's trusted template evaluator for one bounded attestation:
        # ``owned`` for the exact generation, ``mismatch`` for a conflicting
        # owner, and empty output when the exact volume is absent.
        ownership_attestation = (
            f"{{{{if eq .Name {json.dumps(volume)}}}}}"
            "{{if and "
            '(eq (.Label "moonmind.credential_runtime_ref") '
            f"{json.dumps(handle.credentialRuntimeRef)}) "
            '(eq (.Label "moonmind.credential_generation") '
            f"{json.dumps(str(expected_generation))})"
            "}}owned{{else}}mismatch{{end}}{{end}}"
        )
        code, stdout, _stderr = await self._backend.run(
            [
                "docker",
                "volume",
                "ls",
                "--filter",
                f"name=^{volume}$",
                "--format",
                ownership_attestation,
            ]
        )
        if code != 0:
            raise HarnessPlatformError(
                "credential volume cleanup attestation is deferred",
                code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
            )
        observed = stdout.decode("utf-8", errors="replace").strip()
        if not observed:
            return CredentialCleanupResult(
                cleanupRef=handle.cleanupRef,
                removed=True,
                evidence={"alreadyAbsent": True},
            )
        if observed != "owned":
            raise HarnessPlatformError(
                "credential volume ownership or generation fence mismatch",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED,
            )
        code, _stdout, _stderr = await self._backend.run(
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
            verify_config_script = "".join(
                (
                    'set -eu; test "$(stat -c %u:%g /credential)" = 1000:1000; ',
                    'test "$(stat -c %a /credential)" = 700; ',
                    'test "$(stat -c %a /credential/config.yaml)" = 600; ',
                    'test "$(cat /credential/.moonmind-generation)" = "$1"; ',
                    'python3 -c \'import json; d=json.load(open("/credential/config.yaml")); ',
                    'assert list(d)==["providers"]; assert list(d["providers"]); ',
                    'assert d["providers"]["moonmind"]["kind"]=="key"\'',
                )
            )
            await self._run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-i",
                    # See the OpenCode materializer above: only the isolated,
                    # networkless credential writer runs as root.  The volume
                    # it produces remains owned by the workload's UID 1000.
                    "--user",
                    "0:0",
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
                    verify_config_script,
                    "--",
                    str(acquired.credential_generation),
                ],
                failure="provider config volume attestation failed",
            )
        except BaseException:
            await self._backend.run(["docker", "volume", "rm", volume_name])
            raise
        finally:
            del payload
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


class DockerOauthHomeMaterializer(_DockerMaterializerBackendMixin):
    """Attach one enrollment-owned OAuth credential home (profile-owned).

    The OAuth enrollment (MoonMind Settings) populates and owns a durable
    Docker volume; the materializer never copies or reads credential contents.
    It attaches that volume read-write at the runtime pack's credential-home
    target so vendor token refresh persists, fences the home with a generation
    marker, and detaches without deleting on cleanup (#3829 profile-owned
    semantics). Stale or newer generations are rejected; a wrong-layout target
    is rejected before any mount.
    """

    target_path: str = ""

    async def materialize(
        self, context: CredentialMaterializationContext
    ) -> CredentialRuntimeHandle:
        acquired = context.acquired
        home = context.profile_credential_home
        if home is None or home.volume_ref == "" or home.target_path == "":
            context.secrets.clear()
            raise HarnessPlatformError(
                f"materializer {self.ref} requires an enrollment-owned credential home",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE,
            )
        expected_target = OAUTH_HOME_MATERIALIZER_TARGETS[self.ref]
        if home.target_path.rstrip("/") != expected_target.rstrip("/"):
            context.secrets.clear()
            raise HarnessPlatformError(
                f"credential home target {home.target_path!r} does not match "
                f"the {self.ref} layout",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE,
            )
        if not _CREDENTIAL_VOLUME_RE.fullmatch(home.volume_ref):
            context.secrets.clear()
            raise HarnessPlatformError(
                "enrollment-owned credential home volume name is invalid",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE,
            )
        volume = home.volume_ref
        runtime_ref, digest = credential_runtime_identity(acquired, self.ref)
        if not await self._volume_presence(
            volume, failure="enrollment-owned credential home is unavailable"
        ):
            context.secrets.clear()
            raise HarnessPlatformError(
                "enrollment-owned credential home volume is missing",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
            )
        context.secrets.clear()
        # Stage the generation marker inside the profile-owned home. Re-staging
        # is idempotent for the same generation and rejects a newer (rotated)
        # generation so a stale lease can never fence a replacement home.
        # Normalize the enrollment-owned layout to the exact-host contract
        # (root 0700, top-level files 0600, owned 1000:1000) so helper-synced
        # or imported volumes pass attestation without manual repair.
        stage_script = (
            "set -eu; "
            "chown 1000:1000 /credential; "
            "chmod 0700 /credential; "
            "if [ -f /credential/.moonmind-generation ]; then "
            "current=$(cat /credential/.moonmind-generation); "
            'if [ "$current" -gt "$1" ]; then '
            "echo 'credential home generation is newer than the acquired lease' >&2; "
            "exit 79; fi; fi; "
            'for f in /credential/*; do [ -e "$f" ] || continue; '
            '[ -f "$f" ] || continue; '
            'chown 1000:1000 "$f"; chmod 0600 "$f"; done; '
            "printf '%s\\n' \"$1\" > /credential/.moonmind-generation.tmp; "
            "chown 1000:1000 /credential/.moonmind-generation.tmp; "
            "chmod 0600 /credential/.moonmind-generation.tmp; "
            "mv /credential/.moonmind-generation.tmp /credential/.moonmind-generation"
        )
        await self._run(
            [
                "docker",
                "run",
                "--rm",
                "-i",
                "--user",
                "0:0",
                "--network",
                "none",
                "--read-only",
                "--mount",
                f"type=volume,src={volume},dst=/credential",
                "--entrypoint",
                "/bin/sh",
                await self._resolve_writer_ref(context.writer_image_ref),
                "-ceu",
                stage_script,
                "--",
                str(acquired.credential_generation),
            ],
            failure="credential home generation staging failed",
        )
        attachment = {
            "kind": "volume",
            "sourceRef": volume,
            "targetPath": expected_target,
            "accessMode": "read-write",
        }
        evidence = {
            "schemaVersion": "moonmind.credential-materialization-attestation.v1",
            "credentialRuntimeRef": runtime_ref,
            "providerProfileRef": acquired.provider_profile_ref,
            "providerLeaseRef": acquired.provider_lease_ref,
            "credentialGeneration": acquired.credential_generation,
            "materializerRef": self.ref,
            "ownership": "profile",
            "attachment": attachment,
            "owner": "1000:1000",
            "directoryMode": "0700",
            "fileMode": "0600",
            "secretCopied": False,
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
            raise
        return CredentialRuntimeHandle.model_validate(
            {
                "credentialRuntimeRef": runtime_ref,
                "providerProfileRef": acquired.provider_profile_ref,
                "providerLeaseRef": acquired.provider_lease_ref,
                "credentialGeneration": acquired.credential_generation,
                "materializerRef": self.ref,
                "attachments": [attachment],
                "cleanupRef": f"credential-cleanup:sha256:{digest}",
                "attestationRef": attestation_ref,
            }
        )

    async def attest(self, handle: CredentialRuntimeHandle) -> dict[str, Any]:
        if not handle.attachments:
            raise HarnessPlatformError(
                f"{self.ref} runtime has no credential home attachment",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
            )
        return {
            "credentialRuntimeRef": handle.credentialRuntimeRef,
            "credentialGeneration": handle.credentialGeneration,
            "ownership": "profile",
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
        present = await self._volume_presence(
            volume, failure="credential home cleanup attestation is deferred"
        )
        if not present:
            # Detach-only cleanup is idempotent: an already-detached home
            # (for example after the enrollment was disconnected) is success.
            return CredentialCleanupResult(
                cleanupRef=handle.cleanupRef,
                removed=False,
                evidence={"profileOwned": True, "alreadyAbsent": True},
            )
        # Profile-owned state is preserved: never delete the enrollment volume,
        # never wipe credential contents. The mount dies with the run's host.
        return CredentialCleanupResult(
            cleanupRef=handle.cleanupRef,
            removed=False,
            evidence={
                "profileOwned": True,
                "preserved": True,
                "state": "detached",
            },
        )


class DockerCodexOauthHomeMaterializer(DockerOauthHomeMaterializer):
    ref = "codex-oauth-home@1"
    target_path = OAUTH_HOME_MATERIALIZER_TARGETS["codex-oauth-home@1"]


class DockerClaudeOauthHomeMaterializer(DockerOauthHomeMaterializer):
    ref = "claude-oauth-home@1"
    target_path = OAUTH_HOME_MATERIALIZER_TARGETS["claude-oauth-home@1"]


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
            DockerCodexOauthHomeMaterializer(backend),
            DockerClaudeOauthHomeMaterializer(backend),
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

    async def _load_profile_credential_home(
        self, acquired: AcquiredProviderLease
    ) -> ProfileCredentialHome:
        """Resolve the enrollment-owned credential home for one lease.

        A profile-owned materializer never resolves a secret: the durable
        enrollment volume *is* the credential state. The lease's generation is
        re-fenced against the profile row so a rotated enrollment cannot be
        attached under a stale lease.
        """

        from api_service.db.models import ManagedAgentProviderProfile

        async with self._session_factory() as session:
            profile = await session.get(
                ManagedAgentProviderProfile, acquired.provider_profile_ref
            )
        if profile is None:
            raise HarnessPlatformError(
                "Provider Profile disappeared after lease acquisition",
                code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
            )
        if int(profile.credential_generation or 0) != acquired.credential_generation:
            raise HarnessPlatformError(
                "Provider Profile credential generation changed after lease acquisition",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED,
            )
        volume_ref = str(profile.volume_ref or "").strip()
        if not volume_ref:
            raise HarnessPlatformError(
                "profile-owned OAuth materializer requires an enrolled "
                "credential home volume on the Provider Profile",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE,
            )
        target_path = str(profile.volume_mount_path or "").strip()
        if not target_path:
            raise HarnessPlatformError(
                "profile-owned OAuth materializer requires the enrolled "
                "credential home mount path on the Provider Profile",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE,
            )
        return ProfileCredentialHome(volume_ref=volume_ref, target_path=target_path)

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
                cache_key = (acquired.provider_lease_ref, binding.materializerRef)
                handle = materialized.get(cache_key)
                if handle is None:
                    descriptor = get_materializer(binding.materializerRef)
                    profile_credential_home = None
                    if descriptor.state.get("scope") == "profile":
                        profile_credential_home = (
                            await self._load_profile_credential_home(acquired)
                        )
                    anticipated = anticipated_credential_handle(
                        acquired,
                        binding.materializerRef,
                        profile_credential_home=profile_credential_home,
                    )
                    await self._persist_handle(
                        anticipated, cleanup_state="materializing"
                    )
                    handle_index = len(handles)
                    handles.append(anticipated)
                    secrets = await self._secrets.resolve(
                        acquired=acquired,
                        allowed_secret_roles=descriptor.requiredSecretRoles,
                    )
                    implementation = self._registry.require(binding.materializerRef)
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
                                profile_credential_home=profile_credential_home,
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
    "OAUTH_HOME_MATERIALIZER_TARGETS",
    "CredentialCleanupResult",
    "CredentialMaterializationContext",
    "CredentialMaterializerImplementation",
    "CredentialMaterializerImplementationRegistry",
    "CredentialRuntimeHandle",
    "DockerClaudeOauthHomeMaterializer",
    "DockerCodexOauthHomeMaterializer",
    "DockerOauthHomeMaterializer",
    "DockerOmnigentProviderConfigMaterializer",
    "DockerOpencodeAuthJsonMaterializer",
    "LocalDockerCommandBackend",
    "NoopCredentialMaterializer",
    "OmnigentCredentialProvisioningService",
    "ProfileCredentialHome",
    "anticipated_credential_handle",
    "build_default_credential_materializer_registry",
    "credential_runtime_identity",
]
