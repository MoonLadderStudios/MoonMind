"""Synthetic SOC 2 readiness, not a CPA examination, on the shared pack evaluator.

Requirement-level equivalence with the retired framework-pack/v1 evaluator is in
``test_legacy_readiness_equivalence.py``.
"""

from datetime import UTC, datetime

import pytest

from moonmind.security.framework_registry import installed_pack, pack_behavior
from moonmind.security.frameworks import (
    EvidencePeriod,
    FrameworkAssessmentInput,
    normalize_installed_framework,
)
from moonmind.security.pack_readiness import evaluate_readiness

NOW = datetime(2026, 11, 9, tzinfo=UTC)
PACK_ID = "soc2-readiness"


def selection(**system):
    """A saved ``framework.packId`` request, as presets, reruns and schedules keep it."""
    return FrameworkAssessmentInput.model_validate(
        {
            "packId": PACK_ID,
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


def evaluate(request=None, availability=None):
    framework = (request or selection()).model_dump(mode="json", by_alias=True)
    [chosen] = normalize_installed_framework({"framework": framework})["frameworkPacks"]
    pack = installed_pack(PACK_ID)[1]
    return evaluate_readiness(
        pack,
        pack_behavior(pack),
        profile=chosen["profile"],
        period=EvidencePeriod.model_validate(chosen["period"]),
        choices=chosen.get("requirementChoices", {}),
        records=[],
        audit=None,
        admitted_assets=["api"],
        availability=availability or {},
        now=NOW,
    )


def criterion(result, identifier="CC6.1"):
    """One criterion's readiness row, with the behavior's recorded basis."""
    row = result.rows[identifier]
    return {**row, **row["details"], "id": identifier}


def test_published_inventory_and_scope_are_explicit():
    pack = installed_pack(PACK_ID)[1]
    assert pack.edition == "TSC-2017-POF-2022_DC-2018-IG-2022"
    assert pack.publication_status == "published"
    assert len(pack.requirements) == 70
    result = evaluate()
    statuses = [row["status"] for row in result.rows.values()]
    assert statuses.count("incomplete") == 42
    assert statuses.count("not_applicable") == 28
    context = result.context
    assert context["readiness"] == "incomplete"
    assert context["externalClaims"]["examination"]["status"] == "Unverified"
    assert context["sourceContext"]["publicationStatus"] == "published"
    assert "no CPA examination" in context["headline"][0]


def test_type_one_requires_single_date_and_type_two_requires_period():
    with pytest.raises(ValueError, match="Type 1"):
        evaluate(selection(assessmentType="type1"))
    request = selection(assessmentType="type1")
    request.period.start = request.period.end
    result = evaluate(request)
    assert result.rows["DC9"]["status"] == "not_applicable"
    assert all(
        r["purpose"] == "design"
        for r in result.context["evidenceRequests"]
        if "purpose" in r
    )
    request = selection()
    request.period.end = request.period.start
    with pytest.raises(ValueError, match="Type 2"):
        evaluate(request)


@pytest.mark.parametrize(
    "system",
    [{"assessmentType": None}, {"categories": []}, {"edition": "2027"}],
)
def test_assessment_type_categories_and_edition_are_explicit(system):
    with pytest.raises(ValueError):
        evaluate(selection(**system))


def test_period_aware_sampling_needs_an_exact_attributed_review():
    availability = {"art_design": None, "art_operating": None}
    request = complete_requirement()
    assert criterion(evaluate(request, availability))["status"] == "evidence_recorded"
    request.system_inputs["requirementSamples"]["CC6.1"]["selectedRecordIds"] = [
        "never-supplied-record"
    ]
    row = criterion(evaluate(request, availability))
    assert row["status"] == "incomplete"
    assert any("exact sampling context" in gap for gap in row["limitations"])


def test_saved_samples_and_documentary_evidence_reach_the_unified_inputs():
    request = complete_requirement()
    framework = request.model_dump(mode="json", by_alias=True)
    [chosen] = normalize_installed_framework({"framework": framework})["frameworkPacks"]
    assert chosen["profile"]["requirementSamples"]["CC6.1"]["populationSize"] == 2
    evidence = chosen["requirementChoices"]["CC6.1"]["organisationalEvidence"]
    assert [row["id"] for row in evidence] == ["design", "occurrence"]
