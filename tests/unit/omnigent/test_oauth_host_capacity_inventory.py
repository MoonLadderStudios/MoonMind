"""Exercise janitor capacity inventory against real persisted lease rows."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    OmnigentOAuthHostLeaseRecord,
    ProviderCredentialSource,
    ProviderProfileSlotLease,
    RuntimeMaterializationMode,
)
from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostRepository


@pytest.mark.asyncio
async def test_terminal_capacity_inventory_excludes_release_tombstones(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/inventory.db")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(UTC)
    cases = [
        # Expired held leases still consume capacity until confirmed cleanup.
        ("stopped-held", "stopped", "held"),
        ("failed-held", "failed", "held"),
        ("stopped-cleanup", "stopped", "cleanup_requested"),
        ("failed-cleanup", "failed", "cleanup_requested"),
        ("stopped-released", "stopped", "released"),
        ("failed-released", "failed", "released"),
        ("active-released", "draining", "released"),
        ("active-held", "ready", "held"),
        ("missing-provider", "stopped", None),
    ]
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        repository = OmnigentOAuthHostRepository(factory)
        for key, host_state, provider_state in cases:
            async with factory() as session:
                session.add(
                    ManagedAgentProviderProfile(
                        profile_id=key,
                        runtime_id="codex_cli",
                        provider_id="openai",
                        credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                        runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                        volume_ref="codex_auth_volume",
                        volume_mount_path="/home/app/.codex",
                        credential_generation=1,
                        enabled=True,
                    )
                )
                await session.commit()
            binding = await repository.create_or_update_static_binding(
                profile_id=key, endpoint_ref="default", static_host_id=f"host-{key}"
            )
            async with factory() as session:
                session.add(
                    OmnigentOAuthHostLeaseRecord(
                        lease_id=key,
                        provider_profile_id=key,
                        provider_lease_id=f"provider-{key}",
                        binding_ref=binding.binding_ref,
                        credential_generation=1,
                        holder_workflow_id=f"workflow-{key}",
                        idempotency_key=key,
                        lease_purpose="execution_direct",
                        status=host_state,
                        acquired_at=now - timedelta(hours=2),
                        last_heartbeat_at=now - timedelta(hours=1),
                        expires_at=now - timedelta(minutes=1),
                    )
                )
                if provider_state is not None:
                    session.add(
                        ProviderProfileSlotLease(
                            runtime_id="codex_cli",
                            workflow_id=f"workflow-{key}",
                            profile_id=key,
                            lease_id=f"provider-{key}",
                            lease_state=provider_state,
                            expires_at=now - timedelta(minutes=1),
                            released_at=now if provider_state == "released" else None,
                        )
                    )
                await session.commit()
        # Lease IDs are runtime-scoped. A different profile's live lease must
        # not resurrect the released host through a coincidentally equal ID.
        async with factory() as session:
            session.add(
                ProviderProfileSlotLease(
                    runtime_id="claude_code",
                    workflow_id="other-workflow",
                    profile_id="other-profile",
                    lease_id="provider-stopped-released",
                    lease_state="held",
                )
            )
            await session.commit()
        terminal = (
            await repository.list_terminal_host_leases_with_active_provider_capacity()
        )
        assert {lease.lease_id for lease in terminal} == {
            "stopped-held",
            "failed-held",
            "stopped-cleanup",
            "failed-cleanup",
        }
        assert len(terminal) == 4
        # Active host cleanup remains independent of provider tombstones.
        active = await repository.list_active_host_leases()
        assert {lease.lease_id for lease in active} == {
            "active-held",
            "active-released",
        }
        # Historical rows remain available for independent orphan reconciliation.
        assert await repository.get_host_lease("stopped-released") is not None
    finally:
        await engine.dispose()
