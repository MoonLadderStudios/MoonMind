"""Public/synthetic readiness behaviors for MoonLadderStudios/MoonMind#5195."""

import copy
import json
from pathlib import Path

import pytest

from moonmind.security.frameworks import FrameworkAssessmentInput, load_framework_pack

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/security/gli_readiness.json"


def fixture():
    return json.loads(FIXTURE.read_text())


def request_payload():
    return {
        "scope": {"assets": ["casino"]},
        "tools": [],
        "budget": {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
        "credentialRefs": [],
        "networkRefs": [],
        "framework": fixture()["framework"],
    }


def evaluate(data=None, evidence=None):
    from moonmind.security.gli_readiness import prepare_assessment

    data = data or fixture()
    return prepare_assessment(
        FrameworkAssessmentInput.model_validate(data["framework"]),
        evidence=evidence or [],
    )


def test_versioned_pack_uses_shared_contract_and_official_sources():
    from moonmind.security.frameworks import FrameworkPack

    pack = load_framework_pack("gli-19-readiness")
    assert pack.edition == "4.0"
    assert pack.published_on.isoformat() == "2026-11-03"
    assert pack.source.url == fixture()["standardSource"]
    assert FrameworkPack.model_validate_json(pack.model_dump_json()) == pack
    assert {r.module for r in pack.requirements} == {
        "GLI-19",
        "GLI-GSF-1",
        "GLI-GSF-2",
        "GLI-GSF-3",
        "GLI-GSF-5",
    }
    assert all(
        r.summary and r.sources and r.evidence_types and r.responsibilities
        for r in pack.requirements
    )


@pytest.mark.parametrize(
    "entity,product,modules",
    [
        (
            "operator",
            "interactive_gaming",
            {"GLI-19", "GLI-GSF-1", "GLI-GSF-2", "GLI-GSF-5"},
        ),
        (
            "rgs",
            "interactive_gaming",
            {"GLI-19", "GLI-GSF-1", "GLI-GSF-2", "GLI-GSF-5"},
        ),
        (
            "ancillary_provider",
            "ancillary_service",
            {"GLI-GSF-1", "GLI-GSF-2", "GLI-GSF-3"},
        ),
    ],
)
def test_entity_modules_and_visible_exclusions(entity, product, modules):
    data = fixture()
    data["framework"]["systemInputs"].update(
        entityType=entity, productType=product, directlyAffectsGaming=False
    )
    report = evaluate(data)
    assert {
        r["module"] for r in report["modules"] if r["status"] == "applicable"
    } == modules
    assert all(r["reason"] for r in report["modules"])
    assert report["readiness"] == "incomplete"


def test_unknown_scope_partner_ownership_and_explicit_exclusion_survive():
    data = fixture()
    data["framework"]["requirementChoices"] = {
        "GLI-19:3.2": {
            "responsibleEntity": "partner",
            "responsibilityReason": "KYC owned by contracted operator",
        },
        "GLI-19:6.10": {"notApplicableReason": "No progressive games in this product"},
        "GLI-GSF-2:B": {
            "systems": ["casino"],
            "period": {"start": "2026-11-04", "end": "2026-11-06"},
        },
    }
    report = evaluate(data)
    rows = {r["id"]: r for r in report["requirements"]}
    assert rows["GLI-19:3.2"]["status"] == "partner_owned"
    assert (
        rows["GLI-19:3.2"]["responsibilityReason"] == "KYC owned by contracted operator"
    )
    assert rows["GLI-19:6.10"]["status"] == "excluded"
    assert rows["GLI-GSF-2:B"]["period"]["end"] == "2026-11-06"
    del data["framework"]["systemInputs"]["entityType"]
    assert evaluate(data)["applicability"]["status"] == "incomplete"
    assert any(
        r["id"] == "scope.entityType" for r in evaluate(data)["evidenceRequests"]
    )


@pytest.mark.parametrize(
    "product,profile", [("gaming_device", "GLI-11"), ("event_wagering", "GLI-33")]
)
def test_other_products_require_their_own_profile(product, profile):
    data = fixture()
    data["framework"]["systemInputs"]["productType"] = product
    report = evaluate(data)
    assert report["readiness"] == "incomplete"
    assert profile in report["applicability"]["reason"]


def test_only_matching_scoped_evidence_can_support_readiness():
    evidence = fixture()["technicalEvidence"]
    report = evaluate(evidence=[evidence])
    rows = {r["id"]: r for r in report["requirements"]}
    assert rows["GLI-GSF-2:B"]["status"] == "evidence_recorded"
    assert rows["GLI-19:5.2"]["status"] == "incomplete"
    assert rows["GLI-19:1.5"]["status"] == "incomplete"
    assert report["certification"] == "not_performed"
    assert report["jurisdictionApproval"] == "not_performed"
    for change in (
        {"outcome": "failed"},
        {"edition": "3.0"},
        {"systems": ["another"]},
        {"period": {"start": "2020-01-01", "end": "2020-12-31"}},
        {"evidenceType": "organizational"},
        {"method": "enquiry"},
        {"provenance": dict(evidence["provenance"], standard="GLI-GSF-2 v0.0")},
    ):
        changed = dict(copy.deepcopy(evidence), **change)
        assert (
            next(
                r
                for r in evaluate(evidence=[changed])["requirements"]
                if r["id"] == "GLI-GSF-2:B"
            )["status"]
            != "evidence_recorded"
        )


def test_publication_is_not_jurisdiction_adoption_or_approval():
    data = fixture()
    data["framework"]["systemInputs"]["jurisdictionBasis"] = {
        "jurisdiction": "Synthetic",
        "status": "unknown",
        "recordVersion": "2",
    }
    report = evaluate(data)
    assert report["applicability"]["status"] == "incomplete"
    assert any(r["id"] == "jurisdiction.adoption" for r in report["evidenceRequests"])
    assert all(
        r["status"] == "Unverified" and r["gap"] and r["nextStep"]
        for r in report["externalVerification"].values()
    )


def test_unknown_requirements_and_scope_expansion_are_rejected():
    data = fixture()
    data["framework"]["requirementChoices"] = {"invented": {}}
    with pytest.raises(ValueError, match="Unknown GLI requirement"):
        evaluate(data)
    data["framework"]["requirementChoices"] = {"GLI-GSF-2:B": {"systems": ["outside"]}}
    with pytest.raises(ValueError, match="scope"):
        evaluate(data)


def test_specialized_games_and_operational_controls_remain_visible_without_checks():
    rows = {r["id"]: r for r in evaluate()["requirements"]}
    for key in (
        "GLI-19:5.4",
        "GLI-19:5.5",
        "GLI-19:6.18",
        "GLI-19:A.6",
        "GLI-GSF-1:GIS-3",
        "GLI-GSF-5:OGIS-5",
    ):
        assert rows[key]["status"] == "incomplete"
        assert any(
            r.get("requirementId") == key for r in evaluate()["evidenceRequests"]
        )
