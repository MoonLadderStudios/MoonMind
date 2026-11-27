"""Installed readiness packs, their registered behaviors and chooser metadata.

Every built-in pack is installed ``declared-framework-pack/v1`` data under
``packs/``. A judgement that cannot be data is a registered behavior that the
pack names; the shared readiness evaluator runs both.
"""

import hashlib
from functools import cache
from pathlib import Path


def pack_subjects(pack) -> tuple[str, ...]:
    """Subjects a pack's plan can select, from the subjects its rows are tagged with.

    A pack whose every requirement names only endpoint or network subjects is
    for those subjects; any other pack also plans an application.
    """
    tagged = {
        subject
        for row in pack.requirements
        for subject in row.subject_types
        if subject in {"endpoint", "network"}
    }
    only = bool(pack.requirements) and all(
        row.subject_types and set(row.subject_types) <= {"endpoint", "network"}
        for row in pack.requirements
    )
    return tuple(
        subject
        for subject in ("application", "endpoint", "network")
        if subject in tagged or (subject == "application" and not only)
    )


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
    "owasp-asvs-l1",
    "endpoint-best-practices",
)
# Separately licensed packs live with their license and notice.
PACK_FILES = {"owasp-asvs-l1": "owasp-asvs/owasp-asvs-l1.json"}
# The catalog's general best-practices choice for applications: this pack plus
# native checks MoonMind already ships, each collected by its existing preset.
GENERAL_BEST_PRACTICES = "owasp-asvs-l1"
GENERAL_BEST_PRACTICE_CHECKS = (
    {
        "id": "known-vulnerabilities",
        "title": "Known-vulnerability scanning of dependencies and images with Trivy, prioritized with CISA KEV and FIRST EPSS where feed data exists",
        "presets": ["security-code-supply-chain", "security-image-sbom"],
    },
    {
        "id": "secrets",
        "title": "Secret detection with Gitleaks",
        "presets": ["security-code-supply-chain"],
    },
    {
        "id": "misconfiguration",
        "title": "Configuration checks with Trivy misconfiguration scanning",
        "presets": ["security-code-supply-chain"],
    },
)
EXTERNAL_ROLES = frozenset({"laboratory", "qsa", "auditor", "regulator"})


@cache
def installed_pack(pack_id: str):
    """Installed declared-pack bytes and their validated pack, by exact ID."""
    from moonmind.security.framework_packs import parse_declared_pack

    # The file name comes from the installed list, never from the caller.
    installed = next((known for known in INSTALLED_PACK_IDS if known == pack_id), None)
    if installed is None:
        raise ValueError(f"Unknown installed framework pack: {pack_id}")
    raw = (PACK_DIRECTORY / PACK_FILES.get(installed, f"{installed}.json")).read_bytes()
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


def evidence_kinds(pack, *, skill=False) -> tuple[list[str], list[str]]:
    """How a pack's requirements are evidenced, and which outside parties close them."""
    kinds, external = set(), set()
    for row in pack.requirements:
        types = {value.casefold() for value in row.evidence_types}
        if row.objectives or "deterministic_check" in {
            *row.required_evidence_methods,
            *types,
        }:
            kinds.add("deterministic_check")
        if (
            row.organisational_evidence
            or types & {"organizational", "organisational", "documentary"}
            or (
                not skill
                and set(row.required_evidence_methods) - {"deterministic_check"}
            )
        ):
            kinds.add("organisational_evidence")
        if any(
            p.method in {"security-review", "security-investigate"}
            for p in row.procedures
        ):
            kinds.add("agent_review")
        if row.not_assessed_reason:
            kinds.add("not_assessed")
        if row.external_claim:
            kinds.add("external_only")
        external.update(types & EXTERNAL_ROLES)
    if pack.profile is not None:
        # Readiness packs conclude from recorded organisational evidence.
        kinds.add("organisational_evidence")
    if external or any(
        claim.evidence_type not in {"organizational", "organisational"}
        for claim in pack.external_claims
    ):
        kinds.add("external_only")
    return sorted(kinds), sorted(external)


def readiness_catalog():
    from moonmind.security.frameworks import FrameworkPackSelection
    from moonmind.security.pack_readiness import profile_schema

    for pack_id in INSTALLED_PACK_IDS:
        pack = installed_pack(pack_id)[1]
        best_practices = pack_id == GENERAL_BEST_PRACTICES
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
            "subjects": subject_availability(*pack_subjects(pack)),
            "sector": pack.sector,
            "jurisdiction": pack.jurisdiction,
            "requirementCount": len(pack.requirements),
            "evidenceKinds": evidence_kinds(pack)[0],
            "externalEvidence": [
                claim.id
                for claim in pack.external_claims
                if claim.evidence_type not in {"organizational", "organisational"}
            ],
            "conclusions": pack.conclusions,
            # A pack without profile inputs takes the ordinary selection only.
            "inputSchema": (
                profile_schema(pack)
                if pack.profile is not None
                else FrameworkPackSelection.model_json_schema()
            ),
            "choice": "general-best-practices" if best_practices else None,
            "companionChecks": (
                [dict(check) for check in GENERAL_BEST_PRACTICE_CHECKS]
                if best_practices
                else []
            ),
            "available": True,
            "reason": None,
        }
