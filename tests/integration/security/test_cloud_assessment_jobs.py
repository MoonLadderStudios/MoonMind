"""Scoped Prowler cloud-posture assessments through the production job path (#5043).

The operator scope admits one AWS account, its regions, the posture action
class, an egress policy of provider API endpoints and a ``cloud_inventory``
grant. Admission, credential authorization, STS exchange with the production
boto3 adapter, credential delivery, launch arguments, output collection, the
summary adapter, artifact storage, ``security.assess`` and attachment cleanup
are production code. The provider API is a local transport fixture that
answers AssumeRole and GetCallerIdentity and records every call; Prowler is a
stand-in at the binary boundary (``cloud_assessment_fake_prowler``) that
authenticates with the delivered session file through boto3 and writes
recorded Prowler 5.46 output.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import pytest

from pydantic import ValidationError

from api_service.services.container_jobs import (
    ContainerJobScopeDeniedError,
    ContainerJobService,
    owner_artifact_principal,
)
from api_service.services.registry_authorization import (
    PrivateImageAuthorizationPolicy,
    PrivateImageAuthorizationService,
)
from moonmind.config.container_backend_settings import (
    EGRESS_POLICIES_ENV_KEY,
    IMAGE_SOURCES_ENV_KEY,
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobSubmitRequest,
    ImageObservation,
    OwnerIdentity,
)
from moonmind.schemas.repository_scanner_models import CloudAssessment
from moonmind.security.cloud_assessments import (
    UNADMITTED_OPTIONS,
    build_cloud_assessment_job,
)
from moonmind.security.container_job_capabilities import ContainerJobSessionCapability
from moonmind.security.workload_profiles import (
    WORKLOAD_CREDENTIALS_TARGET,
    AdmittedAssessmentScope,
)
from moonmind.workflows.temporal import container_job_attachments as attachments
from moonmind.workflows.temporal.artifacts import ArtifactAccessContext
from moonmind.workflows.temporal.container_job_attachments import storage_for
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from moonmind.workflows.temporal.worker_runtime import _container_job_artifact_reader
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security.test_recurring_assessments import invoke

environment = recurring.environment

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
OWNER = OwnerIdentity(principalType="user", principalId="operator")
FAKE_PROWLER = Path(__file__).with_name("cloud_assessment_fake_prowler.py")
ACCOUNT = "123456789012"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/audit"
IMAGE = "sha256:" + "c" * 64
SOURCE = {"accessKeyId": "AKIASEEDSOURCE0001", "secretAccessKey": "seed-source-secret"}
ISSUED = ("ASIASEEDSESSION0001", "seed-session-secret", "seed-session-token")
SEEDS = (*SOURCE.values(), *ISSUED)
CHECKS = [
    "s3_bucket_level_public_access_block",
    "s3_bucket_object_versioning",
    "iam_password_policy_minimum_length_14",
    "ec2_ebs_default_encryption",
]
READ_ONLY = {"AssumeRole", "GetCallerIdentity"}


class ProviderApi(BaseHTTPRequestHandler):
    """Controlled AWS query transport: STS answers, every call recorded."""

    calls: list[dict] = []
    expired = False

    def do_POST(self):  # noqa: N802 - http.server API
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        form = parse_qs(body)
        action = form["Action"][0]
        key = re.search(r"Credential=([A-Z0-9]+)/", self.headers["Authorization"])
        type(self).calls.append(
            {"action": action, "key": key.group(1) if key else "", "form": form}
        )
        if action == "GetCallerIdentity" and type(self).expired:
            status, reply = 403, (
                "<ErrorResponse><Error><Type>Sender</Type><Code>ExpiredToken"
                "</Code><Message>The security token included in the request is "
                "expired</Message></Error><RequestId>1</RequestId></ErrorResponse>"
            )
        elif action == "GetCallerIdentity":
            status, reply = 200, (
                "<GetCallerIdentityResponse><GetCallerIdentityResult>"
                f"<Arn>{ROLE}/moonmind</Arn><UserId>AROA:moonmind</UserId>"
                f"<Account>{ACCOUNT}</Account></GetCallerIdentityResult>"
                "<ResponseMetadata><RequestId>1</RequestId></ResponseMetadata>"
                "</GetCallerIdentityResponse>"
            )
        elif action == "AssumeRole":
            expires = (datetime.now(UTC) + timedelta(hours=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            status, reply = 200, (
                "<AssumeRoleResponse><AssumeRoleResult><Credentials>"
                f"<AccessKeyId>{ISSUED[0]}</AccessKeyId>"
                f"<SecretAccessKey>{ISSUED[1]}</SecretAccessKey>"
                f"<SessionToken>{ISSUED[2]}</SessionToken>"
                f"<Expiration>{expires}</Expiration></Credentials>"
                "<AssumedRoleUser><AssumedRoleId>AROA:moonmind</AssumedRoleId>"
                f"<Arn>{ROLE}/moonmind</Arn></AssumedRoleUser></AssumeRoleResult>"
                "<ResponseMetadata><RequestId>1</RequestId></ResponseMetadata>"
                "</AssumeRoleResponse>"
            )
        else:
            status, reply = 400, "<ErrorResponse/>"
        self.send_response(status)
        self.send_header("Content-Type", "text/xml")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply.encode())

    def log_message(self, *_args):
        pass


@pytest.fixture
def provider(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), ProviderApi)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ProviderApi.calls = []
    ProviderApi.expired = False
    url = f"http://127.0.0.1:{server.server_port}"
    # Configuration, not credentials: the worker signs only with the stored
    # source key, never with these ambient values.
    monkeypatch.setenv("AWS_ENDPOINT_URL_STS", url)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAAMBIENTWORKER")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ambient-worker-secret")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    yield url
    server.shutdown()


def settings():
    return resolve_container_backend_settings(
        {
            IMAGE_SOURCES_ENV_KEY: json.dumps(
                [{"sourceRef": "cloud-posture", "image": "prowlercloud/prowler:stable"}]
            ),
            EGRESS_POLICIES_ENV_KEY: json.dumps(
                [
                    {
                        "policyRef": "aws-apis",
                        "allowedWorkflowIds": ["assessment"],
                        "allowedImageSourceRefs": ["cloud-posture"],
                        "destinations": [
                            {
                                "host": host,
                                "purpose": "provider-api",
                                "protocol": "https",
                                "port": 443,
                            }
                            for host in (
                                "sts.amazonaws.com",
                                "s3.amazonaws.com",
                                "iam.amazonaws.com",
                                "ec2.us-east-1.amazonaws.com",
                                "ec2.eu-west-1.amazonaws.com",
                            )
                        ],
                    }
                ]
            ),
        }
    )


def scope(**overrides):
    return AdmittedAssessmentScope.model_validate(
        {
            "assessmentRef": "cloud-5043",
            "profileRefs": ["cloud-posture"],
            "actionClasses": ["cloud-posture"],
            "targets": [
                {
                    "cloudProvider": "aws",
                    "cloudAccount": ACCOUNT,
                    "cloudRegions": ["us-east-1", "eu-west-1"],
                }
            ],
            "networkRefs": ["aws-apis"],
            "maxJobs": 20,
            "maxJobSeconds": 3600,
            "evidenceIntent": "read-only posture of the admitted account",
            "grants": [
                {
                    "ref": "secret://cloud/audit",
                    "purpose": "cloud_inventory",
                    "account": ACCOUNT,
                    "profileRefs": ["cloud-posture"],
                    "expiresAt": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
                    "roleArn": ROLE,
                    "revision": 3,
                }
            ],
            **overrides,
        }
    )


def capability(admitted):
    return ContainerJobSessionCapability(
        owner=OWNER,
        agent_run_id="run",
        workflow_id="assessment",
        step_id="assess",
        session_id="session",
        runtime_id="runtime",
        source_kind="omnigent",
        workspace_kind="sandbox",
        workspace_id="cloud",
        workspace_relative_path="repo",
        workspace_read_only=False,
        expires_at=2**31,
        assessment_scope=admitted,
    )


def assessment(**overrides) -> CloudAssessment:
    return CloudAssessment.model_validate(
        {
            "provider": "aws",
            "assessmentRef": "cloud-5043",
            "account": ACCOUNT,
            "credentialRef": "secret://cloud/audit",
            "regions": ["us-east-1", "eu-west-1"],
            "checks": CHECKS,
            "egressPolicyRef": "aws-apis",
            "timeoutSeconds": 1800,
            **overrides,
        }
    )


class EgressLease:
    """The admitted egress gateway; enforcement has its own suites."""

    gateway_ref, network_ref = "fixture-gateway", "fixture-egress"

    def __init__(self, policy):
        self.policy = policy

    async def prepare(self, **_):
        return {
            "proxyAddress": "10.89.0.2",
            "clientAddress": "10.89.0.3",
            "gatewayIdentity": "fixture",
            "snapshotDigest": "fixture",
        }

    def proxy_env(self):
        return (
            "MOONMIND_EGRESS_PROXY=http://gateway.example.test:3128",
            "HTTPS_PROXY=http://gateway.example.test:3128",
        )

    async def attest(self, **_):
        raise RuntimeError("fixture gateway has no runtime evidence")


class Harness:
    def __init__(self, environment, tmp_path, monkeypatch, provider, *, admitted=None):
        self.environment = environment
        self.tmp_path = tmp_path
        self.provider = provider
        self.temporal = AsyncMock()
        self.settings = settings()
        self.jobs = ContainerJobService(
            environment.session,
            temporal=self.temporal,
            artifacts=environment.service,
            authorizer=PrivateImageAuthorizationService(
                PrivateImageAuthorizationPolicy.model_validate({"grants": []})
            ),
            backend_settings=self.settings,
            capability=capability(admitted or scope()),
        )
        self.published: dict[str, bytes] = {}
        self.commands: list[tuple] = []
        self.store = {"secret://cloud/audit": (3, json.dumps(SOURCE))}

        def lease(backend, request):
            policy = backend._settings.authorize_egress(request.request)
            return EgressLease(policy) if policy is not None else None

        async def egress_evidence(backend, request, evidence, **_):
            return await self.publish(request, f"{request.job_id}-egress.json", b"{}")

        monkeypatch.setattr(DockerContainerJobBackend, "_scoped_lease", lease)
        monkeypatch.setattr(
            DockerContainerJobBackend, "_publish_scoped_egress", egress_evidence
        )

    async def publish(self, request, name, payload):
        principal = owner_artifact_principal(request.owner)
        artifact, _ = await self.environment.service.create(
            principal=principal,
            content_type="application/json",
            link={
                "namespace": "default",
                "workflow_id": "assessment",
                "run_id": "run",
                "link_type": "output.container-job",
            },
        )
        await self.environment.service.write_complete(
            artifact_id=artifact.artifact_id, principal=principal, payload=payload
        )
        self.published[name] = payload
        return artifact.artifact_id

    async def submit(self, cloud: CloudAssessment, key: str, jobs=None):
        submission = ContainerJobSubmitRequest(
            idempotencyKey=key,
            source={"source": "workflow", "workflowId": "assessment"},
            spec={
                **build_cloud_assessment_job(cloud),
                "workspaceRef": {"kind": "sandbox", "workspaceId": key},
            },
        )
        accepted = await (jobs or self.jobs).submit(owner=OWNER, request=submission)
        return submission, accepted

    async def run(self, cloud, key, *, scenario, terminal=None):
        submission, accepted = await self.submit(cloud, key)
        started = self.temporal.start_container_job.call_args.args[0]

        async def daemon(argv):
            self.commands.append(tuple(argv))
            if argv[0] == "inspect":
                return 1, b"", b"no such container"
            return 0, b"", b""

        async def resolve_secret(ref, revision):
            current = self.store[ref]
            assert current[0] == revision
            return current[1]

        backend = DockerContainerJobBackend(
            workspace_root=self.tmp_path,
            command_runner=daemon,
            evidence_publisher=self.publish,
            artifact_reader=_container_job_artifact_reader(self.environment.service),
            secret_resolver=resolve_secret,
            log_spool_root=self.tmp_path / "live",
            settings=self.settings,
        )
        request = ContainerJobActivityRequest(
            jobId=accepted.job_id,
            owner=OWNER,
            ownershipToken=f"{accepted.job_id}:v1",
            request=submission,
            workloadCredentials=started.workload_credentials,
            resolvedWorkspaceRef=str(self.tmp_path / "unused"),
            resolvedImageRef=IMAGE,
            imageObservation=ImageObservation(
                requestedReference="prowlercloud/prowler:stable",
                imageSourceRef="cloud-posture",
                resolvedDigest=IMAGE,
            ),
        )
        await backend.create_container(request)
        create = [c for c in self.commands if c[0] == "create"][-1]
        storage = storage_for(self.tmp_path, accepted.job_id)
        self.delivered = {
            p.name: p.read_text()
            for p in storage.workload_credentials.rglob("*")
            if p.is_file()
        }
        code = self.execute(create, storage, scenario)
        request.container_ref = "fixture-container"
        request.exit_code = None if terminal else code
        request.terminal_state = terminal or ("succeeded" if code == 0 else "failed")
        await backend.publish_evidence(request)
        summary = json.loads(self.published[f"{accepted.job_id}-output-summary.json"])
        # The issued session leaves the job's mount when the container exits.
        assert not (storage.workload_credentials / "files").exists()
        return summary, create, storage, accepted

    def execute(self, create, storage, scenario) -> int:
        """Run the launch argv as the container would, with job storage paths."""
        assert "--entrypoint" not in create
        argv = list(create[create.index(IMAGE) + 1 :])
        env = {
            "PATH": os.environ["PATH"],
            "AWS_ENDPOINT_URL": self.provider,
            "FIXTURE_PROWLER_SCENARIO": scenario,
        }
        for i, item in enumerate(create):
            if item == "--env":
                name, _, value = create[i + 1].partition("=")
                env[name] = value
        # The provider transport stands in for the egress gateway's upstream.
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            env.pop(name, None)
        env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1"
        env["AWS_SHARED_CREDENTIALS_FILE"] = env["AWS_SHARED_CREDENTIALS_FILE"].replace(
            WORKLOAD_CREDENTIALS_TARGET, str(storage.workload_credentials / "files")
        )
        argv = [a.replace("/outputs", str(storage.outputs)) for a in argv]
        result = subprocess.run(
            [sys.executable, str(FAKE_PROWLER), *argv],
            env=env,
            capture_output=True,
            timeout=120,
        )
        return result.returncode

    def assert_no_seed(self):
        for name, payload in self.published.items():
            for seed in SEEDS:
                assert seed.encode() not in payload, name
        for path in (self.tmp_path / "live").rglob("*"):
            if path.is_file():
                for seed in SEEDS:
                    assert seed.encode() not in path.read_bytes()


def artifact_names(harness, accepted):
    page = json.loads(harness.published[f"{accepted.job_id}-artifacts.json"])
    return {a["name"]: a for a in page["artifacts"]}


async def test_findings_and_compliant_resources_use_only_the_issued_session(
    environment, tmp_path, monkeypatch, provider
):
    harness = Harness(environment, tmp_path, monkeypatch, provider)
    summary, create, storage, accepted = await harness.run(
        assessment(), "findings", scenario="findings"
    )
    assert (summary["outcome"], summary["status"]) == ("findings", "complete")
    assert summary["actionClass"] == "cloud-posture"
    assert summary["image"] == {
        "sourceRef": "cloud-posture",
        "requestedReference": "prowlercloud/prowler:stable",
        "resolvedRef": IMAGE,
    }
    assert summary["execution"]["version"] == "5.46.0"
    failed = {(f["checkId"], f["resourceUids"][0]) for f in summary["findings"]}
    assert ("s3_bucket_level_public_access_block", "arn:aws:s3:::open-bucket") in failed
    assert summary["coverage"]["compliantResources"] == ["arn:aws:s3:::locked-bucket"]
    assert summary["coverage"]["scannedAccounts"] == [ACCOUNT]

    # The worker exchanged the stored source key, not ambient credentials,
    # for a read-only session; the job signed only with that session.
    assume, identity = ProviderApi.calls
    assert assume["action"] == "AssumeRole" and assume["key"] == SOURCE["accessKeyId"]
    assert assume["form"]["RoleArn"] == [ROLE]
    assert assume["form"]["PolicyArns.member.1.arn"] == [
        "arn:aws:iam::aws:policy/SecurityAudit"
    ]
    assert identity["action"] == "GetCallerIdentity" and identity["key"] == ISSUED[0]
    assert {c["action"] for c in ProviderApi.calls} <= READ_ONLY
    assert f"aws_session_token = {ISSUED[2]}" in harness.delivered["cloud"]

    # Launch: the image's Prowler entrypoint, admitted argv, no shell, no
    # daemon socket, no live workspace, no ambient credential sources.
    joined = " ".join(create)
    assert not set(create) & UNADMITTED_OPTIONS
    assert "docker.sock" not in joined and "dst=/workspace" not in joined
    assert "AWS_CONFIG_FILE=/dev/null" in create
    assert "AWS_EC2_METADATA_DISABLED=true" in create
    for seed in SEEDS:
        assert seed not in joined
    names = artifact_names(harness, accepted)
    assert {"report", "log", "summary"} <= set(names)
    native = json.loads(harness.published[f"{accepted.job_id}-output-report.json"])
    assert {f["findingUid"] for f in summary["findings"]} <= {
        r["finding_info"]["uid"] for r in native
    }
    harness.assert_no_seed()

    # Security Skills review the retained summary through the existing
    # read-only report path; the observed coverage status is kept.
    outputs = await invoke(
        environment.service,
        {
            "scope": {"assets": [f"aws:{ACCOUNT}"]},
            "tools": ["retained-report"],
            "reportRefs": [names["summary"]["artifactRef"]],
            "credentialRefs": [],
            "networkRefs": [],
            "budget": {
                "baseSeconds": 60,
                "maxSeconds": 120,
                "progressStallSeconds": 60,
            },
        },
        key="cloud-review",
    )
    (reviewed,) = outputs["scannerSummaries"]
    assert (reviewed["scanner"], reviewed["coverage"], reviewed["outcome"]) == (
        "prowler",
        "complete",
        "findings",
    )
    assert outputs["counts"]["new"] == 3

    clean, _, _, _ = await harness.run(assessment(), "compliant", scenario="compliant")
    assert (clean["outcome"], clean["status"]) == ("clean", "complete")


async def test_denied_and_throttled_collection_is_partial_coverage(
    environment, tmp_path, monkeypatch, provider
):
    harness = Harness(environment, tmp_path, monkeypatch, provider)
    summary, _, _, accepted = await harness.run(
        assessment(), "partial", scenario="partial"
    )
    assert (summary["outcome"], summary["status"]) == ("findings", "partial")
    assert {"access_denied", "throttled", "manual_results"} <= set(
        summary["limitations"]
    )
    assert summary["coverage"]["regionsWithErrors"] == ["eu-west-1", "us-east-1"]
    assert summary["coverage"]["manualChecks"] == ["s3_bucket_object_versioning"]
    assert "arn:aws:s3:::locked-bucket" not in summary["coverage"]["compliantResources"]
    outputs = await invoke(
        environment.service,
        {
            "scope": {"assets": [f"aws:{ACCOUNT}"]},
            "tools": ["retained-report"],
            "reportRefs": [artifact_names(harness, accepted)["summary"]["artifactRef"]],
            "credentialRefs": [],
            "networkRefs": [],
            "budget": {
                "baseSeconds": 60,
                "maxSeconds": 120,
                "progressStallSeconds": 60,
            },
        },
        key="cloud-partial-review",
    )
    assert outputs["scannerSummaries"][0]["coverage"] == "partial"


async def test_expired_session_is_an_execution_failure_with_its_cause(
    environment, tmp_path, monkeypatch, provider
):
    harness = Harness(environment, tmp_path, monkeypatch, provider)
    ProviderApi.expired = True
    summary, _, _, _ = await harness.run(assessment(), "expired", scenario="findings")
    assert (summary["outcome"], summary["status"]) == ("execution_failed", "partial")
    assert {d["kind"] for d in summary["diagnostics"]} == {"credentials_rejected"}
    assert summary["coverage"]["scannedAccounts"] == []
    harness.assert_no_seed()


@pytest.mark.parametrize(
    "scenario, terminal, overrides, expected",
    [
        ("malformed", None, {}, "report_malformed"),
        ("canceled", "canceled", {}, "execution_canceled"),
        ("oversize", None, {"maxReportBytes": 65536}, "report_exceeds_limit"),
    ],
)
async def test_unusable_output_and_cancellation_stay_incomplete_and_readable(
    environment,
    tmp_path,
    monkeypatch,
    provider,
    scenario,
    terminal,
    overrides,
    expected,
):
    harness = Harness(environment, tmp_path, monkeypatch, provider)
    summary, _, storage, accepted = await harness.run(
        assessment(**overrides), scenario, scenario=scenario, terminal=terminal
    )
    assert summary["status"] == "partial" and expected in summary["limitations"]
    assert summary["outcome"] != "clean"
    if scenario == "canceled":
        # Complete records written before the stop are kept as findings.
        assert summary["outcome"] == "findings"
        assert "report_truncated" in summary["limitations"]
    # Published evidence outlives job storage cleanup; an output collection
    # declined to publish keeps storage, since it holds the only copy.
    assert attachments.release(
        storage, ownership_token=f"{accepted.job_id}:v1", retain_outputs=False
    ) == ("outputs_retained" if scenario == "oversize" else "removed")
    retained = artifact_names(harness, accepted)["summary"]["artifactRef"]
    _, payload = await environment.service.read(
        artifact_id=retained, access=ArtifactAccessContext.for_execution("assessment")
    )
    assert json.loads(payload)["status"] == "partial"


async def test_echoed_session_values_never_reach_published_evidence(
    environment, tmp_path, monkeypatch, provider
):
    harness = Harness(environment, tmp_path, monkeypatch, provider)
    summary, _, _, accepted = await harness.run(assessment(), "echo", scenario="echo")
    native = harness.published[f"{accepted.job_id}-output-report.json"]
    assert b"[redacted]" in native
    harness.assert_no_seed()
    assert summary["status"] == "complete"


async def test_scope_refuses_unadmitted_cloud_work_before_any_job(
    environment, tmp_path, monkeypatch, provider
):
    harness = Harness(environment, tmp_path, monkeypatch, provider)
    for cloud, message in (
        (assessment(regions=["ap-south-1"]), "region"),
        (assessment(account="210987654321"), "account"),
        (assessment(timeoutSeconds=4000), "budget"),
    ):
        with pytest.raises(ContainerJobScopeDeniedError, match=message):
            await harness.submit(cloud, hashlib.sha256(message.encode()).hexdigest())
    other_grant = Harness(
        environment,
        tmp_path,
        monkeypatch,
        provider,
        admitted=scope(grants=[]),
    )
    with pytest.raises(ContainerJobScopeDeniedError, match="credential reference"):
        await other_grant.submit(assessment(), "no-grant")
    unscoped = ContainerJobService(
        environment.session,
        temporal=AsyncMock(),
        artifacts=environment.service,
        backend_settings=settings(),
    )
    with pytest.raises(ContainerJobScopeDeniedError, match="admitted assessment scope"):
        await harness.submit(assessment(), "unscoped", jobs=unscoped)
    with pytest.raises(ValidationError, match="cloud provider azure is unsupported"):
        assessment(provider="azure")
    harness.temporal.start_container_job.assert_not_called()
    assert ProviderApi.calls == []


def posture_review(summary_ref, **extra):
    return {
        "scope": {"assets": [f"aws:{ACCOUNT}"]},
        "tools": ["retained-report"],
        "reportRefs": [summary_ref],
        "credentialRefs": [],
        "networkRefs": [],
        "budget": {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
        **extra,
    }


@pytest.mark.parametrize("case", ["repaired", "retested", "regions-changed"])
async def test_repaired_account_is_verified_only_with_comparable_retest(
    environment, tmp_path, monkeypatch, provider, case
):
    """Vulnerable -> repaired posture: absence, verified retest, or neither."""
    harness = Harness(environment, tmp_path, monkeypatch, provider)
    _, _, _, accepted = await harness.run(
        assessment(), "vulnerable", scenario="findings"
    )
    baseline = await invoke(
        environment.service,
        posture_review(artifact_names(harness, accepted)["summary"]["artifactRef"]),
        key="cloud-vulnerable",
    )
    assert baseline["counts"]["new"] == 3
    prior = await recurring.read(environment.service, baseline["comparisonRef"])
    # Same account and checks with every resource passing; or fewer regions.
    regions = ["us-east-1"] if case == "regions-changed" else ["us-east-1", "eu-west-1"]
    repaired = assessment(regions=regions)
    summary, _, _, accepted = await harness.run(
        repaired, "repaired", scenario="compliant"
    )
    assert (summary["outcome"], summary["status"]) == ("clean", "complete")
    extra = {"baselineRef": baseline["comparisonRef"]}
    if case != "repaired":
        extra["decisionsRef"] = await recurring.put(
            environment.service,
            [
                {
                    "findingId": row["findingId"],
                    "disposition": "fix_retest",
                    "reason": "Enabled the control and reran the check",
                    "evidenceRefs": [baseline["scanRef"]],
                    "author": "operator",
                    "source": "security-remediate",
                    "scope": {"assets": [f"aws:{ACCOUNT}"]},
                    "reviewAt": "2099-01-01T00:00:00+00:00",
                    "reviewConditions": ["check or region change"],
                    "retestOutcome": "passed",
                }
                for row in prior["findings"]
            ],
        )
    outputs = await invoke(
        environment.service,
        posture_review(
            artifact_names(harness, accepted)["summary"]["artifactRef"], **extra
        ),
        key=f"cloud-{case}",
    )
    assert not outputs["diagnostics"]
    comparison = await recurring.read(environment.service, outputs["comparisonRef"])
    states = {row["state"] for row in comparison["findings"]}
    # A changed selection is not the coverage the findings were observed
    # under, so a passing retest still cannot verify their absence.
    assert (
        states
        == {
            "repaired": {"absent_comparable"},
            "retested": {"remediated_verified"},
            "regions-changed": {"not_reassessed"},
        }[case]
    )
    assert outputs["coverageStatus"] == "complete"
    assert outputs["counts"]["new"] == 0
