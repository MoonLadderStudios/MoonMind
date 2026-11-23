"""SOC 2 period-aware sampling judgement for the installed readiness pack.

Criteria identifiers, topic labels, categories, prompts and sources are pack
data (``packs/soc2-readiness.json``). This behavior keeps the judgement that
cannot be data: a Type 1 assessment is an as-of date and a Type 2 assessment an
operating period; design evidence is always needed, and operating criteria in
a Type 2 period also need occurrence evidence with an attributed review of the
exact population sample. Recorded exceptions stay gaps, and a management
response is never a retest. No CPA examination or opinion is made.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from moonmind.security.frameworks import FrameworkContract, Strings, Text
from moonmind.security.pack_readiness import PackBehavior, RequirementOutcome

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


Subservices = Annotated[list[Subservice], Field(max_length=32)]
Samples = Annotated[dict[Text, SampleContext], Field(max_length=70)]


def _categories(inputs) -> list[str]:
    return list(inputs.categories) if inputs.categories is not None else ["security"]


def _operating(requirement_id: str, assessment_type: str) -> bool:
    """Type 2 criteria that need period operating evidence and a sample."""
    return assessment_type == "type2" and (
        not requirement_id.startswith("DC") or requirement_id in {"DC4", "DC9"}
    )


def _validate(pack, state) -> None:
    inputs = state.inputs
    period = state.period
    if inputs.assessment_type is None:
        raise ValueError("Select the SOC 2 assessment type: type1 or type2")
    categories = inputs.categories
    if categories is not None and (
        not categories or len(set(categories)) != len(categories)
    ):
        raise ValueError("Select each SOC 2 category once")
    if inputs.assessment_type == "type1" and period.start != period.end:
        raise ValueError(
            "Type 1 requires a single as-of date (period.start == period.end)"
        )
    if inputs.assessment_type == "type2" and period.start >= period.end:
        raise ValueError("Type 2 requires a non-empty operating period")
    ids = {row.id for row in pack.requirements}
    supplied = set(inputs.requirement_samples or {}) | {
        key
        for subservice in inputs.subservice_organisations or []
        for key in subservice.evidence_requirement_ids
    }
    if supplied - ids:
        raise ValueError("Unknown SOC 2 requirement")


def _scope_requests(pack, state) -> list[dict]:
    inputs = state.inputs
    requests = []
    if not inputs.boundary_reviewed:
        requests.append(
            {
                "id": "scope.responsibilities",
                "prompt": "Review the service boundary, subservice organisations and complementary customer/provider responsibilities, including explicit absence where none apply.",
            }
        )
    for subservice in inputs.subservice_organisations or []:
        if not subservice.responsibilities or not subservice.evidence_requirement_ids:
            requests.append(
                {
                    "id": "subservice." + subservice.name,
                    "prompt": "Identify agreed subservice responsibilities and the criteria requiring supporting evidence; carve-out is not proof of supplier oversight.",
                }
            )
    return requests


def _evaluate(pack, item):
    from moonmind.security.assessments import AssessmentScope, ControlRequirement
    from moonmind.security.control_assessments import review_organisational_evidence

    state, requirement, choice = item.state, item.requirement, item.choice
    inputs = state.inputs
    assessment_type = inputs.assessment_type
    systems, period = item.systems, item.period
    if not state.period.covers(period):
        raise ValueError("Requirement period exceeds assessment period")
    # A narrowed window is retained but cannot quietly establish readiness
    # for the declared historical service period.
    gaps = []
    if period != state.period:
        gaps.append(
            "Requirement period differs from the declared service period; broader operation remains unassessed."
        )
    if assessment_type == "type1" and period.start != period.end:
        raise ValueError("Type 1 requirement requires an as-of date")
    applicable = requirement.category in {
        "security",
        "description",
        *_categories(inputs),
    }
    reason = "Selected category or common/description criterion."
    if requirement.id == "DC9" and assessment_type == "type1":
        applicable, reason = False, "Period-change description applies to Type 2."
    if not applicable:
        reason = reason if requirement.id == "DC9" else "Category not selected."
    if choice.not_applicable_reason:
        applicable, reason = False, choice.not_applicable_reason
    scoped = (
        ControlRequirement(
            id=requirement.id,
            framework_id=pack.id,
            framework_edition=pack.edition,
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
    org = choice.organisational_evidence
    if len({r.id for r in org}) != len(org):
        raise ValueError("duplicate organisational evidence request identity")
    reviewed = (
        [
            review_organisational_evidence(r, scoped, state.availability, state.now)
            for r in org
        ]
        if scoped
        else []
    )
    accepted = {
        r.id: result for r, result in zip(org, reviewed) if result.state == "accepted"
    }
    purposes = {r.purpose for r in org if r.id in accepted}
    if "design" not in purposes:
        gaps.append("Control design evidence is missing or insufficient.")
    sample = (inputs.requirement_samples or {}).get(requirement.id)
    operating = _operating(requirement.id, assessment_type)
    if operating:
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
        else:
            current = population.revisions[-1]
            sampling = current.review.sampling_context
            expected = {
                "requirement_id": requirement.id,
                "population_artifact_ref": current.artifact_ref,
                "population_size": sample.population_size,
                "sampling_rationale": sample.sampling_rationale,
                "selected_record_ids": sample.selected_record_ids,
                "exceptions": sample.exceptions,
            }
            if sampling is None or sampling.model_dump() != expected:
                gaps.append(
                    "The attributed population review does not cover this exact sampling context and current revision; obtain a review of the population size, selected records, rationale and exceptions."
                )
            if sample.population_size and not sample.selected_record_ids:
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
    gaps = list(dict.fromkeys(gaps))
    status = (
        "not_applicable" if not applicable else "incomplete" if gaps else "evidence_recorded"
    )
    requests = []
    if applicable and gaps:
        requests.append(
            {
                "id": requirement.id,
                "prompt": requirement.evidence_prompt,
                "requirementId": requirement.id,
                "systems": systems,
                "period": period.model_dump(mode="json"),
                "purpose": "occurrence" if operating else "design",
                "gaps": gaps,
            }
        )
    details = {
        "organisationalEvidence": [
            r.model_dump(mode="json", by_alias=True) for r in reviewed
        ],
        "sampling": sample.model_dump(mode="json", by_alias=True) if sample else None,
        "limitations": gaps if applicable else [],
        "contextEvidenceRefs": [
            r.artifact_ref for r in state.records if requirement.id in r.requirement_ids
        ],
    }
    return RequirementOutcome(
        status=status,
        reason=reason,
        evidence_refs=[r.current_ref for r in reviewed if r.current_ref],
        requests=requests,
        details={key: value for key, value in details.items() if value},
    )


def _context(pack, rows, audit, state):
    inputs = state.inputs
    result = {
        "sourceContext": {
            "publicationStatus": pack.publication_status,
            "edition": pack.edition,
            "reviewedOn": pack.source.reviewed_on.isoformat(),
            "license": "Identifiers and original topic summaries only; obtain authorized source material from AICPA.",
            "proposed2027": "Not operative in this pack. The October 2026 exposure draft is not the selected published edition.",
            "licensedSourceContext": inputs.licensed_source_context,
        },
        "headline": [
            "SOC 2 readiness only: no CPA examination was performed, no SOC 2 report was issued and practitioner independence is not established."
        ],
    }
    if state.previous_ref and state.previous is not None:
        prior = state.previous.get("readiness") or {}
        result["reassessment"] = {
            "previousRef": state.previous_ref,
            "previousEdition": state.previous.get("edition"),
            "periodChanged": prior.get("period") != state.period.model_dump(mode="json"),
            "scopeChanged": prior.get("profile")
            != inputs.model_dump(mode="json", by_alias=True),
            "priorEvidenceSufficiencyReevaluated": True,
        }
    return result


SOC2_BEHAVIOR = PackBehavior(
    required_evidence_fields=(),
    profile_types={"soc2.subservices": Subservices, "soc2.samples": Samples},
    validate_profile=_validate,
    scope_requests=_scope_requests,
    evaluate_requirement=_evaluate,
    context=_context,
)
