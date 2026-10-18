"""The controlled provider must execute inside admitted host authority."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tools import single_user_journey_provider as provider

MARKER = "first-run-generated:" + "a" * 32
REPORT = f"# Generated scratch report\n\nRequest: {MARKER}\nSHA-256: {hashlib.sha256(MARKER.encode()).hexdigest()}\n"


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv("FIRST_RUN_JOURNEY_PROJECT", "moonmind-test-first-run")
    binding = SimpleNamespace(
        bindingId="binding",
        hostLeaseRef="lease",
        hostLeaseGeneration=1,
        omnigentHostId="host",
        omnigentSessionId=None,
        phaseResults={},
    )
    sink = SimpleNamespace(binding=binding)

    async def created(session):
        binding.omnigentSessionId = session

    async def recorded(phase, payload):
        binding.phaseResults[phase] = payload

    sink.session_created = AsyncMock(side_effect=created)
    sink.record_phase = AsyncMock(side_effect=recorded)
    request = SimpleNamespace(
        instruction_ref=MARKER,
        resolved_skillset_ref=None,
        parameters={
            "omnigent": {
                "agent": {"agentId": "agent", "harnessOverride": "codex"},
                "session": {
                    "hostId": "host",
                    "hostType": "external",
                    "workspace": "/workspaces/run",
                },
            }
        },
        workspace_spec={"workspaceLocator": {"workspaceId": "workspace"}},
        step_execution=SimpleNamespace(
            workflow_id="workflow",
            run_id="run",
            logical_step_id="step",
            execution_ordinal=1,
        ),
    )
    client = SimpleNamespace(create_session=AsyncMock(return_value={"id": "session"}))
    lease = SimpleNamespace(
        runtimeBindingId="binding",
        omnigentHostId="host",
        launchGeneration=1,
        status="ready",
        cleanupHandle={"containerName": "admitted-container"},
    )
    leases = SimpleNamespace(get=AsyncMock(return_value=lease))
    docker = SimpleNamespace(
        inspect_container=AsyncMock(
            return_value={
                "Config": {
                    "Labels": {
                        "moonmind.runtime_binding_id": "binding",
                        "moonmind.host_lease_ref": "lease",
                        "moonmind.host_lease_generation": "1",
                    }
                }
            }
        ),
        run=AsyncMock(side_effect=[(0, "", ""), (0, REPORT, "")]),
    )
    artifacts = SimpleNamespace(
        write_json=AsyncMock(return_value="artifact:evidence"), read_text=AsyncMock()
    )
    return request, sink, client, leases, docker, artifacts


@pytest.mark.asyncio
async def test_real_session_command_readback_and_evidence(setup):
    request, sink, client, leases, docker, artifacts = setup
    result = await provider.ControlledProvider(client, leases, docker)(
        request, session_authority_sink=sink, artifact_gateway=artifacts
    )
    assert result.failure_class is None
    sink.session_created.assert_awaited_once_with("session")
    assert client.create_session.await_args.args[0]["host_id"] == "host"
    assert docker.run.await_args_list[0].args[0][:6] == [
        "docker",
        "exec",
        "-w",
        "/workspaces/run",
        "admitted-container",
        "python3",
    ]
    receipt = artifacts.write_json.await_args.kwargs
    assert receipt["link_type"] == "evidence.first_run.compute"
    assert receipt["payload"]["sha256"] == hashlib.sha256(REPORT.encode()).hexdigest()
    assert receipt["payload"]["sessionId"] == "session"
    assert "saved" not in sink.binding.phaseResults


@pytest.mark.asyncio
async def test_retry_reuses_accepted_compute(setup):
    request, sink, client, leases, docker, artifacts = setup
    driver = provider.ControlledProvider(client, leases, docker)
    await driver(request, session_authority_sink=sink, artifact_gateway=artifacts)
    await driver(request, session_authority_sink=sink, artifact_gateway=artifacts)
    assert docker.run.await_count == 2
    client.create_session.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem", ["project", "marker", "host", "owner", "exit", "readback"]
)
async def test_fails_closed(setup, monkeypatch, problem):
    request, sink, client, leases, docker, artifacts = setup
    if problem == "project":
        monkeypatch.setenv("FIRST_RUN_JOURNEY_PROJECT", "moonmind-production")
    elif problem == "marker":
        request.instruction_ref = "ordinary task"
    elif problem == "host":
        leases.get.return_value.runtimeBindingId = "other"
    elif problem == "owner":
        docker.inspect_container.return_value["Config"]["Labels"][
            "moonmind.runtime_binding_id"
        ] = "other"
    elif problem == "exit":
        docker.run.side_effect = [(1, "", "command failed")]
    elif problem == "readback":
        docker.run.side_effect = [(0, "", ""), (0, "incorrect", "")]
    with pytest.raises(RuntimeError):
        await provider.ControlledProvider(client, leases, docker)(
            request, session_authority_sink=sink, artifact_gateway=artifacts
        )
    artifacts.write_json.assert_not_awaited()
    if problem in {"project", "marker", "host", "owner"}:
        client.create_session.assert_not_awaited()
        docker.run.assert_not_awaited()


@pytest.fixture
def cleanup_setup(tmp_path, monkeypatch):
    from moonmind.workflows.temporal.runtime.workspace_locators import (
        SandboxWorkspaceRecord,
        SandboxWorkspaceRecordStore,
    )

    monkeypatch.setenv("FIRST_RUN_JOURNEY_PROJECT", "moonmind-test-first-run")
    step_id = "workflow:run:step:execution:1"
    workspace_id = hashlib.sha256(f"workflow:{step_id}".encode()).hexdigest()[:24]
    root = tmp_path / "workspaces"
    workspace = root / "temporal_sandbox" / workspace_id
    (workspace / "repo").mkdir(parents=True)
    (workspace / "repo" / "report.md").write_text(REPORT)
    records = SandboxWorkspaceRecordStore(root)
    records.ensure(SandboxWorkspaceRecord(workspace_id, "workflow", step_id, "repo"))
    evidence = {
        "bindingId": "binding",
        "workspaceId": workspace_id,
        "workflowId": "workflow",
        "runId": "run",
        "logicalStepId": "step",
        "executionOrdinal": 1,
        "marker": MARKER,
        "sha256": hashlib.sha256(REPORT.encode()).hexdigest(),
        "sessionId": "session",
        "hostId": "host",
        "exitCode": 0,
        "outputPath": "report.md",
    }
    saved = {
        "checkpointRef": "artifact:checkpoint",
        "archiveRef": "artifact:archive",
        "archiveDigest": "sha256:" + hashlib.sha256(b"archive").hexdigest(),
    }
    binding = SimpleNamespace(
        bindingId="binding",
        state="cleaned",
        hostLeaseRef="lease",
        hostLeaseGeneration=1,
        omnigentHostId="host",
        omnigentSessionId="session",
        phaseResults={
            provider.PHASE: evidence,
            "compute": {"summary": "done"},
            "saved": saved,
            "workspace": {
                "stepExecution": {"workflowId": "workflow", "stepExecutionId": step_id},
                "workspaceSpec": {
                    "workspaceLocator": {
                        "kind": "sandbox",
                        "workspaceId": workspace_id,
                        "relativePath": "repo",
                    }
                },
            },
        },
    )
    bindings = SimpleNamespace(get=AsyncMock(return_value=binding))
    lease = SimpleNamespace(
        runtimeBindingId="binding",
        omnigentHostId="host",
        launchGeneration=1,
        status="cleaned",
        cleanupHandle={"containerName": "admitted-container"},
    )
    leases = SimpleNamespace(get=AsyncMock(return_value=lease))
    docker = SimpleNamespace(run=AsyncMock(return_value=(0, "", "")))
    checkpoint = {
        "boundary": "after_execution",
        "source": {
            key: evidence[key]
            for key in ["workflowId", "runId", "logicalStepId", "executionOrdinal"]
        },
        "workspace": saved,
    }
    artifacts = SimpleNamespace(
        read_bytes=AsyncMock(side_effect=[json.dumps(checkpoint).encode(), b"archive"])
    )
    state = {"generatedOutput": {**evidence, **saved}}
    return state, bindings, leases, docker, artifacts, root, workspace


@pytest.mark.asyncio
async def test_cleanup_removes_only_verified_saved_workspace(cleanup_setup):
    state, bindings, leases, docker, artifacts, root, workspace = cleanup_setup
    other = root / "temporal_sandbox" / "other"
    other.mkdir()
    result = await provider.cleanup_saved_workspace(
        state,
        bindings=bindings,
        leases=leases,
        docker=docker,
        artifacts=artifacts,
        workspace_root=root,
    )
    assert result == {
        "bindingId": "binding",
        "workspaceId": state["generatedOutput"]["workspaceId"],
        "workspaceRemoved": True,
        "hostRemoved": True,
    }
    assert not workspace.exists()
    assert other.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem",
    [
        "active",
        "missing_save",
        "mismatched_digest",
        "bad_archive_bytes",
        "existing_host",
        "daemon_error",
        "wrong_workspace",
        "symlink",
        "records_symlink",
        "compute_failed",
    ],
)
async def test_cleanup_retains_workspace_without_complete_proof(
    cleanup_setup, problem, tmp_path
):
    state, bindings, leases, docker, artifacts, root, workspace = cleanup_setup
    if problem == "active":
        bindings.get.return_value.state = "session_active"
    elif problem == "missing_save":
        del bindings.get.return_value.phaseResults["saved"]
    elif problem == "mismatched_digest":
        state["generatedOutput"]["archiveDigest"] = "sha256:" + "0" * 64
    elif problem == "bad_archive_bytes":
        checkpoint = next(artifacts.read_bytes.side_effect)
        artifacts.read_bytes.side_effect = [checkpoint, b"broken"]
    elif problem == "existing_host":
        docker.run.return_value = (0, "container-id\n", "")
    elif problem == "daemon_error":
        docker.run.return_value = (1, "", "daemon unavailable")
    elif problem == "wrong_workspace":
        state["generatedOutput"]["workspaceId"] = "../../outside"
    elif problem == "compute_failed":
        bindings.get.return_value.phaseResults["compute"][
            "failureClass"
        ] = "integration_error"
    elif problem == "records_symlink":
        records = root / "temporal_sandbox" / ".workspace_records"
        moved = tmp_path / "outside-records"
        records.rename(moved)
        records.symlink_to(moved, target_is_directory=True)
    elif problem == "symlink":
        moved = tmp_path / "outside"
        workspace.rename(moved)
        workspace.symlink_to(moved, target_is_directory=True)
    with pytest.raises(RuntimeError):
        await provider.cleanup_saved_workspace(
            state,
            bindings=bindings,
            leases=leases,
            docker=docker,
            artifacts=artifacts,
            workspace_root=root,
        )
    assert workspace.exists()


def test_host_script_computes_and_reconciles_without_rewriting(tmp_path):
    import subprocess
    import sys

    command = [sys.executable, "-c", provider._COMPUTE, MARKER]
    subprocess.run(command, cwd=tmp_path, check=True)
    report = tmp_path / "report.md"
    assert report.read_bytes() == REPORT.encode()
    modified = report.stat().st_mtime_ns
    subprocess.run(command, cwd=tmp_path, check=True)
    assert report.stat().st_mtime_ns == modified
    report.write_text("tampered")
    assert subprocess.run(command, cwd=tmp_path, capture_output=True).returncode != 0


@pytest.mark.asyncio
async def test_prompt_artifact_marker_is_resolved_from_real_message(setup):
    request, sink, client, leases, docker, artifacts = setup
    request.instruction_ref = None
    request.parameters["omnigent"]["prompt"] = {
        "instructionRef": "artifact:instructions"
    }
    artifacts.read_text.return_value = "Generate the requested report: " + MARKER
    await provider.ControlledProvider(client, leases, docker)(
        request, session_authority_sink=sink, artifact_gateway=artifacts
    )
    artifacts.read_text.assert_awaited_once_with("artifact:instructions")


@pytest.mark.asyncio
async def test_evidence_write_retry_does_not_repeat_host_command(setup):
    request, sink, client, leases, docker, artifacts = setup
    driver = provider.ControlledProvider(client, leases, docker)
    artifacts.write_json.side_effect = [
        RuntimeError("artifact temporarily unavailable"),
        "artifact:evidence",
    ]
    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        await driver(request, session_authority_sink=sink, artifact_gateway=artifacts)
    await driver(request, session_authority_sink=sink, artifact_gateway=artifacts)
    assert docker.run.await_count == 2
    client.create_session.assert_awaited_once()
    assert sink.binding.phaseResults["firstRunGeneratedEvidence"] == {
        "artifactRef": "artifact:evidence"
    }


def test_cleanup_cli_prints_receipt_without_requiring_writable_mount(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("FIRST_RUN_JOURNEY_PROJECT", "moonmind-test-first-run")
    receipt = {
        "bindingId": "binding",
        "workspaceId": "workspace",
        "workspaceRemoved": True,
        "hostRemoved": True,
    }
    cleanup = AsyncMock(return_value=receipt)
    monkeypatch.setattr(provider, "_cleanup_from_file", cleanup)
    state = tmp_path / "state.json"
    monkeypatch.setattr("sys.argv", ["provider", "cleanup", "--state-file", str(state)])
    provider.main()
    assert json.loads(capsys.readouterr().out) == receipt
    cleanup.assert_awaited_once_with(state)


@pytest.mark.asyncio
async def test_session_creation_retries_same_provider_key_after_sink_ack_lost(setup):
    request, sink, client, leases, docker, artifacts = setup
    original_created = sink.session_created.side_effect
    deliveries = 0

    async def persist_session(session):
        nonlocal deliveries
        deliveries += 1
        if deliveries == 1:
            raise RuntimeError("session authority commit unavailable")
        await original_created(session)

    sink.session_created.side_effect = persist_session
    driver = provider.ControlledProvider(client, leases, docker)
    with pytest.raises(RuntimeError, match="commit unavailable"):
        await driver(request, session_authority_sink=sink, artifact_gateway=artifacts)
    docker.run.assert_not_awaited()
    await driver(request, session_authority_sink=sink, artifact_gateway=artifacts)
    first, second = [call.args[0] for call in client.create_session.await_args_list]
    assert first.get("idempotency_key")
    assert first["idempotency_key"] == second["idempotency_key"]
    assert sink.binding.omnigentSessionId == "session"
    assert docker.run.await_count == 2


@pytest.mark.asyncio
async def test_cleanup_accepts_equivalent_save_artifact_references(cleanup_setup):
    state, bindings, leases, docker, artifacts, root, workspace = cleanup_setup
    state["generatedOutput"]["checkpointRef"] = "artifact:prior-checkpoint"
    state["generatedOutput"]["archiveRef"] = "artifact:prior-archive"
    receipt = await provider.cleanup_saved_workspace(
        state,
        bindings=bindings,
        leases=leases,
        docker=docker,
        artifacts=artifacts,
        workspace_root=root,
    )
    assert receipt["workspaceRemoved"]
    assert [call.args[0] for call in artifacts.read_bytes.await_args_list] == [
        "artifact:checkpoint",
        "artifact:archive",
    ]
