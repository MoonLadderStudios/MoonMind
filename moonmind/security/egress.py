"""Trusted restricted-egress profiles and Docker network attestation.

MoonLadderStudios/MoonMind#3516.  Workloads never receive these objects.  They
select a higher-level workload/launch policy and the trusted backend resolves
that policy to this immutable deployment-owned profile.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from datetime import UTC, datetime
from typing import Awaitable, Callable, Literal, Sequence
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CommandRunner = Callable[[Sequence[str]], Awaitable[tuple[int, bytes, bytes]]]

ENFORCER_IMPLEMENTATION = "docker-internal-proxy/v1"
# Digest of the reviewed, mounted Squid policy. Attestation compares this
# deployment-owned value with both the container label and the live file.
EGRESS_CONFIG_DIGEST = "sha256:742cc613eaeed6b3dfb37e5c4d167b4766a35f16cf9781cfd6fabac673d41e5d"
# Deployment-owned network names. Compose resolves these same overrides when it
# creates the networks (``restricted-egress-network`` /
# ``sandbox-egress-network``), so an operator that sets the documented override
# gets the resolved name fed into the immutable profile and every attestation
# rather than a hard-coded default the backend would then fail closed against.
EGRESS_NETWORK_REF = os.environ.get(
    "MOONMIND_RESTRICTED_EGRESS_NETWORK", "moonmind_restricted-egress-network"
)
_SANDBOX_EGRESS_NETWORK_REF = os.environ.get(
    "MOONMIND_SANDBOX_EGRESS_NETWORK", "moonmind_sandbox-egress-network"
)
OMNIGENT_EGRESS_NETWORK_REF = os.environ.get(
    "MOONMIND_OMNIGENT_EGRESS_NETWORK", "moonmind_omnigent-egress-network"
)
EGRESS_GATEWAY_REF = "moonmind-sandbox-egress-proxy"
PROXY_URL = "http://sandbox-egress-proxy:3128"
OMNIGENT_PROXY_URL = "http://omnigent-egress-proxy:3129"
_EXPECTED_GATEWAY_NETWORKS = frozenset(
    {
        EGRESS_NETWORK_REF,
        _SANDBOX_EGRESS_NETWORK_REF,
        OMNIGENT_EGRESS_NETWORK_REF,
        "local-network",
    }
)

_FORBIDDEN_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.168.0.0/16",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::/128",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
        "::ffff:0:0/96",
    )
)


class EgressDestination(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dns_name: str | None = Field(None, alias="dnsName")
    cidr: str | None = None
    ports: tuple[int, ...] = Field(min_length=1)
    protocol: Literal["tcp"] = "tcp"

    @model_validator(mode="after")
    def validate_destination(self) -> "EgressDestination":
        if (self.dns_name is None) == (self.cidr is None):
            raise ValueError("exactly one of dnsName or cidr is required")
        if self.dns_name is not None:
            name = self.dns_name.lower().rstrip(".")
            try:
                ipaddress.ip_address(name)
            except ValueError:
                pass
            else:
                raise ValueError("dnsName cannot be an IP literal")
            if (
                not name
                or "*" in name
                or "/" in name
                or name == "localhost"
                or name.endswith((".local", ".internal"))
            ):
                raise ValueError("dnsName must be an exact or suffix public DNS name")
            object.__setattr__(self, "dns_name", name)
        if self.cidr is not None:
            network = ipaddress.ip_network(self.cidr, strict=True)
            if any(network.overlaps(forbidden) for forbidden in _FORBIDDEN_NETWORKS):
                raise ValueError("destination overlaps a prohibited address range")
            object.__setattr__(self, "cidr", str(network))
        if any(port < 1 or port > 65535 for port in self.ports):
            raise ValueError("ports must be valid TCP ports")
        return self


class EgressProfile(BaseModel):
    """Immutable, declarative profile; commands and credentials are impossible."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str = Field(alias="profileId", pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    version: int = Field(ge=1)
    owner: str = Field(min_length=1, max_length=128)
    destinations: tuple[EgressDestination, ...] = Field(min_length=1)
    resolution: Literal["continuous_proxy_validation"] = Field(
        "continuous_proxy_validation"
    )
    ipv6: Literal["deny"] = "deny"
    dns_servers: tuple[str, ...] = Field(alias="dnsServers", min_length=1)
    permitted_workload_classes: tuple[str, ...] = Field(
        alias="permittedWorkloadClasses", min_length=1
    )
    max_connections: int = Field(ge=1, alias="maxConnections")
    idle_seconds: int = Field(ge=1, alias="idleSeconds")
    diagnostics_retention_days: int = Field(
        ge=1, le=90, alias="diagnosticsRetentionDays"
    )
    security_review_ref: str = Field(alias="securityReviewRef", min_length=1)
    validation_state: Literal["approved"] = Field("approved", alias="validationState")
    network_ref: str = Field(alias="networkRef")
    gateway_ref: str = Field(alias="gatewayRef")

    @field_validator("dns_servers")
    @classmethod
    def approved_resolvers_only(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            address = ipaddress.ip_address(value)
            if not address.is_global and value != "127.0.0.11":
                raise ValueError(
                    "DNS resolvers must be global or Docker's embedded resolver"
                )
        return values

    @property
    def ref(self) -> str:
        return f"{self.profile_id}@{self.version}"

    @property
    def digest(self) -> str:
        payload = self.model_dump(by_alias=True, mode="json")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"


class EgressAttestation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_ref: str = Field(alias="profileRef")
    profile_digest: str = Field(alias="profileDigest")
    enforcer_implementation: str = Field(alias="enforcerImplementation")
    backend_ref: str = Field(alias="backendRef")
    network_ref: str = Field(alias="networkRef")
    gateway_ref: str = Field(alias="gatewayRef")
    applied_rule_digest: str = Field(alias="appliedRuleDigest")
    config_digest: str = Field(alias="configDigest")
    gateway_image_digest: str = Field(alias="gatewayImageDigest")
    health_result: Literal["healthy"] = Field("healthy", alias="healthResult")
    validated_at: datetime = Field(alias="validatedAt")
    validation_result: Literal["passed"] = Field("passed", alias="validationResult")
    denied_connection_count: int = Field(0, alias="deniedConnectionCount", ge=0)
    diagnostics: tuple[str, ...] = Field(default_factory=tuple, max_length=20)


def _iter_scoped_denials(
    access_log: bytes,
    *,
    client_address: str,
    started_at: datetime | None,
    finished_at: datetime | None,
):
    """Yield ``(authority, result)`` for each denial owned by one workload.

    Denials are scoped to the workload both by client address and, when the
    container start/finish interval is known, by the Squid completion timestamp.
    Docker reuses a bridge IP once a prior restricted-egress container is
    removed, so filtering by address alone would attribute a previous holder's
    denials (still present in the gateway's line tail) to the new job. Bracketing
    on the container lifetime keeps terminal evidence per-launch.
    """

    if not client_address:
        return
    start_epoch = started_at.timestamp() if started_at is not None else None
    finish_epoch = finished_at.timestamp() if finished_at is not None else None
    # A small tolerance absorbs clock granularity between the daemon-reported
    # container interval and Squid's completion timestamp without widening the
    # window enough to readmit a prior IP holder's denials.
    tolerance_seconds = 2.0
    for raw_line in access_log.decode("utf-8", errors="replace").splitlines():
        fields = raw_line.split()
        if len(fields) < 7 or fields[2] != client_address:
            continue
        result = fields[3]
        if "DENIED" not in result:
            continue
        if start_epoch is not None or finish_epoch is not None:
            try:
                entry_epoch = float(fields[0])
            except ValueError:
                # A denial that cannot be placed in the container lifetime is not
                # attributable to this launch; exclude it rather than overcount.
                continue
            if start_epoch is not None and entry_epoch < start_epoch - tolerance_seconds:
                continue
            if (
                finish_epoch is not None
                and entry_epoch > finish_epoch + tolerance_seconds
            ):
                continue
        target = fields[6]
        try:
            if "://" in target:
                parsed = urlsplit(target)
                host = parsed.hostname
                if not host:
                    continue
                display_host = f"[{host}]" if ":" in host else host
                authority = (
                    f"{display_host}:{parsed.port}" if parsed.port else display_host
                )
            else:
                authority = target.split("/", 1)[0].rsplit("@", 1)[-1]
                parsed = urlsplit(f"//{authority}")
                if not parsed.hostname or any(char.isspace() for char in authority):
                    continue
                # Accessing port validates malformed bracket/port forms.
                _ = parsed.port
        except ValueError:
            continue
        if authority in {"", "-"}:
            continue
        yield authority[:253], result


def bounded_denial_diagnostics(
    access_log: bytes,
    *,
    client_address: str,
    limit: int = 20,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> tuple[str, ...]:
    """Extract bounded, payload-free denial evidence for one workload.

    Squid's native access log is read only at the trusted backend.  The durable
    form deliberately retains a normalized destination authority and status,
    never the request path, query, credentials, or complete traffic log. The
    optional container start/finish interval scopes denials to this launch so a
    reused bridge IP cannot attribute a prior holder's traffic here.
    """

    if limit < 1:
        return ()
    diagnostics: list[str] = []
    for authority, result in _iter_scoped_denials(
        access_log,
        client_address=client_address,
        started_at=started_at,
        finished_at=finished_at,
    ):
        diagnostics.append(f"denied {authority} {result[:64]}")
        if len(diagnostics) >= min(limit, 20):
            break
    return tuple(diagnostics)


def denied_connection_count(
    access_log: bytes,
    *,
    client_address: str,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> int:
    """Count all in-lifetime denials for one workload, ignoring the diagnostic cap.

    ``bounded_denial_diagnostics`` intentionally truncates the retained sample at
    20 entries; the terminal denied-connection counter must keep scanning so
    evidence does not silently underreport denied traffic above that cap.
    """

    return sum(
        1
        for _ in _iter_scoped_denials(
            access_log,
            client_address=client_address,
            started_at=started_at,
            finished_at=finished_at,
        )
    )


DEFAULT_EGRESS_PROFILE = EgressProfile.model_validate(
    {
        "profileId": "moonmind-provider-egress",
        "version": 1,
        "owner": "MoonMind security",
        "destinations": [
            {"dnsName": name, "ports": [443]}
            for name in (
                "anthropic.com",
                "chatgpt.com",
                "ghcr.io",
                "github.com",
                "githubassets.com",
                "githubusercontent.com",
                "google.com",
                "googleapis.com",
                "openai.com",
            )
        ],
        "dnsServers": ["127.0.0.11"],
        "permittedWorkloadClasses": [
            "container_job",
            "managed_helper",
            "rag_gateway",
            "remediation",
        ],
        "maxConnections": 128,
        "idleSeconds": 300,
        "diagnosticsRetentionDays": 30,
        "securityReviewRef": "MoonLadderStudios/MoonMind#3516",
        "networkRef": EGRESS_NETWORK_REF,
        "gatewayRef": EGRESS_GATEWAY_REF,
    }
)

OMNIGENT_EGRESS_PROFILE = EgressProfile.model_validate(
    {
        **DEFAULT_EGRESS_PROFILE.model_dump(by_alias=True, mode="json"),
        "profileId": "moonmind-omnigent-egress",
        "dnsServers": ["127.0.0.11"],
        "permittedWorkloadClasses": [
            "omnigent_static",
            "omnigent_on_demand",
        ],
        "networkRef": OMNIGENT_EGRESS_NETWORK_REF,
    }
)

EGRESS_PROFILE_DIGESTS = {
    profile.ref: profile.digest
    for profile in (DEFAULT_EGRESS_PROFILE, OMNIGENT_EGRESS_PROFILE)
}


def _gateway_policy_digest(profile: EgressProfile) -> str:
    """Digest proxy-enforced policy without deployment-specific identities."""

    payload = profile.model_dump(by_alias=True, mode="json")
    payload.pop("networkRef")
    payload.pop("gatewayRef")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


EGRESS_PROFILE_SET_DIGEST = "sha256:" + hashlib.sha256(
    json.dumps(
        {
            profile.ref: _gateway_policy_digest(profile)
            for profile in (DEFAULT_EGRESS_PROFILE, OMNIGENT_EGRESS_PROFILE)
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
).hexdigest()


async def attest_docker_egress(
    *,
    runner: CommandRunner,
    profile: EgressProfile,
    backend_ref: str,
) -> EgressAttestation:
    """Prove the internal network and sole dual-homed gateway exist.

    Docker's ``internal`` flag removes a default external route at the network
    layer. The gateway is trusted deployment state and must carry the exact
    approved profile-set and implementation labels. A stale gateway/profile
    therefore fails closed rather than being reused.
    """

    if EGRESS_PROFILE_DIGESTS.get(profile.ref) != profile.digest:
        raise RuntimeError("restricted-egress profile is not approved")

    code, stdout, _ = await runner(
        ("network", "inspect", "--format", "{{json .}}", profile.network_ref)
    )
    if code:
        raise RuntimeError("restricted-egress network is unavailable")
    try:
        network = json.loads(stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("restricted-egress network attestation is malformed") from exc
    if network.get("Internal") is not True or network.get("EnableIPv6") is True:
        raise RuntimeError("restricted-egress network is not internal IPv4-only state")

    format_value = (
        '{"labels":{{json .Config.Labels}},"networks":'
        '{{json .NetworkSettings.Networks}},"image":{{json .Image}},'
        '"health":{{json .State.Health.Status}}}'
    )
    code, stdout, _ = await runner(
        ("inspect", "--format", format_value, profile.gateway_ref)
    )
    if code:
        raise RuntimeError("restricted-egress gateway is unavailable")
    try:
        gateway = json.loads(stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("restricted-egress gateway attestation is malformed") from exc
    labels = gateway.get("labels") or {}
    networks = gateway.get("networks") or {}
    if labels.get("moonmind.egress.profile-set-digest") != EGRESS_PROFILE_SET_DIGEST:
        raise RuntimeError("restricted-egress gateway profile set is stale")
    if labels.get("moonmind.egress.enforcer") != ENFORCER_IMPLEMENTATION:
        raise RuntimeError("restricted-egress gateway implementation is unattested")
    if labels.get("moonmind.egress.config-digest") != EGRESS_CONFIG_DIGEST:
        raise RuntimeError("restricted-egress gateway config label is stale")
    if set(networks) != _EXPECTED_GATEWAY_NETWORKS:
        raise RuntimeError("restricted-egress gateway attachment is invalid")
    image_digest = str(gateway.get("image") or "")
    if not image_digest.startswith("sha256:"):
        raise RuntimeError("restricted-egress gateway image is unattested")
    health = str(gateway.get("health") or "")
    if health != "healthy":
        raise RuntimeError("restricted-egress gateway is not healthy")

    code, stdout, _ = await runner(
        (
            "exec",
            profile.gateway_ref,
            "sha256sum",
            "/etc/squid/squid.conf",
        )
    )
    if code:
        raise RuntimeError("restricted-egress live config cannot be observed")
    observed_config_digest = "sha256:" + stdout.decode(errors="replace").split()[0]
    if observed_config_digest != EGRESS_CONFIG_DIGEST:
        raise RuntimeError("restricted-egress live config is stale or mismatched")

    applied = {
        "profileDigest": profile.digest,
        "configDigest": observed_config_digest,
        "gatewayImageDigest": image_digest,
        "internal": True,
        "ipv6": False,
        "idleSeconds": profile.idle_seconds,
        "gatewayNetworks": sorted(networks),
        "enforcer": ENFORCER_IMPLEMENTATION,
    }
    applied_digest = "sha256:" + hashlib.sha256(
        json.dumps(applied, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return EgressAttestation(
        profileRef=profile.ref,
        profileDigest=profile.digest,
        enforcerImplementation=ENFORCER_IMPLEMENTATION,
        backendRef=backend_ref,
        networkRef=profile.network_ref,
        gatewayRef=profile.gateway_ref,
        appliedRuleDigest=applied_digest,
        configDigest=observed_config_digest,
        gatewayImageDigest=image_digest,
        healthResult="healthy",
        validatedAt=datetime.now(UTC),
    )


async def attest_docker_workload_egress(
    *,
    runner: CommandRunner,
    profile: EgressProfile,
    attestation: EgressAttestation,
    attachment_identity: str,
    expected_image_ref: str,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> dict[str, object]:
    """Prove one launched workload inherited the attested network boundary.

    Gateway attestation alone does not prove that the created container uses the
    gateway or lacks a secondary route.  This check runs after the production
    launch owner creates the workload and binds the exact container, network
    endpoint, immutable image, architecture, and bounded denial observations to
    the pre-launch attestation.  It intentionally reads only selected Docker
    fields so mounts and environment values never enter durable evidence.
    """

    if (
        attestation.profile_ref != profile.ref
        or attestation.profile_digest != profile.digest
        or attestation.network_ref != profile.network_ref
        or attestation.gateway_ref != profile.gateway_ref
        or attestation.validation_result != "passed"
    ):
        raise RuntimeError("restricted-egress launch attestation is inconsistent")
    identity = str(attachment_identity or "").strip()
    if not identity:
        raise RuntimeError("restricted-egress attachment identity is unavailable")
    expected_image = str(expected_image_ref or "").strip()
    if not expected_image:
        raise RuntimeError("restricted-egress workload image authority is unavailable")

    format_value = (
        '{"labels":{{json .Config.Labels}},"networks":'
        '{{json .NetworkSettings.Networks}},"imageRef":{{json .Config.Image}},'
        '"image":{{json .Image}}}'
    )
    code, stdout, _ = await runner(("inspect", "--format", format_value, identity))
    if code or not stdout.strip():
        raise RuntimeError("restricted-egress attachment evidence is unavailable")
    try:
        observed = json.loads(stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            "restricted-egress attachment evidence is malformed"
        ) from exc

    labels = observed.get("labels") if isinstance(observed, dict) else None
    networks = observed.get("networks") if isinstance(observed, dict) else None
    image_digest = str(
        observed.get("image") if isinstance(observed, dict) else ""
    ).strip()
    image_ref = str(
        observed.get("imageRef") if isinstance(observed, dict) else ""
    ).strip()
    if not isinstance(labels, dict) or not isinstance(networks, dict):
        raise RuntimeError("restricted-egress attachment evidence is malformed")
    if set(networks) != {profile.network_ref}:
        raise RuntimeError(
            "restricted-egress attachment is not the sole approved network"
        )
    expected_labels = {
        "moonmind.egress.profile": attestation.profile_ref,
        "moonmind.egress.profile_digest": attestation.profile_digest,
        "moonmind.egress.applied_rule_digest": attestation.applied_rule_digest,
    }
    if any(labels.get(key) != value for key, value in expected_labels.items()):
        raise RuntimeError("restricted-egress workload labels are unattested")
    if not image_digest.startswith("sha256:"):
        raise RuntimeError("restricted-egress workload image is unattested")
    if image_ref != expected_image:
        raise RuntimeError(
            "restricted-egress workload image does not match launch authority"
        )

    attachment = networks.get(profile.network_ref)
    if not isinstance(attachment, dict):
        raise RuntimeError("restricted-egress network attachment is malformed")
    network_id = str(attachment.get("NetworkID") or "").strip()
    endpoint_id = str(attachment.get("EndpointID") or "").strip()
    client_address = str(attachment.get("IPAddress") or "").strip()
    if not network_id or not endpoint_id or not client_address:
        raise RuntimeError(
            "restricted-egress network attachment identity is incomplete"
        )

    code, stdout, _ = await runner(
        ("image", "inspect", "--format", "{{json .Architecture}}", image_digest)
    )
    if code or not stdout.strip():
        raise RuntimeError("restricted-egress workload architecture is unavailable")
    try:
        architecture = json.loads(stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            "restricted-egress workload architecture is malformed"
        ) from exc
    if not isinstance(architecture, str) or not architecture.strip():
        raise RuntimeError("restricted-egress workload architecture is malformed")

    code, access_log, _ = await runner(
        (
            "exec",
            profile.gateway_ref,
            "tail",
            "-n",
            "500",
            "/var/log/squid/access.log",
        )
    )
    if code:
        raise RuntimeError("restricted-egress denial evidence is unavailable")
    denial_diagnostics = bounded_denial_diagnostics(
        access_log,
        client_address=client_address,
        started_at=started_at,
        finished_at=finished_at,
    )
    denied_count = denied_connection_count(
        access_log,
        client_address=client_address,
        started_at=started_at,
        finished_at=finished_at,
    )
    return {
        **attestation.model_dump(by_alias=True, mode="json"),
        "attachmentIdentity": identity,
        "networkIdentity": network_id,
        "endpointIdentity": endpoint_id,
        "attachmentAddressDigest": "sha256:"
        + hashlib.sha256(client_address.encode()).hexdigest(),
        "workloadImageDigest": image_digest,
        "workloadImageRef": image_ref,
        "architecture": architecture.strip(),
        "deniedConnectionCount": denied_count,
        "denialDiagnostics": list(denial_diagnostics),
    }


def restricted_proxy_env() -> tuple[str, ...]:
    """Non-overridable proxy environment for an internally routed workload."""

    return (
        f"HTTP_PROXY={PROXY_URL}",
        f"HTTPS_PROXY={PROXY_URL}",
        f"http_proxy={PROXY_URL}",
        f"https_proxy={PROXY_URL}",
        "NO_PROXY=",
        "no_proxy=",
    )


def omnigent_proxy_env() -> tuple[str, ...]:
    """Proxy environment for isolated Omnigent hosts and their runners.

    Codex's native bridge connects the TUI to its app server over a same-host
    loopback WebSocket.  Loopback cannot reach an external destination from the
    container, so exempt it while continuing to proxy every non-local address.
    """

    return (
        f"HTTP_PROXY={OMNIGENT_PROXY_URL}",
        f"HTTPS_PROXY={OMNIGENT_PROXY_URL}",
        f"http_proxy={OMNIGENT_PROXY_URL}",
        f"https_proxy={OMNIGENT_PROXY_URL}",
        "NO_PROXY=localhost,127.0.0.1",
        "no_proxy=localhost,127.0.0.1",
    )
