"""Recurring security comparison contracts for MoonLadderStudios/MoonMind#5047."""

import copy
from datetime import UTC, datetime, timedelta

import pytest

from moonmind.workflows.skills.tool_definitions import default_registry_tool_payload

NOW = datetime(2026, 10, 26, tzinfo=UTC)


def report(*, findings=True, **changes):
    payload = {
        "schemaVersion": "security-observation/v1",
        "scope": {"assets": ["app"], "exclusions": [], "repositoryExceptions": []},
        "observedAt": NOW.isoformat(),
        "expiresAt": (NOW + timedelta(days=1)).isoformat(),
        "coverage": [
            {
                "source": "advisories",
                "tool": "inventory-feed",
                "assets": ["app"],
                "ruleIds": ["CVE-EXAMPLE"],
                "status": "complete",
                "configRef": "config:default",
            }
        ],
        "provenance": {"inventory": "art_inventory", "feed": "art_feed1"},
        "findings": (
            [
                {
                    "source": "advisories",
                    "tool": "inventory-feed",
                    "ruleId": "CVE-EXAMPLE",
                    "asset": "app",
                    "nativeId": "pkg:pypi/example@1.0",
                    "evidenceRefs": ["art_evidence"],
                }
            ]
            if findings
            else []
        ),
    }
    payload.update(changes)
    return payload


def compare(current, previous=None, *, decisions=(), now=NOW):
    from moonmind.security.assessments import Observation, compare_observations

    return compare_observations(
        Observation.model_validate(current), previous, decisions=decisions, now=now
    )


def test_assessment_requires_explicit_security_scope_and_bounds():
    definition = default_registry_tool_payload(name="security.assess")
    schema = definition["inputs"]["schema"]
    assert set(schema.get("required", ())) >= {
        "scope",
        "tools",
        "budget",
        "credentialRefs",
        "networkRefs",
    }
    assert schema["additionalProperties"] is False


def test_security_assessment_uses_bounded_native_activity():
    definition = default_registry_tool_payload(name="security.assess")
    assert definition["executor"]["activity_type"] == "mm.tool.execute"
    assert definition["policies"]["timeouts"]["start_to_close_seconds"] <= 300
    assert definition["policies"]["retries"]["max_attempts"] == 2


def test_stable_identity_uses_native_rule_and_asset_and_keeps_evidence():
    first = compare(report())
    finding_id = first["findings"][0]["findingId"]
    current = report()
    current["findings"][0]["evidenceRefs"] = ["art_new_evidence"]
    result = compare(current, first)
    assert result["findings"][0]["state"] == "still_observed"
    assert result["findings"][0]["findingId"] == finding_id
    assert result["findings"][0]["finding"]["evidenceRefs"] == [
        "art_evidence",
        "art_new_evidence",
    ]
    for key in ("source", "tool", "ruleId", "asset", "nativeId"):
        changed = copy.deepcopy(current)
        changed["findings"][0][key] += "-different"
        if key == "asset":
            changed["scope"]["assets"].append("app-different")
        assert compare(changed)["findings"][0]["findingId"] != finding_id


@pytest.mark.parametrize(
    "change",
    [
        "partial",
        "authentication_failed",
        "unsupported",
        "failed",
        "scope",
        "exclusions",
        "exceptions",
        "expired",
        "previous_expired",
        "configuration",
        "missing_rule",
    ],
)
def test_comparison_never_resolves_unreassessed_findings(change):
    previous = compare(report())
    current = report(findings=False)
    if change in {"partial", "authentication_failed", "unsupported", "failed"}:
        current["coverage"][0]["status"] = change
    elif change == "scope":
        current["scope"]["assets"] = ["other"]
        current["coverage"][0]["assets"] = ["other"]
    elif change == "exclusions":
        current["scope"]["exclusions"] = ["vendor"]
    elif change == "exceptions":
        current["scope"]["repositoryExceptions"] = ["art_repository_exception"]
    elif change == "expired":
        current["expiresAt"] = NOW.isoformat()
    elif change == "previous_expired":
        previous["observation"]["expiresAt"] = NOW.isoformat()
    elif change == "configuration":
        current["coverage"][0]["configRef"] = "config:changed"
    elif change == "missing_rule":
        current["coverage"][0]["ruleIds"] = []
    result = compare(current, previous)
    assert result["findings"][0]["state"] == "not_reassessed"
    assert result["findings"][0]["finding"]["evidenceRefs"] == ["art_evidence"]


