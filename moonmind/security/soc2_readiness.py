"""Published SOC 2 readiness scope; no examination or independence opinion."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from moonmind.security.assessments import AssessmentScope, ControlRequirement
from moonmind.security.control_assessments import review_organisational_evidence
from moonmind.security.frameworks import (
    FrameworkAssessmentInput,
    FrameworkContract,
    FrameworkEvidence,
    FrameworkPack,
    FrameworkRequirement,
    FrameworkSource,
    OrganisationalEvidenceRequest,
    RequirementChoice,
    Strings,
    Text,
)

TSC_SOURCE = "https://www.aicpa-cima.com/resources/download/2017-trust-services-criteria-with-revised-points-of-focus-2022"
DC_SOURCE = "https://www.aicpa-cima.com/resources/download/get-description-criteria-for-your-organizations-soc-2-r-report"
DRAFT_SOURCE = "https://www.aicpa-cima.com/news/article/aicpa-proposes-update-of-soc-2-r-trust-services-criteria-to-enhance-the-relevance-usability-and-value-of-cpas-reports"
EDITION = "TSC-2017-POF-2022_DC-2018-IG-2022"
Category = Literal[
    "security", "availability", "confidentiality", "processing_integrity", "privacy"
]

# Identifiers and short original topic labels, not licensed criteria text or a
# replacement for the source standard. Common criteria apply in every category.
_GROUPS = [
    ("CC1", 5, "security", "Governance"),
    ("CC2", 3, "security", "Information exchange"),
    ("CC3", 4, "security", "Risk decisions"),
    ("CC4", 2, "security", "Control evaluation"),
    ("CC5", 3, "security", "Control implementation"),
    ("CC6", 8, "security", "Access safeguards"),
    ("CC7", 5, "security", "Operational detection and response"),
    ("CC8", 1, "security", "Change safeguards"),
    ("CC9", 2, "security", "Risk mitigation"),
    ("A1", 3, "availability", "Service resilience"),
    ("C1", 2, "confidentiality", "Confidential information lifecycle"),
    ("PI1", 5, "processing_integrity", "Processing outcomes"),
    ("P1", 1, "privacy", "Privacy disclosures"),
    ("P2", 1, "privacy", "Privacy choices"),
    ("P3", 2, "privacy", "Collection boundaries"),
    ("P4", 3, "privacy", "Personal information lifecycle"),
    ("P5", 2, "privacy", "Individual access"),
    ("P6", 7, "privacy", "Third-party disclosure"),
    ("P7", 1, "privacy", "Information quality"),
    ("P8", 1, "privacy", "Privacy oversight"),
]
_DC = [
    "Service offering",
    "Promised outcomes",
    "System composition",
    "Significant incidents",
    "Criteria and controls",
    "Customer responsibilities",
    "Subservice responsibilities",
    "Excluded criteria",
    "Period changes",
]


def _requirement(identifier, category, topic, url):
    return FrameworkRequirement(
        id=identifier,
        title=f"{identifier}: {topic}",
        category=category,
        summary=f"Prepare a scoped review of {topic.lower()} for {identifier}.",
        applicability="Common criteria apply to all selected categories; other criteria follow the explicit category and scope choices.",
        evidence_prompt=f"Supply design records and, for Type 2, period operating records for {identifier}; identify populations, samples, exceptions and response owners.",
        sources=[
            FrameworkSource(url=url, section=identifier, reviewed_on=date(2026, 11, 9))
        ],
        evidence_types=["organizational"],
        responsibilities=["service organization management"],
    )


SOC2_PACK = FrameworkPack(
    id="soc2-readiness",
    title="SOC 2 readiness",
    edition=EDITION,
    source=FrameworkSource(
        url=TSC_SOURCE, section="TSP 100", reviewed_on=date(2026, 11, 9)
    ),
    guidance_sources=[DC_SOURCE, DRAFT_SOURCE],
    requirements=[
        _requirement(f"{group}.{i}", category, topic, TSC_SOURCE)
        for group, count, category, topic in _GROUPS
        for i in range(1, count + 1)
    ]
    + [
        _requirement(f"DC{i}", "description", topic, DC_SOURCE)
        for i, topic in enumerate(_DC, 1)
    ],
)


class Subservice(FrameworkContract):
    name: Text
    method: Literal["inclusive", "carve_out"]
    services: Text
    responsibilities: Strings = Field(default_factory=list)
    evidence_requirement_ids: Strings = Field(default_factory=list)


class SampleContext(FrameworkContract):
    """Operator-defined sample references, never generated audit samples."""

    population_request_id: Text
    population_size: Annotated[int, Field(ge=0, le=1000000000)]
    sampling_rationale: Text
    selected_record_ids: Strings = Field(default_factory=list)
    exceptions: Strings = Field(default_factory=list)
    management_response: Text | None = None

    @model_validator(mode="after")
    def bounded_selection(self):
        if len(set(self.selected_record_ids)) != len(self.selected_record_ids):
            raise ValueError("duplicate selected population record")
        if len(self.selected_record_ids) > self.population_size:
            raise ValueError("selected records exceed declared population")
        return self


class SOC2SystemInputs(FrameworkContract):
    entity_name: Text | None = None
    systems: Strings = Field(default_factory=list)
    service_boundary: Text | None = None
    service_commitments: Strings = Field(default_factory=list)
    categories: Annotated[list[Category], Field(min_length=1, max_length=5)] = Field(
        default_factory=lambda: ["security"]
    )
    assessment_type: Literal["type1", "type2"]
    edition: Literal["TSC-2017-POF-2022_DC-2018-IG-2022"] = EDITION
    subservice_organisations: Annotated[list[Subservice], Field(max_length=32)] = Field(
        default_factory=list
    )
    complementary_responsibilities: Strings = Field(default_factory=list)
    boundary_reviewed: bool = False
    licensed_source_context: Text | None = None
    requirement_evidence: Annotated[
        dict[
            Text, Annotated[list[OrganisationalEvidenceRequest], Field(max_length=32)]
        ],
        Field(max_length=70),
    ] = Field(default_factory=dict)
    requirement_samples: Annotated[dict[Text, SampleContext], Field(max_length=70)] = (
        Field(default_factory=dict)
    )

    @model_validator(mode="after")
    def unique_categories(self):
        if len(set(self.categories)) != len(self.categories):
            raise ValueError("duplicate category")
        return self


def prepare_assessment(
    request: FrameworkAssessmentInput,
    *,
    evidence,
    now: datetime,
    audit=None,
    evidence_availability=None,
    previous=None,
):
    if request.pack_id != SOC2_PACK.id or audit is not None:
        raise ValueError(
            "SOC 2 readiness uses scoped evidence, not an examination auditRef"
        )
    system = SOC2SystemInputs.model_validate(request.system_inputs)
    if system.assessment_type == "type1" and request.period.start != request.period.end:
        raise ValueError(
            "Type 1 requires a single as-of date (period.start == period.end)"
        )
    if system.assessment_type == "type2" and request.period.start >= request.period.end:
        raise ValueError("Type 2 requires a non-empty operating period")
    ids = {r.id for r in SOC2_PACK.requirements}
    supplied_ids = (
        set(request.requirement_choices)
        | set(system.requirement_evidence)
        | set(system.requirement_samples)
    )
    supplied_ids |= {
        i for s in system.subservice_organisations for i in s.evidence_requirement_ids
    }
    if supplied_ids - ids:
        raise ValueError("Unknown SOC 2 requirement")
    records = [FrameworkEvidence.model_validate(row) for row in evidence]
    if {i for r in records for i in r.requirement_ids} - ids:
        raise ValueError("Unknown SOC 2 evidence requirement")
    requests, rows = [], []

    def missing(key, prompt, **context):
        requests.append({"id": key, "prompt": prompt, **context})

    for field in ("entity_name", "systems", "service_boundary", "service_commitments"):
        if not getattr(system, field):
            missing(
                "scope." + field,
                "Supply the service's " + field.replace("_", " ") + ".",
            )
    if not system.boundary_reviewed:
        missing(
            "scope.responsibilities",
            "Review the service boundary, subservice organisations and complementary customer/provider responsibilities, including explicit absence where none apply.",
        )
    for subservice in system.subservice_organisations:
        if not subservice.responsibilities or not subservice.evidence_requirement_ids:
            missing(
                "subservice." + subservice.name,
                "Identify agreed subservice responsibilities and the criteria requiring supporting evidence; carve-out is not proof of supplier oversight.",
            )
    availability = evidence_availability or {}
    for requirement in SOC2_PACK.requirements:
        choice = request.requirement_choices.get(requirement.id, RequirementChoice())
        systems = system.systems if choice.systems is None else choice.systems
        period = choice.period or request.period
        if not set(systems) <= set(system.systems):
            raise ValueError("Requirement systems exceed service boundary")
        if not request.period.covers(period):
            raise ValueError("Requirement period exceeds assessment period")
        # A narrowed window is retained but cannot quietly establish readiness
        # for the declared historical service period.
        gaps = []
        if period != request.period:
            gaps.append(
                "Requirement period differs from the declared service period; broader operation remains unassessed."
            )
        if system.assessment_type == "type1" and period.start != period.end:
            raise ValueError("Type 1 requirement requires an as-of date")
        applicable = requirement.category in {
            "security",
            "description",
            *system.categories,
        }
        reason = "Selected category or common/description criterion."
        if requirement.id == "DC9" and system.assessment_type == "type1":
            applicable, reason = False, "Period-change description applies to Type 2."
        if not applicable:
            reason = reason if requirement.id == "DC9" else "Category not selected."
        if choice.not_applicable_reason:
            applicable, reason = False, choice.not_applicable_reason
        scoped = (
            ControlRequirement(
                id=requirement.id,
                framework_id=SOC2_PACK.id,
                framework_edition=EDITION,
                title=requirement.title,
                scope=AssessmentScope(assets=systems),
                period=period,
                applicability="applicable" if applicable else "not_applicable",
                applicability_reason=reason,
                objectives=[],
                objectives_complete=False,
                objective_basis="Readiness documentary review, not exhaustive control testing.",
            )
            if systems
            else None
        )
        org = system.requirement_evidence.get(requirement.id, [])
        if len({r.id for r in org}) != len(org):
            raise ValueError("duplicate organisational evidence request identity")
        reviewed = (
            [review_organisational_evidence(r, scoped, availability, now) for r in org]
            if scoped
            else []
        )
        accepted = {
            r.id: result
            for r, result in zip(org, reviewed)
            if result.state == "accepted"
        }
        purposes = {r.purpose for r in org if r.id in accepted}
        if "design" not in purposes:
            gaps.append("Control design evidence is missing or insufficient.")
        sample = system.requirement_samples.get(requirement.id)
        if system.assessment_type == "type2" and (
            not requirement.id.startswith("DC") or requirement.id in {"DC4", "DC9"}
        ):
            if "occurrence" not in purposes:
                gaps.append(
                    "Operating evidence for the selected period is missing or insufficient."
                )
            population = next(
                (r for r in org if sample and r.id == sample.population_request_id),
                None,
            )
            if (
                not sample
                or not population
                or population.id not in accepted
                or population.purpose != "occurrence"
            ):
                gaps.append(
                    "Supply an accepted period population record and a sampling rationale with selected record identifiers; a scan cannot invent events or populations."
                )
            elif sample.population_size and not sample.selected_record_ids:
                gaps.append(
                    "The declared non-empty population has no selected records."
                )
        if sample and sample.exceptions:
            gaps.append(
                "Recorded exceptions remain unresolved readiness gaps; a management response is not a retest."
            )
            if not sample.management_response:
                gaps.append("Supply the management response to recorded exceptions.")
        for result in reviewed:
            gaps.extend(result.limitations)
        if not systems:
            gaps.append("No assessed systems were supplied.")
        if choice.responsible_entity == "partner" and not choice.responsibility_reason:
            gaps.append("Identify the partner and agreed responsibility.")
        status = (
            "not_applicable"
            if not applicable
            else "unassessed" if gaps else "evidence_recorded"
        )
        if applicable and gaps:
            missing(
                requirement.id,
                requirement.evidence_prompt,
                requirementId=requirement.id,
                systems=systems,
                period=period.model_dump(mode="json"),
                purpose=(
                    "occurrence"
                    if system.assessment_type == "type2"
                    and (
                        not requirement.id.startswith("DC")
                        or requirement.id in {"DC4", "DC9"}
                    )
                    else "design"
                ),
                gaps=list(dict.fromkeys(gaps)),
            )
        rows.append(
            {
                **requirement.model_dump(mode="json", by_alias=True),
                "status": status,
                "applicabilityReason": reason,
                "systems": systems,
                "period": period.model_dump(mode="json"),
                "responsibleEntity": choice.responsible_entity,
                "responsibilityReason": choice.responsibility_reason,
                "organisationalEvidence": [
                    r.model_dump(mode="json", by_alias=True) for r in reviewed
                ],
                "sampling": (
                    sample.model_dump(mode="json", by_alias=True) if sample else None
                ),
                "limitations": list(dict.fromkeys(gaps)) if applicable else [],
                "evidenceRefs": [r.current_ref for r in reviewed if r.current_ref],
                "contextEvidenceRefs": [
                    r.artifact_ref
                    for r in records
                    if requirement.id in r.requirement_ids
                ],
            }
        )
    report = {
        "schemaVersion": "soc2-readiness/v1",
        "pack": SOC2_PACK.model_dump(mode="json", by_alias=True),
        "readiness": "incomplete" if requests else "prepared",
        "systemInputs": system.model_dump(mode="json", by_alias=True),
        "period": request.period.model_dump(mode="json"),
        "requirements": rows,
        "evidenceRequests": requests,
        "sourceContext": {
            "publicationStatus": "published",
            "edition": EDITION,
            "reviewedOn": "2026-11-09",
            "license": "Identifiers and original topic summaries only; obtain authorized source material from AICPA.",
            "proposed2027": "Not operative in this pack. The October 2026 exposure draft is not the selected published edition.",
            "licensedSourceContext": system.licensed_source_context,
        },
        "externalVerification": {
            name: {"status": "Unverified", "gap": gap}
            for name, gap in {
                "examination": "No CPA examination or issued SOC 2 report was performed.",
                "independence": "Practitioner independence has not been established.",
                "organizational": "Attributions and synthetic verification do not establish this organisation's real-world readiness.",
            }.items()
        },
        "limitations": [
            "SOC 2 readiness only, not certification or an issued examination report.",
            "Technical findings and agent recommendations cannot establish design, historical operation, populations or organisational events.",
            "Recorded reviews and sample choices are attributed inputs, not independent assessor conclusions.",
        ],
    }

    if previous is not None:
        if previous.get("schemaVersion") != "soc2-readiness/v1":
            raise ValueError("Previous assessment must be a SOC 2 readiness report")
        report["reassessment"] = {
            "previousRef": request.previous_assessment_ref,
            "previousEdition": previous["pack"]["edition"],
            "periodChanged": previous.get("period") != report["period"],
            "scopeChanged": previous.get("systemInputs") != report["systemInputs"],
            "priorEvidenceSufficiencyReevaluated": True,
        }
    return report


def render_assessment(report):
    from moonmind.security.assessment_reports import _text

    lines = [
        "## SOC 2 readiness",
        f"Edition: {_text(report['pack']['edition'])}",
        f"Readiness: {_text(report['readiness'])}",
        *_text_lines(report["limitations"]),
        "### Service boundary and published source",
        f"Service: {_text(report['systemInputs']['entityName'])}; boundary: {_text(report['systemInputs']['serviceBoundary'])}.",
        f"Commitments: {_text(report['systemInputs']['serviceCommitments'])}; categories: {_text(report['systemInputs']['categories'])}.",
        f"Type: {_text(report['systemInputs']['assessmentType'])}; period: {_text(report['period'])}.",
        f"Subservices: {_text(report['systemInputs']['subserviceOrganisations'])}; complementary responsibilities: {_text(report['systemInputs']['complementaryResponsibilities'])}.",
        f"Source context: {_text(report['sourceContext'])}.",
        f"Reassessment: {_text(report.get('reassessment', 'Initial assessment'))}.",
        "### Assessed and unassessed requirements",
    ]
    for row in report["requirements"]:
        lines += [
            f"- {_text(row['id'])}: {_text(row['status'])}; systems {_text(row['systems'])}; period {_text(row['period'])}.",
            f"  Responsibility: {_text(row['responsibleEntity'])}; {_text(row['responsibilityReason'])}.",
            f"  Evidence: {_text(row['evidenceRefs'])}; technical/context only: {_text(row['contextEvidenceRefs'])}.",
            f"  Sampling and exceptions: {_text(row['sampling'])}.",
            *_text_lines(row["limitations"]),
        ]
        for request in report["systemInputs"]["requirementEvidence"].get(row["id"], []):
            lines.append(
                f"  Documentary request {_text(request['id'])}: {_text(request['purpose'])}; owner {_text(request['owner'])}."
            )
            for revision in request["revisions"][-1:]:
                lines.append(
                    f"  Current source: {_text(revision['source'])}; collected {_text(revision['collectedAt'])}; expires {_text(revision['expiresAt'])}; sensitivity {_text(revision['sensitivity'])}; attributed review {_text(revision['review'])}."
                )
    lines += ["### Evidence requests and remediation"]
    lines += [
        f"- {_text(r['id'])}: {_text(r['prompt'])}" for r in report["evidenceRequests"]
    ]
    lines += [
        "### External verification: Unverified",
        *_text_lines([r["gap"] for r in report["externalVerification"].values()]),
    ]
    return "\n".join(lines)


def _text_lines(values):
    from moonmind.security.assessment_reports import _text

    return ["- " + _text(value) for value in values]


# Shared built-in preparation contract.
SystemInputs = SOC2SystemInputs
