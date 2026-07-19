from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from moonmind.omnigent.oauth_host_janitor import OmnigentOAuthHostJanitor


class _Repository:
    def __init__(self, lease):
        self.lease = lease
        self.stopped: list[str] = []

    async def list_active_host_leases(self):
        return [self.lease]

    async def validate_binding(self, _binding_ref):
        return SimpleNamespace()

    async def mark_host_lease_stopped(self, lease_id):
        self.stopped.append(lease_id)


class _Runtime:
    def __init__(self):
        self.stopped = 0

    async def container_exists(self, _name):
        return True

    async def stop_host(self, **_kwargs):
        self.stopped += 1

    async def list_managed_containers(self):
        return []


class _Client:
    async def get_session(self, _session_id):
        return {}

    async def interrupt(self, _session_id):
        return {}

    async def stop_session(self, _session_id):
        return {}


def _lease(*, heartbeat_age: int = 0):
    now = datetime.now(UTC)
    return SimpleNamespace(
        lease_id="lease-1",
        provider_profile_id="profile-1",
        binding_ref="binding-1",
        container_name="host-1",
        omnigent_session_id="session-1",
        last_heartbeat_at=now - timedelta(seconds=heartbeat_age),
        expires_at=now + timedelta(hours=1),
    )


@pytest.mark.asyncio
async def test_janitor_reconciles_stale_heartbeat_after_restart() -> None:
    repository = _Repository(_lease(heartbeat_age=121))
    runtime = _Runtime()

    result = await OmnigentOAuthHostJanitor(
        repository=repository,
        runtime=runtime,
        client=_Client(),
        heartbeat_timeout_seconds=90,
    ).run()

    assert result["actions"][-1]["action"] == "stale_heartbeat_cleanup"
    assert repository.stopped == ["lease-1"]
    assert runtime.stopped == 1


@pytest.mark.asyncio
async def test_janitor_consumes_durable_runner_exit_cleanup_handoff() -> None:
    repository = _Repository(_lease())
    runtime = _Runtime()
    run_store = SimpleNamespace(
        cleanup_required_host_lease_refs=lambda: None,
    )

    async def cleanup_refs():
        return {"lease-1"}

    run_store.cleanup_required_host_lease_refs = cleanup_refs
    result = await OmnigentOAuthHostJanitor(
        repository=repository,
        runtime=runtime,
        client=_Client(),
        run_store=run_store,
    ).run()

    assert result["actions"][-1]["action"] == "runner_exit_cleanup"
    assert repository.stopped == ["lease-1"]


@pytest.mark.asyncio
async def test_janitor_leaves_fresh_host_owned_by_active_session() -> None:
    repository = _Repository(_lease())
    runtime = _Runtime()

    result = await OmnigentOAuthHostJanitor(
        repository=repository,
        runtime=runtime,
        client=_Client(),
    ).run()

    assert result["count"] == 0
    assert repository.stopped == []
    assert runtime.stopped == 0