def test_only_comparable_complete_coverage_supports_absence():
    previous = compare(report())
    current = report(findings=False)
    current["provenance"]["feed"] = "art_feed2"
    result = compare(current, previous)
    assert result["findings"][0]["state"] == "absent_comparable"
    assert result["counts"]["absent_comparable"] == 1
    assert result["findings"][0]["finding"]["evidenceRefs"] == ["art_evidence"]


def test_repeated_changed_configuration_cannot_rebase_an_unobserved_finding():
    original = compare(report())
    changed = report(findings=False)
    changed["coverage"][0]["configRef"] = "config:changed"
    next_run = compare(changed, original)
    assert next_run["findings"][0]["state"] == "not_reassessed"
    assert compare(changed, next_run)["findings"][0]["state"] == "not_reassessed"


def test_later_complete_run_cannot_resolve_findings_carried_from_another_scope():
    previous = compare(report())
    other = report(findings=False, scope={"assets": ["other"]})
    other["coverage"][0]["assets"] = ["other"]
    partial = compare(other, previous)
    assert (
        compare(report(findings=False), partial)["findings"][0]["state"]
        == "not_reassessed"
    )


@pytest.mark.parametrize(
    "disposition",
    [
        "accepted_risk",
        "false_positive",
        "confirmed_vulnerability",
        "fix_retest",
    ],
)
def test_triage_is_attributable_and_never_rewrites_scan_facts(disposition):
    from moonmind.security.assessments import TriageDecision

    first = compare(report())
    decision = {
        "findingId": first["findings"][0]["findingId"],
        "disposition": disposition,
        "reason": "Fixture decision",
        "evidenceRefs": ["art_decision_evidence"],
        "author": "operator",
        "source": "operator",
        "scope": report()["scope"],
        "reviewAt": (NOW + timedelta(hours=2)).isoformat(),
        "reviewConditions": ["scope or rule change"],
    }
    if disposition == "fix_retest":
        decision["retestOutcome"] = "failed"
    result = compare(report(), first, decisions=[decision])
    assert result["findings"][0]["state"] == "still_observed"
    assert result["decisions"][0]["decision"]["disposition"] == disposition
    assert result["decisions"][0]["status"] == "active"
    expired = compare(report(), result, now=NOW + timedelta(hours=3))
    assert expired["decisions"][0]["status"] == "review_required"
    assert expired["counts"]["review_required"] == 1

    changed = report()
    changed["provenance"]["feed"] = "art_feed2"
    pending = compare(changed, result, decisions=[decision])
    assert pending["decisions"][0]["status"] == "review_required"
    repeated = compare(changed, pending, decisions=[decision])
    assert repeated["decisions"][0]["status"] == "review_required"
    assert repeated["counts"]["review_required"] == 1

    renewed = {
        **decision,
        "reason": "Reviewed the updated feed",
        "evidenceRefs": ["art_renewed_decision_evidence"],
    }
    reviewed = compare(changed, repeated, decisions=[renewed])
    assert len(reviewed["decisions"]) == 1
    assert TriageDecision.model_validate(
        reviewed["decisions"][0]["decision"]
    ) == TriageDecision.model_validate(renewed)
    assert reviewed["decisions"][0]["status"] == "active"
    assert reviewed["counts"]["review_required"] == 0
    assert reviewed["findings"][0]["state"] == "still_observed"


def test_malformed_current_and_baseline_fail_closed():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        compare({"findings": []})
    with pytest.raises(ValidationError):
        compare(report(), {"findings": []})


def test_duplicate_native_identities_are_rejected():
    from pydantic import ValidationError

    current = report()
    current["findings"].append(copy.deepcopy(current["findings"][0]))
    with pytest.raises(ValidationError, match="duplicate"):
        compare(current)


def _decision(finding_id, **changes):
    return {
        "findingId": finding_id,
        "disposition": "fix_retest",
        "reason": "Patched and retested",
        "evidenceRefs": ["art_retest"],
        "author": "operator",
        "source": "operator",
        "scope": report()["scope"],
        "reviewAt": (NOW + timedelta(days=7)).isoformat(),
        "reviewConditions": ["scope or rule change"],
        "retestOutcome": "passed",
        **changes,
    }


