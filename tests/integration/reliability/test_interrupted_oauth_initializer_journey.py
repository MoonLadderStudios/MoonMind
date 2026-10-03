"""Recover a created Docker initializer after losing its worker connection."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    OmnigentOAuthHostLeaseRecord,
    ProviderCredentialSource,
    ProviderProfileAuthState,
    RuntimeMaterializationMode,
)
from moonmind.omnigent.execution_profiles import compile_effective_launch
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend
from moonmind.omnigent.oauth_host_janitor import OmnigentOAuthHostJanitor
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostRepository

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


async def test_worker_loss_recovers_initializer_and_preserves_oauth_state(
    tmp_path, monkeypatch
):
    key = "moonmind-test-oauth-init-" + uuid4().hex
    auth_volume, scripts_volume = key + "-oauth", key + "-scripts"
    container_name = key + "-host"
    initializer_name = container_name + "-init"
    owned_volumes = [
        container_name + "-" + suffix for suffix in ("state", "artifacts", "cache")
    ]
    backend = DockerCommandBackend()
    _, image_ref, _ = await backend.run(
        [
            "docker",
            "image",
            "inspect",
            "postgres:17",
            "--format",
            "{{index .RepoDigests 0}}",
        ]
    )
    for selector in (
        "OMNIGENT_IMAGE_REF",
        "OMNIGENT_HOST_IMAGE_REF",
        "OMNIGENT_SHARED_HOST_IMAGE_REF",
    ):
        monkeypatch.setenv(selector, image_ref.strip())
    launch = compile_effective_launch(
        profile_ref="omnigent-codex@1",
        policy_ref=None,
        provider_profile_id=key,
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/hosts.db")
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as session:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id=key,
                    runtime_id="codex_cli",
                    provider_id="openai",
                    credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                    runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                    volume_ref=auth_volume,
                    volume_mount_path="/home/app/.codex",
                    max_parallel_runs=1,
                    credential_generation=1,
                    enabled=True,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                )
            )
            await session.commit()
        repository = OmnigentOAuthHostRepository(sessions)
        binding = await repository.create_or_update_static_binding(
            profile_id=key,
            endpoint_ref="test",
            host_launch_profile_ref=launch["launchPolicyRef"],
            effective_launch_snapshot=launch,
        )
        lease = await repository.create_or_get_host_lease(
            binding=binding,
            provider_lease_id=key + "-grant",
            holder_workflow_id=key,
            agent_run_id=key + "-step",
            idempotency_key=key,
        )
        async with sessions() as session:
            row = await session.get(OmnigentOAuthHostLeaseRecord, lease.lease_id)
            row.container_name = container_name
            await session.commit()
        lease = await repository.get_host_lease(lease.lease_id)

        await backend.run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                key + "-seed",
                "--network",
                "none",
                "--mount",
                f"type=volume,src={auth_volume},dst=/retained",
                "--entrypoint",
                "/bin/sh",
                image_ref.strip(),
                "-c",
                "printf retained-profile-state > /retained/sentinel",
            ]
        )
        runtime = OmnigentOAuthHostRuntime(
            client=SimpleNamespace(), workspace_root=tmp_path
        )
        runtime._discover_upstream_path = AsyncMock(return_value="/usr/bin:/bin")
        runtime._workspace_mount = lambda _path, target, **_kwargs: (
            f"type=volume,src={scripts_volume},dst={target},readonly"
        )
        real_run = runtime._run
        worker_lost = False

        async def interrupted_run(*argv, **kwargs):
            nonlocal worker_lost
            if worker_lost:
                return (1, "", "worker connection lost")
            if (
                argv[:2] == ("docker", "run")
                and "/opt/moonmind/init-oauth-host.sh" in argv
            ):
                # Docker run creates before starting. Losing the CLI here left
                # the reported workflow's initializer in Created, despite --rm.
                await real_run("docker", "create", *argv[3:])
                worker_lost = True
                raise asyncio.CancelledError()
            return await real_run(*argv, **kwargs)

        runtime._run = interrupted_run
        with pytest.raises(asyncio.CancelledError):
            await runtime._launch_on_demand(
                binding=binding,
                host_lease=lease,
                container_name=container_name,
                workspace_source=tmp_path,
                skill_projection=tmp_path / "skills",
                runtime_scripts=tmp_path / "scripts",
                current_step_execution_id=key + "-step",
                effective_launch=launch,
                egress_attestation=SimpleNamespace(
                    profile_ref="test",
                    profile_digest="sha256:" + "a" * 64,
                    applied_rule_digest="sha256:" + "b" * 64,
                ),
            )
        _, raw, _ = await backend.run(["docker", "inspect", initializer_name])
        created = json.loads(raw)[0]
        assert created["State"]["Status"] == "created"
        assert created["Config"]["Labels"]["moonmind.host_lease_id"] == lease.lease_id
        assert created["Config"]["Labels"]["moonmind.kind"] == "omnigent-oauth-host"
        failed_remove, _, _ = await backend.run(
            ["docker", "volume", "rm", owned_volumes[0]], check=False
        )
        assert failed_remove != 0  # The real daemon retains the initializer's mounts.
        async with sessions() as session:
            row = await session.get(OmnigentOAuthHostLeaseRecord, lease.lease_id)
            row.last_heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
            row.acquired_at = row.last_heartbeat_at - timedelta(seconds=1)
            await session.commit()

        # A fresh owner recovers from the durable lease, without its predecessor.
        recovered_runtime = OmnigentOAuthHostRuntime(
            client=SimpleNamespace(), workspace_root=tmp_path
        )
        # The database and daemon may be independent fixtures. Only this test's
        # lease is in the database, so do not run global orphan discovery.
        recovered_runtime.list_managed_containers = AsyncMock(return_value=[])
        result = await OmnigentOAuthHostJanitor(
            repository=repository, runtime=recovered_runtime, client=SimpleNamespace()
        ).run()
        assert result["status"] == "completed", json.dumps(result)
        assert (await repository.get_host_lease(lease.lease_id)).status == "stopped"
        assert not await recovered_runtime.container_present(initializer_name)
        for volume in owned_volumes:
            assert not await recovered_runtime._volume_present(volume)
        _, retained, _ = await backend.run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                key + "-read",
                "--network",
                "none",
                "--mount",
                f"type=volume,src={auth_volume},dst=/retained,readonly",
                "--entrypoint",
                "/bin/sh",
                image_ref.strip(),
                "-c",
                "cat /retained/sentinel",
            ]
        )
        assert retained == "retained-profile-state"
        successor = await repository.create_or_get_host_lease(
            binding=binding,
            provider_lease_id=key + "-next-grant",
            holder_workflow_id=key + "-next",
            agent_run_id=key + "-next-step",
            idempotency_key=key + "-next",
        )
        assert successor.lease_id != lease.lease_id
    finally:
        for name in (initializer_name, container_name, key + "-seed", key + "-read"):
            await backend.run(["docker", "rm", "-f", name], check=False)
        for volume in [*owned_volumes, auth_volume, scripts_volume]:
            await backend.run(["docker", "volume", "rm", volume], check=False)
        await engine.dispose()
