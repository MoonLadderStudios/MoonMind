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
    from moonmind.omnigent.bootstrap.opencode import ZEN_FREE_QUALIFIED

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
            "model": ZEN_FREE_QUALIFIED,
            "omnigent": {
                "agent": {"agentId": "agent", "harnessOverride": "opencode-native"},
                "session": {
                    "hostId": "host",
                    "hostType": "external",
                    "workspace": "/workspaces/run",
                    "modelOverride": ZEN_FREE_QUALIFIED,
                },
            },
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


@pytest.mark.asyncio
async def test_cleanup_serializes_deletion_with_new_workspace_claim(
    cleanup_setup, monkeypatch
):
    import asyncio
    import threading

    from moonmind.workflows.temporal.runtime.workspace_locators import (
        SandboxWorkspaceRecordStore,
        WorkspaceLocatorResolutionError,
    )

    state, bindings, leases, docker, artifacts, root, workspace = cleanup_setup
    workspace_id = state["generatedOutput"]["workspaceId"]
    records = SandboxWorkspaceRecordStore(root)
    deletion_started = threading.Event()
    release_deletion = threading.Event()
    reader_started = threading.Event()
    reader_finished = threading.Event()
    original_rmtree = provider.shutil.rmtree

    def paused_rmtree(path):
        deletion_started.set()
        assert release_deletion.wait(3), "test did not release workspace deletion"
        original_rmtree(path)

    def admit_reader():
        reader_started.set()
        try:
            records.claim_existing_workspace(
                workspace_id,
                SimpleNamespace(grant_id="new-reader", mode="read_only"),
                grantee_workflow_id="reader-workflow",
            )
        except WorkspaceLocatorResolutionError:
            return "workspace unavailable"
        else:
            return "admitted"
        finally:
            reader_finished.set()

    monkeypatch.setattr(provider.shutil, "rmtree", paused_rmtree)
    cleanup = asyncio.create_task(
        provider.cleanup_saved_workspace(
            state,
            bindings=bindings,
            leases=leases,
            docker=docker,
            artifacts=artifacts,
            workspace_root=root,
        )
    )
    reader = None
    try:
        assert await asyncio.to_thread(deletion_started.wait, 3)
        reader = asyncio.create_task(asyncio.to_thread(admit_reader))
        assert await asyncio.to_thread(reader_started.wait, 3)
        # A real claimant must wait behind deletion rather than enter between
        # the final active-reader check and filesystem removal.
        admitted_during_deletion = await asyncio.to_thread(reader_finished.wait, 0.2)
    finally:
        release_deletion.set()
        cleanup_receipt = await cleanup
        assert cleanup_receipt["workspaceRemoved"] is True
    assert reader is not None
    outcome = await reader
    assert not admitted_during_deletion
    assert outcome == "workspace unavailable"
    assert not workspace.exists()
    assert records.load(workspace_id) is None
    assert records._claims_dir(workspace_id).with_suffix(".claims.lock").exists()


