"""Durable ordering for profile-owned GitHub credential publication."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    OmnigentOAuthHostLeaseRecord,
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
from tests.unit.omnigent.test_bridge_store import _request


@pytest.mark.asyncio
async def test_projection_reservation_survives_store_restart_and_fences_stale_owners(
    tmp_path,
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/projection.db")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    request = _request("owner")
    try:
        store = OmnigentBridgeSessionStore(factory)
        await store.get_or_create(
            request=request,
            endpoint_ref="endpoint",
            agent_id=None,
            agent_name=None,
            target_metadata={},
        )
        async with factory() as session:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id="profile",
                    runtime_id="codex_cli",
                    provider_id="openai",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref="codex_auth_volume",
                    volume_mount_path="/home/app/.codex",
                    max_parallel_runs=1,
                    credential_generation=1,
                    enabled=True,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                    last_auth_method=ProviderProfileAuthMethod.OAUTH_VOLUME,
                )
            )
            await session.commit()
        repository = OmnigentOAuthHostRepository(factory)
        binding = await repository.create_or_update_static_binding(
            profile_id="profile",
            endpoint_ref="endpoint",
            static_host_id="host",
        )
        lease = await repository.create_or_get_host_lease(
            binding=binding,
            provider_lease_id="provider",
            holder_workflow_id="corr-1",
            agent_run_id="step",
            idempotency_key=request.idempotency_key,
        )
        lease_id = lease.lease_id
        async with factory() as session:
            row = await store.get_existing("owner")
            row.host_lease_ref = lease_id
            row.provider_profile_id = "profile"
            row.provider_lease_id = "provider"
            row.host_binding_ref = binding.binding_ref
            row.credential_generation = 1
            await session.merge(row)
            await session.commit()
        first = await store.reserve_github_projection(
            request=request, host_lease_ref=lease_id
        )
        async with factory() as session:
            current = await session.get(OmnigentOAuthHostLeaseRecord, lease_id)
            # A same-session repository continuation changes routing, while
            # the original lease still owns credential publication.
            current.bridge_session_id = "continuation-bridge"
            await session.commit()
        restarted = OmnigentBridgeSessionStore(factory)
        second = await restarted.reserve_github_projection(
            request=request, host_lease_ref=lease_id
        )
        assert first["revision"] == 1
        assert second["revision"] == 2
        assert second["ownerRef"] == first["ownerRef"] == f"host-lease:{lease_id}"
        assert first["reservationId"] != second["reservationId"]
        with pytest.raises(
            OmnigentIdempotencyError, match="reservation authority changed"
        ):
            await restarted.validate_github_projection(
                request=request, host_lease_ref=lease_id, reservation=first
            )
        await restarted.validate_github_projection(
            request=request, host_lease_ref=lease_id, reservation=second
        )
        assert (await restarted.get_existing("owner")).metadata_[
            "githubProjectionReservation"
        ] == second
        cleanup_fence = {
            "expected_last_heartbeat_at": (
                await repository.get_host_lease(lease_id)
            ).last_heartbeat_at,
            "expected_provider_lease_id": "provider",
            "expected_credential_generation": 1,
        }
        with pytest.raises(OmnigentIdempotencyError, match="cleanup authority changed"):
            await restarted.get_github_projection_cleanup_authority(
                host_lease_ref=lease_id, **cleanup_fence
            )
        async with factory() as session:
            current = await session.get(OmnigentOAuthHostLeaseRecord, lease_id)
            current.status = "draining"
            await session.commit()
        assert (
            await restarted.get_github_projection_cleanup_authority(
                host_lease_ref=lease_id, **cleanup_fence
            )
            == second
        )
        with pytest.raises(OmnigentIdempotencyError, match="cleanup authority changed"):
            await restarted.get_github_projection_cleanup_authority(
                host_lease_ref=lease_id,
                **{
                    **cleanup_fence,
                    "expected_last_heartbeat_at": lease.last_heartbeat_at,
                },
            )
        for changed in (
            {"idempotency_key": "other"},
            {"status": "draining"},
            {"provider_lease_id": "other"},
        ):
            async with factory() as session:
                lease = await session.get(OmnigentOAuthHostLeaseRecord, lease_id)
                lease.idempotency_key, lease.status, lease.provider_lease_id = (
                    "owner",
                    "assigned",
                    "provider",
                )
                for field, value in changed.items():
                    setattr(lease, field, value)
                await session.commit()
            with pytest.raises(OmnigentIdempotencyError, match="authority changed"):
                await restarted.reserve_github_projection(
                    request=request, host_lease_ref=lease_id
                )
        assert (await restarted.get_existing("owner")).metadata_[
            "githubProjectionReservation"
        ] == second
    finally:
        await engine.dispose()
