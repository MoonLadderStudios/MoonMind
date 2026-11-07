"""Scoped GLI readiness evidence; external testing and approval remain separate."""

from __future__ import annotations

import json
from datetime import date
from typing import Literal

from pydantic import Field

from moonmind.security.frameworks import (
    FrameworkAssessmentInput,
    FrameworkContract,
    FrameworkEvidence,
    FrameworkModule,
    FrameworkPack,
    FrameworkRequirement,
    FrameworkSource,
    RequirementChoice,
    Strings,
    Text,
    load_framework_pack,
)

STANDARDS = "https://gaminglabs.com/gli-standards/"
GLI19 = "https://gaminglabs.com/wp-content/uploads/2026/11/GLI-19-v4.0-Standards-for-Interactive-Gaming-Systems.pdf"
_GSF = "https://gaminglabs.com/wp-content/uploads/"
SOURCES = {
    "GLI-19": (GLI19, "4.0", date(2026, 11, 3)),
    "GLI-GSF-1": (
        _GSF
        + "2024/06/GLI-GSF-1-v1.1-Gaming-Information-Security-GIS-Common-Controls-Audit-FINAL.pdf",
        "1.1",
        date(2025, 9, 30),
    ),
    "GLI-GSF-2": (
        _GSF
        + "2025/02/GLI-GSF-2-v1.0-Gaming-Technical-Security-GTS-Assessment-Final.pdf",
        "1.0",
        date(2025, 2, 7),
    ),
    "GLI-GSF-3": (
        _GSF
        + "2024/06/GLI-GSF-3-v1.0-Gaming-Information-Security-GIS-Vendors-Audit-FINAL.pdf",
        "1.0",
        date(2025, 9, 30),
    ),
    "GLI-GSF-5": (
        _GSF
        + "2024/06/GLI-GSF-5-v1.0-Gaming-Information-Security-GIS-Online-Audit-FINAL.pdf",
        "1.0",
        date(2025, 9, 30),
    ),
}
APPLICABILITY = {
    "GLI-19": "Interactive gaming operators and regulated suppliers, including RGS; assess only their recorded product responsibilities.",
    "GLI-GSF-1": "Common controls for the assessed gaming enterprise or ancillary vendor; record agreed business responsibilities.",
    "GLI-GSF-2": "Technical security of the scoped gaming production environment and integrated ancillary services.",
    "GLI-GSF-3": "Ancillary vendors only where their solution does not directly affect regulated gaming components or activities; never assigned automatically to an RGS.",
    "GLI-GSF-5": "Online gaming environment controls for operators and regulated suppliers, including RGS.",
}
PROMPTS = {
    "technical": "Supply scoped technical results and remediation/retest records linked to the original security observation.",
    "organizational": "Supply approved procedures and dated operational samples with the responsible assessed business identified.",
    "game_math": "Supply game rules, model/version, paytable and mathematical evaluation linked to the game-math observation.",
    "rng": "Supply generator implementation and raw/mapped test evidence linked to the RNG observation.",
    "laboratory": "Supply a qualified laboratory evaluation identifying edition, product/build, scope and findings.",
    "regulator": "Supply the regulator's decision for this jurisdiction, edition and product scope; publication or a lab report is insufficient.",
}


def source(module: str, section: str) -> FrameworkSource:
    return FrameworkSource(
        url=SOURCES[module][0], section=section, reviewed_on=date(2026, 11, 7)
    )


