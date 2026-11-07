"""UKGC RTS section 4 audit preparation; never regulatory attestation."""

from __future__ import annotations

import calendar
import json
from datetime import date, timedelta
from typing import Annotated, Literal

from pydantic import Field

from moonmind.security.frameworks import (
    FrameworkAssessmentInput,
    FrameworkContract,
    FrameworkEvidence,
    FrameworkPack,
    FrameworkRequirement,
    FrameworkSource,
    Strings,
    Text,
)

RTS_SOURCE = "https://www.gamblingcommission.gov.uk/manual/remote-gambling-and-software-technical-standards/4-remote-gambling-and-software-technical-standards-rts-security-requirements"
AUDIT_ADVICE = "https://www.gamblingcommission.gov.uk/licensees-and-businesses/guide/security-audit-advice"
ANNUAL_AUDIT_SOURCE = "https://www.gamblingcommission.gov.uk/strategy/testing-strategy-for-compliance-with-remote-gambling-and-software-technical/7-third-party-annual-security-audit"
SECURITY_GUIDANCE = "https://www.gamblingcommission.gov.uk/licensees-and-businesses/page/technical-standards-security-requirements"

# Explicit selection, not all Annex A controls and not the old 2013 mapping.
_CONTROLS = {
    "organisational": {
        "5.1": "Security policies",
        "5.10": "Acceptable use",
        "5.15": "Access control",
        "5.16": "Identity management",
        "5.17": "Authentication information",
        "5.18": "Access rights",
        "5.19": "Supplier security",
        "5.20": "Supplier agreements",
        "5.21": "ICT supply chain",
        "5.22": "Supplier service oversight",
        "5.23": "Cloud service security",
        "5.24": "Incident preparation",
        "5.25": "Security event assessment",
        "5.26": "Incident response",
        "5.28": "Evidence collection",
        "5.35": "Independent security review",
    },
    "people": {
        "6.3": "Security education",
        "6.5": "Employment changes",
        "6.7": "Remote work",
        "6.8": "Event reporting",
    },
    "physical": {
        "7.8": "Equipment protection",
        "7.10": "Storage media",
        "7.14": "Equipment disposal and reuse",
    },
    "technological": {
        "8.1": "Endpoint devices",
        "8.2": "Privileged access",
        "8.3": "Access restriction",
        "8.5": "Secure authentication",
        "8.7": "Malware protection",
        "8.13": "Backups",
        "8.15": "Logging",
        "8.17": "Clock synchronisation",
        "8.18": "Privileged utilities",
        "8.20": "Network security",
        "8.21": "Network services",
        "8.22": "Network segregation",
        "8.24": "Cryptography",
        "8.25": "Secure development lifecycle",
        "8.26": "Application security requirements",
        "8.27": "Secure architecture",
        "8.29": "Development and acceptance security testing",
        "8.30": "Outsourced development",
        "8.31": "Environment separation",
        "8.32": "Change management",
        "8.33": "Test information",
    },
}
_PROMPTS = {
    "organisational": "Supply policies, approvals, reviews and supplier/incident records with document versions, dates and responsible staff.",
    "people": "Supply training, personnel-change and remote-work records, and staff interview samples.",
    "physical": "Supply site, equipment and media records and inspection or remote walkthrough observations.",
    "technological": "Supply configuration, development and test records with versions, dates and examined samples.",
}
UKGC_RTS_PACK = FrameworkPack(
    id="ukgc-rts-security",
    title="UKGC RTS security-audit readiness",
    edition="ISO/IEC 27001:2022",
    source=FrameworkSource(url=RTS_SOURCE, section="4", reviewed_on=date(2026, 11, 7)),
    guidance_sources=[AUDIT_ADVICE, ANNUAL_AUDIT_SOURCE, SECURITY_GUIDANCE],
    requirements=[
        FrameworkRequirement(
            id=key,
            title=title,
            category=category,
            applicability="RTS critical systems; exclusions require a recorded reason and remain visible for auditor review.",
            evidence_prompt=_PROMPTS[category],
        )
        for category, controls in _CONTROLS.items()
        for key, title in controls.items()
    ],
)
CATEGORIES = (
    "customer_information",
    "outcome_rng",
    "gambling_state",
    "entry_exit",
    "communications",
)
ANNUAL_LICENCES = {
    "remote_general_betting",
    "remote_betting_host_real",
    "remote_betting_host_virtual",
    "remote_pool_betting",
    "remote_betting_intermediary",
    "remote_bingo",
    "remote_game_host_bingo",
    "remote_casino",
    "remote_game_host_casino",
}
LOTTERY_LICENCES = {
    "remote_lottery",
    "remote_external_lottery_manager",
    "remote_society_lottery",
}


