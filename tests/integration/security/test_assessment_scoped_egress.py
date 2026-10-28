"""Typed assessment jobs under admitted egress, cancellation and timeout (#5034).

The ZAP web journey runs with the production scoped-egress lease rather than a
stub: the backend renders the job's admitted policy into its gateway, attests
it at launch, observation, and evidence publication, and cleans it up. Actual
Squid enforces that same admitted policy on loopback, so the admitted origin
is reachable while a destination outside it is refused, and the refusal is
read back through the lease into the job's evidence. Only Docker is a fake
command boundary (``LeaseDocker``), as in the scoped-egress suites.
"""

from __future__ import annotations

import json
import os
import pwd
import shutil
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api_service.services.container_jobs import ContainerJobService
from moonmind.schemas.container_job_models import (
    AuxiliaryOutcome,
    ContainerJobActivityRequest,
    ContainerJobCancelRequest,
    ContainerJobSubmitRequest,
    OwnerIdentity,
)
from moonmind.security import scoped_egress
from moonmind.security.repository_scanner_runner import run_scanner
from moonmind.security.repository_scanners import RepositoryScan, build_scanner_job
from moonmind.security.scoped_egress import render_scoped_proxy
from moonmind.workflows.temporal.container_job_attachments import storage_for
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from moonmind.workflows.temporal.worker_runtime import _container_job_artifact_reader
from tests.integration.security import test_repository_scanner_jobs as repo
from tests.integration.security import test_web_assessment_jobs as web
from tests.integration.security import web_assessment_fixture_app as fixture
from tests.unit.security.test_scoped_egress import LeaseDocker

environment = web.environment
target = web.target

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

PROXY = "127.0.0.4"
PUBLIC = "93.184.216.34"
JOB_IMAGE = "sha256:" + "a" * 64


class Daemon(LeaseDocker):
    """Stateful daemon for the gateway and job; other commands are no-ops."""

    async def __call__(self, args):
        args = tuple(args)
        if args[0] in {"cp", "wait"} or args[:2] == ("image", "inspect"):
            self.calls.append(args)
            return 0, b"", b""
        return await super().__call__(args)


@contextmanager
def squid(policy, root: Path):
    """Actual Squid enforcing the admitted policy on loopback.

    Loopback stands in for the gateway's internal bridge and pinned answers:
    the destinations and their methods/ports are the admitted policy's own.
    Yields a path under ``root`` that holds Squid's flushed access log once
    the proxy stops, since its own directory is removed on exit.
    """
    binary = shutil.which("squid")
    assert binary, "test-runtime must supply the proxy fixture"
    config, hosts = render_scoped_proxy(
        policy,
        {d.host: ("127.0.0.1",) for d in policy.destinations},
        listen_address=PROXY,
        client_address="127.0.0.1",
    )
    with tempfile.TemporaryDirectory(prefix="moonmind-test-scoped-proxy-") as name:
        path = Path(name)
        identity = {}
        if os.geteuid() == 0:
            # Compose runs pytest as root; Squid's own account owns its files.
            account = pwd.getpwnam("proxy")
            os.chown(path, account.pw_uid, account.pw_gid)
            identity = {
                "user": account.pw_uid,
                "group": account.pw_gid,
                "extra_groups": [],
            }
        log = path / "access.log"
        (path / "hosts").write_bytes(hosts)
        conf = path / "squid.conf"
        conf.write_bytes(
            config.replace(b"/tmp/moonmind-hosts", str(path / "hosts").encode())
            .replace(b"/tmp/moonmind-access.log", str(log).encode())
            .replace(b"/tmp/moonmind-squid.pid", str(path / "squid.pid").encode())
            .replace(b"/tmp/moonmind-cache.log", str(path / "cache.log").encode())
        )
        errors = root / "proxy.stderr"
        retained = root / "access.log"
        with errors.open("wb") as stderr:
            proxy = subprocess.Popen(
                [binary, "-NYC", "-d", "1", "-f", str(conf)],
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                **identity,
            )
            try:
                deadline = time.monotonic() + 10
                while True:
                    if proxy.poll() is not None:
                        pytest.fail(f"proxy exited: {errors.read_text()[-4000:]}")
                    try:
                        socket.create_connection((PROXY, 3128), timeout=0.1).close()
                        break
                    except OSError:
                        assert time.monotonic() < deadline, errors.read_text()
                        time.sleep(0.1)
                yield retained
            finally:
                proxy.terminate()
                proxy.wait(timeout=10)
                if log.exists():
                    shutil.copyfile(log, retained)


