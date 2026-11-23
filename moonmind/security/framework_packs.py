"""Bounded operator-supplied framework intelligence on the control evaluator.

Mappings describe directional relationships, never evidence or objective coverage.
No registry or network retrieval is involved. Artifact access and byte integrity
are the caller's responsibility, including operator-owned source documents.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, StringConstraints, model_validator

from moonmind.security.assessments import ControlObjective
from moonmind.security.frameworks import (
    MAX_PACK_REQUIREMENTS,
    AssessmentText,
    DeclaredRequirementChoice,
    EvidencePeriod,
    FrameworkContract,
    FrameworkRequirement,
    FrameworkSource,
    OrganisationalEvidenceCategory,
    OrganisationalEvidenceRequest,
    PackEdition,
)
from moonmind.security.frameworks import PackIdentifier as Identifier
from moonmind.security.frameworks import Text
from moonmind.security.web_api_methodology import AssessmentProcedure

if TYPE_CHECKING:
    from moonmind.security.assessments import AssessmentScope, ControlAssessmentPlan

MAX_PACK_BYTES = 2 * 1024 * 1024

Identifiers = Annotated[list[Identifier], Field(min_length=1, max_length=64)]
# Requirement IDs keep their published section names, which can contain spaces
# (GLI-GSF-1 "Appendix A"); exported control identities depend on them.
RequirementIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/ -]{0,127}$"
    ),
]
RequirementIdentifiers = Annotated[
    list[RequirementIdentifier], Field(min_length=1, max_length=64)
]
ArtifactRef = Annotated[str, StringConstraints(pattern=r"^art_[A-Za-z0-9_-]+$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
# Catalog editions can name amendments, such as ISO/IEC 27001 "2022+Amd1:2024".
MappingEdition = PackEdition


class PackSource(FrameworkSource):
    """A public HTTP(S) source, or an operator-owned document artifact.

    An artifact source names its exact bytes so a later policy revision is a new
    digest, never a silent change. ``excerptSha256`` fingerprints the cited text.
    """

    # Absent fields stay absent so existing URL packs keep their digests.
    url: Text | None = Field(default=None, exclude_if=lambda value: value is None)
    artifact_ref: ArtifactRef | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    sha256: Sha256 | None = Field(default=None, exclude_if=lambda value: value is None)
    excerpt_sha256: Sha256 | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def validate_location(self):
        if self.artifact_ref is not None:
            if self.url is not None:
                raise ValueError("framework source names either a URL or an artifact")
            if self.sha256 is None:
                raise ValueError("artifact framework source requires its SHA-256")
            return self
        if self.sha256 is not None or self.excerpt_sha256 is not None:
            raise ValueError("only artifact framework sources carry document digests")
        parsed = urlsplit(self.url or "")
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError(
                "framework source requires an HTTP(S) URL without credentials"
            )
        return self


class PackObjective(ControlObjective):
    """Existing evaluator objective with bounded machine identifiers."""

    id: Identifier
    source: Identifier
    tool: Identifier
    rule_ids: Annotated[
        list[Identifier | Literal["*"]], Field(min_length=1, max_length=32)
    ]


class PackEvidenceRequest(FrameworkContract):
    """An organisational evidence request the pack raises for one requirement.

    Compiled into the shared request with the assessment's period. The operator
    supplies revisions under the same ``id`` in ``requirementChoices``; the
    category and purpose stay the pack's.
    """

    id: Identifier
    category: OrganisationalEvidenceCategory
    purpose: Literal["design", "adoption", "occurrence"]
    description: AssessmentText


def _absent(value) -> bool:
    return value is None or value == [] or value == {} or value is False


ProfileName = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z][A-Za-z0-9]{0,63}$")
]
ConditionValue = bool | Identifier | Annotated[list[Identifier], Field(max_length=16)]


class ProfileField(FrameworkContract):
    """One typed profile input a built-in or declared pack needs from the operator.

    ``role`` gives a field generic meaning: ``systems`` bounds requirement scope,
    ``jurisdiction_basis`` records adoption without inferring it from
    publication, ``applicability_basis`` explains why rules apply, and
    ``selection`` lists the selectable requirements the operator chose.

    A ``number`` is a non-negative finite amount. A ``structured`` field holds
    records whose shape the pack's registered behavior declares under ``model``,
    such as ISO/IEC 27001 risks or UKGC critical systems.
    """

    name: ProfileName
    type: Literal[
        "text",
        "boolean",
        "number",
        "date",
        "choice",
        "choices",
        "text_list",
        "jurisdiction_basis",
        "structured",
    ]
    model: Identifier | None = Field(default=None, exclude_if=_absent)
    title: AssessmentText | None = Field(default=None, exclude_if=_absent)
    choices: Annotated[list[RequirementIdentifier], Field(max_length=64)] = Field(
        default_factory=list, exclude_if=_absent
    )
    role: (
        Literal[
            "entity_name",
            "entity_type",
            "product_type",
            "systems",
            "jurisdiction_basis",
            "applicability_basis",
            "selection",
        ]
        | None
    ) = Field(default=None, exclude_if=_absent)
    # A required field that is missing becomes an evidence request, never a guess.
    required: bool = Field(default=False, exclude_if=_absent)
    request_id: Identifier | None = Field(default=None, exclude_if=_absent)
    prompt: Text | None = Field(default=None, exclude_if=_absent)
    # Applicability reason when a rule cannot be decided without this field.
    unknown_reason: Text | None = Field(default=None, exclude_if=_absent)

    @model_validator(mode="after")
    def validate_field(self):
        if (self.type in {"choice", "choices"}) != bool(self.choices):
            raise ValueError("choice profile fields, and only they, list choices")
        if (self.type == "structured") != bool(self.model):
            raise ValueError("structured profile fields, and only they, name a model")
        _unique(self.choices, "profile choice")
        expected = {
            "systems": "text_list",
            "jurisdiction_basis": "jurisdiction_basis",
            "selection": "choices",
        }.get(self.role)
        if expected and self.type != expected:
            raise ValueError(f"{self.role} profile field must have type {expected}")
        if self.required and not (self.request_id and self.prompt):
            raise ValueError("required profile field needs a request ID and prompt")
        return self


class PackProfile(FrameworkContract):
    fields: Annotated[list[ProfileField], Field(min_length=1, max_length=32)]
    request_source: Text
    request_section: Text | None = Field(default=None, exclude_if=_absent)

    @model_validator(mode="after")
    def unique_fields(self):
        _unique([row.name for row in self.fields], "profile field")
        _unique([row.role for row in self.fields if row.role], "profile role")
        return self

    def field(self, role):
        return next((row for row in self.fields if row.role == role), None)


class PackModule(FrameworkContract):
    """A separately edited source, such as a GLI-GSF module or a cited NJ rule."""

    id: Identifier
    edition: Text
    published_on: date | None = Field(default=None, exclude_if=_absent)
    source: PackSource
    applicability: Text | None = Field(default=None, exclude_if=_absent)
    source_status: Literal["reviewed", "Unverified"] = "reviewed"
    status_reason: Text | None = Field(default=None, exclude_if=_absent)

    @model_validator(mode="after")
    def unverified_reason(self):
        if self.source_status == "Unverified" and not self.status_reason:
            raise ValueError("an Unverified module source needs its reason")
        return self


class ExternalClaim(FrameworkContract):
    """An assurance only an outside party can give; it always stays Unverified."""

    id: Identifier
    evidence_type: Identifier | None = Field(default=None, exclude_if=_absent)
    gap: Text
    verifiable_by: Text
    next_step: Text


class ApplicabilityText(FrameworkContract):
    applicable: Text
    basis_missing: Text
    not_applicable: Text
    not_selected: Text
    # Append the requirement's own applicability statement to a not-applicable
    # reason, so each exclusion names the rule that produced it.
    cite_rule: bool = Field(default=False, exclude_if=_absent)


EVIDENCE_TYPES = frozenset(
    {
        "deterministic_check",
        "technical",
        "organizational",
        "organisational",
        "game_math",
        "rng",
        "laboratory",
        "regulator",
        "external_audit",
    }
)


class PackRequirement(FrameworkRequirement):
    id: RequirementIdentifier
    title: AssessmentText
    summary: Text
    sources: Annotated[list[PackSource], Field(max_length=16)] = Field(
        default_factory=list
    )
    subject_types: Annotated[list[Identifier], Field(max_length=8)] = Field(
        default_factory=list, exclude_if=_absent
    )
    # Any listed condition set applies. Each set maps profile fields to an
    # expected value or values; a missing input leaves applicability Unverified.
    applicable_when: Annotated[
        list[
            Annotated[
                dict[ProfileName, ConditionValue], Field(min_length=1, max_length=8)
            ]
        ],
        Field(max_length=8),
    ] = Field(default_factory=list, exclude_if=_absent)
    required_evidence_methods: Annotated[
        list[Literal["deterministic_check", "enquiry", "evidence", "observation"]],
        Field(max_length=4),
    ] = Field(default_factory=list)
    objectives: Annotated[list[PackObjective], Field(max_length=32)] = Field(
        default_factory=list
    )
    objectives_complete: bool = False
    objective_basis: AssessmentText
    procedures: Annotated[list[AssessmentProcedure], Field(max_length=16)] = Field(
        default_factory=list
    )
    # Names a pack external claim: only that outside party can close this
    # requirement, so it declares no MoonMind checks.
    external_claim: Identifier | None = Field(default=None, exclude_if=_absent)
    organisational_evidence: Annotated[
        list[PackEvidenceRequest], Field(max_length=8)
    ] = Field(default_factory=list, exclude_if=_absent)
    # MoonMind has no evidence path for this requirement: it is reported not
    # assessed with this reason, and declares no checks, procedures or requests.
    not_assessed_reason: AssessmentText | None = Field(default=None, exclude_if=_absent)

    @model_validator(mode="after")
    def unique_objectives(self):
        _unique([item.id for item in self.objectives], "objective")
        _unique([item.id for item in self.procedures], "procedure")
        _unique([item.id for item in self.organisational_evidence], "evidence request")
        if self.objectives_complete and not self.objectives:
            raise ValueError("complete objectives require explicit checks")
        if self.external_claim and (
            self.objectives or "deterministic_check" in self.required_evidence_methods
        ):
            raise ValueError("an external-only requirement declares no MoonMind checks")
        if self.not_assessed_reason and (
            self.objectives
            or self.procedures
            or self.organisational_evidence
            or self.external_claim
            or self.required_evidence_methods
        ):
            raise ValueError("a not-assessed requirement declares no evidence path")
        return self


class MappingTarget(FrameworkContract):
    """A destination catalog. A ``reportingLens`` groups report results by its
    requirements, such as OWASP Top 10 categories; it is never concluded on."""

    id: Identifier
    framework_id: Identifier
    edition: MappingEdition
    requirement_ids: RequirementIdentifiers
    titles: Annotated[
        dict[RequirementIdentifier, AssessmentText], Field(max_length=64)
    ] = Field(default_factory=dict, exclude_if=_absent)
    reporting_lens: bool = Field(default=False, exclude_if=_absent)

    @model_validator(mode="after")
    def unique_requirements(self):
        _edition(self.edition)
        _unique(self.requirement_ids, "mapping target requirement")
        if set(self.titles) - set(self.requirement_ids):
            raise ValueError("mapping target titles name unknown requirements")
        return self


RulePattern = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, pattern=r"^[A-Za-z0-9*][A-Za-z0-9_.:/*-]{0,127}$"
    ),
]


class FindingClass(FrameworkContract):
    """Native findings of one source and tool, selected by rule.

    ``rulePatterns`` are shell-style patterns over native rule IDs; Opengrep, for
    example, prefixes rule IDs with its configuration path. ``vulnerabilities``
    keeps only findings marked as known vulnerabilities (CVE-prioritized).
    """

    source: Identifier
    tool: Identifier
    rule_patterns: Annotated[list[RulePattern], Field(min_length=1, max_length=16)]
    vulnerabilities: bool = Field(default=False, exclude_if=_absent)

    def matches(self, finding) -> bool:
        from moonmind.security.exploitation_data import CVE

        if (finding.source, finding.tool) != (self.source, self.tool):
            return False
        if self.vulnerabilities and not (
            finding.priority is not None or CVE.fullmatch(finding.rule_id)
        ):
            return False
        return any(fnmatchcase(finding.rule_id, p) for p in self.rule_patterns)


class FrameworkMapping(FrameworkContract):
    """A directional relation from pack requirements, or from finding classes,
    to a target's requirements. It is reference intelligence, never coverage."""

    id: Identifier
    from_requirement_ids: Annotated[
        list[RequirementIdentifier], Field(max_length=64)
    ] = Field(default_factory=list, exclude_if=_absent)
    from_findings: Annotated[list[FindingClass], Field(max_length=16)] = Field(
        default_factory=list, exclude_if=_absent
    )
    target_id: Identifier
    to_requirement_ids: RequirementIdentifiers
    relation: Literal["equivalent", "partial", "subset", "superset", "related"]
    rationale: Text
    source: PackSource
    basis: Literal["upstream", "evaluated"]
    reviewed_by: Text | None = None
    reviewed_on: date | None = None

    @model_validator(mode="after")
    def review_and_uniqueness(self):
        if bool(self.from_requirement_ids) == bool(self.from_findings):
            raise ValueError("mapping names source requirements or finding classes")
        _unique(self.from_requirement_ids, "mapping source")
        _unique(self.to_requirement_ids, "mapping destination")
        if self.basis == "evaluated" and (not self.reviewed_by or not self.reviewed_on):
            raise ValueError("evaluated mapping requires review attribution")
        return self


