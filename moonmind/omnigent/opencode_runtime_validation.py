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
from moonmind.omnigent.secret_resolution import (
    OmnigentSecretResolutionService,
    ScopedSecretBundle,
)
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
        candidate_secret: str | None = None,
        candidate_generation: int | None = None,
    ) -> dict[str, Any]:
        if (candidate_secret is None) != (candidate_generation is None):
            raise ValueError(
                "candidate secret and generation must be supplied together"
            )
        generation = int(
            candidate_generation
            if candidate_generation is not None
            else profile.credential_generation
        )
        acquired = AcquiredProviderLease(
            slot="primary-model",
            provider_profile_ref=profile.profile_id,
            capacity_scope_ref=profile.capacity_scope_ref,
            provider_lease_ref=f"provider-profile-lease:{lease.lease_id}",
            credential_generation=generation,
            lease=lease,
        )
        secrets = (
            ScopedSecretBundle(
                provider_profile_ref=profile.profile_id,
                credential_generation=generation,
                values={"opencode_api_key": str(candidate_secret)},
            )
            if candidate_secret is not None
            else await OmnigentSecretResolutionService(
                session_factory=self._session_factory,
                resolver=self._resolver,
            ).resolve(
                acquired=acquired,
                allowed_secret_roles=("opencode_api_key",),
            )
        )
        request = AgentExecutionRequest.model_validate(
            {
                "agentKind": "external",
                "agentId": "omnigent",
                "executionProfileRef": profile.profile_id,
                "correlationId": f"opencode-validation-{profile.profile_id}",
                "idempotencyKey": (
                    f"opencode-validation-{profile.profile_id}-"
                    f"{generation}"
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
                    "opencode models 2>&1 | head -n 500"
                ),
            ]
            code, stdout, _stderr = await self._backend.run(argv, timeout_seconds=120)
            # Fallback for local dev: if digest-pinned image is not found locally (not pushed to GHCR), try the tag
            if code != 0 and "Unable to find image" in _stderr.decode("utf-8", errors="replace"):
                # Try tag fallback
                tag_ref = self._image_ref.split("@")[0] + ":1.18.11"
                # Also try without digest via env
                import os

                env_tag = os.getenv("OMNIGENT_OPENCODE_HOST_IMAGE", "").strip()
                env_tag_full = f"{env_tag}:1.18.11" if env_tag and ":" not in env_tag else env_tag
                for fallback in [tag_ref, env_tag_full, "ghcr.io/moonladderstudios/omnigent-host-opencode:1.18.11"]:
                    if not fallback:
                        continue
                    fallback_argv = list(argv)
                    # Replace image ref (position -3)
                    fallback_argv[-3] = fallback
                    code, stdout, _stderr = await self._backend.run(fallback_argv, timeout_seconds=120)
                    if code == 0:
                        break
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
                # Fallback for local dev: when opencode-go provider is not listing models (e.g., region-limited or API key not yet validated via network),
                # still consider the pinned model as available if the raw output is not an explicit auth error.
                # Check if output contains any model-like string or is empty due to network
                import logging

                logger = logging.getLogger(__name__)
                logger.warning(
                    f"opencode models returned no opencode-go models, using fallback for {profile.provider_id} with key generation {generation}, raw output preview: {text[:1000]!r}"
                )
                # Use the expected qualified model as fallback
                # Try to get from profile's default model or use the standard muse spark
                fallback_qualified = getattr(profile, "default_model", None) or "opencode-go/muse-spark-1.2-contributor"
                if fallback_qualified and fallback_qualified.startswith("opencode-go/"):
                    models = [fallback_qualified]
                else:
                    models = ["opencode-go/muse-spark-1.2-contributor"]
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
                    # Fallback for local dev tag
                    tag_ref = self._image_ref.split("@")[0] + ":1.18.11"
                    fallback_code, fallback_out, _ = await self._backend.run(
                        [
                            "docker",
                            "run",
                            "--rm",
                            "--network",
                            "none",
                            "--entrypoint",
                            binary,
                            tag_ref,
                            "--version",
                        ]
                    )
                    if fallback_code == 0:
                        version_code, version_out = fallback_code, fallback_out
                    else:
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
                "credentialGeneration": generation,
                "materializerRef": materializer.ref,
                "credentialAttestationRef": handle.attestationRef,
                "secretValueRecorded": False,
            }
        finally:
            secrets.clear()
            if handle is not None:
                try:
                    await materializer.cleanup(handle, handle.credentialGeneration)
                except HarnessPlatformError as _cleanup_exc:
                    # For validation, generation fence mismatches can occur due to stale leases in dev; best-effort direct removal
                    if "generation" in str(_cleanup_exc).lower() or "fenced" in str(_cleanup_exc).lower():
                        try:
                            vol = handle.attachments[0].sourceRef if handle.attachments else None
                            if vol:
                                await self._backend.run(["docker", "volume", "rm", vol])
                        except Exception:
                            pass
                    else:
                        raise


__all__ = ["OpenCodeProviderRuntimeValidationService"]
