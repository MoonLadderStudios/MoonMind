"""Recorded static-connected work after the static host path was removed.

Stored plans and lease rows may still name a static-connected host. They must
fail closed or stay inert: never run on another host and never be reclaimed as
an on-demand container.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
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
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.harness_platform.planning_service import (
    OmnigentPlannedHostResolver,
    reject_retired_static_host_mode,
)
from moonmind.omnigent.host_capacity import GenericHostCapacityAdmission
from moonmind.omnigent.host_failures import OmnigentOAuthHostError
from moonmind.omnigent.host_leases import DbOmnigentHostLeaseRepository
from moonmind.omnigent.oauth_hosts import (
    RETIRED_STATIC_LEASE_PURPOSE,
    OmnigentOAuthHostRepository,
)

_STATIC_LEASE_ID = "ohl_" + "5" * 32


def _recorded_launch(host_mode: str) -> dict:
    return {
        "launchPolicyRef": "codex-static@1",
        "hostMode": host_mode,
        "limits": {
            "cpuMillis": 2000,
            "memoryMiB": 4096,
            "processes": 256,
            "temporaryStorageMiB": 256,
            "timeoutSeconds": 5400,
        },
        "capture": {"retentionDays": 30},
        "cleanup": {},
        "controlCapabilities": ["interrupt", "terminate"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("host_mode", ["static_compose", "static-connected"])
async def test_recorded_static_plan_fails_closed_before_host_selection(
    monkeypatch, host_mode
) -> None:
    plan = SimpleNamespace(
        payload=SimpleNamespace(
            harnessCatalogRef="catalog-1",
            harnessId="codex-native",
            harnessImplementationRef="codex-impl",
            hostImageRef="ghcr.io/example/host@sha256:" + "f" * 64,
            launchPolicyRef="codex-static@1",
        )
    )
    harness = SimpleNamespace(
        id="codex-native",
        implementation=SimpleNamespace(implementation_ref=lambda: "codex-impl"),
    )
    catalogs = SimpleNamespace(
        load=AsyncMock(
            return_value=SimpleNamespace(snapshot=SimpleNamespace(harnesses=[harness]))
        )
    )
    selector = Mock()
    resolver = OmnigentPlannedHostResolver(
        catalog_repository=catalogs, host_class_selector=selector
    )
    monkeypatch.setattr(
        resolver,
        "_load_exact_launch",
        AsyncMock(return_value=_recorded_launch(host_mode)),
    )

    with pytest.raises(HarnessPlatformError) as excinfo:
        await resolver(plan, request=SimpleNamespace())

    assert excinfo.value.code == "OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE"
    assert "was removed" in str(excinfo.value)
    selector.select.assert_not_called()


@pytest.mark.parametrize("host_mode", ["on-demand", "on_demand_docker", None])
def test_on_demand_host_modes_are_not_retired(host_mode) -> None:
    reject_retired_static_host_mode(host_mode)


@pytest_asyncio.fixture()
async def leftover_static_lease(tmp_path):
    """A database holding one lease recorded by a removed static host."""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/retired.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(UTC)
    async with factory() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id="codex",
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
                binding_ref="omnigent-oauth:codex",
                provider_profile_id="codex",
                endpoint_ref="default",
                harness="codex-native",
                static_host_id="omnigent-host-codex",
                credential_mount_template_json={
                    "authVolumeRef": {
                        "providerProfileId": "codex",
                        "runtimeId": "codex_cli",
                        "providerId": "openai",
                        "volumeRef": "codex_auth_volume",
                        "credentialGeneration": 1,
                        "ownerUserId": "user-1",
                    },
                    "targetPath": "/home/app/.codex",
                    "accessMode": "read_write",
                    "runtimeUid": 1000,
                    "runtimeGid": 1000,
                },
            )
        )
        await session.commit()
    async with factory() as session:
        session.add(
            OmnigentOAuthHostLeaseRecord(
                lease_id=_STATIC_LEASE_ID,
                provider_profile_id="codex",
                provider_lease_id="provider-lease-static",
                static_scope_ref="omnigent-static-scope:sha256:" + "6" * 64,
                binding_ref="omnigent-oauth:codex",
                credential_generation=1,
                holder_workflow_id="workflow-static",
                idempotency_key="runtime-binding-static",
                lease_purpose=RETIRED_STATIC_LEASE_PURPOSE,
                container_name="moonmind-omnigent-host-codex-1",
                status="ready",
                acquired_at=now - timedelta(hours=3),
                last_heartbeat_at=now - timedelta(hours=2),
                expires_at=now - timedelta(hours=1),
            )
        )
        await session.commit()
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_generic_lease_ledger_never_recovers_a_retired_static_lease(
    leftover_static_lease,
) -> None:
    leases = DbOmnigentHostLeaseRepository(leftover_static_lease)

    # The stale, expired static row is not a generic host: nothing reclaims
    # or relaunches the Compose container as an on-demand host.
    assert await leases.get(_STATIC_LEASE_ID) is None
    assert await leases.list_recoverable(stale_before=datetime.now(UTC)) == ()
    with pytest.raises(HarnessPlatformError) as excinfo:
        await leases.claim_cleanup(_STATIC_LEASE_ID, expected_generation=1)
    assert excinfo.value.code == "OMNIGENT_RUNTIME_BINDING_CONFLICT"
    async with leftover_static_lease() as session:
        row = await session.get(OmnigentOAuthHostLeaseRecord, _STATIC_LEASE_ID)
        assert row.status == "ready"


@pytest.mark.asyncio
async def test_retired_static_lease_spends_no_generic_host_capacity(
    leftover_static_lease,
) -> None:
    admission = GenericHostCapacityAdmission(
        session_factory=leftover_static_lease,
        host_capacity=1,
        cold_launch_burst=1,
        # The window covers the static allocation's acquisition.
        cold_launch_window_seconds=4 * 3600,
    )

    assert await admission.observe() == (0, 0)
    assert (await admission.evaluate()).admitted is True


@pytest.mark.asyncio
async def test_retired_static_lease_stays_readable_but_cannot_restart(
    leftover_static_lease,
) -> None:
    hosts = OmnigentOAuthHostRepository(leftover_static_lease)

    lease = await hosts.get_host_lease(_STATIC_LEASE_ID)
    assert lease is not None
    assert lease.lease_purpose == RETIRED_STATIC_LEASE_PURPOSE

    async with leftover_static_lease() as session:
        row = await session.get(OmnigentOAuthHostLeaseRecord, _STATIC_LEASE_ID)
        row.status = "stopped"
        await session.commit()
    with pytest.raises(OmnigentOAuthHostError) as excinfo:
        await hosts.restart_host_lease(_STATIC_LEASE_ID)
    assert excinfo.value.code == "OMNIGENT_HOST_CLEANUP_CLAIMED"


@pytest.mark.asyncio
async def test_retiring_static_leases_settles_only_their_records(
    leftover_static_lease,
) -> None:
    now = datetime.now(UTC)
    async with leftover_static_lease() as session:
        for lease_id, purpose, status in (
            ("ohl_" + "7" * 32, RETIRED_STATIC_LEASE_PURPOSE, "failed"),
            ("ohl_" + "8" * 32, "execution_omnigent", "failed"),
        ):
            session.add(
                OmnigentOAuthHostLeaseRecord(
                    lease_id=lease_id,
                    provider_profile_id="codex",
                    provider_lease_id="provider-" + lease_id,
                    binding_ref="omnigent-oauth:codex",
                    credential_generation=1,
                    holder_workflow_id="workflow-" + lease_id,
                    idempotency_key="key-" + lease_id,
                    lease_purpose=purpose,
                    status=status,
                    acquired_at=now - timedelta(hours=1),
                    last_heartbeat_at=now - timedelta(hours=1),
                    expires_at=now + timedelta(hours=1),
                )
            )
        await session.commit()
    hosts = OmnigentOAuthHostRepository(leftover_static_lease)

    retired = await hosts.retire_static_host_leases()

    assert sorted(retired) == [_STATIC_LEASE_ID, "ohl_" + "7" * 32]
    async with leftover_static_lease() as session:
        active = await session.get(OmnigentOAuthHostLeaseRecord, _STATIC_LEASE_ID)
        assert active.status == "stopped"
        assert active.error_code == "OMNIGENT_STATIC_HOST_RETIRED"
        assert active.cleanup_completed_at is not None
        failed = await session.get(OmnigentOAuthHostLeaseRecord, "ohl_" + "7" * 32)
        assert failed.status == "failed" and failed.cleanup_completed_at is not None
        on_demand = await session.get(OmnigentOAuthHostLeaseRecord, "ohl_" + "8" * 32)
        assert on_demand.status == "failed" and on_demand.cleanup_completed_at is None
    assert await hosts.retire_static_host_leases() == []
