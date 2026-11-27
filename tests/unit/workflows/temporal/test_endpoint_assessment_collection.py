"""The worker rebuilds endpoint results and their summary before publication (#5439)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from moonmind.schemas.container_job_models import ContainerJobActivityRequest
from moonmind.schemas.repository_scanner_models import EndpointAssessment
from moonmind.security.endpoint_assessments import (
    EndpointAssessmentSummary,
    build_endpoint_assessment_job,
)
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend

JOB_ID = "container-job:0123456789abcdef0123456789abcdef"
FIXTURES = Path(__file__).resolve().parents[3] / "fixtures/security/endpoint"
RULE = "xccdf_org.moonmind.fixture_rule_"
ASSESSMENT = EndpointAssessment(
    target="rootfs",
    asset="web-01",
    inputArtifactRef="art_rootfs",
    datastream="ssg-debian12-ds.xml",
    profile="xccdf_org.moonmind.fixture_profile_baseline",
)


def request(state="succeeded") -> ContainerJobActivityRequest:
    return ContainerJobActivityRequest.model_validate(
        {
            "jobId": JOB_ID,
            "ownershipToken": f"{JOB_ID}:v1",
            "terminalState": state,
            "request": {
                "idempotencyKey": "issue-5439",
                "source": {"source": "workflow", "workflowId": "mm:5439"},
                "spec": build_endpoint_assessment_job(ASSESSMENT),
            },
            "containerRef": "moonmind-container-job-x",
        }
    )


def execution(**changes) -> dict:
    return {
        "target": "rootfs",
        "version": "1.3.14",
        "exitCode": 2,
        "state": "succeeded",
        "inputDigest": "a" * 64,
        "contentFile": "ssg-debian12-ds.xml",
        "contentDigest": "b" * 64,
        "contentRelease": "0.1.82",
        "offlineRules": {RULE + "process": "runtime", RULE + "sysctl": "runtime"},
        **changes,
    }


async def collect(
    tmp_path, monkeypatch, *, results: bytes | None, execution_body, state="succeeded"
):
    outputs = tmp_path / "outputs"
    (outputs / "endpoint-assessment").mkdir(parents=True)
    if results is not None:
        (outputs / "endpoint-assessment/results.xml").write_bytes(results)
    if execution_body is not None:
        (outputs / "endpoint-assessment/execution.json").write_text(
            json.dumps(execution_body)
        )
    # A job-written summary is never read: the worker writes its own.
    (outputs / "endpoint-assessment/summary.json").write_text(
        '{"coverage": "complete"}'
    )
    published = {}

    async def publish(_request, name, payload):
        published[name] = payload
        return "art_" + name.rsplit("-", 1)[-1].split(".")[0]

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, evidence_publisher=lambda *a: None
    )
    monkeypatch.setattr(backend, "_output_root", lambda _: outputs)
    monkeypatch.setattr(backend, "_publish_artifact", publish)

    async def no_capture_limits(_):
        return []

    monkeypatch.setattr(backend, "_capture_limitations", no_capture_limits)
    manifest = await backend._collect_declared_outputs(request(state))
    return manifest, published


@pytest.mark.asyncio
async def test_retained_results_and_summary_are_rebuilt_by_the_worker(
    tmp_path, monkeypatch
):
    manifest, published = await collect(
        tmp_path,
        monkeypatch,
        results=(FIXTURES / "weakened-arf.xml").read_bytes(),
        execution_body=execution(),
    )
    assert [(m.name, m.collection_status, m.media_type) for m in manifest] == [
        ("results", "collected", "application/xml"),
        ("execution", "collected", "application/json"),
        ("summary", "collected", "application/json"),
    ]
    results = published[f"{JOB_ID}-output-results.xml"]
    # The native ARF's copied content and system characteristics are dropped.
    assert b"data-stream-collection" not in results and b"oval_results" not in results
    summary = EndpointAssessmentSummary.model_validate_json(
        published[f"{JOB_ID}-output-summary.json"]
    )
    assert summary.native_ref == "art_results"
    assert {r.rule_id for r in summary.rules if r.state == "failed"} == {
        RULE + "apt_unauthenticated",
        RULE + "sources_official",
    }
    assert summary.subject.identity == "rootfs@sha256:" + "a" * 64
    assert summary.coverage == "complete"


@pytest.mark.asyncio
async def test_missing_mismatched_or_interrupted_evidence_is_partial(
    tmp_path, monkeypatch
):
    manifest, published = await collect(
        tmp_path,
        monkeypatch,
        results=b"<not-results/>",
        execution_body=None,
        state="timed_out",
    )
    statuses = {m.name: m.collection_status for m in manifest}
    assert statuses == {
        "results": "rejected",
        "execution": "rejected",
        "summary": "collected",
    }
    summary = EndpointAssessmentSummary.model_validate_json(
        published[f"{JOB_ID}-output-summary.json"]
    )
    assert summary.coverage == "partial"
    assert {
        "container_job_timed_out",
        "missing_or_invalid_execution_evidence",
        "native_results_unavailable",
        "target_identity_unavailable",
    } <= set(summary.limitations)
    # An execution record for different content is not this job's evidence.
    _, published = await collect(
        tmp_path / "other",
        monkeypatch,
        results=(FIXTURES / "hardened-arf.xml").read_bytes(),
        execution_body=execution(contentFile="ssg-ubuntu2204-ds.xml"),
    )
    summary = EndpointAssessmentSummary.model_validate_json(
        published[f"{JOB_ID}-output-summary.json"]
    )
    assert summary.execution is None and summary.coverage == "partial"
