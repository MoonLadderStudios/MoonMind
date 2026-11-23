"""Temporary reader for retained pre-unification gaming reports (#5435)."""

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
    legacy_gaming_report,
    legacy_pack_metadata,
    render_legacy_summary,
)

RETAINED = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "fixtures/security/legacy_gaming_reports.json"
    ).read_text()
)["reports"]


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
    report = RETAINED[schema]
    assert legacy_gaming_report(report)
    [previous] = legacy_pack_metadata(report)
    assert (previous["id"], previous["edition"], previous["legacySchema"]) == (
        report["pack"]["id"],
        report["pack"]["edition"],
        schema,
    )
    statuses = previous["readiness"]["requirementStatuses"]
    assert statuses == {row["id"]: row["status"] for row in report["requirements"]}
    assert previous["readiness"]["profile"] == report["systemInputs"]
    assert schema in render_legacy_summary(previous)


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


def test_other_schemas_and_malformed_reports_are_not_read():
    ukgc = dict(RETAINED["framework-audit-preparation/v1"])
    ukgc["pack"] = dict(ukgc["pack"], id="ukgc-rts-security")
    assert not legacy_gaming_report(ukgc)
    broken = copy.deepcopy(RETAINED["gli-readiness/v1"])
    broken["requirements"][0].pop("status")
    with pytest.raises(ValueError):
        legacy_pack_metadata(broken)
