"""Pinned-runtime validation for an enrolled OpenCode Provider Profile."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from moonmind.omnigent.bridge_artifacts import (
    OmnigentArtifactGateway,
    TemporalOmnigentArtifactGateway,
)
from moonmind.omnigent.credential_materializers import (
    CredentialMaterializationContext,
    DockerOpencodeAuthJsonMaterializer,
    LocalDockerCommandBackend,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.provider_leases import AcquiredProviderLease
from moonmind.omnigent.secret_resolution import OmnigentSecretResolutionService
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.security.egress import OMNIGENT_EGRESS_NETWORK_REF


def _models(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str):
        for line in value.splitlines():
            item = line.strip().strip("\"',")
            if item.startswith("opencode-go/"):
                found.add(item)
    elif isinstance(value, dict):
        for item in value.values():
            found.update(_models(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_models(item))
    return found


class OpenCodeProviderRuntimeValidationService:
    def __init__(
        self,
        *,
        session_factory: Any,
        resolver: Any,
        image_ref: str,
        backend: LocalDockerCommandBackend | None = None,
        artifact_gateway: OmnigentArtifactGateway | None = None,
    ) -> None:
        if "@sha256:" not in image_ref:
            raise HarnessPlatformError(
                "OpenCode validation image must be digest-pinned",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
            )
        self._session_factory = session_factory
        self._resolver = resolver
        self._image_ref = image_ref
        self._backend = backend or LocalDockerCommandBackend()
        self._artifacts = artifact_gateway or TemporalOmnigentArtifactGateway(
            session_factory
        )

    async def validate(
        self,
        *,
        profile: Any,
        lease: Any,
    ) -> dict[str, Any]:
        acquired = AcquiredProviderLease(
            slot="primary-model",
            provider_profile_ref=profile.profile_id,
            capacity_scope_ref=profile.capacity_scope_ref,
            provider_lease_ref=f"provider-profile-lease:{lease.lease_id}",
            credential_generation=int(profile.credential_generation),
            lease=lease,
        )
        secrets = await OmnigentSecretResolutionService(
            session_factory=self._session_factory,
            resolver=self._resolver,
        ).resolve(
            acquired=acquired,
            allowed_secret_roles=("opencode_api_key",),
        )
        request = AgentExecutionRequest.model_validate(
            {
                "agentKind": "external",
                "agentId": "omnigent",
                "executionProfileRef": profile.profile_id,
                "correlationId": f"opencode-validation-{profile.profile_id}",
                "idempotencyKey": (
                    f"opencode-validation-{profile.profile_id}-"
                    f"{profile.credential_generation}"
                ),
            }
        )
        materializer = DockerOpencodeAuthJsonMaterializer(self._backend)
        handle = None
        try:
            handle = await materializer.materialize(
                CredentialMaterializationContext(
                    request=request,
                    acquired=acquired,
                    secrets=secrets,
                    writer_image_ref=self._image_ref,
                    artifact_gateway=self._artifacts,
                )
            )
            attachment = handle.attachments[0]
            argv = [
                "docker",
                "run",
                "--rm",
                "--network",
                OMNIGENT_EGRESS_NETWORK_REF,
                "--read-only",
                "--user",
                "1000:1000",
                "--mount",
                (
                    f"type=volume,src={attachment.sourceRef},"
                    f"dst={attachment.targetPath},readonly"
                ),
                "--entrypoint",
                "/bin/sh",
                self._image_ref,
                "-ceu",
                (
                    "unset OPENAI_API_KEY ANTHROPIC_API_KEY OPENCODE_AUTH_CONTENT "
                    "OPENCODE_CONFIG OPENCODE_CONFIG_CONTENT; "
                    "opencode models opencode-go"
                ),
            ]
            code, stdout, _stderr = await self._backend.run(argv, timeout_seconds=120)
            if code != 0:
                raise HarnessPlatformError(
                    "pinned OpenCode runtime rejected the Provider Profile",
                    code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
                )
            text = stdout.decode("utf-8", errors="replace")
            try:
                parsed: Any = json.loads(text)
            except json.JSONDecodeError:
                parsed = text
            models = sorted(_models(parsed))
            if not models:
                raise HarnessPlatformError(
                    "pinned OpenCode runtime returned no opencode-go models",
                    code=HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE,
                )
            versions: dict[str, str] = {}
            for binary in ("opencode", "omnigent"):
                version_code, version_out, _version_err = await self._backend.run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--entrypoint",
                        binary,
                        self._image_ref,
                        "--version",
                    ]
                )
                if version_code != 0:
                    raise HarnessPlatformError(
                        f"pinned image does not provide {binary}",
                        code=HarnessPlatformFailure.OMNIGENT_VENDOR_RUNTIME_MISMATCH,
                    )
                versions[binary] = version_out.decode(
                    "utf-8", errors="replace"
                ).strip()[:128]
            return {
                "schemaVersion": "moonmind.provider-model-catalog-evidence.v1",
                "models": [{"qualifiedId": item} for item in models],
                "imageRef": self._image_ref,
                "runtimeVersions": versions,
                "validatedAt": datetime.now(UTC).isoformat(),
                "credentialGeneration": profile.credential_generation,
                "materializerRef": materializer.ref,
                "credentialAttestationRef": handle.attestationRef,
                "secretValueRecorded": False,
            }
        finally:
            secrets.clear()
            if handle is not None:
                await materializer.cleanup(handle, handle.credentialGeneration)


__all__ = ["OpenCodeProviderRuntimeValidationService"]
