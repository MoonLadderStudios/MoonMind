"""Schema, invariant, and repository-boundary tests for the control plane.

Issue MoonLadderStudios/MoonMind#3703 ([Omnigent control plane 2/11]). Covers
the required schema/invariant tests: one canonical chat authority per session
scope, unique logical command/turn idempotency, attempts that cannot carry
chat-binding authority, terminal session state that cannot be overwritten by a
non-terminal update, observation sequence/dedup constraints, and unknown schema
versions failing per the compatibility policy.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, OmnigentTurnAttempt
from moonmind.omnigent.control_plane import (
    ChatBindingAuthorityError,
    ChatBindingAliasRepository,
    CommandRepository,
    ConflictingAuthorityError,
    DecisionRepository,
    ObservationRepository,
    SessionRepository,
    TerminalSessionOverwriteError,
    TurnAttemptRepository,
    UnknownSchemaVersionError,
    compute_authority_scope,
    create_canonical_session,
)


@pytest_asyncio.fixture()
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/cp.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def _new_session(sessions: SessionRepository, **overrides):
    kwargs = dict(
        moonmind_workflow_id="wf-1",
        provider="omnigent",
        compatibility_profile="omnigent.server.v1",
    )
    kwargs.update(overrides)
    return await sessions.create(**kwargs)


# ---------------------------------------------------------------------------
# One canonical chat authority per session scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_canonical_authority_per_scope(session_factory) -> None:
    async with session_factory() as db:
        sessions = SessionRepository(db)
        await _new_session(sessions, provider_session_id="prov-1")
        await db.commit()
    async with session_factory() as db:
        sessions = SessionRepository(db)
        with pytest.raises(IntegrityError):
            await _new_session(sessions, provider_session_id="prov-1")


@pytest.mark.asyncio
async def test_continuation_turn_reuses_session_and_cannot_add_second_binding(
    session_factory,
) -> None:
    async with session_factory() as db:
        record, first_attempt = await create_canonical_session(
            db,
            moonmind_workflow_id="wf-9",
            provider="omnigent",
            compatibility_profile="omnigent.server.v1",
            idempotency_key="idem-first",
        )
        await db.commit()
    assert record.chat_binding_id is not None
    original_binding = record.chat_binding_id

    # A continuation turn reuses the canonical session and never allocates a
    # second chat binding, even with a fresh idempotency key.
    async with session_factory() as db:
        sessions = SessionRepository(db)
        turns = TurnAttemptRepository(db)
        continuation = await turns.create(
            session_id=record.session_id,
            idempotency_key="idem-continuation",
            turn_kind="continuation",
            continuation_of_attempt_id=first_attempt.turn_attempt_id,
        )
        after = await sessions.allocate_chat_binding(record.session_id)
        await db.commit()
    assert continuation.session_id == record.session_id
    assert after.chat_binding_id == original_binding

    # Re-binding a different id fails closed.
    async with session_factory() as db:
        sessions = SessionRepository(db)
        with pytest.raises(ChatBindingAuthorityError):
            await sessions.allocate_chat_binding(
                record.session_id, chat_binding_id="chatb_other"
            )


# ---------------------------------------------------------------------------
# Unique logical command and turn idempotency identities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_turn_attempt_idempotency_is_unique(session_factory) -> None:
    async with session_factory() as db:
        sessions = SessionRepository(db)
        record = await _new_session(sessions)
        turns = TurnAttemptRepository(db)
        await turns.create(session_id=record.session_id, idempotency_key="dup")
        await db.commit()
    async with session_factory() as db:
        turns = TurnAttemptRepository(db)
        with pytest.raises(IntegrityError):
            await turns.create(session_id=record.session_id, idempotency_key="dup")


@pytest.mark.asyncio
async def test_command_idempotency_is_unique(session_factory) -> None:
    async with session_factory() as db:
        sessions = SessionRepository(db)
        record = await _new_session(sessions)
        commands = CommandRepository(db)
        await commands.record(
            session_id=record.session_id,
            command_type="submit_turn",
            command_idempotency_key="cmd-1",
        )
        await db.commit()
    async with session_factory() as db:
        commands = CommandRepository(db)
        with pytest.raises(IntegrityError):
            await commands.record(
                session_id=record.session_id,
                command_type="submit_turn",
                command_idempotency_key="cmd-1",
            )


# ---------------------------------------------------------------------------
# Attempts cannot carry chat-binding authority
# ---------------------------------------------------------------------------


def test_turn_attempt_model_has_no_chat_binding_column() -> None:
    assert "chat_binding_id" not in OmnigentTurnAttempt.__table__.columns


@pytest.mark.asyncio
async def test_turn_attempt_rejects_chat_binding_metadata(session_factory) -> None:
    async with session_factory() as db:
        sessions = SessionRepository(db)
        record = await _new_session(sessions)
        turns = TurnAttemptRepository(db)
        with pytest.raises(ChatBindingAuthorityError):
            await turns.create(
                session_id=record.session_id,
                idempotency_key="idem",
                metadata={"chat_binding_id": "chatb_x"},
            )


# ---------------------------------------------------------------------------
# Terminal session state cannot be overwritten by a non-terminal update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminal_session_not_overwritten_by_nonterminal_update(
    session_factory,
) -> None:
    async with session_factory() as db:
        sessions = SessionRepository(db)
        record = await _new_session(sessions)
        await sessions.mark_terminal(record.session_id, terminal_state="completed")
        await db.commit()

        with pytest.raises(TerminalSessionOverwriteError):
            await sessions.update_states(
                record.session_id, observed_state="running"
            )
        with pytest.raises(TerminalSessionOverwriteError):
            await sessions.mark_terminal(
                record.session_id, terminal_state="failed"
            )
        # Idempotent re-terminalization with the same state is allowed.
        again = await sessions.mark_terminal(
            record.session_id, terminal_state="completed"
        )
        assert again.terminal_state == "completed"


@pytest.mark.asyncio
async def test_attempt_terminality_separate_from_session_terminality(
    session_factory,
) -> None:
    async with session_factory() as db:
        sessions = SessionRepository(db)
        record = await _new_session(sessions)
        turns = TurnAttemptRepository(db)
        attempt = await turns.create(
            session_id=record.session_id, idempotency_key="t1"
        )
        # A turn attempt terminalizing does NOT terminalize the session.
        await turns.update_state(
            attempt.turn_attempt_id, state="terminal", outcome="failed"
        )
        await db.commit()
        refreshed = await sessions.get(record.session_id)
    assert refreshed is not None
    assert refreshed.terminal_state is None
    assert refreshed.is_terminal is False


# ---------------------------------------------------------------------------
# Observation sequence and deduplication constraints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observation_dedup_constraint(session_factory) -> None:
    async with session_factory() as db:
        sessions = SessionRepository(db)
        record = await _new_session(sessions)
        obs = ObservationRepository(db)
        await obs.append(
            session_id=record.session_id,
            observation_kind="provider_event_batch",
            source="provider",
            observed_at=datetime.now(tz=UTC),
            dedup_identity="dedup-1",
            source_sequence=1,
        )
        await db.commit()
    async with session_factory() as db:
        obs = ObservationRepository(db)
        with pytest.raises(IntegrityError):
            await obs.append(
                session_id=record.session_id,
                observation_kind="provider_event_batch",
                source="provider",
                observed_at=datetime.now(tz=UTC),
                dedup_identity="dedup-1",
                source_sequence=2,
            )


@pytest.mark.asyncio
async def test_observation_sequence_constraint(session_factory) -> None:
    async with session_factory() as db:
        sessions = SessionRepository(db)
        record = await _new_session(sessions)
        obs = ObservationRepository(db)
        await obs.append(
            session_id=record.session_id,
            observation_kind="provider_event_batch",
            source="provider",
            observed_at=datetime.now(tz=UTC),
            dedup_identity="a",
            source_sequence=5,
        )
        await db.commit()
    async with session_factory() as db:
        obs = ObservationRepository(db)
        with pytest.raises(IntegrityError):
            await obs.append(
                session_id=record.session_id,
                observation_kind="provider_event_batch",
                source="provider",
                observed_at=datetime.now(tz=UTC),
                dedup_identity="b",
                source_sequence=5,
            )


@pytest.mark.asyncio
async def test_unknown_schema_version_fails_closed(session_factory) -> None:
    async with session_factory() as db:
        sessions = SessionRepository(db)
        record = await _new_session(sessions)
        obs = ObservationRepository(db)
        with pytest.raises(UnknownSchemaVersionError):
            await obs.append(
                session_id=record.session_id,
                observation_kind="provider_event_batch",
                source="provider",
                observed_at=datetime.now(tz=UTC),
                dedup_identity="c",
                schema_version=999,
            )
        with pytest.raises(UnknownSchemaVersionError):
            await sessions.create(
                moonmind_workflow_id="wf-x",
                provider="omnigent",
                compatibility_profile="omnigent.server.v1",
                schema_version=999,
            )


# ---------------------------------------------------------------------------
# Atomic first-turn creation and authority scope
# ---------------------------------------------------------------------------


def test_authority_scope_requires_workflow_and_provider() -> None:
    with pytest.raises(ConflictingAuthorityError):
        compute_authority_scope(
            moonmind_workflow_id="", provider="omnigent", provider_session_id="p"
        )
    assert (
        compute_authority_scope(
            moonmind_workflow_id="wf",
            provider="omnigent",
            provider_session_id="p",
        )
        == "wf:wf|provider:omnigent|session:p"
    )


@pytest.mark.asyncio
async def test_create_canonical_session_is_atomic(session_factory) -> None:
    async with session_factory() as db:
        record, attempt = await create_canonical_session(
            db,
            moonmind_workflow_id="wf-atomic",
            provider="omnigent",
            compatibility_profile="omnigent.server.v1",
            idempotency_key="idem-atomic",
            instruction_digest="sha256:first",
        )
        await db.commit()
    async with session_factory() as db:
        sessions = SessionRepository(db)
        turns = TurnAttemptRepository(db)
        stored = await sessions.get(record.session_id)
        stored_attempt = await turns.get_by_idempotency_key("idem-atomic")
    assert stored is not None
    assert stored.chat_binding_id is not None
    assert stored.active_turn_attempt_id == attempt.turn_attempt_id
    assert stored_attempt is not None
    assert stored_attempt.turn_kind == "instruction"


# ---------------------------------------------------------------------------
# Chat-binding alias resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_binding_alias_resolution(session_factory) -> None:
    async with session_factory() as db:
        sessions = SessionRepository(db)
        record = await _new_session(sessions, provider_session_id="prov-alias")
        record = await sessions.allocate_chat_binding(record.session_id)
        aliases = ChatBindingAliasRepository(db)
        await aliases.add_alias(
            chat_binding_id="chatb_old",
            canonical_session_id=record.session_id,
        )
        await aliases.add_fail_closed(
            chat_binding_id="chatb_bad", diagnostic_code="ambiguous_authority"
        )
        await db.commit()

    async with session_factory() as db:
        aliases = ChatBindingAliasRepository(db)
        # Canonical binding resolves directly to its session.
        canonical = await aliases.resolve(record.chat_binding_id)
        assert canonical.canonical_session_id == record.session_id
        # Old duplicate binding resolves as a safe alias to canonical authority.
        old = await aliases.resolve("chatb_old")
        assert old.resolution == "alias"
        assert old.canonical_session_id == record.session_id
        # Ambiguous binding resolves to a stable fail-closed diagnostic.
        bad = await aliases.resolve("chatb_bad")
        assert bad.resolution == "fail_closed"
        assert bad.canonical_session_id is None
        assert bad.diagnostic_code == "ambiguous_authority"
        # Unknown binding never exposes provider ids; it fails closed.
        unknown = await aliases.resolve("chatb_unknown")
        assert unknown.resolution == "fail_closed"
        assert unknown.canonical_session_id is None
