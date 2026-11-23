"""Registered readiness pack owners and public chooser metadata.

Two pack contracts coexist while the remaining built-ins move: ISO/IEC 27001,
SOC 2 and UKGC RTS still use ``framework-pack/v1`` modules below; GLI-19/GSF and
New Jersey DGE are installed ``declared-framework-pack/v1`` data evaluated on
the shared control evaluator with their registered behaviors.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache, partial
from pathlib import Path

from pydantic import BaseModel

from moonmind.schemas.form_fields import form_fields_schema
from moonmind.security import iso_27001, soc2_readiness, ukgc_rts
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


PACK_DIRECTORY = Path(__file__).with_name("packs")
INSTALLED_PACK_IDS = ("gli-19-readiness", "nj-dge-readiness")


@cache
def installed_pack(pack_id: str):
    """Installed declared-pack bytes and their validated pack, by exact ID."""
    from moonmind.security.framework_packs import parse_declared_pack

    # The file name comes from the installed list, never from the caller.
    installed = next((known for known in INSTALLED_PACK_IDS if known == pack_id), None)
    if installed is None:
        raise ValueError(f"Unknown installed framework pack: {pack_id}")
    raw = (PACK_DIRECTORY / f"{installed}.json").read_bytes()
    return raw, parse_declared_pack(raw)


def installed_digest(pack_id: str) -> str:
    return hashlib.sha256(installed_pack(pack_id)[0]).hexdigest()


def _behaviors():
    from moonmind.security.gaming_pack_behaviors import GLI_BEHAVIOR, NJ_BEHAVIOR

    return {
        "gli-module-applicability": GLI_BEHAVIOR,
        "nj-annual-assessment": NJ_BEHAVIOR,
    }


def pack_behavior(pack):
    """The registered judgement a pack names, or the shared data-only default."""
    from moonmind.security.pack_readiness import DEFAULT_BEHAVIOR

    if pack.behavior is None:
        return DEFAULT_BEHAVIOR
    try:
        return _behaviors()[pack.behavior]
    except KeyError:
        raise ValueError(
            f"framework pack names an unregistered behavior: {pack.behavior}"
        ) from None


def framework_input_schema(pack_id: str) -> dict:
    """Typed Create fields for a built-in pack's saved system or profile inputs."""
    if pack_id in INSTALLED_PACK_IDS:
        from moonmind.security.pack_readiness import profile_schema

        return profile_schema(installed_pack(pack_id)[1])
    return readiness_pack(pack_id).input_schema()


def standard_requirement_ids(pack_id: str, edition: str) -> set[str] | None:
    """Requirement IDs of an installed catalog standard at its exact edition."""
    if pack_id in INSTALLED_PACK_IDS:
        pack = installed_pack(pack_id)[1]
    elif pack_id in READINESS_PACKS:
        pack = READINESS_PACKS[pack_id].pack
    else:
        return None
    return {row.id for row in pack.requirements} if pack.edition == edition else None


def _installed_catalog():
    from moonmind.security.pack_readiness import profile_schema

    for pack_id in INSTALLED_PACK_IDS:
        pack = installed_pack(pack_id)[1]
        yield {
            "source": "builtin",
            "id": pack.id,
            "title": pack.title,
            "edition": pack.edition,
            "revision": pack.revision,
            "sha256": installed_digest(pack_id),
            "amendments": list(pack.amendments),
            "publicationStatus": pack.publication_status,
            "publishedOn": pack.published_on,
            "effectiveFrom": pack.effective_from,
            "effectiveUntil": pack.effective_until,
            "license": pack.license,
            "distribution": pack.distribution,
            "subjects": subject_availability("application"),
            "sector": pack.sector,
            "jurisdiction": pack.jurisdiction,
            "requirementCount": len(pack.requirements),
            "evidenceKinds": ["organisational_evidence", "external_only"],
            "externalEvidence": [
                claim.id
                for claim in pack.external_claims
                if claim.evidence_type not in {"organizational", "organisational"}
            ],
            "conclusions": pack.conclusions,
            "inputSchema": profile_schema(pack),
            "available": True,
            "reason": None,
        }


def readiness_catalog():
    yield from _installed_catalog()
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