def _edition(value):
    if value.casefold() in {"latest", "current", "default", "any"}:
        raise ValueError(
            "framework edition/revision must be explicit, not a mutable selector"
        )


def _unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {label} identity")


class VersionedFrameworkPack(FrameworkContract):
    schema_version: Literal["declared-framework-pack/v1"] = "declared-framework-pack/v1"
    id: Identifier
    edition: PackEdition
    revision: Identifier
    amendments: Annotated[list[Identifier], Field(max_length=32)] = Field(
        default_factory=list
    )
    title: Text
    source: PackSource
    # A draft has not been reviewed yet; every other status keeps attribution.
    reviewed_by: Text | None = None
    reviewed_on: date | None = None
    publication_status: Literal["published", "draft", "superseded"]
    published_on: date | None = None
    effective_from: date | None = None
    effective_until: date | None = None
    provenance: Text
    license: Text
    # Attribution a reproduced requirement text must carry, such as a CC BY-SA
    # notice. Every report of the pack's metadata repeats it.
    attribution: Text | None = Field(default=None, exclude_if=_absent)
    distribution: Literal["redistributable", "restricted", "prohibited"]
    methodology_editions: Annotated[
        dict[AssessmentText, Identifier], Field(max_length=16)
    ] = Field(default_factory=dict)
    requirements: Annotated[
        list[PackRequirement], Field(min_length=1, max_length=MAX_PACK_REQUIREMENTS)
    ]
    mapping_targets: Annotated[list[MappingTarget], Field(max_length=64)] = Field(
        default_factory=list
    )
    mappings: Annotated[list[FrameworkMapping], Field(max_length=256)] = Field(
        default_factory=list
    )
    # Built-in readiness content. Every field is omitted when absent, so packs
    # that do not use it keep their existing content digests.
    sector: Text | None = Field(default=None, exclude_if=_absent)
    jurisdiction: Text | None = Field(default=None, exclude_if=_absent)
    conclusions: Text | None = Field(default=None, exclude_if=_absent)
    profile: PackProfile | None = Field(default=None, exclude_if=_absent)
    modules: Annotated[list[PackModule], Field(max_length=32)] = Field(
        default_factory=list, exclude_if=_absent
    )
    # A judgement that cannot be data, registered behind the standards registry.
    behavior: Identifier | None = Field(default=None, exclude_if=_absent)
    applicability_text: ApplicabilityText | None = Field(
        default=None, exclude_if=_absent
    )
    exclusion_review: bool = Field(default=False, exclude_if=_absent)
    external_claims: Annotated[list[ExternalClaim], Field(max_length=16)] = Field(
        default_factory=list, exclude_if=_absent
    )
    evidence_guidance: Text | None = Field(default=None, exclude_if=_absent)
    limitations: Annotated[list[Text], Field(max_length=8)] = Field(
        default_factory=list, exclude_if=_absent
    )

    @model_validator(mode="after")
    def validate_references(self):
        _edition(self.edition)
        _edition(self.revision)
        _unique(self.amendments, "amendment")
        ids = {row.id for row in self.requirements}
        _unique([row.id for row in self.requirements], "requirement")
        self._validate_profile_content(ids)
        _unique([row.id for row in self.mapping_targets], "mapping target")
        _unique([row.id for row in self.mappings], "mapping")
        # A framework/edition pair has exactly one catalog. Different editions
        # need explicit targets; no lookup guesses an edition from a framework ID.
        _unique(
            [(row.framework_id, row.edition) for row in self.mapping_targets],
            "mapping edition",
        )
        targets = {row.id: row for row in self.mapping_targets}
        for mapping in self.mappings:
            if set(mapping.from_requirement_ids) - ids:
                raise ValueError("mapping names unknown source requirement")
            target = targets.get(mapping.target_id)
            if target is None or set(mapping.to_requirement_ids) - set(
                target.requirement_ids
            ):
                raise ValueError("mapping names unknown target or requirement")
        if self.publication_status != "draft" and (
            not self.reviewed_by or not self.reviewed_on
        ):
            raise ValueError("reviewed pack requires review attribution")
        if self.publication_status == "published" and (
            not self.published_on or not self.effective_from
        ):
            raise ValueError("published pack requires publication and effective dates")
        self.source_documents()
        if (
            self.effective_until
            and self.effective_from
            and self.effective_until <= self.effective_from
        ):
            raise ValueError("effective interval must be ordered")
        return self

    def _validate_profile_content(self, ids):
        modules = {row.id for row in self.modules}
        _unique([row.id for row in self.modules], "module")
        _unique([row.id for row in self.external_claims], "external claim")
        claims = {row.id for row in self.external_claims}
        for row in self.requirements:
            if modules and row.module not in modules:
                raise ValueError("requirement names an undeclared module")
            if row.external_claim and row.external_claim not in claims:
                raise ValueError("requirement names an undeclared external claim")
        fields = {row.name: row for row in self.profile.fields} if self.profile else {}
        selection = self.profile.field("selection") if self.profile else None
        if selection and set(selection.choices) - ids:
            raise ValueError("profile selection names unknown requirements")
        for row in self.requirements:
            for conditions in row.applicable_when:
                for name, expected in conditions.items():
                    field = fields.get(name)
                    if field is None or field.type not in {
                        "boolean",
                        "choice",
                        "choices",
                    }:
                        raise ValueError(
                            "applicability condition names no boolean or choice profile field"
                        )
                    values = expected if isinstance(expected, list) else [expected]
                    if field.type == "boolean" and any(
                        not isinstance(v, bool) for v in values
                    ):
                        raise ValueError(
                            "boolean applicability condition needs true or false"
                        )
                    if field.type != "boolean" and set(values) - set(field.choices):
                        raise ValueError(
                            "applicability condition names an unknown choice"
                        )
        if (
            any(row.applicable_when for row in self.requirements) or selection
        ) and self.applicability_text is None:
            raise ValueError("profile applicability rules need applicabilityText")
        if self.behavior and self.profile is None:
            raise ValueError("a registered behavior needs declared profile inputs")
        if (
            self.profile
            and any(row.type == "structured" for row in self.profile.fields)
            and not self.behavior
        ):
            raise ValueError("structured profile fields need a registered behavior")

    def source_documents(self) -> dict[str, str]:
        """Operator-owned source artifacts and the exact digest each was read at."""
        documents: dict[str, str] = {}
        sources = [
            self.source,
            *(source for row in self.requirements for source in row.sources),
            *(mapping.source for mapping in self.mappings),
            *(module.source for module in self.modules),
        ]
        for source in sources:
            if source.artifact_ref is None:
                continue
            if (
                documents.setdefault(source.artifact_ref, source.sha256)
                != source.sha256
            ):
                raise ValueError("framework source document has conflicting digests")
        return documents


