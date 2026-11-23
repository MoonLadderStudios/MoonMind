"""GLI-19/GSF readiness as installed declared-pack data (#5195, #5435)."""

import copy
import json
from pathlib import Path

import pytest

from moonmind.security.framework_registry import installed_pack, pack_behavior
from moonmind.security.frameworks import EvidencePeriod
from moonmind.security.pack_readiness import evaluate_readiness, parse_evidence

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/security/gli_readiness.json"
PACK_ID = "gli-19-readiness"


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


def pack():
    return installed_pack(PACK_ID)[1]


def evaluate(data=None, evidence=None):
    """Readiness for the fixture through the installed pack and its behavior."""
    data = data or fixture()
    framework = data["framework"]
    behavior = pack_behavior(pack())
    records = [
        parse_evidence(behavior, row, row.get("artifactRef") or f"art_evidence_{index}")
        for index, row in enumerate(evidence or [])
    ]
    return evaluate_readiness(
        pack(),
        behavior,
        profile=framework["systemInputs"],
        period=EvidencePeriod.model_validate(framework["period"]),
        choices=framework.get("requirementChoices", {}),
        records=records,
        audit=None,
        admitted_assets=["casino"],
    )


def requests(result):
    return result.context["evidenceRequests"]


def test_installed_pack_is_declared_data_with_modules_rights_and_profile():
    current = pack()
    assert current.schema_version == "declared-framework-pack/v1"
    assert (current.edition, current.published_on.isoformat()) == ("4.0", "2026-11-03")
    assert current.source.url == fixture()["standardSource"]
    assert (current.publication_status, current.distribution) == (
        "published",
        "redistributable",
    )
    assert current.behavior == "gli-module-applicability"
    assert [m.id for m in current.modules] == [
        "GLI-19",
        "GLI-GSF-1",
        "GLI-GSF-2",
        "GLI-GSF-3",
        "GLI-GSF-5",
    ]
    assert {r.module for r in current.requirements} == {m.id for m in current.modules}
    assert all(
        r.summary and r.sources and r.evidence_types and r.responsibilities and r.subject_types
        for r in current.requirements
    )
    assert {f.role for f in current.profile.fields} >= {
        "entity_type",
        "product_type",
        "systems",
        "jurisdiction_basis",
    }


@pytest.mark.parametrize(
    "entity,product,modules",
    [
        ("operator", "interactive_gaming", {"GLI-19", "GLI-GSF-1", "GLI-GSF-2", "GLI-GSF-5"}),
        ("rgs", "interactive_gaming", {"GLI-19", "GLI-GSF-1", "GLI-GSF-2", "GLI-GSF-5"}),
        ("ancillary_provider", "ancillary_service", {"GLI-GSF-1", "GLI-GSF-2", "GLI-GSF-3"}),
    ],
)
def test_profile_inputs_select_modules_with_reasons(entity, product, modules):
    data = fixture()
    data["framework"]["systemInputs"].update(
        entityType=entity, productType=product, directlyAffectsGaming=False
    )
    result = evaluate(data)
    assert {
        m["module"] for m in result.context["modules"] if m["status"] == "applicable"
    } == modules
    assert all(m["reason"] for m in result.context["modules"])
    assert result.context["readiness"] == "incomplete"


def test_gsf3_is_never_assigned_automatically_to_an_rgs():
    data = fixture()
    data["framework"]["systemInputs"].update(entityType="rgs", directlyAffectsGaming=False)
    result = evaluate(data)
    gsf3 = next(m for m in result.context["modules"] if m["module"] == "GLI-GSF-3")
    assert gsf3["status"] == "not_applicable"
    assert {
        row["status"] for key, row in result.rows.items() if key.startswith("GLI-GSF-3:")
    } == {"not_applicable"}


