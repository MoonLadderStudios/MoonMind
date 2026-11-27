"""Endpoint results through security.assess: findings, objectives, comparisons (#5439)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from moonmind.schemas.repository_scanner_models import EndpointAssessment
from moonmind.security.assessments import (
    AssessmentScope,
    Comparison,
    ControlAssessmentPlan,
    compare_observations,
)
from moonmind.security.control_assessments import assess_controls
from moonmind.security.endpoint_assessments import (
    EndpointExecution,
    parse_results,
    sanitize_results,
    summarize,
)
from moonmind.workflows.skills.security_assessment import (
    _endpoint_entry,
    _endpoint_observation,
    _scanner_summary,
)

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)
ASSET = "web-01"
PROFILE = "xccdf_org.ssgproject.content_profile_standard"
P = "xccdf_org.ssgproject.content_rule_"
REQUEST = SimpleNamespace(scope=AssessmentScope(assets=[ASSET]))


def results(rules: dict[str, str], *, profile=PROFILE, end="2026-11-27T11:00:00+00:00"):
    rows = "".join(
        f"<rule-result idref='{P}{rule}' severity='high'><result>{value}</result></rule-result>"
        for rule, value in rules.items()
    )
    return sanitize_results(
        (
            "<TestResult xmlns='http://checklists.nist.gov/xccdf/1.2' version='0.1.82' "
            f"end-time='{end}'><benchmark id='xccdf_org.ssgproject.content_benchmark_DEBIAN-12'/>"
            f"<profile idref='{profile}'/>{rows}</TestResult>"
        ).encode(),
        1 << 20,
        keep_target=False,
    )


def endpoint_summary(
    rules,
    *,
    digest="b" * 64,
    subject="a" * 64,
    profile=PROFILE,
    offline=None,
    required=(),
):
    assessment = EndpointAssessment(
        target="rootfs",
        asset=ASSET,
        inputArtifactRef="art_rootfs",
        datastream="ssg-debian12-ds.xml",
        profile=profile,
        ruleIds=[P + rule for rule in required],
    )
    execution = EndpointExecution(
        target="rootfs",
        version="1.3.7",
        exitCode=2,
        state="succeeded",
        inputDigest=subject,
        contentFile="ssg-debian12-ds.xml",
        contentDigest=digest,
        contentRelease="0.1.82",
        offlineRules={P + rule: kind for rule, kind in (offline or {}).items()},
    )
    return summarize(
        assessment,
        execution,
        parse_results(results(rules, profile=profile), 1 << 20),
        native_ref="art_native",
    )


def observe(summary, created=NOW):
    return _endpoint_observation(REQUEST, summary, "art_summary", created)


def objective(summary, rule_ids, *, created=NOW, now=NOW):
    observed = observe(summary, created)
    plan = ControlAssessmentPlan.model_validate(
        {
            "requirements": [
                {
                    "id": "EP-1",
                    "frameworkId": "endpoint-best-practices",
                    "frameworkEdition": "2026-11",
                    "title": "Remote root login is refused",
                    "scope": {"assets": [ASSET]},
                    "period": {
                        "start": now.date().isoformat(),
                        "end": now.date().isoformat(),
                    },
                    "applicability": "applicable",
                    "applicabilityReason": "A Linux endpoint in the plan",
                    "objectivesComplete": True,
                    "objectiveBasis": "Defined by the named rules",
                    "objectives": [
                        {
                            "id": "rules",
                            "source": "endpoint",
                            "tool": "openscap",
                            "ruleIds": [P + rule for rule in rule_ids],
                            "description": "Named ComplianceAsCode rules",
                        }
                    ],
                }
            ]
        }
    )
    compared = Comparison.model_validate(compare_observations(observed, None, now=now))
    assessed = assess_controls(
        plan,
        compared,
        scan_ref="art_scan",
        comparison_ref="art_comparison",
        now=now,
        evidence_availability={},
        coverage_windows=[
            {
                "observedAt": observed.observed_at.isoformat(),
                "expiresAt": observed.expires_at.isoformat(),
            }
            for _ in observed.coverage
        ],
    )
    return assessed.requirements[0]


def test_fail_results_are_findings_and_pass_fail_rules_are_complete_coverage():
    summary = endpoint_summary(
        {
            "sshd_disable_root_login": "fail",
            "package_nis_removed": "pass",
            "partition_for_tmp": "notapplicable",
        }
    )
    observed = observe(summary)
    assert [
        (f.source, f.tool, f.rule_id, f.asset, f.severity) for f in observed.findings
    ] == [("endpoint", "openscap", P + "sshd_disable_root_login", ASSET, "high")]
    assert observed.findings[0].evidence_refs == ["art_native"]
    (row,) = observed.coverage
    assert row.evidence_method == "deterministic_check" and row.status == "complete"
    assert set(row.rule_ids) == {
        P + "sshd_disable_root_login",
        P + "package_nis_removed",
    }
    assert observed.provenance["endpointSubject"] == "rootfs@sha256:" + "a" * 64
    assert "not MoonMind's own observation" in observed.provenance["endpointStatement"]
    assert observed.complete(NOW)


def test_named_rules_are_satisfied_only_by_complete_current_in_scope_passes():
    passing = endpoint_summary(
        {"sshd_disable_root_login": "pass", "sshd_disable_empty_passwords": "pass"}
    )
    met = objective(
        passing, ["sshd_disable_root_login", "sshd_disable_empty_passwords"]
    )
    assert met.objectives[0].outcome == "passed"
    assert met.conclusion == "satisfied"
    failing = endpoint_summary(
        {"sshd_disable_root_login": "fail", "sshd_disable_empty_passwords": "pass"}
    )
    assert objective(failing, ["sshd_disable_root_login"]).conclusion == "not_satisfied"
    # Stale evidence, a coverage state, or an offline-unanswerable rule never passes.
    assert (
        objective(
            passing, ["sshd_disable_root_login"], now=NOW + timedelta(days=3)
        ).conclusion
        != "satisfied"
    )
    for rules, offline in (
        ({"sshd_disable_root_login": "notapplicable"}, None),
        ({"sshd_disable_root_login": "notchecked"}, None),
        ({"sshd_disable_root_login": "notselected"}, None),
        ({"sshd_disable_root_login": "pass"}, {"sshd_disable_root_login": "metadata"}),
    ):
        unproven = objective(
            endpoint_summary(rules, offline=offline), ["sshd_disable_root_login"]
        )
        assert unproven.conclusion != "satisfied"
        assert unproven.objectives[0].coverage != "complete"


def test_errors_and_unselected_required_rules_are_partial_never_clean():
    summary = endpoint_summary(
        {"sshd_disable_root_login": "pass", "package_nis_removed": "error"},
        required=["sshd_disable_root_login", "accounts_maximum_age_login_defs"],
    )
    observed = observe(summary)
    complete, partial = observed.coverage
    assert complete.status == "complete" and complete.rule_ids == [
        P + "sshd_disable_root_login"
    ]
    assert partial.status == "partial"
    assert set(partial.rule_ids) == {
        P + "package_nis_removed",
        P + "accounts_maximum_age_login_defs",
    }
    assert not observed.complete(NOW)
    # A rule that passed keeps its own evidence; the errored one is inconclusive.
    assert (
        objective(summary, ["sshd_disable_root_login"]).objectives[0].outcome
        == "passed"
    )
    assert objective(summary, ["package_nis_removed"]).conclusion != "satisfied"


def test_endpoint_outside_the_admitted_scope_is_refused():
    with pytest.raises(ValueError, match="outside"):
        _endpoint_observation(
            SimpleNamespace(scope=AssessmentScope(assets=["other"])),
            endpoint_summary({"sshd_disable_root_login": "pass"}),
            "art_summary",
            NOW,
        )


def compare(previous_summary, current_summary):
    previous = compare_observations(observe(previous_summary), None, now=NOW)
    return Comparison.model_validate(
        compare_observations(
            observe(current_summary), previous, now=NOW + timedelta(hours=1)
        )
    )


def test_changed_content_is_an_intelligence_change_not_a_changed_endpoint():
    before = endpoint_summary({"sshd_disable_root_login": "fail"})
    after = endpoint_summary({"sshd_disable_root_login": "pass"}, digest="c" * 64)
    compared = compare(before, after)
    assert "openscap:content" in compared.intelligence_changes
    (row,) = compared.findings
    assert row.state == "absent_comparable"
    assert "openscap:content" in row.changed_intelligence
    # The same content on a changed copy: no intelligence change, only a new subject.
    moved = compare(
        before, endpoint_summary({"sshd_disable_root_login": "pass"}, subject="d" * 64)
    )
    assert moved.intelligence_changes == []
    assert moved.findings[0].state == "absent_comparable"
    assert moved.observation.provenance["endpointSubject"].endswith("d" * 64)


def test_a_changed_profile_is_reported_and_absence_is_not_compared_across_it():
    before = endpoint_summary({"sshd_disable_root_login": "fail"})
    other = "xccdf_org.ssgproject.content_profile_cis_level1_server"
    compared = compare(
        before, endpoint_summary({"sshd_disable_root_login": "pass"}, profile=other)
    )
    assert "openscap:profile" in compared.intelligence_changes
    assert compared.findings[0].state == "not_reassessed"


def test_imported_results_are_observed_as_of_their_own_run():
    assessment = EndpointAssessment(
        target="results", asset=ASSET, inputArtifactRef="art_arf"
    )
    summary = summarize(
        assessment,
        EndpointExecution(
            target="results", version="not_run", state="succeeded", inputDigest="e" * 64
        ),
        parse_results(
            results(
                {"sshd_disable_root_login": "pass"}, end="2026-11-20T08:00:00+00:00"
            ),
            1 << 20,
        ),
        native_ref="art_native",
    )
    observed = observe(summary)
    assert observed.observed_at == datetime(2026, 11, 20, 8, tzinfo=UTC)
    assert observed.coverage[0].intelligence["origin"] == "operator-supplied"
    assert observed.provenance["endpointObservation"] == "operator-supplied"
    # A week-old device result is not current evidence today.
    assert objective(summary, ["sshd_disable_root_login"]).conclusion != "satisfied"


def test_the_retained_report_lists_per_rule_evidence():
    summary = endpoint_summary(
        {
            "sshd_disable_root_login": "fail",
            "package_nis_removed": "pass",
            "partition_for_tmp": "pass",
            "x_unselected": "notselected",
        },
        offline={"partition_for_tmp": "runtime"},
    )
    entry = _endpoint_entry(summary, "art_summary", observe(summary))
    text = _scanner_summary([entry])
    assert f"`{P}sshd_disable_root_login`: fail (failed)." in text
    assert (
        f"`{P}partition_for_tmp`: pass (not_assessed). The check reads the running system"
        in text
    )
    assert "x_unselected" not in text
    assert "Unsupported coverage: windows" in text
    assert "not MoonMind's own observation" in text
