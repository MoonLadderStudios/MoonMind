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


def product_evidence():
    """Synthetic assessed-product records for MoonLadderStudios/MoonMind#5193."""
    kinds = (
        "artifact",
        "game_configuration",
        "paytable",
        "feature_settings",
        "critical_file",
        "rng_configuration",
        "external_configuration",
        "database",
    )
    components = [
        {
            "id": kind,
            "kind": kind,
            "identity": f"sha256:{kind}-v1",
            "method": "fixture snapshot signature",
            "evidenceRefs": ["art_component"],
            "omissionReason": None,
        }
        for kind in kinds
    ]
    record = {
        "stage": "approved",
        "product": "app",
        "baselineId": "release-1",
        "status": "available",
        "evidenceRefs": ["art_approval"],
        "reason": "Synthetic approval record; authenticity not established",
        "verifier": "assessed product evidence owner",
    }
    baseline = {
        "schemaVersion": "security-product-baseline/v1",
        "product": "app",
        "baselineId": "release-1",
        "state": "approved",
        "regulatorApprovalRequired": False,
        "components": components,
        "omissions": [],
        "excludedKinds": {},
        "approvedChanges": [],
        "records": [
            {**record, "stage": "submitted"},
            {**record, "stage": "evaluated"},
            {**record, "stage": "certified"},
            record,
        ],
    }
    deployment = {
        "product": "app",
        "deploymentId": "deployment-1",
        "components": copy.deepcopy(components),
        "omissions": [],
    }
    return baseline, deployment


def compare_product(
    baseline, deployment, *, previous=None, baseline_ref="art_baseline"
):
    from moonmind.security.assessments import (
        Observation,
        ProductBaseline,
        compare_observations,
    )

    return compare_observations(
        Observation.model_validate(report(deployment=deployment)),
        previous,
        now=NOW,
        product_baseline=ProductBaseline.model_validate(baseline),
        product_baseline_ref=baseline_ref,
    )


@pytest.mark.parametrize("state", ["submitted", "evaluated", "certified"])
def test_product_digest_match_does_not_approve_submitted_or_evaluated_build(state):
    baseline, deployment = product_evidence()
    baseline["state"] = state
    result = compare_product(baseline, deployment)["productComparison"]
    assert result["status"] == "incomplete"
    assert result["approvalStatus"] == "not_approved"
    assert all(row["status"] == "match" for row in result["components"])
    assert result["baseline"]["state"] == state
    assert "certification" in result["interpretation"]


@pytest.mark.parametrize(
    "kind",
    [
        "artifact",
        "game_configuration",
        "paytable",
        "feature_settings",
        "critical_file",
        "rng_configuration",
        "external_configuration",
        "database",
    ],
)
def test_product_external_change_drifts_with_unchanged_image(kind):
    baseline, deployment = product_evidence()
    next(row for row in deployment["components"] if row["kind"] == kind)[
        "identity"
    ] = "changed"
    result = compare_product(baseline, deployment)["productComparison"]
    rows = {row["id"]: row for row in result["components"]}
    assert result["status"] == "drift"
    assert result["approvalStatus"] == "recorded"
    if kind != "artifact":
        assert rows["artifact"]["status"] == "match"
    assert rows[kind]["status"] == "drift"
    assert rows[kind]["observed"]["identity"] == "changed"
    assert rows[kind]["expected"]["method"] == "fixture snapshot signature"


def test_product_missing_critical_components_or_unrecorded_categories_are_incomplete():
    baseline, deployment = product_evidence()
    deployment["components"].pop()
    deployment["omissions"] = ["External database was inaccessible"]
    result = compare_product(baseline, deployment)["productComparison"]
    assert result["status"] == "incomplete"
    assert result["components"][-1]["status"] == "missing"
    assert "External database was inaccessible" in result["omissions"]
    baseline["components"].pop()
    result = compare_product(baseline, deployment)["productComparison"]
    assert result["status"] == "incomplete"
    assert any("database" in reason for reason in result["reasons"])


@pytest.mark.parametrize(
    "failure",
    [
        "unavailable",
        "unverified",
        "wrong_product",
        "wrong_release",
        "missing",
        "regulator",
    ],
)
def test_product_unavailable_or_inapplicable_approval_remains_unverified(failure):
    baseline, deployment = product_evidence()
    record = baseline["records"][-1]
    if failure in {"unavailable", "unverified"}:
        record["status"] = failure
        record["reason"] = "Private laboratory/regulator records not supplied"
    elif failure == "wrong_product":
        record["product"] = "different-product"
    elif failure == "wrong_release":
        record["baselineId"] = "different-release"
    elif failure == "missing":
        baseline["records"].pop()
    else:
        baseline["regulatorApprovalRequired"] = True
    result = compare_product(baseline, deployment)["productComparison"]
    assert result["status"] == "incomplete"
    assert result["approvalStatus"] == "unverified"
    assert result["reasons"]
    assert result["externalVerification"]["status"] == "Unverified"
    assert result["externalVerification"]["verifiableBy"]


