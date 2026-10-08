"""Legacy GitHub hosts preserve current work before credential-only replacement."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.omnigent.execution_profiles import compile_effective_launch
from moonmind.omnigent.host_failures import OmnigentOAuthHostError
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from tests.helpers.github_projection import projection_reservation, reserve_projection
from tests.unit.omnigent.test_oauth_profile_lifecycle import (
    _binding,
    _egress_attestation,
    _host_lease,
)


@pytest.mark.asyncio
async def test_legacy_host_without_current_save_cannot_return_ready(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OMNIGENT_IMAGE_REF", "example.test/omnigent@sha256:" + "1" * 64)
    for name in ("OMNIGENT_HOST_IMAGE_REF", "OMNIGENT_SHARED_HOST_IMAGE_REF"):
        monkeypatch.setenv(name, "example.test/host@sha256:" + "2" * 64)
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=tmp_path
    )
    runtime.container_exists = AsyncMock(return_value=True)
    runtime.assert_container_owned = AsyncMock()
    runtime._existing_github_projection_is_compatible = AsyncMock(return_value=False)
    runtime._run = AsyncMock()
    launch = compile_effective_launch(
        profile_ref="omnigent-codex@1",
        policy_ref="codex-on-demand@1",
        provider_profile_id="codex",
    )
    with pytest.raises(OmnigentOAuthHostError) as raised:
        await runtime._launch_on_demand(
            binding=_binding(),
            host_lease=_host_lease(),
            container_name="mm-host-lease-1",
            workspace_source=tmp_path,
            skill_projection=tmp_path / "skills",
            runtime_scripts=tmp_path,
            current_step_execution_id="step-1",
            github_token="currentSelectedToken",
            github_projection_reservation=projection_reservation(
                _host_lease().lease_id
            ),
            recovery_request=SimpleNamespace(),
            recovery_store=SimpleNamespace(validate_github_projection=AsyncMock()),
            effective_launch=launch,
            egress_attestation=_egress_attestation(),
        )
    assert raised.value.code == "OMNIGENT_GITHUB_PROJECTION_REFRESH_FAILED"
    runtime._run.assert_not_awaited()


async def _recovery_fixture(tmp_path, monkeypatch):
    import hashlib
    import json
    import os
    import shutil
    import subprocess

    from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
    from moonmind.omnigent.execute import _first_message_marker
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
    from moonmind.workflows.temporal.runtime.workspace_locators import (
        SandboxWorkspaceRecord,
        SandboxWorkspaceRecordStore,
    )
    from tests.unit.omnigent.test_oauth_profile_lifecycle import _init_source_repo

    monkeypatch.setenv("OMNIGENT_IMAGE_REF", "example.test/omnigent@sha256:" + "1" * 64)
    for name in ("OMNIGENT_HOST_IMAGE_REF", "OMNIGENT_SHARED_HOST_IMAGE_REF"):
        monkeypatch.setenv(name, "example.test/host@sha256:" + "2" * 64)
    step_id = "workflow-1:run-1:implement:execution:1"
    workspace_id = hashlib.sha256(f"workflow-1:{step_id}".encode()).hexdigest()[:24]
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    _init_source_repo(workspace)
    (workspace / "README.md").write_text("current dirty work\n")
    (workspace / "untracked.txt").write_text("new saved work\n")
    SandboxWorkspaceRecordStore(tmp_path).ensure(
        SandboxWorkspaceRecord(
            workspace_id,
            "workflow-1",
            step_id,
            "repo",
        )
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="workflow-1",
        idempotencyKey="legacy-retry",
        executionProfileRef="codex",
        workspaceSpec={
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": workspace_id,
                "relativePath": "repo",
            }
        },
        stepExecution={
            "workflowId": "workflow-1",
            "runId": "run-1",
            "logicalStepId": "implement",
            "executionOrdinal": 1,
            "stepExecutionId": step_id,
            "runtimeContextPolicy": "fresh_agent_run",
        },
    )
    lease = _host_lease()
    row = SimpleNamespace(
        host_lease_ref=lease.lease_id,
        omnigent_host_id="host-1",
        omnigent_session_id="session-1",
        bridge_session_id="bridge-1",
        first_message_state="posted",
        first_message_posted_at=True,
        first_message_post_attempted_at=True,
        metadata_={},
    )
    state = {
        "running": True,
        "present": True,
        "events": [],
        "snapshots": [],
        "after_save": None,
        "cpu": {
            "sourceContainerId": "a" * 64,
            "nanoCpus": 1500000000,
            "cpuQuota": 0,
            "cpuPeriod": 0,
            "cpusetCpus": "0-1",
        },
        "stop_error": None,
        "stop_write": None,
    }
    state["identity"] = {
        "containerId": "a" * 64,
        "name": "/mm-host-lease-1",
        "kind": "omnigent-oauth-host",
        "hostLeaseRef": lease.lease_id,
        "credentialGeneration": str(lease.credential_generation),
        "providerProfileId": lease.provider_profile_id,
    }
    gh = shutil.which("gh")
    home = tmp_path / "home"
    reservation = projection_reservation(lease.lease_id)
    reserve_projection(home / ".cache/moonmind-xdg/gh", reservation)
    environment = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".cache/moonmind-xdg"),
        "PATH": os.environ["PATH"],
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GH_PROMPT_DISABLED": "1",
    }
    snapshot = {
        "id": "session-1",
        "host_id": "host-1",
        "status": "idle",
        "runner_online": True,
        "items": [
            {
                "id": "user-1",
                "type": "message",
                "data": {
                    "role": "user",
                    "content": [{"text": _first_message_marker(request=request)}],
                },
            },
            {
                "id": "answer-1",
                "type": "message",
                "data": {
                    "role": "assistant",
                    "content": [{"text": "Saved current progress."}],
                },
            },
        ],
    }

    from moonmind.omnigent.execute import _await_marked_turn_terminal

    state["terminal_waits"] = 0
    state["quiet_seconds"] = 0

    async def bounded_terminal(**kwargs):
        state["terminal_waits"] += 1
        return await _await_marked_turn_terminal(
            **kwargs,
            quiet_period_seconds=state["quiet_seconds"],
            tool_only_quiet_period_seconds=state["quiet_seconds"],
            interval_seconds=0.1,
        )

    monkeypatch.setattr(
        "moonmind.omnigent.execute._await_marked_turn_terminal", bounded_terminal
    )

    async def get_session(_session):
        assert _session == "session-1"
        observed = state["snapshots"].pop(0) if state["snapshots"] else snapshot
        if observed.get("fixtureWrite"):
            (workspace / "untracked.txt").write_text(observed["fixtureWrite"])
        return observed

    class Store:

        async def validate_github_projection(self, **kwargs):
            assert kwargs["reservation"] == reservation

        async def reserve_github_projection(self, **kwargs):
            return reservation

        async def get_existing(self, key):
            assert key == request.idempotency_key
            return row

        async def record_host_credential_recovery(
            self, *, phase, checkpoint=None, **kwargs
        ):
            assert kwargs["request"] is request
            assert kwargs["host_lease_ref"] == lease.lease_id
            receipt = dict(row.metadata_.get("githubCredentialRecovery") or {})
            claim = kwargs.get("recovery_claim")
            if claim is None and phase == "waiting":
                receipt["recoveryClaim"] = receipt.get("recoveryClaim", 0) + 1
            elif claim is not None and claim != receipt.get("recoveryClaim"):
                from moonmind.omnigent.bridge_store import OmnigentIdempotencyError

                raise OmnigentIdempotencyError(
                    "credential recovery attempt was superseded"
                )
            receipt.update(
                phase=phase,
                hostLeaseRef=lease.lease_id,
                omnigentHostId="host-1",
                omnigentSessionId="session-1",
                bridgeSessionId="bridge-1",
            )
            receipt["credentialGeneration"] = lease.credential_generation
            receipt["ownerIdempotencyKey"] = request.idempotency_key
            if kwargs.get("source_container_id"):
                receipt["sourceContainerId"] = kwargs["source_container_id"]
            if kwargs.get("retained_cpu_limit") is not None:
                receipt["retainedCpuLimit"] = kwargs["retained_cpu_limit"]
            if checkpoint:
                receipt["checkpoint"] = checkpoint
            row.metadata_["githubCredentialRecovery"] = receipt
            state["events"].append(("receipt", phase))
            if state["after_save"]:
                await state["after_save"](phase, checkpoint)
            return receipt

    store = Store()
    artifacts = LocalOmnigentArtifactGateway(root=tmp_path / "durable-artifacts")
    mounts = [
        {
            "Destination": destination,
            "Type": "volume",
            "Name": "mm-host-lease-1-" + suffix,
            "RW": True,
        }
        for suffix, destination in (
            ("state", "/home/app/.omnigent"),
            ("artifacts", "/artifacts"),
            ("cache", "/home/app/.cache"),
        )
    ]
    mounts.append(
        {
            "Destination": "/workspaces/run",
            "Type": "bind",
            "Source": str(workspace),
            "RW": True,
        }
    )
    session_files = [
        tmp_path / (name + ".state") for name in ("session", "artifacts", "cache")
    ]
    for path in session_files:
        path.write_text("original durable " + path.name)

    def new_runtime():
        runtime = OmnigentOAuthHostRuntime(
            client=SimpleNamespace(get_session=get_session), workspace_root=tmp_path
        )
        runtime.container_exists = AsyncMock(side_effect=lambda _: state["running"])
        runtime.assert_container_owned = AsyncMock()
        runtime._existing_github_projection_is_compatible = AsyncMock(
            return_value=False
        )
        runtime._discover_upstream_path = AsyncMock(return_value="/usr/bin:/bin")
        runtime._containers.remove_initializer = AsyncMock()
        runtime._project_github_credential = AsyncMock(
            wraps=runtime._project_github_credential
        )

        async def run(*args, **kwargs):
            state["events"].append(args[:2])
            if "-ceu" in args and "moonmind-projection" in args[args.index("-ceu") + 1]:
                script = args[args.index("-ceu") + 1].replace("/home/app", str(home))
                result = await asyncio.to_thread(
                    subprocess.run,
                    [
                        "/bin/sh",
                        "-ceu",
                        script,
                        "--",
                        str(os.getuid()),
                        str(os.getgid()),
                        "github.com",
                        args[-1],
                    ],
                    input=kwargs.get("input_bytes"),
                    capture_output=True,
                    check=True,
                )
                return result.returncode, result.stdout.decode(), result.stderr.decode()
            if args[:2] == ("docker", "inspect"):
                if '"credentialGeneration"' in args[3]:
                    return 0, json.dumps(state["identity"]), ""
                if "HostConfig.NanoCpus" in args[3]:
                    return 0, json.dumps(state["cpu"]), ""
                if args[3] == "{{json .Mounts}}":
                    return 0, json.dumps(mounts), ""
                return (0, lease.lease_id, "") if state["present"] else (1, "", "")
            if args[:2] == ("docker", "stop"):
                state["running"] = False
                if state["stop_write"]:
                    (workspace / "untracked.txt").write_text(state["stop_write"])
                if state["stop_error"]:
                    raise state["stop_error"]
            if args[:2] == ("docker", "rm"):
                assert row.metadata_["githubCredentialRecovery"]["phase"] == "saved"
                state["present"] = False
            if args[:3] == ("docker", "run", "-d"):
                assert "GH_TOKEN=" not in " ".join(args)
                assert "GITHUB_TOKEN=" not in " ".join(args)
                state["running"] = state["present"] = True
                state["cpu"] = {
                    **state["cpu"],
                    "sourceContainerId": "b" * 64,
                }
                if gh:
                    from tests.unit.omnigent.test_gh_config_migration_suppression import (
                        _static_host_github_block,
                    )

                    block = (
                        _static_host_github_block()
                        .replace("/home/app", str(home))
                        .replace("/opt/moonmind-tools/bin/gh", gh)
                    )
                    await asyncio.to_thread(
                        subprocess.run,
                        ["/bin/sh", "-c", block],
                        env=environment,
                        check=True,
                    )
                    token_result = await asyncio.to_thread(
                        subprocess.run,
                        [gh, "auth", "token", "--hostname", "github.com"],
                        env=environment,
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    assert token_result.stdout.strip() == "currentSelectedToken"
            return 0, "", ""

        runtime._run = AsyncMock(side_effect=run)
        return runtime

    launch = compile_effective_launch(
        profile_ref="omnigent-codex@1",
        policy_ref="codex-on-demand@1",
        provider_profile_id="codex",
    )
    args = {
        "binding": _binding(),
        "host_lease": lease,
        "container_name": "mm-host-lease-1",
        "workspace_source": workspace,
        "skill_projection": tmp_path / "skills",
        "runtime_scripts": tmp_path,
        "current_step_execution_id": step_id,
        "github_token": "currentSelectedToken",
        "github_projection_reservation": reservation,
        "effective_launch": launch,
        "egress_attestation": _egress_attestation(),
        "recovery_request": request,
        "recovery_store": store,
        "recovery_artifact_gateway": artifacts,
    }
    return SimpleNamespace(
        new_runtime=new_runtime,
        request=request,
        lease=lease,
        store=store,
        args=args,
        artifacts=artifacts,
        state=state,
        row=row,
        snapshot=snapshot,
        workspace=workspace,
        session_files=session_files,
    )


@pytest.mark.asyncio
async def test_legacy_recovery_captures_current_dirty_and_untracked_work_before_recreation(
    tmp_path, monkeypatch
):
    import io
    import tarfile

    fixture = await _recovery_fixture(tmp_path, monkeypatch)
    runtime = fixture.new_runtime()
    before = {p: p.read_bytes() for p in fixture.session_files}
    await runtime._launch_on_demand(**fixture.args)
    receipt = fixture.row.metadata_["githubCredentialRecovery"]
    assert receipt["omnigentSessionId"] == "session-1"
    assert receipt["bridgeSessionId"] == "bridge-1"
    archive = await fixture.artifacts.read_bytes(receipt["checkpoint"]["archiveRef"])
    with tarfile.open(fileobj=io.BytesIO(archive)) as saved:
        assert saved.extractfile("README.md").read() == b"current dirty work\n"
        assert saved.extractfile("untracked.txt").read() == b"new saved work\n"
    assert {p: p.read_bytes() for p in fixture.session_files} == before
    assert fixture.state["events"].index(("receipt", "saved")) < fixture.state[
        "events"
    ].index(("docker", "rm"))
    assert not any(event == ("docker", "volume") for event in fixture.state["events"])
    assert (
        runtime._project_github_credential.await_args.args[0] == "currentSelectedToken"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("cpu_millis", [1000, 0])
async def test_worker_restart_after_save_recaptures_newer_work_without_rollback(
    tmp_path, monkeypatch, cpu_millis
):
    import io
    import tarfile

    fixture = await _recovery_fixture(tmp_path, monkeypatch)

    async def cancel_after_save(phase, checkpoint):
        if phase == "saved":
            raise asyncio.CancelledError()

    fixture.args["effective_launch"]["limits"]["cpuMillis"] = cpu_millis
    fixture.state["after_save"] = cancel_after_save
    with pytest.raises(asyncio.CancelledError):
        await fixture.new_runtime()._launch_on_demand(**fixture.args)
    assert fixture.state["present"] and not fixture.state["running"]
    assert ("docker", "rm") not in fixture.state["events"]
    old_digest = fixture.row.metadata_["githubCredentialRecovery"]["checkpoint"][
        "workspaceDigest"
    ]
    (fixture.workspace / "untracked.txt").write_text(
        "newer progress after interruption\n"
    )
    fixture.state["after_save"] = None
    await fixture.new_runtime()._launch_on_demand(**fixture.args)
    saved = fixture.row.metadata_["githubCredentialRecovery"]["checkpoint"]
    assert saved["workspaceDigest"] != old_digest
    with tarfile.open(
        fileobj=io.BytesIO(await fixture.artifacts.read_bytes(saved["archiveRef"]))
    ) as archive:
        assert (
            archive.extractfile("untracked.txt").read()
            == b"newer progress after interruption\n"
        )
    assert (
        fixture.workspace / "untracked.txt"
    ).read_text() == "newer progress after interruption\n"


@pytest.mark.asyncio
async def test_turn_reactivation_during_capture_retains_host_and_save(
    tmp_path, monkeypatch
):
    fixture = await _recovery_fixture(tmp_path, monkeypatch)

    async def reactivate(phase, checkpoint):
        if phase == "waiting" and checkpoint:
            fixture.state["snapshots"] = [
                {**fixture.snapshot, "status": "running", "harness": "codex-native"}
            ]

    fixture.state["after_save"] = reactivate
    with pytest.raises(OmnigentOAuthHostError, match="changed while saving"):
        await fixture.new_runtime()._launch_on_demand(**fixture.args)
    assert fixture.state["running"] and fixture.state["present"]
    assert ("docker", "stop") not in fixture.state["events"]
    assert fixture.row.metadata_["githubCredentialRecovery"]["checkpoint"]["archiveRef"]


@pytest.mark.asyncio
async def test_active_turn_automatically_waits_then_saves_same_session(
    tmp_path, monkeypatch
):
    fixture = await _recovery_fixture(tmp_path, monkeypatch)
    fixture.state["snapshots"] = [
        {**fixture.snapshot, "status": "running", "harness": "codex-native"}
    ]
    await fixture.new_runtime()._launch_on_demand(**fixture.args)
    assert fixture.state["terminal_waits"] == 1
    assert (
        fixture.row.metadata_["githubCredentialRecovery"]["omnigentSessionId"]
        == "session-1"
    )


@pytest.mark.asyncio
async def test_durable_receipt_fences_janitor_until_current_terminal_save(
    tmp_path, monkeypatch
):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from api_service.db.models import (
        Base,
        ManagedAgentProviderProfile,
        ProviderCredentialSource,
        ProviderProfileAuthMethod,
        ProviderProfileAuthState,
        RuntimeMaterializationMode,
    )
    from moonmind.omnigent.bridge_store import (
        OmnigentBridgeSessionStore,
        OmnigentIdempotencyError,
    )
    from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostRepository
    from tests.unit.omnigent.test_bridge_store import _effective_launch

    fixture = await _recovery_fixture(tmp_path, monkeypatch)
    saved = await fixture.new_runtime().save_request_workspace(
        fixture.request, artifact_gateway=fixture.artifacts
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/recovery.db")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with factory() as session:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id="codex",
                    runtime_id="codex_cli",
                    provider_id="openai",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="codex_auth_volume",
                    volume_mount_path="/home/app/.codex",
                    max_parallel_runs=1,
                    credential_generation=3,
                    enabled=True,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                    last_auth_method=ProviderProfileAuthMethod.OAUTH_VOLUME,
                )
            )
            await session.commit()
        repository = OmnigentOAuthHostRepository(factory)
        binding = await repository.create_or_update_static_binding(
            profile_id="codex", endpoint_ref="default", static_host_id="host-1"
        )
        lease = await repository.create_or_get_host_lease(
            binding=binding,
            provider_lease_id="provider-lease-1",
            holder_workflow_id="workflow-1",
            agent_run_id="step-1",
            idempotency_key=fixture.request.idempotency_key,
        )
        store = OmnigentBridgeSessionStore(factory)
        await store.bind_profile_authorization(
            request=fixture.request,
            endpoint_ref="default",
            provider_profile_id="codex",
            provider_lease_id="provider-lease-1",
            credential_generation=3,
            host_binding_ref=binding.binding_ref,
            host_lease_ref=lease.lease_id,
            omnigent_host_id="host-1",
            effective_launch_snapshot=_effective_launch(),
        )
        await store.attach_session(fixture.request.idempotency_key, "session-1")
        receipt = await store.record_host_credential_recovery(
            request=fixture.request, host_lease_ref=lease.lease_id, phase="waiting"
        )
        assert receipt["omnigentSessionId"] == "session-1"
        raw_credential = "syntheticRawLegacyCredentialNeverPersist"
        with pytest.raises(OmnigentIdempotencyError, match="unsupported fields"):
            await store.record_host_credential_recovery(
                request=fixture.request,
                host_lease_ref=lease.lease_id,
                phase="waiting",
                retained_cpu_limit={
                    "sourceContainerId": "a" * 64,
                    "nanoCpus": 1500000000,
                    "Env": ["GH_TOKEN=" + raw_credential],
                },
            )
        valid_cpu = {
            "sourceContainerId": "a" * 64,
            "nanoCpus": 1500000000,
            "cpusetCpus": "0-1",
        }
        await store.record_host_credential_recovery(
            request=fixture.request,
            host_lease_ref=lease.lease_id,
            phase="waiting",
            source_container_id="a" * 64,
            retained_cpu_limit=valid_cpu,
        )
        await store.record_host_credential_recovery(
            request=fixture.request,
            host_lease_ref=lease.lease_id,
            phase="saved",
            checkpoint={**saved, "debugEnv": {"GH_TOKEN": raw_credential}},
        )
        persisted_receipt = (
            await store.get_existing(fixture.request.idempotency_key)
        ).metadata_["githubCredentialRecovery"]
        import json

        assert raw_credential not in json.dumps(persisted_receipt)
        assert persisted_receipt["retainedCpuLimit"] == valid_cpu
        assert persisted_receipt["sourceContainerId"] == "a" * 64
        assert "debugEnv" not in persisted_receipt["checkpoint"]
        # A timed-out preservation attempt that keeps running beside its
        # retry cannot move the retry's durable progress backward.
        assert persisted_receipt["recoveryClaim"] > receipt["recoveryClaim"]
        with pytest.raises(OmnigentIdempotencyError, match="superseded"):
            await store.record_host_credential_recovery(
                request=fixture.request,
                host_lease_ref=lease.lease_id,
                phase="waiting",
                recovery_claim=receipt["recoveryClaim"],
            )
        assert (
            await store.get_existing(fixture.request.idempotency_key)
        ).metadata_["githubCredentialRecovery"] == persisted_receipt
        assert (
            await store.record_host_credential_recovery(
                request=fixture.request,
                host_lease_ref=lease.lease_id,
                phase="saved",
                checkpoint=saved,
                recovery_claim=persisted_receipt["recoveryClaim"],
            )
        )["recoveryClaim"] == persisted_receipt["recoveryClaim"]
        await store.record_lifecycle_event(
            fixture.request.idempotency_key, event_type="terminal", status="waiting"
        )
        resumed_bridge = await OmnigentBridgeSessionStore(factory).get_or_create(
            request=fixture.request,
            endpoint_ref="default",
            agent_id=None,
            agent_name=None,
            target_metadata={},
        )
        assert resumed_bridge.omnigent_session_id == "session-1"
        assert resumed_bridge.host_lease_ref == lease.lease_id
        current = await repository.get_host_lease(lease.lease_id)
        assert (
            await repository.claim_host_lease_cleanup(
                current.lease_id,
                expected_status=current.status,
                expected_last_heartbeat_at=current.last_heartbeat_at,
            )
            is None
        )
        # A real DB restart keeps the preservation fence and exact session.
        store = OmnigentBridgeSessionStore(factory)
        await store.record_host_credential_recovery(
            request=fixture.request,
            host_lease_ref=lease.lease_id,
            phase="saved",
            checkpoint=saved,
        )
        from api_service.db.models import OmnigentBridgeSession

        async with factory() as session:
            persisted = await session.get(
                OmnigentBridgeSession, receipt["bridgeSessionId"]
            )
            authentic_metadata = dict(persisted.metadata_)
            persisted.metadata_ = {
                **authentic_metadata,
                "githubCredentialRecovery": {
                    **authentic_metadata["githubCredentialRecovery"],
                    "credentialGeneration": 99,
                },
            }
            await session.commit()
        with pytest.raises(
            OmnigentIdempotencyError, match="preserved host authority changed"
        ):
            await store.record_host_credential_recovery(
                request=fixture.request,
                host_lease_ref=lease.lease_id,
                phase="saved",
                checkpoint=saved,
            )
        async with factory() as session:
            persisted = await session.get(
                OmnigentBridgeSession, receipt["bridgeSessionId"]
            )
            persisted.metadata_ = authentic_metadata
            await session.commit()
        with pytest.raises(OmnigentIdempotencyError, match="current terminal"):
            await store.record_host_credential_recovery(
                request=fixture.request,
                host_lease_ref=lease.lease_id,
                phase="completed",
            )
        with pytest.raises(OmnigentIdempotencyError, match="authority changed"):
            await store.record_host_credential_recovery(
                request=fixture.request, host_lease_ref="foreign-lease", phase="waiting"
            )
        await store.bind_egress_cleanup_authority(
            request=fixture.request,
            host_lease_ref=lease.lease_id,
            egress_evidence={"attachmentIdentity": "old-container"},
            launch_evidence_ref="artifact://old-launch",
        )
        with pytest.raises(OmnigentIdempotencyError):
            await store.bind_egress_cleanup_authority(
                request=fixture.request,
                host_lease_ref=lease.lease_id,
                egress_evidence={"attachmentIdentity": "new-container"},
                launch_evidence_ref="artifact://new-launch",
                phase="launched",
                replaces_launch_evidence_ref="artifact://foreign-launch",
            )
        await store.bind_egress_cleanup_authority(
            request=fixture.request,
            host_lease_ref=lease.lease_id,
            egress_evidence={"attachmentIdentity": "new-container"},
            launch_evidence_ref="artifact://new-launch",
            phase="launched",
            replaces_launch_evidence_ref="artifact://old-launch",
        )
        authority = await store.get_egress_cleanup_authority(
            host_lease_ref=lease.lease_id
        )
        assert authority["launchEvidenceRef"] == "artifact://new-launch"
        assert authority["egressEvidence"]["attachmentIdentity"] == "new-container"
        row = await store.get_existing(fixture.request.idempotency_key)
        assert (
            row.metadata_["priorEgressCleanupAuthorities"][0]["launchEvidenceRef"]
            == "artifact://old-launch"
        )
        assert row.metadata_["githubCredentialRecovery"]["phase"] == "recreated"
        current = await repository.get_host_lease(lease.lease_id)
        assert (
            await repository.claim_host_lease_cleanup(
                current.lease_id,
                expected_status=current.status,
                expected_last_heartbeat_at=current.last_heartbeat_at,
            )
            is None
        )
        terminal_ref = await fixture.artifacts.write_json(
            request=fixture.request,
            name="terminal.json",
            payload={"status": "completed"},
            link_type="evidence.recovery",
        )
        await store.record_host_credential_recovery(
            request=fixture.request,
            host_lease_ref=lease.lease_id,
            phase="completed",
            checkpoint=saved,
            terminal_ref=terminal_ref,
        )
        current = await repository.get_host_lease(lease.lease_id)
        claimed = await repository.claim_host_lease_cleanup(
            current.lease_id,
            expected_status=current.status,
            expected_last_heartbeat_at=current.last_heartbeat_at,
        )
        assert claimed.status == "draining"
        with pytest.raises(OmnigentIdempotencyError, match="authority changed"):
            await store.record_host_credential_recovery(
                request=fixture.request, host_lease_ref=lease.lease_id, phase="waiting"
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("quota", ["nano", "quota", "unbounded", "shares"])
async def test_historical_zero_cpu_uses_only_observed_finite_authority(
    tmp_path, monkeypatch, quota
):
    fixture = await _recovery_fixture(tmp_path, monkeypatch)
    fixture.args["effective_launch"]["limits"]["cpuMillis"] = 0
    if quota == "quota":
        fixture.state["cpu"].update(nanoCpus=0, cpuQuota=125000, cpuPeriod=100000)
    elif quota in {"unbounded", "shares"}:
        fixture.state["cpu"].update(
            nanoCpus=0, cpuQuota=-1, cpuPeriod=100000, cpuShares=2048
        )
    runtime = fixture.new_runtime()
    if quota in {"unbounded", "shares"}:
        with pytest.raises(OmnigentOAuthHostError, match="finite live CPU authority"):
            await runtime._launch_on_demand(**fixture.args)
        assert fixture.state["running"]
        assert ("docker", "stop") not in fixture.state["events"]
        return
    await runtime._launch_on_demand(**fixture.args)
    launch = next(
        c.args
        for c in runtime._run.await_args_list
        if c.args[:3] == ("docker", "run", "-d")
    )
    if quota == "nano":
        assert launch[launch.index("--cpus") + 1] == "1.500000000"
        assert "--cpu-quota" not in launch
    else:
        assert launch[launch.index("--cpu-quota") + 1] == "125000"
        assert launch[launch.index("--cpu-period") + 1] == "100000"
        assert "--cpus" not in launch
    assert launch[launch.index("--cpuset-cpus") + 1] == "0-1"
    assert fixture.args["effective_launch"]["limits"]["cpuMillis"] == 0
    assert (
        fixture.row.metadata_["githubCredentialRecovery"]["retainedCpuLimit"][
            "sourceContainerId"
        ]
        == "a" * 64
    )


@pytest.mark.asyncio
async def test_uncertain_stop_retry_saves_last_write_before_recreation(
    tmp_path, monkeypatch
):
    import io
    import tarfile

    fixture = await _recovery_fixture(tmp_path, monkeypatch)
    fixture.state["stop_write"] = "last write while stop settled\n"
    fixture.state["stop_error"] = RuntimeError("Docker transport response lost")
    with pytest.raises(OmnigentOAuthHostError):
        await fixture.new_runtime()._launch_on_demand(**fixture.args)
    assert fixture.state["present"] and not fixture.state["running"]
    assert ("docker", "rm") not in fixture.state["events"]
    fixture.state["stop_error"] = None
    await fixture.new_runtime()._launch_on_demand(**fixture.args)
    saved = fixture.row.metadata_["githubCredentialRecovery"]["checkpoint"]
    with tarfile.open(
        fileobj=io.BytesIO(await fixture.artifacts.read_bytes(saved["archiveRef"]))
    ) as archive:
        assert (
            archive.extractfile("untracked.txt").read()
            == b"last write while stop settled\n"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["saved", "recreated", "store_unavailable"])
async def test_coordinator_failure_keeps_preservation_and_original_cause(phase):
    from tests.unit.omnigent.test_oauth_profile_lifecycle import (
        _run_coordinator_failure_case,
    )

    captured = {}
    error = OmnigentOAuthHostError(
        "original registration failure", code="ORIGINAL_HOST_FAILURE"
    )

    async def setup(runtime, coordinator):
        runtime._restore_preserved_workspace_if_missing = AsyncMock()

        async def load(_key):
            if phase == "store_unavailable":
                raise RuntimeError("receipt read unavailable")
            return SimpleNamespace(
                metadata_={"githubCredentialRecovery": {"phase": phase}}
            )

        coordinator._run_store.get_existing = load
        # Inject after container recreation, before host re-attestation.
        runtime._launch_on_demand = AsyncMock(side_effect=error)
        original_execute = coordinator.execute

        async def execute(request):
            try:
                return await original_execute(request)
            except OmnigentOAuthHostError as raised:
                captured["error"] = raised
                raise

        coordinator.execute = execute

    events, actions, owner_calls = await _run_coordinator_failure_case(
        fail_at="container_start",
        code="OMNIGENT_GITHUB_PROJECTION_REFRESH_FAILED",
        injected_error=error,
        setup=setup,
    )
    assert captured["error"].__cause__ is error
    assert error.code == "ORIGINAL_HOST_FAILURE"
    assert "provider_released" not in actions
    assert not {"host_remove", "host_stop"}.intersection(owner_calls)
    assert any(payload.get("code") == "ORIGINAL_HOST_FAILURE" for _, payload in events)
    assert (
        next(payload for kind, payload in events if kind == "terminal")["status"]
        == "waiting"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_response", [False, True])
async def test_preserved_session_uses_upstream_retry_without_replaying_input(
    tmp_path, lost_response
):
    import httpx

    from moonmind.workflows.adapters.omnigent_client import (
        OmnigentClientError,
        OmnigentHttpClient,
    )

    requests = []
    online = False

    def handler(request):
        nonlocal online
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"id": "session-1", "host_id": "host-1", "runner_online": online},
            )
        import json

        assert request.url.path == "/v1/sessions/session-1/events"
        assert json.loads(request.content) == {"type": "retry_session", "data": {}}
        online = True
        if lost_response:
            raise httpx.ReadError(
                "reply lost after same-session recovery", request=request
            )
        return httpx.Response(
            202,
            json={"recovered": True, "queued": False, "recovery": "runner_relaunched"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OmnigentHttpClient(base_url="http://omnigent.test", client=http)
        runtime = OmnigentOAuthHostRuntime(client=client, workspace_root=tmp_path)
        receipt = {"omnigentSessionId": "session-1"}
        if lost_response:
            with pytest.raises(OmnigentClientError):
                await runtime._resume_preserved_session(
                    receipt, expected_host_id="host-1"
                )
        await runtime._resume_preserved_session(receipt, expected_host_id="host-1")
    assert sum(request.method == "POST" for request in requests) == 1
    assert all(
        request.url.path.startswith("/v1/sessions/session-1") for request in requests
    )


@pytest.mark.asyncio
async def test_unknown_current_turn_exhausts_bounded_wait_without_cleanup(
    tmp_path, monkeypatch
):
    fixture = await _recovery_fixture(tmp_path, monkeypatch)
    fixture.args["effective_launch"]["limits"]["timeoutSeconds"] = 0
    fixture.state["snapshots"] = [
        {"id": "session-1", "host_id": "host-1", "status": "unknown"}
    ]
    with pytest.raises(OmnigentOAuthHostError, match="trustworthy inactive"):
        await fixture.new_runtime()._launch_on_demand(**fixture.args)
    assert fixture.state["running"]
    assert ("docker", "stop") not in fixture.state["events"]
    assert fixture.row.metadata_["githubCredentialRecovery"]["phase"] == "waiting"


@pytest.mark.asyncio
async def test_save_failure_retains_current_host_and_all_bytes(tmp_path, monkeypatch):
    fixture = await _recovery_fixture(tmp_path, monkeypatch)
    fixture.artifacts.write_bytes = AsyncMock(
        side_effect=RuntimeError("artifact storage unavailable")
    )
    with pytest.raises(OmnigentOAuthHostError):
        await fixture.new_runtime()._launch_on_demand(**fixture.args)
    assert fixture.state["running"] and fixture.state["present"]
    assert ("docker", "stop") not in fixture.state["events"]
    assert (fixture.workspace / "untracked.txt").read_text() == "new saved work\n"


@pytest.mark.asyncio
async def test_preserved_workspace_never_restores_over_current_or_reclones_missing_work(
    tmp_path, monkeypatch
):
    import shutil

    fixture = await _recovery_fixture(tmp_path, monkeypatch)
    runtime = fixture.new_runtime()
    await runtime._launch_on_demand(**fixture.args)
    (fixture.workspace / "untracked.txt").write_text("newer surviving work\n")
    await runtime._restore_preserved_workspace_if_missing(
        request=fixture.request,
        store=fixture.store,
        artifact_gateway=fixture.artifacts,
        host_lease=fixture.lease,
    )
    assert (fixture.workspace / "untracked.txt").read_text() == "newer surviving work\n"
    shutil.rmtree(fixture.workspace)
    with pytest.raises(OmnigentOAuthHostError, match="trustworthy stopped-host save"):
        await runtime._restore_preserved_workspace_if_missing(
            request=fixture.request,
            store=fixture.store,
            artifact_gateway=fixture.artifacts,
            host_lease=fixture.lease,
        )
    assert not fixture.workspace.exists()


@pytest.mark.asyncio
async def test_preserved_workspace_check_follows_custom_admitted_locator(
    tmp_path, monkeypatch
):
    from moonmind.omnigent.workspace_publication import (
        OmnigentWorkspacePublicationService,
    )
    from moonmind.workflows.temporal.runtime.workspace_locators import (
        SandboxWorkspaceRecord,
        SandboxWorkspaceRecordStore,
    )

    fixture = await _recovery_fixture(tmp_path, monkeypatch)
    runtime = fixture.new_runtime()
    await runtime._launch_on_demand(**fixture.args)
    locator = fixture.request.workspace_spec["workspaceLocator"]
    records = SandboxWorkspaceRecordStore(tmp_path)
    records._record_path(locator["workspaceId"]).unlink()
    records.ensure(
        SandboxWorkspaceRecord(
            locator["workspaceId"],
            "workflow-1",
            "workflow-1:run-1:implement:execution:1",
            "custom",
        )
    )
    custom = fixture.workspace.parent / "custom"
    fixture.workspace.rename(custom)
    request = fixture.request.model_copy(
        update={
            "workspace_spec": {
                **fixture.request.workspace_spec,
                "workspaceLocator": {**locator, "relativePath": "custom"},
            }
        }
    )
    restore_owner = AsyncMock()
    monkeypatch.setattr(
        OmnigentWorkspacePublicationService,
        "restore_saved_request_workspace",
        restore_owner,
    )

    await runtime._restore_preserved_workspace_if_missing(
        request=request,
        store=fixture.store,
        artifact_gateway=fixture.artifacts,
        host_lease=fixture.lease,
    )

    restore_owner.assert_not_awaited()
    assert (custom / "untracked.txt").read_text() == "new saved work\n"


@pytest.mark.asyncio
async def test_superseded_preservation_attempt_cannot_stop_or_rewrite_progress(
    tmp_path, monkeypatch
):
    fixture = await _recovery_fixture(tmp_path, monkeypatch)

    async def newer_attempt(phase, checkpoint):
        receipt = fixture.row.metadata_["githubCredentialRecovery"]
        if phase == "waiting" and receipt["recoveryClaim"] == 1:
            fixture.row.metadata_["githubCredentialRecovery"] = {
                **receipt,
                "recoveryClaim": 2,
                "phase": "saved",
            }

    fixture.state["after_save"] = newer_attempt
    with pytest.raises(OmnigentOAuthHostError, match="retained current work"):
        await fixture.new_runtime()._launch_on_demand(**fixture.args)
    assert fixture.state["running"]
    assert ("docker", "stop") not in fixture.state["events"]
    receipt = fixture.row.metadata_["githubCredentialRecovery"]
    assert (receipt["recoveryClaim"], receipt["phase"]) == (2, "saved")


@pytest.mark.asyncio
async def test_changed_finite_cpu_authority_cannot_stop_original_host(
    tmp_path, monkeypatch
):
    fixture = await _recovery_fixture(tmp_path, monkeypatch)
    fixture.args["effective_launch"]["limits"]["cpuMillis"] = 0

    async def change_quota(phase, checkpoint):
        if phase == "waiting" and checkpoint:
            fixture.state["cpu"]["nanoCpus"] = 2000000000

    fixture.state["after_save"] = change_quota
    with pytest.raises(OmnigentOAuthHostError, match="CPU authority changed"):
        await fixture.new_runtime()._launch_on_demand(**fixture.args)
    assert fixture.state["running"]
    assert ("docker", "stop") not in fixture.state["events"]


@pytest.mark.asyncio
async def test_missing_stopped_workspace_delegates_only_its_qualified_save(
    tmp_path, monkeypatch
):
    import shutil

    from moonmind.omnigent.workspace_publication import (
        OmnigentWorkspacePublicationService,
    )

    fixture = await _recovery_fixture(tmp_path, monkeypatch)
    runtime = fixture.new_runtime()
    await runtime._launch_on_demand(**fixture.args)
    fixture.state["running"] = False
    receipt = fixture.row.metadata_["githubCredentialRecovery"]
    receipt["checkpoint"]["checkpointRef"] = "artifact://qualified-current-checkpoint"
    shutil.rmtree(fixture.workspace)

    async def restore(_self, request, saved):
        assert request is fixture.request
        assert saved is receipt["checkpoint"]
        fixture.workspace.mkdir()
        (fixture.workspace / "restored.txt").write_text(
            "canonical restore owns these bytes"
        )

    restore_owner = AsyncMock(side_effect=restore)

    async def invoke(owner, request, saved):
        return await restore_owner(owner, request, saved)

    monkeypatch.setattr(
        OmnigentWorkspacePublicationService, "restore_saved_request_workspace", invoke
    )
    await runtime._restore_preserved_workspace_if_missing(
        request=fixture.request,
        store=fixture.store,
        artifact_gateway=fixture.artifacts,
        host_lease=fixture.lease,
    )
    restore_owner.assert_awaited_once()
    assert (
        fixture.workspace / "restored.txt"
    ).read_text() == "canonical restore owns these bytes"


def test_qualification_receipt_excludes_raw_inspect_credentials():
    import json

    from tests.integration.omnigent.test_exact_docker_n_way_concurrency import (
        _credential_recovery_container_facts,
    )

    secret = "syntheticRawLegacyCredentialNeverPersist"
    facts = _credential_recovery_container_facts(
        json.dumps(
            {
                "containerId": "a" * 64,
                "hostImageId": "sha256:" + "b" * 64,
                "Env": ["GH_TOKEN=" + secret],
                "config": {"token": secret},
                "mounts": [
                    {
                        "Type": "volume",
                        "Destination": "/home/app/.omnigent",
                        "Name": "test-state",
                        "rawCredential": secret,
                    }
                ],
            }
        ),
        expected_state_volume="test-state",
    )
    assert set(facts) == {"containerId", "hostImageId", "stateVolume"}
    assert secret not in json.dumps(facts)


@pytest.mark.asyncio
async def test_delayed_tool_after_assistant_candidate_is_quiesced_before_save(
    tmp_path, monkeypatch
):
    fixture = await _recovery_fixture(tmp_path, monkeypatch)
    initial = dict(fixture.snapshot)
    call = {
        "id": "late-tool",
        "type": "function_call",
        "data": {"call_id": "late", "name": "write_file"},
    }
    output = {
        "id": "late-result",
        "type": "function_call_output",
        "data": {"call_id": "late", "output": "saved"},
    }
    final_answer = {
        "id": "final-answer",
        "type": "message",
        "data": {"role": "assistant", "content": [{"text": "Really finished."}]},
    }
    late = {**initial, "items": [*initial["items"], call]}
    final = {
        **initial,
        "items": [*initial["items"], call, output, final_answer],
        "fixtureWrite": "late tool work\n",
    }
    fixture.snapshot.update(final)
    fixture.state["snapshots"] = [initial, initial, late, final]
    fixture.state["quiet_seconds"] = 0.15
    await fixture.new_runtime()._launch_on_demand(**fixture.args)
    assert fixture.state["terminal_waits"] == 1
    assert (fixture.workspace / "untracked.txt").read_text() == "late tool work\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("uncertain_posting", [False, True])
async def test_proven_never_dispatched_inactive_session_can_save_and_resume(
    tmp_path, monkeypatch, uncertain_posting
):
    fixture = await _recovery_fixture(tmp_path, monkeypatch)
    fixture.row.first_message_state = "prepared"
    fixture.row.first_message_posted_at = None
    fixture.row.first_message_post_attempted_at = True if uncertain_posting else None
    fixture.snapshot["items"] = []
    fixture.args["effective_launch"]["limits"]["timeoutSeconds"] = 0
    if uncertain_posting:
        with pytest.raises(OmnigentOAuthHostError, match="dispatch remains ambiguous"):
            await fixture.new_runtime()._launch_on_demand(**fixture.args)
        assert ("docker", "stop") not in fixture.state["events"]
        assert fixture.state["terminal_waits"] == 0
        return
    await fixture.new_runtime()._launch_on_demand(**fixture.args)
    assert fixture.state["running"]
    assert fixture.state["terminal_waits"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("credentialGeneration", "99"),
        ("providerProfileId", "foreign"),
        ("name", "/different-host"),
    ],
)
async def test_live_host_identity_drift_cannot_supply_inherited_cpu(
    tmp_path, monkeypatch, field, value
):
    fixture = await _recovery_fixture(tmp_path, monkeypatch)
    fixture.args["effective_launch"]["limits"]["cpuMillis"] = 0
    fixture.state["identity"][field] = value
    with pytest.raises(OmnigentOAuthHostError, match="live host recovery authority"):
        await fixture.new_runtime()._launch_on_demand(**fixture.args)
    assert fixture.state["running"]
    assert ("docker", "stop") not in fixture.state["events"]
    assert "retainedCpuLimit" not in fixture.row.metadata_["githubCredentialRecovery"]


@pytest.mark.asyncio
async def test_foreign_session_observation_cannot_authorize_host_stop(
    tmp_path, monkeypatch
):
    fixture = await _recovery_fixture(tmp_path, monkeypatch)
    fixture.snapshot["id"] = "foreign-session"
    with pytest.raises(OmnigentOAuthHostError, match="session/host identity"):
        await fixture.new_runtime()._launch_on_demand(**fixture.args)
    assert fixture.state["running"]
    assert ("docker", "stop") not in fixture.state["events"]


@pytest.mark.asyncio
@pytest.mark.parametrize("installed", [True, False])
@pytest.mark.parametrize("has_capabilities", [True, False])
async def test_exact_recovery_class_uses_installed_native_registry_without_picker_row(
    monkeypatch, tmp_path, installed, has_capabilities
):
    import contextlib
    import importlib.metadata
    import io
    import runpy
    import sys
    from types import ModuleType

    from moonmind.omnigent.harness_platform.catalog_service import _normalize_harness
    from tests.integration.omnigent.test_exact_docker_n_way_concurrency import (
        _credential_recovery_host_class,
    )

    capabilities = {"integration_mode": "native-server", "auth": "own-auth"}
    registry = ModuleType("omnigent.harness_plugins")
    registry.valid_harnesses = lambda: {"opencode-native"} if installed else set()
    registry.harness_capabilities = lambda: (
        {"opencode-native": SimpleNamespace(as_dict=lambda: capabilities)}
        if has_capabilities
        else {}
    )
    monkeypatch.setitem(sys.modules, "omnigent.harness_plugins", registry)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "1.2.3")

    async def inspect(args):
        if "moonmind.omnigent.build_digest" in " ".join(args):
            return 0, "sha256:" + "a" * 64, ""
        if "{{.Os}}/{{.Architecture}}" in args:
            return 0, "linux/arm64", ""
        assert args[:4] == ["docker", "run", "--rm", "--network"]
        assert args[4] == "none"
        assert args[7] == "example.test/host@sha256:" + "b" * 64
        output = io.StringIO()
        probe = tmp_path / "installed-harness-probe.py"
        probe.write_text(args[-1])
        with contextlib.redirect_stdout(output):
            runpy.run_path(str(probe), run_name="__main__")
        return 0, output.getvalue(), ""

    # The real upstream picker deliberately omits native wrapper rows; the
    # installed registry must be sufficient without an HTTP catalog client.
    if not installed or not has_capabilities:
        with pytest.raises(
            AssertionError, match="installed recovery harness is unavailable"
        ):
            await _credential_recovery_host_class(
                SimpleNamespace(run=inspect),
                "example.test/host@sha256:" + "b" * 64,
            )
        return
    result = await _credential_recovery_host_class(
        SimpleNamespace(run=inspect),
        "example.test/host@sha256:" + "b" * 64,
    )
    assert result.omnigentVersion == "1.2.3"
    assert result.omnigentBuildDigest == "sha256:" + "a" * 64
    assert result.architectures == ("linux/arm64",)
    expected = _normalize_harness(
        {"id": "opencode-native", "capabilities": capabilities},
        omnigent_version="1.2.3",
        omnigent_build_digest="sha256:" + "a" * 64,
    )
    assert result.declaredHarnessImplementations[0].implementationRef == (
        expected.implementation.implementation_ref()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("has_old_authority", [False, True])
async def test_restart_after_recreation_before_binding_uses_new_attachment_authority(
    tmp_path, monkeypatch, has_old_authority
):
    """A replacement already running after worker loss is still a new attachment."""
    import copy
    from unittest.mock import MagicMock

    fixture = await _recovery_fixture(tmp_path, monkeypatch)
    launch = fixture.args["effective_launch"]
    old_authority = {
        "effectiveLaunch": launch,
        "egressEvidence": {
            **_egress_attestation().model_dump(by_alias=True, mode="json"),
            "attachmentIdentity": "a" * 64,
        },
        "launchEvidenceRef": "artifact://original-host-launch",
        "phase": "attested",
    } if has_old_authority else None
    authority = copy.deepcopy(old_authority)
    prior_authorities = []
    bind_calls = []

    async def get_authority(**_kwargs):
        return authority

    async def bind_authority(**kwargs):
        nonlocal authority
        bind_calls.append(copy.deepcopy(kwargs))
        if kwargs["phase"] == "launched":
            receipt = fixture.row.metadata_["githubCredentialRecovery"]
            assert receipt["phase"] == "saved"
            assert receipt["replacementEgressPending"] is True
            if old_authority is not None:
                assert kwargs["replaces_launch_evidence_ref"] == old_authority["launchEvidenceRef"]
                prior_authorities.append(copy.deepcopy(authority))
            else:
                assert "replaces_launch_evidence_ref" not in kwargs
            receipt.update(phase="recreated", replacementEgressPending=False)
        authority = {
            "effectiveLaunch": launch,
            "egressEvidence": copy.deepcopy(kwargs["egress_evidence"]),
            "launchEvidenceRef": kwargs["launch_evidence_ref"],
            "phase": kwargs["phase"],
        }

    fixture.store.get_egress_cleanup_authority = get_authority
    fixture.store.bind_egress_cleanup_authority = bind_authority

    # Execute the real save and container-replacement owner, then lose the
    # worker before prepare_host can bind any new cleanup authority.
    await fixture.new_runtime()._launch_on_demand(**fixture.args)
    receipt = fixture.row.metadata_["githubCredentialRecovery"]
    assert receipt["phase"] == "saved"
    receipt["replacementEgressPending"] = True  # Production save-store contract.
    checkpoint = copy.deepcopy(receipt["checkpoint"])
    replacement_id = fixture.state["cpu"]["sourceContainerId"]
    assert replacement_id != receipt["sourceContainerId"]
    assert fixture.state["running"]
    assert authority == old_authority
    assert not bind_calls
    launches_before_retry = fixture.state["events"].count(("docker", "run"))

    runtime = fixture.new_runtime()
    runtime._existing_github_projection_is_compatible.return_value = True
    runtime._prepare_skill_projection = AsyncMock(return_value=tmp_path / "skills")
    runtime._prepare_workspace = AsyncMock(return_value=fixture.workspace)
    runtime._align_workspace_ownership = MagicMock()
    runtime._prepare_runtime_scripts = MagicMock(return_value=tmp_path)
    runtime._container_job_environment = MagicMock(return_value={})
    runtime.reserve_github_projection = AsyncMock()
    runtime._initialize_required_tools = AsyncMock()
    fresh_attestation = _egress_attestation().model_copy(update={
        "gateway_image_digest": "sha256:" + "d" * 64,
        "applied_rule_digest": "sha256:" + "e" * 64,
    })
    runtime._attest_egress = AsyncMock(return_value=fresh_attestation)
    runtime._attest_server_image = AsyncMock(return_value={})
    runtime._resolve_workload_attachment_identity = AsyncMock(return_value=replacement_id)
    runtime._attest_launched_workload_egress = AsyncMock(return_value={
        **fresh_attestation.model_dump(by_alias=True, mode="json"),
        "attachmentIdentity": replacement_id,
        "endpointIdentity": "replacement-endpoint",
    })
    runtime._exec_check = AsyncMock()
    runtime._exec_tools_check = AsyncMock()
    runtime._resolve_exact_host = AsyncMock(return_value={
        "id": "host-1", "harnesses": ["codex-native"],
    })
    runtime._preflight_mounted_tools = AsyncMock(return_value={})
    binding = _binding().model_copy(update={
        "static_host_id": None,
        "host_launch_profile_ref": "codex-on-demand",
        "execution_profile_ref": "omnigent-codex@1",
        "launch_policy_ref": "codex-on-demand@1",
        "effective_launch_snapshot": launch,
    })
    result = await runtime.prepare_host(
        binding=binding,
        host_lease=fixture.lease.model_copy(
            update={"container_name": "mm-host-lease-1"}
        ),
        workspace_key="workspace-1",
        workspace_locator=fixture.request.workspace_spec["workspaceLocator"],
        current_workflow_id="workflow-1",
        current_step_execution_id=fixture.args["current_step_execution_id"],
        artifact_gateway=fixture.artifacts,
        recovery_artifact_gateway=fixture.artifacts,
        evidence_request=fixture.request,
        cleanup_authority_store=fixture.store,
        effective_launch=launch,
        github_token_resolver=AsyncMock(return_value=fixture.args["github_token"]),
        required_capabilities=("gh",),
    )
    assert result["status"] == "ready"
    assert [call["phase"] for call in bind_calls] == ["launched", "attested"]
    assert prior_authorities == ([old_authority] if has_old_authority else [])
    assert authority["egressEvidence"]["attachmentIdentity"] == replacement_id
    assert authority["egressEvidence"]["endpointIdentity"] == "replacement-endpoint"
    runtime._attest_launched_workload_egress.assert_awaited_once_with(
        attestation=fresh_attestation,
        attachment_identity=replacement_id,
        expected_image_ref=launch["hostImageRef"],
    )
    assert receipt["checkpoint"] == checkpoint
    assert receipt["omnigentSessionId"] == "session-1"
    assert receipt["bridgeSessionId"] == "bridge-1"
    assert (fixture.workspace / "README.md").read_text() == "current dirty work\n"
    assert (fixture.workspace / "untracked.txt").read_text() == "new saved work\n"
    # The one additional docker run is the credential writer, never a host.
    assert fixture.state["events"].count(("docker", "run")) == launches_before_retry + 1
    assert not any(call.args[:3] == ("docker", "run", "-d") for call in runtime._run.await_args_list)
