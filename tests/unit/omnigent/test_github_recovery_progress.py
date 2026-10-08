"""Recovery receipts keep newer progress when timed-out deliveries overlap."""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
from tests.unit.omnigent.test_bridge_store import _effective_launch, _request


def checkpoint(name):
    return {
        "archiveRef": "artifact:" + name,
        "archiveDigest": "sha256:" + name * 64,
        "manifestRef": "artifact:manifest-" + name,
        "manifestDigest": "sha256:" + name * 64,
        "workspaceDigest": "sha256:" + name * 64,
        "workspaceIdentityDigest": "sha256:" + "d" * 64,
        "checkpointRef": "artifact:checkpoint-" + name,
    }


@asynccontextmanager
async def recovery_owner(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/progress.db")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    request = _request("recovery-owner")
    try:
        async with sessions() as session:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id="profile",
                    runtime_id="codex_cli",
                    provider_id="openai",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="test-auth",
                    volume_mount_path="/home/app/.codex",
                    max_parallel_runs=1,
                    credential_generation=1,
                    enabled=True,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                    last_auth_method=ProviderProfileAuthMethod.OAUTH_VOLUME,
                )
            )
            await session.commit()
        hosts = OmnigentOAuthHostRepository(sessions)
        binding = await hosts.create_or_update_static_binding(
            profile_id="profile", endpoint_ref="endpoint", static_host_id="host"
        )
        lease = await hosts.create_or_get_host_lease(
            binding=binding,
            provider_lease_id="provider",
            holder_workflow_id=request.correlation_id,
            agent_run_id="step",
            idempotency_key=request.idempotency_key,
        )
        store = OmnigentBridgeSessionStore(sessions)
        await store.bind_profile_authorization(
            request=request,
            endpoint_ref="endpoint",
            provider_profile_id="profile",
            provider_lease_id="provider",
            credential_generation=1,
            host_binding_ref=binding.binding_ref,
            host_lease_ref=lease.lease_id,
            omnigent_host_id="host",
            effective_launch_snapshot=_effective_launch(),
        )
        yield store, request, lease, sessions
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["saved", "recreated"])
async def test_recovery_cannot_move_backward_after_restart(tmp_path, phase):
    async with recovery_owner(tmp_path) as (store, request, lease, sessions):
        await store.record_host_credential_recovery(
            request=request,
            host_lease_ref=lease.lease_id,
            phase="saved",
            checkpoint=checkpoint("a"),
        )
        if phase == "recreated":
            await store.record_host_credential_recovery(
                request=request, host_lease_ref=lease.lease_id, phase="recreated"
            )
        before = (await store.get_existing(request.idempotency_key)).metadata_
        restarted = OmnigentBridgeSessionStore(sessions)
        with pytest.raises(OmnigentIdempotencyError, match="progress|phase"):
            await restarted.record_host_credential_recovery(
                request=request, host_lease_ref=lease.lease_id, phase="waiting"
            )
        assert (
            await restarted.get_existing(request.idempotency_key)
        ).metadata_ == before


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["waiting", "saved", "completed"])
async def test_stale_projection_attempt_cannot_replace_recovery_evidence(
    tmp_path, phase
):
    async with recovery_owner(tmp_path) as (store, request, lease, _sessions):
        older = await store.reserve_github_projection(
            request=request, host_lease_ref=lease.lease_id
        )
        await store.record_host_credential_recovery(
            request=request,
            host_lease_ref=lease.lease_id,
            phase="waiting",
            expected_projection_reservation=older,
        )
        newer = await store.reserve_github_projection(
            request=request, host_lease_ref=lease.lease_id
        )
        await store.record_host_credential_recovery(
            request=request,
            host_lease_ref=lease.lease_id,
            phase="saved",
            checkpoint=checkpoint("b"),
            expected_projection_reservation=newer,
        )
        before = (await store.get_existing(request.idempotency_key)).metadata_
        with pytest.raises(OmnigentIdempotencyError, match="reservation|owner"):
            await store.record_host_credential_recovery(
                request=request,
                host_lease_ref=lease.lease_id,
                phase=phase,
                checkpoint=checkpoint("a"),
                terminal_ref="artifact:old-terminal",
                expected_projection_reservation=older,
            )
        assert (await store.get_existing(request.idempotency_key)).metadata_ == before