# Original readiness summaries group the cited sections. They are not a
# reproduction of the standards or an assertion of exhaustive certification.
_REQUIREMENTS = {
    "GLI-19": [
        ("1.2.3", "Jurisdiction decision", "regulator"),
        ("1.5", "Independent product evaluation", "laboratory"),
        ("2.2", "Clock consistency", "technical"),
        ("2.3", "Program integrity", "technical"),
        ("2.4", "Protected gaming communications", "technical"),
        ("2.5", "Retained gaming records", "organizational"),
        ("2.6", "Reconciled reporting", "organizational"),
        ("3.2", "Player identity verification", "organizational"),
        ("3.3", "Player account safeguards", "organizational"),
        ("3.4", "Player protection limits", "organizational"),
        ("3.5", "Promotional offer controls", "organizational"),
        ("4.2", "Player application safeguards", "technical"),
        ("4.3", "Geographic access validation", "technical"),
        ("5.2", "Generator behavior evaluation", "rng"),
        ("5.3", "Generator strength monitoring", "rng"),
        ("5.4", "Physical generator evaluation", "rng"),
        ("5.5", "Verifiable fairness protocol evaluation", "rng"),
        ("6.2", "Game interface behavior", "game_math"),
        ("6.3", "Gaming session behavior", "game_math"),
        ("6.4", "Game rule disclosures", "game_math"),
        ("6.5", "Game-specific rule evaluation", "game_math"),
        ("6.6", "Random outcome mapping", "rng"),
        ("6.7", "Outcome fairness analysis", "game_math"),
        ("6.8", "Payout and odds analysis", "game_math"),
        ("6.9", "Bonus feature evaluation", "game_math"),
        ("6.10", "Progressive award accounting", "game_math"),
        ("6.11", "Alternative game mode evaluation", "game_math"),
        ("6.12", "Skill game evaluation", "game_math"),
        ("6.13", "Peer game evaluation", "game_math"),
        ("6.14", "Reconstructable game history", "technical"),
        ("6.15", "Safe game disablement", "technical"),
        ("6.16", "Interrupted game recovery", "technical"),
        ("6.17", "Virtual game evaluation", "game_math"),
        ("6.18", "Live game evaluation", "laboratory"),
        ("6.19", "Spectator wager evaluation", "game_math"),
        ("A.2", "Documented internal controls", "organizational"),
        ("A.3", "Operating procedure samples", "organizational"),
        ("A.4", "Public information controls", "organizational"),
        ("A.5", "Gaming procedure samples", "organizational"),
        ("A.6", "Operational player account controls", "organizational"),
        ("A.7", "Operational application controls", "organizational"),
        ("A.8", "Fraud and financial-crime procedures", "organizational"),
        ("B.2", "Independent security evaluation", "technical"),
        ("B.3", "Recurring scan records", "technical"),
        ("C.2", "Identity partner oversight", "organizational"),
        ("C.3", "Location partner oversight", "organizational"),
    ],
    "GLI-GSF-1": [
        ("Appendix A", "Baseline security inventory and safeguards", "organizational"),
        ("GIS-1", "Verified program signatures", "technical"),
        ("GIS-2.1", "Risk oversight records", "organizational"),
        ("GIS-2.2", "Approved security policy", "organizational"),
        ("GIS-2.3", "Reviewed access permissions", "organizational"),
        ("GIS-2.4", "Personnel assurance records", "organizational"),
        ("GIS-2.5", "Personnel conduct acknowledgements", "organizational"),
        ("GIS-2.6", "Agreed security duties", "organizational"),
        ("GIS-2.7", "Privacy program records", "organizational"),
        ("GIS-2.8", "Protected financial transactions", "organizational"),
        ("GIS-3", "Protected system operation", "organizational"),
        ("GIS-4", "Protected data and recoverable backups", "organizational"),
        ("GIS-5", "Secured network operation", "technical"),
        ("GIS-6", "Agreed service-provider safeguards", "organizational"),
        ("GIS-7", "Cryptography and component hardening", "technical"),
        ("GIS-8", "Protected remote access", "technical"),
        ("GIS-9", "Controlled development and changes", "organizational"),
        ("2.5", "Independent audit scope and findings", "organizational"),
        ("2.6", "Tracked corrective actions", "organizational"),
    ],
    "GLI-GSF-2": [
        ("3", "Recurring scan coverage", "technical"),
        ("B", "Vulnerability assessment evidence", "technical"),
        ("C", "Penetration assessment evidence", "technical"),
        ("D", "Cloud and container examination", "technical"),
        ("E", "Additional risk-driven tests", "technical"),
    ],
    "GLI-GSF-3": [
        ("VGIS-1", "Vendor privacy and incident coordination", "organizational"),
        ("VGIS-2", "Enterprise-controlled vendor access", "technical"),
        ("VGIS-3", "Accessible vendor audit trails", "organizational"),
        ("VGIS-4", "Traceable supply-chain oversight", "organizational"),
        ("VGIS-5", "Controlled vendor delivery", "technical"),
        ("VGIS-6", "Vendor-specific security evaluation", "technical"),
    ],
    "GLI-GSF-5": [
        ("OGIS-1", "Operational signature verification records", "organizational"),
        ("OGIS-2", "Restricted back-office access", "technical"),
        ("OGIS-3", "Server-side execution protection", "technical"),
        ("OGIS-4", "Online application abuse protection", "technical"),
        ("OGIS-5", "Resilient public gaming interfaces", "technical"),
    ],
}
GLI_PACK = FrameworkPack(
    id="gli-19-readiness",
    title="GLI-19 and GLI-GSF readiness",
    edition="4.0",
    published_on=date(2026, 11, 3),
    source=source("GLI-19", "1.2.3, 1.3 and 1.5"),
    guidance_sources=[STANDARDS],
    modules=[
        FrameworkModule(
            id=module,
            edition=edition,
            published_on=published,
            source=source(module, "Introduction and cited requirement sections"),
            applicability=APPLICABILITY[module],
        )
        for module, (_, edition, published) in SOURCES.items()
    ],
    requirements=[
        FrameworkRequirement(
            id=f"{module}:{section}",
            title=summary,
            summary=summary,
            category=kind,
            module=module,
            applicability=APPLICABILITY[module],
            sources=[
                source(
                    module,
                    ("Appendix " if module == "GLI-GSF-2" and section in "BCDE" else "")
                    + section,
                )
            ],
            evidence_types=[kind],
            evidence_prompt=PROMPTS[kind],
            responsibilities=[
                "Assessed business must identify its duties and any partner-owned duties for this requirement."
            ],
        )
        for module, requirements in _REQUIREMENTS.items()
        for section, summary, kind in requirements
    ],
)


