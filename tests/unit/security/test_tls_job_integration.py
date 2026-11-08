"""TLS jobs retain the existing admission and evidence owners."""

import pytest

from moonmind.schemas.container_job_models import ContainerJobSpec
from moonmind.security.tls_assessments import TLSAssessment, build_tls_assessment_job
from moonmind.security.workload_profiles import (
    AdmittedAssessmentScope,
    selection_within_scope,
)


def job():
    return ContainerJobSpec.model_validate(
        build_tls_assessment_job(
            TLSAssessment(
                origin="https://example.com",
                egress_policy_ref="tls",
                assessment_ref="review-one",
            )
        )
    )


def scope(**changes):
    return AdmittedAssessmentScope.model_validate(
        dict(
            assessmentRef="review-one",
            profileRefs=["tls-assessment"],
            actionClasses=["tls-inspection"],
            targets=[
                {
                    "webOrigin": "https://example.com",
                    "webRoutes": [{"path": "/", "methods": ["GET"]}],
                }
            ],
            networkRefs=["tls"],
            maxJobs=2,
            maxJobSeconds=200,
            evidenceIntent="Inspect admitted HTTPS TLS",
            **changes,
        )
    )


def test_tls_job_contract_and_scope():
    spec = job()
    assert spec.tls_assessment.origin == "https://example.com"
    assert selection_within_scope(spec, scope())


@pytest.mark.parametrize(
    "field,value",
    [
        ("assessment_ref", "other"),
        ("profile_refs", ("web-assessment",)),
        ("action_classes", ("web-passive",)),
        ("network_refs", ("other",)),
        ("max_job_seconds", 1),
        ("targets", ()),
    ],
)
def test_tls_scope_denies_unadmitted_authority(field, value):
    assert not selection_within_scope(job(), scope().model_copy(update={field: value}))


@pytest.mark.asyncio
async def test_tls_collection_missing_execution_retains_partial_summary(tmp_path):
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from moonmind.config.container_backend_settings import (
        resolve_container_backend_settings,
    )
    from moonmind.workflows.temporal.container_job_backend import (
        DockerContainerJobBackend,
    )

    published = {}

    async def publish(request, name, data):
        published[name] = json.loads(data)
        return "art_retained"

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        settings=resolve_container_backend_settings({}),
        evidence_publisher=publish,
    )
    request = SimpleNamespace(
        request=SimpleNamespace(spec=job()), job_id="tls-job", terminal_state="failed"
    )
    backend._attachment_storage = lambda r: None
    backend._output_root = lambda r: tmp_path
    backend._runtime_egress_evidence = AsyncMock(
        return_value={
            "snapshotDigest": "sha256:one",
            "resolution": "validated_pinned_job_snapshot",
        }
    )
    manifest = await backend._collect_tls_assessment_outputs(request)
    summary = next(v for k, v in published.items() if "summary" in k)
    assert summary["status"] != "complete"
    assert summary["unperformedChecks"]
    assert summary["resolution"]["snapshotDigest"] == "sha256:one"
    assert any(
        item.name == "execution" and item.collection_status == "missing"
        for item in manifest
    )


def test_tls_report_never_claims_exhaustive_backend_coverage():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from moonmind.security.assessments import AssessmentScope
    from moonmind.security.tls_assessments import TLSAssessmentSummary
    from moonmind.workflows.skills.security_assessment import _tls_observation

    summary = TLSAssessmentSummary(
        origin="https://example.com",
        status="complete",
        evidence_ref="art_evidence",
        settings={},
        execution={},
        comparison_key="sha256:identity",
        findings=[
            {
                "code": "certificate_expired",
                "protocol": "TLSv1.2",
                "cipher": None,
                "evidenceRef": "art_evidence",
            }
        ],
    )
    request = SimpleNamespace(scope=AssessmentScope(assets=["https://example.com"]))
    observation = _tls_observation(request, summary, "art_summary", datetime.now(UTC))
    assert observation.coverage[0].status == "partial"
    assert observation.coverage[0].config_ref == "sha256:identity"
    assert observation.findings[0].rule_id == "certificate_expired"
    assert observation.findings[0].evidence_refs == ["art_evidence"]


@pytest.mark.parametrize(
    "changes",
    [
        {"command": ["openssl", "s_client", "-connect", "other.example:443"]},
        {"networkMode": "none"},
        {"egressPolicyRef": "other"},
        {"environment": [{"name": "OPENSSL_CONF", "value": "/workspace/unsafe.cnf"}]},
        {"inputAttachments": None},
        {"resources": {"cpuMillis": 2000, "memoryMiB": 1024, "pids": 64}},
    ],
)
def test_tls_contract_rejects_caller_transport_and_invocation_changes(changes):
    a = TLSAssessment(origin="https://example.com", egress_policy_ref="tls")
    with pytest.raises(ValueError):
        ContainerJobSpec.model_validate({**build_tls_assessment_job(a), **changes})


def test_private_trust_requires_admitted_artifact():
    from moonmind.security.workload_profiles import AssessmentTarget

    a = TLSAssessment(
        origin="https://example.com",
        egress_policy_ref="tls",
        assessment_ref="review-one",
        trust_artifact_ref="art_ca",
    )
    spec = ContainerJobSpec.model_validate(build_tls_assessment_job(a))
    assert not selection_within_scope(spec, scope())
    admitted = scope().model_copy(
        update={"targets": (*scope().targets, AssessmentTarget(artifactRef="art_ca"))}
    )
    assert selection_within_scope(spec, admitted)
    assert spec.input_attachments[0].artifact_ref == "art_ca"
    assert spec.input_attachments[0].file_name == "ca.pem"


