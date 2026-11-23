"""UKGC RTS security-audit preparation on the shared pack evaluator.

Requirement-level equivalence with the retired framework-pack/v1 evaluator is in
``test_legacy_readiness_equivalence.py``.
"""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from moonmind.security.assessments import AssessmentRequest
from moonmind.security.framework_registry import installed_pack, pack_behavior
from moonmind.security.frameworks import EvidencePeriod, normalize_installed_framework
from moonmind.security.pack_readiness import evaluate_readiness, parse_evidence

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/security/ukgc_rts.json"
PACK_ID = "ukgc-rts-security"


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
    data = data or fixture()
    [chosen] = normalize_installed_framework({"framework": data["framework"]})[
        "frameworkPacks"
    ]
    pack = installed_pack(PACK_ID)[1]
    behavior = pack_behavior(pack)
    return evaluate_readiness(
        pack,
        behavior,
        profile=chosen["profile"],
        period=EvidencePeriod.model_validate(chosen["period"]),
        choices=chosen.get("requirementChoices", {}),
        records=[
            parse_evidence(behavior, row, f"art_evidence_{i}")
            for i, row in enumerate(data["evidence"])
        ],
        audit=(
            behavior.records_model.model_validate(data["audit"])
            if data["audit"]
            else None
        ),
        admitted_assets=["casino", "rng"],
    )


def test_saved_framework_is_admitted_as_the_installed_pack():
    request = AssessmentRequest.model_validate(request_payload())
    assert request.framework is None
    [selection] = request.selected_packs
    pack = installed_pack(PACK_ID)[1]
    assert (selection.pack_id, selection.edition) == (PACK_ID, pack.edition)
    assert selection.profile["operatorName"] == "Synthetic Casino"
    assert not request.tools


def test_published_control_selection():
    pack = installed_pack(PACK_ID)[1]
    assert [r.id for r in pack.requirements] == fixture()["controlIds"]
    assert len(pack.requirements) == 44
    assert {r.category for r in pack.requirements} == {
        "organisational",
        "people",
        "physical",
        "technological",
    }
    assert pack.edition == "ISO-IEC-27001-2022"
    assert pack.source.url.endswith("rts-security-requirements")
    assert all(
        r.required_evidence_methods == ["enquiry", "evidence", "observation"]
        for r in pack.requirements
    )
    data = fixture()
    data["framework"]["requirementChoices"]["8.28"] = {"systems": ["casino"]}
    with pytest.raises(ValueError, match="8.28"):
        evaluate(data)


def test_unsupported_saved_licence_class_stays_an_explicit_gap():
    data = fixture()
    data["framework"]["systemInputs"]["licenceClass"] = "retired_class"
    result = evaluate(data)
    assert result.context["auditApplicability"]["annualAudit"] == "unsupported"
    assert any(r["id"] == "licence" for r in result.context["evidenceRequests"])


def test_audit_records_schedule_and_reporting_conditions():
    context = evaluate().context
    assert context["readiness"] == "prepared"
    assert context["schedule"]["firstAuditTarget"] == "2025-07-01"
    assert context["schedule"]["annualAuditTarget"] == "2026-12-31"
    assert context["reportingConditions"]["submissionPerformed"] is False
    assert context["audit"]["findings"][0]["managementResponse"] == "Add test sample"
    assert context["externalAudit"]["status"] == "Unverified"
    data = fixture()
    del data["audit"]["auditor"]["independenceRef"]
    data["evidence"] = [r for r in data["evidence"] if r["method"] != "observation"]
    context = evaluate(data).context
    assert context["readiness"] == "incomplete"
    ids = [r["id"] for r in context["evidenceRequests"]]
    assert "auditor.independence" in ids
    assert any(
        "observation" in r["prompt"] for r in context["evidenceRequests"]
    )


def test_independence_statement_is_a_mandatory_audit_reference():
    from moonmind.security.pack_readiness import input_refs

    pack = installed_pack(PACK_ID)[1]
    behavior = pack_behavior(pack)
    audit = behavior.records_model.model_validate(fixture()["audit"])
    assert "independence" in input_refs(
        pack, behavior, profile={}, records=[], audit=audit
    )


def test_report_preserves_scope_period_exclusion_and_external_boundaries():
    from moonmind.security.control_assessments import _render_readiness
    from moonmind.security.framework_packs import readiness_metadata

    data = fixture()
    data["framework"]["systemInputs"]["criticalSystems"][1]["systems"] = ["rng"]
    data["framework"]["requirementChoices"] = {
        "5.23": {
            "systems": ["casino"],
            "period": {"start": "2025-04-01", "end": "2025-10-31"},
            "notApplicableReason": "No cloud services within the admitted scope",
        },
    }
    result = evaluate(data)
    row = result.rows["5.23"]
    assert row["status"] == "exclusion_for_review"
    assert row["systems"] == ["casino"]
    assert row["exclusionReason"] == "No cloud services within the admitted scope"
    pack = installed_pack(PACK_ID)[1]
    text = "\n".join(
        _render_readiness(
            json.loads(json.dumps(readiness_metadata(pack, result), default=str)),
            str,
        )
    )
    for content in (
        "Synthetic Casino",
        "Synthetic auditor",
        "Add test sample",
        "customer_information",
        "Independent audit and private inspection: Unverified",
        "No regulator submission",
    ):
        assert content in text


def test_requirement_scope_and_period_do_not_widen_evidence():
    data = fixture()
    data["framework"]["requirementChoices"]["8.29"] = {
        "systems": ["casino"],
        "period": {"start": "2025-01-01", "end": "2025-12-31"},
    }
    assert evaluate(data).rows["8.29"]["status"] == "incomplete"
    data["framework"]["requirementChoices"]["8.29"] = {"systems": ["outside"]}
    with pytest.raises(ValueError, match="scope"):
        evaluate(data)
    data["framework"]["period"] = {"start": "2027-01-01", "end": "2026-01-01"}
    with pytest.raises(ValidationError):
        AssessmentRequest.model_validate(
            dict(request_payload(), framework=data["framework"])
        )


def test_reused_certificates_do_not_overclaim_coverage():
    data = fixture()
    certificate = copy.deepcopy(data["evidence"][1])
    certificate.update(kind="iso_certificate", requirementIds=["8.29"])
    data["evidence"] = [certificate, dict(certificate, kind="technical_assessment")]
    result = evaluate(data)
    assert {row["status"] for row in result.rows.values()} == {"incomplete"}
    assert len(result.context["evidenceRequests"]) >= 44
