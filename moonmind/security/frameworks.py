"""Shared framework selection, evidence and saved-request contracts."""

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

MAX_PACK_REQUIREMENTS = 512
MAX_CONTROL_REQUIREMENTS = 1024
MAX_FRAMEWORK_PACKS = 16
MAX_CONTROL_EVIDENCE_REFS = 64
MAX_CONTROL_CELLS = 16384


class AssessmentInputError(ValueError):
    """Field-addressable admission diagnostic safe to expose without input values."""


def selected_framework_packs(single, multiple):
    """Normalize both wire shapes at assessment and artifact-grant admission."""
    if not isinstance(multiple, list) or len(multiple) > MAX_FRAMEWORK_PACKS:
        raise AssessmentInputError(
            f"frameworkPacks must be a list of at most {MAX_FRAMEWORK_PACKS} selections"
        )
    if single is not None and multiple:
        raise AssessmentInputError("supply either frameworkPack or frameworkPacks")
    return [single] if single is not None else multiple


Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)
]
Strings = Annotated[list[Text], Field(max_length=256)]
# Declared framework inputs that flow into the control evaluator share its bound.
# Legacy framework narrative fields retain their wider original contract.
AssessmentText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)
]


PACK_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$"
PackIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, pattern=PACK_IDENTIFIER_PATTERN, max_length=128
    ),
]
# Editions can name amendments, such as ISO/IEC 27001 "2022+Amd1:2024".
PACK_EDITION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,127}$"
PackEdition = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, pattern=PACK_EDITION_PATTERN, max_length=128
    ),
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


class OrganisationalSamplingContext(FrameworkContract):
    """Exact facts covered by an attributed documentary sampling review."""

    requirement_id: AssessmentText
    population_artifact_ref: Annotated[
        str, StringConstraints(pattern=r"^art_[A-Za-z0-9_-]+$")
    ]
    population_size: Annotated[int, Field(ge=0, le=1000000000)]
    sampling_rationale: Text
    selected_record_ids: Strings = Field(default_factory=list)
    exceptions: Strings = Field(default_factory=list)


class OrganisationalEvidenceReview(FrameworkContract):
    """Attributed, purpose-specific review, never an independent attestation."""

    sampling_context: OrganisationalSamplingContext | None = None
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


OrganisationalEvidenceCategory = Literal[
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


class OrganisationalEvidenceRequest(FrameworkContract):
    id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
    ]
    category: OrganisationalEvidenceCategory
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


class FrameworkPackIdentity(FrameworkContract):
    """An explicitly selected, retained pack, not a mutable registry alias."""

    artifact_ref: Annotated[str, StringConstraints(pattern=r"^art_[A-Za-z0-9_-]+$")]
    pack_id: PackIdentifier
    edition: PackEdition
    revision: PackIdentifier
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


def _absent(value) -> bool:
    return value is None or value == []


class FrameworkPackSelection(FrameworkPackIdentity):
    """A retained pack artifact, or an installed pack named by exact identity.

    An installed selection omits ``artifactRef`` and ``sha256``; the assessment
    retains the installed bytes as an artifact, so reports keep citing exact
    content. Profile inputs, evidence records and an audit record apply only to
    packs that declare a profile.
    """

    artifact_ref: (
        Annotated[str, StringConstraints(pattern=r"^art_[A-Za-z0-9_-]+$")] | None
    ) = Field(default=None, exclude_if=_absent)
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = Field(
        default=None, exclude_if=_absent
    )
    period: EvidencePeriod
    requirement_choices: Annotated[
        dict[Text, DeclaredRequirementChoice], Field(max_length=MAX_PACK_REQUIREMENTS)
    ] = Field(default_factory=dict)
    previous_assessment_ref: (
        Annotated[str, StringConstraints(pattern=r"^art_[A-Za-z0-9_-]+$")] | None
    ) = None
    profile: Annotated[dict[str, Any], Field(max_length=64)] | None = Field(
        default=None, exclude_if=_absent
    )
    evidence_refs: Annotated[list[Text], Field(max_length=64)] = Field(
        default_factory=list, exclude_if=_absent
    )
    audit_ref: Text | None = Field(default=None, exclude_if=_absent)

    @model_validator(mode="after")
    def artifact_or_installed(self):
        if (self.artifact_ref is None) != (self.sha256 is None):
            raise ValueError(
                "a retained pack needs artifactRef and sha256; omit both for an installed pack"
            )
        return self


