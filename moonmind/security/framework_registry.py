"""Registered readiness pack owners and public chooser metadata."""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from pydantic import BaseModel

from moonmind.schemas.form_fields import form_fields_schema
from moonmind.security import gli_readiness, iso_27001, nj_dge, soc2_readiness, ukgc_rts
from moonmind.security.builtin_frameworks import (
    prepare_builtin_framework,
    prepare_scoped_framework,
)
from moonmind.security.frameworks import FrameworkEvidence, FrameworkPack


@dataclass(frozen=True)
class ReadinessPack:
    definition: Callable[[], FrameworkPack]
    input_model: type[BaseModel]
    prepare: Callable
    render: Callable
    sector: str | None
    jurisdiction: str | None
    external_evidence: tuple[str, ...]
    conclusion: str
    amendments: tuple[str, ...] = ()
    admits_requirement_evidence: bool = False
    output_fields: tuple[tuple[str, str], ...] = ()

    def input_schema(self):
        return form_fields_schema(
            self.input_model.model_json_schema(by_alias=True), include_defaults=False
        )

    @property
    def pack(self) -> FrameworkPack:
        return self.definition()


def subject_availability(*supported):
    return {
        subject: (
            {"available": True}
            if subject in supported
            else {
                "available": False,
                "reason": f"No installed journey for this pack assesses a standalone {subject}.",
            }
        )
        for subject in ("application", "endpoint", "network")
    }


def _critical_systems(system):
    return [
        s for group in system.critical_systems if group.in_scope for s in group.systems
    ]


READINESS_PACKS = {
    entry.pack.id: entry
    for entry in (
        ReadinessPack(
            lambda: iso_27001.ISO_PACK,
            iso_27001.SystemInputs,
            partial(prepare_builtin_framework, module=iso_27001),
            iso_27001.render_assessment,
            None,
            None,
            ("certification auditor",),
            "ISMS readiness and gaps. Certification requires an independent audit.",
            tuple(module.id for module in iso_27001.ISO_PACK.modules),
            admits_requirement_evidence=True,
        ),
        ReadinessPack(
            lambda: soc2_readiness.SOC2_PACK,
            soc2_readiness.SystemInputs,
            partial(prepare_builtin_framework, module=soc2_readiness),
            soc2_readiness.render_assessment,
            "Service organisations",
            None,
            ("independent CPA",),
            "SOC 2 readiness. An independent CPA issues the examination report.",
            ("2022 revised points of focus and implementation guidance",),
            admits_requirement_evidence=True,
        ),
        ReadinessPack(
            lambda: gli_readiness.GLI_PACK,
            gli_readiness.GLISystemInputs,
            partial(
                prepare_scoped_framework,
                evidence_type=gli_readiness.GLIEvidence,
                prepare=gli_readiness.prepare_assessment,
                system_refs=lambda system: (system.jurisdiction_basis.source_ref,),
                evidence_guidance=" Include evidenceType, outcome, edition and provenance.standard (module and edition); regulator evidence also needs jurisdiction.",
            ),
            gli_readiness.render_assessment,
            "Interactive gaming",
            None,
            ("laboratory", "regulator"),
            "Readiness against the recorded jurisdiction basis. Laboratory testing and regulatory approval remain external.",
            output_fields=(
                ("frameworkJurisdictionBasis", "jurisdictionBasis"),
                ("frameworkScope", "systemInputs"),
                ("frameworkUnresolvedRequirements", "unresolvedRequirements"),
                ("externalVerification", "externalVerification"),
            ),
        ),
        ReadinessPack(
            lambda: nj_dge.NJ_DGE_PACK,
            nj_dge.NJSystemInputs,
            partial(
                prepare_scoped_framework,
                evidence_type=nj_dge.NJEvidence,
                prepare=nj_dge.prepare_assessment,
                audit_type=nj_dge.AnnualAssessmentRecords,
                evidence_refs_for=nj_dge.NJEvidence.supporting_refs,
                audit_refs=nj_dge.AnnualAssessmentRecords.supporting_refs,
            ),
            nj_dge.render_assessment,
            "Gaming",
            "US-NJ",
            ("laboratory", "regulator", "auditor"),
            "Readiness for the selected New Jersey provisions. DGE approval and independent testing remain external.",
            tuple(
                source.edition
                for source in nj_dge.NJ_DGE_PACK.rule_sources
                if source.status == "reviewed_publication"
            ),
        ),
        ReadinessPack(
            lambda: ukgc_rts.UKGC_RTS_PACK,
            ukgc_rts.UKGCSystemInputs,
            partial(
                prepare_scoped_framework,
                evidence_type=FrameworkEvidence,
                prepare=ukgc_rts.prepare_audit,
                audit_type=ukgc_rts.AuditRecord,
                scoped_systems=_critical_systems,
                audit_refs=lambda audit: (
                    [audit.auditor.independence_ref]
                    if audit.auditor.independence_ref
                    else []
                ),
            ),
            ukgc_rts.render_audit_preparation,
            "Remote gambling",
            "Great Britain",
            ("auditor", "regulator"),
            "Audit preparation for scoped RTS controls. The independent audit and regulator decisions remain external.",
        ),
    )
}


def readiness_pack(pack_id: str) -> ReadinessPack:
    try:
        return READINESS_PACKS[pack_id]
    except KeyError:
        raise ValueError(f"Unknown framework pack: {pack_id}") from None


def readiness_catalog():
    for entry in READINESS_PACKS.values():
        pack = entry.pack
        yield {
            "source": "builtin",
            "id": pack.id,
            "title": pack.title,
            "edition": pack.edition,
            "amendments": list(entry.amendments),
            "publicationStatus": "published",
            "publishedOn": pack.published_on,
            "effectiveFrom": None,
            "effectiveUntil": None,
            "license": "Apache-2.0 for original MoonMind summaries. Referenced standards retain their own rights.",
            "distribution": "redistributable",
            "subjects": subject_availability("application"),
            "sector": entry.sector,
            "jurisdiction": entry.jurisdiction,
            "requirementCount": len(pack.requirements),
            "evidenceKinds": ["organisational_evidence", "external_only"],
            "externalEvidence": list(entry.external_evidence),
            "conclusions": entry.conclusion,
            "inputSchema": entry.input_schema(),
            "available": True,
            "reason": None,
        }
