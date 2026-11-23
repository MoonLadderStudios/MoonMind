"""ISO/IEC 27001 Statement of Applicability judgement for the installed pack.

Clause and Annex A identifiers, titles, prompts and sources are pack data
(``packs/iso-27001-readiness.json``). This behavior keeps what cannot be data:
risk-based Annex A applicability and its risk-treatment links, the evidence
category and purpose each management-system clause needs, the draft Statement
of Applicability with its content-derived version, risk-treatment actions,
operator-defined additional controls and reassessment against a previous
report. Documentary evidence is reviewed per purpose (design, adoption,
occurrence): a policy draft or management assertion never establishes that an
event happened, and technical records stay supporting context.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from moonmind.security.frameworks import (
    EvidencePeriod,
    FrameworkContract,
    OrganisationalEvidenceRequest,
    RequirementChoice,
    Strings,
    Text,
)
from moonmind.security.pack_readiness import (
    UNRESOLVED,
    PackBehavior,
    RequirementOutcome,
)

# Evidence category and purpose that establish each management-system clause.
MANAGEMENT_EVIDENCE = {
    "4.1": ("risk_register", "design"),
    "4.2": ("risk_register", "design"),
    "4.3": ("policy", "design"),
    "4.4": ("policy", "occurrence"),
    "5.1": ("management_review", "occurrence"),
    "5.2": ("policy", "adoption"),
    "5.3": ("policy", "adoption"),
    "6.1.1": ("risk_register", "design"),
    "6.1.2": ("risk_register", "design"),
    "6.1.3": ("risk_treatment", "adoption"),
    "6.2": ("policy", "adoption"),
    "6.3": ("policy", "occurrence"),
    "7.1": ("policy", "adoption"),
    "7.2": ("training", "occurrence"),
    "7.3": ("training", "occurrence"),
    "7.4": ("policy", "occurrence"),
    "7.5": ("policy", "occurrence"),
    "8.1": ("supplier", "occurrence"),
    "8.2": ("risk_register", "occurrence"),
    "8.3": ("risk_treatment", "occurrence"),
    "9.1": ("management_review", "occurrence"),
    "9.2": ("internal_audit", "occurrence"),
    "9.3": ("management_review", "occurrence"),
    "10.1": ("corrective_action", "occurrence"),
    "10.2": ("corrective_action", "occurrence"),
}
ALL_PURPOSES = ["design", "adoption", "occurrence"]
REASSESSMENT_PROMPT = "Review applicability and evidence after changed risk, scope, period, responsibility agreement or pack edition."


class Risk(FrameworkContract):
    id: Text
    description: Text
    version: Text
    owner: Text | None = None
    assessment: Text | None = None
    treatment: Literal["mitigate", "avoid", "transfer", "accept", "undetermined"] = (
        "undetermined"
    )
    control_ids: Strings = Field(default_factory=list)
    treatment_plan: Text | None = None
    acceptance_reason: Text | None = None
    residual_risk_approval: Text | None = None


def _absent(value) -> bool:
    return value is None or value == []


# A Statement of Applicability decision for an Annex A or additional control.
# An additional (EXTRA.) control is not a pack requirement, so its scope,
# period, responsibility and documentary evidence are recorded on its decision
# instead of in requirementChoices.
class ApplicabilityDecision(FrameworkContract):

    title: Text | None = None
    decision: Literal["include", "exclude", "undetermined"] = "undetermined"
    rationale: Text | None = None
    risk_ids: Strings = Field(default_factory=list)
    implementation_status: Literal[
        "not_started", "planned", "partial", "implemented", "operating", "unknown"
    ] = "unknown"
    owner: Text | None = None
    systems: Strings | None = Field(default=None, exclude_if=_absent)
    period: EvidencePeriod | None = Field(default=None, exclude_if=_absent)
    not_applicable_reason: Text | None = Field(default=None, exclude_if=_absent)
    responsible_entity: Literal["assessed_entity", "partner"] | None = Field(
        default=None, exclude_if=_absent
    )
    responsibility_reason: Text | None = Field(default=None, exclude_if=_absent)
    organisational_evidence: Annotated[
        list[OrganisationalEvidenceRequest], Field(max_length=32)
    ] = Field(default_factory=list, exclude_if=_absent)

    def choice(self) -> RequirementChoice:
        return RequirementChoice(
            systems=self.systems,
            period=self.period,
            not_applicable_reason=self.not_applicable_reason,
            responsible_entity=self.responsible_entity or "assessed_entity",
            responsibility_reason=self.responsibility_reason,
        )


_CHOICE_FIELDS = {
    "systems",
    "period",
    "not_applicable_reason",
    "responsible_entity",
    "responsibility_reason",
    "organisational_evidence",
}
Risks = Annotated[list[Risk], Field(max_length=128)]
Controls = Annotated[dict[Text, ApplicabilityDecision], Field(max_length=160)]


def _annex(pack) -> dict[str, str]:
    # Restricted distribution keeps licensed control titles in the pack artifact.
    restricted = pack.distribution == "restricted"
    return {
        row.id: row.id if restricted else row.title
        for row in pack.requirements
        if row.category == "annex_a"
    }


def _controls(state) -> dict[str, ApplicabilityDecision]:
    return dict(state.inputs.controls or {})


def _risks(state) -> list[Risk]:
    return list(state.inputs.risks or [])


def _validate(pack, state) -> None:
    if not state.systems:
        raise ValueError("ISMS scope must name at least one system")
    annex = _annex(pack)
    risks = _risks(state)
    ids = {risk.id for risk in risks}
    if len(ids) != len(risks):
        raise ValueError("duplicate risk identity")
    for key, decision in _controls(state).items():
        if key not in annex and (not key.startswith("EXTRA.") or not decision.title):
            raise ValueError("additional controls need EXTRA. identifiers and titles")
        if key in annex and decision.model_fields_set & _CHOICE_FIELDS:
            raise ValueError(
                "record Annex A scope, responsibility and evidence in requirementChoices"
            )
        if set(decision.risk_ids) - ids:
            raise ValueError("control links unknown risk")
    known = set(annex) | set(_controls(state))
    if any(set(risk.control_ids) - known for risk in risks):
        raise ValueError("risk references unknown controls")


def review_requirement(
    *,
    requirement_id,
    title,
    pack,
    systems,
    period,
    requests,
    required_purposes,
    state,
):
    """Purpose-specific documentary review through the control evaluator's owner."""
    from moonmind.security.assessments import AssessmentScope, ControlRequirement
    from moonmind.security.control_assessments import review_organisational_evidence

    requirement = ControlRequirement(
        id=requirement_id,
        title=title,
        framework_id=pack.id,
        framework_edition=pack.edition,
        scope=AssessmentScope(assets=systems),
        period=period,
        applicability="applicable",
        applicability_reason="Included in the recorded ISMS scope.",
        objective_basis="Purpose-specific documentary review; technical checks are supporting context.",
        objectives=[],
        organisational_evidence=requests,
    )
    reviews = [
        review_organisational_evidence(r, requirement, state.availability, state.now)
        for r in requests
    ]
    accepted = {
        r.purpose for r, review in zip(requests, reviews) if review.state == "accepted"
    }
    missing = sorted(set(required_purposes) - accepted)
    return {
        "complete": not missing and all(r.state == "accepted" for r in reviews),
        "missingPurposes": missing,
        "reviews": reviews,
    }


