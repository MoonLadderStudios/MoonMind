"""Synthetic SOC 2 readiness, not a CPA examination."""

from datetime import UTC, datetime

import pytest

from moonmind.security.frameworks import FrameworkAssessmentInput, load_framework_pack

NOW = datetime(2026, 11, 9, tzinfo=UTC)


def selection(**system):
    return FrameworkAssessmentInput.model_validate(
        {
            "packId": "soc2-readiness",
            "systemInputs": {
                "entityName": "Synthetic service",
                "systems": ["api"],
                "serviceBoundary": "API and supporting operations",
                "serviceCommitments": ["Protected requests"],
                "categories": ["security"],
                "assessmentType": "type2",
                **system,
            },
            "period": {"start": "2026-01-01", "end": "2026-10-31"},
        }
    )


def evaluate(request=None, **kwargs):
    from moonmind.security.soc2_readiness import prepare_assessment

    return prepare_assessment(request or selection(), evidence=[], now=NOW, **kwargs)


def test_published_inventory_and_scope_are_explicit():
    pack = load_framework_pack("soc2-readiness")
    assert pack.edition == "TSC-2017-POF-2022_DC-2018-IG-2022"
    assert len(pack.requirements) == 70
    report = evaluate()
    assert len(report["requirements"]) == 70
    assert len([r for r in report["requirements"] if r["status"] == "unassessed"]) == 42
    assert report["readiness"] == "incomplete"
    assert report["externalVerification"]["examination"]["status"] == "Unverified"
    assert report["sourceContext"]["publicationStatus"] == "published"


def test_type_one_requires_single_date_and_type_two_requires_period():
    with pytest.raises(ValueError, match="Type 1"):
        evaluate(selection(assessmentType="type1"))
    request = selection(assessmentType="type1")
    request.period.start = request.period.end
    report = evaluate(request)
    row = next(r for r in report["requirements"] if r["id"] == "DC9")
    assert row["status"] == "not_applicable"
    assert all(
        r["purpose"] == "design" for r in report["evidenceRequests"] if "purpose" in r
    )


def test_exclusions_and_partner_responsibility_remain_visible():
    request = selection()
    from moonmind.security.frameworks import RequirementChoice

    request.requirement_choices["CC6.1"] = RequirementChoice(
        responsible_entity="partner", responsibility_reason="Hosting provider"
    )
    report = evaluate(request)
    row = next(r for r in report["requirements"] if r["id"] == "CC6.1")
    assert row["status"] == "unassessed"
    assert row["responsibleEntity"] == "partner"
    assert any(r.get("requirementId") == "CC6.1" for r in report["evidenceRequests"])


def documentary_request(purpose="design", kind="document", ref="art_design"):
    return {
        "id": purpose,
        "category": "access_review",
        "purpose": purpose,
        "description": "Synthetic reviewed access records",
        "owner": "Synthetic management",
        "revisions": [
            {
                "artifactRef": ref,
                "kind": kind,
                "source": "Synthetic fixture",
                "collectedAt": "2026-11-01T00:00:00Z",
                "expiresAt": "2027-01-01T00:00:00Z",
                "period": {"start": "2026-01-01", "end": "2026-10-31"},
                "population": ["api"],
                "sensitivity": "internal",
                "review": {
                    "outcome": "accepted",
                    "purpose": purpose,
                    "reviewer": "Synthetic reviewer",
                    "reason": "Reviewed cited fixture records",
                    "reviewedAt": "2026-11-02T00:00:00Z",
                    "period": {"start": "2026-01-01", "end": "2026-10-31"},
                    "population": ["api"],
                },
            }
        ],
    }


def complete_requirement():
    request = selection(
        requirementEvidence={
            "CC6.1": [
                documentary_request(),
                documentary_request(
                    "occurrence", "operational_record", "art_operating"
                ),
            ]
        },
        requirementSamples={
            "CC6.1": {
                "populationRequestId": "occurrence",
                "populationSize": 2,
                "samplingRationale": "Both synthetic access events reviewed",
                "selectedRecordIds": ["join-1", "leave-2"],
            }
        },
    )

    review_sampling(request)
    return request


def review_sampling(request, identifier="CC6.1"):
    sample = request.system_inputs["requirementSamples"][identifier]
    population = next(
        row
        for row in request.system_inputs["requirementEvidence"][identifier]
        if row["id"] == sample["populationRequestId"]
    )
    current = population["revisions"][-1]
    current["review"]["samplingContext"] = {
        "requirementId": identifier,
        "populationArtifactRef": current["artifactRef"],
        "populationSize": sample["populationSize"],
        "samplingRationale": sample["samplingRationale"],
        "selectedRecordIds": list(sample["selectedRecordIds"]),
        "exceptions": list(sample.get("exceptions", [])),
    }


def criterion(report, identifier="CC6.1"):
    return next(r for r in report["requirements"] if r["id"] == identifier)


def test_period_change_and_current_snapshot_cannot_establish_historical_operation():
    request = complete_requirement()
    availability = {"art_design": None, "art_operating": None}
    assert (
        criterion(evaluate(request, evidence_availability=availability))["status"]
        == "evidence_recorded"
    )
    request.period.start = request.period.start.replace(year=2025)
    assert (
        criterion(evaluate(request, evidence_availability=availability))["status"]
        == "unassessed"
    )
    request = complete_requirement()
    request.system_inputs["requirementEvidence"]["CC6.1"][1]["revisions"][0][
        "period"
    ] = {"start": "2026-10-31", "end": "2026-10-31"}
    assert (
        criterion(evaluate(request, evidence_availability=availability))["status"]
        == "unassessed"
    )


