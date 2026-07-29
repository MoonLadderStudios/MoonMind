"""Bounded, retry-safe, observable upstream synchronization (MoonLadderStudios/MoonMind#3517).

These cover the AC3 discovery/synchronization journey and the AC4 launch-safety
requirement that a transient outage degrades to last-known-stale evidence rather
than erasing it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, OmnigentUpstreamAgentProjection
from api_service.services.omnigent_agent_profile_service import (
    projection_identity,
    projection_readiness,
    synchronize_endpoint_inventory,
)

pytestmark = [pytest.mark.asyncio]

_NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture()
async def session(tmp_path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/upstream.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db:
        yield db
    await engine.dispose()


def _agents(*ids: str) -> list[dict[str, object]]:
    return [
        {
            "id": agent_id,
            "version": "v1",
            "harness": "codex-native",
            "capabilities": ["session.start"],
        }
        for agent_id in ids
    ]


async def _projections(session: AsyncSession) -> list[OmnigentUpstreamAgentProjection]:
    return list((await session.execute(
        select(OmnigentUpstreamAgentProjection)
    )).scalars())


async def test_success_upserts_and_reports_observable_summary(session: AsyncSession):
    async def list_agents():
        return _agents("agent-a", "agent-b")

    result = await synchronize_endpoint_inventory(
        session,
        endpoint_ref="default",
        bridge_mode="proxy",
        list_agents=list_agents,
        now=_NOW,
    )

    assert result["status"] == "synced"
    assert result["endpointRef"] == "default"
    assert result["bridgeMode"] == "proxy"
    assert result["syncedCount"] == 2
    assert result["projectionCount"] == 2
    assert result["syncedAt"] == _NOW.isoformat()

    rows = await _projections(session)
    assert {row.upstream_id for row in rows} == {"agent-a", "agent-b"}
    ready = projection_readiness(rows[0], now=_NOW)
    assert ready["ready"] is True
    assert ready["freshness"] == "fresh"


async def test_synchronization_is_retry_safe_and_marks_disappearance(session: AsyncSession):
    async def full():
        return _agents("agent-a", "agent-b")

    async def shrunk():
        return _agents("agent-a")

    first = await synchronize_endpoint_inventory(
        session, endpoint_ref="default", bridge_mode="proxy",
        list_agents=full, now=_NOW,
    )
    # Re-running the same inventory is idempotent: no new rows are created.
    repeat = await synchronize_endpoint_inventory(
        session, endpoint_ref="default", bridge_mode="proxy",
        list_agents=full, now=_NOW + timedelta(seconds=1),
    )
    assert first["projectionCount"] == repeat["projectionCount"] == 2
    assert len(await _projections(session)) == 2

    later = _NOW + timedelta(minutes=1)
    await synchronize_endpoint_inventory(
        session, endpoint_ref="default", bridge_mode="proxy",
        list_agents=shrunk, now=later,
    )

    disappeared = await session.get(
        OmnigentUpstreamAgentProjection,
        projection_identity("default", "agent-b", "v1"),
    )
    surviving = await session.get(
        OmnigentUpstreamAgentProjection,
        projection_identity("default", "agent-a", "v1"),
    )
    assert disappeared.available is False
    assert disappeared.error
    assert surviving.available is True
    # A disappeared upstream identity blocks new launches without being deleted.
    assert projection_readiness(disappeared, now=later)["ready"] is False


async def test_transient_outage_degrades_to_last_known_stale(session: AsyncSession):
    async def healthy():
        return _agents("agent-a")

    await synchronize_endpoint_inventory(
        session, endpoint_ref="default", bridge_mode="proxy",
        list_agents=healthy, now=_NOW,
    )

    class _Outage(RuntimeError):
        pass

    async def outage():
        raise _Outage("bridge timeout\nwith newline")

    outage_at = _NOW + timedelta(seconds=30)
    result = await synchronize_endpoint_inventory(
        session, endpoint_ref="default", bridge_mode="proxy",
        list_agents=outage, now=outage_at,
    )

    assert result["status"] == "degraded"
    assert result["retainedStaleProjections"] == 1
    # The safe error is bounded and single-line.
    assert "\n" not in result["error"]
    assert result["error"] == "bridge timeout with newline"

    row = await session.get(
        OmnigentUpstreamAgentProjection,
        projection_identity("default", "agent-a", "v1"),
    )
    # Last-known metadata is retained, but marked explicitly stale and unready.
    assert row.metadata_snapshot["id"] == "agent-a"
    assert row.error == "bridge timeout with newline"
    readiness = projection_readiness(row, now=outage_at)
    assert readiness["ready"] is False
    assert readiness["freshness"] == "stale"


async def test_outage_before_any_sync_records_nothing_but_reports_degraded(session: AsyncSession):
    async def outage():
        raise RuntimeError("")

    result = await synchronize_endpoint_inventory(
        session, endpoint_ref="default", bridge_mode="embedded",
        list_agents=outage, now=_NOW,
    )

    assert result["status"] == "degraded"
    assert result["retainedStaleProjections"] == 0
    # An empty message falls back to the exception class name, never blank.
    assert result["error"] == "RuntimeError"
    assert await _projections(session) == []