async def verify_source_documents(pack: VersionedFrameworkPack, digest) -> None:
    """Recheck each source document through the caller's artifact owner.

    ``digest(ref)`` returns the SHA-256 of the bytes the caller may read now and
    raises the owner's own error when the artifact is missing or unauthorized.
    """
    for ref, expected in pack.source_documents().items():
        if await digest(ref) != expected:
            raise ValueError(
                f"framework source document {ref} changed; draft a new pack revision"
            )


def parse_declared_pack(payload: bytes) -> VersionedFrameworkPack:
    if len(payload) > MAX_PACK_BYTES:
        raise ValueError("framework pack artifact exceeds the 2 MiB byte bound")

    # Reject duplicate JSON keys rather than silently admitting the last edition.
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate framework pack JSON key")
            result[key] = value
        return result

    return VersionedFrameworkPack.model_validate(
        json.loads(payload, object_pairs_hook=pairs)
    )


def load_declared_pack(
    payload: bytes, *, pack_id: str, edition: str, revision: str
) -> VersionedFrameworkPack:
    pack = parse_declared_pack(payload)
    if (pack.id, pack.edition, pack.revision) != (pack_id, edition, revision):
        raise ValueError(
            "framework pack selection does not match declared identity/edition/revision"
        )
    return pack


