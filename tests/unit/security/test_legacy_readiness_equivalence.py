"""ISO/IEC 27001, SOC 2 and UKGC RTS on the unified pack contract match legacy.

``legacy_readiness_equivalence.json`` froze the framework-pack/v1 evaluators'
requirement-level statuses, scope overrides, documentary reviews, evidence
requests, Statement of Applicability decisions, risk treatment, reassessment
and external-claim boundaries before the migration
(MoonLadderStudios/MoonMind#5436). Each scenario is replayed through the saved
``framework.packId`` request boundary, so saved inputs reach the same
conclusions.

Intended differences, asserted here rather than hidden:

- status vocabulary is shared: ``evidence_reviewed`` and SOC 2 ``unassessed``
  are ``evidence_recorded`` and ``incomplete``; a complete ISO readiness is
  ``prepared``;
- requests raised for missing profile inputs also cite the pack's source;
- certification, organisational operation, CPA examination and independence
  are named external claims that stay Unverified;
- the UKGC edition is the identifier ``ISO-IEC-27001-2022``, and its annual
  audit applicability is ``auditApplicability`` beside the shared
  ``applicability``.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from moonmind.security.framework_packs import (
    readiness_metadata,
    retained_pack_metadata,
)
from moonmind.security.framework_registry import (
    installed_digest,
    installed_pack,
    pack_behavior,
)
from moonmind.security.frameworks import EvidencePeriod, normalize_installed_framework
from moonmind.security.oscal_export import control_id
from moonmind.security.pack_readiness import evaluate_readiness, parse_evidence

GOLDEN = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "fixtures/security/legacy_readiness_equivalence.json"
    ).read_text()
)
NOW = datetime.fromisoformat(GOLDEN["evaluatedAt"])
STATUS = {"evidence_reviewed": "evidence_recorded", "unassessed": "incomplete"}
READINESS = {"evidence_reviewed": "prepared"}
SCENARIOS = {row["id"]: row for row in GOLDEN["scenarios"]}


def resolve(value):
    if isinstance(value, str) and value[:1] in {"@", "#"} and value[1:].isdigit():
        if value[0] == "@":
            return GOLDEN["strings"][int(value[1:])]
        return resolve(GOLDEN["objects"][int(value[1:])])
    if isinstance(value, list):
        return [resolve(item) for item in value]
    if isinstance(value, dict):
        return {key: resolve(item) for key, item in value.items()}
    return value


def selection(scenario):
    normalized = normalize_installed_framework({"framework": scenario["framework"]})
    if "framework" in normalized:
        raise ValueError("saved framework inputs are invalid")
    return normalized["frameworkPacks"][0]


def admitted(scenario):
    system = scenario["framework"]["systemInputs"]
    assets = set(system.get("systems") or [])
    for item in system.get("criticalSystems") or []:
        assets.update(item.get("systems") or [])
    return sorted(assets)


def unified(scenario, previous=None):
    chosen = selection(scenario)
    pack = installed_pack(chosen["packId"])[1]
    behavior = pack_behavior(pack)
    audit = scenario["audit"]
    return evaluate_readiness(
        pack,
        behavior,
        profile=chosen.get("profile"),
        period=EvidencePeriod.model_validate(chosen["period"]),
        choices=chosen.get("requirementChoices", {}),
        records=[
            parse_evidence(behavior, row, row["artifactRef"])
            for row in scenario["evidence"]
        ],
        audit=(
            behavior.records_model.model_validate(audit)
            if audit and behavior.records_model
            else None
        ),
        admitted_assets=admitted(scenario),
        availability=scenario["availability"],
        now=NOW,
        previous=previous,
        previous_ref="art_previous" if previous is not None else None,
    )


def retained(scenario, result):
    """The previous assessment's pack metadata, as a unified report keeps it."""
    pack_id = selection(scenario)["packId"]
    pack = installed_pack(pack_id)[1]
    metadata = retained_pack_metadata(
        pack, artifact_ref="art_pack", sha256=installed_digest(pack_id), now=NOW
    )
    metadata["readiness"] = json.loads(
        json.dumps(readiness_metadata(pack, result), default=str)
    )
    return metadata


def evaluated(scenario):
    previous = None
    if scenario["previous"]:
        prior = SCENARIOS[scenario["previous"]]
        previous = retained(prior, evaluated(prior))
    return unified(scenario, previous)


def matches_request(legacy, current):
    """Requests for missing profile inputs now also cite the pack's source."""
    extra = {key for key in ("source", "section") if key not in legacy}
    return {k: v for k, v in current.items() if k not in extra} == legacy


def grouped(pairs):
    result = {}
    for key, status in pairs:
        result.setdefault(STATUS.get(status, status), []).append(key)
    return result


def legacy_statuses(expected):
    result = {}
    for status, ids in expected["statuses"].items():
        result.setdefault(STATUS.get(status, status), []).extend(ids)
    return result


