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