def validate_pack_effectivity(
    pack: VersionedFrameworkPack, now: datetime, period: EvidencePeriod | None = None
):
    if pack.distribution == "prohibited":
        raise ValueError("framework pack distribution prohibited; assessment refused")
    name = f"{pack.id} {pack.edition} revision {pack.revision}"
    if pack.publication_status != "published":
        raise ValueError(
            f"framework pack {name} is {pack.publication_status}, not operative"
        )
    today = now.date()
    if pack.published_on > today or pack.effective_from > today:
        raise ValueError(
            f"framework pack {name} publication/effectivity is in the future: published {pack.published_on}, effective from {pack.effective_from}"
        )
    if pack.reviewed_on > today or pack.source.reviewed_on > today:
        raise ValueError("framework pack review is in the future")
    if pack.effective_until and today >= pack.effective_until:
        raise ValueError(
            f"framework pack {name} is no longer effective since {pack.effective_until}"
        )
    if period and (
        period.start < pack.effective_from
        or (pack.effective_until and period.end >= pack.effective_until)
    ):
        raise ValueError("assessment period falls outside pack effective interval")


def compile_framework_plan(
    pack: VersionedFrameworkPack,
    *,
    scope: AssessmentScope,
    period: EvidencePeriod,
    choices: dict,
    now: datetime,
    readiness=None,
) -> tuple[ControlAssessmentPlan, list[str]]:
    """Compile pack requirements into the shared control plan.

    A pack with profile inputs passes its ``evaluate_readiness`` result, whose
    applicability, scope and recorded evidence each requirement carries.
    """
    from moonmind.security.assessments import (
        AssessmentScope,
        ControlAssessmentPlan,
        ControlRequirement,
    )

    validate_pack_effectivity(pack, now, period)
    if set(choices) - {row.id for row in pack.requirements}:
        raise ValueError("choices name unknown framework requirement")
    if (pack.profile is None) != (readiness is None):
        raise ValueError("profile packs, and only they, compile from readiness inputs")
    restricted = pack.distribution == "restricted"
    diagnostics = (
        ["Restricted pack text omitted; retained identity and content digests only."]
        if restricted
        else []
    )
    if readiness is not None:
        return (
            _compile_readiness(
                pack, readiness, choices, scope=scope, restricted=restricted
            ),
            diagnostics
            + [
                (
                    "Readiness requirements conclude from profile inputs, recorded evidence and partial objective checks on named requirements; no requirement is concluded satisfied."
                    if any(row.objectives for row in pack.requirements)
                    else "Readiness requirements conclude from profile inputs and recorded evidence; no executable objective checks are declared, so no requirement is concluded satisfied."
                )
            ],
        )
    rows = []
    for requirement in pack.requirements:
        choice = DeclaredRequirementChoice.model_validate(
            choices.get(requirement.id, {})
        )
        applicability = choice.applicability
        reason = choice.applicability_reason
        if choice.not_applicable_reason:
            if applicability == "applicable":
                raise ValueError("conflicting requirement applicability choices")
            applicability = "not_applicable"
            reason = choice.not_applicable_reason
        if applicability != "undetermined" and not reason:
            raise ValueError("explicit requirement applicability requires a reason")
        assets = scope.assets if choice.systems is None else choice.systems
        if (
            not assets
            or set(assets) - set(scope.assets)
            or set(assets) & set(scope.exclusions)
        ):
            raise ValueError("requirement systems outside admitted scope")
        selected_period = choice.period or period
        if not period.covers(selected_period):
            raise ValueError("requirement period outside admitted period")
        if choice.responsible_entity == "partner" and not choice.responsibility_reason:
            raise ValueError("partner responsibility requires a reason")
        methods_unfulfilled = bool(
            set(requirement.required_evidence_methods) - {"deterministic_check"}
        ) or bool(set(requirement.evidence_types) - {"deterministic_check"})
        if methods_unfulfilled:
            diagnostics.append(
                f"{requirement.id}: declared evidence methods/types require evidence beyond deterministic checks; completeness withheld."
            )
        rows.append(
            ControlRequirement(
                id=requirement.id,
                framework_id=pack.id,
                framework_edition=pack.edition,
                title=requirement.id if restricted else requirement.title,
                scope=AssessmentScope(
                    assets=assets,
                    exclusions=scope.exclusions,
                    repository_exceptions=scope.repository_exceptions,
                ),
                period=selected_period,
                applicability=applicability,
                applicability_reason=reason
                or "Requirement applicability has not been determined for the admitted scope.",
                objectives_complete=requirement.objectives_complete
                and not methods_unfulfilled,
                objective_basis=(
                    "Restricted pack objective basis omitted."
                    if restricted
                    else requirement.objective_basis
                ),
                objectives=_objectives(requirement, restricted),
                organisational_evidence=_organisational_evidence(
                    requirement,
                    choice,
                    applicable=applicability != "not_applicable",
                    restricted=restricted,
                ),
                owner=choice.responsible_entity,
                responsibility_reason=choice.responsibility_reason,
                evidence_plan=evidence_plan(pack, requirement),
            )
        )
    if pack.mappings:
        diagnostics.append(
            "Directional mappings are reference intelligence only; they provide no executed checks or requirement coverage."
        )
    return ControlAssessmentPlan(requirements=rows), diagnostics


