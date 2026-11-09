"""Signed jobs execute the installed inventory runner through the native gateway.

The overlay uses production stage_runner and .pth installation, with the existing
CI Python dependency image. It qualifies those installed sources and transport,
not the separately released repository-security image or its upstream tools.
There are no runner monkeypatches or source mounts in the workload container.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.schemas.container_job_models import (
    AuxiliaryOutcome,
    ContainerJobActivityRequest,
    container_job_workflow_id,
)
from moonmind.security.service_inventory import MAX_SERVICE_INVENTORY_EXECUTION_BYTES
from moonmind.workflows.temporal import worker_runtime
from moonmind.workflows.temporal.container_job_attachments import storage_for
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from tests.integration.security import test_admitted_assessment_journeys as journeys
from tests.integration.security import test_native_scoped_egress_docker as native_docker
from tests.integration.security.test_admitted_assessment_journeys import (
    NO_GRANTS,
    Session,
    assert_retained,
    publisher,
)
from tests.integration.security.test_native_scoped_egress_docker import (
    docker_only,
)
from tests.integration.security.test_service_inventory_admission import admission_bundle

environment = journeys.environment
docker_lab = native_docker.docker_lab

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.integration_ci,
    docker_only,
]


async def fixture_target(lab, suffix, network, *, policy="native-fixtures"):
    name = lab.prefix + "-" + suffix
    lab.containers.append(name)
    await lab.run(
        "run",
        "-d",
        "--name",
        name,
        "--network",
        network,
        "--label",
        "moonmind.test.owner=" + lab.prefix,
        "--label",
        "moonmind.security.target=" + policy,
        "--mount",
        f"type=bind,src={lab.root / 'tests/integration/security/service_inventory_fixture.py'},dst=/fixture.py,readonly",
        "--entrypoint",
        "python",
        lab.image,
        "/fixture.py",
        "/tmp/smtp",
    )
    for _ in range(40):
        code, _, _ = await lab.runner(("exec", name, "test", "-f", "/tmp/smtp/ready"))
        if code == 0:
            break
        await asyncio.sleep(0.1)
    assert code == 0
    lab.targets.append(name)
    return name


async def activity_call(backend, method, request):
    activity = ActivityEnvironment()
    activity.info = replace(
        activity.info, workflow_id=container_job_workflow_id(request.job_id)
    )
    return await activity.run(getattr(backend, method), request)


@pytest.mark.parametrize("scenario", ["complete", "uncertain", "budget"])
async def test_installed_inventory_cli_mcp_job_retains_bounded_evidence_after_cleanup(
    docker_lab, environment, tmp_path, monkeypatch, scenario
):
    lab = docker_lab
    image = os.environ["MOONMIND_INVENTORY_TEST_IMAGE"]
    installed = json.loads(
        await lab.run(
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "python",
            image,
            "-I",
            "-c",
            "import importlib.metadata,json; from moonmind.security import service_inventory_runner as r; "
            "assert r.__file__.startswith('/opt/moonmind-scanner-python/'),r.__file__; "
            "print(json.dumps({'runner':r.__file__,'pydantic':importlib.metadata.version('pydantic'),"
            "'cryptography':importlib.metadata.version('cryptography')}))",
        )
    )
    network = await lab.network("inventory-target", policy="native-fixtures")
    target = await fixture_target(lab, "inventory-target", network)
    other_network = await lab.network("inventory-peer", policy="other-assessment")
    peer = await fixture_target(
        lab, "inventory-peer", other_network, policy="other-assessment"
    )
    settings, selected, scope = admission_bundle(
        network=network, target=target, image=image, scenario=scenario
    )
    session = Session(environment, monkeypatch, scope=scope)
    started = await session.submit(
        tmp_path,
        "service-inventory",
        selected,
        temporal=AsyncMock(),
        backend_settings=settings,
        authorizer=NO_GRANTS,
    )
    spec = started.request.spec
    assert spec.input_attachments == []
    assert spec.workspace_ref.workspace_id == "assess"
    assert spec.workspace_ref.relative_path == "repo"
    assert spec.entrypoint == ["python"]
    assert spec.command[:3] == [
        "-I",
        "-m",
        "moonmind.security.service_inventory_runner",
    ]
    published = {}
    # This directory, unlike driver's /tmp, has the same path on the daemon host.
    jobs = lab.artifacts / "jobs"
    jobs.mkdir()
    workspace = jobs / "temporal_sandbox/assess/repo"
    workspace.mkdir(parents=True)
    (workspace / "private.txt").write_text("unmounted-workspace-marker")
    monkeypatch.setattr(
        worker_runtime, "get_async_session_context", environment.sessions
    )
    backend = DockerContainerJobBackend(
        workspace_root=jobs,
        settings=settings,
        command_runner=lab.runner,
        evidence_publisher=publisher(environment, published),
        assessment_authority=worker_runtime._container_job_assessment_authority(
            settings
        ),
    )
    request = ContainerJobActivityRequest(
        jobId=started.job_id,
        owner=started.owner,
        ownershipToken=started.ownership_token,
        request=started.request,
        registryAuthorization=started.registry_authorization,
        targetAuthorization=started.target_authorization,
        resolvedImageRef=await lab.run(
            "image", "inspect", "--format", "{{.Id}}", image
        ),
    )
    resolved = await activity_call(backend, "resolve_workspace", request)
    request.resolved_workspace_ref = resolved.resolved_workspace_ref
    lease = backend._scoped_lease(request)
    lab.leases.append(lease)
    created = await activity_call(backend, "create_container", request)
    request.container_ref = created.container_ref
    request.egress_attestation_ref = created.diagnostics_ref
    lab.containers.append(request.container_ref)
    inspected = json.loads(
        await lab.run("inspect", "--format", "{{json .}}", request.container_ref)
    )
    assert set(inspected["NetworkSettings"]["Networks"]) == {lease.network_ref}
    assert {mount["Destination"] for mount in inspected["Mounts"]} == {
        "/outputs",
        "/tmp",
    }
    assert all(
        mount["Source"].startswith(str(storage_for(jobs, request.job_id).root))
        for mount in inspected["Mounts"]
    )
    assert inspected["HostConfig"]["CapDrop"] == ["ALL"]
    before = await lease.attest(
        workload=True, expected_image=request.resolved_image_ref
    )
    assert (
        f"MOONMIND_EGRESS_DEADLINE={before['expiresAt']}" in inspected["Config"]["Env"]
    )
    await activity_call(backend, "start_container", request)
    for _ in range(60):
        observed = await activity_call(backend, "observe_container", request)
        if observed.terminal_state:
            break
        await asyncio.sleep(0.1)
    assert observed.terminal_state == "succeeded" and observed.exit_code == 0
    request.terminal_state, request.exit_code = (
        observed.terminal_state,
        observed.exit_code,
    )
    request.started_at, request.finished_at = observed.started_at, observed.finished_at
    # Reattachment preserves the original lease and never replays the runner.
    after = await lease.prepare(timeout_seconds=9999)
    assert (after["expiresAt"], after["snapshotDigest"]) == (
        before["expiresAt"],
        before["snapshotDigest"],
    )
    await activity_call(backend, "start_container", request)
    await activity_call(backend, "publish_evidence", request)
    summaries = [
        (ref, json.loads(body))
        for ref, body in published.items()
        if b'"service-inventory/v1"' in body
    ]
    assert len(summaries) == 1, list(published)
    summary_ref, summary = summaries[0]
    assert all(b"unmounted-workspace-marker" not in body for body in published.values())
    native_ref, attestation_ref = summary["evidenceRef"], summary["attestationRef"]
    assert native_ref and attestation_ref
    assert len(published[native_ref]) <= MAX_SERVICE_INVENTORY_EXECUTION_BYTES
    native, attestation = (
        json.loads(published[native_ref]),
        json.loads(published[attestation_ref]),
    )
    records = {row["authority"].rsplit(":", 1)[1]: row for row in native["records"]}
    assert records["2525"]["reachability"] == "open"
    assert records["2525"]["observation"]["confidence"] == "unverified"
    assert records["2525"]["observation"]["source"] == "peer_banner"
    assert records["2526"]["reachability"] == "refused"
    assert not any("vulnerability" in row for row in records.values())
    expected = 2 if scenario == "budget" else 6 if scenario == "uncertain" else 3
    counters = attestation["nativeNetwork"]
    assert counters["attempts"] == expected
    assert counters["dialOutcomes"] == {"open": expected - 1, "refused": 1}
    assert counters["activeConnections"] == 0
    assert counters["bytesReserved"] <= selected["nativeNetwork"]["limits"]["maxBytes"]
    assert (
        attestation["deniedConnectionCount"] == attestation["transportErrorCount"] == 0
    )
    assert set(attestation["resolvedAddresses"]) == {"native.example.test"}
    if scenario == "complete":
        assert summary["status"] == "complete", summary["limitations"]
        assert summary["comparisonKey"]
        assert records["8080"]["httpStatus"] == 302
    elif scenario == "uncertain":
        assert (
            summary["status"] == "partial"
            and "incomplete_probe" in summary["limitations"]
        )
        assert records["8081"]["probeStatus"] == "malformed"
        for port in ("8082", "8083"):
            assert records[port]["reachability"] == "open"
            assert records[port]["probeStatus"] == "timed_out"
    else:
        assert (
            summary["status"] == "partial"
            and "run_incomplete" in summary["limitations"]
        )
        assert native["stopReason"] == "connection_budget_exhausted"
        assert records["8080"]["reachability"] == "untested"
    counts = await lab.counts(target)
    assert counts["2525"]["connections"] == 1
    assert counts["9090"]["connections"] == 0
    assert sum(row["connections"] for row in counts.values()) == expected - 1
    for port in ("8080", "8081"):
        for wire in counts[port]["requests"]:
            assert (
                wire
                == f"HEAD / HTTP/1.1\r\nHost: native.example.test:{port}\r\nConnection: close\r\n\r\n"
            )
    peer_counts = await lab.counts(peer)
    assert sum(row["connections"] for row in peer_counts.values()) == 0
    request.publication = AuxiliaryOutcome(state="succeeded")
    await activity_call(backend, "remove_container", request)
    cleaned = await activity_call(backend, "cleanup", request)
    assert cleaned.cleanup_succeeded is True
    lab.leases.remove(lease)
    assert not storage_for(jobs, request.job_id).root.exists()
    assert (await lab.runner(("inspect", lease.gateway_ref)))[0] != 0
    await assert_retained(
        environment, [summary_ref, native_ref, attestation_ref], published
    )
    # Independently owned fixture peers survive the job's owned cleanup.
    await lab.run("inspect", target, peer)
    (lab.artifacts / "installed-inventory.json").write_text(
        json.dumps(
            {
                "candidate": os.environ.get("MOONMIND_TEST_CANDIDATE"),
                "scenario": scenario,
                "installed": installed,
                "image": request.resolved_image_ref,
                "dependencyBase": os.environ["MOONMIND_SCOPED_TEST_IMAGE"],
                "summary": summary,
                "execution": native,
                "attestation": attestation,
                "targetCounters": counts,
                "unadmittedPeerCounters": peer_counts,
                "retainedAfterCleanup": [summary_ref, native_ref, attestation_ref],
            },
            indent=2,
        )
    )
