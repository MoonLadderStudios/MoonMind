"""Pinned-runtime validation for an enrolled OpenCode Provider Profile."""

from __future__ import annotations

import json
import re
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
    NoopCredentialMaterializer,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.host_image_drift import compatible_deployed_fallback
from moonmind.omnigent.harness_platform.materializers import (
    materializer_ref_for_provider,
)
from moonmind.omnigent.provider_leases import AcquiredProviderLease
from moonmind.omnigent.secret_resolution import (
    OmnigentSecretResolutionService,
    ScopedSecretBundle,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.security.egress import OMNIGENT_EGRESS_NETWORK_REF, omnigent_proxy_env


def _models(value: Any, *, provider_id: str) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str):
        for line in value.splitlines():
            item = line.strip().strip("\"',")
            provider, separator, model = item.partition("/")
            if separator and model and provider == provider_id:
                found.add(item)
    elif isinstance(value, dict):
        for item in value.values():
            found.update(_models(item, provider_id=provider_id))
    elif isinstance(value, list):
        for item in value:
            found.update(_models(item, provider_id=provider_id))
    return found


def _validated_models(value: Any, *, provider_id: str) -> list[str]:
    """Return observed OpenCode models or reject the validation result.

    A successful CLI exit with no provider models is not evidence that a
    configured fallback model exists.  Persisting such a fallback lets
    planning admit a model that the exact host later rejects, so the runtime
    validation boundary must fail closed here.
    """

    models = sorted(_models(value, provider_id=provider_id))
    if not models:
        raise HarnessPlatformError(
            "pinned OpenCode runtime returned no OpenCode models",
            code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
        )
    return models


#: Prefix marking an affirmative provider-side credential rejection. The API
#: enrollment boundary persists ``auth_invalid`` only for errors carrying this
#: marker; every other validation failure keeps the enrolled credential.
CREDENTIAL_REJECTED_MARKER = "OpenCode provider credential rejected"

#: Substrings of catalog stderr that affirm the provider rejected the
#: credential (as opposed to infrastructure failing to ask).
_CREDENTIAL_REJECTED_SIGNALS = (
    "invalid api key",
    "invalid_api_key",
    "incorrect api key",
    "wrong api key",
    "api key is invalid",
    "unauthorized",
    "unauthenticated",
    "authentication failed",
    "bad credentials",
    "credential rejected",
    "auth rejected",
    "status 401",
    "status 403",
    "error 401",
    "error 403",
    "http 401",
    "http 403",
)

#: Substrings of catalog stderr showing the probe image cannot serve catalog
#: discovery for this provider route (a runtime incompatibility, not a
#: credential verdict).
_CATALOG_UNSUPPORTED_SIGNALS = (
    "unknown command",
    "unknown flag",
    "unknown shorthand",
    "executable file not found",
    "command not found",
    ": not found",
    "no such file or directory",
)

#: Substrings of catalog stderr showing temporary infrastructure or transport
#: failure. These must stay retryable and must never be persisted as
#: credential rejection.
_CATALOG_INFRA_SIGNALS = (
    "connection refused",
    "connection reset",
    "connection timed out",
    "timed out",
    "timeout",
    "temporary failure",
    "try again",
    "name resolution",
    "network is unreachable",
    "network unreachable",
    "socket hang up",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "cannot connect to the docker daemon",
    "context deadline",
    "i/o timeout",
    "econnrefused",
    "econnreset",
    "etimedout",
    "enotfound",
    "tls",
    "certificate",
    "proxy",
    "dns",
)


def _stderr_excerpt(stderr: bytes, *, limit: int = 500) -> str:
    return stderr.decode("utf-8", errors="replace").strip()[:limit]


def _catalog_probe_failure(
    *,
    exit_code: int,
    stderr: bytes,
    effective_image_ref: str,
) -> HarnessPlatformError:
    """Classify a nonzero catalog probe without dropping its stderr.

    Credential rejection, required runtime incompatibility, and temporary
    infrastructure failure receive different failure codes so callers can
    recover (retry, re-pin, re-enroll) instead of collapsing every cause
    into one generic validation message.
    """

    excerpt = _stderr_excerpt(stderr)
    lowered = excerpt.lower()
    detail = f"(exit {exit_code}): {excerpt}" if excerpt else f"(exit {exit_code})"
    if any(signal in lowered for signal in _CATALOG_UNSUPPORTED_SIGNALS):
        return HarnessPlatformError(
            f"OpenCode runtime image {effective_image_ref} does not support "
            f"provider catalog discovery {detail}",
            code=HarnessPlatformFailure.OMNIGENT_VENDOR_RUNTIME_MISMATCH,
        )
    if any(signal in lowered for signal in _CATALOG_INFRA_SIGNALS):
        return HarnessPlatformError(
            "OpenCode catalog discovery unavailable "
            f"{detail}; retry without changing the credential",
            code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
        )
    if any(signal in lowered for signal in _CREDENTIAL_REJECTED_SIGNALS):
        return HarnessPlatformError(
            f"{CREDENTIAL_REJECTED_MARKER} by the pinned runtime {detail}",
            code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
        )
    return HarnessPlatformError(
        f"OpenCode catalog discovery failed {detail}",
        code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
    )


