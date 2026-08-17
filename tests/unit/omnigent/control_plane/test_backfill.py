"""Backfill tests for the Omnigent control-plane decomposition.

Issue MoonLadderStudios/MoonMind#3703 (Migration and compatibility). Covers the
required migration tests: zero/one/seven existing bridge rows for one provider
session, duplicate rows with complementary vs conflicting immutable authority,
previously issued chat-binding aliases, idempotent repeat dry-run/apply, and no
loss of artifact/event/terminal/publication/remediation evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    OmnigentBridgeSession,
    OmnigentChatBindingAlias,
    OmnigentSession,
    OmnigentTurnAttempt,
)
from moonmind.omnigent.control_plane.backfill import (
    BridgeRowView,
    plan_backfill,
    run_backfill,
)

_BASE_TIME = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)


def _view(
    bridge_id: str,
    *,
    workflow: str = "wf-1",
    provider: str = "omnigent",
    profile: str = "omnigent.server.v1",
    provider_session: str | None = "prov-1",
    chat_binding: str | None = None,
    status: str = "running",
    idem: str | None = None,
    index: int = 0,
    refs: dict | None = None,
    terminal_refs: dict | None = None,
) -> BridgeRowView:
    return BridgeRowView(
        bridge_session_id=bridge_id,
        provider=provider,
        compatibility_profile=profile,
        moonmind_workflow_id=workflow,
        moonmind_run_id="run-1",
        moonmind_agent_run_id="agent-1",
        step_execution_id="step-1",
        idempotency_key=idem or f"idem-{bridge_id}",
        provider_session_id=provider_session,
        chat_binding_id=chat_binding,
        status=status,
        first_message_state="posted",
        first_message_digest="sha256:first",
        provider_profile_id="prof-1",
        credential_generation=1,
        host_binding_ref="hb-1",
        host_lease_ref="hl-1",
        terminal_refs=terminal_refs or {},
        metadata={},
        refs=refs or {},
        created_at=_BASE_TIME + timedelta(seconds=index),
    )


def test_plan_zero_rows() -> None:
    plan = plan_backfill([])
    assert plan.sessions == ()
    assert plan.quarantined == ()


def test_plan_single_row() -> None:
    plan = plan_backfill([_view("brs-1", chat_binding="chatb_1")])
    assert len(plan.sessions) == 1
    session = plan.sessions[0]
    assert session.provider_session_id == "prov-1"
    assert session.chat_binding_id == "chatb_1"
    assert len(session.turn_attempts) == 1
    assert session.turn_attempts[0].turn_kind == "instruction"
    assert session.aliases == ()


def test_plan_seven_rows_one_provider_session() -> None:
    rows = [
        _view(f"brs-{i}", chat_binding=f"chatb_{i}", idem=f"idem-{i}", index=i)
        for i in range(7)
    ]
    plan = plan_backfill(rows)
    # Seven bridge rows collapse to exactly one canonical authority.
    assert len(plan.sessions) == 1
    session = plan.sessions[0]
    # One canonical chat binding; the other six become safe aliases.
    assert session.chat_binding_id == "chatb_0"
    assert len(session.aliases) == 6
    assert all(a.resolution == "alias" for a in session.aliases)
    assert all(a.canonical_session_id == session.session_id for a in session.aliases)
    # Every request row becomes a turn attempt; the first is the instruction and
    # the rest are continuation turns of it.
    assert len(session.turn_attempts) == 7
    assert session.turn_attempts[0].turn_kind == "instruction"
    assert all(t.turn_kind == "continuation" for t in session.turn_attempts[1:])
    first_id = session.turn_attempts[0].turn_attempt_id
    assert all(
        t.continuation_of_attempt_id == first_id for t in session.turn_attempts[1:]
    )


def test_plan_complementary_rows_single_canonical() -> None:
    rows = [
        _view("brs-a", chat_binding="chatb_a", status="running", index=0),
        _view("brs-b", chat_binding="chatb_b", status="running", index=1),
    ]
    plan = plan_backfill(rows)
    assert len(plan.sessions) == 1
    assert plan.quarantined == ()


def test_plan_conflicting_authority_quarantined() -> None:
    # Same provider session + Workflow but conflicting immutable provider/profile
    # authority must fail closed, never chosen by updated_at.
    rows = [
        _view("brs-a", chat_binding="chatb_a", profile="omnigent.server.v1"),
        _view("brs-b", chat_binding="chatb_b", profile="omnigent.compat.v2"),
    ]
    plan = plan_backfill(rows)
    assert plan.sessions == ()
    assert len(plan.quarantined) == 1
    group = plan.quarantined[0]
    assert group.reason == "conflicting_immutable_authority"
    assert {a.chat_binding_id for a in group.aliases} == {"chatb_a", "chatb_b"}
    assert all(a.resolution == "fail_closed" for a in group.aliases)
    assert all(a.diagnostic_code == "ambiguous_authority" for a in group.aliases)


def test_plan_terminality_conservative() -> None:
    # A single terminal continuation row does not terminalize the canonical
    # session while another request row is still active (#3685 regression class).
    rows = [
        _view("brs-a", status="running", idem="idem-a", index=0),
        _view("brs-b", status="failed", idem="idem-b", index=1),
    ]
    plan = plan_backfill(rows)
    assert plan.sessions[0].terminal_state is None

    # When every request row is terminal, the canonical session terminalizes.
    rows = [
        _view("brs-a", status="completed", idem="idem-a", index=0),
        _view("brs-b", status="completed", idem="idem-b", index=1),
    ]
    plan = plan_backfill(rows)
    assert plan.sessions[0].terminal_state == "completed"


def test_plan_preserves_all_evidence() -> None:
    refs = {"rawEventsRef": "art://raw", "diagnosticsRef": "art://diag"}
    terminal = {"cleanupState": "completed", "publicationRef": "art://pub"}
    plan = plan_backfill(
        [_view("brs-x", chat_binding="chatb_x", refs=refs, terminal_refs=terminal)]
    )
    preserved = plan.sessions[0].metadata["backfill"]["preservedRefs"]["brs-x"]
    assert preserved["refs"]["rawEventsRef"] == "art://raw"
    assert preserved["refs"]["diagnosticsRef"] == "art://diag"
    assert preserved["terminalRefs"]["publicationRef"] == "art://pub"


# ---------------------------------------------------------------------------
# DB-backed idempotent apply / dry-run
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bf.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield maker
    await engine.dispose()


def _bridge_row(bridge_id: str, *, chat_binding: str, idem: str, index: int, status="running"):
    return OmnigentBridgeSession(
        bridge_session_id=bridge_id,
        chat_binding_id=chat_binding,
        provider="omnigent",
        compatibility_profile="omnigent.server.v1",
        moonmind_workflow_id="wf-db",
        moonmind_run_id="run-db",
        moonmind_agent_run_id="agent-db",
        idempotency_key=idem,
        omnigent_endpoint_ref="endpoint",
        omnigent_session_id="prov-db",
        host_type="managed",
        status=status,
        first_message_digest="sha256:first",
        raw_events_ref="art://raw",
        diagnostics_ref="art://diag",
        terminal_refs={"publicationRef": "art://pub"},
        metadata_={},
        created_at=_BASE_TIME + timedelta(seconds=index),
    )


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(session_factory) -> None:
    async with session_factory() as db:
        db.add(_bridge_row("brs-1", chat_binding="chatb_1", idem="idem-1", index=0))
        await db.commit()
    async with session_factory() as db:
        report = await run_backfill(db, apply=False)
    assert report.applied is False
    assert report.sessions_planned == 1
    async with session_factory() as db:
        count = (await db.execute(select(OmnigentSession))).scalars().all()
    assert count == []


@pytest.mark.asyncio
async def test_apply_is_idempotent(session_factory) -> None:
    async with session_factory() as db:
        for i in range(7):
            db.add(
                _bridge_row(
                    f"brs-{i}", chat_binding=f"chatb_{i}", idem=f"idem-{i}", index=i
                )
            )
        await db.commit()

    async with session_factory() as db:
        first = await run_backfill(db, apply=True)
    assert first.sessions_created == 1
    assert first.turn_attempts_created == 7
    assert first.aliases_created == 6

    # Repeat apply is a no-op: deterministic ids mean nothing new is created.
    async with session_factory() as db:
        second = await run_backfill(db, apply=True)
    assert second.sessions_created == 0
    assert second.turn_attempts_created == 0
    assert second.aliases_created == 0

    async with session_factory() as db:
        sessions = (await db.execute(select(OmnigentSession))).scalars().all()
        attempts = (await db.execute(select(OmnigentTurnAttempt))).scalars().all()
        aliases = (await db.execute(select(OmnigentChatBindingAlias))).scalars().all()
    assert len(sessions) == 1
    assert len(attempts) == 7
    assert len(aliases) == 6
    # Legacy bridge rows are preserved untouched.
    async with session_factory() as db:
        bridge = (await db.execute(select(OmnigentBridgeSession))).scalars().all()
    assert len(bridge) == 7
    # No evidence loss: refs preserved on the canonical session metadata.
    preserved = sessions[0].metadata_["backfill"]["preservedRefs"]
    assert preserved["brs-0"]["refs"]["rawEventsRef"] == "art://raw"
    assert preserved["brs-0"]["terminalRefs"]["publicationRef"] == "art://pub"
