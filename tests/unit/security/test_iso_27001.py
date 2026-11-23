"""ISO/IEC 27001 ISMS readiness as an installed pack on the shared evaluator.

Requirement-level equivalence with the retired framework-pack/v1 evaluator is in
``test_legacy_readiness_equivalence.py``. These tests cover the pack data, the
saved request shape and the Statement of Applicability behavior directly.
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
PACK_ID = "iso-27001-readiness"


def selection(**changes):
    """A saved ``framework.packId`` request, as presets, reruns and schedules keep it."""
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
        pack_id=PACK_ID,
        system_inputs=system,
        period={"start": "2026-11-01", "end": "2026-11-02"},
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


def normalized(request=None):
    framework = (request or selection()).model_dump(mode="json", by_alias=True)
    return normalize_installed_framework({"framework": framework})["frameworkPacks"][0]


def evaluate(request=None, availability=None, previous=None):
    chosen = normalized(request)
    pack = installed_pack(PACK_ID)[1]
    return evaluate_readiness(
        pack,
        pack_behavior(pack),
        profile=chosen["profile"],
        period=EvidencePeriod.model_validate(chosen["period"]),
        choices=chosen.get("requirementChoices", {}),
        records=[],
        audit=None,
        admitted_assets=["isms", "second"],
        availability=availability or {},
        now=NOW,
        previous=previous,
        previous_ref="art_previous" if previous else None,
    )


def test_pack_covers_management_system_and_all_reference_identifiers():
    pack = installed_pack(PACK_ID)[1]
    assert pack.edition == "2022+Amd1:2024"
    assert pack.amendments == ["Amd1:2024"]
    assert pack.behavior == "iso-27001-statement-of-applicability"
    assert len([r for r in pack.requirements if r.category == "annex_a"]) == 93
    management = {r.id for r in pack.requirements if r.category == "management_system"}
    assert {"4.1", "4.2", "9.2", "9.3", "10.1", "10.2"} <= management
    # Amendment 1 sources stay on the climate-related context clauses.
    clauses = {r.id: r for r in pack.requirements}
    assert any("88435" in s.url for s in clauses["4.1"].sources)
    profile = {field.name: field for field in pack.profile.fields}
    assert {
        "scopeStatement",
        "context",
        "interestedParties",
        "climateRelevance",
        "risks",
        "controls",
    } <= set(profile)
    assert profile["risks"].type == profile["controls"].type == "structured"


def test_saved_request_moves_documentary_evidence_to_requirement_choices():
    chosen = normalized(
        selection(
            requirementEvidence={
                "9.2": [reviewed_evidence()],
                "EXTRA.1": [reviewed_evidence("design")],
            }
        )
    )
    assert "requirementEvidence" not in chosen["profile"]
    [moved] = chosen["requirementChoices"]["9.2"]["organisationalEvidence"]
    assert moved["id"] == "audit-record"
    # An additional control is not a pack requirement; its evidence stays on
    # its Statement of Applicability decision.
    extra = chosen["profile"]["controls"]["EXTRA.1"]
    assert extra["organisationalEvidence"][0]["purpose"] == "design"


def test_saved_evidence_for_an_unknown_requirement_is_refused():
    from moonmind.security.assessments import AssessmentRequest

    request = selection(requirementEvidence={"EXTRA.9": [reviewed_evidence()]})
    with pytest.raises(ValueError, match="unknown evidence requirement"):
        AssessmentRequest.model_validate(
            {
                "scope": {"assets": ["isms"]},
                "tools": [],
                "budget": {
                    "baseSeconds": 60,
                    "maxSeconds": 120,
                    "progressStallSeconds": 60,
                },
                "credentialRefs": [],
                "networkRefs": [],
                "framework": request.model_dump(mode="json", by_alias=True),
            }
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"systems": []}, "at least one system"),
        ({"controls": {"B.1": {"decision": "include"}}}, "EXTRA."),
        (
            {"controls": {"A.8.8": {"decision": "include", "riskIds": ["unknown"]}}},
            "unknown risk",
        ),
        (
            {"controls": {"A.8.8": {"decision": "include", "systems": ["isms"]}}},
            "requirementChoices",
        ),
    ],
)
def test_statement_of_applicability_inputs_stay_consistent(changes, message):
    with pytest.raises(ValueError, match=message):
        evaluate(selection(**changes))


def test_draft_soa_has_a_content_derived_version_and_headline():
    result = evaluate()
    soa = result.context["statementOfApplicability"]
    assert soa["status"] == "draft"
    assert soa["version"] == evaluate().context["statementOfApplicability"]["version"]
    decisions = {row["controlId"]: row for row in soa["decisions"]}
    assert len(decisions) == 94
    assert decisions["EXTRA.1"]["riskIds"] == ["R1"]
    assert decisions["A.8.8"]["verification"] == "Unverified"
    assert soa["version"] in result.context["headline"][0]
    changed = evaluate(selection(scopeStatement="Wider scope"))
    assert changed.context["statementOfApplicability"]["version"] != soa["version"]
    # Evidence revisions change conclusions, not the SoA scope.
    with_evidence = evaluate(
        selection(requirementEvidence={"9.2": [reviewed_evidence()]})
    )
    assert (
        with_evidence.context["statementOfApplicability"]["version"] == soa["version"]
    )


def test_conclusions_never_certify_the_isms():
    result = evaluate(
        selection(requirementEvidence={"9.2": [reviewed_evidence()]}),
        availability={"art_audit": None},
    )
    assert result.rows["9.2"]["status"] == "evidence_recorded"
    assert result.context["readiness"] == "incomplete"
    claims = result.context["externalClaims"]
    assert {claims[k]["status"] for k in ("certification", "organisational-operation")} == {
        "Unverified"
    }
    assert result.context["certification"] == "not_performed"


def test_additional_control_choices_and_evidence_are_admitted_from_the_profile():
    from moonmind.security.organisational_evidence import organisational_evidence_refs

    item = reviewed_evidence("design")
    item["revisions"].append(dict(item["revisions"][0], artifactRef="art_current"))
    chosen = normalized(
        selection(
            requirementEvidence={"EXTRA.1": [item], "9.2": [reviewed_evidence()]}
        )
    )
    task = {"tool": {"id": "security.assess", "inputs": {"frameworkPacks": [chosen]}}}
    assert set(organisational_evidence_refs(task)) == {"art_audit", "art_current"}
    saved = {
        "tool": {
            "id": "security.assess",
            "inputs": {
                "framework": selection(
                    requirementEvidence={"EXTRA.1": [item]}
                ).model_dump(mode="json", by_alias=True)
            },
        }
    }
    assert organisational_evidence_refs(saved) == ["art_current"]


def test_export_association_includes_only_current_documents():
    from moonmind.security.assessments import AssessmentRequest
    from moonmind.workflows.skills.security_evidence_export import associated_evidence

    item = reviewed_evidence()
    item["revisions"].append(dict(item["revisions"][0], artifactRef="art_current"))
    request = AssessmentRequest.model_validate(
        {
            "scope": {"assets": ["isms"]},
            "tools": [],
            "budget": {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
            "credentialRefs": [],
            "networkRefs": [],
            "framework": selection(requirementEvidence={"9.2": [item]}).model_dump(
                mode="json", by_alias=True
            ),
        }
    )
    refs = associated_evidence(
        request=request,
        work={},
        outputs={"scanRef": "art_scan"},
        evidence=[],
        summary_ref=None,
        review=None,
    )
    assert "art_current" in refs
    assert "art_audit" not in refs
