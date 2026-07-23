"""Validated embedded-mode enablement evidence.

Source issue: MoonLadderStudios/MoonMind#3425.

Configuration carries artifact identifiers only.  This module validates the
versioned claim stored in each artifact; authorization and byte retrieval stay
at the API/service boundary.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Literal, Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from moonmind.omnigent.host_auth_adapter import PINNED_OMNIGENT_COMMIT

EMBEDDED_EVIDENCE_SCHEMA_VERSION = "moonmind.omnigent.embedded-evidence/v1"
EMBEDDED_PROTOCOL_PROFILE = "omnigent.runner_tunnel.538494ff"
_PINNED_IMAGE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")

EmbeddedClaimType = Literal[
    "proxy_conformance", "live_smoke", "host_auth_conformance"
]


class EmbeddedEvidenceError(ValueError):
    """Evidence is unsafe, incompatible, or does not prove enablement."""


class EvidenceOutcome(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    status: Literal["passed"]
    evidence_refs: tuple[str, ...] = Field(..., alias="evidenceRefs", min_length=1)

    @field_validator("evidence_refs")
    @classmethod
    def _refs_are_non_blank(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not ref.strip() for ref in value):
            raise ValueError("evidenceRefs must not contain blank values")
        return value


class EmbeddedEnablementEvidence(BaseModel):
    """One immutable, expiring embedded enablement claim."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    schema_version: Literal[EMBEDDED_EVIDENCE_SCHEMA_VERSION] = Field(
        ..., alias="schemaVersion"
    )
    claim_type: EmbeddedClaimType = Field(..., alias="claimType")
    status: Literal["passed"]
    moonmind_build_identity: str = Field(..., alias="moonmindBuildIdentity")
    bridge_config_sha256: str = Field(
        ..., alias="bridgeConfigSha256", pattern=r"^[0-9a-f]{64}$"
    )
    omnigent_source_commit: Literal[PINNED_OMNIGENT_COMMIT] = Field(
        ..., alias="omnigentSourceCommit"
    )
    protocol_profile: Literal[EMBEDDED_PROTOCOL_PROFILE] = Field(
        ..., alias="protocolProfile"
    )
    images: dict[str, str]
    test_matrix: dict[str, EvidenceOutcome] = Field(..., alias="testMatrix")
    generated_at: datetime = Field(..., alias="generatedAt")
    expires_at: datetime = Field(..., alias="expiresAt")
    superseded_by: str | None = Field(None, alias="supersededBy")
    revoked_at: datetime | None = Field(None, alias="revokedAt")
    secret_scan: Literal["passed"] = Field(..., alias="secretScan")
    cleanup: Literal["passed"]
    producer: str

    @model_validator(mode="after")
    def _is_usable(self) -> "EmbeddedEnablementEvidence":
        if not self.moonmind_build_identity.strip() or not self.producer.strip():
            raise ValueError("build identity and producer are required")
        if self.generated_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("generatedAt and expiresAt must include a timezone")
        if self.expires_at <= self.generated_at:
            raise ValueError("expiresAt must be later than generatedAt")
        if self.revoked_at is not None or self.superseded_by is not None:
            raise ValueError("revoked or superseded evidence cannot unlock embedded mode")
        if not self.test_matrix:
            raise ValueError("testMatrix must contain passing cases")
        if not self.images:
            raise ValueError("immutable server/host images are required")
        for role in ("server", "host"):
            image = self.images.get(role, "")
            if not _PINNED_IMAGE.fullmatch(image):
                raise ValueError(f"{role} image must use an immutable sha256 digest")
        return self


def validate_embedded_evidence(
    payload: bytes | str | Mapping[str, Any],
    *,
    expected_claim_type: EmbeddedClaimType,
    moonmind_build_identity: str,
    bridge_config_sha256: str,
    now: datetime | None = None,
) -> EmbeddedEnablementEvidence:
    """Parse and policy-bind one artifact claim to the running deployment."""

    try:
        raw: Any = payload
        if isinstance(payload, bytes):
            raw = json.loads(payload.decode("utf-8"))
        elif isinstance(payload, str):
            raw = json.loads(payload)
        claim = EmbeddedEnablementEvidence.model_validate(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise EmbeddedEvidenceError(f"malformed embedded evidence: {exc}") from exc
    if claim.claim_type != expected_claim_type:
        raise EmbeddedEvidenceError("evidence claim type does not match configured slot")
    if claim.moonmind_build_identity != moonmind_build_identity:
        raise EmbeddedEvidenceError("evidence is for a different MoonMind build")
    if claim.bridge_config_sha256 != bridge_config_sha256:
        raise EmbeddedEvidenceError("evidence is for a different bridge configuration")
    current = now or datetime.now(timezone.utc)
    if current < claim.generated_at:
        raise EmbeddedEvidenceError("embedded evidence is not yet valid")
    if current >= claim.expires_at:
        raise EmbeddedEvidenceError("embedded evidence has expired")
    return claim


__all__ = [
    "EMBEDDED_EVIDENCE_SCHEMA_VERSION",
    "EMBEDDED_PROTOCOL_PROFILE",
    "EmbeddedEnablementEvidence",
    "EmbeddedEvidenceError",
    "EmbeddedClaimType",
    "validate_embedded_evidence",
]
