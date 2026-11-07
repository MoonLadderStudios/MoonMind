"""Selected New Jersey rule readiness evidence, never regulatory attestation."""

from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Literal

from pydantic import Field

from moonmind.security.frameworks import (
    EvidencePeriod,
    FrameworkAssessmentInput,
    FrameworkContract,
    FrameworkEvidence,
    FrameworkPack,
    FrameworkRequirement,
    FrameworkSource,
    Strings,
    Text,
)

DGE_RULES = "https://www.njoag.gov/about/divisions-and-offices/division-of-gaming-enforcement-home/new-jersey-casino-control-act-and-regulations/"
DGE_LAB = "https://www.njoag.gov/about/divisions-and-offices/division-of-gaming-enforcement-home/slot-laboratory-tsb/"
INTERNET_RULE = "https://www.law.cornell.edu/regulations/new-jersey/N-J-A-C-13-69O-1-2"
NETWORK_RULE = "https://www.law.cornell.edu/regulations/new-jersey/N-J-A-C-13-69D-2-4"
RGS_RULE = "https://www.law.cornell.edu/regulations/new-jersey/N-J-A-C-13-69O-3-1"
REVIEWED_ON = date(2026, 11, 7)
ANNUAL_RULES = {
    "nj.internet.annual": (INTERNET_RULE, "13:69O-1.2(q)"),
    "nj.network.annual": (NETWORK_RULE, "13:69D-2.4(e)"),
    "nj.rgs.annual": (RGS_RULE, "13:69O-3.1(a)(4)"),
}


class RuleSource(FrameworkContract):
    url: Text
    section: Text
    edition: Text
    reviewed_on: date
    status: Literal["reviewed_publication", "Unverified"]
    reason: Text


class NJPack(FrameworkPack):
    # Source metadata participates in the shared artifact content key, so a
    # later source update cannot reuse a preparation with an older source set.
    rule_sources: list[RuleSource]