async def test_web_assessment_reaches_only_its_admitted_origin(
    environment, tmp_path, monkeypatch, target
):
    app, gateway = target
    production_lease = DockerContainerJobBackend._scoped_lease
    publish_scoped = DockerContainerJobBackend._publish_scoped_egress
    harness = web.Harness(environment, tmp_path, monkeypatch, app, gateway)
    # The web journey's harness stubs the lease; this journey uses production's.
    monkeypatch.setattr(DockerContainerJobBackend, "_scoped_lease", production_lease)
    monkeypatch.setattr(
        DockerContainerJobBackend, "_publish_scoped_egress", publish_scoped
    )
    monkeypatch.setattr(
        scoped_egress, "_public_answers", AsyncMock(return_value=(PUBLIC,))
    )
    monkeypatch.setattr(
        scoped_egress,
        "attest_docker_egress",
        AsyncMock(
            return_value=SimpleNamespace(gateway_image_digest="sha256:" + "1" * 64)
        ),
    )
    assessment = web.assessment(app, mode="active", activeStrength="low")
    submission, accepted = await harness.submit(assessment, "scoped-egress")
    started = harness.temporal.start_container_job.call_args.args[0]
    workspace = tmp_path / "temporal_sandbox" / "scoped-egress" / "repo"
    workspace.mkdir(parents=True)
    docker = Daemon()
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=docker,
        evidence_publisher=harness.publish,
        artifact_reader=_container_job_artifact_reader(environment.service),
        secret_resolver=AsyncMock(),
        log_spool_root=tmp_path / "live",
        settings=harness.settings,
    )
    request = ContainerJobActivityRequest(
        jobId=accepted.job_id,
        owner=web.OWNER,
        ownershipToken=f"{accepted.job_id}:v1",
        request=submission,
        registryAuthorization=started.registry_authorization,
        workloadCredentials=started.workload_credentials,
        resolvedWorkspaceRef=str(workspace),
        resolvedImageRef=JOB_IMAGE,
    )
    created = await backend.create_container(request)
    request.container_ref = created.container_ref
    request.egress_attestation_ref = created.diagnostics_ref
    launched = await backend.start_container(request)
    assert launched.running is True
    lease = backend._scoped_lease(request)
    assert lease.policy.policy_ref == "web-target"
    # The job reaches the network only through its own gateway.
    create = next(c for c in docker.calls if c[0] == "create" and JOB_IMAGE in c)
    assert create[create.index("--network") + 1] == lease.network_ref
    assert f"MOONMIND_EGRESS_PROXY={lease.proxy_url}" in create
    installed = docker.files["/tmp/moonmind-squid.conf"].decode()
    assert f"dstdomain -n {web.HOST}" in installed
    assert "request-denied.moonmind.invalid" not in installed

    with squid(lease.policy, tmp_path) as access_log:
        storage = storage_for(tmp_path, accepted.job_id)
        code = run_job(harness, create, storage, assessment, lease)
    # The admitted origin answered through the proxy, including an admitted
    # POST; the guard's refused form submission was denied by the proxy.
    assert "/login" in fixture.App.posted and "/feedback" not in fixture.App.posted
    lines = access_log.read_text().splitlines()
    assert any("TCP_DENIED/403" in line and "request-denied" in line for line in lines)
    assert any(
        f"{web.HOST}:{app.server_port}/login" in line and "TCP_DENIED" not in line
        for line in lines
    )
    snapshot = json.loads(docker.files["/tmp/moonmind-scope.json"])
    # The gateway's log, attributed to the job's address on its internal bridge.
    docker.files["/tmp/moonmind-access.log"] = (
        "\n".join(
            line.replace(" 127.0.0.1 ", f" {snapshot['clientAddress']} ", 1)
            for line in lines
        ).encode()
        + b"\n"
    )
    docker.container(request.container_ref)["State"].update(
        Status="exited", Running=False, ExitCode=code
    )

    observed = await backend.observe_container(request)
    assert observed.exit_code == 0
    # A clean exit cannot hide a refused destination.
    assert observed.terminal_state == "failed"
    assert observed.egress_denial_observation.denied_connection_count >= 1
    request.exit_code = observed.exit_code
    request.terminal_state = observed.terminal_state
    request.egress_denial_observation = observed.egress_denial_observation
    await backend._collect_live_logs(request, request.container_ref)
    published = await backend.publish_evidence(request)
    # As the job workflow does before removal and cleanup.
    request.publication = AuxiliaryOutcome(
        state="succeeded", diagnosticsRef=published.diagnostics_ref
    )
    diagnostics = json.loads(harness.published[f"{accepted.job_id}-diagnostics.json"])
    egress = diagnostics["egressEvidence"]
    assert egress["profileRef"] == "web-target"
    assert egress["coverage"] == "partial"
    assert "required_network_scope_denied" in egress["limitations"]
    assert egress["deniedConnectionCount"] >= 1
    # Normalized destination authority only; never the request path.
    assert any(
        row.startswith("denied request-denied.moonmind.invalid")
        for row in egress["denialDiagnostics"]
    )
    assert not any("/feedback" in row for row in egress["denialDiagnostics"])
    summary = json.loads(harness.published[f"{accepted.job_id}-output-summary.json"])
    assert "POST /login" in summary["coverage"]["testedRoutes"]
    harness.assert_no_secret()

    await backend.remove_container(request)
    cleaned = await backend.cleanup(request)
    lifecycle = json.loads(
        harness.published[f"{accepted.job_id}-egress-lifecycle.json"]
    )
    assert cleaned.cleanup_succeeded is True, lifecycle
    assert lifecycle["retainedDenialObservation"]["deniedConnectionCount"] >= 1
    # Job storage is released; the published native report remains readable.
    assert not storage.root.exists()
    report = web.artifact_names(harness, accepted)["report"]["artifactRef"]
    _, body = await environment.service.read(
        artifact_id=report,
        principal=web.owner_artifact_principal(web.OWNER),
        allow_restricted_raw=True,
    )
    assert json.loads(body)["site"]
    # The job-owned gateway and networks are gone; the denial stays in evidence.
    assert lease.gateway_ref not in docker.containers
    assert not {lease.network_ref, lease.outbound_ref} & set(docker.networks)