def _objectives(requirement, restricted):
    return [
        ControlObjective(
            **dict(
                item.model_dump(),
                description=item.id if restricted else item.description,
            )
        )
        for item in requirement.objectives
    ]


def _organisational_evidence(requirement, choice, *, applicable, restricted):
    """The pack's requests for an applicable requirement, plus the operator's.

    A request the operator supplies under a pack request ID replaces it with its
    revisions, but keeps the pack's category and purpose.
    """
    supplied = {item.id: item for item in choice.organisational_evidence}
    requests = []
    for template in requirement.organisational_evidence if applicable else []:
        item = supplied.pop(template.id, None)
        if item is None:
            item = OrganisationalEvidenceRequest(
                id=template.id,
                category=template.category,
                purpose=template.purpose,
                description=template.id if restricted else template.description,
                owner=(
                    "Partner"
                    if choice.responsible_entity == "partner"
                    else "Assessed entity"
                ),
            )
        elif (item.category, item.purpose) != (template.category, template.purpose):
            raise ValueError(
                f"evidence request {template.id} keeps the pack's category and purpose"
            )
        requests.append(item)
    return requests + list(supplied.values())


def evidence_plan(pack, requirement):
    """The requirement's declared evidence methods and who can close it."""
    from moonmind.security.assessments import RequirementEvidencePlan

    claim = next(
        (row for row in pack.external_claims if row.id == requirement.external_claim),
        None,
    )
    return RequirementEvidencePlan(
        methods=requirement.required_evidence_methods
        or (["deterministic_check"] if requirement.objectives else []),
        evidence_types=requirement.evidence_types,
        checks=len(requirement.objectives),
        procedures=[item.id for item in requirement.procedures],
        external_claim=claim and claim.id,
        external_party=claim and claim.verifiable_by,
        not_assessed_reason=requirement.not_assessed_reason,
    )


