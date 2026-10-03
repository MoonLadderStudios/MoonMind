"""Interrupted OAuth initialization must not retain Provider Profile capacity."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.omnigent.execution_profiles import compile_effective_launch
from moonmind.omnigent.host_failures import OmnigentOAuthHostError
from moonmind.omnigent.oauth_host_janitor import OmnigentOAuthHostJanitor
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime


@pytest.fixture(autouse=True)
def _immutable_images(monkeypatch):
    monkeypatch.setenv("OMNIGENT_IMAGE_REF", "example.test/omnigent@sha256:" + "1" * 64)
    monkeypatch.setenv(
        "OMNIGENT_HOST_IMAGE_REF", "example.test/host@sha256:" + "2" * 64
    )
    monkeypatch.setenv(
        "OMNIGENT_SHARED_HOST_IMAGE_REF", "example.test/host@sha256:" + "2" * 64
    )


class _DockerDaemon:
    """A stopped initializer still keeps its mounts in use after its CLI exits."""

    def __init__(
        self,
        *,
        lease_id="lease-1",
        kind="omnigent-oauth-host",
        remove_fails=False,
        cancel_initializer=False,
    ):
        self.initializer = "host-1-init"
        self.labels = {"moonmind.kind": kind, "moonmind.host_lease_id": lease_id}
        self.volumes = {
            "host-1-" + suffix for suffix in ("state", "artifacts", "cache")
        }
        self.volumes.add("profile-oauth")
        self.remove_fails = remove_fails
        self.cancel_initializer = cancel_initializer
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
        if argv[:2] == ("docker", "run"):
            if "/opt/moonmind/init-oauth-host.sh" in argv:
                if self.initializer:
                    raise OmnigentOAuthHostError("initializer name already in use")
                self.initializer = argv[argv.index("--name") + 1]
                if self.cancel_initializer:
                    raise asyncio.CancelledError()
                self.initializer = None  # Successful --rm initialization.
            return (0, "", "")
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


def _launch(runtime, tmp_path):
    binding, lease = _authority()
    runtime._discover_upstream_path = AsyncMock(return_value="/usr/bin:/bin")
    return runtime._launch_on_demand(
        binding=binding,
        host_lease=lease,
        container_name=lease.container_name,
        workspace_source=tmp_path,
        skill_projection=tmp_path / "skills",
        runtime_scripts=tmp_path / "scripts",
        current_step_execution_id="workflow:run:node-1:execution:1",
        effective_launch=compile_effective_launch(
            profile_ref="omnigent-codex@1",
            policy_ref="codex-on-demand@1",
            provider_profile_id="profile-1",
        ),
        egress_attestation=SimpleNamespace(
            profile_ref="restricted",
            profile_digest="sha256:" + "a" * 64,
            applied_rule_digest="sha256:" + "b" * 64,
        ),
    )


@pytest.mark.asyncio
async def test_retry_reconciles_initializer_before_launching_again(tmp_path):
    daemon = _DockerDaemon()
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=tmp_path
    )
    runtime._run = daemon.run

    await _launch(runtime, tmp_path)

    assert daemon.initializer is None
    assert any(call[:3] == ("docker", "run", "-d") for call in daemon.calls)
    assert daemon.calls.index(("docker", "rm", "-f", "host-1-init")) < next(
        index
        for index, call in enumerate(daemon.calls)
        if "/opt/moonmind/init-oauth-host.sh" in call
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_initializer_cancellation_preserves_original_error_and_cleanup(
    tmp_path, cleanup_fails
):
    daemon = _DockerDaemon(cancel_initializer=True, remove_fails=cleanup_fails)
    daemon.initializer = None
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=tmp_path
    )
    runtime._run = daemon.run

    with pytest.raises(asyncio.CancelledError):
        await _launch(runtime, tmp_path)

    assert not any(call[:3] == ("docker", "run", "-d") for call in daemon.calls)
    assert ("docker", "rm", "-f", "host-1-init") in daemon.calls
    assert (daemon.initializer is not None) == cleanup_fails


@pytest.mark.asyncio
async def test_stop_host_removes_abandoned_initializer_before_volumes(tmp_path):
    daemon = _DockerDaemon()
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=tmp_path
    )
    runtime._run = daemon.run
    binding, lease = _authority()

    result = await runtime.stop_host(binding=binding, host_lease=lease)

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
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=tmp_path
    )

    async def run(*argv, **kwargs):
        if argv[:2] == ("docker", "inspect") and argv[-1] == "host-1-init":
            daemon.initializer = None
        return await daemon.run(*argv, **kwargs)

    runtime._run = run
    await runtime._containers.remove_initializer(
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
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=tmp_path
    )

    async def run(*argv, **kwargs):
        if argv[:2] == ("docker", "ps"):
            return (1, "", "daemon unavailable")
        return await daemon.run(*argv, **kwargs)

    runtime._run = run
    binding, lease = _authority()
    with pytest.raises(OmnigentOAuthHostError) as failure:
        await runtime.stop_host(binding=binding, host_lease=lease)
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
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=tmp_path
    )
    runtime._run = daemon.run
    binding, lease = _authority()

    with pytest.raises(OmnigentOAuthHostError) as failure:
        await runtime.stop_host(binding=binding, host_lease=lease)

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
        runtime = OmnigentOAuthHostRuntime(
            client=SimpleNamespace(), workspace_root=tmp_path
        )
        runtime._run = daemon.run
        return OmnigentOAuthHostJanitor(
            repository=Repository(),
            runtime=runtime,
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