def test_worker_entrypoint_imports_real_runtime_in_fresh_process():
    import os
    from pathlib import Path
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent(
        """
        import importlib.abc
        import importlib.machinery
        import runpy
        import sys

        class RuntimeLoader(importlib.abc.Loader):
            def __init__(self, wrapped):
                self.wrapped = wrapped

            def create_module(self, spec):
                return self.wrapped.create_module(spec)

            def exec_module(self, module):
                self.wrapped.exec_module(module)
                async def main():
                    from moonmind.omnigent import production
                    assert production.run_omnigent_execution.__name__ == "controlled_provider"
                    print("NORMAL_WORKER_MAIN_REACHED")
                module.main_async = main
                module._configure_worker_logging = lambda **kwargs: None

        class RuntimeFinder(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                if fullname != "moonmind.workflows.temporal.worker_runtime":
                    return None
                spec = importlib.machinery.PathFinder.find_spec(fullname, path)
                spec.loader = RuntimeLoader(spec.loader)
                return spec

        sys.meta_path.insert(0, RuntimeFinder())
        sys.argv = ["tools/single_user_journey_provider.py", "worker"]
        runpy.run_path(sys.argv[0], run_name="__main__")
    """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(provider.__file__).resolve().parents[1],
        env={**os.environ, "FIRST_RUN_JOURNEY_PROJECT": "moonmind-test-first-run"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "NORMAL_WORKER_MAIN_REACHED" in result.stdout


def test_cleanup_entrypoint_imports_real_dependencies_in_fresh_process(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    import textwrap

    state = tmp_path / "state.json"
    state.write_text("{}")
    script = textwrap.dedent(
        """
        import asyncio
        from pathlib import Path
        import sys
        from tools import single_user_journey_provider as provider

        async def cleanup(state, **dependencies):
            assert type(dependencies["bindings"]).__name__ == "DbRuntimeBindingStore"
            assert type(dependencies["leases"]).__name__ == "DbOmnigentHostLeaseRepository"
            assert type(dependencies["artifacts"]).__name__ == "TemporalOmnigentArtifactGateway"
            print("CLEANUP_DEPENDENCIES_READY")
            return {}

        provider.cleanup_saved_workspace = cleanup
        asyncio.run(provider._cleanup_from_file(Path(sys.argv[1])))
    """
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(state)],
        cwd=Path(provider.__file__).resolve().parents[1],
        env={**os.environ, "WORKFLOW_WORKSPACE_ROOT": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "CLEANUP_DEPENDENCIES_READY" in result.stdout


@pytest.fixture
def controlled_catalog(monkeypatch):
    from moonmind.omnigent import production
    from moonmind.omnigent.host_services import attestation

    original = AsyncMock(return_value=({"models": []}, "real-provider"))
    monkeypatch.setattr(attestation, "_read_exact_host_model_options", original)
    monkeypatch.setattr(
        production, "DockerOmnigentHostAttestor", attestation.DockerOmnigentHostAttestor
    )
    monkeypatch.setenv("FIRST_RUN_JOURNEY_PROJECT", "moonmind-test-first-run")
    provider.install_controlled_catalog()
    return attestation, production, original


@pytest.mark.asyncio
async def test_controlled_catalog_exact_host_and_real_membership(
    controlled_catalog, setup, monkeypatch
):
    import subprocess
    import sys
    from moonmind.omnigent.bootstrap.opencode import ZEN_FREE_QUALIFIED

    attestation, production, original = controlled_catalog
    request, _sink, _client, _leases, _docker, artifacts = setup
    backend = SimpleNamespace(run=AsyncMock())

    async def execute_probe(argv, **kwargs):
        assert argv[:5] == [
            "docker",
            "exec",
            "exact-admitted-host",
            "/opt/venv/bin/python",
            "-c",
        ]
        result = subprocess.run(
            [sys.executable, "-c", argv[5]], capture_output=True, text=True, check=True
        )
        return result.returncode, result.stdout, result.stderr

    backend.run.side_effect = execute_probe

    async def real_boundary(self, **kwargs):
        return await attestation._read_exact_host_model_options(
            backend=backend,
            client=None,
            container_name="exact-admitted-host",
            omnigent_host_id="host",
            harness_id=kwargs["plan"].payload.harnessId,
        )

    monkeypatch.setattr(attestation.DockerOmnigentHostAttestor, "attest", real_boundary)
    wrapped = production.DockerOmnigentHostAttestor(
        backend=backend, client=None, artifacts=artifacts
    )
    payload, source = await wrapped.attest(
        request=request,
        plan=SimpleNamespace(payload=SimpleNamespace(harnessId="opencode-native")),
    )
    assert payload == {"models": [{"id": ZEN_FREE_QUALIFIED}]}
    assert source == "exact-host-controlled-first-run-provider"
    original.assert_not_awaited()
    for model, expected in [
        (ZEN_FREE_QUALIFIED, True),
        ("opencode/unsupported", False),
        ("", False),
    ]:
        _, present = await attestation.confirm_exact_host_model(
            backend=backend,
            container_name="exact-admitted-host",
            harness_id="opencode-native",
            model_options=payload,
            selected_model=model,
        )
        assert present is expected
    _, present = await attestation.confirm_exact_host_model(
        backend=backend,
        container_name="exact-admitted-host",
        harness_id="opencode-native",
        model_options={"models": []},
        selected_model=ZEN_FREE_QUALIFIED,
    )
    assert not present
    # No active request scope may inherit the controlled provider catalog.
    assert (
        await attestation._read_exact_host_model_options(
            backend=backend,
            client=None,
            container_name="another-host",
            omnigent_host_id="other",
            harness_id="opencode-native",
        )
    )[1] == "real-provider"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "harness,marked", [("opencode-native", False), ("codex-native", True)]
)
async def test_catalog_delegates_unrelated_requests(
    controlled_catalog, setup, monkeypatch, harness, marked
):
    attestation, production, original = controlled_catalog
    request, _sink, _client, _leases, docker, artifacts = setup
    if not marked:
        request.instruction_ref = "ordinary task"

    async def real_boundary(self, **kwargs):
        return await attestation._read_exact_host_model_options(
            backend=docker,
            client=None,
            container_name="host",
            omnigent_host_id="host",
            harness_id=harness,
        )

    monkeypatch.setattr(attestation.DockerOmnigentHostAttestor, "attest", real_boundary)
    wrapped = production.DockerOmnigentHostAttestor(
        backend=docker, client=None, artifacts=artifacts
    )
    assert (
        await wrapped.attest(
            request=request,
            plan=SimpleNamespace(payload=SimpleNamespace(harnessId=harness)),
        )
    )[1] == "real-provider"
    original.assert_awaited_once()
    docker.run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response,code",
    [
        ((1, "", "private-provider-diagnostic"), "OMNIGENT_MODEL_UNAVAILABLE"),
        ((0, "not-json", ""), "OMNIGENT_MODEL_UNAVAILABLE"),
        ((0, '{"models": {}}', ""), "OMNIGENT_MODEL_UNAVAILABLE"),
        (
            (
                97,
                "",
                "moonmind-attestation-substrate-unavailable: ImportError: missing helper",
            ),
            "OMNIGENT_HARNESS_BUILD_MISMATCH",
        ),
    ],
)
async def test_catalog_preserves_probe_failures(
    controlled_catalog, setup, monkeypatch, response, code
):
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

    attestation, production, original = controlled_catalog
    request, _sink, _client, _leases, docker, artifacts = setup
    docker.run.side_effect = None
    docker.run.return_value = response

    async def real_boundary(self, **kwargs):
        return await attestation._read_exact_host_model_options(
            backend=docker,
            client=None,
            container_name="exact-host",
            omnigent_host_id="host",
            harness_id="opencode-native",
        )

    monkeypatch.setattr(attestation.DockerOmnigentHostAttestor, "attest", real_boundary)
    wrapped = production.DockerOmnigentHostAttestor(
        backend=docker, client=None, artifacts=artifacts
    )
    with pytest.raises(HarnessPlatformError) as error:
        await wrapped.attest(
            request=request,
            plan=SimpleNamespace(payload=SimpleNamespace(harnessId="opencode-native")),
        )
    assert str(error.value.code) == code
    assert "private-provider-diagnostic" not in str(error.value)
    # Even failure must reset the task-local scope.
    await attestation._read_exact_host_model_options(
        backend=docker,
        client=None,
        container_name="other",
        omnigent_host_id="other",
        harness_id="opencode-native",
    )
    original.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["model", "session", "harness", "missing"])
async def test_execution_rejects_unadvertised_model_before_effects(setup, mismatch):
    request, sink, client, leases, docker, artifacts = setup
    if mismatch == "model":
        request.parameters["model"] = "opencode/unsupported"
    elif mismatch == "session":
        request.parameters["omnigent"]["session"][
            "modelOverride"
        ] = "opencode/unsupported"
    elif mismatch == "harness":
        request.parameters["omnigent"]["agent"]["harnessOverride"] = "codex-native"
    else:
        del request.parameters["model"]
    with pytest.raises(RuntimeError, match="supported model"):
        await provider.ControlledProvider(client, leases, docker)(
            request, session_authority_sink=sink, artifact_gateway=artifacts
        )
    client.create_session.assert_not_awaited()
    leases.get.assert_not_awaited()
    docker.run.assert_not_awaited()
