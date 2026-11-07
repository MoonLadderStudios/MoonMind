"""UKGC RTS readiness behaviors for MoonLadderStudios/MoonMind#5197."""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from moonmind.security.assessments import AssessmentRequest

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/security/ukgc_rts.json"


def fixture():
    return json.loads(FIXTURE.read_text())


def request_payload():
    return {
        "scope": {"assets": ["casino"]},
        "tools": [],
        "budget": {"baseSeconds": 30, "maxSeconds": 60, "progressStallSeconds": 30},
        "credentialRefs": [],
        "networkRefs": [],
        "framework": fixture()["framework"],
    }


def evaluate(data=None):
    from moonmind.security.frameworks import FrameworkAssessmentInput
    from moonmind.security.ukgc_rts import prepare_audit

    data = data or fixture()
    return prepare_audit(
        FrameworkAssessmentInput.model_validate(data["framework"]),
        evidence=[
            dict(row, artifactRef=f"art_evidence_{i}")
            for i, row in enumerate(data["evidence"])
        ],
        audit=data["audit"],
    )


def test_framework_is_admitted_through_shared_assessment():
    request = AssessmentRequest.model_validate(request_payload())
    assert request.framework.pack_id == "ukgc-rts-security"
    assert not request.tools


def test_published_control_selection():
    from moonmind.security.frameworks import FrameworkPack, load_framework_pack

    pack = load_framework_pack("ukgc-rts-security")
    assert [r.id for r in pack.requirements] == fixture()["controlIds"]
    assert len(pack.requirements) == 44
    assert {r.category for r in pack.requirements} == {
        "organisational",
        "people",
        "physical",
        "technological",
    }
    assert pack.edition == "ISO/IEC 27001:2022"
    assert pack.source.url.endswith("rts-security-requirements")
    assert FrameworkPack.model_validate_json(pack.model_dump_json()) == pack
    data = fixture()
    data["framework"]["requirementChoices"]["8.28"] = {"systems": ["casino"]}
    with pytest.raises(ValueError, match="8.28"):
        evaluate(data)


@pytest.mark.parametrize(
    "licence",
    [
        "remote_casino",
        "remote_betting_host_virtual",
        "remote_betting_host_real",
        "remote_bingo",
        "remote_pool_betting",
        "remote_betting_intermediary",
        "remote_general_betting",
        "remote_game_host_bingo",
        "remote_game_host_casino",
    ],
)
def test_licence_and_critical_system_applicability(licence):
    data = fixture()
    data["framework"]["systemInputs"]["licenceClass"] = licence
    report = evaluate(data)
    assert report["applicability"]["annualAudit"] == "applicable"
    assert len(report["systemInputs"]["criticalSystems"]) == 5
    assert report["systemInputs"]["thirdParties"][0]["responsibilities"]


@pytest.mark.parametrize(
    ("licence", "sales", "expected"),
    [
        ("software_supplier", None, "not_required"),
        ("telephone_only", None, "not_required"),
        ("trading_room", None, "not_required"),
        ("unknown", None, "unsupported"),
        ("remote_lottery", None, "incomplete"),
        ("remote_lottery", 250000, "not_required"),
        ("remote_lottery", 250001, "applicable"),
        ("remote_external_lottery_manager", 250001, "applicable"),
    ],
)
def test_annual_audit_licence_limits(licence, sales, expected):
    data = fixture()
    data["framework"]["systemInputs"].update(
        licenceClass=licence, annualLotterySalesGbp=sales
    )
    assert evaluate(data)["applicability"]["annualAudit"] == expected


def test_missing_system_category_stays_incomplete():
    data = fixture()
    data["framework"]["systemInputs"]["criticalSystems"].pop()
    report = evaluate(data)
    assert report["applicability"]["scope"] == "incomplete"
    assert any("communications" in r["prompt"] for r in report["evidenceRequests"])