def is_confirmed_credential_rejection(exc: BaseException) -> bool:
    """Report whether a validation error affirms provider credential rejection.

    Only this verdict authorizes the API enrollment boundary to persist
    ``auth_invalid``. Infrastructure, runtime-incompatibility, and
    model-discovery failures never qualify, even when they surface through
    the same validation path.
    """

    if not isinstance(exc, HarnessPlatformError):
        return False
    if exc.code != HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE:
        return False
    text = str(exc).lower()
    return CREDENTIAL_REJECTED_MARKER.lower() in text or any(
        signal in text for signal in _CREDENTIAL_REJECTED_SIGNALS
    )


def is_transient_validation_error(exc: BaseException) -> bool:
    """Report whether a validation error must not consume a failure budget.

    Transient infrastructure, lease, secret-resolution, and materialization
    failures carry no verdict about the credential or the runtime, so the
    re-validation exhaustion budget and any persisted failure latch must
    survive them intact.
    """

    if not isinstance(exc, HarnessPlatformError):
        return False
    return exc.code in {
        HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
        HarnessPlatformFailure.OMNIGENT_HOST_REGISTRATION_TIMEOUT,
        HarnessPlatformFailure.OMNIGENT_PROVIDER_LEASE_UNAVAILABLE,
        HarnessPlatformFailure.OMNIGENT_SECRET_RESOLUTION_FAILED,
        HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE,
        HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
        HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_UNAVAILABLE,
        HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_STALE,
    }