def test_incomplete_profile_yields_incomplete_applicability_not_a_guess():
    data = fixture()
    for key in ("entityType", "productType"):
        data["framework"]["systemInputs"].pop(key)
    result = evaluate(data)
    assert result.context["applicability"]["status"] == "incomplete"
    assert {m["status"] for m in result.context["modules"]} == {"incomplete"}
    assert {row["status"] for row in result.rows.values()} == {"applicability_missing"}
    ids = {r["id"] for r in requests(result)}
    assert {"scope.entityType", "scope.productType", "module.GLI-19"} <= ids


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
    rows = evaluate(data).rows
    assert rows["GLI-19:3.2"]["status"] == "partner_owned"
    assert rows["GLI-19:3.2"]["responsibilityReason"] == "KYC owned by contracted operator"
    assert rows["GLI-19:6.10"]["status"] == "excluded"
    assert rows["GLI-GSF-2:B"]["period"].end.isoformat() == "2026-11-06"
    del data["framework"]["systemInputs"]["entityType"]
    result = evaluate(data)
    assert result.context["applicability"]["status"] == "incomplete"
    assert any(r["id"] == "scope.entityType" for r in requests(result))


@pytest.mark.parametrize(
    "product,profile", [("gaming_device", "GLI-11"), ("event_wagering", "GLI-33")]
)
def test_other_products_require_their_own_profile(product, profile):
    data = fixture()
    data["framework"]["systemInputs"]["productType"] = product
    result = evaluate(data)
    assert result.context["readiness"] == "incomplete"
    assert profile in result.context["applicability"]["reason"]


def test_only_matching_scoped_evidence_can_support_readiness():
    evidence = fixture()["technicalEvidence"]
    result = evaluate(evidence=[evidence])
    rows = result.rows
    assert rows["GLI-GSF-2:B"]["status"] == "evidence_recorded"
    assert rows["GLI-19:5.2"]["status"] == "incomplete"
    assert rows["GLI-19:1.5"]["status"] == "incomplete"
    assert result.context["certification"] == "not_performed"
    assert result.context["jurisdictionApproval"] == "not_performed"
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
        assert evaluate(evidence=[changed]).rows["GLI-GSF-2:B"]["status"] != "evidence_recorded"


def test_clean_scan_leaves_game_operational_and_laboratory_gaps_open():
    result = evaluate(evidence=[fixture()["technicalEvidence"]])
    missing = {kind for r in requests(result) for kind in r.get("evidenceTypes", [])}
    assert {"rng", "game_math", "organizational", "laboratory", "regulator"} <= missing


def test_publication_is_not_jurisdiction_adoption_or_approval():
    data = fixture()
    data["framework"]["systemInputs"]["jurisdictionBasis"] = {
        "jurisdiction": "Synthetic",
        "status": "unknown",
        "recordVersion": "2",
    }
    result = evaluate(data)
    assert result.context["applicability"]["status"] == "incomplete"
    assert any(r["id"] == "jurisdiction.adoption" for r in requests(result))


def test_laboratory_regulator_and_customer_qualification_stay_unverified():
    claims = evaluate(evidence=[fixture()["technicalEvidence"]]).context["externalClaims"]
    for name in ("laboratory", "regulator", "customer"):
        assert claims[name]["status"] == "Unverified"
        assert claims[name]["verifiableBy"] and claims[name]["nextStep"]
    # Laboratory evaluation and regulator approval are separate claims.
    assert claims["laboratory"]["verifiableBy"] != claims["regulator"]["verifiableBy"]


def test_unknown_requirements_and_scope_expansion_are_rejected():
    data = fixture()
    data["framework"]["requirementChoices"] = {"invented": {}}
    with pytest.raises(ValueError, match="Unknown framework requirement"):
        evaluate(data)
    data["framework"]["requirementChoices"] = {"GLI-GSF-2:B": {"systems": ["outside"]}}
    with pytest.raises(ValueError, match="scope"):
        evaluate(data)
    data["framework"]["requirementChoices"] = {}
    data["framework"]["systemInputs"]["systems"] = ["outside"]
    with pytest.raises(ValueError, match="admitted assessment scope"):
        evaluate(data)


def test_specialized_games_and_operational_controls_remain_visible_without_checks():
    result = evaluate()
    for key in (
        "GLI-19:5.4",
        "GLI-19:5.5",
        "GLI-19:6.18",
        "GLI-19:A.6",
        "GLI-GSF-1:GIS-3",
        "GLI-GSF-5:OGIS-5",
    ):
        assert result.rows[key]["status"] == "incomplete"
        assert any(r.get("requirementId") == key for r in requests(result))