@pytest.mark.asyncio
async def test_current_stopped_retry_retains_recreated_progress_while_saving_new_bytes(
    tmp_path,
):
    async with recovery_owner(tmp_path) as (store, request, lease, _sessions):
        old = await store.reserve_github_projection(
            request=request, host_lease_ref=lease.lease_id
        )
        await store.record_host_credential_recovery(
            request=request,
            host_lease_ref=lease.lease_id,
            phase="saved",
            checkpoint=checkpoint("a"),
            expected_projection_reservation=old,
        )
        await store.record_host_credential_recovery(
            request=request,
            host_lease_ref=lease.lease_id,
            phase="recreated",
            expected_projection_reservation=old,
        )
        new = await store.reserve_github_projection(
            request=request, host_lease_ref=lease.lease_id
        )
        receipt = await store.record_host_credential_recovery(
            request=request,
            host_lease_ref=lease.lease_id,
            phase="recreated",
            checkpoint=checkpoint("b"),
            replacement_egress_pending=True,
            expected_projection_reservation=new,
        )
        assert receipt["phase"] == "recreated"
        assert receipt["checkpoint"] == checkpoint("b")
        assert receipt["replacementEgressPending"] is True


@pytest.mark.asyncio
async def test_replacement_binding_and_terminal_save_use_current_reservation(tmp_path):
    async with recovery_owner(tmp_path) as (store, request, lease, sessions):
        old = await store.reserve_github_projection(
            request=request, host_lease_ref=lease.lease_id
        )
        await store.record_host_credential_recovery(
            request=request,
            host_lease_ref=lease.lease_id,
            phase="saved",
            checkpoint=checkpoint("a"),
            expected_projection_reservation=old,
        )
        await store.bind_egress_cleanup_authority(
            request=request,
            host_lease_ref=lease.lease_id,
            egress_evidence={"attachmentIdentity": "a" * 64},
            launch_evidence_ref="artifact:launch-a",
            phase="launched",
            expected_projection_reservation=old,
        )
        new = await store.reserve_github_projection(
            request=request, host_lease_ref=lease.lease_id
        )
        await store.record_host_credential_recovery(
            request=request,
            host_lease_ref=lease.lease_id,
            phase="recreated",
            checkpoint=checkpoint("b"),
            replacement_egress_pending=True,
            expected_projection_reservation=new,
        )
        before = (await store.get_existing(request.idempotency_key)).metadata_
        with pytest.raises(OmnigentIdempotencyError, match="reservation owner"):
            await store.bind_egress_cleanup_authority(
                request=request,
                host_lease_ref=lease.lease_id,
                egress_evidence={"attachmentIdentity": "b" * 64},
                launch_evidence_ref="artifact:launch-b",
                phase="launched",
                replaces_launch_evidence_ref="artifact:launch-a",
                expected_projection_reservation=old,
            )
        assert (await store.get_existing(request.idempotency_key)).metadata_ == before
        await store.bind_egress_cleanup_authority(
            request=request,
            host_lease_ref=lease.lease_id,
            egress_evidence={"attachmentIdentity": "b" * 64},
            launch_evidence_ref="artifact:launch-b",
            phase="launched",
            replaces_launch_evidence_ref="artifact:launch-a",
            expected_projection_reservation=new,
        )
        after = (await store.get_existing(request.idempotency_key)).metadata_
        assert after["githubCredentialRecovery"]["phase"] == "recreated"
        assert after["githubCredentialRecovery"]["checkpoint"] == checkpoint("b")
        assert after["githubCredentialRecovery"]["replacementEgressPending"] is False
        args = {
            "request": request,
            "host_lease_ref": lease.lease_id,
            "phase": "completed",
            "checkpoint": checkpoint("c"),
            "terminal_ref": "artifact:current-terminal",
            "expected_projection_reservation": new,
        }
        completed = await store.record_host_credential_recovery(**args)
        # A committed response lost at transport is reconciled by the same
        # immutable checkpoint/terminal receipt, never a second effect.
        restarted = OmnigentBridgeSessionStore(sessions)
        assert await restarted.record_host_credential_recovery(**args) == completed
        with pytest.raises(OmnigentIdempotencyError):
            await restarted.record_host_credential_recovery(
                **{**args, "checkpoint": checkpoint("a")}
            )
        assert (await restarted.get_existing(request.idempotency_key)).metadata_[
            "githubCredentialRecovery"
        ] == completed


