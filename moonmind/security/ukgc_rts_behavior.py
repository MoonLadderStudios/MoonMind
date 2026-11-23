"""UKGC RTS audit-record judgement for the installed security-audit pack.

The selected ISO/IEC 27001:2022 controls, their prompts and the enquiry,
evidence and observation methods each needs are pack data
(``packs/ukgc-rts-security.json``). This behavior keeps the judgement that
cannot be data: annual-audit applicability by licence class and lottery
sales, critical-system scope, method coverage per in-scope system, the
external audit record with auditor independence, audit timing targets and
reporting conditions. Supplied records are preparation evidence; the
independent audit, private inspection and every regulator decision stay
Unverified, and no report is submitted.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Annotated, Literal

from pydantic import Field

from moonmind.security.frameworks import FrameworkContract, Strings, Text
from moonmind.security.pack_readiness import PackBehavior, RequirementOutcome

AUDIT_ADVICE = "https://www.gamblingcommission.gov.uk/licensees-and-businesses/guide/security-audit-advice"
ANNUAL_AUDIT_SOURCE = "https://www.gamblingcommission.gov.uk/strategy/testing-strategy-for-compliance-with-remote-gambling-and-software-technical/7-third-party-annual-security-audit"
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
NON_ANNUAL_LICENCES = {"software_supplier", "telephone_only", "trading_room"}
KNOWN_LICENCES = sorted(ANNUAL_LICENCES | LOTTERY_LICENCES | NON_ANNUAL_LICENCES)
# Create offers the known classes; retained requests can carry another class,
# which stays an explicit applicability gap rather than a rejected rerun.
LicenceClass = Annotated[Text, Field(json_schema_extra={"enum": KNOWN_LICENCES})]


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


CriticalSystems = Annotated[list[CriticalSystem], Field(max_length=5)]
ThirdParties = Annotated[list[ThirdParty], Field(max_length=64)]


def _months(value: date | None, count: int) -> str | None:
    if value is None:
        return None
    year, month = divmod(value.year * 12 + value.month - 1 + count, 12)
    return date(
        year, month + 1, min(value.day, calendar.monthrange(year, month + 1)[1])
    ).isoformat()


def _days(value: date | None, count: int) -> str | None:
    return (value + timedelta(days=count)).isoformat() if value else None


def _critical(inputs) -> list[CriticalSystem]:
    return list(inputs.critical_systems or [])


def _scoped_systems(inputs) -> list[str]:
    return sorted(
        {s for item in _critical(inputs) if item.in_scope for s in item.systems}
    )


def _scope_complete(inputs) -> bool:
    for category in CATEGORIES:
        item = next((s for s in _critical(inputs) if s.category == category), None)
        if item is None or (item.in_scope and not item.systems):
            return False
    return bool(_scoped_systems(inputs))


def _annual(inputs) -> str:
    licence = inputs.licence_class
    if licence in ANNUAL_LICENCES:
        return "applicable"
    if licence in LOTTERY_LICENCES:
        sales = inputs.annual_lottery_sales_gbp
        if sales is None:
            return "incomplete"
        return "applicable" if sales > 250000 else "not_required"
    if licence in NON_ANNUAL_LICENCES:
        return "not_required"
    return "unsupported" if licence else "incomplete"


def _request(key, prompt, **scope):
    return {"id": key, "prompt": prompt, "source": AUDIT_ADVICE, **scope}


def _validate(pack, state) -> None:
    categories = [item.category for item in _critical(state.inputs)]
    if len(categories) != len(set(categories)):
        raise ValueError("Duplicate critical-system category")
    for third in state.inputs.third_parties or []:
        if not set(third.systems) <= set(state.systems):
            raise ValueError("Third-party systems outside critical-system scope")


def _validate_records(pack, record: AuditRecord, profile) -> None:
    unknown = {f.requirement_id for f in record.findings} - {
        row.id for row in pack.requirements
    }
    if unknown:
        raise ValueError("Unknown RTS requirement: " + ", ".join(sorted(unknown)))


def _scope_requests(pack, state) -> list[dict]:
    inputs = state.inputs
    requests = []
    if _annual(inputs) in {"incomplete", "unsupported"}:
        requests.append(
            _request(
                "licence",
                "Select a listed remote operator/host licence and record annual lottery sales where relevant; unknown classes cannot establish annual-audit applicability.",
            )
        )
    for category in CATEGORIES:
        item = next((s for s in _critical(inputs) if s.category == category), None)
        if item is None or (item.in_scope and not item.systems):
            requests.append(
                _request(
                    "scope." + category,
                    f"Identify {category} systems, boundaries and third-party responsibility, or record why the category is outside scope.",
                )
            )
    if not state.systems:
        requests.append(
            _request(
                "scope.systems",
                "Identify at least one critical system before claiming audit coverage.",
            )
        )
    for field in ("operator_name", "business_description", "system_identification"):
        if not getattr(inputs, field):
            requests.append(
                _request(
                    "system." + field,
                    "Record " + field.replace("_", " ") + " for the audit report.",
                )
            )
    return requests


def _evaluate(pack, item):
    state, requirement, choice = item.state, item.requirement, item.choice
    selected, period = item.systems, item.period
    required = set(requirement.required_evidence_methods) - {"deterministic_check"}
    matched = []
    methods_by_system = {system_id: set() for system_id in selected}
    for record in state.records:
        covered = [
            system_id
            for system_id in selected
            if record.covers(requirement.id, [system_id], period)
        ]
        if covered:
            matched.append(record)
            for system_id in covered:
                methods_by_system[system_id].add(record.method)
    methods = (
        set.intersection(*methods_by_system.values()) if methods_by_system else set()
    )
    missing_methods = sorted(required - methods)
    requests = []
    if choice.not_applicable_reason:
        status = "exclusion_for_review"
        requests.append(
            _request(
                "requirement." + requirement.id,
                "Have the independent auditor review the recorded exclusion; selected RTS elements remain in the report.",
                requirementId=requirement.id,
            )
        )
    elif not missing_methods and selected and _scope_complete(state.inputs):
        status = "evidence_recorded"
    else:
        status = "incomplete"
        requests.append(
            _request(
                "requirement." + requirement.id,
                f"{requirement.id} {requirement.title}: {requirement.evidence_prompt} Record missing methods: {', '.join(missing_methods) or 'verified system boundaries'}.",
                requirementId=requirement.id,
                systems=selected,
                period=period.model_dump(mode="json"),
                methods=missing_methods,
            )
        )
    record = state.audit or AuditRecord()
    details = {
        "methods": sorted(methods),
        "findings": [
            f.model_dump(mode="json", by_alias=True)
            for f in record.findings
            if f.requirement_id == requirement.id
        ],
    }
    return RequirementOutcome(
        status=status,
        reason=requirement.applicability,
        evidence_refs=[e.artifact_ref for e in matched],
        requests=requests,
        details={key: value for key, value in details.items() if value},
    )


def _context(pack, rows, audit, state):
    inputs = state.inputs
    record = audit or AuditRecord()
    requests = state.requests
    for field in ("name", "firm", "background", "qualifications"):
        if not getattr(record.auditor, field):
            requests.append(
                _request(
                    "auditor." + field,
                    "Supply the independent auditor's "
                    + field
                    + " and suitability to assess ISO/IEC 27001:2022.",
                )
            )
    if record.auditor.independent is not True or not record.auditor.independence_ref:
        requests.append(
            _request(
                "auditor.independence",
                "Supply an independence statement with evidence; auditor name or qualifications alone do not establish independence.",
            )
        )
    for field in ("audited_on", "locations", "executive_summary", "overall_opinion"):
        if not getattr(record, field):
            requests.append(
                _request(
                    "audit." + field,
                    "Record audit "
                    + field.replace("_", " ")
                    + ". Observation may use documented remote inspection techniques.",
                )
            )
    for i, finding in enumerate(record.findings):
        if finding.classification != "fully_compliant" and not all(
            (finding.management_response, finding.action_owner, finding.target_date)
        ):
            requests.append(
                _request(
                    f"finding.{i}.management",
                    "Record a management response, action owner and target date.",
                    requirementId=finding.requirement_id,
                )
            )
    annual = _annual(inputs)
    first_start = (
        inputs.operations_started_on
        if inputs.first_audit_extension_approved
        else inputs.licence_granted_on
    )
    if annual == "applicable" and not inputs.regulator_due_on:
        requests.append(
            _request(
                "schedule.due",
                "Record the Commission-set audit due date; calculated dates are planning targets only. A delayed first audit requires an approved application.",
            )
        )
    if annual == "applicable" and not first_start:
        requests.append(
            _request(
                "schedule.start",
                "Record the licence grant date or the approved first-audit extension and operations start date.",
            )
        )
    return {
        "auditApplicability": {
            "annualAudit": annual,
            "scope": "complete" if _scope_complete(inputs) else "incomplete",
            "licenceClass": inputs.licence_class,
            "sources": [AUDIT_ADVICE, ANNUAL_AUDIT_SOURCE],
            "note": "Software-only suppliers are not listed annual-audit operators. RTS security obligations and third-party responsibilities still need review. Audit advice includes real-event operators absent from the strategy footnote.",
        },
        "schedule": {
            "firstAuditTarget": _months(first_start, 6),
            "annualAuditTarget": _months(inputs.last_audit_on, 12),
            "effectiveDueOn": (
                inputs.regulator_due_on.isoformat() if inputs.regulator_due_on else None
            ),
            "firstReportTarget": (
                _days(inputs.regulator_due_on, 7) if not inputs.last_audit_on else None
            ),
            "confirmationResponseTarget": _days(inputs.confirmation_requested_on, 7),
            "requestedReportTarget": _days(inputs.report_requested_on, 7),
        },
        "reportingConditions": {
            "retainFullReport": True,
            "requestedReportWithinDays": 7,
            "annualConfirmationWithinDays": 7,
            "majorNonconformity": "Audit advice requires notification and the full report without delay; the testing strategy also specifies within seven days of receipt. Include explanations and management responses.",
            "majorNonconformityOuterTarget": (
                _days(inputs.report_received_on, 7)
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
        "externalAudit": {
            "status": "Unverified",
            "reason": (
                "Synthetic preparation records do not constitute an independent audit or private inspection."
                if record.kind == "synthetic"
                else "Supplied records are preparation evidence; MoonMind has not independently verified an external audit or private inspection."
            ),
            "verifiableBy": "Qualified independent auditor and operator evidence owner",
        },
        "headline": [
            "Preparation evidence only. Independent audit and private inspection: Unverified. No regulator submission has been performed."
        ],
    }


def _record_refs(record: AuditRecord):
    # The independence statement is mandatory evidence: an unreadable one
    # fails the read instead of being dropped.
    ref = record.auditor.independence_ref
    return [ref] if ref else []


UKGC_BEHAVIOR = PackBehavior(
    required_evidence_fields=(),
    records_model=AuditRecord,
    validate_records=_validate_records,
    record_refs=_record_refs,
    profile_types={
        "ukgc.licence-class": LicenceClass,
        "ukgc.critical-systems": CriticalSystems,
        "ukgc.third-parties": ThirdParties,
    },
    validate_profile=_validate,
    scoped_systems=_scoped_systems,
    scope_requests=_scope_requests,
    evaluate_requirement=_evaluate,
    context=_context,
)
