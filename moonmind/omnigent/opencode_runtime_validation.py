"""Pinned-runtime validation for an enrolled OpenCode Provider Profile."""

from __future__ import annotations

import json
import re
import shlex
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
#: A bare HTTP 403 alone is not credential evidence: insufficient model
#: permissions, regional policy, billing, or an upstream WAF all surface as
#: 403 for a valid key. Only explicit authentication-rejection wording (or a
#: 401 status, which names the unauthorized condition) carries the verdict.
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
    "error 401",
    "http 401",
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


def _redact_known_secrets(text: str, secrets: tuple[str, ...] | None) -> str:
    """Remove exact active-secret values from provider output before retaining it.

    Providers echo the submitted key in errors such as ``Incorrect API key
    provided: sk-...``. The generic text redactor does not recognize
    arbitrary key shapes, so redact against the resolved/candidate secret
    itself. Only values of at least 4 characters participate to avoid
    scrubbing ordinary prose.
    """

    if not text or not secrets:
        return text
    redacted = text
    for secret in secrets:
        if not isinstance(secret, str) or len(secret) < 4:
            continue
        if secret in redacted:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def _stderr_excerpt(
    stderr: bytes, *, limit: int = 500, secrets_to_redact: tuple[str, ...] | None = None
) -> str:
    raw = stderr.decode("utf-8", errors="replace").strip()
    # Redact before truncating so a long key cannot leak its prefix in the
    # retained excerpt.
    redacted = _redact_known_secrets(raw, secrets_to_redact)
    return redacted[:limit]


def _active_secrets_for_redaction(
    *, candidate_secret: str | None, secrets: ScopedSecretBundle | None
) -> tuple[str, ...]:
    found: list[str] = []
    if candidate_secret:
        found.append(candidate_secret)
    values = getattr(secrets, "values", None)
    if isinstance(values, dict):
        for value in values.values():
            if isinstance(value, str) and value and value not in found:
                found.append(value)
    return tuple(found)