@pytest.mark.parametrize(
    "host,port,protocol,purpose",
    [
        ("example.com", 443, "https", "live-target"),
        ("other.example", 443, "https", "live-target"),
        ("example.com", 8443, "https", "live-target"),
        ("example.com", 443, "http", "live-target"),
        ("example.com", 443, "https", "publication"),
    ],
)
def test_policy_requires_exact_target_not_publication(host, port, protocol, purpose):
    from types import SimpleNamespace

    from moonmind.security.tls_assessments import tls_policy_admits

    policy = SimpleNamespace(
        destinations=[
            SimpleNamespace(host=host, port=port, protocol=protocol, purpose=purpose)
        ]
    )
    a = TLSAssessment(origin="https://example.com", egress_policy_ref="tls")
    assert tls_policy_admits(policy, a) == (
        (host, port, protocol, purpose) == ("example.com", 443, "https", "live-target")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [False, True])
async def test_native_tls_evidence_is_retained_or_rejected_before_cleanup(
    tmp_path, invalid
):
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from moonmind.workflows.temporal.container_job_backend import (
        DockerContainerJobBackend,
        _withholds_console,
    )

    a = job().tls_assessment
    execution = {
        "origin": a.origin,
        "settings": a.model_dump(mode="json", by_alias=True),
        "startedAt": "2026-11-08T00:00:00+00:00",
        "finishedAt": None,
        "tool": {"name": "OpenSSL", "version": None},
        "trust": {},
        "probes": [],
        "errors": ["trust_source_unavailable_or_invalid"],
        "resolution": {"status": "proxy_pinned", "backendAddress": None},
    }
    # System CA bundles exceed the console-tail bound; artifact retention
    # uses its own existing total-byte ceiling.
    from moonmind.security.tls_assessment_runner import _trust_material

    execution["trust"], raw = _trust_material(a, tmp_path)
    import hashlib

    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    cert = x509.load_pem_x509_certificate(raw).public_bytes(serialization.Encoding.PEM)
    bundle = b"\n".join([cert] * 100)
    execution["trust"]["certificatesPem"] = bundle.decode()
    execution["trust"]["sha256"] = hashlib.sha256(bundle).hexdigest()
    assert len(json.dumps(execution)) > 64_000
    if invalid:
        execution["stdout"] = "must not publish raw session secrets"
    out = tmp_path / "tls-assessment"
    out.mkdir()
    (out / "execution.json").write_text(json.dumps(execution))
    (out / "summary.json").write_text('{"status":"complete", "injected":"ignored"}')
    published = {}

    async def publish(request, name, data):
        published[name] = json.loads(data)
        return "art_retained"

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, evidence_publisher=publish
    )
    backend._attachment_storage = lambda r: None
    backend._output_root = lambda r: tmp_path
    backend._runtime_egress_evidence = AsyncMock(
        return_value={
            "snapshotDigest": "sha256:pinned",
            "resolution": "validated_pinned_job_snapshot",
            "coverage": "complete",
        }
    )
    request = SimpleNamespace(
        request=SimpleNamespace(spec=job()), job_id="tls", terminal_state="succeeded"
    )
    manifest = await backend._collect_tls_assessment_outputs(request)
    assert _withholds_console(job())
    summary = published["tls-output-summary.json"]
    assert "injected" not in summary
    assert summary["unperformedChecks"]
    native = next(item for item in manifest if item.name == "execution")
    assert native.collection_status == ("rejected" if invalid else "collected")
    assert ("tls-output-execution.json" in published) is not invalid
    assert "must not publish" not in json.dumps(published)


def test_tls_requires_scoped_capability_not_ordinary_workflow_identity():
    from dataclasses import replace

    from moonmind.schemas.container_job_models import (
        ContainerJobSubmitRequest,
        OwnerIdentity,
    )
    from moonmind.schemas.workspace_locator_models import SandboxWorkspaceLocator
    from moonmind.security.container_job_capabilities import (
        ContainerJobSessionCapability,
        container_job_matches_capability,
    )

    spec = job().model_copy(
        update={
            "workspace_ref": SandboxWorkspaceLocator(
                workspaceId="test", relativePath="."
            )
        }
    )
    request = ContainerJobSubmitRequest.model_validate(
        {
            "idempotencyKey": "tls-test",
            "spec": spec,
            "source": {
                "source": "omnigent",
                "workflowId": "workflow",
                "agentRunId": "run",
                "stepId": "step",
                "omnigentConversationId": "session",
            },
        }
    )
    capability = ContainerJobSessionCapability(
        owner=OwnerIdentity(principalId="operator", principalType="user"),
        agent_run_id="run",
        workflow_id="workflow",
        step_id="step",
        session_id="session",
        runtime_id="codex_cli",
        source_kind="omnigent",
        workspace_kind="sandbox",
        workspace_id="test",
        workspace_relative_path=".",
        workspace_read_only=False,
        expires_at=9999999999,
    )
    assert not container_job_matches_capability(request, capability)
    assert container_job_matches_capability(
        request, replace(capability, assessment_scope=scope())
    )


def test_cli_tls_admission_uses_canonical_job(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from moonmind import cli

    admission = tmp_path / "admission.json"
    admission.write_text(job().tls_assessment.model_dump_json(by_alias=True))
    submissions = []
    monkeypatch.setattr(cli, "run_container_job", lambda spec: submissions.append(spec))
    monkeypatch.setattr(cli, "_print_container_job_result", lambda result: None)
    result = CliRunner().invoke(
        cli.app, ["container", "tls-assessment", "--admission", str(admission)]
    )
    assert result.exit_code == 0, result.output
    assert len(submissions) == 1
    assert (
        ContainerJobSpec.model_validate(submissions[0]).tls_assessment
        == job().tls_assessment
    )