def _control_outcome(pack, state, *, key, title, management, choice, supplied):
    """One requirement or additional control, as the legacy ISMS review concluded it."""
    systems = state.systems if choice.systems is None else choice.systems
    if not systems or not set(systems) <= set(state.systems):
        raise ValueError("requirement scope exceeds or empties ISMS scope")
    period = choice.period or state.period
    if not state.period.covers(period):
        raise ValueError("requirement period exceeds assessment period")
    selected = _controls(state).get(key, ApplicabilityDecision())
    risk_links = sorted(
        set(selected.risk_ids) | {r.id for r in _risks(state) if key in r.control_ids}
    )
    applicable = management or selected.decision == "include"
    valid_exclusion = (
        not management
        and selected.decision == "exclude"
        and bool(selected.rationale)
        and not risk_links
    )
    reasons = []
    if management and choice.not_applicable_reason:
        reasons.append(
            "Management-system requirements cannot be excluded from the ISMS readiness assessment."
        )
    if not management:
        if selected.decision == "undetermined":
            reasons.append("Record a risk-based inclusion or exclusion decision.")
        if not selected.rationale:
            reasons.append("Record the inclusion or exclusion rationale.")
        if selected.decision == "exclude" and risk_links:
            reasons.append(
                "A control required by recorded risk treatment cannot be excluded without revising that treatment."
            )
        if choice.not_applicable_reason and not valid_exclusion:
            reasons.append(
                "Requirement choices cannot override a missing or conflicting SoA decision."
            )
    category, purpose = (
        MANAGEMENT_EVIDENCE[key] if management else ("policy", "occurrence")
    )
    if management and any(item.category != category for item in supplied):
        reasons.append(
            f"Supply {category} evidence for this management-system requirement; "
            "a review of another evidence category does not establish it."
        )
    required = [purpose] if management else ALL_PURPOSES
    reviewed = review_requirement(
        requirement_id=key,
        title=title,
        pack=pack,
        systems=systems,
        period=period,
        requests=supplied,
        required_purposes=required,
        state=state,
    )
    requests = []
    if applicable and not supplied:
        for needed in required:
            requests.append(
                {
                    "id": f"evidence.{key}.{needed}",
                    "requirementId": key,
                    "category": category,
                    "purpose": needed,
                    "prompt": f"Supply purpose-specific {needed} evidence for {title}; record owner, period, population and review.",
                }
            )
    elif applicable and not reviewed["complete"]:
        requests.append(
            {
                "id": f"evidence.{key}",
                "requirementId": key,
                "prompt": "Replace stale or insufficient evidence, or complete the scoped purpose-specific review.",
                "missingPurposes": reviewed["missingPurposes"],
            }
        )
    if choice.responsible_entity == "partner" and not choice.responsibility_reason:
        reasons.append(
            "Identify the partner and responsibility agreement; outsourcing does not remove the evidence requirement."
        )
    if reasons:
        requests.append(
            {"id": "soa." + key, "requirementId": key, "prompt": " ".join(reasons)}
        )
    # Technical records are supporting context; they never replace the
    # organisational record a requirement needs.
    technical = [
        record.artifact_ref
        for record in state.records
        if key in record.requirement_ids
        and set(systems) <= set(record.systems)
        and record.period.covers(period)
    ]
    status = (
        "excluded"
        if valid_exclusion and not reasons
        else (
            "evidence_recorded"
            if applicable and not reasons and reviewed["complete"]
            else "incomplete"
        )
    )
    if management:
        reason = (
            "Management-system requirement of the recorded ISMS; it cannot be excluded."
        )
    elif status == "excluded":
        reason = selected.rationale
    elif selected.decision == "include":
        reason = "Included in the draft Statement of Applicability: " + (
            selected.rationale or "rationale not recorded."
        )
    else:
        reason = "No justified Statement of Applicability decision is recorded."
    details = {
        "evidenceReviews": [
            r.model_dump(mode="json", by_alias=True) for r in reviewed["reviews"]
        ],
        "technicalContextRefs": technical,
        "limitations": reasons,
    }
    return RequirementOutcome(
        status=status,
        reason=reason,
        evidence_refs=[r.current_ref for r in reviewed["reviews"] if r.current_ref],
        requests=requests,
        details={key: value for key, value in details.items() if value},
    ), {
        "selected": selected,
        "riskIds": risk_links,
        "systems": systems,
        "period": period,
        "choice": choice,
        "limitations": reasons,
    }