def _catalog_probe_failure(
    *,
    exit_code: int,
    stderr: bytes,
    effective_image_ref: str,
    secrets_to_redact: tuple[str, ...] | None = None,
) -> HarnessPlatformError:
    """Classify a nonzero catalog probe without dropping its stderr.

    Credential rejection, required runtime incompatibility, and temporary
    infrastructure failure receive different failure codes so callers can
    recover (retry, re-pin, re-enroll) instead of collapsing every cause
    into one generic validation message.
    """

    excerpt = _stderr_excerpt(stderr, secrets_to_redact=secrets_to_redact)
    lowered = excerpt.lower()
    detail = f"(exit {exit_code}): {excerpt}" if excerpt else f"(exit {exit_code})"
    if "opencode credential verification unavailable" in lowered:
        return HarnessPlatformError(
            f"OpenCode credential verification unavailable {detail}; retry without changing the credential",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_UNAVAILABLE,
        )
    if "opencode local" in lowered and "api unavailable" in lowered:
        return HarnessPlatformError(
            f"OpenCode catalog transport unavailable {detail}; retry without changing the credential",
            code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
        )
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
    # Unknown catalog failures (HTTP 429/500, plain 403, unexpected shapes)
    # carry no definitive credential or runtime verdict. Keep them retryable
    # so re-validation preserves its attempt budget instead of latching a
    # transient provider outage into a credential reconnect or image repin.
    return HarnessPlatformError(
        f"OpenCode catalog discovery failed {detail}; "
        "retry without changing the credential",
        code=HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_UNAVAILABLE,
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


# Runs inside the selected immutable image; only model IDs cross this boundary.
_OPENCODE_V2_CATALOG_PROBE = r"""
import base64
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

provider, credential_dir, mode = sys.argv[1:]
if mode not in ("validate", "catalog"):
    raise SystemExit("invalid catalog probe mode")
root = Path(tempfile.mkdtemp(prefix="opencode-catalog-"))
# Preserve transport and runtime settings only, never ambient provider accounts.
env = {name: value for name, value in os.environ.items() if name in {
    "PATH", "LANG", "LC_ALL", "TZ", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "NODE_EXTRA_CA_CERTS",
}}
for name, directory in (("HOME", "home"), ("XDG_CONFIG_HOME", "config"),
                        ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"),
                        ("XDG_STATE_HOME", "state")):
    path = root / directory
    path.mkdir()
    env[name] = str(path)
env["OPENCODE_PASSWORD"] = secrets.token_urlsafe(32)
env["OPENCODE_DB"] = str(root / "opencode.db")
env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
authorization = "Basic " + base64.b64encode(
    ("opencode:" + env["OPENCODE_PASSWORD"]).encode()
).decode()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
deadline = time.monotonic() + 90


def request(path, body=None):
    headers = {"Authorization": authorization}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/{path}", data=data, headers=headers
    )
    with opener.open(req, timeout=min(10, max(.1, deadline-time.monotonic()))) as response:
        payload = response.read(16_777_217)
        if len(payload) > 16_777_216:
            raise RuntimeError("catalog response exceeded 16777216 bytes")
        return json.loads(payload) if payload else None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verify_provider_key(provider, key):
    # Provider-owned non-generation endpoints. Never take a URL from catalog
    # metadata or follow redirects with the credential.
    endpoint = {
        "openrouter": "https://openrouter.ai/api/v1/key",
        "opencode-go": "https://opencode.ai/zen/go/v1/usage",
    }.get(provider)
    if endpoint is None:
        # v2 has no universal account-verification operation. Importing a key
        # and observing models establishes configuration, not authentication.
        return "unverified"
    provider_opener = urllib.request.build_opener(NoRedirect())
    req = urllib.request.Request(endpoint, headers={
        "Authorization": "Bearer " + key, "Accept": "application/json",
    })
    try:
        with provider_opener.open(req, timeout=15) as response:
            payload = response.read(262145)
            if len(payload) > 262144:
                raise RuntimeError("OpenCode credential verification unavailable: oversized response")
            body = json.loads(payload)
    except urllib.error.HTTPError as error:
        # Do not retain account metadata or error bodies, which can echo keys.
        if error.code == 401:
            raise RuntimeError("OpenCode provider credential rejected: HTTP 401") from None
        raise RuntimeError("OpenCode credential verification unavailable: provider HTTP " + str(error.code)) from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise RuntimeError("OpenCode credential verification unavailable: provider transport or response") from None
    if provider == "openrouter":
        data = body.get("data") if isinstance(body, dict) else None
        valid = isinstance(data, dict) and isinstance(data.get("label"), str) and bool(data["label"].strip())
        if valid and data.get("disabled") is True:
            raise RuntimeError("OpenCode provider credential rejected: disabled key")
    else:
        usage = body.get("usage") if isinstance(body, dict) else None
        def meter(value):
            return (isinstance(value, dict) and value.get("status") in ("ok", "rate-limited")
                and type(value.get("percent")) in (int, float) and math.isfinite(value["percent"])
                and isinstance(value.get("resetsAt"), str) and bool(value["resetsAt"]))
        valid = isinstance(usage, dict) and all(meter(usage.get(k)) for k in ("rolling", "weekly", "monthly"))
    if not valid:
        raise RuntimeError("OpenCode credential verification unavailable: unexpected provider response")
    return "verified"


with (root / "serve.log").open("w+b") as log:
    # The host PATH may contain MoonMind's context-restoring wrapper. Invoke
    # the image-owned vendor executable directly so it cannot rehydrate auth.
    binary = "/usr/local/bin/opencode"
    if not Path(binary).is_file():
        binary = shutil.which("opencode") or binary
    process = subprocess.Popen(
        [binary, "serve", "--hostname", "127.0.0.1", "--port", str(port), "--stdio"],
        stdin=subprocess.PIPE, stdout=log, stderr=log, env=env, cwd=root,
    )
    try:
        while True:
            if process.poll() is not None:
                raise RuntimeError("OpenCode server exited before catalog readiness (exit " + str(process.returncode) + ")")
            try:
                info = request("info")
                if not isinstance(info, dict) or not isinstance(info.get("version"), str) or not info["version"].strip():
                    raise RuntimeError("OpenCode info API returned an invalid response")
                break
            except urllib.error.HTTPError as error:
                # Loopback API auth failures are not provider credential verdicts.
                raise RuntimeError(f"OpenCode local API unavailable (status {error.code})") from None
            except (urllib.error.URLError, TimeoutError):
                if time.monotonic() >= deadline:
                    raise RuntimeError("OpenCode startup timeout") from None
                time.sleep(.25)
        verification = "not_required" if provider == "opencode" and not credential_dir else "unverified"
        if credential_dir and not (Path(credential_dir) / "auth.json").is_file():
            if mode == "catalog" and provider == "opencode":
                credential_dir = ""
            else:
                raise RuntimeError("OpenCode credential verification unavailable: selected provider credential is missing")
        if credential_dir:
            credentials = json.loads((Path(credential_dir) / "auth.json").read_text())
            entry = credentials.get(provider, {})
            if entry.get("type") != "api" or not isinstance(entry.get("key"), str) or not entry["key"]:
                raise RuntimeError("OpenCode credential materialization missing selected provider key")
            if mode == "validate":
                verification = verify_provider_key(provider, entry["key"])
            try:
                request(f"integration/{provider}/connect/key", {"key": entry["key"]})
            except urllib.error.HTTPError as error:
                if error.code == 401:
                    raise RuntimeError("OpenCode local API unavailable (status 401)") from None
                raise RuntimeError(f"OpenCode local integration API unavailable (status {error.code})") from None
        while True:
            try:
                payload = request("model")
            except urllib.error.HTTPError as error:
                raise RuntimeError(f"OpenCode local catalog API unavailable (status {error.code})") from None
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise RuntimeError("OpenCode model API returned an invalid catalog shape")
            models = sorted({provider + "/" + row["id"] for row in rows
                if isinstance(row, dict) and row.get("providerID") == provider
                and isinstance(row.get("id"), str) and row["id"].strip()
                and row.get("enabled") is not False})
            if models:
                # API rows can include provider keys in settings. Emit IDs only.
                print(json.dumps({"models": models, "credentialVerification": {"status": verification}}
                    if mode == "validate" else models))
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("OpenCode catalog settlement timeout: no models for selected provider")
            time.sleep(.5)
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        # Disposable SQLite credentials and server diagnostics are not retained.
        shutil.rmtree(root, ignore_errors=True)
"""


def opencode_catalog_probe_argv(
    *,
    provider_id: str,
    credential_target: str | None = None,
    verify_credentials: bool = True,
) -> list[str]:
    """Run isolated v2 discovery; catalog-only mode never attests credentials."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", provider_id):
        raise ValueError("invalid OpenCode provider ID")
    return [
        "python3",
        "-c",
        _OPENCODE_V2_CATALOG_PROBE,
        provider_id,
        credential_target or "",
        "validate" if verify_credentials else "catalog",
    ]


def _model_probe_argv(
    *,
    image_ref: str,
    provider_id: str,
    credential_source: str | None = None,
    credential_target: str | None = None,
) -> list[str]:
    """Build the exact pinned-runtime catalog probe command.

    The credential materializer deliberately mounts a read-only staging
    directory. The v2 key-connection endpoint imports only the selected provider into a
    fresh, disposable database. Catalog rows are projected to IDs in the
    container because their settings can contain credentials.
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
    stage_and_probe = (
        "set -eu; exec python3 -c "
        + shlex.quote(_OPENCODE_V2_CATALOG_PROBE)
        + ' "$2" "$1" validate'
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
            secrets_to_redact = _active_secrets_for_redaction(
                candidate_secret=candidate_secret, secrets=secrets
            )
            if code != 0 and "Unable to find image" in _stderr.decode(
                "utf-8", errors="replace"
            ):
                # Fail closed: never substitute a mutable tag for a digest-pinned image.
                raw_missing = _stderr.decode("utf-8", errors="replace")[:500]
                raise HarnessPlatformError(
                    f"pinned OpenCode image {effective_image_ref} not found: "
                    f"{_redact_known_secrets(raw_missing, secrets_to_redact)}",
                    code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
                )
            if code != 0:
                raise _catalog_probe_failure(
                    exit_code=code,
                    stderr=_stderr,
                    effective_image_ref=effective_image_ref,
                    secrets_to_redact=secrets_to_redact,
                )
            text = stdout.decode("utf-8", errors="replace")
            try:
                parsed: Any = json.loads(text)
            except json.JSONDecodeError:
                parsed = text
            models = _validated_models(
                parsed, provider_id=str(profile.provider_id or "")
            )
            verification = (
                parsed.get("credentialVerification")
                if isinstance(parsed, dict)
                else None
            )
            if not isinstance(verification, dict) or verification.get("status") not in {
                "verified",
                "unverified",
                "not_required",
            }:
                verification = {"status": "unverified"}
            # Only the authenticated probe result certifies the current key.
            verification = {"status": verification["status"]}
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
                "credentialVerification": verification,
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
    "opencode_catalog_probe_argv",
    "_validated_models",
    "is_confirmed_credential_rejection",
    "is_transient_validation_error",
]
