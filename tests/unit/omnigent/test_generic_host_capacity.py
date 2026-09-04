"""Aggregate generic-host and cold-launch admission.

Source issue: MoonLadderStudios/MoonMind#3878 (invariant 7, AC7, AC11).

Provider Profile capacity says nothing about how many Docker hosts the machine
can carry. These tests pin the second and third governed limits: an aggregate
ceiling on concurrently allocated hosts, and a separately bounded cold-launch
rate.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.host_capacity import (
    ACTIVE_HOST_LEASE_STATUSES,
    COLD_LAUNCH_HOST_LEASE_STATUSES,
    LIMITING_LAYER_COLD_LAUNCH_RATE,
    LIMITING_LAYER_HOST_CAPACITY,
    GenericHostCapacityAdmission,
    evaluate_generic_host_capacity,
)
from moonmind.omnigent.settings import (
    OMNIGENT_GENERIC_HOST_CAPACITY_ENV,
    OMNIGENT_GENERIC_HOST_COLD_LAUNCH_BURST_ENV,
    OMNIGENT_GENERIC_HOST_COLD_LAUNCH_WINDOW_ENV,
    OMNIGENT_GENERIC_HOST_DEFAULT_CAPACITY,
    OMNIGENT_GENERIC_HOST_DEFAULT_COLD_LAUNCH_BURST,
    OMNIGENT_GENERIC_HOST_DEFAULT_COLD_LAUNCH_WINDOW_SECONDS,
    generic_host_capacity,
    generic_host_cold_launch_burst,
    generic_host_cold_launch_window_seconds,
)


def _decision(
    *,
    active_hosts: int,
    recent_cold_launches: int = 0,
    host_capacity: int = 8,
    cold_launch_burst: int = 2,
    cold_launch_window_seconds: int = 30,
):
    return evaluate_generic_host_capacity(
        active_hosts=active_hosts,
        recent_cold_launches=recent_cold_launches,
        host_capacity=host_capacity,
        cold_launch_burst=cold_launch_burst,
        cold_launch_window_seconds=cold_launch_window_seconds,
    )


@pytest.mark.parametrize("host_capacity", [1, 2, 4, 8, 16])
def test_aggregate_ceiling_admits_exactly_capacity_hosts(host_capacity: int) -> None:
    """The ceiling is the same rule at every size; there is no special case."""

    for active in range(host_capacity):
        assert _decision(
            active_hosts=active, host_capacity=host_capacity
        ).admitted is True

    refused = _decision(active_hosts=host_capacity, host_capacity=host_capacity)
    assert refused.admitted is False
    assert refused.limiting_layer == LIMITING_LAYER_HOST_CAPACITY


def test_cold_launch_burst_refuses_a_launch_storm_below_the_ceiling() -> None:
    """Spare steady-state room does not authorize simultaneous cold launches."""

    decision = _decision(
        active_hosts=2,
        recent_cold_launches=2,
        host_capacity=16,
        cold_launch_burst=2,
    )

    assert decision.admitted is False
    assert decision.limiting_layer == LIMITING_LAYER_COLD_LAUNCH_RATE
    assert decision.retry_after_seconds == 30


def test_full_machine_reports_capacity_not_cold_launch_rate() -> None:
    """Naming the wrong layer sends the operator after the wrong limit."""

    decision = _decision(
        active_hosts=8,
        recent_cold_launches=8,
        host_capacity=8,
        cold_launch_burst=2,
    )

    assert decision.limiting_layer == LIMITING_LAYER_HOST_CAPACITY
    assert "active_hosts=8" in decision.waiting_reason
    assert "host_capacity=8" in decision.waiting_reason


def test_waiting_reasons_name_the_layer_without_identity() -> None:
    """AC11: the reason is low cardinality and carries no run-specific identity."""

    host_full = _decision(active_hosts=8, host_capacity=8)
    launch_full = _decision(
        active_hosts=1, recent_cold_launches=2, host_capacity=8, cold_launch_burst=2
    )

    assert f"missing_condition={LIMITING_LAYER_HOST_CAPACITY}" in host_full.waiting_reason
    assert (
        f"missing_condition={LIMITING_LAYER_COLD_LAUNCH_RATE}"
        in launch_full.waiting_reason
    )
    for reason in (host_full.waiting_reason, launch_full.waiting_reason):
        for identity in ("profile", "credential", "repository", "http", "://"):
            assert identity not in reason.lower()


def test_admitted_decision_carries_no_wait() -> None:
    decision = _decision(active_hosts=0)

    assert decision.admitted is True
    assert decision.limiting_layer is None
    assert decision.retry_after_seconds == 0
    assert decision.waiting_reason == "Generic host capacity is available."


def test_payload_projection_is_compact_and_serializable() -> None:
    payload = _decision(active_hosts=3, host_capacity=8).as_payload()

    assert payload == {
        "admitted": True,
        "limitingLayer": None,
        "activeHosts": 3,
        "hostCapacity": 8,
        "recentColdLaunches": 0,
        "coldLaunchBurst": 2,
        "coldLaunchWindowSeconds": 30,
        "retryAfterSeconds": 0,
    }


class _FakeResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar(self) -> int:
        return self._value


class _FakeSession:
    def __init__(self, counts: list[int]) -> None:
        self._counts = list(counts)
        self.statements: list[object] = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _FakeResult(self._counts.pop(0))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _session_factory(counts: list[int]):
    session = _FakeSession(counts)

    def factory():
        return session

    return factory, session


@pytest.mark.asyncio
async def test_admission_reads_the_durable_ledger_not_process_state() -> None:
    """Two workers must observe the same counts, so the ledger is the source."""

    factory, session = _session_factory([5, 1])
    admission = GenericHostCapacityAdmission(
        session_factory=factory,
        host_capacity=8,
        cold_launch_burst=2,
        cold_launch_window_seconds=30,
    )

    decision = await admission.evaluate()

    assert decision.admitted is True
    assert decision.active_hosts == 5
    assert decision.recent_cold_launches == 1
    assert len(session.statements) == 2


@pytest.mark.asyncio
async def test_already_allocated_run_is_never_refused_by_its_own_capacity() -> None:
    """A retry counted in the ledger must not be blocked by that same count."""

    factory, _ = _session_factory([8, 8])
    admission = GenericHostCapacityAdmission(
        session_factory=factory,
        host_capacity=8,
        cold_launch_burst=2,
        cold_launch_window_seconds=30,
    )

    decision = await admission.evaluate(already_allocated=True)

    assert decision.admitted is True
    assert decision.limiting_layer is None
    assert decision.active_hosts == 8


@pytest.mark.asyncio
async def test_admission_refuses_when_the_ledger_is_full() -> None:
    factory, _ = _session_factory([8, 0])
    admission = GenericHostCapacityAdmission(
        session_factory=factory,
        host_capacity=8,
        cold_launch_burst=2,
        cold_launch_window_seconds=30,
    )

    decision = await admission.evaluate()

    assert decision.admitted is False
    assert decision.limiting_layer == LIMITING_LAYER_HOST_CAPACITY


@pytest.mark.parametrize(
    ("host_capacity", "burst", "window"),
    [(0, 2, 30), (8, 0, 30), (8, 2, 0)],
)
def test_non_positive_limits_are_rejected_at_construction(
    host_capacity: int, burst: int, window: int
) -> None:
    with pytest.raises(ValueError):
        GenericHostCapacityAdmission(
            session_factory=lambda: None,
            host_capacity=host_capacity,
            cold_launch_burst=burst,
            cold_launch_window_seconds=window,
        )


def test_cleanup_pending_hosts_still_hold_capacity() -> None:
    """A host is released by cleanup completing, not by the run finishing."""

    assert "cleanup_pending" in ACTIVE_HOST_LEASE_STATUSES
    # A host being cleaned up is not a launch, so it must not consume burst.
    assert "cleanup_pending" not in COLD_LAUNCH_HOST_LEASE_STATUSES


def test_limits_default_without_configuration() -> None:
    assert generic_host_capacity(env={}) == OMNIGENT_GENERIC_HOST_DEFAULT_CAPACITY
    assert (
        generic_host_cold_launch_burst(env={})
        == OMNIGENT_GENERIC_HOST_DEFAULT_COLD_LAUNCH_BURST
    )
    assert (
        generic_host_cold_launch_window_seconds(env={})
        == OMNIGENT_GENERIC_HOST_DEFAULT_COLD_LAUNCH_WINDOW_SECONDS
    )


def test_limits_are_operator_configurable() -> None:
    env = {
        OMNIGENT_GENERIC_HOST_CAPACITY_ENV: "16",
        OMNIGENT_GENERIC_HOST_COLD_LAUNCH_BURST_ENV: "4",
        OMNIGENT_GENERIC_HOST_COLD_LAUNCH_WINDOW_ENV: "10",
    }

    assert generic_host_capacity(env=env) == 16
    assert generic_host_cold_launch_burst(env=env) == 4
    assert generic_host_cold_launch_window_seconds(env=env) == 10


@pytest.mark.parametrize("raw", ["0", "-1", "not-a-number", "2.5"])
def test_invalid_limits_fail_fast_instead_of_falling_back(raw: str) -> None:
    """A typo must not silently restore the default and oversubscribe a host."""

    with pytest.raises(ValueError, match=OMNIGENT_GENERIC_HOST_CAPACITY_ENV):
        generic_host_capacity(env={OMNIGENT_GENERIC_HOST_CAPACITY_ENV: raw})
