"""Retained access evidence is re-evaluated and scoped, never trusted prose."""

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from moonmind.security.application_access import (
    ApplicationAccessBundle,
    assess_application_access,
    bind_job_result,
    spec_digest,
)
from moonmind.security.assessments import AssessmentScope, content_key
from moonmind.security.target_assessments import TargetScope
from moonmind.workflows.skills import security_assessment as retained
from tests.unit.security.test_application_access import observation, spec


def evidence(*, terminal="succeeded", payload=None):
    selected = spec()
    scope = TargetScope.model_validate(
        {
            "web": {
                "origin": "https://app.example",
                "mode": "baseline",
                "routes": [{"path": "/", "methods": ["GET"]}],
                "startPaths": ["/identity"],
                "egressPolicyRef": "app",
            },
            "credentialSlots": {"slot://owner": "owner"},
        }
    )
    bundle = ApplicationAccessBundle(
        spec_digest=spec_digest(selected),
        observations=[
            observation(
                selected, {"error": "forbidden"} if payload is None else payload
            )
        ],
    )
    raw_spec = selected.model_dump_json(by_alias=True).encode()
    raw_capture = json.dumps(
        {
            "scope": scope.model_dump(mode="json", by_alias=True),
            "scopeDigest": content_key(scope.model_dump(mode="json")),
            "maxSeconds": 60,
            "startedAt": 1.0,
            "nextRequestAt": 1.0,
            "requestsSpent": 3,
            "bundle": bundle.model_dump(mode="json", by_alias=True),
            "progress": [{"caseRef": "read", "phase": "complete"}],
            "status": "complete",
        }
    ).encode()
    report = assess_application_access(selected, bundle)
    report.input_digests = {
        "spec": hashlib.sha256(raw_spec).hexdigest(),
        "capture": hashlib.sha256(raw_capture).hexdigest(),
    }
    report = type(report).model_validate_json(
        bind_job_result(
            report.model_dump_json(by_alias=True).encode(),
            terminal,
            0 if terminal == "succeeded" else None,
            {"spec": "art_spec", "capture": "art_capture"},
        )
    )
    return selected, report, raw_spec, raw_capture


def test_retained_report_checks_raw_inputs_and_recomputes_case_decisions():
    selected, report, raw_spec, raw_capture = evidence()
    verified_spec, capture = retained._verify_application_access_report(
        report, raw_spec, raw_capture
    )
    assert verified_spec == selected
    assert capture.scope.web.origin == "https://app.example"
    with pytest.raises(ValueError):
        retained._verify_application_access_report(report, raw_spec + b" ", raw_capture)
    altered = report.model_copy(deep=True)
    altered.cases[0].authentication_state = "unverified"
    with pytest.raises(ValueError):
        retained._verify_application_access_report(altered, raw_spec, raw_capture)


def test_retained_failed_job_and_target_scope_cannot_be_overclaimed():
    selected, report, raw_spec, raw_capture = evidence(terminal="failed")
    _, capture = retained._verify_application_access_report(
        report, raw_spec, raw_capture
    )
    request = SimpleNamespace(scope=AssessmentScope(assets=[capture.scope.web.origin]))
    observed = retained._application_access_observation(
        request, report, selected, capture, "art_report", datetime.now(UTC)
    )
    assert observed.coverage[0].status == "partial"
    request.scope = AssessmentScope(assets=["https://other.example"])
    with pytest.raises(ValueError):
        retained._application_access_observation(
            request, report, selected, capture, "art_report", datetime.now(UTC)
        )


def test_access_finding_and_coverage_bind_case_identity_and_scope():
    selected, report, raw_spec, raw_capture = evidence(
        payload={"record": {"id": "record"}}
    )
    _, capture = retained._verify_application_access_report(
        report, raw_spec, raw_capture
    )
    request = SimpleNamespace(scope=AssessmentScope(assets=[capture.scope.web.origin]))
    result = retained._application_access_observation(
        request, report, selected, capture, "art_report", datetime.now(UTC)
    )
    assert len(result.findings) == 1
    assert result.findings[0].rule_id == "read"
    assert result.findings[0].evidence_refs == ["art_report", "art_spec", "art_capture"]
    assert result.coverage[0].evidence_method == "deterministic_check"
    original = result.coverage[0].config_ref
    capture.scope_digest = "f" * 64
    changed = retained._application_access_observation(
        request, report, selected, capture, "art_report", datetime.now(UTC)
    )
    assert changed.coverage[0].config_ref != original