@pytest.mark.parametrize(
    "change", ["population", "assertion", "exception", "missing", "expired"]
)
def test_organisational_gaps_cannot_be_hidden(change):
    request = complete_requirement()
    availability = {"art_design": None, "art_operating": None}
    op = request.system_inputs["requirementEvidence"]["CC6.1"][1]["revisions"][0]
    if change == "population":
        request.system_inputs["requirementSamples"] = {}
    elif change == "assertion":
        op["kind"] = "management_assertion"
    elif change == "exception":
        request.system_inputs["requirementSamples"]["CC6.1"].update(
            exceptions=["Late offboarding"], managementResponse="Will repair"
        )
    elif change == "missing":
        availability["art_operating"] = "unavailable"
    elif change == "expired":
        op["expiresAt"] = "2026-11-03T00:00:00Z"
    row = criterion(evaluate(request, evidence_availability=availability))
    assert row["status"] == "unassessed"
    assert row["limitations"]


def test_draft_edition_is_not_selectable_and_common_criteria_always_apply():
    with pytest.raises(ValueError):
        evaluate(selection(edition="2027"))
    report = evaluate(selection(categories=["availability"]))
    assert criterion(report)["status"] == "unassessed"
    assert criterion(report, "A1.1")["status"] == "unassessed"
    assert criterion(report, "P1.1")["status"] == "not_applicable"


def test_missing_system_boundary_produces_actionable_requests():
    report = evaluate(selection(systems=[]))
    assert any(r["id"] == "scope.systems" for r in report["evidenceRequests"])
    assert criterion(report)["status"] == "unassessed"


def test_type_two_incidents_and_changes_need_real_period_records():
    request = selection(
        requirementEvidence={
            "DC4": [documentary_request()],
            "DC9": [documentary_request()],
        }
    )
    report = evaluate(request, evidence_availability={"art_design": None})
    assert criterion(report, "DC4")["status"] == "unassessed"
    assert criterion(report, "DC9")["status"] == "unassessed"
    assert any(
        "Operating evidence" in gap for gap in criterion(report, "DC9")["limitations"]
    )


def test_readable_report_retains_service_and_review_context():
    from moonmind.security.soc2_readiness import render_assessment

    request = complete_requirement()
    report = evaluate(
        request, evidence_availability={"art_design": None, "art_operating": None}
    )
    rendered = render_assessment(report)
    assert "API and supporting operations" in rendered
    assert "Protected requests" in rendered
    assert "Synthetic reviewer" in rendered
    assert "Synthetic fixture" in rendered
    assert "published" in rendered


@pytest.mark.parametrize("change", ["records", "zero", "rationale"])
def test_changed_sampling_context_requires_a_new_attributed_review(change):
    request = complete_requirement()
    sample = request.system_inputs["requirementSamples"]["CC6.1"]
    if change == "records":
        sample["selectedRecordIds"] = ["never-supplied-record"]
    elif change == "zero":
        sample.update(populationSize=0, selectedRecordIds=[])
    else:
        sample["samplingRationale"] = "Unreviewed selection change"
    row = criterion(
        evaluate(
            request, evidence_availability={"art_design": None, "art_operating": None}
        )
    )
    assert row["status"] == "unassessed"
    assert any("sampling" in gap for gap in row["limitations"])


def test_zero_event_population_requires_exact_review_and_current_revision():
    request = complete_requirement()
    sample = request.system_inputs["requirementSamples"]["CC6.1"]
    sample.update(populationSize=0, selectedRecordIds=[])
    availability = {"art_design": None, "art_operating": None, "art_replacement": None}
    assert (
        criterion(evaluate(request, evidence_availability=availability))["status"]
        == "unassessed"
    )
    review_sampling(request)
    assert (
        criterion(evaluate(request, evidence_availability=availability))["status"]
        == "evidence_recorded"
    )
    population = request.system_inputs["requirementEvidence"]["CC6.1"][1]
    import copy

    replacement = copy.deepcopy(population["revisions"][-1])
    replacement["artifactRef"] = "art_replacement"
    population["revisions"].append(replacement)
    assert (
        criterion(evaluate(request, evidence_availability=availability))["status"]
        == "unassessed"
    )
    review_sampling(request)
    assert (
        criterion(evaluate(request, evidence_availability=availability))["status"]
        == "evidence_recorded"
    )


def test_sampling_review_cannot_be_missing_or_clear_reviewed_exceptions():
    request = complete_requirement()
    review = request.system_inputs["requirementEvidence"]["CC6.1"][1]["revisions"][-1][
        "review"
    ]
    review.pop("samplingContext")
    availability = {"art_design": None, "art_operating": None}
    assert (
        criterion(evaluate(request, evidence_availability=availability))["status"]
        == "unassessed"
    )
    request.system_inputs["requirementSamples"]["CC6.1"]["exceptions"] = [
        "Late offboarding"
    ]
    review_sampling(request)
    request.system_inputs["requirementSamples"]["CC6.1"]["exceptions"] = []
    row = criterion(evaluate(request, evidence_availability=availability))
    assert row["status"] == "unassessed"
    assert any("exact sampling context" in gap for gap in row["limitations"])


def test_type_one_design_does_not_require_operating_sampling_review():
    request = selection(
        assessmentType="type1", requirementEvidence={"CC6.1": [documentary_request()]}
    )
    request.period.start = request.period.end
    assert (
        criterion(evaluate(request, evidence_availability={"art_design": None}))[
            "status"
        ]
        == "evidence_recorded"
    )