def test_passing_retest_with_comparable_absence_is_verified_remediation():
    previous = compare(report())
    finding_id = previous["findings"][0]["findingId"]
    verified = compare(
        report(findings=False), previous, decisions=[_decision(finding_id)]
    )
    assert verified["findings"][0]["state"] == "remediated_verified"
    assert verified["findings"][0]["verification"] == "retest_passed"
    assert verified["counts"]["remediated_verified"] == 1
    assert verified["counts"]["absent_comparable"] == 0

    # Absence without a linked retest stays a coverage observation.
    assert (
        compare(report(findings=False), previous)["findings"][0]["state"]
        == "absent_comparable"
    )
    # A failed retest or an incomparable rerun is never verified remediation.
    failed = _decision(finding_id, retestOutcome="failed")
    assert (
        compare(report(findings=False), previous, decisions=[failed])["findings"][0][
            "state"
        ]
        == "absent_comparable"
    )
    partial = report(findings=False)
    partial["coverage"][0]["status"] = "partial"
    result = compare(partial, previous, decisions=[_decision(finding_id)])
    assert result["findings"][0]["state"] == "not_reassessed"
    assert result["findings"][0]["verification"] is None


def test_retest_contradicted_by_a_current_observation_requires_review():
    previous = compare(report())
    finding_id = previous["findings"][0]["findingId"]
    result = compare(report(), previous, decisions=[_decision(finding_id)])
    assert result["findings"][0]["state"] == "still_observed"
    assert result["decisions"][0]["status"] == "review_required"
    assert result["decisions"][0]["reviewReasons"] == ["retest_contradicted"]


@pytest.mark.parametrize(
    ("closure", "state", "verification"),
    [
        ("fixed", "remediated_verified", "upstream_fixed"),
        ("dismissed", "closed_upstream", None),
        ("resolved", "closed_upstream", None),
    ],
)
def test_upstream_closure_is_distinct_from_absence(closure, state, verification):
    previous = compare(report())
    finding_id = previous["findings"][0]["findingId"]
    # The source's own closed state applies even when this import is partial.
    current = report(findings=False, upstreamClosed={finding_id: closure})
    current["coverage"][0]["status"] = "partial"
    result = compare(current, previous)
    assert result["findings"][0]["state"] == state
    assert result["findings"][0]["verification"] == verification
    assert result["counts"][state] == 1


def test_an_observed_finding_cannot_also_be_closed_upstream():
    from pydantic import ValidationError

    finding_id = compare(report())["findings"][0]["findingId"]
    with pytest.raises(ValidationError, match="closed upstream"):
        compare(report(upstreamClosed={finding_id: "fixed"}))


def test_refreshed_intelligence_is_attributed_to_new_and_absent_findings():
    def with_feed(revision, **changes):
        payload = report(**changes)
        payload["coverage"][0]["intelligence"] = {"feedRevision": revision}
        return payload

    clean = compare(with_feed("r1", findings=False))
    appeared = compare(with_feed("r2"), clean)
    assert appeared["findings"][0]["state"] == "new"
    assert appeared["findings"][0]["changedIntelligence"] == [
        "inventory-feed:feedRevision"
    ]
    assert appeared["intelligenceChanges"] == ["inventory-feed:feedRevision"]

    gone = compare(with_feed("r3", findings=False), appeared)
    assert gone["findings"][0]["state"] == "absent_comparable"
    assert gone["findings"][0]["changedIntelligence"] == ["inventory-feed:feedRevision"]

    unchanged = compare(with_feed("r1"), compare(with_feed("r1", findings=False)))
    assert unchanged["findings"][0]["changedIntelligence"] == []
    assert unchanged["intelligenceChanges"] == []
    # A tool with no recorded intelligence on one side was not refreshed.
    assert (
        compare(with_feed("r2"), compare(report(findings=False)))["intelligenceChanges"]
        == []
    )


def test_decision_review_records_its_reasons():
    first = compare(report())
    finding_id = first["findings"][0]["findingId"]
    decision = _decision(
        finding_id,
        disposition="accepted_risk",
        retestOutcome=None,
        reviewAt=(NOW + timedelta(hours=2)).isoformat(),
        expiresAt=(NOW + timedelta(hours=4)).isoformat(),
    )
    active = compare(report(), first, decisions=[decision])
    assert active["decisions"][0]["reviewReasons"] == []

    due = compare(report(), active, now=NOW + timedelta(hours=5))
    assert due["decisions"][0]["reviewReasons"] == ["review_due", "expired"]

    pending = compare(report(), due)
    assert pending["decisions"][0]["reviewReasons"] == ["review_pending"]

    changed = report()
    changed["provenance"]["inventory"] = "art_inventory2"
    assert compare(changed, active)["decisions"][0]["reviewReasons"] == [
        "provenance_changed"
    ]

    refreshed = report()
    refreshed["coverage"][0]["intelligence"] = {"feedRevision": "r2"}
    baseline = report()
    baseline["coverage"][0]["intelligence"] = {"feedRevision": "r1"}
    prior = compare(baseline, first, decisions=[decision])
    assert compare(refreshed, prior)["decisions"][0]["reviewReasons"] == [
        "intelligence_changed"
    ]
