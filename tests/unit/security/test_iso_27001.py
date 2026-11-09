from datetime import UTC, datetime

import pytest

from moonmind.security.frameworks import FrameworkAssessmentInput, load_framework_pack

NOW = datetime(2026, 11, 9, tzinfo=UTC)


def selection(**changes):
    system = {
        "systems": ["isms"],
        "scopeStatement": "Synthetic SaaS",
        "context": "Synthetic context",
        "interestedParties": ["Customers: confidentiality"],
        "climateRelevance": "No material issue; reviewed",
        "risks": [
            {
                "id": "R1",
                "description": "Unauthorized service access",
                "version": "1",
                "owner": "Security",
                "treatment": "mitigate",
                "controlIds": ["A.8.8", "EXTRA.1"],
            }
        ],
        "controls": {
            "A.8.8": {
                "decision": "include",
                "rationale": "Reduce R1",
                "riskIds": ["R1"],
                "implementationStatus": "implemented",
            },
            "EXTRA.1": {
                "title": "Tenant-independent restore drill",
                "decision": "include",
                "rationale": "Required by R1",
                "riskIds": ["R1"],
            },
        },
    }
    system.update(changes)
    return FrameworkAssessmentInput(
        pack_id="iso-27001-readiness",
        system_inputs=system,
        period={"start": "2026-11-01", "end": "2026-11-02"},
    )


def evaluate(request=None, **kwargs):
    from moonmind.security.iso_27001 import prepare_assessment

    return prepare_assessment(request or selection(), evidence=[], now=NOW, **kwargs)


def test_pack_covers_management_system_and_all_reference_identifiers():
    pack = load_framework_pack("iso-27001-readiness")
    assert pack.edition == "2022+Amd1:2024"
    assert len([r for r in pack.requirements if r.category == "annex_a"]) == 93
    assert {"4.1", "4.2", "9.2", "9.3", "10.1", "10.2"} <= {
        r.id for r in pack.requirements
    }


def test_risk_controls_without_scanner_mapping_remain_in_draft_soa():
    report = evaluate()
    rows = {r["controlId"]: r for r in report["statementOfApplicability"]["decisions"]}
    assert rows["EXTRA.1"]["riskIds"] == ["R1"]
    assert rows["A.8.8"]["implementationStatus"] == "implemented"
    assert rows["A.8.8"]["verification"] == "Unverified"
    assert report["readiness"] == "incomplete"
    assert report["certification"] == "Unverified"
    assert {"9.2", "9.3"} <= set(report["unresolvedRequirements"])


def test_unjustified_exclusions_and_incomplete_treatment_stay_gaps():
    request = selection()
    request.system_inputs["controls"]["A.8.8"] = {"decision": "exclude"}
    report = evaluate(request)
    assert any(r["id"] == "soa.A.8.8" for r in report["evidenceRequests"])
    assert report["riskTreatmentActions"]


def test_scope_risk_and_edition_changes_preserve_previous_decisions():
    import copy

    old = evaluate()
    original = copy.deepcopy(old)
    updated = selection(scopeStatement="New scope")
    updated.system_inputs["risks"][0]["version"] = "2"
    old["pack"]["edition"] = "2022"
    report = evaluate(updated, previous=old)
    assert report["reassessment"]["affectedControlIds"]
    assert (
        report["statementOfApplicability"]["version"]
        != original["statementOfApplicability"]["version"]
    )
    assert old["statementOfApplicability"] == original["statementOfApplicability"]


def test_unknown_requirement_and_control_risk_links_rejected():
    with pytest.raises(ValueError, match="risk"):
        evaluate(
            selection(
                controls={"A.8.8": {"decision": "include", "riskIds": ["unknown"]}}
            )
        )


def reviewed_evidence(purpose="occurrence", kind="operational_record"):
    return {
        "id": "audit-record",
        "category": "internal_audit",
        "purpose": purpose,
        "description": "Synthetic completed audit",
        "owner": "Assurance owner",
        "revisions": [
            {
                "artifactRef": "art_audit",
                "kind": kind,
                "source": "Synthetic audit record",
                "collectedAt": "2026-11-03T00:00:00Z",
                "expiresAt": "2027-01-01T00:00:00Z",
                "period": {"start": "2026-11-01", "end": "2026-11-02"},
                "population": ["isms"],
                "sensitivity": "internal",
                "review": {
                    "outcome": "accepted",
                    "purpose": purpose,
                    "reviewer": "Reviewer",
                    "reason": "Examined recorded scope, completion and findings",
                    "reviewedAt": "2026-11-04T00:00:00Z",
                    "period": {"start": "2026-11-01", "end": "2026-11-02"},
                    "population": ["isms"],
                },
            }
        ],
    }


