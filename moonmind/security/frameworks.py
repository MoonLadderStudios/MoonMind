"""Framework packs and scoped evidence on the existing assessment path."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
from pydantic.alias_generators import to_camel

Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)
]
Strings = Annotated[list[Text], Field(max_length=256)]
# Declared framework inputs that flow into the control evaluator share its bound.
# Legacy framework narrative fields retain their wider original contract.
AssessmentText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)
]


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
    summary: Text | None = None
    module: Text | None = None
    sources: Annotated[list[FrameworkSource], Field(max_length=16)] = Field(
        default_factory=list
    )
    evidence_types: Strings = Field(default_factory=list)
    responsibilities: Strings = Field(default_factory=list)


class FrameworkModule(FrameworkContract):
    id: Text
    edition: Text
    published_on: date
    source: FrameworkSource
    applicability: Text


class FrameworkPack(FrameworkContract):
    schema_version: Literal["framework-pack/v1"] = "framework-pack/v1"
    id: Text
    title: Text
    edition: Text
    published_on: date | None = None
    modules: Annotated[list[FrameworkModule], Field(max_length=16)] = Field(
        default_factory=list
    )
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


class OrganisationalEvidenceReview(FrameworkContract):
    """Attributed, purpose-specific review, never an independent attestation."""

    outcome: Literal["accepted", "insufficient"]
    purpose: Literal["design", "adoption", "occurrence"]
    reviewer: AssessmentText
    reason: AssessmentText
    reviewed_at: AwareDatetime
    period: EvidencePeriod
    population: Annotated[list[AssessmentText], Field(min_length=1, max_length=256)]


class OrganisationalEvidenceRevision(FrameworkContract):
    artifact_ref: Annotated[str, StringConstraints(pattern=r"^art_[A-Za-z0-9_-]+$")]
    kind: Literal[
        "document", "operational_record", "management_assertion", "generated_draft"
    ]
    source: AssessmentText
    collected_at: AwareDatetime
    expires_at: AwareDatetime
    period: EvidencePeriod
    population: Annotated[list[AssessmentText], Field(min_length=1, max_length=256)]
    sensitivity: Literal["public", "internal", "confidential", "restricted"]
    review: OrganisationalEvidenceReview | None = None


class OrganisationalEvidenceRequest(FrameworkContract):
    id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
    ]
    category: Literal[
        "risk_register",
        "risk_treatment",
        "statement_of_applicability",
        "policy",
        "training",
        "access_review",
        "incident",
        "supplier",
        "internal_audit",
        "management_review",
        "corrective_action",
    ]
    purpose: Literal["design", "adoption", "occurrence"]
    description: AssessmentText
    owner: AssessmentText
    revisions: Annotated[list[OrganisationalEvidenceRevision], Field(max_length=8)] = (
        Field(default_factory=list)
    )

    @model_validator(mode="after")
    def unique_revisions(self):
        refs = [item.artifact_ref for item in self.revisions]
        if len(refs) != len(set(refs)):
            raise ValueError("replacement must have a new immutable artifact reference")
        return self


class OrganisationalEvidenceResult(FrameworkContract):
    request_id: AssessmentText
    state: Literal["requested", "supplied", "insufficient", "accepted"]
    current_ref: str | None = None
    superseded_refs: list[str] = Field(default_factory=list)
    limitations: list[AssessmentText] = Field(default_factory=list)


class RequirementChoice(FrameworkContract):
    systems: Strings | None = None
    period: EvidencePeriod | None = None
    not_applicable_reason: Text | None = None
    responsible_entity: Literal["assessed_entity", "partner"] = "assessed_entity"
    responsibility_reason: Text | None = None


class DeclaredRequirementChoice(RequirementChoice):
    organisational_evidence: Annotated[
        list[OrganisationalEvidenceRequest], Field(max_length=32)
    ] = Field(default_factory=list)
    applicability: Literal["applicable", "not_applicable", "undetermined"] = (
        "undetermined"
    )
    applicability_reason: AssessmentText | None = None
    not_applicable_reason: AssessmentText | None = None
    responsibility_reason: AssessmentText | None = None


class FrameworkPackSelection(FrameworkContract):
    """An explicitly selected, retained pack, not a mutable registry alias."""

    artifact_ref: Annotated[str, StringConstraints(pattern=r"^art_[A-Za-z0-9_-]+$")]
    pack_id: Text
    edition: Text
    revision: Text
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    period: EvidencePeriod
    requirement_choices: Annotated[
        dict[Text, DeclaredRequirementChoice], Field(max_length=64)
    ] = Field(default_factory=dict)
    previous_assessment_ref: (
        Annotated[str, StringConstraints(pattern=r"^art_[A-Za-z0-9_-]+$")] | None
    ) = None


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
    previous_assessment_ref: Text | None = None


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
    if pack_id == "soc2-readiness":
        from moonmind.security.soc2_readiness import SOC2_PACK

        return SOC2_PACK.model_copy(deep=True)
    if pack_id == "ukgc-rts-security":
        from moonmind.security.ukgc_rts import UKGC_RTS_PACK

        return UKGC_RTS_PACK.model_copy(deep=True)
    if pack_id == "gli-19-readiness":
        from moonmind.security.gli_readiness import GLI_PACK

        return GLI_PACK.model_copy(deep=True)
    if pack_id == "nj-dge-readiness":
        from moonmind.security.nj_dge import NJ_DGE_PACK

        return NJ_DGE_PACK.model_copy(deep=True)
    raise ValueError(f"Unknown framework pack: {pack_id}")


def render_framework_preparation(report: dict) -> str:
    if report["pack"]["id"] == "soc2-readiness":
        from moonmind.security.soc2_readiness import render_assessment

        return render_assessment(report)
    if report["pack"]["id"] == "gli-19-readiness":
        from moonmind.security.gli_readiness import render_assessment

        return render_assessment(report)
    if report["pack"]["id"] == "nj-dge-readiness":
        from moonmind.security.nj_dge import render_assessment

        return render_assessment(report)
    if report["pack"]["id"] == "ukgc-rts-security":
        from moonmind.security.ukgc_rts import render_audit_preparation

        return render_audit_preparation(report)
    raise ValueError("Unknown framework report pack")
