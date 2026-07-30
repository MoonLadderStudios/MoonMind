"""Immutable, versioned egress profiles (MoonLadderStudios/MoonMind#3516).

An egress profile is a portable, credential-free declaration of the outbound
destinations a workload class is permitted to reach.  It carries *references and
declarative allow-lists only*: never credentials, never executable firewall
commands, never raw host paths.  The trusted backend compiles a validated
profile into a concrete default-deny rule set and attests enforcement before a
network ref may be reported as enforced (see ``egress_enforcement`` and
``docs/Omnigent/RestrictedEgressEnforcement.md``).

Design invariants:

* A declared or reflected network is *not* proof of restricted egress.  Only a
  validated profile whose compiled rule set passes conformance is attestable.
* Private, loopback, link-local (including the cloud metadata endpoint),
  multicast, Docker bridge/host-gateway and MoonMind control-plane ranges are
  denied implicitly and are not overridable from an allow-list unless a profile
  narrowly opts in with a recorded justification.
* Profiles are immutable and content-addressed; a version bump is required to
  change any field, and stale backend state keyed on an old digest never
  satisfies a new profile version.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Implicit, non-overridable denial ranges.
# ---------------------------------------------------------------------------
# These protect local, control-plane and infrastructure address space.  They
# are enforced by the compiler regardless of the allow-list, so an operator can
# never accidentally (or maliciously) open metadata or loopback egress by
# widening a CIDR entry.
IMPLICIT_DENY_CIDRS: tuple[str, ...] = (
    # Private (RFC1918) — also covers the default Docker bridge 172.17.0.0/16.
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    # Carrier-grade NAT / shared address space.
    "100.64.0.0/10",
    # Loopback.
    "127.0.0.0/8",
    "::1/128",
    # Link-local — includes the 169.254.169.254 cloud metadata endpoint and the
    # Docker host-gateway alias.
    "169.254.0.0/16",
    "fe80::/10",
    # Unique-local IPv6.
    "fc00::/7",
    # Multicast.
    "224.0.0.0/4",
    "ff00::/8",
    # Unspecified.
    "0.0.0.0/8",
    "::/128",
)

# The single cloud/instance metadata endpoint, called out explicitly so
# diagnostics can name it even though it also falls inside link-local space.
METADATA_ENDPOINTS: tuple[str, ...] = ("169.254.169.254", "fd00:ec2::254")

_IMPLICIT_DENY_NETWORKS = tuple(
    ipaddress.ip_network(cidr) for cidr in IMPLICIT_DENY_CIDRS
)

_DNS_NAME = re.compile(
    r"^(?=.{1,253}$)(\*\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
_SAFE_REF = re.compile(r"^[a-z0-9][a-z0-9._:@/-]{0,127}$")
# Shell / firewall-command metacharacters that must never appear in a profile;
# profiles declare intent, they do not carry executable rules.
_FORBIDDEN_CHARS = re.compile(r"[;&|`$<>\n\r\t\\]")
_FORBIDDEN_TOKENS = ("iptables", "nft", "ip route", "ip6tables", "--", "$(")

Ref = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@/-]*$")]
Port = Annotated[int, Field(ge=1, le=65535)]


def is_implicitly_denied(address: str) -> bool:
    """Return True when *address* falls inside a non-overridable denial range."""

    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    # Collapse IPv4-mapped IPv6 (``::ffff:a.b.c.d``) to the embedded IPv4 so a
    # v6-mapped metadata/loopback address cannot slip past the range check.
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    return any(parsed in network for network in _IMPLICIT_DENY_NETWORKS)


class EgressLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_connections: int = Field(alias="maxConnections", ge=1, le=100000)
    max_rate_per_minute: int = Field(alias="maxRatePerMinute", ge=1, le=1000000)
    max_bytes: int = Field(alias="maxBytes", ge=1)
    idle_seconds: int = Field(alias="idleSeconds", ge=1, le=86400)


class EgressLogging(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    retention_days: int = Field(alias="retentionDays", ge=1, le=365)
    redaction: Literal["required", "best_effort"] = "required"
    # Never persist full traffic payloads; only bounded, redacted diagnostics.
    payload_capture: Literal["denied"] = "denied"


class EgressProfile(BaseModel):
    """An immutable, versioned, credential-free egress allow-list."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    profile_id: str = Field(alias="profileId", pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    version: int = Field(ge=1)
    owner: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=512)

    allowed_dns: tuple[str, ...] = Field(default=(), alias="allowedDns")
    allowed_cidrs: tuple[str, ...] = Field(default=(), alias="allowedCidrs")
    allowed_ports: tuple[Port, ...] = Field(default=(443,), alias="allowedPorts")
    allowed_protocols: tuple[Literal["tcp", "udp"], ...] = Field(
        default=("tcp",), alias="allowedProtocols"
    )

    # ``resolve_once`` pins the first answer, ``revalidate`` re-checks every
    # answer against the allow-list on each connection (rebinding defence),
    # ``pin`` requires an operator-pinned IP set supplied out of band.
    resolution: Literal["resolve_once", "revalidate", "pin"] = "revalidate"
    dns_servers: tuple[str, ...] = Field(default=(), alias="dnsServers")
    resolver_policy: Literal["profile_dns_only", "system"] = Field(
        default="profile_dns_only", alias="resolverPolicy"
    )
    proxy_endpoints: tuple[str, ...] = Field(default=(), alias="proxyEndpoints")

    ipv6_policy: Literal["deny", "allow_listed"] = Field(
        default="deny", alias="ipv6Policy"
    )

    limits: EgressLimits
    logging: EgressLogging

    # Narrow, recorded opt-in required to allow an otherwise-forbidden internal
    # range (e.g. a lab gateway).  Absent this flag the compiler denies overlap.
    allow_internal_ranges: bool = Field(default=False, alias="allowInternalRanges")
    internal_range_justification: str = Field(
        default="", alias="internalRangeJustification", max_length=512
    )

    validation_state: Literal["draft", "validated", "revoked"] = Field(
        default="draft", alias="validationState"
    )
    security_review_ref: str | None = Field(default=None, alias="securityReviewRef")
    workload_classes: tuple[str, ...] = Field(
        default=(), alias="workloadClasses", min_length=1
    )
    policy_refs: tuple[str, ...] = Field(default=(), alias="policyRefs")

    @model_validator(mode="after")
    def _validate(self) -> "EgressProfile":
        self._reject_executable_or_secret_material()
        self._validate_destinations()
        self._validate_resolver()
        self._validate_validation_state()
        return self

    # -- validation helpers -------------------------------------------------
    def _reject_executable_or_secret_material(self) -> None:
        forbidden_keys = {
            "password", "token", "secret", "apikey", "accesstoken",
            "authtoken", "credential", "credentials",
        }

        def inspect(value: object, path: str) -> None:
            if isinstance(value, Mapping):
                for key, item in value.items():
                    if str(key).lower().replace("_", "") in forbidden_keys:
                        raise ValueError(
                            f"{path}{key}: egress profiles must not carry credentials"
                        )
                    inspect(item, f"{path}{key}.")
            elif isinstance(value, (list, tuple)):
                for item in value:
                    inspect(item, path)
            elif isinstance(value, str):
                if _FORBIDDEN_CHARS.search(value) or any(
                    token in value.lower() for token in _FORBIDDEN_TOKENS
                ):
                    raise ValueError(
                        f"{path[:-1]}: egress profiles declare intent and must not "
                        "contain executable firewall commands"
                    )

        inspect(self.model_dump(by_alias=True, mode="json"), "")

    def _validate_destinations(self) -> None:
        for name in self.allowed_dns:
            if not _DNS_NAME.fullmatch(name):
                raise ValueError(f"allowedDns entry is not a valid DNS name: {name!r}")
        seen_v6 = False
        for cidr in self.allowed_cidrs:
            try:
                network = ipaddress.ip_network(cidr, strict=False)
            except ValueError as exc:  # pragma: no cover - message passthrough
                raise ValueError(f"allowedCidrs entry is not a CIDR: {cidr!r}") from exc
            if network.version == 6:
                seen_v6 = True
            overlaps_denied = any(
                network.overlaps(denied) for denied in _IMPLICIT_DENY_NETWORKS
            )
            if overlaps_denied and not self.allow_internal_ranges:
                raise ValueError(
                    f"allowedCidrs {cidr!r} overlaps an implicitly denied internal "
                    "range; set allowInternalRanges with a justification to opt in"
                )
        if seen_v6 and self.ipv6_policy != "allow_listed":
            raise ValueError(
                "IPv6 CIDRs require ipv6Policy=allow_listed"
            )
        if self.allow_internal_ranges and not self.internal_range_justification.strip():
            raise ValueError(
                "allowInternalRanges requires internalRangeJustification"
            )
        for endpoint in self.proxy_endpoints:
            host, _, port = endpoint.rpartition(":")
            if not host or not port.isdigit() or not (1 <= int(port) <= 65535):
                raise ValueError(f"proxyEndpoints entry must be host:port: {endpoint!r}")

    def _validate_resolver(self) -> None:
        for server in self.dns_servers:
            try:
                ipaddress.ip_address(server)
            except ValueError as exc:
                raise ValueError(f"dnsServers entry must be an IP: {server!r}") from exc
        if self.resolver_policy == "profile_dns_only" and self.allowed_dns and not self.dns_servers:
            raise ValueError(
                "resolverPolicy=profile_dns_only requires explicit dnsServers when "
                "DNS names are allowed"
            )

    def _validate_validation_state(self) -> None:
        if self.validation_state == "validated" and not self.security_review_ref:
            raise ValueError(
                "a validated egress profile requires a securityReviewRef"
            )
        if self.security_review_ref is not None and not _SAFE_REF.fullmatch(
            self.security_review_ref
        ):
            raise ValueError("securityReviewRef must be a safe reference")

    # -- identity -----------------------------------------------------------
    @property
    def ref(self) -> str:
        return f"{self.profile_id}@{self.version}"

    @property
    def digest(self) -> str:
        canonical = json.dumps(
            self.model_dump(by_alias=True, mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()

    def permits_workload_class(self, workload_class: str) -> bool:
        return workload_class in self.workload_classes


# ---------------------------------------------------------------------------
# Built-in catalog.  Refs are stable; changing a destination requires a new
# version and a fresh security review.
# ---------------------------------------------------------------------------
_BASELINE = EgressProfile(
    profileId="egress-omnigent-baseline",
    version=1,
    owner="moonmind-security",
    description=(
        "Restricted egress for Omnigent hosts and generic workloads: provider "
        "APIs and source control over TLS only."
    ),
    allowedDns=(
        "api.openai.com",
        "api.anthropic.com",
        "github.com",
        "api.github.com",
        "codeload.github.com",
        "objects.githubusercontent.com",
    ),
    allowedPorts=(443,),
    allowedProtocols=("tcp",),
    resolution="revalidate",
    dnsServers=("1.1.1.1", "8.8.8.8"),
    resolverPolicy="profile_dns_only",
    ipv6Policy="deny",
    limits=EgressLimits(
        maxConnections=512,
        maxRatePerMinute=6000,
        maxBytes=5_368_709_120,
        idleSeconds=300,
    ),
    logging=EgressLogging(retentionDays=30, redaction="required"),
    validationState="validated",
    securityReviewRef="sec-review:omnigent-baseline-egress@1",
    workloadClasses=("omnigent_host", "container_job", "rag_gateway", "remediation"),
    policyRefs=("codex-static@1", "claude-static@1"),
)

_DENY_ALL = EgressProfile(
    profileId="egress-deny-all",
    version=1,
    owner="moonmind-security",
    description="Default-deny egress: no outbound destinations are permitted.",
    allowedDns=(),
    allowedCidrs=(),
    allowedPorts=(443,),
    ipv6Policy="deny",
    limits=EgressLimits(
        maxConnections=1,
        maxRatePerMinute=1,
        maxBytes=1,
        idleSeconds=1,
    ),
    logging=EgressLogging(retentionDays=30, redaction="required"),
    validationState="validated",
    securityReviewRef="sec-review:egress-deny-all@1",
    workloadClasses=("omnigent_host", "container_job", "rag_gateway", "remediation"),
)

EGRESS_PROFILES: dict[str, EgressProfile] = {
    profile.ref: profile for profile in (_BASELINE, _DENY_ALL)
}


def get_egress_profile(ref: str) -> EgressProfile | None:
    return EGRESS_PROFILES.get(ref)


def public_egress_catalog() -> dict[str, Any]:
    """Return the safe, product-selectable built-in egress profile catalog."""

    return {
        "profiles": [
            profile.model_dump(by_alias=True, mode="json")
            | {"ref": profile.ref, "digest": profile.digest}
            for profile in EGRESS_PROFILES.values()
            if profile.validation_state == "validated"
        ]
    }