@pytest.mark.parametrize(
    "change",
    [
        "fresh",
        "expired",
        "assertion",
        "draft",
        "denied",
        "wrong-purpose",
        "wrong-population",
    ],
)
def test_documentary_review_is_purpose_scoped_fresh_and_not_an_assertion(change):
    item = reviewed_evidence()
    current = item["revisions"][0]
    availability = {"art_audit": None}
    if change == "expired":
        current["expiresAt"] = "2026-11-05T00:00:00Z"
    elif change == "assertion":
        current["kind"] = "management_assertion"
    elif change == "draft":
        current["kind"] = "generated_draft"
    elif change == "denied":
        availability["art_audit"] = "denied"
    elif change == "wrong-purpose":
        current["review"]["purpose"] = "design"
    elif change == "wrong-population":
        current["population"] = ["other"]
    report = evaluate(
        selection(requirementEvidence={"9.2": [item]}),
        evidence_availability=availability,
    )
    row = next(r for r in report["requirements"] if r["id"] == "9.2")
    assert row["status"] == ("evidence_reviewed" if change == "fresh" else "incomplete")
    assert report["certification"] == "Unverified"


def test_technical_evidence_reuse_keeps_independent_requirement_conclusions():
    from moonmind.security.iso_27001 import prepare_assessment

    request = selection(requirementEvidence={"9.2": [reviewed_evidence()]})
    record = {
        "kind": "technical_assessment",
        "requirementIds": ["9.2", "9.3", "A.8.8"],
        "systems": ["isms"],
        "period": {"start": "2026-11-01", "end": "2026-11-02"},
        "method": "evidence",
        "description": "Synthetic completed vulnerability retest",
        "provenance": {
            "sourceRef": "art_scan",
            "issuer": "Fixture",
            "version": "1",
            "recordedOn": "2026-11-02",
        },
        "artifactRef": "art_mapping",
    }
    report = prepare_assessment(
        request, evidence=[record], now=NOW, evidence_availability={"art_audit": None}
    )
    rows = {r["id"]: r for r in report["requirements"]}
    assert rows["9.2"]["status"] == "evidence_reviewed"
    assert rows["9.3"]["status"] == "incomplete"
    assert (
        rows["9.2"]["technicalContextRefs"]
        == rows["9.3"]["technicalContextRefs"]
        == ["art_mapping"]
    )
    assert rows["A.8.8"]["status"] == "incomplete"


@pytest.mark.parametrize(
    "choice",
    [
        {"systems": ["isms"]},
        {"period": {"start": "2026-11-02", "end": "2026-11-02"}},
        {
            "responsibleEntity": "partner",
            "responsibilityReason": "Independent audit provider",
        },
    ],
)
def test_management_choices_trigger_scoped_review_without_annex_changes(choice):
    import copy

    from moonmind.security.frameworks import RequirementChoice

    request = selection(systems=["isms", "second"])
    previous = evaluate(request)
    unchanged = copy.deepcopy(previous)
    request.requirement_choices["9.2"] = RequirementChoice.model_validate(choice)
    report = evaluate(request, previous=previous)
    assert report["reassessment"]["affectedRequirementIds"] == ["9.2"]
    assert report["reassessment"]["affectedControlIds"] == []
    prompt = next(
        r for r in report["evidenceRequests"] if r["id"] == "reassessment.applicability"
    )
    assert prompt["affectedRequirementIds"] == ["9.2"]
    assert previous == unchanged
    assert (
        report["statementOfApplicability"]["version"]
        == previous["statementOfApplicability"]["version"]
    )


def test_reordered_management_scope_is_not_a_changed_requirement():
    from moonmind.security.frameworks import RequirementChoice

    request = selection(systems=["isms", "second"])
    previous = evaluate(request)
    request.requirement_choices["9.2"] = RequirementChoice(systems=["second", "isms"])
    assert (
        evaluate(request, previous=previous)["reassessment"]["affectedRequirementIds"]
        == []
    )