def _evaluate(pack, item):
    outcome, _ = _control_outcome(
        pack,
        item.state,
        key=item.requirement.id,
        title=item.requirement.title,
        management=item.requirement.category == "management_system",
        choice=item.choice,
        supplied=item.choice.organisational_evidence,
    )
    return outcome


def _decision(key, title, status, basis):
    selected = basis["selected"]
    choice = basis["choice"]
    return {
        "controlId": key,
        **selected.model_dump(mode="json", by_alias=True, exclude=_CHOICE_FIELDS),
        "title": title,
        "riskIds": basis["riskIds"],
        "systems": basis["systems"],
        "period": basis["period"].model_dump(mode="json"),
        "responsibleEntity": choice.responsible_entity,
        "responsibilityReason": choice.responsibility_reason,
        "evidenceStatus": status,
        "verification": "Unverified",
    }


def _management_basis(row):
    # Compare effective requirement choices rather than input spelling.
    return {
        "systems": sorted(set(row.get("systems", []))),
        **{
            key: row.get(key)
            for key in (
                "period",
                "responsibleEntity",
                "responsibilityReason",
                "limitations",
            )
        },
    }


_CHOICE_ALIASES = {
    "systems",
    "period",
    "notApplicableReason",
    "responsibleEntity",
    "responsibilityReason",
    "organisationalEvidence",
}


