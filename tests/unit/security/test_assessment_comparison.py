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
