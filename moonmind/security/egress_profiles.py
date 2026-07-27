"""Immutable restricted-egress policy and Docker attestation contracts.

Profiles are data, never firewall programs.  The trusted Docker backend accepts a
network only when a privileged, deployment-owned reconciler has labelled it with
the exact profile and applied-rule digests.  A plain Docker bridge can therefore
never be advertised as enforced egress.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
from datetime import datetime
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ENFORCER_IMPLEMENTATION = "moonmind-docker-gateway/v1"
ATTESTATION_LABELS = {
    "profile_ref": "moonmind.egress.profile_ref",
    "profile_digest": "moonmind.egress.profile_digest",
    "rules_digest": "moonmind.egress.rules_digest",
    "enforcer": "moonmind.egress.enforcer",
    "validated": "moonmind.egress.validated",
    "validated_at": "moonmind.egress.validated_at",
    "signature": "moonmind.egress.attestation_signature",
}
_DNS_NAME = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")


class EgressDestination(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dns_name: str | None = Field(None, alias="dnsName")
    cidr: str | None = None
    ports: tuple[int, ...] = Field(min_length=1)
    protocol: Literal["tcp", "udp"] = "tcp"

    @model_validator(mode="after")
    def validate_destination(self) -> "EgressDestination":
        if (self.dns_name is None) == (self.cidr is None):
            raise ValueError("exactly one of dnsName or cidr is required")
        if self.dns_name is not None and not _DNS_NAME.fullmatch(self.dns_name):
            raise ValueError("dnsName must be a normalized absolute DNS name")
        if self.cidr is not None:
            network = ipaddress.ip_network(self.cidr, strict=True)
            if not network.is_global:
                raise ValueError("allowed CIDRs must be globally routable")
        if any(port < 1 or port > 65535 for port in self.ports):
            raise ValueError("ports must be in 1..65535")
        return self


class EgressProfile(BaseModel):
    """Security-reviewed, immutable input to the privileged gateway reconciler."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    profile_id: str = Field(alias="profileId", pattern=r"^[a-z0-9][a-z0-9.-]{0,62}$")
    version: int = Field(ge=1)
    owner: str = Field(min_length=1, max_length=255)
    allowed_destinations: tuple[EgressDestination, ...] = Field(
        alias="allowedDestinations", min_length=1
    )
    resolution_mode: Literal["continuous", "pinned"] = Field(alias="resolutionMode")
    dns_servers: tuple[str, ...] = Field(alias="dnsServers", min_length=1)
    ipv6_policy: Literal["deny", "enforce"] = Field(alias="ipv6Policy")
    permitted_workload_classes: tuple[str, ...] = Field(
        alias="permittedWorkloadClasses", min_length=1
    )
    security_review_ref: str = Field(alias="securityReviewRef", min_length=1)
    validation_state: Literal["approved"] = Field(alias="validationState")
    max_connections: int = Field(alias="maxConnections", ge=1)
    max_bytes: int = Field(alias="maxBytes", ge=1)
    idle_seconds: int = Field(alias="idleSeconds", ge=1)
    diagnostics_retention_days: int = Field(alias="diagnosticsRetentionDays", ge=1)

    @field_validator("dns_servers")
    @classmethod
    def public_dns_servers(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            address = ipaddress.ip_address(value)
            if not address.is_global:
                raise ValueError("DNS servers must be globally routable")
        return values

    @property
    def ref(self) -> str:
        return f"{self.profile_id}@{self.version}"

    @property
    def digest(self) -> str:
        payload = self.model_dump(by_alias=True, mode="json")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


class EgressAttestation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_ref: str = Field(alias="profileRef")
    profile_digest: str = Field(alias="profileDigest", pattern=r"^sha256:[0-9a-f]{64}$")
    backend_ref: str = Field(alias="backendRef")
    network_ref: str = Field(alias="networkRef")
    enforcer_version: Literal[ENFORCER_IMPLEMENTATION] = Field(alias="enforcerVersion")
    applied_rule_digest: str = Field(alias="appliedRuleDigest", pattern=r"^sha256:[0-9a-f]{64}$")
    validated_at: datetime = Field(alias="validatedAt")


def attestation_from_network_labels(
    *,
    profile: EgressProfile,
    network_ref: str,
    backend_ref: str,
    labels: Mapping[str, str],
    attestation_key: bytes,
) -> EgressAttestation:
    """Validate authenticated reconciler evidence; reject declarative labels."""

    if len(attestation_key) < 32:
        raise ValueError("restricted-egress attestation key must be at least 32 bytes")

    expected = {
        ATTESTATION_LABELS["profile_ref"]: profile.ref,
        ATTESTATION_LABELS["profile_digest"]: profile.digest,
        ATTESTATION_LABELS["enforcer"]: ENFORCER_IMPLEMENTATION,
        ATTESTATION_LABELS["validated"]: "true",
    }
    if any(str(labels.get(key, "")) != value for key, value in expected.items()):
        raise ValueError("network does not carry a current restricted-egress attestation")
    rules_digest = str(labels.get(ATTESTATION_LABELS["rules_digest"], ""))
    validated_at = str(labels.get(ATTESTATION_LABELS["validated_at"], ""))
    signed_fields = (
        profile.ref,
        profile.digest,
        rules_digest,
        ENFORCER_IMPLEMENTATION,
        validated_at,
        network_ref,
        backend_ref,
    )
    expected_signature = hmac.new(
        attestation_key, "\n".join(signed_fields).encode(), hashlib.sha256
    ).hexdigest()
    supplied_signature = str(labels.get(ATTESTATION_LABELS["signature"], ""))
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise ValueError(
            "network does not carry an authenticated restricted-egress attestation"
        )
    return EgressAttestation(
        profileRef=profile.ref,
        profileDigest=profile.digest,
        backendRef=backend_ref,
        networkRef=network_ref,
        enforcerVersion=ENFORCER_IMPLEMENTATION,
        appliedRuleDigest=rules_digest,
        validatedAt=validated_at,
    )
