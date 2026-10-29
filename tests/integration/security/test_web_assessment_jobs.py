"""Scoped ZAP web and API assessments through the production job path (#5044).

ZAP is a deterministic stand-in at the binary boundary
(``web_assessment_fake_zap``): it executes the plan the worker wrote, through
the upstream proxy the production launch script derives from the egress
environment, against the disposable fixture application with vulnerable and
fixed routes, two login roles, a bearer-token service and an OpenAPI
definition. Scope admission, identity
grants, attachment capture, plan rendering, secret delivery, launch
arguments, output collection, sanitization, the adapter, artifact storage and
attachment cleanup are production code; no daemon socket, external target or
paid service is involved. The stand-in honors the launch's rate-limit rule
the way ZAP's network rate limiter does.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from temporalio.testing import ActivityEnvironment

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
    ContainerJobBackendError,
    ContainerJobFailureClass,
    ContainerJobSubmitRequest,
    OwnerIdentity,
)
from moonmind.schemas.repository_scanner_models import WebAssessment
from moonmind.security.container_job_capabilities import ContainerJobSessionCapability
from moonmind.security.web_assessments import (
    CREDENTIAL_TARGET,
    build_web_assessment_job,
)
from moonmind.security.workload_profiles import AdmittedAssessmentScope
from moonmind.workflows.temporal import container_job_attachments as attachments
from moonmind.workflows.temporal.artifacts import ArtifactAccessContext
from moonmind.workflows.temporal.container_job_attachments import storage_for
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from moonmind.workflows.temporal.worker_runtime import _container_job_artifact_reader
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security import web_assessment_fixture_app as fixture
from tests.integration.security.test_recurring_assessments import invoke

environment = recurring.environment

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
OWNER = OwnerIdentity(principalType="user", principalId="operator")
HOST = "app.example.test"
FAKE_ZAP = Path(__file__).with_name("web_assessment_fake_zap.py")
GATEWAY = "gateway.example.test:3128"
SECRETS = (*fixture.PASSWORDS.values(), *fixture.TOKENS)


@pytest.fixture
def target():
    app, gateway = fixture.start(app_host=HOST)
    yield app, gateway
    app.shutdown()
    gateway.shutdown()
    fixture.Gateway.denied.clear()
    fixture.App.posted.clear()


def origin(app) -> str:
    return f"http://{HOST}:{app.server_port}"


def settings(app):
    return resolve_container_backend_settings(
        {
            IMAGE_SOURCES_ENV_KEY: json.dumps(
                [
                    {
                        "sourceRef": "web-assessment",
                        "image": "ghcr.io/zaproxy/zaproxy:stable",
                    }
                ]
            ),
            EGRESS_POLICIES_ENV_KEY: json.dumps(
                [
                    {
                        "policyRef": "web-target",
                        "allowedWorkflowIds": ["assessment"],
                        "allowedImageSourceRefs": ["web-assessment"],
                        "destinations": [
                            {
                                "host": HOST,
                                "purpose": "live-target",
                                "protocol": "http",
                                "port": app.server_port,
                            }
                        ],
                    }
                ]
            ),
        }
    )


def grant(name):
    return {
        "ref": f"secret://web/{name}",
        "purpose": "application_auth",
        "account": name,
        "profileRefs": ["web-assessment"],
        "expiresAt": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
    }


ROUTES = [
    {"path": "/", "methods": ["GET"]},
    {"path": "/login", "methods": ["GET", "POST"]},
]


def scope(
    app, *, action_classes=("web-passive", "web-api", "web-active"), artifacts=()
):
    return AdmittedAssessmentScope.model_validate(
        {
            "assessmentRef": "web-5044",
            "profileRefs": ["web-assessment"],
            "actionClasses": list(action_classes),
            "targets": [
                {
                    "webOrigin": origin(app),
                    "webRoutes": ROUTES,
                    "webExclusions": ["/private"],
                },
                *({"artifactRef": ref} for ref in artifacts),
            ],
            "networkRefs": ["web-target"],
            "maxJobs": 20,
            "maxJobSeconds": 3600,
            "evidenceIntent": "web assessment journey",
            "grants": [grant("alice"), grant("root"), grant("svc")],
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
        workspace_id="web",
        workspace_relative_path="repo",
        workspace_read_only=False,
        expires_at=2**31,
        assessment_scope=admitted,
    )


def identity(name):
    return {
        "name": name,
        "method": "form",
        "credentialRef": f"secret://web/{name}",
        "verificationPath": "/account",
        "loggedInMarker": f"Signed in as {name}",
        "loginPath": "/login",
        "username": name,
    }


def bearer(verification_path="/account"):
    return {
        "name": "svc",
        "method": "bearer",
        "credentialRef": "secret://web/svc",
        "verificationPath": verification_path,
        "loggedInMarker": "Signed in as svc",
    }


def assessment(app, **overrides) -> WebAssessment:
    return WebAssessment.model_validate(
        {
            "mode": "baseline",
            "assessmentRef": "web-5044",
            "origin": origin(app),
            "routes": ROUTES,
            "exclusions": ["/private"],
            "egressPolicyRef": "web-target",
            "timeoutSeconds": 300,
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
            f"MOONMIND_EGRESS_PROXY=http://{GATEWAY}",
            f"HTTP_PROXY=http://{GATEWAY}",
        )

    async def attest(self, **_):
        raise RuntimeError("fixture gateway has no runtime evidence")


class Harness:
    def __init__(
        self, environment, tmp_path, monkeypatch, app, gateway, *, admitted=None
    ):
        self.environment = environment
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path
        self.app, self.gateway = app, gateway
        self.temporal = AsyncMock()
        self.settings = settings(app)
        self.jobs = ContainerJobService(
            environment.session,
            temporal=self.temporal,
            artifacts=environment.service,
            authorizer=PrivateImageAuthorizationService(
                PrivateImageAuthorizationPolicy.model_validate({"grants": []})
            ),
            backend_settings=self.settings,
            capability=capability(admitted or scope(app)),
        )
        self.published: dict[str, bytes] = {}
        self.commands: list[tuple] = []
        self.passwords = {
            **fixture.PASSWORDS,
            **{name: token for token, name in fixture.TOKENS.items()},
        }

        def lease(backend, request):
            policy = backend._settings.authorize_egress(request.request)
            return EgressLease(policy) if policy is not None else None

        async def egress_evidence(backend, request, evidence, **_):
            return await self.publish(request, f"{request.job_id}-egress.json", b"{}")

        monkeypatch.setattr(DockerContainerJobBackend, "_scoped_lease", lease)
        monkeypatch.setattr(
            DockerContainerJobBackend, "_publish_scoped_egress", egress_evidence
        )
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        zap = bin_dir / "zap.sh"
        zap.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_ZAP}" "$@"\n')
        zap.chmod(0o755)
        self.bin_dir = bin_dir

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

    async def owned_artifact(self, payload: bytes) -> str:
        principal = owner_artifact_principal(OWNER)
        artifact, _ = await self.environment.service.create(
            principal=principal,
            content_type="application/json",
            link={
                "namespace": "default",
                "workflow_id": "assessment",
                "run_id": "run",
                "link_type": "input.assessment",
            },
        )
        await self.environment.service.write_complete(
            artifact_id=artifact.artifact_id, principal=principal, payload=payload
        )
        return artifact.artifact_id

    async def submit(self, web: WebAssessment, key: str):
        submission = ContainerJobSubmitRequest(
            idempotencyKey=key,
            source={"source": "workflow", "workflowId": "assessment"},
            spec={
                **build_web_assessment_job(web),
                "workspaceRef": {"kind": "sandbox", "workspaceId": key},
            },
        )
        accepted = await self.jobs.submit(owner=OWNER, request=submission)
        return submission, accepted

    async def run(self, web: WebAssessment, key: str, *, fault="", terminal=None):
        submission, accepted = await self.submit(web, key)
        started = self.temporal.start_container_job.call_args.args[0]
        workspace = self.tmp_path / "temporal_sandbox" / key / "repo"
        workspace.mkdir(parents=True)

        async def daemon(argv):
            self.commands.append(tuple(argv))
            if argv[0] == "inspect":
                return 1, b"", b"no such container"
            return 0, b"", b""

        resolved = []

        async def resolve_secret(ref, revision):
            resolved.append(ref)
            return self.passwords[ref.rsplit("/", 1)[-1]]

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
            registryAuthorization=started.registry_authorization,
            workloadCredentials=started.workload_credentials,
            resolvedWorkspaceRef=str(workspace),
            resolvedImageRef="sha256:" + "a" * 64,
        )
        await create_container_in_activity(
            backend, request, self.environment, self.monkeypatch
        )
        create = [c for c in self.commands if c[0] == "create"][-1]
        storage = storage_for(self.tmp_path, accepted.job_id)
        code = self.execute(create, storage, web, fault)
        request.container_ref = "fixture-container"
        request.exit_code = code
        request.terminal_state = terminal or (
            "succeeded" if code in (0, 1, 2) else "failed"
        )
        await backend._collect_live_logs(request, request.container_ref)
        await backend.publish_evidence(request)
        self.backend, self.request = backend, request
        summary = json.loads(self.published[f"{accepted.job_id}-output-summary.json"])
        # The identity secret's lifetime ends with the container.
        assert not storage.credentials.exists()
        return summary, create, storage, accepted, resolved

    def execute(self, create, storage, web, fault) -> int:
        """Run the launch argv as the container would, with job storage paths."""
        image = create.index("sha256:" + "a" * 64)
        entrypoint = create[create.index("--entrypoint") + 1]
        argv = [entrypoint, *create[image + 1 :]]
        assert argv[:2] == ["sh", "-c"]
        script = argv[2].replace(CREDENTIAL_TARGET, str(storage.credentials))
        out = storage.outputs / web.output_directory
        env = {
            "PATH": f"{self.bin_dir}:{os.environ['PATH']}",
            "FIXTURE_OUTPUTS": str(storage.outputs),
            "FIXTURE_INPUTS": str(storage.inputs),
            "FIXTURE_PROXY_MAP": f"{GATEWAY}=127.0.0.1:{self.gateway.server_port}",
            "FIXTURE_ZAP_FAULT": fault,
        }
        for i, item in enumerate(create):
            if item == "--env":
                name, _, value = create[i + 1].partition("=")
                env[name] = value
        result = subprocess.run(
            ["sh", "-c", script, argv[3], str(out), *argv[5:]],
            env=env,
            capture_output=True,
            timeout=120,
        )
        return result.returncode

    def assert_no_secret(self):
        for name, payload in self.published.items():
            for secret in SECRETS:
                assert secret.encode() not in payload, name
        for path in (self.tmp_path / "live").rglob("*"):
            if path.is_file():
                for secret in SECRETS:
                    assert secret.encode() not in path.read_bytes()


def artifact_names(harness, accepted):
    page = json.loads(harness.published[f"{accepted.job_id}-artifacts.json"])
    return {a["name"]: a for a in page["artifacts"]}


async def test_baseline_never_attacks_while_active_finds_only_the_vulnerable_route(
    environment, tmp_path, monkeypatch, target
):
    app, gateway = target
    harness = Harness(environment, tmp_path, monkeypatch, app, gateway)
    baseline, create, storage, accepted, _ = await harness.run(
        assessment(app), "baseline"
    )
    assert baseline["actionClass"] == "web-passive"
    rules = {f["ruleId"] for f in baseline["findings"]}
    assert "10021" in rules and "40012" not in rules
    assert not baseline["coverage"]["activeChecks"]
    assert "GET /search" in baseline["coverage"]["testedRoutes"]
    # The excluded route was never requested and the plan is retained.
    assert not any("/private" in r for r in baseline["coverage"]["testedRoutes"])
    names = artifact_names(harness, accepted)
    assert {"plan", "report", "messages", "summary"} <= set(names)
    plan = json.loads(harness.published[f"{accepted.job_id}-output-plan.yaml"])
    assert "activeScan" not in {job["type"] for job in plan["jobs"]}
    # No daemon socket, privilege or live workspace; egress is the gateway.
    joined = json.dumps(create)
    assert "docker.sock" not in joined and "dst=/workspace" not in joined
    assert [a for a in create if a.startswith("--privileged")] == ["--privileged=false"]

    active, _, _, accepted, _ = await harness.run(
        assessment(app, mode="active", activeStrength="low"), "active"
    )
    assert active["actionClass"] == "web-active"
    assert active["coverage"]["activeChecks"]
    xss = [f for f in active["findings"] if f["ruleId"] == "40012"]
    assert [(f["route"], f["parameter"], f["phase"], f["risk"]) for f in xss] == [
        ("/search", "q", "active", "high")
    ]
    assert xss[0]["identity"] == "anonymous" and xss[0]["target"] == origin(app)
    assert xss[0]["reproduction"].startswith(active["nativeRef"])
    # Both phases are reported; the passive one first.
    assert {"passive-report", "passive-messages"} <= set(
        artifact_names(harness, accepted)
    )
    harness.assert_no_secret()
    # Security Skills review the retained findings through the existing
    # read-only report path; nothing is changed or published on the target.
    outputs = await invoke(
        environment.service,
        {
            "scope": {"assets": [origin(app)]},
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
        key="web-review",
    )
    (reviewed,) = outputs["scannerSummaries"]
    assert (reviewed["scanner"], reviewed["target"]) == ("zap", origin(app))
    assert outputs["counts"]["new"] == len({f["findingId"] for f in active["findings"]})
    assert active["targetActions"] == "none"
    assert active["reviewSkills"] == ["security-investigate", "security-remediate"]


async def test_user_and_admin_identities_have_verified_distinct_coverage(
    environment, tmp_path, monkeypatch, target
):
    app, gateway = target
    harness = Harness(environment, tmp_path, monkeypatch, app, gateway)
    user, create, storage, _, resolved = await harness.run(
        assessment(app, identity=identity("alice")), "alice"
    )
    assert resolved == ["secret://web/alice"]
    assert any(
        f"dst={CREDENTIAL_TARGET}" in m and m.endswith(",readonly") for m in create
    )
    auth = user["coverage"]["authentication"]
    assert (auth["identity"], auth["status"], auth["anonymousCompared"]) == (
        "alice",
        "verified",
        True,
    )
    # A user cannot reach the admin route: partial, with the reason.
    assert "GET /admin" in user["coverage"]["inaccessibleRoutes"]
    assert user["status"] == "partial" and "routes_inaccessible" in user["limitations"]
    assert all(f["identity"] == "alice" for f in user["findings"])

    admin, *_ = await harness.run(assessment(app, identity=identity("root")), "root")
    assert admin["coverage"]["authentication"]["status"] == "verified"
    assert "GET /admin" in admin["coverage"]["testedRoutes"]
    assert "GET /admin" not in admin["coverage"]["inaccessibleRoutes"]
    harness.assert_no_secret()
    assert not any(
        secret
        in json.dumps(harness.temporal.start_container_job.call_args_list, default=str)
        for secret in SECRETS
    )


async def test_failed_login_cannot_report_clean(
    environment, tmp_path, monkeypatch, target
):
    app, gateway = target
    harness = Harness(environment, tmp_path, monkeypatch, app, gateway)
    harness.passwords["alice"] = "wrong-password"
    summary, *_ = await harness.run(assessment(app, identity=identity("alice")), "bad")
    assert summary["coverage"]["authentication"]["status"] == "failed"
    assert summary["status"] == "partial"
    assert "authentication_failed" in summary["limitations"]


async def test_publication_retry_still_redacts_the_identity_secret(
    environment, tmp_path, monkeypatch, target
):
    app, gateway = target
    harness = Harness(environment, tmp_path, monkeypatch, app, gateway)
    web = assessment(app, identity=identity("alice"))
    _, _, storage, accepted, _ = await harness.run(web, "retry")
    # ZAP evidence can echo an opaque password that no pattern recognizes.
    report = storage.outputs / web.output_directory / "report.json"
    native = json.loads(report.read_text())
    native["site"][0]["alerts"][0]["instances"][0]["evidence"] = fixture.PASSWORDS[
        "alice"
    ]
    report.write_text(json.dumps(native))
    harness.published.clear()
    await harness.backend.publish_evidence(harness.request)
    assert f"{accepted.job_id}-output-report.json" in harness.published
    harness.assert_no_secret()
    # Cleanup removes the value kept for redaction.
    assert list(storage.workload_credentials.rglob("credential-secret"))
    await harness.backend.cleanup(harness.request)
    assert not list(storage.root.rglob("credential-secret"))


async def test_bearer_identity_is_verified_only_when_its_token_works(
    environment, tmp_path, monkeypatch, target
):
    app, gateway = target
    harness = Harness(environment, tmp_path, monkeypatch, app, gateway)
    valid, create, _, _, resolved = await harness.run(
        assessment(app, identity=bearer()), "svc"
    )
    assert resolved == ["secret://web/svc"]
    auth = valid["coverage"]["authentication"]
    # The token is on every request, so its marker is the only proof.
    assert (auth["method"], auth["status"], auth["anonymousCompared"]) == (
        "bearer",
        "verified",
        False,
    )
    assert "GET /account/settings" in valid["coverage"]["testedRoutes"]
    assert all(f["identity"] == "svc" for f in valid["findings"])
    assert not any(token in json.dumps(create) for token in fixture.TOKENS)

    # An invalid or expired token, whether the verification route needs a
    # session or answers everyone, cannot report authenticated coverage.
    harness.passwords["svc"] = "expired-fixture-token"
    for path, key in (("/account", "svc-expired"), ("/", "svc-public")):
        summary, *_ = await harness.run(assessment(app, identity=bearer(path)), key)
        auth = summary["coverage"]["authentication"]
        assert auth["status"] == "failed", path
        assert "did not answer for the identity" in auth["reason"]
        assert summary["status"] == "partial"
        assert "authentication_failed" in summary["limitations"]
        assert "GET /account/settings" not in summary["coverage"]["testedRoutes"]
    harness.assert_no_secret()


async def test_unadmitted_methods_never_reach_the_target(
    environment, tmp_path, monkeypatch, target
):
    app, gateway = target
    harness = Harness(environment, tmp_path, monkeypatch, app, gateway)
    summary, *_ = await harness.run(
        assessment(app, mode="active", activeStrength="low"), "methods"
    )
    # Active crawling submits forms, but only where a route admits POST: the
    # login form reaches the app while the home page's GET-only form does not.
    assert "/login" in fixture.App.posted and "/feedback" not in fixture.App.posted
    assert "POST request-denied.moonmind.invalid" in fixture.Gateway.denied
    assert "POST /login" in summary["coverage"]["testedRoutes"]
    assert not any("/feedback" in r for r in summary["coverage"]["testedRoutes"])


async def test_admitted_rate_bounds_every_request_zap_sends(
    environment, tmp_path, monkeypatch, target
):
    app, gateway = target
    harness = Harness(environment, tmp_path, monkeypatch, app, gateway)
    summary, _, _, accepted, _ = await harness.run(
        assessment(app, identity=identity("alice"), maxRequestsPerSecond=5), "rate"
    )
    assert summary["coverage"]["testedRoutes"]
    messages = json.loads(harness.published[f"{accepted.job_id}-output-messages.har"])
    sent = sorted(
        datetime.fromisoformat(entry["startedDateTime"])
        for entry in messages["log"]["entries"]
    )
    # Login, verification and crawl requests alike: at most five a second.
    assert len(sent) > 5
    assert all(
        later - earlier >= timedelta(seconds=1)
        for earlier, later in zip(sent, sent[5:])
    )


async def test_off_origin_redirect_is_denied_and_recorded(
    environment, tmp_path, monkeypatch, target
):
    app, gateway = target
    harness = Harness(environment, tmp_path, monkeypatch, app, gateway)
    summary, *_ = await harness.run(assessment(app), "redirect")
    assert (
        "redirect GET /away -> http://outside.example.test/"
        in summary["coverage"]["deniedRequests"]
    )
    assert "out_of_scope_requests_denied" in summary["limitations"]
    assert summary["status"] == "partial"


async def test_unreachable_target_is_partial_not_clean(
    environment, tmp_path, monkeypatch, target
):
    app, gateway = target
    harness = Harness(environment, tmp_path, monkeypatch, app, gateway)
    web = assessment(app)
    app.shutdown()
    app.server_close()
    summary, *_ = await harness.run(web, "unreachable")
    assert summary["status"] == "partial"
    assert {"routes_unreachable", "no_routes_tested"} <= set(summary["limitations"])
    assert summary["coverage"]["testedRoutes"] == []


@pytest.mark.parametrize(
    "fault, terminal, expected",
    [
        ("malformed", None, "report_report_malformed"),
        ("warning", None, "zap_plan_warnings"),
        ("cancel", "canceled", "container_job_canceled"),
    ],
)
async def test_malformed_output_plan_codes_and_cancellation_stay_partial(
    environment, tmp_path, monkeypatch, target, fault, terminal, expected
):
    app, gateway = target
    harness = Harness(environment, tmp_path, monkeypatch, app, gateway)
    web = assessment(app, mode="active", activeStrength="medium")
    summary, _, storage, accepted, _ = await harness.run(
        web, fault, fault=fault, terminal=terminal
    )
    assert summary["status"] == "partial" and expected in summary["limitations"]
    if fault == "warning":
        # Findings never come from the exit code; the plan still finished.
        assert summary["execution"] == {
            "state": "succeeded",
            "exitCode": 2,
            "plan": "warnings",
            "version": "2.17.0",
        }
        assert any(f["ruleId"] == "40012" for f in summary["findings"])
    if fault == "cancel":
        # The passive phase survives cancellation during active testing.
        assert summary["execution"]["state"] == "canceled"
        assert not summary["coverage"]["activeChecks"]
        assert summary["findings"] and all(
            f["phase"] == "passive" for f in summary["findings"]
        )
        assert "passive-report" in artifact_names(harness, accepted)
    # Published evidence outlives job storage cleanup; an output collection
    # declined to publish keeps storage, since it holds the only copy.
    assert attachments.release(
        storage, ownership_token=f"{accepted.job_id}:v1", retain_outputs=False
    ) == ("outputs_retained" if fault == "malformed" else "removed")
    retained = artifact_names(harness, accepted)["summary"]["artifactRef"]
    _, payload = await environment.service.read(
        artifact_id=retained, access=ArtifactAccessContext.for_execution("assessment")
    )
    assert json.loads(payload)["status"] == "partial"


async def test_api_definition_is_imported_and_out_of_scope_definitions_refused(
    environment, tmp_path, monkeypatch, target
):
    app, gateway = target
    definition = json.dumps(fixture.SPEC).encode()
    outside = json.dumps(
        {**fixture.SPEC, "servers": [{"url": "http://outside.example.test"}]}
    ).encode()
    harness = Harness(environment, tmp_path, monkeypatch, app, gateway)
    good = await harness.owned_artifact(definition)
    bad = await harness.owned_artifact(outside)
    routes = [*ROUTES, {"path": "/api", "methods": ["GET"]}]
    admitted = scope(app, artifacts=(good, bad))
    admitted = admitted.model_copy(
        update={
            "targets": (
                admitted.targets[0].model_copy(
                    update={
                        "web_routes": tuple(
                            type(admitted.targets[0].web_routes[0]).model_validate(r)
                            for r in routes
                        )
                    }
                ),
                *admitted.targets[1:],
            )
        }
    )
    harness = Harness(
        environment, tmp_path, monkeypatch, app, gateway, admitted=admitted
    )
    api = {"format": "openapi", "inputArtifactRef": good}
    summary, *_ = await harness.run(
        assessment(app, mode="api", routes=routes, apiDefinition=api), "api"
    )
    assert summary["actionClass"] == "web-api"
    assert "GET /api/items" in summary["coverage"]["testedRoutes"]
    assert not summary["coverage"]["activeChecks"]
    with pytest.raises(ContainerJobBackendError) as refused:
        await harness.run(
            assessment(
                app,
                mode="api",
                routes=routes,
                apiDefinition={**api, "inputArtifactRef": bad},
            ),
            "api-outside",
        )
    assert refused.value.failure_class == ContainerJobFailureClass.AUTHORIZATION


async def test_scope_refuses_unadmitted_modes_and_unscoped_sessions(
    environment, tmp_path, monkeypatch, target
):
    app, gateway = target
    passive_only = Harness(
        environment,
        tmp_path,
        monkeypatch,
        app,
        gateway,
        admitted=scope(app, action_classes=("web-passive",)),
    )
    with pytest.raises(ContainerJobScopeDeniedError, match="action class"):
        await passive_only.submit(
            assessment(app, mode="active", activeStrength="high"), "escalate"
        )
    with pytest.raises(ContainerJobScopeDeniedError, match="route or method"):
        await passive_only.submit(
            assessment(app, routes=[{"path": "/", "methods": ["GET", "DELETE"]}]),
            "delete",
        )
    unscoped = ContainerJobService(
        environment.session,
        temporal=AsyncMock(),
        artifacts=environment.service,
        backend_settings=settings(app),
    )
    with pytest.raises(ContainerJobScopeDeniedError, match="admitted assessment scope"):
        await unscoped.submit(
            owner=OWNER,
            request=ContainerJobSubmitRequest(
                idempotencyKey="unscoped",
                source={"source": "workflow", "workflowId": "assessment"},
                spec={
                    **build_web_assessment_job(assessment(app)),
                    "workspaceRef": {"kind": "sandbox", "workspaceId": "unscoped"},
                },
            ),
        )


async def test_clean_target_is_complete_and_distinct_from_lost_coverage(
    environment, tmp_path, monkeypatch, target
):
    """A hardened route with no alerts is clean; losing the target is not."""
    app, gateway = target
    harness = Harness(environment, tmp_path, monkeypatch, app, gateway)
    routes = [{"path": "/fixed/search", "methods": ["GET"]}]
    web = assessment(app, routes=routes, exclusions=[], startPaths=["/fixed/search"])
    clean, _, _, accepted, _ = await harness.run(web, "clean")
    assert (clean["status"], clean["findings"], clean["limitations"]) == (
        "complete",
        [],
        [],
    )
    assert clean["coverage"]["testedRoutes"] == ["GET /fixed/search"]

    def review(summary_ref, **extra):
        return {
            "scope": {"assets": [origin(app)]},
            "tools": ["retained-report"],
            "reportRefs": [summary_ref],
            "credentialRefs": [],
            "networkRefs": [],
            "budget": {
                "baseSeconds": 60,
                "maxSeconds": 120,
                "progressStallSeconds": 60,
            },
            **extra,
        }

    first = await invoke(
        environment.service,
        review(artifact_names(harness, accepted)["summary"]["artifactRef"]),
        key="web-clean",
    )
    assert first["coverageStatus"] == "complete"
    assert first["scannerSummaries"][0]["coverage"] == "complete"
    assert sum(first["counts"].values()) == 0

    # The same admitted job against an unreachable target finds nothing too,
    # but that is lost coverage, never a second clean result.
    app.shutdown()
    app.server_close()
    lost, _, _, accepted, _ = await harness.run(web, "clean-unreachable")
    assert lost["findings"] == [] and lost["status"] == "partial"
    second = await invoke(
        environment.service,
        review(
            artifact_names(harness, accepted)["summary"]["artifactRef"],
            baselineRef=first["comparisonRef"],
        ),
        key="web-clean-unreachable",
    )
    assert second["coverageStatus"] == "stale_or_partial"
    assert second["scannerSummaries"][0]["coverage"] == "partial"


async def create_container_in_activity(backend, request, environment, monkeypatch):
    """Exercise input restoration with its actual job Activity and persisted admission."""
    from moonmind.schemas.container_job_models import container_job_workflow_id
    from moonmind.workflows.temporal import worker_runtime

    monkeypatch.setattr(worker_runtime, "get_async_session_context", environment.sessions)
    activity = ActivityEnvironment()
    activity.info = replace(
        activity.info, workflow_id=container_job_workflow_id(request.job_id)
    )
    return await activity.run(backend.create_container, request)
