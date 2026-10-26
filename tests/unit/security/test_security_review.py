"""Security procedure artifact boundaries for MoonLadderStudios/MoonMind#5042."""

import copy

import pytest
from pydantic import ValidationError


def review_payload():
    return {
        "schemaVersion": "security-review/v1",
        "scope": {"assets": ["app/routes.py"]},
        "candidateRef": "git:fixture-vulnerable",
        "threatModel": {
            "entryPoints": ["GET /invoices/{id}"],
            "trustBoundaries": ["authenticated caller to invoice owner"],
            "invariants": ["only the invoice owner can read its amount"],
        },
        "interpretations": [
            {
                "id": "invoice-owner",
                "subject": {
                    "asset": "app/routes.py",
                    "locator": "get_invoice:3",
                    "identity": "authenticated caller other than invoice owner",
                    "resource": "invoice:2",
                },
                "hypothesis": "A caller can retrieve another owner's invoice.",
                "disposition": "confirmed",
                "evidenceStatus": "supported",
                "evidenceRefs": ["art_route", "art_reproduction"],
                "limitations": ["Controlled fixture; no live target exercised."],
            }
        ],
        "limitations": ["No scanner findings supplied; direct logic review only."],
        "retests": [],
    }


def test_direct_review_records_the_examined_identity_and_resource_without_a_scan():
    from moonmind.security.assessments import SecurityReview

    review = SecurityReview.model_validate(review_payload())
    assert review.interpretations[0].subject.identity.startswith("authenticated")
    assert review.interpretations[0].subject.resource == "invoice:2"
    assert review.threat_model.invariants


@pytest.mark.parametrize("evidence_status", ["contradicted", "unsupported"])
def test_unsupported_or_contradicted_claims_cannot_be_confirmed(evidence_status):
    from moonmind.security.assessments import SecurityReview

    payload = review_payload()
    payload["interpretations"][0]["evidenceStatus"] = evidence_status
    with pytest.raises(ValidationError, match="unresolved"):
        SecurityReview.model_validate(payload)
    payload["interpretations"][0]["disposition"] = "unresolved"
    assert (
        SecurityReview.model_validate(payload).interpretations[0].disposition
        == "unresolved"
    )


@pytest.mark.parametrize(
    "change", ["outside_scope", "no_evidence", "confinement", "tests_passed"]
)
def test_review_cannot_expand_scope_or_certify_controls(change):
    from moonmind.security.assessments import SecurityReview

    payload = review_payload()
    if change == "outside_scope":
        payload["interpretations"][0]["subject"]["asset"] = "outside.py"
    elif change == "no_evidence":
        payload["interpretations"][0]["evidenceRefs"] = []
    else:
        payload[change] = True
    with pytest.raises(ValidationError):
        SecurityReview.model_validate(payload)


def test_direct_review_is_an_explicit_native_assessment_input():
    from moonmind.security.assessments import AssessmentRequest

    request = {
        "scope": {"assets": ["app/routes.py"]},
        "tools": [],
        "reviewRef": "art_review",
        "credentialRefs": [],
        "networkRefs": [],
        "budget": {"baseSeconds": 30, "maxSeconds": 60, "progressStallSeconds": 30},
    }
    assert AssessmentRequest.model_validate(request).review_ref == "art_review"
    missing = copy.deepcopy(request)
    missing.pop("reviewRef")
    with pytest.raises(ValidationError):
        AssessmentRequest.model_validate(missing)


@pytest.mark.parametrize("missing", ["original_scope", "original_evidence"])
def test_complete_retest_retains_original_hypothesis_scope_and_evidence(missing):
    from moonmind.security.assessments import SecurityReview

    payload = review_payload()
    payload["scope"]["assets"].append("app/helper.py")
    payload["retests"] = [
        {
            "hypothesisId": "invoice-owner",
            "originalEvidenceRefs": ["art_route", "art_reproduction"],
            "currentEvidenceRefs": ["art_current"],
            "changedAssets": ["app/helper.py"],
            "coveredAssets": ["app/routes.py", "app/helper.py"],
            "coverage": "complete",
            "limitations": ["Controlled fixture only."],
        }
    ]
    if missing == "original_scope":
        payload["retests"][0]["coveredAssets"] = ["app/helper.py"]
    else:
        payload["retests"][0]["originalEvidenceRefs"] = ["art_unrelated"]
    with pytest.raises(ValidationError, match="original"):
        SecurityReview.model_validate(payload)