def normalize_installed_framework(data):
    """Map a saved ``framework.packId`` for an installed pack to ``frameworkPacks``.

    Saved presets, reruns and schedules keep their legacy shape. This single
    request-boundary step selects the currently installed edition and revision;
    system inputs become profile inputs and ``auditRef`` stays the audit record.
    Saved per-requirement documentary evidence (``requirementEvidence``) moves to
    that requirement's choice, where every pack keeps organisational evidence.
    Any other ``framework`` value, including an invalid one, is left for the
    ordinary model validation to accept or reject.
    """
    if not isinstance(data, dict) or not isinstance(data.get("framework"), dict):
        return data
    from pydantic import ValidationError

    from moonmind.security.framework_registry import INSTALLED_PACK_IDS, installed_pack

    framework = data["framework"]
    if framework.get("packId", framework.get("pack_id")) not in INSTALLED_PACK_IDS:
        return data
    try:
        legacy = FrameworkAssessmentInput.model_validate(framework)
    except ValidationError:
        return data
    pack = installed_pack(legacy.pack_id)[1]
    # A saved shape this pack cannot read is an admission error, not a guess.
    profile, choices = saved_profile_and_choices(
        pack,
        legacy.system_inputs,
        {
            key: choice.model_dump(mode="json", by_alias=True, exclude_none=True)
            for key, choice in legacy.requirement_choices.items()
        },
    )
    selection = {
        "packId": pack.id,
        "edition": pack.edition,
        "revision": pack.revision,
        "period": legacy.period.model_dump(mode="json"),
        "requirementChoices": choices,
        "profile": profile,
        "evidenceRefs": legacy.evidence_refs,
        "auditRef": legacy.audit_ref,
        "previousAssessmentRef": legacy.previous_assessment_ref,
    }
    normalized = {key: value for key, value in data.items() if key != "framework"}
    single = normalized.pop("frameworkPack", normalized.pop("framework_pack", None))
    multiple = normalized.pop("frameworkPacks", normalized.pop("framework_packs", None))
    normalized["frameworkPacks"] = [
        {key: value for key, value in selection.items() if value is not None},
        *([single] if single is not None else []),
        *(multiple if isinstance(multiple, list) else []),
    ]
    return normalized


def saved_profile_and_choices(pack, system_inputs, choices):
    """Saved system inputs as an installed pack's profile and requirement choices.

    Raises ``ValueError`` when the saved shape is malformed.
    """
    profile = dict(system_inputs or {})
    choices = {key: dict(value) for key, value in (choices or {}).items()}
    evidence = profile.pop(
        "requirementEvidence", profile.pop("requirement_evidence", {})
    )
    if not isinstance(evidence, dict):
        raise ValueError("saved requirementEvidence must be an object")
    ids = {row.id for row in pack.requirements}
    leftover = {}
    for key, requests in evidence.items():
        if not isinstance(requests, list):
            raise ValueError("saved requirementEvidence entries must be lists")
        if key in ids:
            choice = choices.setdefault(key, {})
            choice["organisationalEvidence"] = [
                *choice.get("organisationalEvidence", []),
                *requests,
            ]
        else:
            leftover[key] = requests
    from moonmind.security.framework_registry import pack_behavior

    migrate = pack_behavior(pack).saved_inputs
    if migrate is not None:
        profile, choices = migrate(profile, choices, leftover, ids)
    elif leftover:
        # Unknown requirements stay visible to profile validation.
        profile["requirementEvidence"] = leftover
    return profile, choices


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