def run_job(harness, create, storage, assessment, lease) -> int:
    """The launch argv as the container runs it, its proxy on loopback."""
    image = create.index(JOB_IMAGE)
    entrypoint = create[create.index("--entrypoint") + 1]
    argv = [entrypoint, *create[image + 1 :]]
    assert argv[:2] == ["sh", "-c"]
    script = argv[2].replace(web.CREDENTIAL_TARGET, str(storage.credentials))
    out = storage.outputs / assessment.output_directory
    env = {
        "PATH": f"{harness.bin_dir}:{os.environ['PATH']}",
        "FIXTURE_OUTPUTS": str(storage.outputs),
        "FIXTURE_INPUTS": str(storage.inputs),
        "FIXTURE_PROXY_MAP": f"{lease.gateway_ref}:3128={PROXY}:3128",
        "FIXTURE_ZAP_FAULT": "",
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
        check=False,
    )
    return result.returncode


@pytest.mark.parametrize("failure", ["cancel", "timeout"])
async def test_repository_job_stopped_through_its_admitted_path_keeps_evidence(
    environment, tmp_path, monkeypatch, failure
):
    """Cancellation and timeout of an admitted repository scanner job.

    The operator's cancel goes through the job service; the stopped scanner
    keeps the native report it finished, publication marks coverage partial,
    the released job storage leaves the retained report readable, and no
    successor job starts.
    """
    root = tmp_path / "repository"
    repo.repository(root)
    scan = RepositoryScan(
        scanner="gitleaks", outputDirectory="artifacts/scan", timeoutSeconds=1
    )
    repo.install_fixture_process(tmp_path, "gitleaks", monkeypatch, failure=failure)
    workload = build_scanner_job(scan)
    workload.pop("imageSourceRef")
    workload.update(
        image="python:3.13",
        workspaceRef={"kind": "sandbox", "workspaceId": "repository"},
    )
    submission = ContainerJobSubmitRequest(
        idempotencyKey=f"stopped-{failure}",
        source={"source": "workflow", "workflowId": "assessment"},
        spec=workload,
    )
    owner = OwnerIdentity(principalType="user", principalId="operator")
    temporal = AsyncMock()
    jobs = ContainerJobService(environment.session, temporal=temporal)
    accepted = await jobs.submit(owner=owner, request=submission)
    published = {}

    async def publish(_request, name, payload):
        assert repo.MARKER.encode() not in payload
        published[name] = await repo.put(environment.service, payload)
        return published[name]

    async def daemon(argv):
        if argv[0] == "inspect":
            return 1, b"", b"no such container"
        return 0, b"", b""

    request = ContainerJobActivityRequest(
        jobId=accepted.job_id,
        ownershipToken=f"{accepted.job_id}:v1",
        request=submission,
        resolvedWorkspaceRef=str(root),
        resolvedImageRef=JOB_IMAGE,
    )
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=daemon,
        evidence_publisher=publish,
        log_spool_root=tmp_path / "live",
    )
    await backend.create_container(request)
    if failure == "cancel":
        canceled = await jobs.cancel(
            owner=owner,
            job_id=accepted.job_id,
            request=ContainerJobCancelRequest(idempotencyKey="operator-cancel"),
        )
        assert canceled.accepted is True
        temporal.signal_container_job_cancel.assert_awaited_once_with(accepted.job_id)
    storage = storage_for(tmp_path, accepted.job_id)
    # The scanner the container runs: docker stop delivers SIGTERM (cancel);
    # the admitted budget ends a scanner that does not finish (timeout).
    code = run_scanner(
        scan,
        source=storage.inputs / "source",
        output=storage.outputs / scan.output_directory,
    )
    assert code == 2
    request.container_ref = "fixture-container"
    request.terminal_state = "canceled" if failure == "cancel" else "timed_out"
    request.exit_code = code
    evidence = await backend.publish_evidence(request)
    request.publication = AuxiliaryOutcome(
        state="succeeded", diagnosticsRef=evidence.diagnostics_ref
    )
    await backend.remove_container(request)
    cleaned = await backend.cleanup(request)
    assert cleaned.cleanup_succeeded is True
    assert not storage.root.exists()

    summary_ref = published[f"{accepted.job_id}-output-summary.json"]
    summary = await repo.read(environment.service, summary_ref)
    assert summary["coverage"] == "partial"
    stopped = "canceled" if failure == "cancel" else "timed_out"
    assert stopped in summary["execution"]["states"].values()
    # The finished native report survives the stop and the storage release.
    history = summary["nativeRefs"]["history"]
    _, body = await environment.service.read(
        artifact_id=history, principal="workflow:assessment", allow_restricted_raw=True
    )
    assert body and repo.MARKER.encode() not in body
    assert summary["results"]["history"]["findings"]
    outputs = await repo.invoke(
        environment.service,
        {
            "scope": {
                "assets": sorted(
                    {
                        f["asset"]
                        for row in summary["results"].values()
                        for f in row["findings"]
                    }
                    | set(summary["execution"]["assets"])
                )
            },
            "tools": ["retained-report"],
            "reportRefs": [summary_ref],
            "credentialRefs": [],
            "networkRefs": [],
            "budget": {
                "baseSeconds": 60,
                "maxSeconds": 120,
                "progressStallSeconds": 60,
            },
        },
    )
    # Findings and incomplete coverage coexist; nothing reads as clean.
    assert outputs["counts"]["new"] >= 1
    assert outputs["coverageStatus"] == "stale_or_partial"
    # Stopping the job starts no successor.
    assert temporal.start_container_job.await_count == 1
    status = await jobs.status(owner=owner, job_id=accepted.job_id)
    assert status.job_id == accepted.job_id