_REQUIREMENTS = [
    (
        "nj.internet.annual",
        "Internet gaming annual system integrity and security assessment",
        "annual",
        "Casino licensee offering Internet gaming",
        "Supply the independent annual report and all fields required by 13:69O-1.2(q).",
    ),
    (
        "nj.network.annual",
        "Casino network annual system integrity and security assessment",
        "annual",
        "Casino licensee network",
        "Supply the independent annual network report and all fields required by 13:69D-2.4(e).",
    ),
    (
        "nj.rgs.annual",
        "RGS annual system integrity and security assessment",
        "annual",
        "RGS providing content to another Internet gaming operator",
        "Supply the independent annual RGS report with the fields incorporated from 13:69O-1.2(q).",
    ),
    (
        "nj.rgs.internal-controls",
        "RGS internal controls and responsibilities",
        "rgs",
        "RGS providing content to another Internet gaming operator",
        "Supply versioned security, operations and accounting controls, change reviews, responsible personnel and Division approval evidence for the controls that require it.",
    ),
    (
        "nj.rgs.games-releases",
        "Approved games and release records",
        "rgs",
        "RGS",
        "Identify deployed game and release versions, Division approval records, disabled-game notices, and approval evidence for applicable transaction overrides and CDN changes.",
    ),
    (
        "nj.rgs.incidents",
        "Incident monitoring and notification",
        "rgs",
        "RGS",
        "Supply monitoring and incident records with operator and Division notification evidence, or an evidenced statement of no reportable incidents for this period.",
    ),
    (
        "nj.rgs.transactions",
        "Transaction reports and distribution",
        "rgs",
        "RGS",
        "Supply daily game-summary, cumulative pending and void transaction samples, required fields, reconciliation and secure delivery evidence for the casino licensees and Division.",
    ),
    (
        "nj.rgs.integration",
        "Operator integration responsibilities",
        "partner",
        "RGS",
        "Identify approved operator integrations, agreed void procedures, three-calendar-day issue resolutions, responsibility boundaries and operator evidence.",
    ),
    (
        "nj.partner.player-accounts",
        "Partner-owned player-account evidence",
        "partner",
        "Internet gaming casino licensee or RGS",
        "Identify the operator accountable for player accounts, complaints and patron-data permissions, the integration boundary, and scoped operator-owned evidence. Supplier scans cannot establish partner coverage.",
    ),
]
_SOURCES = {
    **ANNUAL_RULES,
    "nj.rgs.internal-controls": (RGS_RULE, "13:69O-3.1(a)(1)-(3)"),
    "nj.rgs.games-releases": (RGS_RULE, "13:69O-3.1(b), (e), (g)"),
    "nj.rgs.incidents": (RGS_RULE, "13:69O-3.1(f)"),
    "nj.rgs.transactions": (RGS_RULE, "13:69O-3.1(h)"),
    "nj.rgs.integration": (RGS_RULE, "13:69O-3.1(c), (d), (h)"),
    "nj.partner.player-accounts": (
        INTERNET_RULE,
        "13:69O-1.2(r), (w); partner evidence boundary",
    ),
}
NJ_DGE_PACK = NJPack(
    id="nj-dge-readiness",
    title="New Jersey DGE readiness",
    edition="Selected NJ rule-source snapshot reviewed 2026-11-07",
    source=FrameworkSource(
        url=DGE_RULES,
        section="Selected 13:69O and 13:69D provisions",
        reviewed_on=REVIEWED_ON,
    ),
    guidance_sources=[DGE_LAB, INTERNET_RULE, NETWORK_RULE, RGS_RULE],
    requirements=[
        FrameworkRequirement(
            id=id,
            title=title,
            category=category,
            applicability=applicability,
            evidence_prompt=prompt,
        )
        for id, title, category, applicability, prompt in _REQUIREMENTS
    ],
    rule_sources=[
        RuleSource(
            url=url,
            section=section,
            edition=edition,
            reviewed_on=REVIEWED_ON,
            status="reviewed_publication",
            reason="LII publication and amendment notes reviewed; this is a selected source snapshot, not certification of the complete current NJ code.",
        )
        for url, section, edition in (
            (
                INTERNET_RULE,
                "13:69O-1.2",
                "LII publication; amendment notes through 50 N.J.R. 617(a), effective 2018-01-16",
            ),
            (
                NETWORK_RULE,
                "13:69D-2.4",
                "LII publication; amendment notes through 49 N.J.R. 3781(a), effective 2017-12-04",
            ),
            (
                RGS_RULE,
                "13:69O-3.1",
                "LII publication; amendment notes through 56 N.J.R. 1182(b), effective 2024-07-01",
            ),
        )
    ]
    + [
        RuleSource(
            url=url,
            section=section,
            edition="Reference checked 2026-11-07; current publication Unverified",
            reviewed_on=REVIEWED_ON,
            status="Unverified",
            reason="Direct retrieval returned HTTP 403; use the DGE rule index and official codification to verify later amendments and applicable technical guidance.",
        )
        for url, section in (
            (DGE_RULES, "DGE rule index and official code access"),
            (DGE_LAB, "Technical Services Bureau guidance"),
        )
    ],
)


class NJSystemInputs(FrameworkContract):
    entity_name: Text | None = None
    entity_type: (
        Literal["casino_licensee", "rgs_content_provider", "unrelated_supplier"] | None
    ) = None
    offers_internet_gaming: bool | None = None
    provides_content_to_other_operator: bool | None = None
    systems: Strings = Field(default_factory=list)
    applicability_basis: Text | None = None
    annual_rules: Strings | None = None


class Assessor(FrameworkContract):
    name: Text | None = None
    affiliation: Text | None = None
    independent: bool | None = None
    independence_ref: Text | None = None


class AnnualAssessment(FrameworkContract):
    requirement_id: Text
    systems: Strings = Field(default_factory=list)
    period: EvidencePeriod | None = None
    scope: Text | None = None
    assessors: Annotated[list[Assessor], Field(max_length=64)] = Field(
        default_factory=list
    )
    assessed_on: date | None = None
    findings: Strings | None = None
    corrective_actions: Text | None = None
    licensee_response: Text | None = None
    opinion: Text | None = None
    professional_evidence_ref: Text | None = None
    division_approval_ref: Text | None = None
    report_ref: Text | None = None
    submission_ref: Text | None = None


