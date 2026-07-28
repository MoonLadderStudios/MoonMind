"""Trusted restricted-egress profiles and Docker network attestation.

MoonLadderStudios/MoonMind#3516.  Workloads never receive these objects.  They
select a higher-level workload/launch policy and the trusted backend resolves
that policy to this immutable deployment-owned profile.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
from datetime import UTC, datetime
from typing import Awaitable, Callable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CommandRunner = Callable[[Sequence[str]], Awaitable[tuple[int, bytes, bytes]]]

ENFORCER_IMPLEMENTATION = "docker-internal-proxy/v1"
# Digest of the reviewed, mounted Squid policy. Attestation compares this
# deployment-owned value with both the container label and the live file.
EGRESS_CONFIG_DIGEST = "sha256:c3ad0d533dab70608bbbbcb0a5e82b94f67364cfcb2f4d670d0c944b2e945faa"
EGRESS_NETWORK_REF = "moonmind_restricted-egress-network"
EGRESS_GATEWAY_REF = "moonmind-sandbox-egress-proxy"
PROXY_URL = "http://sandbox-egress-proxy:3128"
_EXPECTED_GATEWAY_NETWORKS = frozenset(
    {
        EGRESS_NETWORK_REF,
        "moonmind_sandbox-egress-network",
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
    def public_resolvers_only(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            address = ipaddress.ip_address(value)
            if not address.is_global:
                raise ValueError("DNS resolvers must use global addresses")
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
        "dnsServers": ["1.1.1.1", "8.8.8.8"],
        "permittedWorkloadClasses": [
            "container_job",
            "managed_helper",
            "omnigent_static",
            "omnigent_on_demand",
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


async def attest_docker_egress(
    *,
    runner: CommandRunner,
    profile: EgressProfile,
    backend_ref: str,
) -> EgressAttestation:
    """Prove the internal network and sole dual-homed gateway exist.

    Docker's ``internal`` flag removes a default external route at the network
    layer.  The gateway is trusted deployment state and must carry the exact
    profile and implementation labels.  A stale gateway/profile therefore
    fails closed rather than being reused.
    """

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
    if labels.get("moonmind.egress.profile") != profile.ref:
        raise RuntimeError("restricted-egress gateway profile is stale or mismatched")
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