class JurisdictionBasis(FrameworkContract):
    jurisdiction: Text | None = None
    status: Literal["unknown", "adopted", "not_adopted"] = "unknown"
    edition: Text | None = None
    adopted_on: date | None = None
    effective_on: date | None = None
    record_version: Text | None = None
    source_ref: Text | None = None
    reason: Text | None = None


class GLISystemInputs(FrameworkContract):
    entity_name: Text | None = None
    entity_type: Literal["operator", "rgs", "ancillary_provider"] | None = None
    product_type: (
        Literal[
            "interactive_gaming", "ancillary_service", "gaming_device", "event_wagering"
        ]
        | None
    ) = None
    directly_affects_gaming: bool | None = None
    systems: Strings = Field(default_factory=list)
    jurisdiction_basis: JurisdictionBasis = Field(default_factory=JurisdictionBasis)


class GLIEvidence(FrameworkEvidence):
    evidence_type: Literal[
        "technical", "organizational", "game_math", "rng", "laboratory", "regulator"
    ]
    outcome: Literal["passed", "failed", "incomplete"]
    edition: Text
    jurisdiction: Text | None = None

    def supports(self, requirement, systems, period, pack, jurisdiction) -> bool:
        kinds = {
            "technical": {"technical_assessment", "control_evidence", "external_audit"},
            "organizational": {"control_evidence", "external_audit"},
            "game_math": {"technical_assessment", "control_evidence"},
            "rng": {"technical_assessment", "control_evidence"},
            "laboratory": {"external_audit"},
            "regulator": {"external_audit"},
        }
        module = next(m for m in pack.modules if m.id == requirement.module)
        return (
            self.outcome == "passed"
            and self.edition == pack.edition
            and self.provenance.standard == f"{module.id} v{module.edition}"
            and self.kind in kinds[self.evidence_type]
            and self.evidence_type in requirement.evidence_types
            and (self.evidence_type == "organizational" or self.method != "enquiry")
            and requirement.id in self.requirement_ids
            and bool(systems)
            and set(systems) <= set(self.systems)
            and self.period.covers(period)
            and (self.evidence_type != "regulator" or self.jurisdiction == jurisdiction)
            and (
                self.evidence_type not in {"laboratory", "regulator"}
                or bool(self.provenance.standard)
            )
        )