def test_product_explicit_new_baseline_and_approved_change_preserve_history():
    baseline, deployment = product_evidence()
    original = compare_product(baseline, deployment)
    before = copy.deepcopy(original)
    newer = copy.deepcopy(baseline)
    newer["baselineId"] = "release-2"
    for record in newer["records"]:
        record["baselineId"] = "release-2"
    changed = copy.deepcopy(newer["components"][2])
    changed["identity"] = "new-approved-paytable"
    newer["approvedChanges"] = [
        {
            "id": "change-1",
            "component": changed,
            "record": {**newer["records"][-1], "reason": "Explicit paytable approval"},
        }
    ]
    deployment["components"][2] = copy.deepcopy(changed)
    result = compare_product(
        newer, deployment, previous=original, baseline_ref="art_baseline2"
    )["productComparison"]
    assert result["status"] == "match"
    assert result["baselineRef"] == "art_baseline2"
    assert result["baseline"]["baselineId"] == "release-2"
    assert result["components"][2]["approvedChangeId"] == "change-1"
    assert original == before
    assert baseline["components"][2]["identity"] != changed["identity"]
    # Proposed/unapproved changes cannot replace the recorded expected identity.
    newer["approvedChanges"][0]["record"]["stage"] = "submitted"
    rejected = compare_product(newer, deployment)["productComparison"]
    assert rejected["status"] == "incomplete"
    assert rejected["components"][2]["status"] == "drift"
    assert rejected["components"][2]["expected"] == baseline["components"][2]


def test_product_evidence_stays_in_admitted_scope_and_rejects_duplicate_components():
    from pydantic import ValidationError

    from moonmind.security.assessments import Observation

    _, deployment = product_evidence()
    deployment["product"] = "outside-scope"
    with pytest.raises(ValidationError, match="deployment outside assessment scope"):
        Observation.model_validate(report(deployment=deployment))
    deployment["product"] = "app"
    deployment["components"].append(deployment["components"][0])
    with pytest.raises(ValidationError, match="duplicate product component"):
        Observation.model_validate(report(deployment=deployment))


@pytest.mark.parametrize("required", [True, False])
def test_product_explicit_regulator_approval_is_distinct_and_applicable(required):
    baseline, deployment = product_evidence()
    baseline["state"] = "regulator_approved"
    baseline["regulatorApprovalRequired"] = required
    baseline["records"][-1]["stage"] = "regulator_approved"
    result = compare_product(baseline, deployment)["productComparison"]
    assert result["status"] == "match"
    assert result["approvalStatus"] == "recorded"
    assert result["baseline"]["records"][-1]["stage"] == "regulator_approved"


def test_product_new_component_without_identity_is_incomplete():
    baseline, deployment = product_evidence()
    deployment["components"].append(
        {
            "id": "external-feature",
            "kind": "feature_settings",
            "identity": None,
            "method": "database snapshot unavailable",
            "evidenceRefs": [],
            "omissionReason": "Feature settings could not be read",
        }
    )
    result = compare_product(baseline, deployment)["productComparison"]
    assert result["status"] == "incomplete"
    assert result["components"][-1]["status"] == "missing"


def test_product_explicit_nonapplicable_scope_omission_is_reported():
    baseline, deployment = product_evidence()
    baseline["components"].pop()
    deployment["components"].pop()
    baseline["excludedKinds"] = {
        "database": "Product has no separately stored database elements"
    }
    result = compare_product(baseline, deployment)["productComparison"]
    assert result["status"] == "match"
    assert result["omissions"] == [
        "database: Product has no separately stored database elements"
    ]


def test_product_previous_comparison_cannot_silently_select_a_baseline():
    baseline, deployment = product_evidence()
    previous = compare_product(baseline, deployment)
    result = compare(report(deployment=deployment), previous)["productComparison"]
    assert result["baseline"] is None
    assert result["baselineRef"] is None
    assert result["status"] == "incomplete"
    assert result["approvalStatus"] == "unverified"


def test_product_reports_preserve_full_bounded_record_and_scope_reasons():
    baseline, deployment = product_evidence()
    baseline["records"][0].update(
        status="unverified", reason="r" * 1024, verifier="v" * 1024
    )
    baseline["components"].pop()
    deployment["components"].pop()
    baseline["excludedKinds"] = {"database": "d" * 1024}
    result = compare_product(baseline, deployment)["productComparison"]
    assert result["status"] == "incomplete"
    assert "r" * 1024 in result["reasons"][0]
    assert "v" * 1024 in result["reasons"][0]
    assert result["omissions"] == ["database: " + "d" * 1024]


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


def test_invalidated_retest_never_verifies_later_absence():
    previous = compare(report())
    finding_id = previous["findings"][0]["findingId"]
    retest = _decision(finding_id)
    contradicted = compare(report(), previous, decisions=[retest])
    # The contradiction stays with the unchanged decision on every later scan.
    for _ in range(2):
        contradicted = compare(report(findings=False), contradicted)
        assert contradicted["findings"][0]["state"] == "absent_comparable"
        assert contradicted["findings"][0]["verification"] is None
        assert "retest_contradicted" in contradicted["decisions"][0]["reviewReasons"]
    # A replacement retest is new evidence and can verify remediation.
    renewed = _decision(finding_id, evidenceRefs=["art_retest2"])
    result = compare(report(findings=False), contradicted, decisions=[renewed])
    assert result["findings"][0]["state"] == "remediated_verified"

    expired = _decision(finding_id, expiresAt=(NOW - timedelta(hours=1)).isoformat())
    result = compare(report(findings=False), previous, decisions=[expired])
    assert result["findings"][0]["state"] == "absent_comparable"
    assert result["decisions"][0]["reviewReasons"] == ["expired"]


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