def _compile_readiness(pack, readiness, choices, *, scope, restricted):
    from moonmind.security.assessments import (
        AssessmentScope,
        ControlAssessmentPlan,
        ControlRequirement,
        RequirementReadiness,
    )
    from moonmind.security.pack_readiness import APPLICABLE, UNDETERMINED

    rows = []
    for requirement in pack.requirements:
        row = readiness.rows[requirement.id]
        status = row["status"]
        choice = DeclaredRequirementChoice.model_validate(
            choices.get(requirement.id, {})
        )
        # Requirement rows need a population; an empty profile system list is
        # already an evidence request, so the admitted scope stands in for it.
        assets = row["systems"] or scope.assets
        if set(assets) - set(scope.assets) or set(assets) & set(scope.exclusions):
            raise ValueError("requirement systems outside admitted scope")
        rows.append(
            ControlRequirement(
                id=requirement.id,
                framework_id=pack.id,
                framework_edition=pack.edition,
                title=requirement.id if restricted else requirement.title,
                scope=AssessmentScope(
                    assets=assets,
                    exclusions=scope.exclusions,
                    repository_exceptions=scope.repository_exceptions,
                ),
                period=row["period"],
                applicability=(
                    "applicable"
                    if status in APPLICABLE
                    else "undetermined" if status in UNDETERMINED else "not_applicable"
                ),
                applicability_reason=(
                    "Restricted pack applicability reason omitted."
                    if restricted
                    else row["reason"]
                    or "Requirement applicability has not been determined for the admitted scope."
                ),
                # Readiness objectives are partial evidence for the named
                # requirement; they never complete it.
                objectives_complete=False,
                objective_basis=(
                    "Restricted pack objective basis omitted."
                    if restricted
                    else requirement.objective_basis
                ),
                objectives=_objectives(requirement, restricted),
                organisational_evidence=_organisational_evidence(
                    requirement,
                    choice,
                    applicable=status in APPLICABLE or status in UNDETERMINED,
                    restricted=restricted,
                ),
                owner=row["responsibleEntity"],
                responsibility_reason=row["responsibilityReason"],
                readiness=RequirementReadiness(
                    status=status,
                    systems=row["systems"],
                    evidence_refs=row["evidenceRefs"],
                    exclusion_reason=row["exclusionReason"],
                ),
                evidence_plan=evidence_plan(pack, requirement),
            )
        )
    return ControlAssessmentPlan(requirements=rows)


