"""Registered judgement for the installed gaming packs that cannot be pack data.

GLI module applicability depends on the assessed entity and product together.
The New Jersey behavior checks independent annual-assessment records and the
approval, operator and incident qualifiers on scoped evidence. Requirement
content, sources and every other conclusion live in the installed pack data.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import Field

from moonmind.security.frameworks import (
    EvidencePeriod,
    FrameworkContract,
    Strings,
    Text,
)
from moonmind.security.pack_readiness import PackBehavior, PackEvidence


def gli_module_applicability(module, profile) -> tuple[str, str]:
    entity, product = profile.get("entityType"), profile.get("productType")
    if not entity or not product:
        return (
            "incomplete",
            "Identify assessed entity and product before selecting modules.",
        )
    if product in {"gaming_device", "event_wagering"}:
        other = "GLI-11" if product == "gaming_device" else "GLI-33"
        return (
            "incomplete",
            f"Use the {other} product profile; shared GSF controls alone cannot establish product readiness.",
        )
    if module.id == "GLI-GSF-3":
        if entity != "ancillary_provider":
            return (
                "not_applicable",
                "GSF-3 covers ancillary vendors, not automatically an operator or RGS.",
            )
        direct = profile.get("directlyAffectsGaming")
        if direct is None:
            return (
                "incomplete",
                "Record whether this ancillary solution directly affects regulated gaming.",
            )
        if direct:
            return (
                "incomplete",
                "GSF-3 ancillary scope does not cover a solution directly affecting regulated gaming.",
            )
        return "applicable", module.applicability
    if module.id in {"GLI-19", "GLI-GSF-5"} and entity == "ancillary_provider":
        return (
            "not_applicable",
            "Ancillary vendor scope excludes direct interactive-gaming product controls; retain partner responsibilities separately.",
        )
    if entity in {"operator", "rgs"} and product != "interactive_gaming":
        return (
            "incomplete",
            "Confirm an interactive-gaming product or select the ancillary-provider scope.",
        )
    return "applicable", module.applicability


GLI_BEHAVIOR = PackBehavior(module_applicability=gli_module_applicability)


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


# Scoped evidence never inherits Division approval, operator coverage or an
# incident disposition; each must be recorded on the evidence itself.
_APPROVAL = {"nj.rgs.internal-controls", "nj.rgs.games-releases"}
_OPERATOR = {"nj.rgs.transactions", "nj.rgs.integration", "nj.partner.player-accounts"}
_INCIDENTS = {"nj.rgs.incidents"}
_ANNUAL_PROMPT = "Supply annual assessment {field} for this rule, system scope and period. Record findings explicitly (an empty list means none), corrective actions or why none apply, and the licensee response. Independent-professional, independence, assessor-selection approval and annual submission require evidence artifacts; scans cannot supply the independent opinion."


def _annual_rules(pack) -> set[str]:
    return {row.id for row in pack.requirements if row.category == "annual"}


def _validate_annual(pack, records: AnnualAssessmentRecords, profile) -> None:
    ids = [row.requirement_id for row in records.assessments]
    unknown = set(ids) - _annual_rules(pack)
    if unknown:
        raise ValueError("Unknown framework requirement: " + ", ".join(sorted(unknown)))
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate annual assessment rule")
    systems = set(profile.get("systems") or [])
    if any(not set(row.systems) <= systems for row in records.assessments):
        raise ValueError("Annual assessment systems outside admitted system scope")


def _annual_gaps(row: AnnualAssessment, selected, period):
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
        and all(a.independent is True and a.independence_ref for a in row.assessors),
        "professionalEvidenceRef": row.professional_evidence_ref,
        "divisionApprovalRef": row.division_approval_ref,
        "reportRef": row.report_ref,
        "submissionRef": row.submission_ref,
    }
    for index, assessor in enumerate(row.assessors):
        fields[f"assessors.{index}.name"] = assessor.name
        fields[f"assessors.{index}.affiliation"] = assessor.affiliation
    return [
        (f"annual.{row.requirement_id}.{field}", _ANNUAL_PROMPT.format(field=field))
        for field, value in fields.items()
        if not value
    ]


def _qualified(record: PackEvidence, requirement_id: str) -> bool:
    # Technical assessments and certificates are context, and generic external
    # audits do not inherit NJ approval.
    if record.kind != "control_evidence" or record.method not in {
        "evidence",
        "observation",
    }:
        return False
    if requirement_id in _APPROVAL and not record.division_approval_ref:
        return False
    if requirement_id in _OPERATOR and not record.operator_evidence_ref:
        return False
    if requirement_id in _INCIDENTS and (
        not record.incident_disposition
        or (
            record.incident_disposition == "events_reported"
            and not record.notification_ref
        )
    ):
        return False
    return True


def _nj_evidence(pack, requirement, selected, period, records, audit):
    gaps = []
    if not selected:
        gaps.append(
            (
                "scope." + requirement.id,
                "Identify at least one admitted system for this requirement.",
            )
        )
    if requirement.id in _annual_rules(pack):
        annual = {
            row.requirement_id: row for row in (audit.assessments if audit else [])
        }
        row = annual.get(
            requirement.id, AnnualAssessment(requirement_id=requirement.id)
        )
        gaps.extend(_annual_gaps(row, selected, period))
        return ([row.report_ref] if row.report_ref else []), gaps
    matched = [
        r
        for r in records
        if requirement.id in r.requirement_ids
        and r.period.covers(period)
        and _qualified(r, requirement.id)
    ]
    covered = {system for r in matched for system in r.systems}
    if not selected or not set(selected) <= covered:
        gaps.append(
            (
                "requirement." + requirement.id,
                requirement.evidence_prompt
                + " Identify evidence owner, artifact, systems, period and provenance; missing approval, notifications and partner evidence remain gaps.",
            )
        )
    return [r.artifact_ref for r in matched if set(selected) & set(r.systems)], gaps


def _nj_context(pack, rows, audit, state=None):
    applicable = [
        key
        for key in sorted(
            _annual_rules(pack), key=[r.id for r in pack.requirements].index
        )
        if rows[key]["applicabilityStatus"] == "applicable"
    ]
    return {
        "annualAssessments": (audit or AnnualAssessmentRecords()).model_dump(
            mode="json", by_alias=True
        ),
        "reportingConditions": {
            "annualReportToDivision": bool(applicable),
            "applicableAnnualRules": applicable,
            "submissionPerformed": False,
        },
    }


def _nj_evidence_refs(record: PackEvidence):
    return [
        ref
        for ref in (
            record.division_approval_ref,
            record.operator_evidence_ref,
            record.notification_ref,
        )
        if ref
    ]


def _nj_record_refs(records: AnnualAssessmentRecords):
    return [
        ref
        for row in records.assessments
        for ref in (
            row.professional_evidence_ref,
            row.division_approval_ref,
            row.report_ref,
            row.submission_ref,
            *(a.independence_ref for a in row.assessors),
        )
        if ref
    ]


NJ_BEHAVIOR = PackBehavior(
    required_evidence_fields=(),
    records_model=AnnualAssessmentRecords,
    validate_records=_validate_annual,
    evaluate_evidence=_nj_evidence,
    evidence_refs=_nj_evidence_refs,
    record_refs=_nj_record_refs,
    context=_nj_context,
)