class AnnualAssessmentRecords(FrameworkContract):
    kind: Literal["synthetic", "external"] = "external"
    assessments: Annotated[list[AnnualAssessment], Field(max_length=3)] = Field(
        default_factory=list
    )

    def supporting_refs(self) -> list[str]:
        return [
            ref
            for row in self.assessments
            for ref in (
                row.professional_evidence_ref,
                row.division_approval_ref,
                row.report_ref,
                row.submission_ref,
                *(a.independence_ref for a in row.assessors),
            )
            if ref
        ]


class NJEvidence(FrameworkEvidence):
    division_approval_ref: Text | None = None
    operator_evidence_ref: Text | None = None
    incident_disposition: (
        Literal["events_reported", "no_reportable_incidents"] | None
    ) = None
    notification_ref: Text | None = None

    def supporting_refs(self) -> list[str]:
        return [
            ref
            for ref in (
                self.division_approval_ref,
                self.operator_evidence_ref,
                self.notification_ref,
            )
            if ref
        ]


def _applicability(requirement: str, system: NJSystemInputs) -> tuple[str, str]:
    if (
        requirement in ANNUAL_RULES
        and system.annual_rules is not None
        and requirement not in system.annual_rules
    ):
        return (
            "not_selected",
            "Annual rule not selected; no conclusion about obligations outside this assessment selection.",
        )
    entity = system.entity_type
    if entity is None:
        return "Unverified", "Identify the entity type and its applicability basis."
    if requirement == "nj.network.annual":
        applies = entity == "casino_licensee"
    elif requirement == "nj.internet.annual":
        applies = entity == "casino_licensee" and system.offers_internet_gaming
    elif requirement in {"nj.rgs.annual", "nj.rgs.internal-controls"}:
        applies = (
            entity == "rgs_content_provider"
            and system.provides_content_to_other_operator
        )
    elif requirement == "nj.partner.player-accounts":
        applies = entity == "rgs_content_provider" or (
            entity == "casino_licensee" and system.offers_internet_gaming
        )
    else:
        applies = entity == "rgs_content_provider"
    if applies is None:
        return (
            "Unverified",
            "Record whether the casino offers Internet gaming or the RGS supplies game content to another Internet gaming operator, as applicable.",
        )
    if not applies:
        return (
            "not_applicable",
            "The recorded entity and activity do not meet this selected provision's scope.",
        )
    return (
        "applicable",
        "Recorded entity/activity meets the selected scope; basis: "
        + (
            system.applicability_basis
            or "missing; supply licence, contract and system-boundary evidence"
        ),
    )


