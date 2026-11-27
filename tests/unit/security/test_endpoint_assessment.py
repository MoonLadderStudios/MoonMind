"""Offline OpenSCAP endpoint jobs: contract, results and summary (#5439).

``tests/fixtures/security/endpoint`` holds native output of real OpenSCAP
1.3.14 evaluating ``fixture-xccdf.xml``/``fixture-oval.xml`` (a synthetic
benchmark) against the hardened and weakened qualification root filesystems.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from pydantic import ValidationError

from moonmind.schemas.container_job_models import ContainerJobSpec
from moonmind.schemas.repository_scanner_models import EndpointAssessment
from moonmind.security.assessment_preset_inputs import (
    assessment_job_spec,
    validate_assessment_input,
)
from moonmind.security.endpoint_assessments import (
    OBSERVATION_STATEMENTS,
    REASONS,
    EndpointExecution,
    EndpointResultsError,
    aligned_with,
    build_endpoint_assessment_job,
    parse_results,
    sanitize_results,
    summarize,
    verify_summary,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/security/endpoint"
LIMIT = 32 * 1024 * 1024
PROFILE = "xccdf_org.moonmind.fixture_profile_baseline"
RULE = "xccdf_org.moonmind.fixture_rule_"
EVALUATED = {
    "target": "rootfs",
    "asset": "web-01",
    "inputArtifactRef": "art_rootfs",
    "datastream": "ssg-debian12-ds.xml",
    "profile": PROFILE,
}


def admission(**changes):
    return EndpointAssessment.model_validate({**EVALUATED, **changes})


def execution(**changes):
    return EndpointExecution.model_validate(
        {
            "target": "rootfs",
            "version": "1.3.14",
            "exitCode": 2,
            "state": "succeeded",
            "inputDigest": "a" * 64,
            "contentFile": "ssg-debian12-ds.xml",
            "contentDigest": "b" * 64,
            "contentRelease": "0.1.82",
            "offlineRules": {
                RULE + "process": "runtime",
                RULE + "sysctl": "runtime",
                RULE + "shadow_owner": "metadata",
            },
            **changes,
        }
    )


def native(name="hardened-arf.xml"):
    return parse_results((FIXTURES / name).read_bytes(), LIMIT)


def test_rootfs_and_image_name_shipped_content_and_imports_do_not():
    assert admission().target == "rootfs"
    assert admission(target="image").datastream == "ssg-debian12-ds.xml"
    imported = admission(target="results", datastream=None)
    assert imported.profile == PROFILE
    for broken in (
        {"datastream": None},
        {"profile": None},
        {"target": "results"},
        {"target": "results", "datastream": None, "tailoringArtifactRef": "art_t"},
        {"datastream": "../ssg-debian12-ds.xml"},
        {"asset": "-bad"},
        {"ruleIds": [RULE + "a", RULE + "a"]},
        {"tailoringArtifactRef": "art_rootfs"},
    ):
        with pytest.raises(ValidationError):
            admission(**broken)


@pytest.mark.parametrize(
    "key", ["remediate", "remediation", "fixType", "generateFix", "command", "script"]
)
def test_the_contract_refuses_remediation_fixes_and_commands(key):
    with pytest.raises(ValidationError, match="read-only"):
        EndpointAssessment.model_validate({**EVALUATED, key: True})
    with pytest.raises(ValueError):
        validate_assessment_input("endpoint-assessment", {**EVALUATED, key: "x"})


def test_job_runs_offline_without_socket_privilege_or_extra_authority():
    spec = ContainerJobSpec.model_validate(
        build_endpoint_assessment_job(admission(tailoringArtifactRef="art_tail"))
    )
    assert spec.network_mode == "none"
    assert spec.egress_policy_ref is None
    assert spec.environment == []
    assert spec.registry_credential_ref is None
    assert spec.service_ref is None
    assert spec.image_source_ref == "repository-security"
    assert [(a.name, a.file_name) for a in spec.input_attachments] == [
        ("endpoint", "rootfs.tar"),
        ("tailoring", "tailoring.xml"),
    ]
    assert spec.command[:3] == [
        "-I",
        "-m",
        "moonmind.security.endpoint_assessment_runner",
    ]
    assert not any("remediat" in part or "--fix" in part for part in spec.command)
    assert [o.relative_path for o in spec.outputs] == [
        "endpoint-assessment/results.xml",
        "endpoint-assessment/execution.json",
        "endpoint-assessment/summary.json",
    ]
    assert (
        assessment_job_spec(
            "endpoint-assessment", admission().model_dump(by_alias=True)
        )["networkMode"]
        == "none"
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda job: job.update(networkMode="bridge"),
        lambda job: job.update(command=[*job["command"], "--remediate"]),
        lambda job: job.update(entrypoint=["sh"]),
        lambda job: job.update(
            environment=[{"name": "OSCAP_PROBE_ROOT", "value": "/"}]
        ),
        lambda job: job.update(
            caches=[{"cacheRef": "docker", "target": "/var/run", "readOnly": True}]
        ),
        lambda job: job["inputAttachments"].append(
            {"name": "extra", "workspacePath": ".", "includeGit": False, "maxBytes": 1}
        ),
        lambda job: job.update(image="evil/oscap:latest", imageSourceRef=None),
        lambda job: job.update(
            imageAssessment={"operation": "sbom-generate", "target": "source"}
        ),
    ],
)
def test_any_change_to_the_canonical_workload_is_refused(change):
    job = copy.deepcopy(build_endpoint_assessment_job(admission()))
    change(job)
    with pytest.raises(ValidationError):
        ContainerJobSpec.model_validate(job)


def test_arf_and_xccdf_results_parse_to_the_same_rule_outcomes():
    arf, xccdf = native(), native("hardened-xccdf-results.xml")
    assert arf.benchmark_id == "xccdf_org.moonmind.fixture_benchmark_ENDPOINT"
    assert (arf.benchmark_version, arf.profile_id) == ("1.0", PROFILE)
    assert arf.end_time is not None and arf.end_time.tzinfo is not None
    outcomes = {rule.rule_id: rule.result for rule in arf.rules}
    assert outcomes == {rule.rule_id: rule.result for rule in xccdf.rules}
    assert outcomes[RULE + "apt_unauthenticated"] == "pass"
    assert outcomes[RULE + "never_applicable"] == "notapplicable"
    assert outcomes[RULE + "unselected"] == "notselected"
    assert outcomes[RULE + "manual"] == "notchecked"


def test_retained_results_drop_copied_content_system_facts_and_scanner_target():
    body = (FIXTURES / "hardened-arf.xml").read_bytes()
    assert b"data-stream-collection" in body and b"oval_results" in body
    safe = sanitize_results(body, LIMIT, keep_target=False)
    for dropped in (
        b"data-stream-collection",
        b"oval_results",
        b"target-facts",
        b"<target>",
        b"asset",
    ):
        assert dropped not in safe
    assert parse_results(safe, LIMIT) == native().model_copy(update={"target": None})
    assert b"<target>unknown</target>" in sanitize_results(
        body, LIMIT, keep_target=True
    )
    # Sanitizing again is stable, so the worker's rebuild equals the job's.
    assert sanitize_results(safe, LIMIT, keep_target=False) == safe


@pytest.mark.parametrize(
    "body,code",
    [
        (b'<!DOCTYPE x [<!ENTITY a "b">]><TestResult/>', "results_malformed"),
        (b"<TestResult", "results_malformed"),
        (
            "<?xml version='1.0' encoding='UTF-16'?><a/>".encode("utf-16"),
            "results_malformed",
        ),
        (
            b"<Benchmark xmlns='http://checklists.nist.gov/xccdf/1.2'/>",
            "results_without_test_result",
        ),
        (
            b"<a xmlns='http://checklists.nist.gov/xccdf/1.2'><TestResult/><TestResult/></a>",
            "results_ambiguous",
        ),
        (b"x" * 2048, "results_exceed_bound"),
    ],
)
def test_unsafe_ambiguous_or_oversized_results_are_refused(body, code):
    with pytest.raises(EndpointResultsError) as raised:
        parse_results(body, 1024 if code == "results_exceed_bound" else LIMIT)
    assert raised.value.code == code


def test_hardened_and_weakened_fixtures_differ_on_the_expected_rules():
    hardened = summarize(admission(), execution(), native(), native_ref="art_h")
    weakened = summarize(
        admission(), execution(), native("weakened-arf.xml"), native_ref="art_w"
    )
    states = lambda summary: {r.rule_id: r.state for r in summary.rules}  # noqa: E731
    changed = {
        rule
        for rule, state in states(hardened).items()
        if state != states(weakened)[rule]
    }
    assert changed == {RULE + "apt_unauthenticated", RULE + "sources_official"}
    assert states(hardened)[RULE + "apt_unauthenticated"] == "passed"
    assert states(weakened)[RULE + "apt_unauthenticated"] == "failed"
    assert hardened.subject.identity == "rootfs@sha256:" + "a" * 64
    assert hardened.subject.observation == "offline-copy"
    assert hardened.subject.statement == OBSERVATION_STATEMENTS["offline-copy"]
    content = hardened.content
    assert (content.profile_id, content.content_digest, content.content_release) == (
        PROFILE,
        "b" * 64,
        "0.1.82",
    )
    assert content.tailoring.state == "none"


def test_other_results_and_offline_unanswerable_rules_are_coverage_states():
    summary = summarize(admission(), execution(), native(), native_ref="art_h")
    rows = {r.rule_id.removeprefix(RULE): r for r in summary.rules}
    # OpenSCAP's offline "fail" for process and kernel checks is not a finding.
    assert rows["process"].result == "fail" and rows["process"].state == "not_assessed"
    assert "running system" in rows["process"].reason
    assert rows["shadow_owner"].result == "pass"
    assert rows["shadow_owner"].state == "not_assessed"
    assert "ownership" in rows["shadow_owner"].reason
    for name, result in (
        ("never_applicable", "notapplicable"),
        ("manual", "notchecked"),
        ("unselected", "notselected"),
    ):
        assert rows[name].state == "not_assessed"
        assert rows[name].reason == REASONS[result]
    assert summary.platform == "partially_applicable"
    # Rules outside the profile, and declared offline limits, do not make it partial.
    assert summary.coverage == "complete" and summary.limitations == []


def _test_result(*results):
    rows = "".join(
        f"<rule-result idref='{RULE}r{index}'><result>{value}</result></rule-result>"
        for index, value in enumerate(results)
    )
    return (
        "<TestResult xmlns='http://checklists.nist.gov/xccdf/1.2' version='1' "
        "end-time='2026-11-20T10:00:00+00:00'>"
        "<benchmark id='xccdf_org.ssgproject.content_benchmark_DEBIAN-12'/>"
        f"<profile idref='{PROFILE}'/><target>web-01.internal</target>{rows}"
        "</TestResult>"
    ).encode()


@pytest.mark.parametrize(
    "result",
    [
        "error",
        "unknown",
        "notapplicable",
        "notchecked",
        "notselected",
        "informational",
        "fixed",
    ],
)
def test_every_non_pass_fail_result_is_never_a_pass(result):
    summary = summarize(
        admission(),
        execution(offlineRules={}),
        parse_results(_test_result("pass", result), LIMIT),
        native_ref="art",
    )
    row = summary.rules[1]
    assert (row.result, row.state, row.reason) == (
        result,
        "not_assessed",
        REASONS[result],
    )
    assert summary.counts == {"pass": 1, result: 1}


@pytest.mark.parametrize("result", ["error", "unknown"])
def test_errors_leave_the_run_partial(result):
    summary = summarize(
        admission(),
        execution(offlineRules={}),
        parse_results(_test_result("pass", result), LIMIT),
        native_ref="art",
    )
    assert summary.coverage == "partial"
    assert "rule_check_errors" in summary.limitations


def test_required_rules_left_unselected_leave_the_run_partial():
    body = _test_result("pass", "notselected")
    complete = summarize(
        admission(ruleIds=[RULE + "r0"]),
        execution(offlineRules={}),
        parse_results(body, LIMIT),
        native_ref="art",
    )
    assert complete.coverage == "complete"
    partial = summarize(
        admission(ruleIds=[RULE + "r0", RULE + "r1"]),
        execution(offlineRules={}),
        parse_results(body, LIMIT),
        native_ref="art",
    )
    assert partial.coverage == "partial"
    assert "required_rules_not_evaluated" in partial.limitations


def test_content_that_does_not_apply_or_a_failed_run_is_never_clean():
    inapplicable = summarize(
        admission(),
        execution(offlineRules={}),
        parse_results(_test_result("notapplicable", "notapplicable"), LIMIT),
        native_ref="art",
    )
    assert inapplicable.platform == "not_applicable"
    assert "content_not_applicable" in inapplicable.limitations
    failed = summarize(admission(), execution(state="timed_out"), None, native_ref=None)
    assert failed.coverage == "partial"
    assert {"scanner_timed_out", "native_results_unavailable"} <= set(
        failed.limitations
    )
    missing = summarize(admission(), None, native(), native_ref="art")
    assert missing.subject is None and missing.coverage == "partial"


def test_imported_results_have_the_same_shape_marked_operator_supplied():
    imported = admission(target="results", datastream=None, inputArtifactRef="art_arf")
    body = sanitize_results(_test_result("pass", "fail"), LIMIT, keep_target=True)
    summary = summarize(
        imported,
        EndpointExecution(
            target="results", version="not_run", state="succeeded", inputDigest="c" * 64
        ),
        parse_results(body, LIMIT),
        native_ref="art_native",
    )
    assert summary.subject.identity == "openscap-results@sha256:" + "c" * 64
    assert summary.subject.observation == "operator-supplied"
    assert "did not run the evaluation" in summary.subject.statement
    assert summary.subject.declared_target == "web-01.internal"
    assert summary.content.tailoring.state == "not_recorded"
    assert summary.content.family == "ComplianceAsCode"
    assert summary.observed_at.isoformat() == "2026-11-20T10:00:00+00:00"
    # The content digest is not in the result; the run is still complete.
    assert summary.limitations == ["content_digest_not_recorded"]
    assert summary.coverage == "complete"
    assert [r.state for r in summary.rules] == ["passed", "failed"]
    other = summarize(
        imported.model_copy(update={"profile": "xccdf_org.x_profile_other"}),
        summary.execution,
        parse_results(body, LIMIT),
        native_ref="art_native",
    )
    assert "profile_differs_from_admission" in other.limitations


def test_alignment_names_the_benchmark_without_claiming_it():
    assert (
        aligned_with("xccdf_org.ssgproject.content_profile_cis_level1_server")
        == "CIS Benchmark"
    )
    assert aligned_with("xccdf_org.ssgproject.content_profile_stig") == "DISA STIG"
    assert aligned_with("xccdf_org.ssgproject.content_profile_standard") is None
    summary = summarize(admission(), execution(), native(), native_ref="art")
    assert "never as that benchmark" in summary.content_rights
    assert set(summary.unsupported_platforms) >= {"windows", "macos"}


def test_a_summary_must_match_its_retained_native_result():
    safe = sanitize_results(
        (FIXTURES / "weakened-arf.xml").read_bytes(), LIMIT, keep_target=False
    )
    summary = summarize(
        admission(), execution(), parse_results(safe, LIMIT), native_ref="art"
    )
    assert verify_summary(summary, safe) == summary
    forged = summary.model_copy(
        update={
            "rules": [
                r.model_copy(update={"state": "passed"}) if r.state == "failed" else r
                for r in summary.rules
            ]
        }
    )
    with pytest.raises(ValueError, match="differs"):
        verify_summary(forged, safe)
    # A partial run cannot be restated as complete by dropping its limitations.
    errors_body = sanitize_results(_test_result("error"), LIMIT, keep_target=False)
    errors = summarize(
        admission(),
        execution(offlineRules={}),
        parse_results(errors_body, LIMIT),
        native_ref="art",
    )
    assert errors.coverage == "partial"
    with pytest.raises(ValueError, match="differs"):
        verify_summary(
            errors.model_copy(update={"coverage": "complete", "limitations": []}),
            errors_body,
        )