def isms_scope(profile: dict | None) -> dict:
    """The recorded ISMS scope, without additional controls' choices or evidence.

    Choices and evidence revisions change conclusions, not the scope itself.
    """
    scope = dict(profile or {})
    scope["controls"] = {
        key: {k: v for k, v in (value or {}).items() if k not in _CHOICE_ALIASES}
        for key, value in (scope.get("controls") or {}).items()
    }
    return scope


def _pack_identity(pack) -> dict:
    from moonmind.security.framework_packs import _digest

    return {
        "edition": pack.edition,
        "revision": pack.revision,
        "requirementsDigest": _digest(
            pack.model_dump(mode="json", by_alias=True)["requirements"]
        ),
    }


def _context(pack, rows, audit, state):
    from moonmind.security.assessments import content_key

    annex = _annex(pack)
    requirements = {row.id: row for row in pack.requirements}
    decisions, additional, basis_rows = [], {}, {}
    for key, row in rows.items():
        if requirements[key].category == "management_system":
            basis_rows[key] = _management_basis(
                {
                    "systems": row["systems"],
                    "period": row["period"].model_dump(mode="json"),
                    "responsibleEntity": row["responsibleEntity"],
                    "responsibilityReason": row["responsibilityReason"],
                    "limitations": row["details"].get("limitations", []),
                }
            )
            continue
        choice = RequirementChoice(
            systems=row["systems"],
            period=row["period"],
            responsible_entity=row["responsibleEntity"],
            responsibility_reason=row["responsibilityReason"],
        )
        selected = _controls(state).get(key, ApplicabilityDecision())
        decisions.append(
            _decision(
                key,
                annex[key],
                row["status"],
                {
                    "selected": selected,
                    "choice": choice,
                    "systems": row["systems"],
                    "period": row["period"],
                    "riskIds": sorted(
                        set(selected.risk_ids)
                        | {r.id for r in _risks(state) if key in r.control_ids}
                    ),
                },
            )
        )
    for key, decision in _controls(state).items():
        if key in annex:
            continue
        outcome, basis = _control_outcome(
            pack,
            state,
            key=key,
            title=decision.title,
            management=False,
            choice=decision.choice(),
            supplied=decision.organisational_evidence,
        )
        state.requests.extend(outcome.requests)
        decisions.append(_decision(key, decision.title, outcome.status, basis))
        additional[key] = {
            "title": decision.title,
            "status": outcome.status,
            "systems": basis["systems"],
            "period": basis["period"].model_dump(mode="json"),
            "responsibleEntity": basis["choice"].responsible_entity,
            "responsibilityReason": basis["choice"].responsibility_reason,
            "evidenceRefs": outcome.evidence_refs,
            **outcome.details,
        }
    controls = _controls(state)
    treatment = []
    for risk in _risks(state):
        gaps = []
        if not risk.owner or not risk.assessment:
            gaps.append("Record risk owner, assessment basis and evaluation.")
        if risk.treatment == "undetermined" or not risk.treatment_plan:
            gaps.append("Record a treatment decision, plan and completion criteria.")
        if not risk.residual_risk_approval:
            gaps.append("Retain the risk owner's residual-risk decision.")
        if risk.treatment == "accept" and not risk.acceptance_reason:
            gaps.append("Justify acceptance against recorded criteria.")
        if risk.treatment == "mitigate" and not risk.control_ids:
            gaps.append("Select necessary treatment controls.")
        if any(
            controls.get(key, ApplicabilityDecision()).decision != "include"
            for key in risk.control_ids
        ):
            gaps.append("Resolve missing or excluded treatment controls in the SoA.")
        if gaps:
            treatment.append({"riskId": risk.id, "actions": gaps})
    profile = state.inputs.model_dump(mode="json", by_alias=True)
    # The version identifies the SoA content: scope, period and decisions.
    # Supplied evidence revisions change it only through a decision's status.
    scope = isms_scope(profile)
    version = content_key(
        {
            "edition": pack.edition,
            "scope": scope,
            "period": state.period.model_dump(mode="json"),
            "decisions": decisions,
        }
    )
    soa = {
        "status": "draft",
        "version": version,
        "edition": pack.edition,
        "scopeStatement": state.inputs.scope_statement,
        "decisions": decisions,
        "previousAssessmentRef": state.previous_ref,
    }
    unresolved = [
        key
        for key, row in rows.items()
        if row["status"] in UNRESOLVED or row["status"] == "Unverified"
    ] + [key for key, row in additional.items() if row["status"] == "incomplete"]
    result = {
        "statementOfApplicability": soa,
        "riskTreatmentActions": treatment,
        "requirementBasis": basis_rows,
        "unresolvedRequirements": unresolved,
        "headline": [
            f"Draft Statement of Applicability version {version}; certification and actual organisational operation remain Unverified."
        ],
    }
    if additional:
        result["additionalControls"] = additional
    if treatment:
        result["readiness"] = "incomplete"
    if state.previous_ref and state.previous is not None:
        result["reassessment"] = _reassess(
            pack, state, rows, decisions, basis_rows, scope, additional
        )
    return result