DECISION_DEFAULTS = {
    "decision": "undetermined",
    "rationale": None,
    "riskIds": [],
    "implementationStatus": "unknown",
    "owner": None,
    "responsibleEntity": "assessed_entity",
    "responsibilityReason": None,
    "evidenceStatus": "incomplete",
    "verification": "Unverified",
}


def compress_decisions(decisions, systems, period):
    changed = {}
    for row in decisions:
        defaults = dict(DECISION_DEFAULTS, systems=systems, period=period)
        diff = {
            key: (STATUS.get(value, value) if key == "evidenceStatus" else value)
            for key, value in row.items()
            if key not in {"controlId", "title"} and defaults.get(key) != value
        }
        if not row["controlId"].startswith("A."):
            diff["title"] = row["title"]
        if diff:
            changed[row["controlId"]] = diff
    return {"count": len(decisions), "changed": changed}


def iso_projection(scenario, result):
    framework = scenario["framework"]
    context = result.context
    systems = context["profile"]["systems"]
    additional = context.get("additionalControls", {})
    rows = {
        **{
            key: {
                "status": row["status"],
                "systems": row["systems"],
                "period": row["period"].model_dump(mode="json"),
                "responsibleEntity": row["responsibleEntity"],
                "responsibilityReason": row["responsibilityReason"],
                **row["details"],
            }
            for key, row in result.rows.items()
        },
        **additional,
    }
    details = {}
    for key, row in rows.items():
        extra = {}
        if sorted(row["systems"]) != sorted(systems):
            extra["systems"] = row["systems"]
        if row["period"] != framework["period"]:
            extra["period"] = row["period"]
        for name in ("technicalContextRefs", "limitations"):
            if row.get(name):
                extra[name] = row[name]
        if row["responsibleEntity"] != "assessed_entity":
            extra["responsibleEntity"] = row["responsibleEntity"]
            extra["responsibilityReason"] = row["responsibilityReason"]
        if row.get("evidenceReviews"):
            extra["evidenceReviews"] = [
                [r["requestId"], r["state"], r["limitations"]]
                for r in row["evidenceReviews"]
            ]
        if key in additional:
            extra["additional"] = True
        if extra:
            details[key] = extra
    soa = context["statementOfApplicability"]
    projected = {
        "readiness": context["readiness"],
        "statuses": grouped((key, row["status"]) for key, row in rows.items()),
        "details": details,
        "statementOfApplicability": {
            **{key: soa[key] for key in ("status", "edition", "scopeStatement")},
            "decisions": compress_decisions(
                soa["decisions"], systems, framework["period"]
            ),
        },
        "riskTreatmentActions": context["riskTreatmentActions"],
        "certification": context["externalClaims"]["certification"]["status"],
        "organisationalOperation": context["externalClaims"][
            "organisational-operation"
        ]["status"],
    }
    if "reassessment" in context:
        projected["reassessment"] = {
            key: context["reassessment"][key]
            for key in ("affectedControlIds", "affectedRequirementIds")
        }
    return projected


def soc2_projection(scenario, result):
    framework = scenario["framework"]
    context = result.context
    systems = context["profile"]["systems"]
    details = {}
    for key, row in result.rows.items():
        extra = {}
        if row["systems"] != systems:
            extra["systems"] = row["systems"]
        period = row["period"].model_dump(mode="json")
        if period != framework["period"]:
            extra["period"] = period
        detail = row["details"]
        for name in ("limitations",):
            if detail.get(name):
                extra[name] = detail[name]
        if row["evidenceRefs"]:
            extra["evidenceRefs"] = row["evidenceRefs"]
        if detail.get("contextEvidenceRefs"):
            extra["contextEvidenceRefs"] = detail["contextEvidenceRefs"]
        if detail.get("sampling") is not None:
            extra["sampling"] = detail["sampling"]
        if detail.get("organisationalEvidence"):
            extra["organisationalEvidence"] = [
                [r["requestId"], r["state"], r["currentRef"], r["limitations"]]
                for r in detail["organisationalEvidence"]
            ]
        if row["responsibleEntity"] != "assessed_entity":
            extra["responsibleEntity"] = row["responsibleEntity"]
            extra["responsibilityReason"] = row["responsibilityReason"]
        if extra:
            details[key] = extra
    reasons = {}
    for key, row in result.rows.items():
        reasons.setdefault(row["reason"], []).append(key)
    claims = context["externalClaims"]
    return {
        "readiness": context["readiness"],
        "statuses": grouped((key, row["status"]) for key, row in result.rows.items()),
        "reasons": reasons,
        "details": details,
        "externalVerification": {
            name: {"status": claims[name]["status"], "gap": claims[name]["gap"]}
            for name in ("examination", "independence", "organizational")
        },
        "sourceContext": context["sourceContext"],
    }


