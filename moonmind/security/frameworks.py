"""Framework packs and scoped evidence on the existing assessment path."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic.alias_generators import to_camel

Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)
]
Strings = Annotated[list[Text], Field(max_length=256)]


class FrameworkContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, alias_generator=to_camel
    )


class FrameworkSource(FrameworkContract):
    url: Text
    section: Text
    reviewed_on: date


class FrameworkRequirement(FrameworkContract):
    id: Text
    title: Text
    category: Text
    applicability: Text
    evidence_prompt: Text


class FrameworkPack(FrameworkContract):
    schema_version: Literal["framework-pack/v1"] = "framework-pack/v1"
    id: Text
    title: Text
    edition: Text
    source: FrameworkSource
    guidance_sources: Strings
    requirements: Annotated[
        list[FrameworkRequirement], Field(min_length=1, max_length=256)
    ]


class EvidencePeriod(FrameworkContract):
    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("period end precedes start")
        return self

    def covers(self, other: EvidencePeriod) -> bool:
        return self.start <= other.start and self.end >= other.end


class RequirementChoice(FrameworkContract):
    systems: Strings | None = None
    period: EvidencePeriod | None = None
    not_applicable_reason: Text | None = None


class FrameworkAssessmentInput(FrameworkContract):
    pack_id: Text
    system_inputs: Annotated[dict[str, Any], Field(max_length=64)]
    period: EvidencePeriod
    requirement_choices: Annotated[
        dict[Text, RequirementChoice], Field(max_length=256)
    ] = Field(default_factory=dict)
    evidence_refs: Annotated[list[Text], Field(max_length=64)] = Field(
        default_factory=list
    )
    audit_ref: Text | None = None


class EvidenceProvenance(FrameworkContract):
    source_ref: Text
    issuer: Text
    version: Text
    recorded_on: date
    standard: Text | None = None


class AuditReliance(FrameworkContract):
    reviewed_by: Text
    reason: Text
    review_ref: Text
    valid_for: EvidencePeriod
    equivalence_reason: Text | None = None


class FrameworkEvidence(FrameworkContract):
    kind: Literal[
        "control_evidence", "external_audit", "iso_certificate", "technical_assessment"
    ]
    requirement_ids: Strings
    systems: Strings
    period: EvidencePeriod
    method: Literal["enquiry", "evidence", "observation"]
    description: Text
    provenance: EvidenceProvenance
    reliance: AuditReliance | None = None
    artifact_ref: Text

    def covers(
        self, requirement: str, systems: list[str], period: EvidencePeriod
    ) -> bool:
        if self.kind not in {"control_evidence", "external_audit"}:
            return False
        if requirement not in self.requirement_ids or not set(systems) <= set(
            self.systems
        ):
            return False
        if self.kind == "external_audit":
            # The auditor must explicitly review and accept prior work for this
            # scope/period. Certification or the report's date alone is no proof.
            if not self.reliance or not self.reliance.valid_for.covers(period):
                return False
            if (
                self.provenance.standard != "ISO/IEC 27001:2022"
                and not self.reliance.equivalence_reason
            ):
                return False
        return self.period.covers(period) or (
            self.kind == "external_audit"
            and self.reliance is not None
            and self.reliance.valid_for.covers(period)
        )


def load_framework_pack(pack_id: str) -> FrameworkPack:
    if pack_id == "ukgc-rts-security":
        from moonmind.security.ukgc_rts import UKGC_RTS_PACK

        return UKGC_RTS_PACK.model_copy(deep=True)
    raise ValueError(f"Unknown framework pack: {pack_id}")
