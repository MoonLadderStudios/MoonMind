"""Real local protocols through CLI admission, native artifacts and reports.

The signed MCP route, input materializer, target CLI, collector and artifact
service are production code. Only Temporal start and Docker lifecycle commands
are fixtures. The local target doubles as the configured proxy; this does not
claim Docker network isolation or deployment proxy qualification.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from urllib.parse import urlsplit

import pytest
from httpx import ASGITransport, AsyncClient
from typer.testing import CliRunner

from moonmind.cli import app
from moonmind.container_job_cli import ContainerJobCliError, ContainerJobMcpClient
from moonmind.schemas.container_job_models import ContainerJobActivityRequest
from moonmind.security.application_access_target import ApplicationAccessCapture
from moonmind.security.workload_profiles import TARGET_SCOPE_ENV
from moonmind.workflows.temporal.container_job_attachments import storage_for
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from moonmind.workflows.temporal.worker_runtime import _container_job_artifact_reader
from tests.artifact_access import access_for_test
from tests.integration.security import test_application_access_jobs as access_jobs
from tests.integration.security import test_game_math_jobs as game_jobs
from tests.integration.security.test_admitted_assessment_journeys import (
    Session,
    assert_assessment_retains,
    assert_retained,
    finish,
    publisher,
)
from tests.unit.security.test_realtime_application_access import (
    admitted_scope,
    application_fixture,
    selected_realtime,
)

environment = game_jobs.environment
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


async def launch_cli_job(job, policy, key):
    """Replay the CLI's exact signed submission, then launch its persisted job."""
    session = Session(job.environment, job.monkeypatch, workspace_id=key)
    session.service = {"temporal": job.temporal, "backend_settings": job.settings}
    raw_spec = policy.model_dump_json(by_alias=True).encode()
    ref = await game_jobs.owned(job.environment, raw_spec)
    await job.environment.service.link_artifact(
        artifact_id=ref,
        principal=game_jobs.owner_artifact_principal(game_jobs.OWNER),
        execution_ref={
            "namespace": "default",
            "workflow_id": "assessment",
            "run_id": "run",
            "link_type": "input.container-job",
        },
    )
    spec_path = job.tmp_path / "job.json"
    spec_path.write_text(
        json.dumps(
            {
                "workloadProfile": {
                    "profileRef": "application-access",
                    "mode": "evaluate",
                    "arguments": {"spec": "spec/spec.json", "maxSeconds": 10},
                },
                "inputAttachments": [
                    {"name": "spec", "artifactRef": ref, "fileName": "spec.json"}
                ],
                "resources": {"cpuMillis": 1000, "memoryMiB": 512},
                "timeoutSeconds": 600,
            }
        )
    )
    sent = []

    def capture(client, tool, arguments):
        if tool == "container.submit":
            sent.append((client._endpoint, client._authorization_headers(), arguments))
            return {"jobId": "pending-replay"}
        if tool == "container.status":
            return {"state": "succeeded"}
        raise ContainerJobCliError("auxiliary CLI reads are outside this fixture")

    job.monkeypatch.setattr(ContainerJobMcpClient, "call", capture)
    result = CliRunner().invoke(app, ["container", "run", "--spec", str(spec_path)])
    assert result.exit_code == 0, result.output
    ((endpoint, headers, arguments),) = sent
    async with AsyncClient(
        transport=ASGITransport(app=session.app), base_url="http://api:8000"
    ) as client:
        response = await client.post(
            urlsplit(endpoint).path,
            headers=headers,
            json={"tool": "container.submit", "arguments": arguments},
        )
    assert response.status_code == 200, response.text
    started = job.temporal.start_container_job.call_args.args[0]
    assert started.job_id == response.json()["result"]["jobId"]
    assert started.request.source.source == "omnigent"

    async def daemon(argv):
        job.commands.append(tuple(argv))
        if argv[0] == "inspect":
            return 1, b"", b"no such container"
        return 0, b"", b""

    job.backend = DockerContainerJobBackend(
        workspace_root=job.tmp_path,
        command_runner=daemon,
        evidence_publisher=publisher(job.environment, job.published),
        artifact_reader=_container_job_artifact_reader(job.environment.service),
        settings=job.settings,
    )
    workspace = job.tmp_path / "temporal_sandbox" / key / "repo"
    workspace.mkdir(parents=True)
    job.request = ContainerJobActivityRequest(
        jobId=started.job_id,
        owner=started.owner,
        ownershipToken=started.ownership_token,
        request=started.request,
        resolvedWorkspaceRef=str(workspace),
        resolvedImageRef=game_jobs.IMAGE,
    )
    await game_jobs.create_container_in_activity(
        job.backend, job.request, job.environment, job.monkeypatch
    )
    job.storage = storage_for(job.tmp_path, started.job_id)
    assert (job.storage.inputs / "spec/spec.json").read_bytes() == raw_spec
    create = [c for c in job.commands if c[0] == "create"][-1]
    rendered_env = dict(
        create[index + 1].split("=", 1)
        for index, value in enumerate(create[:-1])
        if value == "--env"
    )
    return list(create[create.index(game_jobs.IMAGE) + 1 :]), raw_spec, rendered_env


