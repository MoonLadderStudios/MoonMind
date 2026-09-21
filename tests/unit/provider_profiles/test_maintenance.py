"""Unit tests for credential-maintenance helpers (drain bound + queue status)."""

import asyncio
from types import SimpleNamespace

import pytest

from moonmind.provider_profiles import maintenance


class _ImmediateHandle:
    def __init__(self, result):
        self._result = result

    async def result(self):
        return self._result


class _HangingHandle:
    async def result(self):
        await asyncio.sleep(30)
        return {}


class _CancellableHangingHandle:
    """Hanging janitor handle that records best-effort cancellation."""

    def __init__(self) -> None:
        self.cancelled = False

    async def result(self):
        await asyncio.sleep(30)
        return {}

    async def cancel(self):
        self.cancelled = True


class _FakeClient:
    def __init__(self, handle):
        self._handle = handle
        self.started = []

    async def start_workflow(self, *args, **kwargs):
        self.started.append((args, kwargs))
        return self._handle


def _install_adapter(monkeypatch: pytest.MonkeyPatch, handle) -> _FakeClient:
    client = _FakeClient(handle)

    class _FakeAdapter:
        async def get_client(self):
            return client

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter", _FakeAdapter
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.activity_catalog.get_workflow_task_queue",
        lambda: "test-queue",
    )
    return client


@pytest.mark.asyncio
async def test_drain_returns_janitor_result(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _install_adapter(
        monkeypatch, _ImmediateHandle({"cleaned": 2, "profile_id": "p"})
    )

    result = await maintenance.drain_profile_bound_hosts(
        profile_id="p", operation_id="op-1", timeout_seconds=5.0
    )

    assert result == {"cleaned": 2, "profile_id": "p"}
    assert len(client.started) == 1


@pytest.mark.asyncio
async def test_drain_times_out_when_janitor_result_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_adapter(monkeypatch, _HangingHandle())

    with pytest.raises(TimeoutError, match="timed out"):
        await maintenance.drain_profile_bound_hosts(
            profile_id="p", operation_id="op-1", timeout_seconds=0.05
        )


@pytest.mark.asyncio
async def test_drain_cancels_janitor_before_reporting_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = _CancellableHangingHandle()
    _install_adapter(monkeypatch, handle)

    with pytest.raises(TimeoutError, match="timed out"):
        await maintenance.drain_profile_bound_hosts(
            profile_id="p", operation_id="op-cancel", timeout_seconds=0.05
        )

    assert handle.cancelled is True


@pytest.mark.asyncio
async def test_drain_applies_one_deadline_to_the_complete_host_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Later drain phases must receive only the remaining bound."""
    import asyncio as _asyncio

    seen_timeouts: list[float] = []
    real_wait_for = _asyncio.wait_for

    async def _recording_wait_for(awaitable, timeout=None):
        if timeout is not None:
            seen_timeouts.append(float(timeout))
        return await real_wait_for(awaitable, timeout=timeout)

    async def _slow_get_client():
        await _asyncio.sleep(0.05)
        return _FakeClient(_ImmediateHandle({"cleaned": 1}))

    class _SlowAdapter:
        async def get_client(self):
            return await _slow_get_client()

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter", _SlowAdapter
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.activity_catalog.get_workflow_task_queue",
        lambda: "test-queue",
    )
    monkeypatch.setattr(
        "moonmind.provider_profiles.maintenance.asyncio.wait_for",
        _recording_wait_for,
    )

    result = await maintenance.drain_profile_bound_hosts(
        profile_id="p", operation_id="op-deadline", timeout_seconds=5.0
    )

    assert result == {"cleaned": 1}
    # Three waits ran (connect, start, result); a fresh bound per phase
    # would record 5.0 every time, while the shared deadline shrinks.
    assert len(seen_timeouts) == 3
    assert seen_timeouts[0] == 5.0
    assert seen_timeouts[1] < seen_timeouts[0]
    assert seen_timeouts[2] <= seen_timeouts[1]


def test_maintenance_status_from_state_reports_queue_position() -> None:
    state = {
        "profiles": {
            "opencode-go-default": {
                "current_leases": ["some-execution-lease"],
                "execution_lease_count": 1,
                "exclusive_maintenance_waiters": 2,
                "exclusive_maintenance_queue": [
                    {"ownerId": "other-owner", "purpose": "credential_validation"},
                    {"ownerId": "our-owner", "purpose": "credential_validation"},
                ],
            }
        }
    }

    status = maintenance.maintenance_status_from_state(
        state=state,
        runtime_id="opencode",
        profile_id="opencode-go-default",
        owner_id="our-owner",
    )

    assert status["known"] is True
    assert status["exclusive_maintenance_waiters"] == 2
    assert status["waiter_position"] == 2
    assert status["lease_held"] is False
    assert status["execution_lease_count"] == 1


def test_maintenance_status_from_state_reports_held_lease() -> None:
    state = {
        "profiles": {
            "opencode-go-default": {
                "current_leases": ["our-owner"],
                "execution_lease_count": 0,
                "exclusive_maintenance_waiters": 0,
                "exclusive_maintenance_queue": [],
            }
        }
    }

    status = maintenance.maintenance_status_from_state(
        state=state,
        runtime_id="opencode",
        profile_id="opencode-go-default",
        owner_id="our-owner",
    )

    assert status["known"] is True
    assert status["waiter_position"] is None
    assert status["lease_held"] is True


def test_maintenance_status_from_state_unknown_profile() -> None:
    status = maintenance.maintenance_status_from_state(
        state={"profiles": {}},
        runtime_id="opencode",
        profile_id="missing",
        owner_id="our-owner",
    )

    assert status["known"] is False
    assert status["profile_id"] == "missing"


@pytest.mark.asyncio
async def test_query_status_returns_unknown_when_manager_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingAdapter:
        async def get_client(self):
            raise RuntimeError("no temporal in unit tests")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter", _FailingAdapter
    )

    status = await maintenance.query_credential_maintenance_status(
        runtime_id="opencode",
        profile_id="opencode-go-default",
        owner_id="our-owner",
        timeout_seconds=1.0,
    )

    assert status["known"] is False
    assert status["profile_id"] == "opencode-go-default"


@pytest.mark.asyncio
async def test_query_status_projects_manager_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "profiles": {
            "opencode-go-default": {
                "current_leases": [],
                "execution_lease_count": 0,
                "exclusive_maintenance_waiters": 1,
                "exclusive_maintenance_queue": [
                    {"ownerId": "our-owner", "purpose": "credential_validation"},
                ],
            }
        }
    }

    async def _fake_query_workflow(client, workflow_id, query_name, arg=None):
        assert workflow_id == "provider-profile-manager:opencode"
        assert query_name == "get_state"
        return state

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.query_workflow", _fake_query_workflow
    )

    class _Adapter:
        async def get_client(self):
            return SimpleNamespace()

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter", _Adapter
    )

    status = await maintenance.query_credential_maintenance_status(
        runtime_id="opencode",
        profile_id="opencode-go-default",
        owner_id="our-owner",
        timeout_seconds=5.0,
    )

    assert status["known"] is True
    assert status["waiter_position"] == 1