def readiness_metadata(pack, readiness) -> dict:
    """Per-framework readiness context retained beside the pack metadata."""
    context = dict(readiness.context)
    context["requirementStatuses"] = {
        key: row["status"] for key, row in readiness.rows.items()
    }
    if pack.distribution == "restricted":
        # Licensed prompts and claim text stay in the authorized pack artifact.
        context["evidenceRequests"] = [
            {key: value for key, value in row.items() if key != "prompt"}
            for row in context["evidenceRequests"]
        ]
        context["externalClaims"] = {
            key: {k: v for k, v in claim.items() if k not in {"gap", "nextStep"}}
            for key, claim in context["externalClaims"].items()
        }
        context["limitations"] = []
    return context


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def retained_pack_metadata(
    pack: VersionedFrameworkPack, *, artifact_ref: str, sha256: str, now: datetime
) -> dict:
    validate_pack_effectivity(pack, now)
    # Restricted source text stays in its authorized artifact. Digests preserve
    # comparison without copying that text into portable report metadata.
    data = pack.model_dump(mode="json", by_alias=True)
    result = {
        "schemaVersion": pack.schema_version,
        "id": pack.id,
        "edition": pack.edition,
        "revision": pack.revision,
        "amendments": pack.amendments,
        "artifactRef": artifact_ref,
        "sha256": sha256,
        "distribution": pack.distribution,
        "publicationStatus": pack.publication_status,
        "publishedOn": data["publishedOn"],
        "effectiveFrom": data["effectiveFrom"],
        "effectiveUntil": data["effectiveUntil"],
        "reviewedOn": data["reviewedOn"],
        "requirementsDigest": _digest(data["requirements"]),
        "mappingsDigest": _digest([data["mappingTargets"], data["mappings"]]),
        "provenanceDigest": _digest(
            [data["source"], data["provenance"], data["reviewedBy"], data["license"]]
            + ([data["attribution"]] if pack.attribution else [])
        ),
        "textOmitted": pack.distribution == "restricted",
    }
    if pack.attribution:
        result["attribution"] = pack.attribution
    if pack.profile is not None:
        result["profileDigest"] = _digest(data["profile"])
    if documents := pack.source_documents():
        # Identity and digest only: a later policy revision is detectable even
        # when restricted distribution omits every title and summary.
        result["sourceDocuments"] = [
            {"artifactRef": ref, "sha256": sha}
            for ref, sha in sorted(documents.items())
        ]

    if pack.distribution == "redistributable":
        result.update(
            {
                key: data[key]
                for key in (
                    "title",
                    "source",
                    "reviewedBy",
                    "provenance",
                    "license",
                    "requirements",
                    "mappingTargets",
                    "mappings",
                )
            }
        )
    else:
        result["omissionReason"] = (
            "Licensed content omitted under restricted distribution; original artifact access remains required."
        )
        result["licenseDigest"] = _digest(data["license"])
    return result