def prepare_assessment(
    request: FrameworkAssessmentInput,
    *,
    evidence: list[dict],
    audit: dict | None = None,
) -> dict:
    """Prepare scoped evidence without treating scans or assertions as approval."""
    if request.pack_id != NJ_DGE_PACK.id:
        raise ValueError("Unsupported framework pack")
    system = NJSystemInputs.model_validate(request.system_inputs)
    annual = AnnualAssessmentRecords.model_validate(audit or {})
    records = [NJEvidence.model_validate(row) for row in evidence]
    ids = {r.id for r in NJ_DGE_PACK.requirements}
    unknown = set(request.requirement_choices) - ids
    unknown |= set(system.annual_rules or []) - set(ANNUAL_RULES)
    unknown |= {r.requirement_id for r in annual.assessments} - set(ANNUAL_RULES)
    unknown |= {id for r in records for id in r.requirement_ids} - ids
    if unknown:
        raise ValueError("Unknown NJ requirement: " + ", ".join(sorted(unknown)))
    annual_by_id = {r.requirement_id: r for r in annual.assessments}
    if len(annual_by_id) != len(annual.assessments):
        raise ValueError("Duplicate annual assessment rule")
    if any(not set(r.systems) <= set(system.systems) for r in annual.assessments):
        raise ValueError("Annual assessment systems outside admitted system scope")
    requests, results = [], []

    def missing(id, prompt, *, requirement_id=None, **scope):
        url, section = (
            _SOURCES[requirement_id]
            if requirement_id
            else (DGE_RULES, "Applicability basis")
        )
        requests.append(
            dict(
                id=id,
                prompt=prompt,
                source=url,
                section=section,
                requirementId=requirement_id,
                **scope,
            )
        )

    for field in ("entity_name", "systems", "applicability_basis"):
        if not getattr(system, field):
            missing(
                "system." + field,
                "Identify "
                + field.replace("_", " ")
                + " and retain the supporting licence, contract or system inventory.",
            )

    for requirement in NJ_DGE_PACK.requirements:
        id = requirement.id
        applicability, reason = _applicability(id, system)
        choice = request.requirement_choices.get(id)
        selected = (
            choice.systems if choice and choice.systems is not None else system.systems
        )
        period = choice.period if choice and choice.period else request.period
        if not set(selected) <= set(system.systems):
            raise ValueError("Requirement systems outside admitted system scope")
        start = len(requests)
        matched = []

        def gap(field, prompt):
            missing(
                field,
                prompt,
                requirement_id=id,
                systems=selected,
                period=period.model_dump(mode="json"),
            )

        status = applicability
        if applicability == "Unverified":
            gap("applicability." + id, reason)
        elif applicability == "applicable":
            if choice and choice.not_applicable_reason:
                gap(
                    "requirement." + id,
                    "Review the recorded exclusion against the selected rule; partner ownership does not establish evidence coverage.",
                )
                status = "exclusion_for_review"
            else:
                if not selected:
                    gap(
                        "scope." + id,
                        "Identify at least one admitted system for this requirement.",
                    )
                if id in ANNUAL_RULES:
                    row = annual_by_id.get(id, AnnualAssessment(requirement_id=id))
                    fields = {
                        "scope": row.scope,
                        "systems": bool(selected) and set(selected) <= set(row.systems),
                        "period": row.period and row.period.covers(period),
                        "assessors": row.assessors,
                        "assessedOn": row.assessed_on,
                        "findings": row.findings is not None,
                        "correctiveActions": row.corrective_actions,
                        "licenseeResponse": row.licensee_response,
                        "opinion": row.opinion,
                        "independence": bool(row.assessors)
                        and all(
                            a.independent is True and a.independence_ref
                            for a in row.assessors
                        ),
                        "professionalEvidenceRef": row.professional_evidence_ref,
                        "divisionApprovalRef": row.division_approval_ref,
                        "reportRef": row.report_ref,
                        "submissionRef": row.submission_ref,
                    }
                    for index, assessor in enumerate(row.assessors):
                        fields[f"assessors.{index}.name"] = assessor.name
                        fields[f"assessors.{index}.affiliation"] = assessor.affiliation
                    for field, value in fields.items():
                        if not value:
                            gap(
                                f"annual.{id}.{field}",
                                f"Supply annual assessment {field} for this rule, system scope and period. Record findings explicitly (an empty list means none), corrective actions or why none apply, and the licensee response. Independent-professional, independence, assessor-selection approval and annual submission require evidence artifacts; scans cannot supply the independent opinion.",
                            )
                    if row.report_ref:
                        matched.append(row.report_ref)
                else:
                    for record in records:
                        # Technical assessments and certificates are context,
                        # and generic external audits do not inherit NJ approval.
                        if record.kind != "control_evidence" or record.method not in {
                            "evidence",
                            "observation",
                        }:
                            continue
                        if (
                            id not in record.requirement_ids
                            or not record.period.covers(period)
                        ):
                            continue
                        if (
                            id in {"nj.rgs.internal-controls", "nj.rgs.games-releases"}
                            and not record.division_approval_ref
                        ):
                            continue
                        if (
                            id
                            in {
                                "nj.rgs.transactions",
                                "nj.rgs.integration",
                                "nj.partner.player-accounts",
                            }
                            and not record.operator_evidence_ref
                        ):
                            continue
                        if id == "nj.rgs.incidents" and (
                            not record.incident_disposition
                            or (
                                record.incident_disposition == "events_reported"
                                and not record.notification_ref
                            )
                        ):
                            continue
                        matched.append(record)
                    covered = {s for r in matched for s in r.systems}
                    if not selected or not set(selected) <= covered:
                        gap(
                            "requirement." + id,
                            requirement.evidence_prompt
                            + " Identify evidence owner, artifact, systems, period and provenance; missing approval, notifications and partner evidence remain gaps.",
                        )
                    matched = [
                        r.artifact_ref
                        for r in matched
                        if set(selected) & set(r.systems)
                    ]
                status = "incomplete" if len(requests) > start else "evidence_recorded"
        url, section = _SOURCES[id]
        results.append(
            {
                **requirement.model_dump(mode="json", by_alias=True),
                "source": url,
                "section": section,
                "applicabilityStatus": applicability,
                "reason": reason,
                "status": status,
                "systems": selected,
                "period": period.model_dump(mode="json"),
                "evidenceRefs": matched,
                "exclusionReason": choice.not_applicable_reason if choice else None,
            }
        )
    applicable_annual = [
        r["id"]
        for r in results
        if r["id"] in ANNUAL_RULES and r["applicabilityStatus"] == "applicable"
    ]
    return {
        "schemaVersion": "framework-audit-preparation/v1",
        "pack": NJ_DGE_PACK.model_dump(mode="json", by_alias=True),
        "readiness": "incomplete" if requests else "prepared",
        "systemInputs": system.model_dump(mode="json", by_alias=True),
        "period": request.period.model_dump(mode="json"),
        "annualAssessments": annual.model_dump(mode="json", by_alias=True),
        "requirements": results,
        "evidence": [r.model_dump(mode="json", by_alias=True) for r in records],
        "evidenceRequests": requests,
        "ruleSources": [
            s.model_dump(mode="json", by_alias=True) for s in NJ_DGE_PACK.rule_sources
        ],
        "sourceCompleteness": {
            "status": "Unverified",
            "source": DGE_RULES,
            "reason": "Selected published provisions and amendment notes are versioned here. The complete current official codification and subsequent DGE guidance remain unverified; the historical CHAPTER69O PDF alone cannot establish them.",
        },
        "gliAdoption": {
            "status": "Unverified",
            "source": DGE_LAB,
            "reason": "No GLI edition is assumed adopted by DGE. Lab readiness does not establish Division approval; retain the applicable authority and approval records.",
        },
        "reportingConditions": {
            "annualReportToDivision": bool(applicable_annual),
            "applicableAnnualRules": applicable_annual,
            "submissionPerformed": False,
        },
        "externalAssurance": {
            "status": "Unverified",
            "reason": "Synthetic or supplied preparation records do not establish an independently verified professional assessment, customer/operator verification or DGE approval.",
            "verifiableBy": "Independent professional, licensee/operator evidence owner and New Jersey Division of Gaming Enforcement for their respective records",
        },
    }


def render_assessment(report: dict) -> str:
    parts = [
        "# New Jersey DGE readiness preparation",
        "",
        f"Readiness: {report['readiness']}",
        "Preparation evidence only. Independent professional, operator and Division verification: Unverified. No regulator submission has been performed.",
    ]
    # Include the source editions and every scope, annual field and evidence gap
    # in the existing Markdown export, as well as the structured artifact.
    for title, key in (
        ("Entity and system boundaries", "systemInputs"),
        ("Assessment evidence period", "period"),
        ("Independent annual assessment records", "annualAssessments"),
        ("Requirement results and applicability", "requirements"),
        ("Recorded supporting evidence", "evidence"),
        ("Missing evidence and next actions", "evidenceRequests"),
        ("Rule-source editions", "ruleSources"),
        ("Source completeness", "sourceCompleteness"),
        ("GLI adoption evidence", "gliAdoption"),
        ("Reporting conditions", "reportingConditions"),
        ("External verification", "externalAssurance"),
    ):
        parts += [
            "",
            "## " + title,
            "",
            "```json",
            json.dumps(report[key], indent=2),
            "```",
        ]
    return "\n".join(parts) + "\n"