@pytest.mark.asyncio
@pytest.mark.parametrize("newer_phase", ["saved", "recreated"])
async def test_delayed_preserver_does_not_stop_after_newer_progress(
    tmp_path, monkeypatch, newer_phase
):
    from moonmind.omnigent.host_failures import OmnigentOAuthHostError
    from tests.helpers.github_projection import projection_reservation
    from tests.unit.omnigent.test_legacy_github_host_recovery import _recovery_fixture

    fixture = await _recovery_fixture(tmp_path, monkeypatch)
    old = fixture.args["github_projection_reservation"]
    new = projection_reservation(fixture.args["host_lease"].lease_id, revision=2)
    fixture.row.metadata_["githubProjectionReservation"] = old
    original = fixture.store.record_host_credential_recovery

    async def checked_record(**kwargs):
        OmnigentBridgeSessionStore._verify_recovery_projection_owner(
            fixture.row.metadata_, kwargs.get("expected_projection_reservation")
        )
        return await original(**kwargs)

    fixture.store.record_host_credential_recovery = checked_record

    async def newer_save(phase, saved):
        if phase == "waiting" and saved:
            fixture.row.metadata_["githubProjectionReservation"] = new
            fixture.row.metadata_["githubCredentialRecovery"] = {
                **fixture.row.metadata_["githubCredentialRecovery"],
                "phase": newer_phase,
                "checkpoint": checkpoint("b"),
                "replacementEgressPending": False,
            }

    fixture.state["after_save"] = newer_save
    runtime = fixture.new_runtime()
    with pytest.raises(OmnigentOAuthHostError, match="retained current work"):
        await runtime._launch_on_demand(**fixture.args)
    assert fixture.state["running"]
    assert ("docker", "stop") not in fixture.state["events"]
    assert fixture.row.metadata_["githubCredentialRecovery"]["phase"] == newer_phase
    assert fixture.row.metadata_["githubCredentialRecovery"][
        "checkpoint"
    ] == checkpoint("b")


@pytest.mark.asyncio
async def test_stopped_recreated_retry_recaptures_current_bytes_without_regressing(
    tmp_path, monkeypatch
):
    from tests.unit.omnigent.test_legacy_github_host_recovery import _recovery_fixture

    fixture = await _recovery_fixture(tmp_path, monkeypatch)
    await fixture.new_runtime()._launch_on_demand(**fixture.args)
    old = fixture.row.metadata_["githubCredentialRecovery"]["checkpoint"]
    fixture.row.metadata_["githubCredentialRecovery"].update(
        phase="recreated", replacementEgressPending=False
    )
    fixture.state["running"] = False
    (fixture.workspace / "untracked.txt").write_text("newer stopped replacement work\n")
    runtime = fixture.new_runtime()
    args = fixture.args
    result = await runtime._preserve_legacy_github_host(
        request=fixture.request,
        store=fixture.store,
        artifact_gateway=fixture.artifacts,
        host_lease=args["host_lease"],
        container_name=args["container_name"],
        workspace_source=fixture.workspace,
        effective_launch=args["effective_launch"],
        github_projection_reservation=args["github_projection_reservation"],
    )
    assert result["phase"] == "recreated"
    assert result["replacementEgressPending"] is True
    assert result["checkpoint"]["workspaceDigest"] != old["workspaceDigest"]
    assert (
        fixture.workspace / "untracked.txt"
    ).read_text() == "newer stopped replacement work\n"


