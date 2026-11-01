"""Static host choices keep their ownership on the shared lifecycle."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from moonmind.omnigent.harness_platform.host_classes import OmnigentHostClassSelector
from moonmind.omnigent.harness_platform import static_hosts
from moonmind.omnigent.execution_profiles import (
    seal_effective_launch_snapshot,
    validate_effective_launch_snapshot,
)
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.host_failures import OmnigentOAuthHostError


@pytest.mark.parametrize("harness", ["codex-native", "claude-native"])
def test_shared_host_class_preserves_an_explicit_static_mode(harness):
    image = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "1" * 64
    selected = OmnigentHostClassSelector(environment={}).select(
        harness=SimpleNamespace(
            id=harness,
            implementation=SimpleNamespace(
                implementation_ref=lambda: "omnigent-harness-implementation:sha256:"
                + "2" * 64,
            ),
        ),
        omnigent_version="0.15.0",
        integration_mode="native-server",
        materializer_refs=[f"{harness.removesuffix('-native')}-oauth-home@1"],
        requested_host_mode="static-connected",
        host_image_ref=image,
        omnigent_host_build_digest="sha256:" + "3" * 64,
    )

    assert selected.imageRef == image
    assert selected.declares_harness(
        harness, "omnigent-harness-implementation:sha256:" + "2" * 64
    )


def _launch():
    return seal_effective_launch_snapshot(
        {
            "hostMode": "static_compose",
            "providerProfileId": "profile-a",
            "harness": "codex-native",
            "endpointRef": "default",
            "executionProfileRef": "omnigent-codex@1",
            "launchPolicyRef": "codex-static@1",
        }
    )


def _binding(**changes):
    return SimpleNamespace(
        **{
            "binding_ref": "binding-a",
            "provider_profile_id": "profile-a",
            "harness": "codex-native",
            "endpoint_ref": "default",
            "host_launch_profile_ref": None,
            "static_host_id": "1" * 32,
            "launch_policy_ref": "codex-static@1",
            **changes,
        }
    )


@pytest.mark.asyncio
async def test_static_launch_freezes_only_the_selected_logical_host_binding():
    original = _launch()
    binding = _binding()
    repository = SimpleNamespace(
        get_binding_for_profile=AsyncMock(return_value=binding),
        create_or_update_static_binding=AsyncMock(),
    )

    actual = await static_hosts.bind_static_host_selection(
        repository=repository,
        effective_launch=original,
    )

    assert actual["staticHostSelection"] == {
        "bindingRef": "binding-a",
        "providerProfileId": "profile-a",
        "harness": "codex-native",
        "endpointRef": "default",
        "staticHostId": "1" * 32,
    }
    assert actual["snapshotRef"] != original["snapshotRef"]
    validate_effective_launch_snapshot(actual)
    binding.static_host_id = "2" * 32
    assert actual["staticHostSelection"]["staticHostId"] == "1" * 32
    assert "staticHostSelection" not in original
    repository.create_or_update_static_binding.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"endpoint_ref": "other"},
        {"harness": "claude-native"},
        {"host_launch_profile_ref": "on-demand"},
        {"launch_policy_ref": "codex-static@2"},
    ],
)
async def test_static_selection_never_retargets_an_existing_binding(changes):
    repository = SimpleNamespace(
        get_binding_for_profile=AsyncMock(return_value=_binding(**changes)),
        create_or_update_static_binding=AsyncMock(),
    )
    with pytest.raises(HarnessPlatformError):
        await static_hosts.bind_static_host_selection(
            repository=repository,
            effective_launch=_launch(),
        )
    repository.create_or_update_static_binding.assert_not_called()


@pytest.mark.asyncio
async def test_ordinary_launch_bytes_do_not_gain_static_authority():
    original = seal_effective_launch_snapshot(
        {**_launch(), "hostMode": "on_demand_docker"}
    )
    repository = SimpleNamespace(get_binding_for_profile=AsyncMock())
    assert (
        await static_hosts.bind_static_host_selection(
            repository=repository,
            effective_launch=original,
        )
        == original
    )
    repository.get_binding_for_profile.assert_not_called()


@pytest_asyncio.fixture
async def physical_hosts(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from api_service.db.models import Base, ManagedAgentProviderProfile
    from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostRepository

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/static.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with factory() as session:
            session.add_all(
                [
                    ManagedAgentProviderProfile(
                        profile_id=profile,
                        runtime_id="codex_cli",
                        provider_id="openai",
                        credential_source="oauth_volume",
                        runtime_materialization_mode="oauth_home",
                        volume_ref=f"{profile}-home",
                        volume_mount_path="/home/app/.codex",
                        credential_generation=1,
                        max_parallel_runs=1,
                        enabled=True,
                    )
                    for profile in ("profile-a", "profile-b")
                ]
            )
            await session.commit()
        repository = OmnigentOAuthHostRepository(factory)
        bindings = [
            await repository.create_or_update_static_binding(
                profile_id=profile,
                endpoint_ref="default",
                static_host_id="1" * 32,
                launch_policy_ref="codex-static@1",
            )
            for profile in ("profile-a", "profile-b")
        ]
        yield repository, factory, bindings
    finally:
        await engine.dispose()


async def _reserve(
    repository, binding, key, *, grant="grant-a", purpose="execution_generic_static"
):
    return await repository.create_or_get_host_lease(
        binding=binding,
        provider_lease_id=grant,
        holder_workflow_id="workflow",
        agent_run_id="agent-run",
        idempotency_key=key,
        lease_purpose=purpose,
    )


@pytest.mark.asyncio
async def test_static_readmission_preserves_the_prior_allocation_record(physical_hosts):
    from sqlalchemy import select
    from api_service.db.models import OmnigentOAuthHostLeaseRecord

    repository, factory, (binding, _other) = physical_hosts
    first = await _reserve(repository, binding, "runtime-binding:first")
    await repository.claim_host_lease_cleanup(
        first.lease_id,
        expected_status=first.status,
        expected_last_heartbeat_at=first.last_heartbeat_at,
    )
    await repository.mark_host_lease_stopped(first.lease_id)
    second = await _reserve(repository, binding, "runtime-binding:successor")

    assert second.lease_id != first.lease_id
    assert (await repository.get_host_lease(first.lease_id)).status == "stopped"
    async with factory() as session:
        records = (await session.scalars(select(OmnigentOAuthHostLeaseRecord))).all()
        assert {row.idempotency_key for row in records} == {
            "runtime-binding:first",
            "runtime-binding:successor",
        }


@pytest.mark.asyncio
async def test_generic_static_admission_cannot_take_a_legacy_hosts_physical_slot(
    physical_hosts,
):
    repository, _factory, (first, second) = physical_hosts
    await _reserve(repository, first, "legacy-attempt", purpose="execution_omnigent")
    with pytest.raises(OmnigentOAuthHostError, match="still active"):
        await _reserve(repository, second, "generic-attempt", grant="grant-b")


@pytest_asyncio.fixture
async def static_port(physical_hosts):
    from moonmind.omnigent.provider_leases import AcquiredProviderLease
    from moonmind.omnigent.runtime_bindings import InMemoryStableRuntimeBindingStore
    from moonmind.omnigent.static_host_leases import StaticOmnigentHostLeaseRepository
    from moonmind.provider_profiles.lease_client import (
        CredentialLease,
        CredentialLeasePurpose,
    )
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    hosts, factory, (binding, _other) = physical_hosts
    selection = {
        "bindingRef": binding.binding_ref,
        "providerProfileId": "profile-a",
        "endpointRef": "default",
        "harness": "codex-native",
        "staticHostId": "1" * 32,
    }
    grant = CredentialLease(
        profile_id="profile-a",
        runtime_id="codex_cli",
        lease_id="grant-a",
        owner_id="grant-a",
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        fencing_generation=7,
    )
    acquired = AcquiredProviderLease(
        slot="primary-model",
        provider_profile_ref="profile-a",
        capacity_scope_ref="provider-profile:profile-a",
        provider_lease_ref="provider-profile-lease:grant-a",
        credential_generation=1,
        lease=grant,
    )
    store = InMemoryStableRuntimeBindingStore()
    runtime = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "a" * 64,
        idempotency_key="turn-1",
        provider_leases={"primary-model": acquired.runtime_binding_value()},
        initial_phase_results={
            "staticHostSelection": {
                "hostClassRef": "omnigent-codex@1",
                "selection": selection,
            }
        },
    )
    port = StaticOmnigentHostLeaseRepository(
        host_repository=hosts, runtime_binding_store=store
    )
    kwargs = {
        "runtime_binding_id": runtime.bindingId,
        "execution_plan_ref": runtime.executionPlanRef,
        "host_class_ref": "omnigent-codex@1",
        "launch_policy_ref": "codex-static@1",
        "static_selection": selection,
        "provider_leases": (acquired,),
        "request": AgentExecutionRequest(
            agentKind="managed",
            agentId="omnigent",
            correlationId="workflow",
            idempotencyKey="turn-1",
            executionProfileRef="profile-a",
        ),
    }
    return SimpleNamespace(
        port=port,
        store=store,
        runtime=runtime,
        kwargs=kwargs,
        hosts=hosts,
        factory=factory,
    )


@pytest.mark.asyncio
async def test_static_port_uses_the_existing_physical_lease_owner(static_port):
    from sqlalchemy import select
    from api_service.db.models import OmnigentHostLeaseRecordV2

    scene = static_port
    lease = await scene.port.acquire(**scene.kwargs)
    physical = await scene.hosts.get_host_lease(lease.leaseRef)
    assert physical.provider_lease_id == "grant-a"
    assert physical.lease_purpose == "execution_generic_static"
    assert physical.runtime_binding_id == scene.runtime.bindingId
    assert lease.runtimeBindingId == scene.runtime.bindingId
    assert lease.generation == lease.launchGeneration == 1
    async with scene.factory() as session:
        assert (await session.scalars(select(OmnigentHostLeaseRecordV2))).all() == []


@pytest.mark.asyncio
async def test_static_port_fences_cleanup_and_never_reopens_a_completed_allocation(
    static_port,
):
    scene = static_port
    lease = await scene.port.acquire(**scene.kwargs)
    authority = {
        "kind": "host",
        "containerName": "configured-static-host",
        "hostLeaseRef": lease.leaseRef,
        "launchGeneration": 1,
        "stateVolumeRef": "retained-static-state",
        "static": True,
    }
    runtime = await scene.store.get(scene.runtime.bindingId)
    await scene.store.update(
        runtime.bindingId,
        expected_revision=runtime.revision,
        expected_fencing_generation=runtime.fencingGeneration,
        updates={"cleanupAuthorityRefs": [authority]},
    )
    await scene.port.record_launch(
        lease.leaseRef, expected_generation=1, cleanup_handle=authority
    )
    ready = await scene.port.mark_ready(
        lease.leaseRef,
        expected_generation=1,
        omnigent_host_id="1" * 32,
        cleanup_handle=authority,
    )
    assert ready.status == "ready"
    claimed = await scene.port.claim_cleanup(lease.leaseRef, expected_generation=1)
    assert claimed.generation == 2
    assert claimed.status == "cleanup_pending"
    completed = await scene.port.mark_cleaned(lease.leaseRef, expected_generation=2)
    assert completed.status == "cleaned"
    with pytest.raises(HarnessPlatformError):
        await scene.port.heartbeat(lease.leaseRef, expected_generation=1)
    with pytest.raises(HarnessPlatformError):
        await scene.port.mark_ready(
            lease.leaseRef,
            expected_generation=1,
            omnigent_host_id="1" * 32,
            cleanup_handle=authority,
        )
    with pytest.raises(OmnigentOAuthHostError, match="new admission"):
        await scene.hosts.restart_host_lease(lease.leaseRef)


@pytest.mark.asyncio
async def test_static_port_rejects_a_scope_changed_after_admission(static_port):
    scene = static_port
    current = await scene.hosts.get_binding_for_profile("profile-a")
    await scene.hosts.create_or_update_static_binding(
        profile_id="profile-a",
        endpoint_ref="default",
        static_host_id="2" * 32,
        launch_policy_ref="codex-static@1",
        expected_binding=current,
        require_idle=True,
    )
    with pytest.raises(HarnessPlatformError, match="changed"):
        await scene.port.acquire(**scene.kwargs)


@pytest.mark.asyncio
async def test_shared_lease_repository_routes_static_lifecycle_without_a_second_ledger(
    static_port,
):
    from moonmind.omnigent.host_leases import DbOmnigentHostLeaseRepository

    scene = static_port
    leases = DbOmnigentHostLeaseRepository(scene.factory, static_repository=scene.port)
    lease = await leases.acquire(**scene.kwargs)
    assert await leases.get(lease.leaseRef) == lease
    assert (
        await leases.heartbeat(lease.leaseRef, expected_generation=1)
    ).leaseRef == lease.leaseRef
    assert (
        await leases.claim_cleanup(lease.leaseRef, expected_generation=1)
    ).status == "cleanup_pending"
    assert (
        await leases.mark_cleaned(lease.leaseRef, expected_generation=2)
    ).status == "cleaned"


@pytest.mark.asyncio
async def test_generic_capacity_counts_a_static_allocation_once(static_port):
    from moonmind.omnigent.host_capacity import GenericHostCapacityAdmission

    scene = static_port
    await scene.port.acquire(**scene.kwargs)
    admission = GenericHostCapacityAdmission(
        session_factory=scene.factory,
        host_capacity=1,
        cold_launch_burst=10,
        cold_launch_window_seconds=60,
    )
    decision = await admission.evaluate()
    assert decision.active_hosts == decision.recent_cold_launches == 1
    assert decision.admitted is False


def test_verified_static_launch_rehydrates_the_frozen_selection():
    from moonmind.omnigent.harness_platform.host_classes import HostClass
    from moonmind.omnigent.harness_platform.planning_service import (
        OmnigentPlannedHostResolver,
    )

    selection = {
        "bindingRef": "binding-a",
        "providerProfileId": "profile-a",
        "endpointRef": "default",
        "harness": "codex-native",
        "staticHostId": "1" * 32,
    }
    launch = seal_effective_launch_snapshot(
        {
            **_launch(),
            "staticHostSelection": selection,
            "architectures": ["amd64"],
            "runtimeUid": 1000,
            "runtimeGid": 1000,
        }
    )
    host = HostClass(
        hostClassId="omnigent-codex",
        version=1,
        imageRef="example/host@sha256:" + "1" * 64,
        omnigentBuildDigest="sha256:" + "2" * 64,
        architectures=["linux/amd64"],
        runtime={"home": "/home/app"},
        omnigentVersion="0.15.0",
        declaredHarnessImplementations=[],
        integrationModes=["native-server"],
        materializerRefs=["codex-oauth-home@1"],
        features={},
    )
    plan = SimpleNamespace(
        payload=SimpleNamespace(
            hostArchitecture="linux/amd64",
            hostImageRef=host.imageRef,
            omnigentHostBuildDigest=host.omnigentBuildDigest,
            harnessId="codex-native",
            endpointRef="default",
        )
    )
    resolved = OmnigentPlannedHostResolver._resolve_exact_host(
        plan=plan, host_class=host, launch=launch
    )
    assert resolved.runtime["staticHostSelection"] == selection
    assert resolved.runtime["effectiveLaunchSnapshot"] == launch


@pytest.mark.asyncio
async def test_static_cleanup_drains_without_removing_container_or_state():
    from moonmind.omnigent.host_services.cleanup import DockerOmnigentHostCleanupService

    calls = []

    class Backend:
        async def run(self, argv, **kwargs):
            calls.append((argv, kwargs))
            if argv[:3] == ["docker", "container", "inspect"]:
                return 0, "allocation|1", ""
            if argv[:2] == ["docker", "inspect"]:
                return 0, "sha256:" + "a" * 64, ""
            return 0, "", ""

    static = SimpleNamespace(assert_quiescent=AsyncMock())
    result = await DockerOmnigentHostCleanupService(
        Backend(), static_hosts=static
    ).cleanup(
        container_name="static-codex",
        host_lease_ref="allocation",
        host_lease_generation=1,
        state_volume_ref="existing-state",
        control_volume_ref="static-control",
        static_host_id="1" * 32,
    )
    static.assert_quiescent.assert_awaited_once_with("1" * 32)
    assert result["containerRemoved"] is False
    assert result["stateVolumeRemoved"] is False
    assert result["staticHostDrained"] is True
    assert all(
        argv[:3] not in (["docker", "rm", "-f"], ["docker", "volume", "rm"])
        for argv, _ in calls
    )
    writes = [argv for argv, _ in calls if argv[:2] == ["docker", "run"]]
    assert len(writes) == 1
    assert "existing-state" not in " ".join(writes[0])
    assert "execution-fanout" in " ".join(writes[0])


@pytest.mark.asyncio
async def test_static_cleanup_cannot_wipe_a_successors_control_projection():
    from moonmind.omnigent.host_services.cleanup import DockerOmnigentHostCleanupService

    backend = SimpleNamespace(run=AsyncMock(return_value=(0, "successor|1", "")))
    static = SimpleNamespace(assert_quiescent=AsyncMock())
    with pytest.raises(HarnessPlatformError, match="stale"):
        await DockerOmnigentHostCleanupService(backend, static_hosts=static).cleanup(
            container_name="static-codex",
            host_lease_ref="predecessor",
            host_lease_generation=1,
            state_volume_ref="existing-state",
            control_volume_ref="static-control",
            static_host_id="1" * 32,
        )
    assert backend.run.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "page",
    [
        {"data": [], "has_more": True},
        {"data": [], "has_more": None},
        {"data": [{"id": "unknown"}], "has_more": False},
        {
            "data": [
                {
                    "id": "native",
                    "host_id": "1" * 32,
                    "status": "idle",
                    "runner_online": None,
                }
            ],
            "has_more": False,
        },
        {
            "data": [
                {
                    "id": "native",
                    "host_id": "1" * 32,
                    "status": "idle",
                    "runner_online": True,
                }
            ],
            "has_more": False,
        },
    ],
)
async def test_static_attachment_never_treats_unknown_or_live_inventory_as_drained(
    page,
):
    from moonmind.omnigent.host_services.compose_static import ComposeStaticHostService

    service = ComposeStaticHostService(
        backend=None, client=SimpleNamespace(list_sessions=AsyncMock(return_value=page))
    )
    with pytest.raises(HarnessPlatformError):
        await service.assert_quiescent("1" * 32)


@pytest.mark.asyncio
async def test_static_attachment_reads_all_pages_and_allows_stopped_siblings():
    from moonmind.omnigent.host_services.compose_static import ComposeStaticHostService

    client = SimpleNamespace(
        list_sessions=AsyncMock(
            side_effect=[
                {
                    "data": [
                        {
                            "id": "other-host",
                            "host_id": "2" * 32,
                            "status": "running",
                            "runner_online": True,
                        }
                    ],
                    "has_more": True,
                },
                {
                    "data": [
                        {
                            "id": "retained",
                            "host_id": "1" * 32,
                            "status": "idle",
                            "runner_online": False,
                        }
                    ],
                    "has_more": False,
                },
            ]
        )
    )
    await ComposeStaticHostService(backend=None, client=client).assert_quiescent(
        "1" * 32
    )
    assert client.list_sessions.await_count == 2
    assert client.list_sessions.await_args_list[1].kwargs == {
        "agent_id": None,
        "after": "other-host",
    }


@pytest.mark.asyncio
async def test_failed_legacy_allocation_keeps_its_physical_slot_until_cleanup(
    physical_hosts,
):
    from sqlalchemy import update
    from api_service.db.models import OmnigentOAuthHostLeaseRecord

    repository, factory, (first, second) = physical_hosts
    lease = await _reserve(
        repository, first, "retained-failed", purpose="execution_omnigent"
    )
    await repository.transition_host_lease(
        lease.lease_id, expected_status="allocating", new_status="failed"
    )
    async with factory() as session:
        await session.execute(
            update(OmnigentOAuthHostLeaseRecord)
            .where(OmnigentOAuthHostLeaseRecord.lease_id == lease.lease_id)
            .values(static_scope_ref=None)
        )
        await session.commit()
    with pytest.raises(OmnigentOAuthHostError, match="still active"):
        await _reserve(repository, second, "new-binding", grant="grant-b")