class CriticalSystem(FrameworkContract):
    category: Literal[
        "customer_information",
        "outcome_rng",
        "gambling_state",
        "entry_exit",
        "communications",
    ]
    systems: Strings = Field(default_factory=list)
    rationale: Text
    in_scope: bool


class ThirdParty(FrameworkContract):
    name: Text
    systems: Strings
    responsibilities: Text
    operator_responsibilities: Text


class UKGCSystemInputs(FrameworkContract):
    operator_name: Text | None = None
    business_description: Text | None = None
    licence_class: Text | None = None
    annual_lottery_sales_gbp: (
        Annotated[float, Field(ge=0, allow_inf_nan=False)] | None
    ) = None
    licence_granted_on: date | None = None
    operations_started_on: date | None = None
    first_audit_extension_approved: bool = False
    regulator_due_on: date | None = None
    last_audit_on: date | None = None
    confirmation_requested_on: date | None = None
    report_requested_on: date | None = None
    report_received_on: date | None = None
    system_identification: Text | None = None
    critical_systems: Annotated[list[CriticalSystem], Field(max_length=5)] = Field(
        default_factory=list
    )
    third_parties: Annotated[list[ThirdParty], Field(max_length=64)] = Field(
        default_factory=list
    )


class Auditor(FrameworkContract):
    name: Text | None = None
    firm: Text | None = None
    background: Text | None = None
    qualifications: Strings = Field(default_factory=list)
    independent: bool | None = None
    independence_ref: Text | None = None


class AuditFinding(FrameworkContract):
    requirement_id: Text
    classification: Literal[
        "fully_compliant", "observation", "minor_nonconformity", "major_nonconformity"
    ]
    description: Text
    management_response: Text | None = None
    action_owner: Text | None = None
    target_date: date | None = None


class AuditRecord(FrameworkContract):
    kind: Literal["synthetic", "external"] = "external"
    auditor: Auditor = Field(default_factory=Auditor)
    audited_on: date | None = None
    locations: Strings = Field(default_factory=list)
    executive_summary: Text | None = None
    overall_opinion: Text | None = None
    findings: Annotated[list[AuditFinding], Field(max_length=256)] = Field(
        default_factory=list
    )


def _months(value: date | None, count: int) -> str | None:
    if value is None:
        return None
    year, month = divmod(value.year * 12 + value.month - 1 + count, 12)
    return date(
        year, month + 1, min(value.day, calendar.monthrange(year, month + 1)[1])
    ).isoformat()


def _days(value: date | None, count: int) -> str | None:
    return (value + timedelta(days=count)).isoformat() if value else None