@pytest.mark.asyncio
async def test_stale_restore_delivery_keeps_current_workspace(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from moonmind.omnigent.host_failures import OmnigentOAuthHostError
    from moonmind.omnigent.workspace_publication import (
        OmnigentWorkspacePublicationService,
    )
    from tests.helpers.github_projection import projection_reservation
    from tests.unit.omnigent.test_legacy_github_host_recovery import _recovery_fixture

    fixture = await _recovery_fixture(tmp_path, monkeypatch)
    await fixture.new_runtime()._launch_on_demand(**fixture.args)
    old = fixture.args["github_projection_reservation"]
    fixture.row.metadata_["githubProjectionReservation"] = projection_reservation(
        fixture.lease.lease_id, 2
    )

    async def validate(**kwargs):
        OmnigentBridgeSessionStore._verify_recovery_projection_owner(
            fixture.row.metadata_, kwargs["reservation"]
        )

    fixture.store.validate_github_projection = validate
    restore = AsyncMock()
    monkeypatch.setattr(
        OmnigentWorkspacePublicationService, "restore_saved_request_workspace", restore
    )
    current = (fixture.workspace / "untracked.txt").read_bytes()
    with pytest.raises(OmnigentOAuthHostError, match="delivery authority changed"):
        await fixture.new_runtime()._restore_preserved_workspace_if_missing(
            request=fixture.request,
            store=fixture.store,
            artifact_gateway=fixture.artifacts,
            host_lease=fixture.lease,
            expected_projection_reservation=old,
        )
    restore.assert_not_awaited()
    assert (fixture.workspace / "untracked.txt").read_bytes() == current


@pytest.mark.asyncio
async def test_stale_terminal_writer_does_not_cleanup_newer_completed_owner():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from tests.helpers.github_projection import projection_reservation
    from tests.unit.omnigent.test_oauth_profile_lifecycle import (
        _run_coordinator_failure_case,
    )

    completed = False

    async def setup(runtime, coordinator):
        original_prepare = runtime.prepare_host

        async def prepare(**kwargs):
            result = await original_prepare(**kwargs)
            result["githubProjectionReservation"] = projection_reservation(
                kwargs["host_lease"].lease_id
            )
            return result

        runtime.prepare_host = prepare
        runtime._restore_preserved_workspace_if_missing = AsyncMock()

        async def load(_key):
            return SimpleNamespace(
                metadata_={
                    "githubCredentialRecovery": {
                        "phase": "completed" if completed else "recreated"
                    }
                }
            )

        async def stale_terminal(**kwargs):
            nonlocal completed
            assert kwargs["phase"] == "completed"
            assert kwargs["expected_projection_reservation"]
            completed = True
            raise OmnigentIdempotencyError(
                "credential recovery reservation owner changed"
            )

        coordinator._run_store.get_existing = load
        coordinator._run_store.validate_github_projection = AsyncMock()
        coordinator._run_store.record_host_credential_recovery = stale_terminal
        coordinator._workspace_preservation.save_request_workspace = AsyncMock(
            return_value=checkpoint("a")
        )
        coordinator._write_plan_runtime_evidence = AsyncMock(
            return_value="artifact:terminal"
        )

    events, actions, calls = await _run_coordinator_failure_case(
        fail_at="terminal_reservation",
        code="OMNIGENT_GITHUB_PROJECTION_REFRESH_FAILED",
        setup=setup,
    )
    assert completed
    assert "provider_released" not in actions
    assert not {"host_remove", "host_stop"}.intersection(calls)
    assert any(
        payload.get("status") == "waiting"
        for kind, payload in events
        if kind == "terminal"
    )