def ukgc_projection(scenario, result):
    framework = scenario["framework"]
    context = result.context
    systems = sorted(
        {
            s
            for item in context["profile"]["criticalSystems"] or []
            if item["inScope"]
            for s in item["systems"]
        }
    )
    details = {}
    for key, row in result.rows.items():
        extra = {}
        if row["systems"] != systems:
            extra["systems"] = row["systems"]
        period = row["period"].model_dump(mode="json")
        if period != framework["period"]:
            extra["period"] = period
        detail = row["details"]
        if detail.get("methods"):
            extra["methods"] = detail["methods"]
        if row["evidenceRefs"]:
            extra["evidenceRefs"] = row["evidenceRefs"]
        if detail.get("findings"):
            extra["findings"] = detail["findings"]
        if row["exclusionReason"]:
            extra["exclusionReason"] = row["exclusionReason"]
        if extra:
            details[key] = extra
    return {
        "readiness": context["readiness"],
        "statuses": grouped((key, row["status"]) for key, row in result.rows.items()),
        "details": details,
        "applicability": context["auditApplicability"],
        **{
            key: context[key]
            for key in ("schedule", "reportingConditions", "audit", "externalAudit")
        },
    }


PROJECTIONS = {
    "iso": iso_projection,
    "soc2": soc2_projection,
    "ukgc": ukgc_projection,
}
CONCLUDED = [row for row in GOLDEN["scenarios"] if "error" not in row["expected"]]


@pytest.mark.parametrize("scenario", CONCLUDED, ids=lambda s: s["id"])
def test_requirement_conclusions_match_the_frozen_legacy_evaluator(scenario):
    expected = resolve(scenario["expected"])
    result = evaluated(scenario)
    projected = PROJECTIONS[scenario["family"]](scenario, result)
    expected_statuses = legacy_statuses(expected)
    assert projected.pop("statuses") == expected_statuses
    assert projected.pop("readiness") == READINESS.get(
        expected["readiness"], expected["readiness"]
    )
    if scenario["family"] == "iso":
        soa = expected["statementOfApplicability"]
        soa["decisions"]["changed"] = {
            key: {
                name: (STATUS.get(value, value) if name == "evidenceStatus" else value)
                for name, value in row.items()
            }
            for key, row in soa["decisions"]["changed"].items()
        }
    # Every frozen conclusion is compared; requests and SoA versions have
    # their own checks below.
    assert set(expected) - set(projected) == {
        "statuses",
        "readiness",
        "evidenceRequests",
        *(["soaVersion"] if scenario["family"] == "iso" else []),
        *(["soaVersionChanged"] if "soaVersionChanged" in expected else []),
    }
    for key, value in projected.items():
        assert value == expected[key], key


@pytest.mark.parametrize("scenario", CONCLUDED, ids=lambda s: s["id"])
def test_evidence_requests_match_the_frozen_legacy_evaluator(scenario):
    expected = resolve(scenario["expected"])["evidenceRequests"]
    current = evaluated(scenario).context["evidenceRequests"]
    assert [row["id"] for row in current] == [row["id"] for row in expected]
    for legacy, row in zip(expected, current, strict=True):
        assert matches_request(legacy, row), legacy["id"]


@pytest.mark.parametrize(
    "scenario",
    [row for row in CONCLUDED if row["family"] == "iso" and row["previous"]],
    ids=lambda s: s["id"],
)
def test_statement_of_applicability_version_changes_as_before(scenario):
    expected = resolve(scenario["expected"])
    prior = SCENARIOS[scenario["previous"]]
    before = evaluated(prior).context["statementOfApplicability"]["version"]
    after = evaluated(scenario).context
    assert (after["statementOfApplicability"]["version"] != before) == expected[
        "soaVersionChanged"
    ]
    assert after["reassessment"]["previousSoAVersion"] == before


@pytest.mark.parametrize(
    "scenario",
    [row for row in GOLDEN["scenarios"] if "error" in row["expected"]],
    ids=lambda s: s["id"],
)
def test_inputs_the_legacy_evaluator_rejected_are_still_rejected(scenario):
    with pytest.raises(ValueError):
        evaluated(scenario)


@pytest.mark.parametrize("pack_id", sorted(GOLDEN["legacyPacks"]))
def test_requirement_identities_and_oscal_control_ids_are_unchanged(pack_id):
    legacy = GOLDEN["legacyPacks"][pack_id]
    pack = installed_pack(pack_id)[1]
    assert [
        [row.id, row.title, row.category, control_id(row.id)]
        for row in pack.requirements
    ] == legacy["requirements"]
    assert pack.title == legacy["title"]
    assert pack.edition == (
        "ISO-IEC-27001-2022" if pack_id == "ukgc-rts-security" else legacy["edition"]
    )


@pytest.mark.parametrize("family", ["iso", "soc2", "ukgc"])
def test_external_claims_stay_unverified_with_next_checks(family):
    scenario = next(row for row in CONCLUDED if row["family"] == family)
    claims = evaluated(scenario).context["externalClaims"]
    assert claims
    assert all(
        claim["status"] == "Unverified" and claim["verifiableBy"] and claim["nextStep"]
        for claim in claims.values()
    )
