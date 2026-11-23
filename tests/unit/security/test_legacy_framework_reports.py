"""Temporary reader for retained pre-unification readiness reports (#5435, #5436)."""

import copy
import json
from pathlib import Path

import pytest

from moonmind.security.framework_packs import (
    compare_pack_metadata,
    compare_selected_frameworks,
)
from moonmind.security.legacy_framework_reports import (
    compare_legacy_report,
    legacy_pack_metadata,
    legacy_readiness_report,
    render_legacy_summary,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/security"
RETAINED = {
    **json.loads((FIXTURES / "legacy_gaming_reports.json").read_text())["reports"],
    # ISO/IEC 27001, SOC 2 and UKGC RTS reports written before #5436.
    **json.loads((FIXTURES / "legacy_readiness_reports.json").read_text())["reports"],
}
LEGACY_STATUS = {"evidence_reviewed": "evidence_recorded", "unassessed": "incomplete"}


def current(previous, **changes):
    readiness = copy.deepcopy(previous["readiness"])
    pack = {
        "id": previous["id"],
        "edition": previous["edition"],
        "readiness": readiness,
        "diagnostics": [],
        "previousAssessmentRef": "art_previous",
    }
    pack.update(changes)
    return pack


@pytest.mark.parametrize("schema", sorted(RETAINED))
def test_retained_report_reads_as_comparable_pack_metadata(schema):
    from moonmind.security.framework_registry import installed_pack
    from moonmind.security.frameworks import saved_profile_and_choices

    report = RETAINED[schema]
    assert legacy_readiness_report(report)
    [previous] = legacy_pack_metadata(report)
    assert (previous["id"], previous["edition"], previous["legacySchema"]) == (
        report["pack"]["id"],
        report["pack"]["edition"],
        report["schemaVersion"],
    )
    statuses = previous["readiness"]["requirementStatuses"]
    # Renamed statuses are the same conclusions, not readiness changes.
    assert statuses == {
        row["id"]: LEGACY_STATUS.get(row["status"], row["status"])
        for row in report["requirements"]
    }
    pack = installed_pack(report["pack"]["id"])[1]
    profile, _ = saved_profile_and_choices(pack, report["systemInputs"], {})
    assert previous["readiness"]["profile"] == profile
    assert "requirementEvidence" not in previous["readiness"]["profile"]
    assert report["schemaVersion"] in render_legacy_summary(previous)


@pytest.mark.parametrize("schema", sorted(RETAINED))
def test_matching_requirements_compare_and_the_rest_is_unverified(schema):
    [previous] = legacy_pack_metadata(RETAINED[schema])
    unchanged = compare_legacy_report(current(previous), previous)
    assert len(unchanged) == 1 and "Unverified" in unchanged[0]

    pack = current(previous, edition="2099")
    statuses = pack["readiness"]["requirementStatuses"]
    changed_id, removed_id = list(statuses)[:2]
    statuses[changed_id] = "Unverified"
    del statuses[removed_id]
    pack["readiness"]["profile"] = {"entityName": "Changed"}
    changes = compare_pack_metadata(pack, previous)
    assert any("edition changed" in row for row in changes)
    assert any("profile inputs changed" in row for row in changes)
    assert any(changed_id in row and "Unverified" in row for row in changes)
    assert any(
        removed_id in row and "only in the previous report" in row for row in changes
    )


def test_unique_pack_lineage_survives_the_edition_rename():
    # NJ editions became identifiers; the unique pack ID keeps the lineage.
    [previous] = legacy_pack_metadata(RETAINED["framework-audit-preparation/v1"])
    pack = current(previous, edition="2026-11-07")
    compare_selected_frameworks([pack], {"art_previous": [previous]})
    assert pack["previousAssessmentStatus"] == "available"
    assert "previousOnlyFrameworks" not in pack
    assert pack["previousReportSummary"].startswith(
        "Previous framework-audit-preparation/v1 report"
    )


def test_retained_iso_report_keeps_its_statement_of_applicability_basis():
    report = RETAINED["iso-27001-readiness/v1"]
    [previous] = legacy_pack_metadata(report)
    readiness = previous["readiness"]
    assert readiness["statementOfApplicability"] == report["statementOfApplicability"]
    assert set(readiness["requirementBasis"]) == {
        row["id"]
        for row in report["requirements"]
        if row["category"] == "management_system"
    }
    assert readiness["readiness"] == "incomplete"
    assert readiness["period"] == report["period"]


def test_other_schemas_and_malformed_reports_are_not_read():
    retired = dict(RETAINED["framework-audit-preparation/v1"])
    retired["pack"] = dict(retired["pack"], id="retired-readiness")
    assert not legacy_readiness_report(retired)
    broken = copy.deepcopy(RETAINED["gli-readiness/v1"])
    broken["requirements"][0].pop("status")
    with pytest.raises(ValueError):
        legacy_pack_metadata(broken)