def _model_probe_argv(
    *,
    image_ref: str,
    provider_id: str,
    credential_source: str | None = None,
    credential_target: str | None = None,
) -> list[str]:
    """Build the exact pinned-runtime catalog probe command.

    The credential materializer deliberately mounts a read-only staging
    directory. OpenCode reads ``auth.json`` from its writable data directory,
    so validation must perform the same staging step as the real host before
    invoking catalog discovery.
    """

    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", provider_id):
        raise ValueError("invalid OpenCode provider ID")
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
        "--env",
        "HOME=/home/app",
    ]
    if (credential_source is None) != (credential_target is None):
        raise ValueError("credential source and target must be supplied together")
    if credential_source is not None and credential_target is not None:
        argv[argv.index("--env") : argv.index("--env")] = [
            "--mount",
            (
                f"type=volume,src={credential_source},"
                f"dst={credential_target},readonly"
            ),
        ]
    for item in omnigent_proxy_env():
        argv.extend(("--env", item))
    stage_auth = (
        "mkdir -p /home/app/.local/share/opencode; "
        'cp "$1/auth.json" /home/app/.local/share/opencode/auth.json; '
        "chmod 0600 /home/app/.local/share/opencode/auth.json; "
        if credential_target is not None
        else ""
    )
    stage_and_probe = (
        "set -eu; "
        "unset OPENAI_API_KEY ANTHROPIC_API_KEY OPENCODE_AUTH_CONTENT "
        "OPENCODE_CONFIG OPENCODE_CONFIG_CONTENT; "
        + stage_auth
        + 'exec opencode models --refresh "$2"'
    )
    argv.extend(("--entrypoint", "/bin/sh", image_ref, "-ceu", stage_and_probe))
    argv.extend(("--", credential_target or "", provider_id))
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

    async def _resolve_effective_image_ref(self) -> str:
        """Return the one concrete image this operation runs.

        When the requested digest-pinned image is missing locally but a
        qualified same-repository deployment image is installed, the whole
        operation — credential preparation, catalog discovery, version
        probes, and recorded evidence — uses that replacement. Otherwise the
        requested ref is returned unchanged so downstream probes fail closed
        on the missing image instead of substituting a mutable tag.
        """

        requested = self._image_ref
        try:
            code, _, _ = await self._backend.run(
                ["docker", "image", "inspect", requested, "--format", "{{.Id}}"]
            )
        except Exception:
            # The presence probe itself could not run (Docker/transport
            # failure). Return the requested ref unchanged: the credential
            # materializer owns pull-or-fail and surfaces the real cause.
            return requested
        if code == 0:
            return requested
        try:
            fallback = compatible_deployed_fallback(requested)
        except Exception:
            fallback = None
        if fallback is not None:
            try:
                fallback_code, _, _ = await self._backend.run(
                    ["docker", "image", "inspect", fallback, "--format", "{{.Id}}"]
                )
            except Exception:
                return requested
            if fallback_code == 0:
                return fallback
        return requested

    async def validate(
        self,
        *,
        profile: Any,
        lease: Any,
        candidate_secret: str | None = None,
        candidate_generation: int | None = None,
    ) -> dict[str, Any]:
        if profile.runtime_id != "opencode":
            raise HarnessPlatformError(
                "OpenCode validation requires an OpenCode Provider Profile",
                code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
            )
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
        materializer_ref = materializer_ref_for_provider(
            str(profile.runtime_id or ""),
            str(profile.provider_id or ""),
        )
        if materializer_ref == "none@1":
            if candidate_secret is not None:
                raise ValueError("credentialless OpenCode validation rejects secrets")
            secrets = ScopedSecretBundle(
                provider_profile_ref=profile.profile_id,
                credential_generation=generation,
                values={},
            )
            materializer: Any = NoopCredentialMaterializer(materializer_ref)
        else:
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
            materializer = DockerOpencodeAuthJsonMaterializer(self._backend)
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
        handle = None
        try:
            effective_image_ref = await self._resolve_effective_image_ref()
            handle = await materializer.materialize(
                CredentialMaterializationContext(
                    request=request,
                    acquired=acquired,
                    secrets=secrets,
                    writer_image_ref=effective_image_ref,
                    artifact_gateway=self._artifacts,
                    provider_route_ref=str(profile.provider_id or ""),
                )
            )
            attachment = handle.attachments[0] if handle.attachments else None
            argv = _model_probe_argv(
                image_ref=effective_image_ref,
                provider_id=str(profile.provider_id or ""),
                credential_source=(attachment.sourceRef if attachment else None),
                credential_target=(attachment.targetPath if attachment else None),
            )
            code, stdout, _stderr = await self._backend.run(
                argv, timeout_seconds=120, output_limit_bytes=1_048_576
            )
            if code != 0 and "Unable to find image" in _stderr.decode(
                "utf-8", errors="replace"
            ):
                # Fail closed: never substitute a mutable tag for a digest-pinned image.
                raise HarnessPlatformError(
                    f"pinned OpenCode image {effective_image_ref} not found: {_stderr.decode('utf-8', errors='replace')[:500]}",
                    code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
                )
            if code != 0:
                raise _catalog_probe_failure(
                    exit_code=code,
                    stderr=_stderr,
                    effective_image_ref=effective_image_ref,
                )
            text = stdout.decode("utf-8", errors="replace")
            try:
                parsed: Any = json.loads(text)
            except json.JSONDecodeError:
                parsed = text
            models = _validated_models(
                parsed, provider_id=str(profile.provider_id or "")
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
                        effective_image_ref,
                        "--version",
                    ]
                )
                if version_code != 0 and "Unable to find image" in _version_err.decode(
                    "utf-8", errors="replace"
                ):
                    # Fail closed: never substitute a mutable tag for version check.
                    raise HarnessPlatformError(
                        f"pinned image {effective_image_ref} not found for {binary} version check: {_version_err.decode('utf-8', errors='replace')[:500]}",
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
            evidence: dict[str, Any] = {
                "schemaVersion": "moonmind.provider-model-catalog-evidence.v1",
                "models": [{"qualifiedId": item} for item in models],
                "imageRef": effective_image_ref,
                "runtimeVersions": versions,
                "validatedAt": datetime.now(UTC).isoformat(),
                "credentialGeneration": generation,
                "materializerRef": materializer.ref,
                "credentialAttestationRef": handle.attestationRef,
                "secretValueRecorded": False,
            }
            if effective_image_ref != self._image_ref:
                # Record the substitution truthfully: observations name the
                # image that actually ran, and the requested digest is kept
                # alongside for diagnosis. Historical records stay immutable.
                evidence["requestedImageRef"] = self._image_ref
            return evidence
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
    "CREDENTIAL_REJECTED_MARKER",
    "OpenCodeProviderRuntimeValidationService",
    "_model_probe_argv",
    "_validated_models",
    "is_confirmed_credential_rejection",
    "is_transient_validation_error",
]
