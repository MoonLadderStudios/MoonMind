"""Resolved Skills and delivery refs (section 10).

Skill intent resolves to immutable per-run snapshot before plan commitment.
Plan carries only compact refs, not bodies/paths. Retry reuses same refs.
Branch/remediation may select new snapshot via new explicit plan.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ResolvedSkillSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    resolvedSkillSetRef: str = Field(alias="resolvedSkillSetRef")
    resolvedSkillSetDigest: str = Field(alias="resolvedSkillSetDigest")
    skillDeliveryRef: str = Field(alias="skillDeliveryRef")

    @model_validator(mode="after")
    def validate(self) -> "ResolvedSkillSet":
        if not self.resolvedSkillSetRef.startswith("artifact:"):
            raise ValueError("resolvedSkillSetRef must be artifact:")
        if not _DIGEST_RE.fullmatch(self.resolvedSkillSetDigest):
            raise ValueError("resolvedSkillSetDigest must be sha256")
        if not self.skillDeliveryRef.startswith("skill-delivery:sha256:"):
            raise ValueError("skillDeliveryRef must be skill-delivery:sha256:")
        return self


def create_resolved_skillset(
    *,
    skill_bundle_bytes: bytes,
    delivery_metadata: dict[str, Any],
) -> ResolvedSkillSet:
    digest = "sha256:" + hashlib.sha256(skill_bundle_bytes).hexdigest()
    # Delivery ref is digest of normalized delivery metadata
    canonical = json.dumps(delivery_metadata, sort_keys=True, separators=(",", ":"), default=str)
    delivery_digest = hashlib.sha256(canonical.encode()).hexdigest()
    # Use full content digest as artifact ref for collision resistance (full 64 hex, not 8)
    return ResolvedSkillSet.model_validate(
        {
            "resolvedSkillSetRef": f"artifact:{digest}",
            "resolvedSkillSetDigest": digest,
            "skillDeliveryRef": f"skill-delivery:sha256:{delivery_digest}",
        }
    )


def validate_skill_refs_for_plan(refs: dict[str, Any] | ResolvedSkillSet) -> ResolvedSkillSet:
    if isinstance(refs, ResolvedSkillSet):
        return refs
    try:
        return ResolvedSkillSet.model_validate(refs)
    except Exception as exc:
        raise HarnessPlatformError(
            f"invalid resolved skill refs: {exc}",
            code=HarnessPlatformFailure.OMNIGENT_SKILL_SNAPSHOT_UNAVAILABLE,
        ) from exc


def assert_skill_delivery_attestation(
    *,
    planned: ResolvedSkillSet,
    attested_delivery_ref: str,
    attested_digest: str,
) -> None:
    if attested_delivery_ref != planned.skillDeliveryRef:
        raise HarnessPlatformError(
            f"skill delivery mismatch: {attested_delivery_ref} != {planned.skillDeliveryRef}",
            code=HarnessPlatformFailure.OMNIGENT_SKILL_DELIVERY_MISMATCH,
        )
    if attested_digest != planned.resolvedSkillSetDigest:
        raise HarnessPlatformError(
            "skill set digest mismatch",
            code=HarnessPlatformFailure.OMNIGENT_SKILL_DELIVERY_MISMATCH,
        )
