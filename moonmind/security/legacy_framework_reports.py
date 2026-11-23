"""Temporary reader for retained pre-unification readiness reports.

Before every built-in pack used the declared pack contract, GLI-19/GSF, New
Jersey DGE, ISO/IEC 27001, SOC 2 and UKGC RTS wrote their own report schemas.
Retained reports stay immutable. This reader lets a new assessment use one as
its ``previousAssessmentRef``: it compares matching requirement IDs, marks
everything else Unverified and summarises the retained report for the new
assessment's Markdown export. An ISO/IEC 27001 report also keeps its Statement
of Applicability and management-system basis, so reassessment names the
decisions and requirements to review.

Consumers: ``AssessmentExecution.previous_framework_metadata`` (comparison),
``compare_pack_metadata`` (intelligence changes), ``compare_selected_frameworks``
(the previous-report summary rendered in the new report) and the evidence
export's schema check (``evidence_exports._validate_json``).

Removal condition: no producer writes these schemas any more, so delete this
module once no retained report under them is still inside assessment
retention. Until then it is the only path that keeps those reports usable.
"""

from __future__ import annotations

# The UKGC and NJ reports share framework-audit-preparation/v1; the pack ID
# tells them apart.
LEGACY_SCHEMAS = {
    ("gli-readiness/v1", "gli-19-readiness"),
    ("framework-audit-preparation/v1", "nj-dge-readiness"),
    ("framework-audit-preparation/v1", "ukgc-rts-security"),
    ("iso-27001-readiness/v1", "iso-27001-readiness"),
    ("soc2-readiness/v1", "soc2-readiness"),
}
UNRESOLVED = {"incomplete", "partner_owned", "applicability_missing", "Unverified"}
# Legacy status words for the same conclusions, so a renamed status is not
# reported as a readiness change.
STATUS = {"evidence_reviewed": "evidence_recorded", "unassessed": "incomplete"}


def legacy_readiness_report(document) -> bool:
    return (
        isinstance(document, dict)
        and isinstance(document.get("pack"), dict)
        and (document.get("schemaVersion"), document["pack"].get("id"))
        in LEGACY_SCHEMAS
    )


def _profile(document) -> dict | None:
    """Saved system inputs in the installed pack's profile shape, when possible."""
    system = document.get("systemInputs")
    if not isinstance(system, dict):
        return system
    from moonmind.security.framework_registry import installed_pack
    from moonmind.security.frameworks import saved_profile_and_choices

    try:
        pack = installed_pack(document["pack"]["id"])[1]
        profile, _ = saved_profile_and_choices(pack, system, {})
    except ValueError:
        return system
    return profile


def legacy_pack_metadata(document) -> list[dict]:
    """Comparable identity of a retained report; raises ValueError when malformed."""
    if not legacy_readiness_report(document):
        raise ValueError("not a retained readiness report")
    pack = document["pack"]
    rows = document.get("requirements")
    if not isinstance(rows, list) or not all(
        isinstance(row, dict)
        and isinstance(row.get("id"), str)
        and isinstance(row.get("status"), str)
        for row in rows
    ):
        raise ValueError("retained report requirements are malformed")
    edition = pack.get("edition")
    if not isinstance(edition, str):
        raise ValueError("retained report edition is malformed")
    statuses = {row["id"]: STATUS.get(row["status"], row["status"]) for row in rows}
    readiness = {
        "readiness": (
            "prepared"
            if document.get("readiness") == "evidence_reviewed"
            else document.get("readiness")
        ),
        "profile": _profile(document),
        "period": document.get("period"),
        "requirementStatuses": statuses,
        "unresolvedRequirements": [
            key for key, status in statuses.items() if status in UNRESOLVED
        ],
    }
    soa = document.get("statementOfApplicability")
    if pack["id"] == "iso-27001-readiness" and isinstance(soa, dict):
        readiness["statementOfApplicability"] = soa
        readiness["requirementBasis"] = {
            row["id"]: {
                "systems": sorted(set(row.get("systems") or [])),
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
            for row in rows
            if row.get("category") == "management_system"
        }
    return [
        {
            "id": pack["id"],
            "edition": edition,
            "legacySchema": document["schemaVersion"],
            "readiness": readiness,
        }
    ]


def compare_legacy_report(current: dict, previous: dict) -> list[str]:
    """Compare matching requirement IDs; the rest of the comparison is Unverified."""
    changes = [
        f"Previous report uses {previous['legacySchema']}; pack revision and content digests are Unverified, and matching requirement IDs are compared instead."
    ]
    if current.get("edition") != previous.get("edition"):
        changes.append(
            f"Framework edition changed from {previous.get('edition')}; this is not remediation evidence."
        )
    now = current.get("readiness") or {}
    before = previous.get("readiness") or {}
    if now.get("profile") is not None and now.get("profile") != before.get("profile"):
        changes.append(
            "Framework profile inputs changed; reassess applicability before comparing conclusions."
        )
    statuses = now.get("requirementStatuses") or {}
    prior = before.get("requirementStatuses") or {}
    changed = sorted(
        key for key in statuses.keys() & prior.keys() if statuses[key] != prior[key]
    )
    if changed:
        changes.append(
            "Requirement readiness changed: "
            + ", ".join(
                f"{key} {prior[key]} -> {statuses[key]}" for key in changed[:16]
            )
            + (f" and {len(changed) - 16} more" if len(changed) > 16 else "")
            + "."
        )
    missing = sorted(prior.keys() - statuses.keys())
    if missing:
        changes.append(
            "Requirements only in the previous report are Unverified in this assessment: "
            + ", ".join(missing[:16])
            + "."
        )
    return changes


def render_legacy_summary(previous: dict) -> str:
    readiness = previous.get("readiness") or {}
    return (
        f"Previous {previous['legacySchema']} report ({previous['id']} / {previous['edition']}): "
        f"readiness {readiness.get('readiness')}; "
        f"{len(readiness.get('unresolvedRequirements') or [])} unresolved requirements."
    )
