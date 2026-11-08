"""Retained TLS probes flow through the existing assessment artifact owner."""

import pytest

from moonmind.security.tls_assessment_runner import inspect_tls
from moonmind.security.tls_assessments import TLSAssessment, summarize_tls_assessment
from tests.integration.security import test_recurring_assessments as recurring
from tests.unit.security.test_tls_assessment import (
    NOW,
    FixtureProcesses,
    make_certificates,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
environment = recurring.environment


async def retained_tls(service, tmp_path):
    leaf, ca = make_certificates(expired=True)
    trust = tmp_path / "tls-trust"
    trust.mkdir()
    (trust / "ca.pem").write_bytes(ca)
    admission = TLSAssessment(
        origin="https://example.com",
        egress_policy_ref="tls",
        assessment_ref="review-one",
        trust_artifact_ref="art_ca",
        protocols=["TLSv1", "TLSv1.2"],
        max_requests_per_second=10,
    )
    native = inspect_tls(
        admission,
        tmp_path / "output",
        inputs=tmp_path,
        assessed_at=NOW,
        environ={
            "MOONMIND_EGRESS_PROXY": "http://gateway:3128",
            "PATH": "/usr/bin:/bin",
        },
        processes=FixtureProcesses(
            leaf + ca, responses=["no protocols available", "observed"]
        ),
    )
    native_ref = await recurring.put(service, native)
    summary = summarize_tls_assessment(
        admission,
        native,
        native_ref,
        resolution={
            "snapshotDigest": "sha256:fixture",
            "resolution": "validated_pinned_job_snapshot",
            "coverage": "complete",
        },
    )
    summary_ref = await recurring.put(
        service, summary.model_dump(mode="json", by_alias=True)
    )
    request = {
        "scope": {"assets": [admission.origin]},
        "tools": ["retained-report"],
        "reportRefs": [summary_ref],
        "credentialRefs": [],
        "networkRefs": [],
        "budget": {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
    }
    return request, summary, native_ref


async def test_tls_native_evidence_survives_report_and_retry(environment, tmp_path):
    service = environment.service
    request, summary, native_ref = await retained_tls(service, tmp_path)
    first = await recurring.invoke(service, request)
    scan = await recurring.read(service, first["scanRef"])
    assert scan["scanOutcome"] == "incomplete"
    coverage = scan["observation"]["coverage"][0]
    assert coverage["status"] == "partial"
    assert coverage["configRef"] == summary.comparison_key
    assert any(
        f["ruleId"] == "certificate_expired" for f in scan["observation"]["findings"]
    )
    assert native_ref in await recurring.required(service, first["scanRef"])
    _, report = await service.read(
        artifact_id=recurring.report_ref(first),
        access=recurring.access_for_test("workflow:assessment"),
    )
    assert b"unsupported_client" in report
    assert b"TLSv1/default" in report
    assert b"backend/load-balancer" in report
    assert b"CERTIFICATE-----" not in report
    second = await recurring.invoke(service, request, key="retry")
    assert second["scanRef"] == first["scanRef"]


async def test_edited_tls_summary_cannot_replace_native_observations(
    environment, tmp_path
):
    service = environment.service
    request, summary, _ = await retained_tls(service, tmp_path)
    summary.execution["probes"][0]["status"] = "server_refused"
    request["reportRefs"] = [
        await recurring.put(service, summary.model_dump(mode="json", by_alias=True))
    ]
    result = await recurring.invoke(service, request)
    scan = await recurring.read(service, result["scanRef"])
    assert scan["scanOutcome"] == "incomplete"
    assert not scan["observation"]["findings"]
    assert scan["diagnostics"]
    assert not scan["scannerSummaries"]