def _module_applicability(module: str, system: GLISystemInputs) -> tuple[str, str]:
    if not system.entity_type or not system.product_type:
        return (
            "incomplete",
            "Identify assessed entity and product before selecting modules.",
        )
    if system.product_type in {"gaming_device", "event_wagering"}:
        profile = "GLI-11" if system.product_type == "gaming_device" else "GLI-33"
        return (
            "incomplete",
            f"Use the {profile} product profile; shared GSF controls alone cannot establish product readiness.",
        )
    if module == "GLI-GSF-3":
        if system.entity_type != "ancillary_provider":
            return (
                "not_applicable",
                "GSF-3 covers ancillary vendors, not automatically an operator or RGS.",
            )
        if system.directly_affects_gaming is None:
            return (
                "incomplete",
                "Record whether this ancillary solution directly affects regulated gaming.",
            )
        if system.directly_affects_gaming:
            return (
                "incomplete",
                "GSF-3 ancillary scope does not cover a solution directly affecting regulated gaming.",
            )
        return "applicable", APPLICABILITY[module]
    if module in {"GLI-19", "GLI-GSF-5"} and system.entity_type == "ancillary_provider":
        return (
            "not_applicable",
            "Ancillary vendor scope excludes direct interactive-gaming product controls; retain partner responsibilities separately.",
        )
    if (
        system.entity_type in {"operator", "rgs"}
        and system.product_type != "interactive_gaming"
    ):
        return (
            "incomplete",
            "Confirm an interactive-gaming product or select the ancillary-provider scope.",
        )
    return "applicable", APPLICABILITY[module]


