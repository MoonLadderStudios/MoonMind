"""Aggregate generic-host capacity and cold-launch admission.

Source issue: MoonLadderStudios/MoonMind#3878 (invariant 7).

Provider Profile capacity governs how many workflows one provider credential
admits. It says nothing about how many on-demand Docker hosts the machine can
carry, and nothing about how many of those hosts may begin a cold launch at
once. A deployment-selected provider ceiling of 8 or 16 must therefore be
admitted against two further limits before a container is created:

* an aggregate ceiling on concurrently allocated generic hosts, and
* a separately bounded cold-launch rate.

Both limits are evaluated against the durable host-lease ledger, so every
worker, every Activity retry, and every restarted process observes the same
counts. An in-process semaphore could not: two agent-runtime workers would each
admit up to their own limit.

Waiting is the caller's job. This module only reports a decision and how long
to wait, so the AgentRun workflow can hold the wait as durable workflow state
instead of occupying a long-running execution Activity slot (invariant 6).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

#: Host lease statuses that still own a container or its cleanup authority.
ACTIVE_HOST_LEASE_STATUSES = ("allocating", "ready", "cleanup_pending")
#: A lease row exists only because a launch was started for it, so the
#: cold-launch window counts rows by age and never by current status. Counting
#: only live statuses let a launch that failed or finished cleanup inside the
#: window stop being evidence, so repeated short-lived failures each observed
#: an empty window and bypassed the burst limit entirely (#3878).

#: Serializes the count-and-reserve pair for generic host admission. Two
#: allocators that only read counts both observe a free slot and both insert,
#: so neither the aggregate ceiling nor the burst limit is actually enforced
#: under the concurrency this change enables (#3878). The key is a fixed
#: constant: it names the machine-wide generic-host domain and never carries a
#: provider, credential, host, or repository identity.
GENERIC_HOST_ADMISSION_LOCK_KEY = 3878001

#: Layer names are low-cardinality by construction: they never carry a
#: provider, credential, host, or repository identity (#3878 AC11).
LIMITING_LAYER_HOST_CAPACITY = "generic_host_capacity"
LIMITING_LAYER_COLD_LAUNCH_RATE = "generic_host_cold_launch_rate"


@dataclass(frozen=True)
class GenericHostCapacityDecision:
    """One admission verdict for a generic host allocation."""

    admitted: bool
    limiting_layer: str | None
    active_hosts: int
    host_capacity: int
    recent_cold_launches: int
    cold_launch_burst: int
    cold_launch_window_seconds: int
    retry_after_seconds: int

    def as_payload(self) -> dict[str, Any]:
        """Return the compact, identity-free projection for workflow history."""

        return {
            "admitted": self.admitted,
            "limitingLayer": self.limiting_layer,
            "activeHosts": self.active_hosts,
            "hostCapacity": self.host_capacity,
            "recentColdLaunches": self.recent_cold_launches,
            "coldLaunchBurst": self.cold_launch_burst,
            "coldLaunchWindowSeconds": self.cold_launch_window_seconds,
            "retryAfterSeconds": self.retry_after_seconds,
        }

    @property
    def waiting_reason(self) -> str:
        """Return an operator-facing reason naming the actual limiting layer."""

        if self.admitted:
            return "Generic host capacity is available."
        if self.limiting_layer == LIMITING_LAYER_COLD_LAUNCH_RATE:
            return (
                "Waiting for generic host cold-launch capacity; "
                f"missing_condition={LIMITING_LAYER_COLD_LAUNCH_RATE}; "
                f"launches_in_window={self.recent_cold_launches}; "
                f"cold_launch_burst={self.cold_launch_burst}; "
                f"window_seconds={self.cold_launch_window_seconds}."
            )
        return (
            "Waiting for generic host capacity; "
            f"missing_condition={LIMITING_LAYER_HOST_CAPACITY}; "
            f"active_hosts={self.active_hosts}; "
            f"host_capacity={self.host_capacity}."
        )


def evaluate_generic_host_capacity(
    *,
    active_hosts: int,
    recent_cold_launches: int,
    host_capacity: int,
    cold_launch_burst: int,
    cold_launch_window_seconds: int,
) -> GenericHostCapacityDecision:
    """Return the admission verdict for one generic host allocation.

    The aggregate ceiling is checked first: when the machine is already full,
    reporting the cold-launch layer would send the operator after the wrong
    limit.
    """

    if active_hosts >= host_capacity:
        return GenericHostCapacityDecision(
            admitted=False,
            limiting_layer=LIMITING_LAYER_HOST_CAPACITY,
            active_hosts=active_hosts,
            host_capacity=host_capacity,
            recent_cold_launches=recent_cold_launches,
            cold_launch_burst=cold_launch_burst,
            cold_launch_window_seconds=cold_launch_window_seconds,
            # A host is released by cleanup, not by a clock, so re-check on the
            # cold-launch window rather than inventing a longer backoff.
            retry_after_seconds=cold_launch_window_seconds,
        )
    if recent_cold_launches >= cold_launch_burst:
        return GenericHostCapacityDecision(
            admitted=False,
            limiting_layer=LIMITING_LAYER_COLD_LAUNCH_RATE,
            active_hosts=active_hosts,
            host_capacity=host_capacity,
            recent_cold_launches=recent_cold_launches,
            cold_launch_burst=cold_launch_burst,
            cold_launch_window_seconds=cold_launch_window_seconds,
            retry_after_seconds=cold_launch_window_seconds,
        )
    return GenericHostCapacityDecision(
        admitted=True,
        limiting_layer=None,
        active_hosts=active_hosts,
        host_capacity=host_capacity,
        recent_cold_launches=recent_cold_launches,
        cold_launch_burst=cold_launch_burst,
        cold_launch_window_seconds=cold_launch_window_seconds,
        retry_after_seconds=0,
    )


class GenericHostCapacityAdmission:
    """Evaluate aggregate host and cold-launch limits against the durable ledger."""

    def __init__(
        self,
        *,
        session_factory: Any,
        host_capacity: int,
        cold_launch_burst: int,
        cold_launch_window_seconds: int,
    ) -> None:
        if host_capacity < 1:
            raise ValueError("host_capacity must be positive")
        if cold_launch_burst < 1:
            raise ValueError("cold_launch_burst must be positive")
        if cold_launch_window_seconds < 1:
            raise ValueError("cold_launch_window_seconds must be positive")
        self._session_factory = session_factory
        self._host_capacity = host_capacity
        self._cold_launch_burst = cold_launch_burst
        self._cold_launch_window_seconds = cold_launch_window_seconds

    @classmethod
    def from_environment(
        cls,
        *,
        session_factory: Any,
        env: Mapping[str, Any] | None = None,
    ) -> "GenericHostCapacityAdmission":
        from moonmind.omnigent.settings import (
            generic_host_capacity,
            generic_host_cold_launch_burst,
            generic_host_cold_launch_window_seconds,
        )

        return cls(
            session_factory=session_factory,
            host_capacity=generic_host_capacity(env=env),
            cold_launch_burst=generic_host_cold_launch_burst(env=env),
            cold_launch_window_seconds=(
                generic_host_cold_launch_window_seconds(env=env)
            ),
        )

    async def observe(
        self, *, now: datetime | None = None
    ) -> tuple[int, int]:
        """Return (active hosts, cold launches inside the current window)."""

        from sqlalchemy import func, select

        from api_service.db.models import OmnigentHostLeaseRecordV2

        async with self._session_factory() as session:
            return await self.observe_within(session, now=now)

    async def observe_within(
        self, session: Any, *, now: datetime | None = None
    ) -> tuple[int, int]:
        """Count active hosts and in-window launches inside ``session``."""

        from sqlalchemy import func, select

        from api_service.db.models import OmnigentHostLeaseRecordV2

        observed_at = now or datetime.now(UTC)
        window_start = observed_at - timedelta(
            seconds=self._cold_launch_window_seconds
        )
        active = await session.execute(
            select(func.count())
            .select_from(OmnigentHostLeaseRecordV2)
            .where(
                OmnigentHostLeaseRecordV2.status.in_(
                    ACTIVE_HOST_LEASE_STATUSES
                )
            )
        )
        recent = await session.execute(
            select(func.count())
            .select_from(OmnigentHostLeaseRecordV2)
            .where(
                # Status-independent: a row inside the window is launch
                # evidence even after it failed or finished cleanup.
                OmnigentHostLeaseRecordV2.created_at >= window_start,
            )
        )
        return int(active.scalar() or 0), int(recent.scalar() or 0)

    async def lock_for_admission(self, session: Any) -> None:
        """Serialize concurrent admissions for the caller's transaction.

        PostgreSQL takes a transaction-scoped advisory lock, so a second
        allocator blocks until the first has inserted its lease row and
        therefore counts it. SQLite serializes writes and has no advisory
        locks, so the surrounding transaction already provides the guarantee.
        """

        from sqlalchemy import text

        dialect = getattr(getattr(session, "bind", None), "dialect", None)
        name = getattr(dialect, "name", "") or ""
        if not name:
            engine = getattr(session, "get_bind", None)
            if callable(engine):
                try:
                    name = getattr(engine().dialect, "name", "") or ""
                except Exception:
                    name = ""
        if name.startswith("postgres"):
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": GENERIC_HOST_ADMISSION_LOCK_KEY},
            )

    async def evaluate(
        self,
        *,
        already_allocated: bool = False,
        now: datetime | None = None,
    ) -> GenericHostCapacityDecision:
        """Return the current admission verdict.

        ``already_allocated`` is set when this execution's own host lease
        already exists — a retry or a resumed attempt must never be refused by
        the capacity it is already counted in.
        """

        active_hosts, recent_cold_launches = await self.observe(now=now)
        return self._verdict(
            active_hosts=active_hosts,
            recent_cold_launches=recent_cold_launches,
            already_allocated=already_allocated,
        )

    async def evaluate_within(
        self,
        session: Any,
        *,
        already_allocated: bool = False,
        now: datetime | None = None,
    ) -> GenericHostCapacityDecision:
        """Return the admission verdict inside the caller's transaction.

        The caller must insert its durable reservation in this same
        transaction: counting in one transaction and reserving in another is
        the race this method exists to close (#3878).
        """

        await self.lock_for_admission(session)
        active_hosts, recent_cold_launches = await self.observe_within(
            session, now=now
        )
        return self._verdict(
            active_hosts=active_hosts,
            recent_cold_launches=recent_cold_launches,
            already_allocated=already_allocated,
        )

    def _verdict(
        self,
        *,
        active_hosts: int,
        recent_cold_launches: int,
        already_allocated: bool,
    ) -> GenericHostCapacityDecision:
        if already_allocated:
            return GenericHostCapacityDecision(
                admitted=True,
                limiting_layer=None,
                active_hosts=active_hosts,
                host_capacity=self._host_capacity,
                recent_cold_launches=recent_cold_launches,
                cold_launch_burst=self._cold_launch_burst,
                cold_launch_window_seconds=self._cold_launch_window_seconds,
                retry_after_seconds=0,
            )
        return evaluate_generic_host_capacity(
            active_hosts=active_hosts,
            recent_cold_launches=recent_cold_launches,
            host_capacity=self._host_capacity,
            cold_launch_burst=self._cold_launch_burst,
            cold_launch_window_seconds=self._cold_launch_window_seconds,
        )


__all__ = [
    "ACTIVE_HOST_LEASE_STATUSES",
    "LIMITING_LAYER_COLD_LAUNCH_RATE",
    "LIMITING_LAYER_HOST_CAPACITY",
    "GenericHostCapacityAdmission",
    "GenericHostCapacityDecision",
    "evaluate_generic_host_capacity",
]