def prepare_audit(
    request: FrameworkAssessmentInput,
    *,
    evidence: list[dict],
    audit: dict | None = None,
) -> dict:
    """Evaluate recorded coverage, preserving missing external audit evidence."""
    if request.pack_id != UKGC_RTS_PACK.id:
        raise ValueError("Unsupported framework pack")
    system = UKGCSystemInputs.model_validate(request.system_inputs)
    record = AuditRecord.model_validate(audit or {})
    records = [FrameworkEvidence.model_validate(item) for item in evidence]
    ids = {r.id for r in UKGC_RTS_PACK.requirements}
    unknown = set(request.requirement_choices) - ids
    unknown |= {f.requirement_id for f in record.findings} - ids
    if unknown:
        raise ValueError("Unknown RTS requirement: " + ", ".join(sorted(unknown)))
    requests = []

    def missing(key, prompt, **scope):
        requests.append({"id": key, "prompt": prompt, "source": AUDIT_ADVICE, **scope})

    licence = system.licence_class
    if licence in ANNUAL_LICENCES:
        annual = "applicable"
    elif licence in LOTTERY_LICENCES:
        annual = (
            "incomplete"
            if system.annual_lottery_sales_gbp is None
            else (
                "applicable"
                if system.annual_lottery_sales_gbp > 250000
                else "not_required"
            )
        )
    elif licence in {"software_supplier", "telephone_only", "trading_room"}:
        annual = "not_required"
    else:
        annual = "unsupported" if licence else "incomplete"
    if annual in {"incomplete", "unsupported"}:
        missing(
            "licence",
            "Select a listed remote operator/host licence and record annual lottery sales where relevant; unknown classes cannot establish annual-audit applicability.",
        )
    categories = [item.category for item in system.critical_systems]
    if len(categories) != len(set(categories)):
        raise ValueError("Duplicate critical-system category")
    scope_complete = True
    for category in CATEGORIES:
        item = next(
            (s for s in system.critical_systems if s.category == category), None
        )
        if item is None or (item.in_scope and not item.systems):
            scope_complete = False
            missing(
                "scope." + category,
                f"Identify {category} systems, boundaries and third-party responsibility, or record why the category is outside scope.",
            )
    systems = sorted(
        {s for item in system.critical_systems if item.in_scope for s in item.systems}
    )
    if not systems:
        scope_complete = False
        missing(
            "scope.systems",
            "Identify at least one critical system before claiming audit coverage.",
        )
    for field in ("operator_name", "business_description", "system_identification"):
        if not getattr(system, field):
            missing(
                "system." + field,
                "Record " + field.replace("_", " ") + " for the audit report.",
            )
    for third in system.third_parties:
        if not set(third.systems) <= set(systems):
            raise ValueError("Third-party systems outside critical-system scope")
    results = []
    for requirement in UKGC_RTS_PACK.requirements:
        choice = request.requirement_choices.get(requirement.id)
        selected = choice.systems if choice and choice.systems is not None else systems
        period = choice.period if choice and choice.period else request.period
        if not set(selected) <= set(systems):
            raise ValueError("Requirement systems outside critical-system scope")
        matched = []
        methods_by_system = {system_id: set() for system_id in selected}
        for item in records:
            covered = [
                system_id
                for system_id in selected
                if item.covers(requirement.id, [system_id], period)
            ]
            if covered:
                matched.append(item)
                for system_id in covered:
                    methods_by_system[system_id].add(item.method)
        methods = (
            set.intersection(*methods_by_system.values())
            if methods_by_system
            else set()
        )
        missing_methods = sorted({"enquiry", "evidence", "observation"} - methods)
        excluded = bool(choice and choice.not_applicable_reason)
        status = (
            "evidence_recorded"
            if not missing_methods and selected and scope_complete
            else "incomplete"
        )
        if excluded:
            status = "exclusion_for_review"
            missing(
                "requirement." + requirement.id,
                "Have the independent auditor review the recorded exclusion; selected RTS elements remain in the report.",
                requirementId=requirement.id,
            )
        elif status == "incomplete":
            missing(
                "requirement." + requirement.id,
                f"{requirement.id} {requirement.title}: {requirement.evidence_prompt} Record missing methods: {', '.join(missing_methods) or 'verified system boundaries'}.",
                requirementId=requirement.id,
                systems=selected,
                period=period.model_dump(mode="json"),
                methods=missing_methods,
            )
        results.append(
            {
                **requirement.model_dump(mode="json", by_alias=True),
                "status": status,
                "systems": selected,
                "period": period.model_dump(mode="json"),
                "methods": sorted(methods),
                "evidenceRefs": [e.artifact_ref for e in matched],
                "exclusionReason": choice.not_applicable_reason if choice else None,
                "findings": [
                    f.model_dump(mode="json", by_alias=True)
                    for f in record.findings
                    if f.requirement_id == requirement.id
                ],
            }
        )
    for field in ("name", "firm", "background", "qualifications"):
        if not getattr(record.auditor, field):
            missing(
                "auditor." + field,
                "Supply the independent auditor's "
                + field
                + " and suitability to assess ISO/IEC 27001:2022.",
            )
    if record.auditor.independent is not True or not record.auditor.independence_ref:
        missing(
            "auditor.independence",
            "Supply an independence statement with evidence; auditor name or qualifications alone do not establish independence.",
        )
    for field in ("audited_on", "locations", "executive_summary", "overall_opinion"):
        if not getattr(record, field):
            missing(
                "audit." + field,
                "Record audit "
                + field.replace("_", " ")
                + ". Observation may use documented remote inspection techniques.",
            )
    for i, finding in enumerate(record.findings):
        if finding.classification != "fully_compliant" and not all(
            (finding.management_response, finding.action_owner, finding.target_date)
        ):
            missing(
                f"finding.{i}.management",
                "Record a management response, action owner and target date.",
                requirementId=finding.requirement_id,
            )
    first_start = (
        system.operations_started_on
        if system.first_audit_extension_approved
        else system.licence_granted_on
    )
    first = _months(first_start, 6)
    annual_due = _months(system.last_audit_on, 12)
    if annual == "applicable" and not system.regulator_due_on:
        missing(
            "schedule.due",
            "Record the Commission-set audit due date; calculated dates are planning targets only. A delayed first audit requires an approved application.",
        )
    if annual == "applicable" and not first_start:
        missing(
            "schedule.start",
            "Record the licence grant date or the approved first-audit extension and operations start date.",
        )
    return {
        "schemaVersion": "framework-audit-preparation/v1",
        "pack": UKGC_RTS_PACK.model_dump(mode="json", by_alias=True),
        "readiness": "incomplete" if requests else "prepared",
        "applicability": {
            "annualAudit": annual,
            "scope": "complete" if scope_complete else "incomplete",
            "licenceClass": licence,
            "sources": [AUDIT_ADVICE, ANNUAL_AUDIT_SOURCE],
            "note": "Software-only suppliers are not listed annual-audit operators. RTS security obligations and third-party responsibilities still need review. Audit advice includes real-event operators absent from the strategy footnote.",
        },
        "systemInputs": system.model_dump(mode="json", by_alias=True),
        "period": request.period.model_dump(mode="json"),
        "schedule": {
            "firstAuditTarget": first,
            "annualAuditTarget": annual_due,
            "effectiveDueOn": (
                system.regulator_due_on.isoformat() if system.regulator_due_on else None
            ),
            "firstReportTarget": (
                _days(system.regulator_due_on, 7) if not system.last_audit_on else None
            ),
            "confirmationResponseTarget": _days(system.confirmation_requested_on, 7),
            "requestedReportTarget": _days(system.report_requested_on, 7),
        },
        "reportingConditions": {
            "retainFullReport": True,
            "requestedReportWithinDays": 7,
            "annualConfirmationWithinDays": 7,
            "majorNonconformity": "Audit advice requires notification and the full report without delay; the testing strategy also specifies within seven days of receipt. Include explanations and management responses.",
            "majorNonconformityOuterTarget": (
                _days(system.report_received_on, 7)
                if any(
                    f.classification == "major_nonconformity" for f in record.findings
                )
                else None
            ),
            "isoTransition": "Record certification start, stage, accredited body and award date with no gap in audit coverage; agree the certification-cycle reminders with the Commission.",
            "source": AUDIT_ADVICE,
            "submissionPerformed": False,
        },
        "audit": record.model_dump(mode="json", by_alias=True),
        "requirements": results,
        "evidence": [r.model_dump(mode="json", by_alias=True) for r in records],
        "evidenceRequests": requests,
        "externalAudit": {
            "status": "Unverified",
            "reason": (
                "Synthetic preparation records do not constitute an independent audit or private inspection."
                if record.kind == "synthetic"
                else "Supplied records are preparation evidence; MoonMind has not independently verified an external audit or private inspection."
            ),
            "verifiableBy": "Qualified independent auditor and operator evidence owner",
        },
    }


