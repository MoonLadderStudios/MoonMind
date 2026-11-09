"""Original, risk-based ISMS readiness summaries; no licensed standard text."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from moonmind.security.assessments import content_key
from moonmind.security.builtin_frameworks import review_requirement
from moonmind.security.frameworks import (
    FrameworkContract,
    FrameworkEvidence,
    FrameworkModule,
    FrameworkPack,
    FrameworkRequirement,
    FrameworkSource,
    OrganisationalEvidenceRequest,
    RequirementChoice,
    Strings,
    Text,
)

STANDARD = "https://www.iso.org/standard/27001"
AMENDMENT = "https://www.iso.org/standard/88435.html"
SOA_GUIDANCE = "https://committee.iso.org/files/live/sites/jtc1sc27/files/resources/ISO-IECJTC1-SC27-WG1_N3298_Auditing%20Practices%20Note%20-%20SoA.pdf"
EDITION = "2022+Amd1:2024"
PACK_ID = "iso-27001-readiness"

# Clause identifiers with original readiness prompts, not substitute standard text.
MANAGEMENT = {
    "4.1": ("Business context and climate relevance", "risk_register", "design"),
    "4.2": (
        "Interested parties and relevant obligations, including climate-related needs",
        "risk_register",
        "design",
    ),
    "4.3": ("ISMS boundaries, dependencies and scope rationale", "policy", "design"),
    "4.4": ("Operating security management processes", "policy", "occurrence"),
    "5.1": ("Leadership decisions and resourcing", "management_review", "occurrence"),
    "5.2": ("Adopted security policy", "policy", "adoption"),
    "5.3": ("Assigned security accountabilities", "policy", "adoption"),
    "6.1.1": (
        "Management-system opportunities and uncertainty",
        "risk_register",
        "design",
    ),
    "6.1.2": (
        "Repeatable risk assessment and acceptance criteria",
        "risk_register",
        "design",
    ),
    "6.1.3": (
        "Risk treatment choices and justified applicability",
        "risk_treatment",
        "adoption",
    ),
    "6.2": (
        "Measurable objectives and delivery responsibilities",
        "policy",
        "adoption",
    ),
    "6.3": ("Planned changes to the management system", "policy", "occurrence"),
    "7.1": ("Resources available for security operation", "policy", "adoption"),
    "7.2": ("Competence demonstrated for assigned duties", "training", "occurrence"),
    "7.3": (
        "Staff understanding of security responsibilities",
        "training",
        "occurrence",
    ),
    "7.4": ("Security communications and audiences", "policy", "occurrence"),
    "7.5": (
        "Controlled, available and retained management records",
        "policy",
        "occurrence",
    ),
    "8.1": (
        "Operational processes and outsourced dependencies",
        "supplier",
        "occurrence",
    ),
    "8.2": (
        "Repeated and change-triggered risk assessments",
        "risk_register",
        "occurrence",
    ),
    "8.3": (
        "Executed treatment plans and retained outcomes",
        "risk_treatment",
        "occurrence",
    ),
    "9.1": (
        "Measured performance and evaluated effectiveness",
        "management_review",
        "occurrence",
    ),
    "9.2": (
        "Completed independent internal-audit programme",
        "internal_audit",
        "occurrence",
    ),
    "9.3": (
        "Management review decisions and tracked actions",
        "management_review",
        "occurrence",
    ),
    "10.1": (
        "Demonstrated continuing ISMS improvement",
        "corrective_action",
        "occurrence",
    ),
    "10.2": (
        "Corrected nonconformities and effectiveness follow-up",
        "corrective_action",
        "occurrence",
    ),
}
# Original topic labels help the operator locate each reference in their licensed
# copy. Inclusion follows recorded risk treatment, not a scanner crosswalk.
ANNEX_TOPICS = {
    5: [
        "Security direction",
        "Security accountability",
        "Conflicting duties",
        "Manager oversight",
        "Authority liaison",
        "Professional security networks",
        "Threat knowledge",
        "Project safeguards",
        "Information ownership",
        "Responsible asset use",
        "Asset return",
        "Information sensitivity",
        "Sensitivity labels",
        "Information exchange",
        "Access decisions",
        "Identity administration",
        "Authentication secrets",
        "Entitlement lifecycle",
        "Supplier exposure",
        "Supplier agreements",
        "Technology supply chains",
        "Supplier change oversight",
        "Cloud service lifecycle",
        "Incident preparation",
        "Event triage",
        "Incident handling",
        "Incident learning",
        "Preserved evidence",
        "Security during disruption",
        "Technology recovery readiness",
        "Legal obligations",
        "Intellectual property",
        "Record preservation",
        "Personal information",
        "Independent security examination",
        "Conformance monitoring",
        "Repeatable operating procedures",
    ],
    6: [
        "Pre-employment checks",
        "Employment expectations",
        "Security learning",
        "Conduct breaches",
        "Departures and role changes",
        "Confidentiality undertakings",
        "Remote work safeguards",
        "Event reporting",
    ],
    7: [
        "Facility boundaries",
        "Entry safeguards",
        "Protected work areas",
        "Physical observation",
        "Environmental hazards",
        "Work in restricted areas",
        "Unattended information",
        "Equipment placement",
        "Off-site equipment",
        "Media lifecycle",
        "Supporting utilities",
        "Cable protection",
        "Equipment upkeep",
        "Safe equipment retirement",
    ],
    8: [
        "End-user devices",
        "Privileged access",
        "Information access limits",
        "Source access",
        "Authentication mechanisms",
        "Resource capacity",
        "Malicious code defence",
        "Vulnerability handling",
        "Configuration baselines",
        "Information deletion",
        "Data masking",
        "Information leakage",
        "Recoverable backups",
        "Service redundancy",
        "Recorded events",
        "Anomaly monitoring",
        "Time consistency",
        "Powerful system utilities",
        "Software installation",
        "Network protection",
        "Network service safeguards",
        "Network separation",
        "Web access filtering",
        "Cryptographic choices",
        "Secure delivery lifecycle",
        "Application security needs",
        "Security architecture",
        "Safer coding",
        "Security tests",
        "External development",
        "Environment separation",
        "Controlled changes",
        "Protected test data",
        "Assessment safeguards",
    ],
}
ANNEX = {
    f"A.{section}.{i}": title
    for section, titles in ANNEX_TOPICS.items()
    for i, title in enumerate(titles, 1)
}


def source(section, amended=False):
    return FrameworkSource(
        url=AMENDMENT if amended else STANDARD,
        section=section,
        reviewed_on=date(2026, 11, 9),
    )


ISO_PACK = FrameworkPack(
    id=PACK_ID,
    title="ISO/IEC 27001 ISMS readiness",
    edition=EDITION,
    published_on=date(2022, 10, 25),
    source=source("Clauses 4–10 and Annex A"),
    guidance_sources=[AMENDMENT, SOA_GUIDANCE],
    modules=[
        FrameworkModule(
            id="Amd1:2024",
            edition="2024",
            published_on=date(2024, 2, 23),
            source=source("4.1 and 4.2", True),
            applicability="Context and interested-party climate considerations",
        )
    ],
    requirements=[
        FrameworkRequirement(
            id=key,
            title=title,
            summary=f"Retain scoped evidence for {title.lower()}.",
            category="management_system" if key in MANAGEMENT else "annex_a",
            module="ISMS" if key in MANAGEMENT else "Annex A",
            applicability="Management-system requirements apply to the ISMS; Annex A inclusion and exclusion need risk-based decisions.",
            sources=[source(key)]
            + ([source(key, True)] if key in {"4.1", "4.2"} else []),
            evidence_types=["organisational", "technical_context"],
            evidence_prompt=f"Provide reviewed records for {title.lower()}, covering this scope and period; drafts and assertions do not establish operation.",
            responsibilities=[
                "Record the accountable owner and independent reviewer where applicable."
            ],
        )
        for key, title in {**{k: v[0] for k, v in MANAGEMENT.items()}, **ANNEX}.items()
    ],
)


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


class ApplicabilityDecision(FrameworkContract):
    title: Text | None = None
    decision: Literal["include", "exclude", "undetermined"] = "undetermined"
    rationale: Text | None = None
    risk_ids: Strings = Field(default_factory=list)
    implementation_status: Literal[
        "not_started", "planned", "partial", "implemented", "operating", "unknown"
    ] = "unknown"
    owner: Text | None = None


class SystemInputs(FrameworkContract):
    systems: Annotated[list[Text], Field(min_length=1, max_length=256)]
    scope_statement: Text | None = None
    context: Text | None = None
    interested_parties: Strings = Field(default_factory=list)
    climate_relevance: Text | None = None
    risks: Annotated[list[Risk], Field(max_length=128)] = Field(default_factory=list)
    controls: Annotated[dict[Text, ApplicabilityDecision], Field(max_length=160)] = (
        Field(default_factory=dict)
    )
    requirement_evidence: Annotated[
        dict[
            Text, Annotated[list[OrganisationalEvidenceRequest], Field(max_length=32)]
        ],
        Field(max_length=256),
    ] = Field(default_factory=dict)

    @model_validator(mode="after")
    def consistent_links(self):
        ids = {risk.id for risk in self.risks}
        if len(ids) != len(self.risks):
            raise ValueError("duplicate risk identity")
        for key, decision in self.controls.items():
            if key not in ANNEX and (
                not key.startswith("EXTRA.") or not decision.title
            ):
                raise ValueError(
                    "additional controls need EXTRA. identifiers and titles"
                )
            if set(decision.risk_ids) - ids:
                raise ValueError("control links unknown risk")
        known = set(ANNEX) | set(self.controls)
        if any(set(r.control_ids) - known for r in self.risks):
            raise ValueError("risk references unknown controls")
        if set(self.requirement_evidence) - (known | set(MANAGEMENT)):
            raise ValueError("unknown evidence requirement")
        return self


def prepare_assessment(
    selection,
    *,
    evidence,
    audit=None,
    now=None,
    evidence_availability=None,
    previous=None,
):
    now = now or datetime.now(UTC)
    system = SystemInputs.model_validate(selection.system_inputs)
    known = set(MANAGEMENT) | set(ANNEX) | set(system.controls)
    if set(selection.requirement_choices) - known:
        raise ValueError("unknown requirement choice")
    availability = evidence_availability or {}
    requests, rows, decisions, treatment = [], [], [], []
    for field in (
        "scope_statement",
        "context",
        "interested_parties",
        "climate_relevance",
        "risks",
    ):
        if not getattr(system, field):
            requests.append(
                {
                    "id": "scope." + field,
                    "prompt": f"Record ISMS {field.replace('_', ' ')}; context assertions remain unverified until reviewed.",
                }
            )
    controls = {
        **ANNEX,
        **{k: d.title or ANNEX.get(k, k) for k, d in system.controls.items()},
    }
    for key, title in {**{k: v[0] for k, v in MANAGEMENT.items()}, **controls}.items():
        choice = selection.requirement_choices.get(key, RequirementChoice())
        systems = choice.systems if choice.systems is not None else system.systems
        if not systems or not set(systems) <= set(system.systems):
            raise ValueError("requirement scope exceeds or empties ISMS scope")
        period = choice.period or selection.period
        if not selection.period.covers(period):
            raise ValueError("requirement period exceeds assessment period")
        selected = system.controls.get(key, ApplicabilityDecision())
        is_management = key in MANAGEMENT
        risk_links = sorted(
            set(selected.risk_ids)
            | {r.id for r in system.risks if key in r.control_ids}
        )
        applicable = is_management or selected.decision == "include"
        valid_exclusion = (
            not is_management
            and selected.decision == "exclude"
            and bool(selected.rationale)
            and not risk_links
        )
        reasons = []
        if is_management and choice.not_applicable_reason:
            reasons.append(
                "Management-system requirements cannot be excluded from the ISMS readiness assessment."
            )
        if not is_management:
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
            MANAGEMENT[key][1:] if is_management else ("policy", "occurrence")
        )
        supplied = system.requirement_evidence.get(key, [])
        required = [purpose] if is_management else ["design", "adoption", "occurrence"]
        reviewed = review_requirement(
            requirement_id=key,
            title=title,
            pack_id=PACK_ID,
            edition=EDITION,
            systems=systems,
            period=period,
            requests=supplied,
            required_purposes=required,
            now=now,
            evidence_availability=availability,
        )
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
        elif applicable and reviewed["status"] != "evidence_reviewed":
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
        technical = []
        for raw in evidence:
            record = FrameworkEvidence.model_validate(raw)
            if (
                key in record.requirement_ids
                and set(systems) <= set(record.systems)
                and record.period.covers(period)
            ):
                technical.append(record.artifact_ref)
        status = (
            "excluded"
            if valid_exclusion and not reasons
            else (
                "evidence_reviewed"
                if applicable
                and not reasons
                and reviewed["status"] == "evidence_reviewed"
                else "incomplete"
            )
        )
        row = {
            "id": key,
            "title": title,
            "category": (
                "management_system"
                if is_management
                else "annex_a" if key in ANNEX else "additional_control"
            ),
            "status": status,
            "systems": systems,
            "period": period.model_dump(mode="json"),
            "evidenceReviews": reviewed["evidenceReviews"],
            "technicalContextRefs": technical,
            "limitations": reasons,
            "responsibleEntity": choice.responsible_entity,
            "responsibilityReason": choice.responsibility_reason,
        }
        rows.append(row)
        if not is_management:
            decisions.append(
                {
                    "controlId": key,
                    **selected.model_dump(mode="json", by_alias=True),
                    "title": title,
                    "riskIds": risk_links,
                    "systems": systems,
                    "period": row["period"],
                    "evidenceStatus": status,
                    "verification": "Unverified",
                }
            )
    for risk in system.risks:
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
            system.controls.get(key, ApplicabilityDecision()).decision != "include"
            for key in risk.control_ids
        ):
            gaps.append("Resolve missing or excluded treatment controls in the SoA.")
        if gaps:
            treatment.append({"riskId": risk.id, "actions": gaps})
    basis = {
        "edition": EDITION,
        "scope": system.model_dump(mode="json", by_alias=True),
        "period": selection.period.model_dump(mode="json"),
        "decisions": decisions,
    }
    soa = {
        "status": "draft",
        "version": content_key(basis),
        "edition": EDITION,
        "scopeStatement": system.scope_statement,
        "decisions": decisions,
        "previousAssessmentRef": selection.previous_assessment_ref,
    }
    report = {
        "schemaVersion": "iso-27001-readiness/v1",
        "pack": ISO_PACK.model_dump(mode="json", by_alias=True),
        "systemInputs": system.model_dump(mode="json", by_alias=True),
        "evaluatedAt": now.isoformat(),
        "period": selection.period.model_dump(mode="json"),
        "requirements": rows,
        "statementOfApplicability": soa,
        "riskTreatmentActions": treatment,
        "unresolvedRequirements": [
            r["id"] for r in rows if r["status"] == "incomplete"
        ],
        "evidenceRequests": requests,
        "readiness": "incomplete" if requests or treatment else "evidence_reviewed",
        "certification": "Unverified",
        "organisationalOperation": "Unverified",
        "limitations": [
            "Original readiness summaries are not the licensed standard. Consult the authorised edition for complete requirements.",
            "SoA and generated policies are drafts. Technical evidence and attributed documentary reviews do not establish certification or independent verification of organisational operation.",
        ],
    }
    if previous is not None:
        if (
            previous.get("schemaVersion") != report["schemaVersion"]
            or previous.get("pack", {}).get("id") != PACK_ID
        ):
            raise ValueError("previous assessment schema or pack mismatch")
        prior = previous.get("statementOfApplicability", {})
        old = {r["controlId"]: r for r in prior.get("decisions", [])}
        global_change = (
            previous.get("systemInputs") != report["systemInputs"]
            or previous["pack"] != report["pack"]
            or previous.get("period") != report["period"]
        )
        affected = (
            sorted(set(old) | set(controls))
            if global_change
            else [r["controlId"] for r in decisions if old.get(r["controlId"]) != r]
        )

        # Compare effective requirement choices rather than input spelling.
        # A management-only scope/period change invalidates that evidence review,
        # without changing unrelated Annex A applicability or its SoA version.
        def management_basis(row):
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

        old_requirements = {
            row["id"]: management_basis(row)
            for row in previous.get("requirements", [])
            if row["id"] in MANAGEMENT
        }
        management_changes = {
            row["id"]
            for row in rows
            if row["id"] in MANAGEMENT
            and old_requirements.get(row["id"]) != management_basis(row)
        }
        affected_requirements = (
            sorted(known)
            if global_change
            else sorted(set(affected) | management_changes)
        )
        if affected_requirements:
            report["readiness"] = "incomplete"
            report["evidenceRequests"].append(
                {
                    "id": "reassessment.applicability",
                    "prompt": "Review applicability and evidence after changed risk, scope, period or pack edition.",
                    "affectedControlIds": affected,
                    "affectedRequirementIds": affected_requirements,
                }
            )
        report["reassessment"] = {
            "previousRef": selection.previous_assessment_ref,
            "previousSoAVersion": prior.get("version"),
            "previousEdition": previous["pack"]["edition"],
            "affectedControlIds": affected,
            "affectedRequirementIds": affected_requirements,
            "prompt": "Review affected applicability and evidence; historical SoA decisions remain retained in the previous immutable report.",
        }
    return report


def render_assessment(report):
    from moonmind.security.assessment_reports import _text

    lines = [
        "## ISO/IEC 27001 ISMS readiness",
        "",
        f"Edition: {EDITION}. Readiness: {_text(report['readiness'])}.",
        "Certification and actual organisational operation: Unverified. Statement of Applicability: draft.",
        "",
        f"SoA version: {_text(report['statementOfApplicability']['version'])}.",
        "",
        "### Requirement gaps and evidence coverage",
    ]
    for row in report["requirements"]:
        lines.append(
            f"- {_text(row['id'])}: {_text(row['title'])}; {_text(row['status'])}. Scope: {_text(row['systems'])}; period: {_text(row['period'])}."
        )
    lines += ["", "### Draft Statement of Applicability"]
    for row in report["statementOfApplicability"]["decisions"]:
        lines.append(
            f"- {_text(row['controlId'])}: {_text(row['decision'])}; reason: {_text(row['rationale'] or 'missing')}; declared implementation: {_text(row['implementationStatus'])}; evidence: {_text(row['evidenceStatus'])}; risks: {_text(row['riskIds'])}."
        )
    lines += ["", "### Risk treatment and evidence requests"]
    for row in report["riskTreatmentActions"]:
        lines.append(f"- {_text(row['riskId'])}: {_text(row['actions'])}")
    for row in report["evidenceRequests"]:
        lines.append(f"- {_text(row['id'])}: {_text(row['prompt'])}")
    if report.get("reassessment"):
        lines.append(f"Reassessment: {_text(report['reassessment'])}")
    lines.extend(_text(item) for item in report["limitations"])
    return "\n".join(lines) + "\n"