@pytest.mark.parametrize("protocol", ["websocket", "signalr-json"])
@pytest.mark.parametrize("vulnerable", [False, True])
async def test_real_realtime_cli_capture_survives_collection_and_report_delivery(
    environment,
    tmp_path,
    monkeypatch,
    protocol,
    vulnerable,
):
    policy = selected_realtime(protocol, "anon")
    scope = admitted_scope(policy)
    monkeypatch.setattr(access_jobs, "target_scope", lambda: scope)
    monkeypatch.setattr(access_jobs, "ORIGIN", scope.web.origin)
    job = access_jobs.Job(environment, tmp_path, monkeypatch)
    argv, raw_spec, rendered_env = await launch_cli_job(job, policy, "realtime-cli")
    assert json.loads(rendered_env[TARGET_SCOPE_ENV]) == scope.model_dump(
        mode="json", by_alias=True
    )
    for old, new in [
        ("/inputs/", f"{job.storage.inputs}/"),
        ("/outputs/", f"{job.storage.outputs}/"),
    ]:
        argv = [arg.replace(old, new) for arg in argv]
    async with application_fixture(protocol, vulnerable=vulnerable) as state:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            *argv,
            cwd=game_jobs.REPOSITORY,
            env={
                **os.environ,
                **rendered_env,
                "MOONMIND_EGRESS_POLICY_REF": "fixture",
                "MOONMIND_EGRESS_PROXY": state["proxy"],
            },
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 20)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        assert process.returncode == 0, (stdout, stderr)
        assert b"observation complete" in stdout
        assert len(state["messages"]) == 1
        await asyncio.wait_for(state["closed"].wait(), 1)
    raw_capture = (job.storage.outputs / "capture.json").read_bytes()
    capture = ApplicationAccessCapture.model_validate_json(raw_capture)
    assert capture.scope == scope and capture.max_seconds == 10
    assert capture.transport_budget.connections == 1
    assert capture.bundle.observations[0].transcript
    ref = await job.publish(terminal_state="succeeded", exit_code=process.returncode)
    report = json.loads(job.published[ref])
    refs = report["job"]["inputRefs"]
    bodies = {"spec": raw_spec, "capture": raw_capture}
    assert set(refs) == set(bodies)
    assert report["inputDigests"] == {
        name: hashlib.sha256(body).hexdigest() for name, body in bodies.items()
    }
    assert all(job.published[refs[name]] == body for name, body in bodies.items())
    for secret in [
        b"PRIVATE_MARKER",
        b"NEGOTIATION_SECRET",
        b"token-owner",
        b"token-other",
    ]:
        assert secret not in b"".join(job.published.values())
    await finish(job.backend, job.request, tmp_path)
    result = await job.assess(ref, "realtime-retained-review")
    assert not result["diagnostics"]
    assert result["counts"]["new"] == int(vulnerable)
    (summary,) = result["scannerSummaries"]
    assert summary["coverage"] == "complete"
    (case,) = summary["cases"]
    assert case["outcome"] == ("finding" if vulnerable else "pass")
    rendered_ref = game_jobs.recurring.report_ref(result)
    _, rendered = await environment.service.read(
        artifact_id=rendered_ref,
        access=access_for_test("workflow:assessment"),
    )
    assert b"Coverage: complete" in rendered
    assert b"application-access" in rendered
    assert protocol.encode() in rendered
    assert {ref, *refs.values()} <= await game_jobs.recurring.required(
        environment.service, rendered_ref
    )
    await assert_retained(environment, [ref, *refs.values()], job.published)
    await assert_assessment_retains(
        environment, ref, list(refs.values()), [scope.web.origin]
    )
    assert case["request"] == {
        "method": "GET",
        "path": "/ws",
        "protocol": protocol,
        "messageType": "read",
        "effect": "read_only",
        "realtimeCaseRef": "read",
    }
