"""Typed adapter for the portable verifier's declared acceptance evidence.

This validates the terminal contract and identity bindings, never collects or
classifies requirements. The resolved moonspec-verify bundle owns those semantics.
Existing gate artifacts carry this value under validatedRefs.acceptance.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Record(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, str_strip_whitespace=True
    )


class AcceptanceSubject(_Record):
    repository: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    content_digest: str = Field(alias="contentDigest", min_length=1)


class AcceptanceTarget(_Record):
    ref: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    content_digest: str = Field(alias="contentDigest", min_length=1)


class AcceptanceScope(_Record):
    source_ref: str = Field(alias="sourceRef", min_length=1)
    source_digest: str = Field(alias="sourceDigest", min_length=1)
    complete: Literal[True]
    requirement_ids: list[str] = Field(alias="requirementIds", min_length=1)


class RequirementEvidence(_Record):
    requirement_id: str = Field(alias="requirementId", min_length=1)
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1)


class EvidenceFreshness(_Record):
    policy: str = Field(min_length=1)
    valid_until: datetime | None = Field(None, alias="validUntil")

    @model_validator(mode="after")
    def timezone_required(self) -> "EvidenceFreshness":
        if self.valid_until is not None and self.valid_until.tzinfo is None:
            raise ValueError("validUntil requires a timezone")
        return self


class AcceptanceEvidence(_Record):
    schema_version: Literal["acceptance/v1"] = Field(alias="schemaVersion")
    subject: AcceptanceSubject
    scope: AcceptanceScope
    completion_target: AcceptanceTarget = Field(alias="completionTarget")
    evidence: list[RequirementEvidence] = Field(min_length=1)
    freshness: EvidenceFreshness

    @model_validator(mode="after")
    def complete_binding(self) -> "AcceptanceEvidence":
        required = self.scope.requirement_ids
        evidenced = [item.requirement_id for item in self.evidence]
        if (
            any(not item.strip() for item in required)
            or len(set(required)) != len(required)
            or len(set(evidenced)) != len(evidenced)
            or set(required) != set(evidenced)
            or any(
                not ref.strip() for item in self.evidence for ref in item.evidence_refs
            )
        ):
            raise ValueError(
                "acceptance evidence must bind every mandatory requirement exactly once"
            )
        return self

    def matches_target(self, *, repository: str, ref: str, revision: str) -> bool:
        """Validate an independently observed target against declared evidence."""
        return (
            self.subject.repository == repository
            and self.completion_target.ref == ref
            and self.completion_target.revision == revision
            and self.subject.content_digest == self.completion_target.content_digest
        )

    def is_current(self, now: datetime) -> bool:
        expiry = self.freshness.valid_until
        return expiry is None or now < expiry


def acceptance_evidence(payload: Mapping[str, Any]) -> AcceptanceEvidence | None:
    """Read a well-formed declaration; legacy verdicts grant no new authority."""
    refs = payload.get("validatedRefs")
    raw = refs.get("acceptance") if isinstance(refs, Mapping) else None
    if not isinstance(raw, Mapping):
        return None
    try:
        return AcceptanceEvidence.model_validate(raw)
    except ValueError:
        return None


async def validate_completion_target(
    payload: Mapping[str, Any],
    *,
    repository: str,
    source_ref: str,
    expected_ref: str = "",
    read_target: Callable[[str, str], Awaitable[Mapping[str, str]]],
) -> str | None:
    """Validate the terminal declaration against the owning live ref reader.

    Return an actionable missing-evidence reason; do not reinterpret requirements,
    rerun tests, publish code, or derive acceptance from an initial assessment.
    """
    evidence = acceptance_evidence(payload)
    if (
        payload.get("verdict") != "FULLY_IMPLEMENTED"
        or payload.get("invalid")
        or payload.get("degraded")
        or evidence is None
    ):
        return "Run objective verification with complete acceptance/v1 evidence before issue completion"
    if not source_ref or evidence.scope.source_ref != source_ref:
        return "Verify the original issue scope; the acceptance source identity does not match"
    if not evidence.is_current(datetime.now(timezone.utc)):
        return "Required acceptance evidence expired; repeat the affected verification"
    try:
        target = await read_target(repository, expected_ref)
    except Exception:
        return "Completion target is unreadable through the authorized repository reader; retry that evidence read"
    if (
        not evidence.matches_target(
            repository=repository,
            ref=target.get("ref", ""),
            revision=target.get("revision", ""),
        )
        or target.get("contentDigest") != evidence.subject.content_digest
    ):
        return "Verify the current completion target or publish the candidate through its required review path"
    return None