def compare_pack_metadata(current: dict, previous: dict | None) -> list[str]:
    if previous is None:
        return []
    if previous.get("legacySchema"):
        from moonmind.security.legacy_framework_reports import compare_legacy_report

        return compare_legacy_report(current, previous)
    changes = []
    inputs = (current.get("readiness") or {}).get("profile")
    prior = (previous.get("readiness") or {}).get("profile")
    if inputs is not None and prior is not None and inputs != prior:
        changes.append(
            "Framework profile inputs changed; reassess applicability before comparing conclusions."
        )
    keys = (
        "id",
        "edition",
        "revision",
        "amendments",
        "requirementsDigest",
        "mappingsDigest",
        "provenanceDigest",
        "distribution",
        "publicationStatus",
        "publishedOn",
        "effectiveFrom",
        "effectiveUntil",
        "reviewedOn",
        "sha256",
        "sourceDocuments",
        "profileDigest",
    )
    return changes + [
        f"Framework assessment intelligence changed: {key}; this is not remediation evidence."
        for key in keys
        if current.get(key) != previous.get(key)
    ]


def compare_selected_frameworks(packs, history):
    """Match exact editions first. A unique edition change retains its lineage."""
    from collections import Counter

    current_ids = Counter(pack["id"] for pack in packs)
    for ref, previous in history.items():
        readers = [pack for pack in packs if pack.get("previousAssessmentRef") == ref]
        if previous is None:
            for pack in readers:
                pack["previousAssessmentStatus"] = "unavailable"
                pack["diagnostics"].append(
                    "Previous framework assessment is unavailable, invalid or unsealed; assessment-intelligence comparison is Unverified."
                )
            continue

        by_key = {(p["id"], p["edition"]): p for p in previous}
        previous_ids = Counter(p["id"] for p in previous)
        matched = set()
        for pack in packs:
            key = (pack["id"], pack["edition"])
            prior = by_key.get(key)
            if prior is None and current_ids[key[0]] == previous_ids[key[0]] == 1:
                prior = next(p for p in previous if p["id"] == key[0])
            if prior is not None:
                matched.add((prior["id"], prior["edition"]))
                if pack.get("previousAssessmentRef") == ref:
                    pack["previousAssessmentStatus"] = "available"
                    pack["intelligenceChanges"] = compare_pack_metadata(pack, prior)
                    if prior.get("legacySchema"):
                        from moonmind.security.legacy_framework_reports import (
                            render_legacy_summary,
                        )

                        pack["previousReportSummary"] = render_legacy_summary(prior)
        missing = [
            {"id": ident, "edition": edition, "status": "unverified"}
            for ident, edition in by_key
            if (ident, edition) not in matched
        ]
        if missing:
            for pack in readers:
                pack["previousOnlyFrameworks"] = missing