def prepare_assessment(
    request: FrameworkAssessmentInput,
    *,
    evidence: list[dict],
    audit: dict | None = None,
) -> dict:
    pack = load_framework_pack(request.pack_id)
    if pack.id != GLI_PACK.id or audit is not None:
        raise ValueError("GLI evidence belongs in evidenceRefs")
    system = GLISystemInputs.model_validate(request.system_inputs)
    records = [GLIEvidence.model_validate(row) for row in evidence]
    ids = {r.id for r in pack.requirements}
    unknown = set(request.requirement_choices) - ids
    unknown |= {key for row in records for key in row.requirement_ids} - ids
    if unknown:
        raise ValueError("Unknown GLI requirement: " + ", ".join(sorted(unknown)))
    requests = []

    def missing(key, prompt, **scope):
        requests.append({"id": key, "prompt": prompt, "source": STANDARDS, **scope})

    for field in ("entity_name", "entity_type", "product_type", "systems"):
        if not getattr(system, field):
            alias = GLISystemInputs.model_fields[field].alias
            missing(
                "scope." + alias,
                "Supply "
                + alias
                + " for the assessed business and admitted system scope.",
            )
    basis = system.jurisdiction_basis
    adoption_known = bool(
        basis.status == "adopted"
        and basis.jurisdiction
        and basis.source_ref
        and basis.record_version
        and basis.edition == pack.edition
        and basis.adopted_on
        and basis.effective_on
        and basis.adopted_on <= basis.effective_on <= request.period.start
    )
    if not adoption_known:
        missing(
            "jurisdiction.adoption",
            "Supply the jurisdiction's sourced adoption record, recordVersion, adopted edition and adoption/effective dates covering the assessment period. Unknown or unadopted publication is comparison information, not jurisdiction approval.",
        )
    modules = []
    for module in pack.modules:
        status, reason = _module_applicability(module.id, system)
        modules.append(
            {
                "module": module.id,
                "edition": module.edition,
                "status": status,
                "reason": reason,
            }
        )
        if status == "incomplete":
            missing("module." + module.id, reason)
    by_module = {m["module"]: m for m in modules}
    rows = []
    for requirement in pack.requirements:
        choice = request.requirement_choices.get(requirement.id, RequirementChoice())
        systems = system.systems if choice.systems is None else choice.systems
        if not set(systems) <= set(system.systems):
            raise ValueError("Requirement systems exceed assessed scope")
        period = choice.period or request.period
        applicable = by_module[requirement.module]
        reason = applicable["reason"]
        matched = [
            r.artifact_ref
            for r in records
            if r.supports(requirement, systems, period, pack, basis.jurisdiction)
        ]
        if applicable["status"] == "not_applicable":
            status = "not_applicable"
        elif applicable["status"] == "incomplete":
            status = "applicability_missing"
        elif choice.not_applicable_reason:
            status, reason = "excluded", choice.not_applicable_reason
        elif matched:
            status = "evidence_recorded"
        else:
            status = (
                "partner_owned"
                if choice.responsible_entity == "partner"
                else "incomplete"
            )
        if status in {"incomplete", "partner_owned", "applicability_missing"}:
            missing(
                requirement.id,
                requirement.evidence_prompt,
                requirementId=requirement.id,
                systems=systems,
                period=period.model_dump(mode="json"),
                evidenceTypes=requirement.evidence_types,
                responsibleEntity=choice.responsible_entity,
            )
        if choice.responsible_entity == "partner" and not choice.responsibility_reason:
            missing(
                "responsibility." + requirement.id,
                "Identify the partner and agreed responsibility; partner ownership does not remove the evidence obligation.",
                requirementId=requirement.id,
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
                "evidenceRefs": matched,
            }
        )
    external = {
        name: {
            "status": "Unverified",
            "gap": gap,
            "verifiableBy": owner,
            "nextStep": step,
            "evidenceRefs": [
                r.artifact_ref for r in records if r.evidence_type == kind
            ],
        }
        for name, kind, gap, owner, step in [
            (
                "organizational",
                "organizational",
                "Private operational records have not been independently verified by MoonMind.",
                "Assessed business evidence owner",
                "Supply scoped procedures and period samples with issuer and version provenance.",
            ),
            (
                "laboratory",
                "laboratory",
                "Recording laboratory evidence does not perform an independent product evaluation.",
                "Qualified gaming laboratory",
                "Obtain or verify a laboratory report for the chosen edition, product and scope.",
            ),
            (
                "regulator",
                "regulator",
                "Edition publication and adoption do not approve this product.",
                "Relevant jurisdiction regulator",
                "Obtain or verify the regulator's product-specific approval and conditions.",
            ),
        ]
    }
    return {
        "schemaVersion": "gli-readiness/v1",
        "pack": pack.model_dump(mode="json", by_alias=True),
        "readiness": "incomplete" if requests else "prepared",
        "systemInputs": system.model_dump(mode="json", by_alias=True),
        "jurisdictionBasis": basis.model_dump(mode="json", by_alias=True),
        "period": request.period.model_dump(mode="json"),
        "applicability": {
            "status": (
                "incomplete"
                if not adoption_known
                or any(m["status"] == "incomplete" for m in modules)
                or not system.systems
                or not system.entity_name
                else "scoped"
            ),
            "reason": "; ".join(
                dict.fromkeys(
                    m["reason"] for m in modules if m["status"] == "incomplete"
                )
            )
            or "Scoped readiness baseline; adoption is distinct from product approval.",
        },
        "modules": modules,
        "requirements": rows,
        "unresolvedRequirements": [
            r["id"]
            for r in rows
            if r["status"] in {"incomplete", "partner_owned", "applicability_missing"}
        ],
        "evidence": [r.model_dump(mode="json", by_alias=True) for r in records],
        "evidenceRequests": requests,
        "externalVerification": external,
        "certification": "not_performed",
        "jurisdictionApproval": "not_performed",
        "limitations": [
            "Selected section-level readiness summaries are not exhaustive laboratory certification criteria. Specialized evidence may be added independently; absent evidence stays unresolved."
        ],
    }


def render_assessment(report: dict) -> str:
    # Include the complete scoped record, so exports retain exclusions, partner
    # ownership, provenance and requests rather than just a readiness headline.
    return (
        "## GLI-19 and GLI-GSF readiness\n\n"
        f"Edition: {report['pack']['edition']}; published: {report['pack']['publishedOn']}. "
        f"Readiness: {report['readiness']}. Certification and jurisdiction approval: not performed.\n\n"
        "Jurisdiction basis, assessed scope, evidence links, unresolved requirements and external verification:\n\n"
        "```json\n" + json.dumps(report, indent=2, ensure_ascii=False) + "\n```\n"
    )
