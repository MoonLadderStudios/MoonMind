"""Durable ordering for profile-owned GitHub credential publication."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
from moonmind.omnigent.host_failures import OmnigentOAuthHostError
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
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


@pytest.mark.asyncio
async def test_unreserved_profile_value_is_never_published(tmp_path):
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=tmp_path
    )
    runtime._run = AsyncMock()
    with pytest.raises(OmnigentOAuthHostError, match="reservation"):
        await runtime._project_github_credential(
            "oldCachedValue",
            cache_volume="cache",
            host_image_ref="host",
            runtime_uid=1000,
            runtime_gid=1000,
        )
    runtime._run.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_publish_reconciles_only_same_reservation_after_lost_ack(
    tmp_path,
):
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=tmp_path
    )
    reservation = {
        "ownerRef": "host-lease:lease",
        "revision": 3,
        "reservationId": "c1ff73e9-c42d-48b5-acce-1e8c0f07c9c3",
    }
    runtime._run = AsyncMock(
        side_effect=[
            RuntimeError("lost acknowledgment"),
            (0, json.dumps(reservation), ""),
        ]
    )
    await runtime._project_github_credential(
        "selectedValue",
        cache_volume="cache",
        host_image_ref="host",
        runtime_uid=1000,
        runtime_gid=1000,
        github_projection_reservation=reservation,
    )
    assert runtime._run.await_count == 2
    assert runtime._run.await_args_list[0].kwargs["input_bytes"] == b"selectedValue"
    assert "input_bytes" not in runtime._run.await_args_list[1].kwargs
    stale = {**reservation, "revision": 4}
    runtime._run = AsyncMock(
        side_effect=[RuntimeError("lost acknowledgment"), (0, json.dumps(stale), "")]
    )
    with pytest.raises(OmnigentOAuthHostError, match="retain owned work") as raised:
        await runtime._project_github_credential(
            "selectedValue",
            cache_volume="cache",
            host_image_ref="host",
            runtime_uid=1000,
            runtime_gid=1000,
            github_projection_reservation=reservation,
        )

    assert raised.value.code == "OMNIGENT_GITHUB_PROJECTION_REFRESH_FAILED"
    assert isinstance(raised.value.__cause__, RuntimeError)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["database", "destination", "value", None])
async def test_profile_reserves_before_acquisition_or_workspace_effects(
    tmp_path, failure
):
    from unittest.mock import Mock

    from tests.unit.omnigent.test_oauth_profile_lifecycle import _binding, _host_lease

    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=tmp_path
    )
    launch = {"harness": "codex-native", "providerRuntime": "codex_cli"}
    runtime._validate_effective_launch = Mock(return_value=launch)
    order = []
    reservation = {
        "ownerRef": "host-lease:lease",
        "revision": 1,
        "reservationId": "c1ff73e9-c42d-48b5-acce-1e8c0f07c9c3",
    }

    async def operation(name, result=None):
        order.append(name)
        if failure == name:
            raise RuntimeError("unavailable")
        return result

    store = SimpleNamespace(validate_github_projection=AsyncMock())

    async def reserve_database(**_):
        return await operation("database", reservation)

    async def reserve_destination(**_):
        return await operation("destination")

    async def acquire():
        return await operation("value", "selectedValue")

    async def next_effect(**_):
        order.append("skills")
        raise RuntimeError("preparation reached")

    store.reserve_github_projection = reserve_database
    runtime.reserve_github_projection = reserve_destination
    runtime._prepare_skill_projection = next_effect
    with pytest.raises((RuntimeError, OmnigentOAuthHostError)) as raised:
        await runtime.prepare_host(
            binding=_binding().model_copy(
                update={"static_host_id": None, "host_launch_profile_ref": "codex"}
            ),
            host_lease=_host_lease(),
            workspace_key="workflow:step",
            workspace_locator={},
            current_workflow_id="workflow",
            current_step_execution_id="step",
            required_capabilities=("gh",),
            evidence_request=_request(),
            cleanup_authority_store=store,
            github_token_resolver=acquire,
        )
    expected = ["database", "destination", "value", "skills"]
    assert order == (expected[: expected.index(failure) + 1] if failure else expected)
    if failure:
        assert raised.value.code == "OMNIGENT_GITHUB_PROJECTION_REFRESH_FAILED"
    assert store.validate_github_projection.await_count == (
        2 if failure is None else 1 if failure == "value" else 0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("historical", [False, True])
async def test_profile_cleanup_cannot_remove_newer_stamped_cache(tmp_path, historical):
    import os
    import subprocess

    from tests.helpers.github_projection import (
        projection_reservation,
        reserve_projection,
    )
    from tests.unit.omnigent.test_oauth_profile_lifecycle import _binding, _host_lease

    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=tmp_path
    )
    lease = _host_lease()
    home = tmp_path / "home"
    directory = home / ".cache/moonmind-xdg/gh"
    newer = projection_reservation(lease.lease_id, revision=2)
    reserve_projection(directory, newer)
    before = (directory / ".projection-reservation.json").read_bytes()
    runtime.container_present = AsyncMock(return_value=False)
    runtime._volume_present = AsyncMock(return_value=True)
    calls = []

    async def run(*args, **kwargs):
        calls.append(args)
        if "-ceu" in args and "moonmind-projection" in args[args.index("-ceu") + 1]:
            index = args.index("-ceu")
            completed = await asyncio.to_thread(
                subprocess.run,
                [
                    "/bin/sh",
                    "-ceu",
                    args[index + 1].replace("/home/app", str(home)),
                    *args[index + 2 :],
                ],
                capture_output=True,
                check=False,
            )
            if completed.returncode:
                raise OmnigentOAuthHostError("projection reservation stale")
            return 0, completed.stdout.decode(), ""
        return 0, "", ""

    runtime._run = run
    store = SimpleNamespace(
        get_github_projection_cleanup_authority=AsyncMock(
            return_value=None if historical else projection_reservation(lease.lease_id),
        )
    )
    with pytest.raises(OmnigentOAuthHostError):
        await runtime.stop_host(
            binding=_binding().model_copy(
                update={"static_host_id": None, "host_launch_profile_ref": "codex"}
            ),
            host_lease=lease,
            cleanup_authority_store=store,
            effective_launch={
                "runtimeUid": os.getuid(),
                "runtimeGid": os.getgid(),
                "hostImageRef": "host",
            },
        )
    assert (directory / ".projection-reservation.json").read_bytes() == before
    assert not any(
        args[:2] in {("docker", "stop"), ("docker", "rm")}
        or args[:3] == ("docker", "volume", "rm")
        for args in calls
    )


@pytest.mark.asyncio
async def test_static_host_without_request_projection_keeps_lazy_credential_owner(
    tmp_path,
):
    from unittest.mock import Mock

    from tests.unit.omnigent.test_oauth_profile_lifecycle import _binding, _host_lease

    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=tmp_path
    )
    runtime._validate_effective_launch = Mock(
        return_value={"harness": "codex-native", "providerRuntime": "codex_cli"}
    )
    runtime._prepare_skill_projection = AsyncMock(
        side_effect=RuntimeError("static preparation reached")
    )
    runtime.reserve_github_projection = AsyncMock()
    resolver = AsyncMock()
    with pytest.raises(RuntimeError, match="static preparation reached"):
        await runtime.prepare_host(
            binding=_binding(),
            host_lease=_host_lease(),
            workspace_key="workflow:step",
            workspace_locator={},
            current_workflow_id="workflow",
            current_step_execution_id="step",
            required_capabilities=("gh",),
            github_token_resolver=resolver,
        )
    runtime.reserve_github_projection.assert_not_awaited()
    resolver.assert_not_awaited()