def _reassess(pack, state, rows, decisions, basis_rows, scope, additional):
    previous = state.previous
    prior = previous.get("readiness") or {}
    prior_soa = prior.get("statementOfApplicability") or {}
    old = {r["controlId"]: r for r in prior_soa.get("decisions", [])}
    current_identity = _pack_identity(pack)
    global_change = (
        isms_scope(prior.get("profile")) != scope
        or any(previous.get(key) != value for key, value in current_identity.items())
        or prior.get("period") != state.period.model_dump(mode="json")
    )
    controls = [*_annex(pack), *additional]
    affected = (
        sorted(set(old) | set(controls))
        if global_change
        else [r["controlId"] for r in decisions if old.get(r["controlId"]) != r]
    )
    # A management-only scope or period change invalidates that evidence
    # review without changing unrelated Annex A applicability or the SoA.
    old_basis = prior.get("requirementBasis") or {}
    management_changes = {
        key for key, basis in basis_rows.items() if old_basis.get(key) != basis
    }
    known = set(rows) | set(additional)
    affected_requirements = (
        sorted(known) if global_change else sorted(set(affected) | management_changes)
    )
    if affected_requirements:
        state.requests.append(
            {
                "id": "reassessment.applicability",
                "prompt": REASSESSMENT_PROMPT,
                "affectedControlIds": affected,
                "affectedRequirementIds": affected_requirements,
            }
        )
    return {
        "previousRef": state.previous_ref,
        "previousSoAVersion": prior_soa.get("version"),
        "previousEdition": previous.get("edition"),
        "affectedControlIds": affected,
        "affectedRequirementIds": affected_requirements,
        "prompt": "Review affected applicability and evidence; historical SoA decisions remain retained in the previous immutable report.",
    }


def _organisational_refs(inputs):
    return [
        revision.artifact_ref
        for decision in (inputs.controls or {}).values()
        for request in decision.organisational_evidence
        for revision in request.revisions[-1:]
    ]


def _saved_inputs(profile, choices, leftover, ids):
    """Saved additional-control evidence and choices move onto the decision."""
    controls = profile.get("controls")
    controls = dict(controls) if isinstance(controls, dict) else {}
    for key in [key for key in choices if key not in ids]:
        if key in controls:
            choice = choices.pop(key)
            decision = dict(controls[key])
            decision.update(
                {
                    name: value
                    for name, value in choice.items()
                    if name
                    in {
                        "systems",
                        "period",
                        "notApplicableReason",
                        "responsibleEntity",
                        "responsibilityReason",
                    }
                }
            )
            if choice.get("organisationalEvidence"):
                leftover[key] = [
                    *choice["organisationalEvidence"],
                    *leftover.get(key, []),
                ]
            controls[key] = decision
    for key, requests in leftover.items():
        if key not in controls:
            raise ValueError("unknown evidence requirement")
        decision = dict(controls[key])
        decision["organisationalEvidence"] = [
            *decision.get("organisationalEvidence", []),
            *requests,
        ]
        controls[key] = decision
    if controls:
        profile = {**profile, "controls": controls}
    return profile, choices


ISO_BEHAVIOR = PackBehavior(
    required_evidence_fields=(),
    profile_types={"iso-27001.risks": Risks, "iso-27001.controls": Controls},
    validate_profile=_validate,
    evaluate_requirement=_evaluate,
    context=_context,
    organisational_refs=_organisational_refs,
    saved_inputs=_saved_inputs,
)
