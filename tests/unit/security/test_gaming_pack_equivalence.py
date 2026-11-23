"""GLI-19/GSF and NJ DGE on the unified pack contract match their legacy results.

``gaming_equivalence.json`` froze the legacy framework-pack/v1 evaluators'
requirement-level statuses, reasons, scope overrides, evidence requests and
external-claim boundaries before the migration (MoonLadderStudios/MoonMind#5435).
"""

import json
from pathlib import Path

import pytest

from moonmind.security.framework_registry import installed_pack, pack_behavior
from moonmind.security.frameworks import EvidencePeriod
from moonmind.security.pack_readiness import evaluate_readiness, parse_evidence

GOLDEN = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "fixtures/security/gaming_equivalence.json"
    ).read_text()
)
PACKS = {"gli": "gli-19-readiness", "nj": "nj-dge-readiness"}
LEGACY_CLAIMS = {
    "gli": {"organizational", "laboratory", "regulator"},
    "nj": {
        "source-completeness",
        "gli-adoption",
        "professional-assessment",
        "regulator",
    },
}


def unified(scenario):
    pack = installed_pack(PACKS[scenario["family"]])[1]
    behavior = pack_behavior(pack)
    framework = scenario["framework"]
    profile = framework["systemInputs"]
    audit = scenario["audit"]
    return evaluate_readiness(
        pack,
        behavior,
        profile=profile,
        period=EvidencePeriod.model_validate(framework["period"]),
        choices=framework.get("requirementChoices", {}),
        records=[
            parse_evidence(behavior, r, r["artifactRef"]) for r in scenario["evidence"]
        ],
        audit=behavior.records_model.model_validate(audit) if audit else None,
        admitted_assets=profile.get("systems") or ["casino"],
    )


def project(scenario, result):
    family = scenario["family"]
    framework = scenario["framework"]
    systems = framework["systemInputs"].get("systems", [])
    statuses, reasons, overrides = {}, {}, {}
    for key, row in result.rows.items():
        statuses.setdefault(row["status"], []).append(key)
        reasons.setdefault(row["reason"], []).append(key)
        extra = {}
        if row["systems"] != systems:
            extra["systems"] = row["systems"]
        period = row["period"].model_dump(mode="json")
        if period != framework["period"]:
            extra["period"] = period
        if row["evidenceRefs"]:
            extra["evidenceRefs"] = row["evidenceRefs"]
        if family == "gli" and row["responsibleEntity"] != "assessed_entity":
            extra["responsibleEntity"] = row["responsibleEntity"]
        if family == "nj" and row["applicabilityStatus"] != row["status"]:
            extra["applicabilityStatus"] = row["applicabilityStatus"]
        if extra:
            overrides[key] = extra
    context = result.context
    projected = {
        "readiness": context["readiness"],
        "statuses": statuses,
        "reasons": [[reason, ids] for reason, ids in reasons.items()],
        "overrides": overrides,
        "evidenceRequests": [
            (
                r["id"]
                if r.get("requirementId") in (None, r["id"])
                else [r["id"], r["requirementId"]]
            )
            for r in context["evidenceRequests"]
        ],
    }
    if family == "gli":
        projected["modules"] = [
            [m["module"], m["status"], m["reason"]] for m in context["modules"]
        ]
        projected["applicability"] = context["applicability"]
        projected["certification"] = context["certification"]
        projected["jurisdictionApproval"] = context["jurisdictionApproval"]
    else:
        projected["reportingConditions"] = context["reportingConditions"]
    return projected


@pytest.mark.parametrize("scenario", GOLDEN["scenarios"], ids=lambda s: s["id"])
def test_requirement_conclusions_and_requests_match_legacy(scenario):
    result = unified(scenario)
    expected = {
        key: value
        for key, value in scenario["expected"].items()
        if key not in {"externalClaims", "boundaries"}
    }
    assert project(scenario, result) == expected


@pytest.mark.parametrize("scenario", GOLDEN["scenarios"], ids=lambda s: s["id"])
def test_external_claim_boundaries_stay_unverified_with_next_checks(scenario):
    claims = unified(scenario).context["externalClaims"]
    family = scenario["family"]
    assert LEGACY_CLAIMS[family] | {"laboratory", "regulator", "customer"} <= set(
        claims
    )
    assert all(
        claim["status"] == "Unverified" and claim["verifiableBy"] and claim["nextStep"]
        for claim in claims.values()
    )
    if family == "gli":
        for name, legacy in scenario["expected"]["externalClaims"].items():
            claim = claims[name]
            assert (claim["status"], claim["verifiableBy"], claim["evidenceRefs"]) == (
                legacy["status"],
                legacy["verifiableBy"],
                legacy["evidenceRefs"],
            )
    else:
        expected = scenario["expected"]
        assert expected["externalClaims"]["externalAssurance"] == "Unverified"
        assert expected["boundaries"] == {
            "sourceCompleteness": claims["source-completeness"]["status"],
            "gliAdoption": claims["gli-adoption"]["status"],
        }
