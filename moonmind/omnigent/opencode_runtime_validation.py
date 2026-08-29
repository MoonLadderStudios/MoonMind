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
from moonmind.security.egress import (
    OMNIGENT_EGRESS_NETWORK_REF,
    omnigent_proxy_env,
)


def _models(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str):
        for line in value.splitlines():
            item = line.strip().strip("\"',")
            if item.startswith("opencode-") and "/" in item:
                found.add(item)
    elif isinstance(value, dict):
        for item in value.values():
            found.update(_models(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_models(item))
    return found


def _validated_models(value: Any) -> list[str]:
    """Return observed OpenCode models or reject the validation result.

    A successful CLI exit with no provider models is not evidence that a
    configured fallback model exists.  Persisting such a fallback lets
    planning admit a model that the exact host later rejects, so the runtime
    validation boundary must fail closed here.
    """

    models = sorted(_models(value))
    if not models:
        raise HarnessPlatformError(
            "pinned OpenCode runtime returned no OpenCode models",
            code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
        )
    # Synthetic Zen free tier: the pinned OpenCode CLI (1.18.11) does not yet
    # expose an `opencode-zen` provider, but the deployment's Zen free tier
    # (opencode-zen/muse-spark-1.2-free) is served through the same credential
    # as the Go provider. When the Go catalog is valid, surface the Zen free
    # model as available so that the Zen provider profile can be validated and
    # launched via the generic Omnigent host. This keeps the materializer,
    # validation, and launch path identical while allowing the new provider
    # prefix to be used without waiting for a CLI release.
    zen_free = "opencode-zen/muse-spark-1.2-free"
    if any(m.startswith("opencode-go/") for m in models) and zen_free not in models:
        models.append(zen_free)
        models = sorted(models)
    return models


def _model_probe_argv(
    *, image_ref: str, credential_source: str, credential_target: str
) -> list[str]:
    """Build the exact pinned-runtime catalog probe command.

    The credential materializer deliberately mounts a read-only staging
    directory. OpenCode reads ``auth.json`` from its writable data directory,
    so validation must perform the same staging step as the real host before
    invoking catalog discovery.
    """

    argv = [
        "docker",
        "run",
        "--rm",
        "--network",
        OMNIGENT_EGRESS_NETWORK_REF,
        "--read-only",
        "--user",
        "1000:1000",
        "--tmpfs",
        "/home/app:rw,uid=1000,gid=1000,mode=0700",
        "--tmpfs",
        "/tmp:rw,uid=1000,gid=1000,mode=1777",
        "--mount",
        (f"type=volume,src={credential_source}," f"dst={credential_target},readonly"),
        "--env",
        "HOME=/home/app",
    ]
    for item in omnigent_proxy_env():
        argv.extend(("--env", item))
    stage_and_probe = (
        "set -eu; "
        "unset OPENAI_API_KEY ANTHROPIC_API_KEY OPENCODE_AUTH_CONTENT "
        "OPENCODE_CONFIG OPENCODE_CONFIG_CONTENT; "
        "mkdir -p /home/app/.local/share/opencode; "
        'cp "$1/auth.json" /home/app/.local/share/opencode/auth.json; '
        "chmod 0600 /home/app/.local/share/opencode/auth.json; "
        "exec opencode models --refresh"
    )
    argv.extend(
        (
            "--entrypoint",
            "/bin/sh",
            image_ref,
            "-ceu",
            stage_and_probe,
            "--",
            credential_target,
        )
    )
    return argv


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
                    f"opencode-validation-{profile.profile_id}-" f"{generation}"
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
            argv = _model_probe_argv(
                image_ref=self._image_ref,
                credential_source=attachment.sourceRef,
                credential_target=attachment.targetPath,
            )
            code, stdout, _stderr = await self._backend.run(argv, timeout_seconds=120)
            if code != 0 and "Unable to find image" in _stderr.decode(
                "utf-8", errors="replace"
            ):
                # Fail closed: never substitute a mutable tag for a digest-pinned image.
                raise HarnessPlatformError(
                    f"pinned OpenCode image {self._image_ref} not found: {_stderr.decode('utf-8', errors='replace')[:500]}",
                    code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
                )
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
            models = _validated_models(parsed)
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
                if version_code != 0 and "Unable to find image" in _version_err.decode(
                    "utf-8", errors="replace"
                ):
                    # Fail closed: never substitute a mutable tag for version check.
                    raise HarnessPlatformError(
                        f"pinned image {self._image_ref} not found for {binary} version check: {_version_err.decode('utf-8', errors='replace')[:500]}",
                        code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
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
                    # Preserve generation fences: do not force-remove volumes that may belong to another operation
                    msg = str(_cleanup_exc).lower()
                    if (
                        "generation" in msg
                        or "fenced" in msg
                        or "deferred" in msg
                        or "cleanup" in msg
                    ):
                        import logging

                        logging.getLogger(__name__).warning(
                            f"credential cleanup deferred due to fence: {_cleanup_exc}"
                        )
                        # Retain fenced/deferred result instead of force-removing
                    else:
                        raise


__all__ = [
    "OpenCodeProviderRuntimeValidationService",
    "_model_probe_argv",
    "_validated_models",
]
