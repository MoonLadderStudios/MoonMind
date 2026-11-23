"""Installed readiness packs, their registered behaviors and chooser metadata.

Every built-in pack is installed ``declared-framework-pack/v1`` data under
``packs/``. A judgement that cannot be data is a registered behavior that the
pack names; the shared readiness evaluator runs both.
"""

import hashlib
from functools import cache
from pathlib import Path


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


PACK_DIRECTORY = Path(__file__).with_name("packs")
INSTALLED_PACK_IDS = (
    "gli-19-readiness",
    "nj-dge-readiness",
    "pci-dss-readiness",
    "iso-27001-readiness",
    "soc2-readiness",
    "ukgc-rts-security",
)


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


@cache
def _behaviors():
    from moonmind.security.gaming_pack_behaviors import GLI_BEHAVIOR, NJ_BEHAVIOR
    from moonmind.security.iso_27001_behavior import ISO_BEHAVIOR
    from moonmind.security.soc2_behavior import SOC2_BEHAVIOR
    from moonmind.security.ukgc_rts_behavior import UKGC_BEHAVIOR

    return {
        "gli-module-applicability": GLI_BEHAVIOR,
        "nj-annual-assessment": NJ_BEHAVIOR,
        "iso-27001-statement-of-applicability": ISO_BEHAVIOR,
        "soc2-period-sampling": SOC2_BEHAVIOR,
        "ukgc-rts-audit-record": UKGC_BEHAVIOR,
    }


def registered_behavior(name: str | None):
    """The registered judgement named ``name``, or the shared data-only default."""
    from moonmind.security.pack_readiness import DEFAULT_BEHAVIOR

    if name is None:
        return DEFAULT_BEHAVIOR
    try:
        return _behaviors()[name]
    except KeyError:
        raise ValueError(
            f"framework pack names an unregistered behavior: {name}"
        ) from None


def pack_behavior(pack):
    """The registered judgement a pack names, or the shared data-only default."""
    return registered_behavior(pack.behavior)


def framework_input_schema(pack_id: str) -> dict:
    """Typed Create fields for a built-in pack's saved profile inputs."""
    from moonmind.security.pack_readiness import profile_schema

    return profile_schema(installed_pack(pack_id)[1])


def standard_requirement_ids(pack_id: str, edition: str) -> set[str] | None:
    """Requirement IDs of an installed catalog standard at its exact edition."""
    if pack_id not in INSTALLED_PACK_IDS:
        return None
    pack = installed_pack(pack_id)[1]
    return {row.id for row in pack.requirements} if pack.edition == edition else None


def readiness_catalog():
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
