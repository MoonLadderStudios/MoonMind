"""Interrupted OAuth initialization must not retain Provider Profile capacity."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.omnigent.host_failures import OmnigentOAuthHostError
from moonmind.omnigent.host_services.legacy_host_containers import (
    LegacyOmnigentHostContainerService,
)
from moonmind.omnigent.oauth_host_janitor import OmnigentOAuthHostJanitor


class _DockerDaemon:
    """A stopped initializer still keeps its mounts in use after its CLI exits."""

    def __init__(
        self,
        *,
        lease_id="lease-1",
        kind="omnigent-oauth-host",
        remove_fails=False,
    ):
        self.initializer = "host-1-init"
        self.labels = {"moonmind.kind": kind, "moonmind.host_lease_id": lease_id}
        self.volumes = {
            "host-1-" + suffix for suffix in ("state", "artifacts", "cache")
        }
        self.volumes.add("profile-oauth")
        self.remove_fails = remove_fails
        self.calls = []

    async def run(self, *argv, **_kwargs):
        self.calls.append(argv)
        if argv[:2] == ("docker", "ps"):
            return (0, (self.initializer + "\n") if self.initializer else "", "")
        if argv[:2] == ("docker", "inspect"):
            if not self.initializer or argv[-1] != self.initializer:
                return (1, "", "No such container")
            template = argv[3]
            if template == "{{.Id}}":
                return (0, "initializer-id\n", "")
            if template == "{{.State.Running}}":
                return (0, "false\n", "")
            if "moonmind.kind" in template:
                return (
                    0,
                    self.labels["moonmind.kind"]
                    + "|"
                    + self.labels["moonmind.host_lease_id"],
                    "",
                )
            if "moonmind.host_lease_id" in template:
                return (0, self.labels["moonmind.host_lease_id"], "")
            raise AssertionError(f"unexpected inspect template: {template}")
        if argv[:3] == ("docker", "rm", "-f"):
            if argv[-1] == self.initializer:
                if self.remove_fails:
                    return (1, "", "daemon could not remove initializer")
                self.initializer = None
            return (0, "", "")
        if argv[:3] == ("docker", "volume", "rm"):
            if self.initializer and argv[-1].startswith("host-1-"):
                return (1, "", "volume is in use by initializer-id")
            self.volumes.discard(argv[-1])
            return (0, "", "")
        if argv[:3] == ("docker", "volume", "inspect"):
            return (
                (0, argv[-1], "")
                if argv[-1] in self.volumes
                else (1, "", "No such volume")
            )
        if argv[:2] == ("docker", "stop"):
            return (1, "", "No such container")
        raise AssertionError(f"unexpected Docker command: {argv}")


def _authority():
    binding = SimpleNamespace(
        binding_ref="binding-1",
        provider_profile_id="profile-1",
        harness="codex-native",
        host_launch_profile_ref="codex-on-demand@1",
        credential_mount_ref=SimpleNamespace(
            auth_volume_ref=SimpleNamespace(
                runtime_id="codex_cli", volume_ref="profile-oauth"
            )
        ),
    )
    lease = SimpleNamespace(
        lease_id="lease-1",
        lease_purpose="execution_omnigent",
        container_name="host-1",
        provider_profile_id="profile-1",
        provider_lease_id="provider-lease-1",
        credential_generation=1,
        binding_ref="binding-1",
        status="starting",
        omnigent_session_id=None,
        last_heartbeat_at=datetime.now(UTC) - timedelta(minutes=10),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
        effective_launch_snapshot=None,
    )
    return binding, lease


def _containers(run, tmp_path):
    return LegacyOmnigentHostContainerService(
        run_command=run, runtime_scripts_root=tmp_path / ".omnigent-runtime-scripts"
    )


@pytest.mark.asyncio
async def test_stop_host_removes_abandoned_initializer_before_volumes(tmp_path):
    daemon = _DockerDaemon()
    containers = _containers(daemon.run, tmp_path)
    binding, lease = _authority()

    result = await containers.stop_host(binding=binding, host_lease=lease)

    assert result["cleanupResult"] == "succeeded"
    assert daemon.initializer is None
    assert daemon.volumes == {"profile-oauth"}
    assert daemon.calls.index(("docker", "rm", "-f", "host-1-init")) < next(
        index
        for index, call in enumerate(daemon.calls)
        if call[:3] == ("docker", "volume", "rm")
    )


@pytest.mark.asyncio
async def test_initializer_auto_removal_during_inspection_is_reconciled(tmp_path):
    daemon = _DockerDaemon()

    async def run(*argv, **kwargs):
        if argv[:2] == ("docker", "inspect") and argv[-1] == "host-1-init":
            daemon.initializer = None
        return await daemon.run(*argv, **kwargs)

    await _containers(run, tmp_path).remove_initializer(
        container_name="host-1", lease_id="lease-1"
    )
    assert daemon.initializer is None
    assert daemon.volumes == {
        "profile-oauth",
        "host-1-state",
        "host-1-artifacts",
        "host-1-cache",
    }


@pytest.mark.asyncio
async def test_unavailable_initializer_inventory_keeps_cleanup_unconfirmed(tmp_path):
    daemon = _DockerDaemon()

    async def run(*argv, **kwargs):
        if argv[:2] == ("docker", "ps"):
            return (1, "", "daemon unavailable")
        return await daemon.run(*argv, **kwargs)

    binding, lease = _authority()
    with pytest.raises(OmnigentOAuthHostError) as failure:
        await _containers(run, tmp_path).stop_host(binding=binding, host_lease=lease)
    assert failure.value.code == "OMNIGENT_HOST_CLEANUP_INCOMPLETE"
    assert len(daemon.volumes) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "labels", [{"lease_id": "replacement-lease"}, {"kind": "foreign"}]
)
async def test_stop_host_preserves_initializer_outside_lease_authority(
    tmp_path, labels
):
    daemon = _DockerDaemon(**labels)
    containers = _containers(daemon.run, tmp_path)
    binding, lease = _authority()

    with pytest.raises(OmnigentOAuthHostError) as failure:
        await containers.stop_host(binding=binding, host_lease=lease)

    assert failure.value.code == "OMNIGENT_HOST_OWNERSHIP_MISMATCH"
    assert daemon.initializer == "host-1-init"
    assert len(daemon.volumes) == 4
    assert not any(call[:2] == ("docker", "rm") for call in daemon.calls)


@pytest.mark.asyncio
async def test_janitor_keeps_capacity_until_initializer_cleanup_succeeds(tmp_path):
    daemon = _DockerDaemon(remove_fails=True)
    binding, lease = _authority()
    released = []

    class Repository:
        async def list_active_host_leases(self, **_kwargs):
            return [lease]

        async def validate_binding(self, _ref):
            return binding

        async def claim_host_lease_cleanup(self, _ref, **_kwargs):
            lease.status = "draining"
            return lease

        async def mark_host_lease_stopped(self, _ref):
            lease.status = "stopped"
            return lease

    class LeaseClient:
        async def release_lease(self, grant):
            released.append(grant.lease_id)

    def janitor_after_restart():
        return OmnigentOAuthHostJanitor(
            repository=Repository(),
            runtime=_containers(daemon.run, tmp_path),
            client=SimpleNamespace(),
            lease_client=LeaseClient(),
        )

    failed = await janitor_after_restart().run()
    assert failed["status"] == "degraded"
    assert released == []
    assert lease.status == "draining"
    assert daemon.initializer == "host-1-init"

    daemon.remove_fails = False
    recovered = await janitor_after_restart().run()
    assert recovered["status"] == "completed"
    assert lease.status == "stopped"
    assert released == ["provider-lease-1"]
    assert daemon.volumes == {"profile-oauth"}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["get_session", "interrupt", "stop_session"])
@pytest.mark.parametrize("remove_host_fails", [False, True])
async def test_session_api_failure_cannot_outlive_verified_host_teardown(
    tmp_path, operation, remove_host_fails
):
    """A session stop kills the runner, not its retained conversation record.

    Pinned Omnigent stop_session is non-sticky and terminates the bound runner
    and tunnel (routes/sessions/routes_events.py). Removing its owning host
    container therefore supplies the same runtime-absence evidence, even when
    that API fails. Exercise the real host stop owner, not a mocked success.
    """
    from moonmind.workflows.adapters.omnigent_client import OmnigentClientError

    class RunningHostDaemon(_DockerDaemon):
        def __init__(self):
            super().__init__()
            self.host_present = True
            self.runner_alive = True
            self.remove_host_fails = remove_host_fails

        async def run(self, *argv, **kwargs):
            if argv[:2] == ("docker", "inspect") and argv[-1] == "host-1":
                self.calls.append(argv)
                if not self.host_present:
                    return (1, "", "No such container")
                template = argv[3]
                if template == "{{.Id}}":
                    return (0, "host-id\n", "")
                if template == "{{.State.Running}}":
                    return (0, "true\n" if self.runner_alive else "false\n", "")
                if "moonmind.host_lease_id" in template:
                    return (0, "lease-1", "")
                raise AssertionError(template)
            if argv[:2] == ("docker", "stop") and argv[-1] == "host-1":
                self.calls.append(argv)
                if self.remove_host_fails:
                    return (1, "", "daemon unavailable")
                self.runner_alive = False
                return (0, "", "")
            if argv[:3] == ("docker", "rm", "-f") and argv[-1] == "host-1":
                self.calls.append(argv)
                if self.remove_host_fails:
                    return (1, "", "daemon unavailable")
                self.host_present = False
                self.runner_alive = False
                return (0, "", "")
            return await super().run(*argv, **kwargs)

    daemon = RunningHostDaemon()
    binding, lease = _authority()
    lease.status = "ready"
    lease.omnigent_session_id = "session-1"
    released = []

    class Repository:
        async def list_active_host_leases(self, **_kwargs):
            return [] if lease.status == "stopped" else [lease]

        async def validate_binding(self, _ref):
            return binding

        async def claim_host_lease_cleanup(self, _ref, **_kwargs):
            lease.status = "draining"
            return lease

        async def mark_host_lease_stopped(self, _ref):
            lease.status = "stopped"
            return lease

    class LeaseClient:
        async def release_lease(self, grant):
            # Capacity can never release based on the failed API alone.
            assert not daemon.host_present and not daemon.runner_alive
            released.append(grant.lease_id)

    client = SimpleNamespace(
        get_session=AsyncMock(return_value={}),
        interrupt=AsyncMock(return_value={}),
        stop_session=AsyncMock(return_value={}),
    )
    getattr(client, operation).side_effect = OmnigentClientError(
        "upstream unavailable", status_code=503
    )

    async def run_after_restart():
        return await OmnigentOAuthHostJanitor(
            repository=Repository(),
            runtime=_containers(daemon.run, tmp_path),
            client=client,
            lease_client=LeaseClient(),
        ).run()

    result = await run_after_restart()
    assert any(item["action"] == "session_cleanup_failed" for item in result["actions"])
    if remove_host_fails:
        assert lease.status == "draining"
        assert released == []
        assert daemon.host_present and daemon.runner_alive
        daemon.remove_host_fails = False
        await run_after_restart()
    assert lease.status == "stopped"
    assert released == ["provider-lease-1"]
    assert not daemon.host_present and not daemon.runner_alive
    assert daemon.initializer is None
    assert daemon.volumes == {"profile-oauth"}
    calls = getattr(client, operation).await_count
    await run_after_restart()
    assert getattr(client, operation).await_count == calls
    assert released == ["provider-lease-1"]