def render_audit_preparation(report: dict) -> str:
    """Render all preparation data, including gaps, without asserting compliance."""
    parts = [
        "# UKGC RTS audit preparation",
        "",
        f"Readiness: {report['readiness']}",
        "Preparation evidence only. Independent audit and private inspection: Unverified.",
        "No regulator submission has been performed.",
    ]
    for title, key in (
        ("Critical-system boundaries and operator", "systemInputs"),
        ("Auditor, methods and management responses", "audit"),
        ("Schedule", "schedule"),
        ("Reporting conditions", "reportingConditions"),
    ):
        parts += [
            "",
            "## " + title,
            "",
            "```json",
            json.dumps(report[key], indent=2),
            "```",
        ]
    parts += ["", "## Requirement results", ""]
    for row in report["requirements"]:
        parts.append(
            f"- {row['id']} {row['title']}: {row['status']}; "
            f"systems: {', '.join(row['systems']) or 'none'}; "
            f"period: {row['period']['start']} to {row['period']['end']}; "
            f"exclusion reason: {row['exclusionReason'] or 'none'}; "
            f"methods: {', '.join(row['methods']) or 'missing'}; "
            f"evidence: {', '.join(row['evidenceRefs']) or 'missing'}"
        )
    parts += ["", "## Missing external evidence and next actions", ""]
    parts += [f"- {r['id']}: {r['prompt']}" for r in report["evidenceRequests"]] or [
        "No preparation gaps recorded; independent audit remains Unverified."
    ]
    parts += ["", "## Sources", "", RTS_SOURCE, AUDIT_ADVICE, ANNUAL_AUDIT_SOURCE]
    return "\n".join(parts) + "\n"
