"""Database contract tests for profile-bound Omnigent OAuth hosts."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    OmnigentOAuthHostBindingRecord,
    OmnigentOAuthHostLeaseRecord,
    ProviderCredentialSource,
    RuntimeMaterializationMode,
)


def _mount_ref(profile_id: str, generation: int = 1) -> dict:
    return {
        "authVolumeRef": {
            "providerProfileId": profile_id,
            "runtimeId": "codex_cli",
            "providerId": "openai",
            "volumeRef": f"{profile_id}-volume",
            "credentialGeneration": generation,
            "ownerUserId": "user-1",
        },
        "targetPath": "/home/app/.codex",
        "accessMode": "read_write",
        "runtimeUid": 1000,
        "runtimeGid": 1000,
    }


def test_binding_enforces_one_durable_host_configuration_per_profile() -> None:
    table = OmnigentOAuthHostBindingRecord.__table__
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("provider_profile_id",) in unique_columns
    assert ("binding_ref", "provider_profile_id") in unique_columns
    assert table.c.credential_mount_template_json.nullable is False


def test_host_lease_enforces_one_active_record_per_profile_and_provider_lease() -> None:
    table = OmnigentOAuthHostLeaseRecord.__table__
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("provider_profile_id",) not in unique_columns
    assert ("provider_lease_id",) in unique_columns
    assert ("idempotency_key",) in unique_columns
    assert table.c.credential_generation.nullable is False
    assert any(
        index.name == "ix_omnigent_oauth_host_lease_expiry"
        and tuple(column.name for column in index.columns) == ("expires_at",)
        for index in table.indexes
    )


def test_binding_rejects_mount_contract_owned_by_another_profile() -> None:
    with pytest.raises(ValueError, match="must belong to provider_profile_id"):
        OmnigentOAuthHostBindingRecord(
            binding_ref="binding-a",
            provider_profile_id="profile-a",
            endpoint_ref="default",
            harness="codex-native",
            credential_mount_template_json=_mount_ref("profile-b"),
        )


@pytest.mark.asyncio
async def test_host_lease_composite_fk_and_active_index_reject_bypasses(
    tmp_path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/host-leases.db")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    session_factory = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    acquired_at = datetime(2026, 7, 12, tzinfo=UTC)
    try:
        async with session_factory() as session:
            for profile_id in ("profile-a", "profile-b"):
                session.add(
                    ManagedAgentProviderProfile(
                        profile_id=profile_id,
                        runtime_id="codex_cli",
                        provider_id="openai",
                        credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                        runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                        max_parallel_runs=1,
                        credential_generation=1,
                    )
                )
            session.add(
                OmnigentOAuthHostBindingRecord(
                    binding_ref="binding-a",
                    provider_profile_id="profile-a",
                    endpoint_ref="default",
                    harness="codex-native",
                    credential_mount_template_json=_mount_ref("profile-a"),
                )
            )
            await session.commit()

        async with session_factory() as session:
            session.add(
                OmnigentOAuthHostLeaseRecord(
                    lease_id="mismatched-lease",
                    provider_profile_id="profile-b",
                    provider_lease_id="provider-lease-b",
                    binding_ref="binding-a",
                    credential_generation=1,
                    holder_workflow_id="workflow-b",
                    idempotency_key="mismatched",
                    lease_purpose="execution_omnigent",
                    status="allocating",
                    acquired_at=acquired_at,
                    last_heartbeat_at=acquired_at,
                    expires_at=acquired_at + timedelta(minutes=10),
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

        async with session_factory() as session:
            session.add(
                OmnigentOAuthHostLeaseRecord(
                    lease_id="stale-generation",
                    provider_profile_id="profile-a",
                    provider_lease_id="provider-lease-stale",
                    binding_ref="binding-a",
                    credential_generation=2,
                    holder_workflow_id="workflow-stale",
                    idempotency_key="stale-generation",
                    lease_purpose="execution_omnigent",
                    status="allocating",
                    acquired_at=acquired_at,
                    last_heartbeat_at=acquired_at,
                    expires_at=acquired_at + timedelta(minutes=10),
                )
            )
            with pytest.raises(
                ValueError, match="credential_generation must match"
            ):
                await session.commit()

        async with session_factory() as session:
            for suffix in ("one", "two"):
                session.add(
                    OmnigentOAuthHostLeaseRecord(
                        lease_id=f"active-{suffix}",
                        provider_profile_id="profile-a",
                        provider_lease_id=f"provider-lease-{suffix}",
                        binding_ref="binding-a",
                        credential_generation=1,
                        holder_workflow_id=f"workflow-{suffix}",
                        idempotency_key=f"active-{suffix}",
                        lease_purpose="execution_omnigent",
                        status="allocating",
                        acquired_at=acquired_at,
                        last_heartbeat_at=acquired_at,
                        expires_at=acquired_at + timedelta(minutes=10),
                    )
                )
            with pytest.raises(IntegrityError):
                await session.commit()
    finally:
        await engine.dispose()
    table = OmnigentOAuthHostLeaseRecord.__table__
    assert any(
        index.name == "ux_omnigent_oauth_host_lease_active_profile"
        and index.unique
        for index in table.indexes
    )
