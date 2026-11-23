"""New Jersey DGE readiness as installed declared-pack data (#5196, #5435)."""

import copy
import json
from pathlib import Path

import pytest

from moonmind.security.framework_registry import installed_pack, pack_behavior
from moonmind.security.frameworks import EvidencePeriod
from moonmind.security.pack_readiness import evaluate_readiness, parse_evidence

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/security/nj_dge.json"
ANNUAL = {"nj.internet.annual", "nj.network.annual", "nj.rgs.annual"}
PACK_ID = "nj-dge-readiness"


def fixture():
    return json.loads(FIXTURE.read_text())


def request_payload(data=None):
    data = data or fixture()
    return {
        "scope": {"assets": data["framework"]["systemInputs"]["systems"]},
        "tools": [],
        "budget": {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
        "credentialRefs": [],
        "networkRefs": [],
        "framework": data["framework"],
    }


class Report(dict):
    """Rows and readiness context in the shape the assertions below read."""


def evaluate(data=None):
    data = data or fixture()
    framework = data["framework"]
    pack = installed_pack(PACK_ID)[1]
    behavior = pack_behavior(pack)
    result = evaluate_readiness(
        pack,
        behavior,
        profile=framework["systemInputs"],
        period=EvidencePeriod.model_validate(framework["period"]),
        choices=framework.get("requirementChoices", {}),
        records=[
            parse_evidence(behavior, row, f"art_evidence_{i}")
            for i, row in enumerate(data["evidence"])
        ],
        audit=behavior.records_model.model_validate(data["audit"]),
        admitted_assets=framework["systemInputs"].get("systems", []),
    )
    report = Report(result.context)
    by_id = {r.id: r for r in pack.requirements}
    report["requirements"] = [
        {
            **row,
            "id": key,
            "title": by_id[key].title,
            "source": by_id[key].sources[0].url,
            "systems": row["systems"],
            "period": row["period"].model_dump(mode="json"),
        }
        for key, row in result.rows.items()
    ]
    return report


def rows(report):
    return {r["id"]: r for r in report["requirements"]}


@pytest.mark.parametrize(
    "profile, applicable",
    [
        ("casino_licensee", {"nj.internet.annual", "nj.network.annual"}),
        ("rgs_content_provider", {"nj.rgs.annual"}),
        ("unrelated_supplier", set()),
    ],
)
def test_entity_rule_applicability(profile, applicable):
    data = fixture()
    data["framework"]["systemInputs"] = data["profiles"][profile]
    data["audit"] = {}
    data["evidence"] = []
    report = evaluate(data)
    assert {
        r["id"]
        for r in report["requirements"]
        if r["id"] in ANNUAL and r["applicabilityStatus"] == "applicable"
    } == applicable
    assert all(r["reason"] and r["source"] for r in report["requirements"])
    assert report["applicability"]["status"] == "scoped"
    assert report["reportingConditions"]["annualReportToDivision"] == bool(applicable)


def test_rgs_content_to_another_operator_condition_and_selected_rule_applicability():
    data = fixture()
    data["framework"]["systemInputs"]["providesContentToOtherOperator"] = False
    report = evaluate(data)
    assert rows(report)["nj.rgs.annual"]["status"] == "not_applicable"
    assert rows(report)["nj.rgs.incidents"]["applicabilityStatus"] == "applicable"
    data["framework"]["systemInputs"]["providesContentToOtherOperator"] = True
    data["framework"]["systemInputs"]["annualRules"] = ["nj.network.annual"]
    report = evaluate(data)
    assert rows(report)["nj.rgs.annual"]["status"] == "not_selected"
    assert rows(report)["nj.network.annual"]["status"] == "not_applicable"
    assert not any(r["id"].startswith("annual.") for r in report["evidenceRequests"])


@pytest.mark.parametrize(
    "offers_internet, status", [(False, "not_applicable"), (None, "Unverified")]
)
def test_casino_internet_activity_applicability(offers_internet, status):
    data = fixture()
    data["framework"]["systemInputs"] = data["profiles"]["casino_licensee"]
    data["framework"]["systemInputs"]["offersInternetGaming"] = offers_internet
    data["audit"], data["evidence"] = {}, []
    report = evaluate(data)
    assert rows(report)["nj.internet.annual"]["status"] == status
    assert rows(report)["nj.network.annual"]["applicabilityStatus"] == "applicable"


def test_omitted_annual_rules_evaluate_the_documented_default_selection():
    implicit = evaluate()
    data = fixture()
    data["framework"]["systemInputs"]["annualRules"] = sorted(ANNUAL)
    explicit = evaluate(data)
    assert explicit["requirements"] == implicit["requirements"]
    assert explicit["evidenceRequests"] == implicit["evidenceRequests"]


def test_no_reportable_incidents_still_requires_scoped_evidence():
    data = fixture()
    record = data["evidence"][2]
    record["incidentDisposition"] = "no_reportable_incidents"
    record.pop("notificationRef")
    assert rows(evaluate(data))["nj.rgs.incidents"]["status"] == "evidence_recorded"
    record["method"] = "enquiry"
    assert rows(evaluate(data))["nj.rgs.incidents"]["status"] == "incomplete"


@pytest.mark.parametrize("field", ["entityType", "providesContentToOtherOperator"])
def test_unknown_applicability_requests_inputs_without_inherited_pass(field):
    data = fixture()
    data["framework"]["systemInputs"].pop(field)
    report = evaluate(data)
    assert rows(report)["nj.rgs.annual"]["status"] == "Unverified"
    assert any(
        r["requirementId"] == "nj.rgs.annual" for r in report["evidenceRequests"]
    )


def test_annual_report_requires_each_field_and_independent_professional_approval():
    data = fixture()
    data["audit"] = {}
    report = evaluate(data)
    prompts = {r["id"] for r in report["evidenceRequests"]}
    for field in (
        "scope",
        "systems",
        "period",
        "assessors",
        "assessedOn",
        "findings",
        "correctiveActions",
        "licenseeResponse",
        "opinion",
        "independence",
        "professionalEvidenceRef",
        "divisionApprovalRef",
        "reportRef",
        "submissionRef",
    ):
        assert f"annual.nj.rgs.annual.{field}" in prompts
    assert rows(report)["nj.rgs.annual"]["status"] == "incomplete"
    assert (
        rows(report)["nj.rgs.annual"]["title"]
        == "RGS annual system integrity and security assessment"
    )
    data["audit"] = fixture()["audit"]
    data["audit"]["assessments"][0]["assessors"] = [{"name": "Named only"}]
    prompts = {r["id"] for r in evaluate(data)["evidenceRequests"]}
    assert "annual.nj.rgs.annual.assessors.0.affiliation" in prompts
    assert "annual.nj.rgs.annual.independence" in prompts


def test_scoped_evidence_never_inherits_approval_incident_or_partner_status():
    data = fixture()
    for row in data["evidence"]:
        for key in (
            "divisionApprovalRef",
            "operatorEvidenceRef",
            "incidentDisposition",
            "notificationRef",
        ):
            row.pop(key, None)
    report = evaluate(data)
    for requirement in (
        "nj.rgs.internal-controls",
        "nj.rgs.games-releases",
        "nj.rgs.incidents",
        "nj.rgs.transactions",
        "nj.rgs.integration",
        "nj.partner.player-accounts",
    ):
        assert rows(report)[requirement]["status"] == "incomplete"
        assert any(
            r["requirementId"] == requirement and r["source"] and r["prompt"]
            for r in report["evidenceRequests"]
        )


def test_source_snapshot_modules_and_external_claims_are_explicit():
    report = evaluate()
    assert report["readiness"] == "prepared"
    modules = {m["module"]: m for m in report["modules"]}
    assert {"13:69O-1.2", "13:69D-2.4", "13:69O-3.1"} <= set(modules)
    assert all(m["edition"] for m in modules.values())
    # An unverified rule source stays marked Unverified with its reason.
    assert {key for key, m in modules.items() if m["sourceStatus"] == "Unverified"} == {
        "dge-rule-index",
        "dge-tsb-guidance",
    }
    assert all(m["statusReason"] for m in modules.values())
    claims = report["externalClaims"]
    assert claims["source-completeness"]["status"] == "Unverified"
    assert claims["gli-adoption"]["status"] == "Unverified"
    for name in ("laboratory", "regulator", "customer", "professional-assessment"):
        assert claims[name]["status"] == "Unverified"
        assert claims[name]["verifiableBy"] and claims[name]["nextStep"]
    assert report["reportingConditions"]["submissionPerformed"] is False
    assert installed_pack(PACK_ID)[1].distribution == "redistributable"


def test_requirement_specific_scope_period_and_exclusion_evidence():
    data = fixture()
    data["framework"]["systemInputs"]["systems"].append("backup")
    data["framework"]["requirementChoices"] = {
        "nj.rgs.incidents": {
            "systems": ["rgs"],
            "period": {"start": "2026-03-01", "end": "2026-06-30"},
        },
        "nj.partner.player-accounts": {"notApplicableReason": "Partner owns accounts"},
    }
    report = evaluate(data)
    incident = rows(report)["nj.rgs.incidents"]
    assert incident["status"] == "evidence_recorded"
    assert incident["systems"] == ["rgs"]
    assert incident["period"]["start"] == "2026-03-01"
    assert (
        rows(report)["nj.partner.player-accounts"]["status"] == "exclusion_for_review"
    )
    assert rows(report)["nj.rgs.games-releases"]["status"] == "incomplete"
    assert rows(report)["nj.rgs.annual"]["status"] == "incomplete"


@pytest.mark.parametrize(
    "change",
    [
        "unknown_rule",
        "unknown_choice",
        "outside_system",
        "duplicate_annual",
        "outside_annual",
    ],
)
def test_invalid_requirement_or_scope_is_rejected(change):
    data = fixture()
    if change == "unknown_rule":
        data["framework"]["systemInputs"]["annualRules"] = ["invented"]
    elif change == "unknown_choice":
        data["framework"]["requirementChoices"] = {"invented": {}}
    elif change == "outside_system":
        data["framework"]["requirementChoices"] = {
            "nj.rgs.incidents": {"systems": ["outside"]}
        }
    elif change == "duplicate_annual":
        data["audit"]["assessments"].append(
            copy.deepcopy(data["audit"]["assessments"][0])
        )
    else:
        data["audit"]["assessments"][0]["systems"] = ["outside"]
    with pytest.raises(ValueError):
        evaluate(data)
