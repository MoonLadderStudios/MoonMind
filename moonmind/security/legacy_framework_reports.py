"""Temporary reader for retained gaming readiness reports.

GLI-19/GSF and New Jersey DGE used to write their own report schemas
(``gli-readiness/v1`` and ``framework-audit-preparation/v1``). Retained reports
stay immutable. This reader lets a new assessment use one as its
``previousAssessmentRef``: it compares matching requirement IDs and marks
everything else Unverified, and it summarises the retained report for the new
assessment's Markdown export.

Consumers: ``AssessmentExecution.previous_framework_metadata`` (comparison),
``compare_pack_metadata`` (intelligence changes) and
``render_control_assessment`` (previous-report summary).

Removal condition: delete this module when no retained report under either
schema is still inside assessment retention, or when the issue that retires the
legacy ``framework-pack/v1`` contract migrates those reports.
"""

from __future__ import annotations

# The UKGC RTS pack still writes framework-audit-preparation/v1 on its own
# legacy path, so only these two pack IDs are read here.
LEGACY_SCHEMAS = {
    ("gli-readiness/v1", "gli-19-readiness"),
    ("framework-audit-preparation/v1", "nj-dge-readiness"),
}


def legacy_gaming_report(document) -> bool:
    return (
        isinstance(document, dict)
        and isinstance(document.get("pack"), dict)
        and (document.get("schemaVersion"), document["pack"].get("id")) in LEGACY_SCHEMAS
    )


def legacy_pack_metadata(document) -> list[dict]:
    """Comparable identity of a retained report; raises ValueError when malformed."""
    if not legacy_gaming_report(document):
        raise ValueError("not a retained gaming readiness report")
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
    return [
        {
            "id": pack["id"],
            "edition": edition,
            "legacySchema": document["schemaVersion"],
            "readiness": {
                "readiness": document.get("readiness"),
                "profile": document.get("systemInputs"),
                "requirementStatuses": {row["id"]: row["status"] for row in rows},
                "unresolvedRequirements": [
                    row["id"]
                    for row in rows
                    if row["status"]
                    in {"incomplete", "partner_owned", "applicability_missing", "Unverified"}
                ],
            },
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
    changed = sorted(key for key in statuses.keys() & prior.keys() if statuses[key] != prior[key])
    if changed:
        changes.append(
            "Requirement readiness changed: "
            + ", ".join(f"{key} {prior[key]} -> {statuses[key]}" for key in changed[:16])
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