def test_reused_evidence_does_not_overclaim_coverage():
    data = fixture()
    certificate = copy.deepcopy(data["evidence"][1])
    certificate.update(kind="iso_certificate", requirementIds=["8.29"])
    data["evidence"] = [certificate, dict(certificate, kind="technical_assessment")]
    report = evaluate(data)
    assert report["readiness"] == "incomplete"
    assert all(r["status"] == "incomplete" for r in report["requirements"])
    assert len(report["evidenceRequests"]) >= 44
    assert report["evidence"][0]["provenance"]["sourceRef"] == "fixture:evidence"
    assert any("observation" in r["prompt"] for r in report["evidenceRequests"])
    data = fixture()
    for row in data["evidence"]:
        row.update(
            kind="external_audit", period={"start": "2025-01-01", "end": "2025-12-31"}
        )
    assert evaluate(data)["readiness"] == "incomplete"
    for row in data["evidence"]:
        row["reliance"] = {
            "reviewedBy": "Synthetic auditor",
            "reason": "Recent external work reviewed against current scope",
            "reviewRef": "prior-review",
            "validFor": data["framework"]["period"],
        }
    assert evaluate(data)["readiness"] == "prepared"
    data["evidence"][0]["provenance"]["standard"] = "unspecified"
    assert evaluate(data)["readiness"] == "incomplete"
    data["evidence"][0]["provenance"]["standard"] = "Equivalent external standard"
    data["evidence"][0]["reliance"][
        "equivalenceReason"
    ] = "Auditor reviewed the mapping to the selected 2022 RTS elements"
    assert evaluate(data)["readiness"] == "prepared"


def test_audit_records_schedule_and_reporting_conditions():
    report = evaluate()
    assert report["schedule"]["firstAuditTarget"] == "2025-07-01"
    assert report["schedule"]["annualAuditTarget"] == "2026-12-31"
    assert report["schedule"]["effectiveDueOn"] == "2026-12-31"
    assert report["reportingConditions"]["requestedReportWithinDays"] == 7
    assert report["reportingConditions"]["majorNonconformity"]
    assert report["audit"]["findings"][0]["managementResponse"] == "Add test sample"
    data = fixture()
    del data["audit"]["auditor"]["independenceRef"]
    data["evidence"] = [r for r in data["evidence"] if r["method"] != "observation"]
    report = evaluate(data)
    assert report["readiness"] == "incomplete"
    assert any(r["id"] == "auditor.independence" for r in report["evidenceRequests"])
    assert any("observation" in r["prompt"] for r in report["evidenceRequests"])


def test_audit_preparation_export():
    from moonmind.security.ukgc_rts import render_audit_preparation

    report = evaluate()
    assert report["readiness"] == "prepared"
    assert len(report["requirements"]) == 44
    assert {r["status"] for r in report["requirements"]} == {"evidence_recorded"}
    assert report["externalAudit"]["status"] == "Unverified"
    text = render_audit_preparation(report)
    for content in (
        "Synthetic Casino",
        "Synthetic auditor",
        "8.29",
        "Add test sample",
        "customer_information",
        "observation",
        "Unverified",
    ):
        assert content in text


def test_requirement_scope_and_period_do_not_widen_evidence():
    data = fixture()
    data["framework"]["requirementChoices"]["8.29"] = {
        "systems": ["casino"],
        "period": {"start": "2025-01-01", "end": "2025-12-31"},
    }
    report = evaluate(data)
    assert (
        next(r for r in report["requirements"] if r["id"] == "8.29")["status"]
        == "incomplete"
    )
    data["framework"]["requirementChoices"]["8.29"] = {"systems": ["outside"]}
    with pytest.raises(ValueError, match="scope"):
        evaluate(data)
    data["framework"]["period"] = {"start": "2027-01-01", "end": "2026-01-01"}
    with pytest.raises(ValidationError):
        AssessmentRequest.model_validate(
            dict(request_payload(), framework=data["framework"])
        )
