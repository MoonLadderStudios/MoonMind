"""Unit tests for the Omnigent control-plane durable aggregates.

Source: MoonLadderStudios/MoonMind#3703 ([Omnigent control plane 2/11]).

Covers schema/invariant behaviour, the deterministic bridge-row backfill, and
historical read projections against the canonical aggregate. The decisive
uniqueness/concurrency cases are additionally exercised on PostgreSQL in
``tests/integration/omnigent/test_control_plane_postgres.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    OmnigentBridgeSession,
    OmnigentBridgeSessionEvent,
    OmnigentExecutionPlanRecord,
    OmnigentRuntimeBindingRecord,
    OmnigentSession,
    OmnigentTurnAttempt,
)
from moonmind.omnigent.control_plane import (
    ALIAS_STATE_QUARANTINED,
    TURN_STATE_TERMINAL,
    CommandIdempotencyConflictError,
    ConflictingSessionAuthorityError,
    ControlPlaneOutcome,
    FencingConflictError,
    FencingScope,
    OmnigentControlPlaneStore,
    RevisionConflictError,
    TerminalSessionOverwriteError,
    TurnIdempotencyConflictError,
    UnknownSchemaVersionError,
    compute_digest,
    plan_backfill,
    run_backfill,
)
from moonmind.omnigent.control_plane.turn_commands import (
    CanonicalSessionBootstrap,
    CanonicalTurnCommandService,
)


@pytest_asyncio.fixture()
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/control_plane.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest_asyncio.fixture()
async def store(session_factory):
    return OmnigentControlPlaneStore(session_factory)


def _bridge_row(
    bridge_session_id: str,
    *,
    workflow_id: str = "wf-1",
    provider: str = "codex",
    compatibility_profile: str = "codex-default",
    provider_session_id: str | None = None,
    chat_binding_id: str | None = None,
    agent_run_id: str | None = None,
    idempotency_key: str | None = None,
    status: str = "active",
    terminal_refs: dict | None = None,
    external_state_ref: str | None = None,
    diagnostics_ref: str | None = None,
) -> OmnigentBridgeSession:
    return OmnigentBridgeSession(
        bridge_session_id=bridge_session_id,
        provider=provider,
        compatibility_profile=compatibility_profile,
        moonmind_workflow_id=workflow_id,
        moonmind_agent_run_id=agent_run_id or f"agent-{bridge_session_id}",
        idempotency_key=idempotency_key or f"idem-{bridge_session_id}",
        omnigent_endpoint_ref="endpoint",
        omnigent_session_id=provider_session_id,
        chat_binding_id=chat_binding_id,
        host_type="proxy",
        status=status,
        terminal_refs=terminal_refs or {},
        external_state_ref=external_state_ref,
        diagnostics_ref=diagnostics_ref,
    )


def _bridge_event(
    event_id: str,
    *,
    bridge_session_id: str,
    sequence: int,
    artifact_ref: str | None = None,
    event_type: str = "message",
    direction: str = "inbound",
    normalized_status: str | None = None,
) -> OmnigentBridgeSessionEvent:
    return OmnigentBridgeSessionEvent(
        event_id=event_id,
        bridge_session_id=bridge_session_id,
        sequence=sequence,
        deduplication_key=f"dedup-{event_id}",
        timestamp=datetime(2026, 8, 18, tzinfo=UTC),
        direction=direction,
        event_type=event_type,
        normalized_status=normalized_status,
        artifact_ref=artifact_ref,
        metadata_={},
    )


async def _seed_bridge_rows(session_factory, rows: list) -> None:
    async with session_factory() as session:
        for row in rows:
            session.add(row)
        await session.commit()


# --- Schema / invariant tests -----------------------------------------------


@pytest.mark.asyncio
async def test_one_canonical_authority_per_provider_session_scope(store) -> None:
    async with store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1",
            moonmind_workflow_id="wf-1",
            provider="codex",
            provider_session_ref="psess-1",
        )
    with pytest.raises(ConflictingSessionAuthorityError):
        async with store.transaction() as repos:
            await repos.sessions.create(
                session_id="s2",
                moonmind_workflow_id="wf-1",
                provider="codex",
                provider_session_ref="psess-1",
            )


@pytest.mark.asyncio
async def test_one_chat_binding_maps_to_one_session(store) -> None:
    async with store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1",
            moonmind_workflow_id="wf-1",
            provider="codex",
            provider_session_ref="psess-1",
            chat_binding_id="cb-shared",
        )
    with pytest.raises(ConflictingSessionAuthorityError):
        async with store.transaction() as repos:
            await repos.sessions.create(
                session_id="s2",
                moonmind_workflow_id="wf-2",
                provider="codex",
                provider_session_ref="psess-2",
                chat_binding_id="cb-shared",
            )


@pytest.mark.asyncio
async def test_turn_attempt_has_no_chat_binding_authority() -> None:
    # Structural invariant: a turn attempt row cannot carry chat-binding
    # authority because the model has no such column.
    assert not hasattr(OmnigentTurnAttempt, "chat_binding_id")


@pytest.mark.asyncio
async def test_unique_turn_idempotency_identity(store) -> None:
    async with store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1", moonmind_workflow_id="wf-1", provider="codex"
        )
        await repos.turn_attempts.create(
            turn_attempt_id="t1",
            session_id="s1",
            idempotency_key="idem-1",
            instruction_digest="d1",
        )
    # Same key, different logical turn -> conflict (fails closed).
    with pytest.raises(TurnIdempotencyConflictError):
        async with store.transaction() as repos:
            await repos.turn_attempts.create(
                turn_attempt_id="t2",
                session_id="s1",
                idempotency_key="idem-1",
                instruction_digest="d2",
            )


@pytest.mark.asyncio
async def test_unique_command_idempotency_identity(store) -> None:
    async with store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1", moonmind_workflow_id="wf-1", provider="codex"
        )
        first = await repos.commands.record(
            command_id="c1",
            session_id="s1",
            command_type="submit_turn",
            idempotency_key="cmd-1",
            payload_digest=compute_digest({"turn": 1}),
            expected_session_revision=1,
            fencing_generation=0,
        )
    # Re-recording the same logical command collapses to one journal row.
    async with store.transaction() as repos:
        again = await repos.commands.record(
            command_id="c2",
            session_id="s1",
            command_type="submit_turn",
            idempotency_key="cmd-1",
            payload_digest=compute_digest({"turn": 1}),
        )
    assert again.command_id == first.command_id == "c1"
    assert again.payload_digest == first.payload_digest
    assert again.expected_session_revision == 1


@pytest.mark.asyncio
async def test_command_idempotency_key_reuse_with_different_payload_fails_closed(
    store,
) -> None:
    async with store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1", moonmind_workflow_id="wf-1", provider="codex"
        )
        await repos.commands.record(
            command_id="c1",
            session_id="s1",
            command_type="submit_turn",
            idempotency_key="cmd-1",
            payload_digest=compute_digest({"turn": 1}),
        )
    # Reusing the key after changing the payload must fail closed rather than
    # returning a receipt/status for unrelated input.
    with pytest.raises(CommandIdempotencyConflictError):
        async with store.transaction() as repos:
            await repos.commands.record(
                command_id="c2",
                session_id="s1",
                command_type="submit_turn",
                idempotency_key="cmd-1",
                payload_digest=compute_digest({"turn": 2}),
            )
    # A different command_type on the same key also fails closed.
    with pytest.raises(CommandIdempotencyConflictError):
        async with store.transaction() as repos:
            await repos.commands.record(
                command_id="c3",
                session_id="s1",
                command_type="ensure_host",
                idempotency_key="cmd-1",
                payload_digest=compute_digest({"turn": 1}),
            )


@pytest.mark.asyncio
async def test_command_claim_and_delivery_unknown(store) -> None:
    from moonmind.omnigent.control_plane import ControlPlaneOutcome

    async with store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1", moonmind_workflow_id="wf-1", provider="codex"
        )
        recorded = await repos.commands.record(
            command_id="c1",
            session_id="s1",
            command_type="ensure_host",
            idempotency_key="cmd-1",
            payload_digest="digest",
            fencing_generation=3,
        )
        assert recorded.status == "pending"
        assert recorded.revision == 1
        # Exactly one caller may claim execution authority.
        claim = await repos.commands.claim_command(
            "c1", owner_class="session_supervisor", claim_token="worker-a"
        )
        assert claim.outcome is ControlPlaneOutcome.APPLIED
        assert claim.record.status == "claimed"
        # A concurrent worker that shares the owner_class but presents a different
        # claim token does not win authority and must not execute the side effect.
        reclaim = await repos.commands.claim_command(
            "c1", owner_class="session_supervisor", claim_token="worker-b"
        )
        assert reclaim.outcome is ControlPlaneOutcome.NOT_OWNER
        # A possibly-delivered provider side effect is parked as ambiguous rather
        # than blindly reissued.
        delivered = await repos.commands.record_command_delivery(
            "c1",
            owner_class="session_supervisor",
            claim_token="worker-a",
            outcome=ControlPlaneOutcome.DELIVERY_UNKNOWN,
            provider_receipt_id="rcpt-1",
            result_ref="art://result",
        )
    assert delivered.outcome is ControlPlaneOutcome.DELIVERY_UNKNOWN
    assert delivered.record.status == "delivery_unknown"
    assert delivered.record.delivery_ambiguous is True
    assert delivered.record.provider_receipt_id == "rcpt-1"
    assert delivered.record.fencing_generation == 3
    assert delivered.record.result_ref == "art://result"


@pytest.mark.asyncio
async def test_command_delivery_rejects_non_owner(store) -> None:
    from moonmind.omnigent.control_plane import (
        ControlPlaneOutcome,
        NotCommandOwnerError,
    )

    async with store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1", moonmind_workflow_id="wf-1", provider="codex"
        )
        await repos.commands.record(
            command_id="c1",
            session_id="s1",
            command_type="ensure_host",
            idempotency_key="cmd-1",
            payload_digest="digest",
        )
        await repos.commands.claim_command(
            "c1", owner_class="session_supervisor", claim_token="worker-a"
        )
    # A worker that does not own the claim cannot record its delivery.
    with pytest.raises(NotCommandOwnerError):
        async with store.transaction() as repos:
            await repos.commands.record_command_delivery(
                "c1",
                owner_class="stale_worker",
                claim_token="worker-a",
                outcome=ControlPlaneOutcome.APPLIED,
            )


@pytest.mark.asyncio
async def test_terminal_session_not_overwritten_by_nonterminal_update(store) -> None:
    async with store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1", moonmind_workflow_id="wf-1", provider="codex"
        )
        # Fresh session is revision 1 / fencing generation 0; the terminal write
        # advances it to revision 2.
        await repos.sessions.mark_terminal(
            "s1", "completed", expected_revision=1, expected_fencing_generation=0
        )
    # A nonterminal lifecycle update on a terminal session fails closed even when
    # the caller presents current authority.
    with pytest.raises(TerminalSessionOverwriteError):
        async with store.transaction() as repos:
            await repos.sessions.update_lifecycle(
                "s1",
                expected_revision=2,
                expected_fencing_generation=0,
                observed_state="running",
            )
    # A conflicting terminal state also fails closed; the same one is idempotent.
    with pytest.raises(TerminalSessionOverwriteError):
        async with store.transaction() as repos:
            await repos.sessions.mark_terminal(
                "s1", "failed", expected_revision=2, expected_fencing_generation=0
            )
    async with store.transaction() as repos:
        idempotent = await repos.sessions.mark_terminal(
            "s1", "completed", expected_revision=2, expected_fencing_generation=0
        )
    assert idempotent.terminal_state == "completed"


@pytest.mark.asyncio
async def test_terminal_session_allows_cleanup_and_archive_transitions(store) -> None:
    async with store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1", moonmind_workflow_id="wf-1", provider="codex"
        )
        await repos.sessions.mark_terminal(
            "s1", "completed", expected_revision=1, expected_fencing_generation=0
        )
    # The terminal-then-cleanup journey (no separate cleanup writer exists) must
    # still record cleanup/archive progress on the terminal session.
    async with store.transaction() as repos:
        cleaned = await repos.sessions.update_lifecycle(
            "s1",
            expected_revision=2,
            expected_fencing_generation=0,
            cleanup_state="released",
            historical_read_state="archived",
        )
    assert cleaned.terminal_state == "completed"
    assert cleaned.cleanup_state == "released"
    assert cleaned.historical_read_state == "archived"
    # A cleanup update mixed with a nonterminal-state mutation still fails closed.
    with pytest.raises(TerminalSessionOverwriteError):
        async with store.transaction() as repos:
            await repos.sessions.update_lifecycle(
                "s1",
                expected_revision=3,
                expected_fencing_generation=0,
                cleanup_state="purged",
                observed_state="running",
            )


@pytest.mark.asyncio
async def test_supervisor_runtime_authority_bind_is_fenced_and_idempotent(store) -> None:
    """MoonLadderStudios/MoonMind#3705 receipts survive Activity retries."""

    async with store.transaction() as repos:
        created = await repos.sessions.create(
            session_id="s1", moonmind_workflow_id="wf-1", provider="codex"
        )
        bound = await repos.sessions.bind_runtime_authority(
            "s1",
            expected_revision=created.revision,
            expected_fencing_generation=created.fencing_generation,
            host_binding_ref="binding-1",
            host_lease_ref="lease-1",
            metadata_patch={"endpointRef": "endpoint-1"},
        )
        replayed = await repos.sessions.bind_runtime_authority(
            "s1",
            expected_revision=created.revision,
            expected_fencing_generation=created.fencing_generation,
            host_binding_ref="binding-1",
            host_lease_ref="lease-1",
            metadata_patch={"endpointRef": "endpoint-1"},
        )
    assert replayed == bound

    with pytest.raises(RevisionConflictError):
        async with store.transaction() as repos:
            await repos.sessions.bind_runtime_authority(
                "s1",
                expected_revision=created.revision,
                expected_fencing_generation=created.fencing_generation,
                metadata_patch={"differentReceipt": "artifact-2"},
            )


@pytest.mark.asyncio
async def test_supervisor_terminal_evidence_is_immutable(store) -> None:
    """MoonLadderStudios/MoonMind#3705 keeps historical reads authoritative."""

    async with store.transaction() as repos:
        created = await repos.sessions.create(
            session_id="s1", moonmind_workflow_id="wf-1", provider="codex"
        )
        terminal = await repos.sessions.mark_terminal(
            "s1",
            "completed",
            expected_revision=created.revision,
            expected_fencing_generation=created.fencing_generation,
        )
        attached = await repos.sessions.attach_terminal_evidence(
            "s1",
            terminal_evidence_ref="artifact-1",
            expected_revision=terminal.revision,
            expected_fencing_generation=terminal.fencing_generation,
        )
        replayed = await repos.sessions.attach_terminal_evidence(
            "s1",
            terminal_evidence_ref="artifact-1",
            expected_revision=terminal.revision,
            expected_fencing_generation=terminal.fencing_generation,
        )
    assert attached == replayed

    with pytest.raises(TerminalSessionOverwriteError):
        async with store.transaction() as repos:
            await repos.sessions.attach_terminal_evidence(
                "s1",
                terminal_evidence_ref="artifact-2",
                expected_revision=attached.revision,
                expected_fencing_generation=terminal.fencing_generation,
            )

    async with store.transaction() as repos:
        second = await repos.sessions.create(
            session_id="s2", moonmind_workflow_id="wf-2", provider="codex"
        )
        second_terminal = await repos.sessions.mark_terminal(
            "s2",
            "completed",
            expected_revision=second.revision,
            expected_fencing_generation=second.fencing_generation,
        )
        await repos.sessions.update_lifecycle(
            "s2",
            expected_revision=second_terminal.revision,
            expected_fencing_generation=second_terminal.fencing_generation,
            cleanup_state="provider_stopped",
        )
    with pytest.raises(RevisionConflictError):
        async with store.transaction() as repos:
            await repos.sessions.attach_terminal_evidence(
                "s2",
                terminal_evidence_ref="artifact-stale",
                expected_revision=second_terminal.revision,
                expected_fencing_generation=second_terminal.fencing_generation,
            )


@pytest.mark.asyncio
async def test_supervisor_provider_attachment_rejects_delayed_authority(store) -> None:
    """MoonLadderStudios/MoonMind#3705 fences delayed provider receipts."""

    async with store.transaction() as repos:
        created = await repos.sessions.create(
            session_id="s1", moonmind_workflow_id="wf-1", provider="codex"
        )
        attached = await repos.sessions.attach_provider_session(
            "s1",
            "provider-session-1",
            expected_revision=created.revision,
            expected_fencing_generation=created.fencing_generation,
        )
        replayed = await repos.sessions.attach_provider_session(
            "s1",
            "provider-session-1",
            expected_revision=created.revision,
            expected_fencing_generation=created.fencing_generation,
        )
    assert attached == replayed
    assert attached.revision == created.revision + 1

    async with store.transaction() as repos:
        second = await repos.sessions.create(
            session_id="s2", moonmind_workflow_id="wf-2", provider="codex"
        )
        advanced = await repos.sessions.update_lifecycle(
            "s2",
            expected_revision=second.revision,
            expected_fencing_generation=second.fencing_generation,
            observed_state="launching",
        )
    assert advanced.revision == second.revision + 1
    with pytest.raises(RevisionConflictError):
        async with store.transaction() as repos:
            await repos.sessions.attach_provider_session(
                "s2",
                "provider-session-2",
                expected_revision=second.revision,
                expected_fencing_generation=second.fencing_generation,
            )

    async with store.transaction() as repos:
        third = await repos.sessions.create(
            session_id="s3", moonmind_workflow_id="wf-3", provider="codex"
        )
        await repos.sessions.acquire_fencing_generation(
            "s3",
            FencingScope.SESSION_SUPERVISOR,
            expected_revision=third.revision,
        )
    with pytest.raises(FencingConflictError):
        async with store.transaction() as repos:
            await repos.sessions.attach_provider_session(
                "s3",
                "provider-session-3",
                expected_revision=third.revision,
                expected_fencing_generation=third.fencing_generation,
            )


@pytest.mark.asyncio
async def test_turn_writes_require_active_supervisor_generation(store) -> None:
    """#3705 Activities must present the session fence, not the turn's old one."""

    async with store.transaction() as repos:
        created = await repos.sessions.create(
            session_id="s1", moonmind_workflow_id="wf-1", provider="codex"
        )
        turn = await repos.turn_attempts.create(
            turn_attempt_id="t1",
            session_id="s1",
            idempotency_key="turn-1",
        )
        fenced = await repos.sessions.acquire_fencing_generation(
            "s1",
            FencingScope.SESSION_SUPERVISOR,
            expected_revision=created.revision,
        )

    assert turn.fencing_generation == 0
    assert fenced.fencing_generation == 1
    with pytest.raises(FencingConflictError):
        async with store.transaction() as repos:
            await repos.turn_attempts.advance_state(
                "t1",
                "accepted",
                expected_revision=turn.revision,
                expected_fencing_generation=turn.fencing_generation,
            )

    async with store.transaction() as repos:
        accepted = await repos.turn_attempts.advance_state(
            "t1",
            "accepted",
            expected_revision=turn.revision,
            expected_fencing_generation=fenced.fencing_generation,
        )
    assert accepted.state == "accepted"
    assert accepted.fencing_generation == fenced.fencing_generation


@pytest.mark.asyncio
async def test_get_by_scope_rejects_ambiguous_null_lookup(store) -> None:
    async with store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1", moonmind_workflow_id="wf-1", provider="codex"
        )
        await repos.sessions.create(
            session_id="s2", moonmind_workflow_id="wf-1", provider="codex"
        )
    # Two unattached (NULL provider_session_ref) sessions coexist by design, so a
    # NULL scope lookup is ambiguous and must fail closed with an actionable
    # error rather than raising MultipleResultsFound.
    with pytest.raises(ConflictingSessionAuthorityError):
        async with store.transaction() as repos:
            await repos.sessions.get_by_scope("wf-1", None)


@pytest.mark.asyncio
async def test_attempt_terminality_is_separate_from_session_terminality(store) -> None:
    async with store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1", moonmind_workflow_id="wf-1", provider="codex"
        )
        await repos.turn_attempts.create(
            turn_attempt_id="t1", session_id="s1", idempotency_key="idem-1"
        )
        await repos.turn_attempts.mark_terminal(
            "t1",
            "completed",
            expected_revision=1,
            expected_fencing_generation=0,
            attempt_outcome="ok",
        )
        session = await repos.sessions.get("s1")
        turn = await repos.turn_attempts.get("t1")
    # A terminal attempt does not terminalize the canonical session.
    assert turn.is_terminal is True
    assert turn.state == TURN_STATE_TERMINAL
    assert session.is_terminal is False
    assert session.terminal_state is None


@pytest.mark.asyncio
async def test_observation_dedup_is_idempotent(store) -> None:
    observed = datetime(2026, 8, 18, tzinfo=UTC)
    async with store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1", moonmind_workflow_id="wf-1", provider="codex"
        )
        first = await repos.observations.append(
            observation_id="o1",
            session_id="s1",
            observation_type="provider_event_batch",
            source="provider",
            observed_at=observed,
            deduplication_key="batch-1",
            payload_ref="art://batch-1",
            bounded_index={"count": 3},
        )
    async with store.transaction() as repos:
        again = await repos.observations.append(
            observation_id="o2",
            session_id="s1",
            observation_type="provider_event_batch",
            source="provider",
            observed_at=observed,
            deduplication_key="batch-1",
        )
        listed = await repos.observations.list_for_session("s1")
    assert again.observation_id == first.observation_id == "o1"
    assert len(listed) == 1
    assert listed[0].payload_ref == "art://batch-1"
    assert listed[0].bounded_index == {"count": 3}


@pytest.mark.asyncio
async def test_unknown_schema_version_fails_closed(store, session_factory) -> None:
    async with session_factory() as session:
        session.add(
            OmnigentSession(
                session_id="s-bad",
                moonmind_workflow_id="wf-1",
                provider="codex",
                schema_version=999,
            )
        )
        await session.commit()
    with pytest.raises(UnknownSchemaVersionError):
        async with store.transaction() as repos:
            await repos.sessions.get("s-bad")


@pytest.mark.asyncio
async def test_continuation_turn_reuses_session_without_new_binding(store) -> None:
    session, first_turn = await store.establish_session(
        session_id="s1",
        moonmind_workflow_id="wf-1",
        provider="codex",
        chat_binding_id="cb-1",
        provider_session_ref="psess-1",
        first_turn_attempt_id="t1",
        first_turn_idempotency_key="idem-1",
    )
    assert session.chat_binding_id == "cb-1"
    assert first_turn.lineage_kind == "initial"

    # A remediation/continuation turn reuses the canonical session and cannot
    # allocate another chat binding (the repo/model expose no such affordance).
    async with store.transaction() as repos:
        continuation = await repos.turn_attempts.create(
            turn_attempt_id="t2",
            session_id="s1",
            idempotency_key="idem-2",
            lineage_kind="continuation",
            parent_turn_attempt_id="t1",
        )
        refreshed = await repos.sessions.get("s1")
        turns = await repos.turn_attempts.list_for_session("s1")
    assert continuation.session_id == "s1"
    assert refreshed.chat_binding_id == "cb-1"
    assert {t.turn_attempt_id for t in turns} == {"t1", "t2"}
    # There is exactly one chat authority for the scope.
    async with store.transaction() as repos:
        by_binding = await repos.sessions.get_by_chat_binding("cb-1")
    assert by_binding.session_id == "s1"


@pytest.mark.asyncio
async def test_workflow_chat_uses_canonical_turn_and_command_authority(
    store, session_factory
) -> None:
    session, initial = await store.establish_session(
        session_id="s-chat-command",
        moonmind_workflow_id="wf-chat-command",
        provider="omnigent",
        chat_binding_id="canonical-chat-binding",
        provider_session_ref="provider-session-chat-command",
        first_turn_attempt_id="initial-chat-command",
        first_turn_idempotency_key="initial-chat-command-idempotency",
    )
    service = CanonicalTurnCommandService(store)

    claim = await service.claim(
        workflow_id="wf-chat-command",
        provider_session_ref="provider-session-chat-command",
        chat_binding_id="browser-chat-binding",
        command_type="message",
        idempotency_key="browser-message-1",
        payload_digest="sha256:" + "5" * 64,
        step_execution_id="step-chat-command",
    )

    assert claim.owns_delivery is True
    async with store.transaction() as repos:
        current = await repos.sessions.get(session.session_id)
        turn = await repos.turn_attempts.get(claim.turn_attempt_id)
        command = await repos.commands.get(claim.command_id)
        alias = await repos.chat_binding_aliases.resolve("browser-chat-binding")
    assert current is not None and current.terminal_state is None
    assert current.active_turn_attempt_id == claim.turn_attempt_id
    assert turn is not None and turn.lineage_kind == "continuation"
    assert turn.parent_turn_attempt_id == initial.turn_attempt_id
    assert command is not None and command.status == "claimed"
    assert alias is not None and alias.session_id == session.session_id

    outcome = await service.settle(
        workflow_id="wf-chat-command",
        idempotency_key="browser-message-1",
        outcome=ControlPlaneOutcome.APPLIED,
        provider_receipt_id="provider-receipt-1",
        result_ref="omnigent-bridge-event://receipt-1",
    )

    assert outcome is ControlPlaneOutcome.APPLIED
    async with store.transaction() as repos:
        current = await repos.sessions.get(session.session_id)
        turn = await repos.turn_attempts.get(claim.turn_attempt_id)
        command = await repos.commands.get(claim.command_id)
    assert current is not None and current.terminal_state is None
    assert turn is not None and turn.state == "accepted" and not turn.is_terminal
    assert command is not None and command.status == "applied"

    replay = await service.claim(
        workflow_id="wf-chat-command",
        provider_session_ref="provider-session-chat-command",
        chat_binding_id="browser-chat-binding",
        command_type="message",
        idempotency_key="browser-message-1",
        payload_digest="sha256:" + "5" * 64,
        step_execution_id="step-chat-command",
    )
    assert replay.outcome is ControlPlaneOutcome.ALREADY_APPLIED


@pytest.mark.asyncio
async def test_initial_command_uses_the_single_bootstrap_turn(
    store, session_factory
) -> None:
    service = CanonicalTurnCommandService(store)

    claim = await service.claim(
        workflow_id="wf-initial-command",
        provider_session_ref="",
        chat_binding_id=None,
        command_type="execute_admitted_plan",
        idempotency_key="initial-command-idempotency",
        payload_digest="sha256:" + "7" * 64,
        step_execution_id="step-initial-command",
        bootstrap=CanonicalSessionBootstrap(
            provider="omnigent",
            step_execution_id="step-initial-command",
            agent_run_id="agent-run-initial-command",
            source_idempotency_key="initial-command-idempotency",
            execution_plan_ref=None,
        ),
    )

    async with store.transaction() as repos:
        turns = await repos.turn_attempts.list_for_session(claim.session_id)
    assert len(turns) == 1
    assert turns[0].turn_attempt_id == claim.turn_attempt_id
    assert turns[0].lineage_kind == "initial"
    assert turns[0].instruction_digest == "sha256:" + "7" * 64

    await service.settle(
        workflow_id="wf-initial-command",
        idempotency_key="initial-command-idempotency",
        outcome=ControlPlaneOutcome.APPLIED,
    )
    replay = await service.claim(
        workflow_id="wf-initial-command",
        provider_session_ref="",
        chat_binding_id=None,
        command_type="execute_admitted_plan",
        idempotency_key="initial-command-idempotency",
        payload_digest="sha256:" + "7" * 64,
        step_execution_id="step-initial-command",
        bootstrap=CanonicalSessionBootstrap(
            provider="omnigent",
            step_execution_id="step-initial-command",
            agent_run_id="agent-run-initial-command",
            source_idempotency_key="initial-command-idempotency",
            execution_plan_ref=None,
        ),
    )
    assert replay.outcome is ControlPlaneOutcome.ALREADY_APPLIED


@pytest.mark.asyncio
async def test_canonical_command_idempotency_is_scoped_to_workflow(store) -> None:
    service = CanonicalTurnCommandService(store)
    claims = []
    for workflow_id in ("wf-scope-one", "wf-scope-two"):
        claims.append(
            await service.claim(
                workflow_id=workflow_id,
                provider_session_ref="",
                chat_binding_id=None,
                command_type="execute_admitted_plan",
                idempotency_key="shared-client-key",
                payload_digest="sha256:" + "8" * 64,
                step_execution_id=f"step-{workflow_id}",
                bootstrap=CanonicalSessionBootstrap(
                    provider="omnigent",
                    step_execution_id=f"step-{workflow_id}",
                    agent_run_id=f"agent-{workflow_id}",
                    source_idempotency_key="shared-client-key",
                    execution_plan_ref=None,
                ),
            )
        )

    assert claims[0].session_id != claims[1].session_id
    assert claims[0].command_id != claims[1].command_id
    assert claims[0].idempotency_key != claims[1].idempotency_key
    assert all(claim.owns_delivery for claim in claims)


@pytest.mark.asyncio
async def test_session_binds_plan_and_runtime_authority_without_mutating_plan(
    store, session_factory
) -> None:
    plan_ref = "omnigent-execution-plan:sha256:" + "3" * 64
    runtime_binding_ref = "omnigent-runtime-binding:sha256:" + "4" * 64
    alternate_binding_ref = "omnigent-runtime-binding:sha256:" + "6" * 64
    async with session_factory() as db_session:
        db_session.add(
            OmnigentExecutionPlanRecord(
                plan_ref=plan_ref,
                schema_version="moonmind.omnigent-execution-plan.v1",
                payload_json={},
                harness_id="codex-native",
                harness_implementation_ref="core:omnigent@1",
                host_class_ref="omnigent-codex-current@1",
                launch_policy_ref="codex-on-demand@1",
                execution_realizer_ref="codex-profile-bound@1",
            )
        )
        db_session.add(
            OmnigentRuntimeBindingRecord(
                runtime_binding_ref=runtime_binding_ref,
                execution_plan_ref=plan_ref,
                state="credentials_acquired",
                provider_leases_json={},
            )
        )
        db_session.add(
            OmnigentRuntimeBindingRecord(
                runtime_binding_ref=alternate_binding_ref,
                execution_plan_ref=plan_ref,
                state="credentials_acquired",
                provider_leases_json={},
            )
        )
        await db_session.commit()
    session, _turn = await store.establish_session(
        session_id="s-authority",
        moonmind_workflow_id="wf-authority",
        provider="omnigent",
        chat_binding_id="cb-authority",
        first_turn_attempt_id="turn-authority",
        first_turn_idempotency_key="idem-authority",
        execution_plan_ref=plan_ref,
    )
    assert session.execution_plan_ref == plan_ref
    assert session.runtime_binding_ref is None

    async with store.transaction() as repos:
        bound = await repos.sessions.bind_runtime_authority(
            "s-authority",
            expected_revision=session.revision,
            expected_fencing_generation=session.fencing_generation,
            execution_plan_ref=plan_ref,
            runtime_binding_ref=runtime_binding_ref,
        )
    assert bound.execution_plan_ref == plan_ref
    assert bound.runtime_binding_ref == runtime_binding_ref

    async with store.transaction() as repos:
        replayed = await repos.sessions.bind_runtime_authority(
            "s-authority",
            expected_revision=session.revision,
            expected_fencing_generation=session.fencing_generation,
            execution_plan_ref=plan_ref,
            runtime_binding_ref=runtime_binding_ref,
        )
    assert replayed == bound

    async with store.transaction() as repos:
        with pytest.raises(ConflictingSessionAuthorityError):
            await repos.sessions.bind_runtime_authority(
                "s-authority",
                expected_revision=bound.revision,
                expected_fencing_generation=bound.fencing_generation,
                execution_plan_ref=plan_ref,
                runtime_binding_ref=alternate_binding_ref,
            )

    async with store.transaction() as repos:
        with pytest.raises(ConflictingSessionAuthorityError):
            await repos.sessions.bind_runtime_authority(
                "s-authority",
                expected_revision=bound.revision,
                expected_fencing_generation=bound.fencing_generation,
                execution_plan_ref="omnigent-execution-plan:sha256:" + "5" * 64,
                runtime_binding_ref=runtime_binding_ref,
            )


@pytest.mark.asyncio
async def test_db_runtime_binding_store_returns_highest_immutable_stage(
    session_factory,
) -> None:
    from moonmind.omnigent.harness_platform.stores import DbRuntimeBindingStore

    plan_ref = "omnigent-execution-plan:sha256:" + "8" * 64
    async with session_factory() as session:
        session.add(
            OmnigentExecutionPlanRecord(
                plan_ref=plan_ref,
                schema_version="moonmind.omnigent-execution-plan.v1",
                payload_json={},
                harness_id="opencode-native",
                harness_implementation_ref="core:omnigent@1",
                host_class_ref="omnigent-opencode@1",
                launch_policy_ref="omnigent-on-demand@1",
                execution_realizer_ref="generic-omnigent-host@1",
            )
        )
        await session.commit()
    store = DbRuntimeBindingStore(session_factory)
    credentials = await store.create_initial(
        execution_plan_ref=plan_ref,
        provider_leases={
            "primary-model": {
                "providerProfileRef": "profile-1",
                "providerLeaseRef": "provider-lease-1",
                "credentialGeneration": 2,
                "credentialRuntimeRef": "credential-runtime-1",
            }
        },
    )
    host = await store.update_with_host(
        credentials.runtimeBindingRef,
        host_binding_ref="host-binding-1",
        host_lease_ref="host-lease-1",
        host_lease_generation=3,
        omnigent_host_id="host-1",
    )
    bound = await store.update_with_session(
        host.runtimeBindingRef,
        omnigent_session_id="provider-session-1",
    )

    latest = await store.latest_for_plan(plan_ref)

    assert latest == bound


@pytest.mark.asyncio
async def test_active_chat_alias_cannot_be_reassigned(store) -> None:
    async with store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1", moonmind_workflow_id="wf-1", provider="codex"
        )
        await repos.sessions.create(
            session_id="s2", moonmind_workflow_id="wf-2", provider="codex"
        )
        await repos.chat_binding_aliases.register(
            chat_binding_id="cb-1", session_id="s1"
        )
    # Identical re-registration is preserved (idempotent).
    async with store.transaction() as repos:
        again = await repos.chat_binding_aliases.register(
            chat_binding_id="cb-1", session_id="s1"
        )
    assert again.session_id == "s1"
    # Reassigning an active handle to a different session fails closed.
    with pytest.raises(ConflictingSessionAuthorityError):
        async with store.transaction() as repos:
            await repos.chat_binding_aliases.register(
                chat_binding_id="cb-1", session_id="s2"
            )
    # An explicit quarantine transition is still permitted.
    async with store.transaction() as repos:
        quarantined = await repos.chat_binding_aliases.quarantine(
            "cb-1", diagnostic_reason="handle retired"
        )
    assert quarantined.alias_state == ALIAS_STATE_QUARANTINED
    assert quarantined.resolves is False


# --- Migration / backfill tests ---------------------------------------------


@pytest.mark.asyncio
async def test_backfill_zero_rows(session_factory) -> None:
    report = await run_backfill(session_factory, dry_run=False)
    assert report.plan.summary()["sessions"] == 0
    assert report.sessions_written == 0


@pytest.mark.asyncio
async def test_backfill_one_row(session_factory) -> None:
    await _seed_bridge_rows(
        session_factory,
        [
            _bridge_row(
                "b1",
                provider_session_id="psess-1",
                chat_binding_id="cb-1",
                external_state_ref="art://state-1",
            )
        ],
    )
    report = await run_backfill(session_factory, dry_run=False)
    assert report.sessions_written == 1
    assert report.turn_attempts_written == 1
    assert report.plan.turn_attempts[0].lineage_kind == "initial"

    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        session_id = report.plan.sessions[0].session_id
        session = await repos.sessions.get(session_id)
        turns = await repos.turn_attempts.list_for_session(session_id)
        observations = await repos.observations.list_for_session(session_id)
        alias = await repos.chat_binding_aliases.resolve("cb-1")
    assert session.provider_session_ref == "psess-1"
    assert session.chat_binding_id == "cb-1"
    assert len(turns) == 1
    assert observations[0].bounded_index["external_state_ref"] == "art://state-1"
    assert alias.resolves is True
    assert alias.session_id == session_id


@pytest.mark.asyncio
async def test_backfill_seven_rows_one_provider_session(session_factory) -> None:
    rows = [
        _bridge_row(
            f"b{i}",
            provider_session_id="psess-1",
            chat_binding_id=f"cb-{i}",
            external_state_ref=f"art://state-{i}",
        )
        for i in range(1, 8)
    ]
    await _seed_bridge_rows(session_factory, rows)

    report = await run_backfill(session_factory, dry_run=False)
    # Seven bridge rows collapse to one canonical session authority.
    assert report.sessions_written == 1
    assert report.turn_attempts_written == 7
    lineages = [t.lineage_kind for t in report.plan.turn_attempts]
    assert lineages.count("initial") == 1
    assert lineages.count("continuation") == 6

    session_id = report.plan.sessions[0].session_id
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        session = await repos.sessions.get(session_id)
        observations = await repos.observations.list_for_session(session_id)
        # Every previously issued chat binding resolves to the one authority.
        resolved = [
            await repos.chat_binding_aliases.resolve(f"cb-{i}") for i in range(1, 8)
        ]
    assert len({o.deduplication_key for o in observations}) == 7  # no evidence lost
    assert all(a.resolves for a in resolved)
    assert {a.session_id for a in resolved} == {session_id}
    assert session.chat_binding_id == "cb-1"


@pytest.mark.asyncio
async def test_backfill_preserves_per_event_artifact_evidence(session_factory) -> None:
    # An artifact referenced only by an event row (no session-level ref) must be
    # preserved as a canonical observation so it stays reachable after the legacy
    # tables are retired.
    await _seed_bridge_rows(
        session_factory,
        [
            _bridge_row("b1", provider_session_id="psess-1", chat_binding_id="cb-1"),
            _bridge_event(
                "e1",
                bridge_session_id="b1",
                sequence=1,
                artifact_ref="art://event-artifact-1",
            ),
            # An event without an artifact_ref contributes no observation.
            _bridge_event("e2", bridge_session_id="b1", sequence=2),
        ],
    )
    report = await run_backfill(session_factory, dry_run=False)
    session_id = report.plan.sessions[0].session_id

    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        event_obs = await repos.observations.list_for_session(
            session_id, observation_type="legacy_bridge_event"
        )
    assert len(event_obs) == 1
    assert event_obs[0].payload_ref == "art://event-artifact-1"
    assert event_obs[0].bounded_index["artifact_ref"] == "art://event-artifact-1"
    assert event_obs[0].bounded_index["event_id"] == "e1"

    # Repeat apply is idempotent: the event observation is not duplicated.
    report2 = await run_backfill(session_factory, dry_run=False)
    async with store.transaction() as repos:
        event_obs_again = await repos.observations.list_for_session(
            session_id, observation_type="legacy_bridge_event"
        )
    assert len(event_obs_again) == 1
    assert report2.observations_written == 0


@pytest.mark.asyncio
async def test_backfill_conflicting_authority_is_quarantined(session_factory) -> None:
    await _seed_bridge_rows(
        session_factory,
        [
            _bridge_row(
                "b1",
                provider="codex",
                provider_session_id="psess-1",
                chat_binding_id="cb-1",
            ),
            _bridge_row(
                "b2",
                provider="claude",  # conflicting immutable authority
                provider_session_id="psess-1",
                chat_binding_id="cb-2",
            ),
        ],
    )
    report = await run_backfill(session_factory, dry_run=False)
    assert report.sessions_written == 0
    assert len(report.plan.quarantined_groups) == 1

    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        alias = await repos.chat_binding_aliases.resolve("cb-1")
    # Fail closed: quarantined alias exposes no provider identity, does not
    # resolve to a canonical session.
    assert alias.alias_state == ALIAS_STATE_QUARANTINED
    assert alias.resolves is False
    assert alias.session_id is None


@pytest.mark.asyncio
async def test_backfill_dry_run_and_apply_are_idempotent(session_factory) -> None:
    rows = [
        _bridge_row(
            f"b{i}",
            provider_session_id="psess-1",
            chat_binding_id=f"cb-{i}",
            external_state_ref=f"art://state-{i}",
        )
        for i in range(1, 4)
    ]
    await _seed_bridge_rows(session_factory, rows)

    dry1 = await run_backfill(session_factory, dry_run=True)
    dry2 = await run_backfill(session_factory, dry_run=True)
    assert dry1.plan.summary() == dry2.plan.summary()
    # Dry run writes nothing.
    async with session_factory() as session:
        assert (await plan_backfill(session)).summary()["sessions"] == 1

    apply1 = await run_backfill(session_factory, dry_run=False)
    apply2 = await run_backfill(session_factory, dry_run=False)
    assert apply1.sessions_written == 1
    assert apply1.turn_attempts_written == 3
    assert apply1.observations_written == 3
    # Repeat apply is idempotent: nothing new is written.
    assert apply2.sessions_written == 0
    assert apply2.turn_attempts_written == 0
    assert apply2.observations_written == 0
    assert apply2.aliases_written == 0


# --- Projection tests --------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_binding_resolution_uses_canonical_aggregate(session_factory) -> None:
    await _seed_bridge_rows(
        session_factory,
        [
            _bridge_row(
                "b1",
                provider_session_id="psess-1",
                chat_binding_id="cb-old",
                external_state_ref="art://state-1",
            )
        ],
    )
    await run_backfill(session_factory, dry_run=False)

    store = OmnigentControlPlaneStore(session_factory)
    # Simulate provider/host resources being removed and the session going to
    # historical read: the canonical aggregate still answers projection reads.
    async with store.transaction() as repos:
        session_id = (await repos.chat_binding_aliases.resolve("cb-old")).session_id
        current = await repos.sessions.get(session_id)
        await repos.sessions.update_lifecycle(
            session_id,
            expected_revision=current.revision,
            expected_fencing_generation=current.fencing_generation,
            historical_read_state="archived",
        )

    async with store.transaction() as repos:
        alias = await repos.chat_binding_aliases.resolve("cb-old")
        assert alias.resolves is True
        session = await repos.sessions.get(alias.session_id)
        observations = await repos.observations.list_for_session(alias.session_id)
    assert session.historical_read_state == "archived"
    assert session.provider_session_ref == "psess-1"
    # Historical evidence remains readable after provider resources are gone.
    assert observations[0].bounded_index["external_state_ref"] == "art://state-1"


@pytest.mark.asyncio
async def test_bounded_diagnostic_queries(store) -> None:
    """Bounded latest/active/count queries backing the operator session-timeline
    endpoints return the freshest/active row and a count without materializing the
    full append-only history (#3708)."""
    from datetime import timedelta

    from moonmind.omnigent.control_plane import ControlPlaneOutcome

    base = datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)
    async with store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1", moonmind_workflow_id="wf-1", provider="codex"
        )
        await repos.turn_attempts.create(
            turn_attempt_id="t1", session_id="s1", idempotency_key="idem-1"
        )
        await repos.turn_attempts.create(
            turn_attempt_id="t2", session_id="s1", idempotency_key="idem-2"
        )
        # Two event observations and one snapshot; latest_for_session must return
        # the newest row of the requested channel.
        await repos.observations.append(
            observation_id="o1", session_id="s1", observation_type="event",
            source="provider", observed_at=base, deduplication_key="d1",
        )
        await repos.observations.append(
            observation_id="o2", session_id="s1", observation_type="event",
            source="provider", observed_at=base + timedelta(minutes=5), deduplication_key="d2",
        )
        await repos.observations.append(
            observation_id="o3", session_id="s1", observation_type="snapshot",
            source="provider", observed_at=base + timedelta(minutes=1), deduplication_key="d3",
        )
        # Two active commands: a claimed one and a delivery-unknown one.
        await repos.commands.record(
            command_id="c1", session_id="s1", command_type="ensure_host",
            idempotency_key="cmd-1", payload_digest="pd1", fencing_generation=0,
        )
        await repos.commands.claim_command(
            "c1", owner_class="session_supervisor", claim_token="worker-a"
        )
        await repos.commands.record(
            command_id="c2", session_id="s1", command_type="submit_turn",
            idempotency_key="cmd-2", payload_digest="pd2", fencing_generation=0,
        )
        await repos.commands.claim_command(
            "c2", owner_class="session_supervisor", claim_token="worker-b"
        )
        await repos.commands.record_command_delivery(
            "c2", owner_class="session_supervisor", claim_token="worker-b",
            outcome=ControlPlaneOutcome.DELIVERY_UNKNOWN, provider_receipt_id="rcpt-1",
        )
        # Decisions under two reason codes.
        await repos.decisions.append(
            decision_id="dec-1", session_id="s1", decision_code="await_observation",
            reason_code="awaiting_provider",
        )
        await repos.decisions.append(
            decision_id="dec-2", session_id="s1", decision_code="quarantine_ambiguous_state",
            reason_code="moonmind_active_no_recent_evidence",
        )
        await repos.decisions.append(
            decision_id="dec-3", session_id="s1", decision_code="quarantine_ambiguous_state",
            reason_code="moonmind_active_no_recent_evidence",
        )

    async with store.transaction() as repos:
        latest_event = await repos.observations.latest_for_session(
            "s1", observation_types=("event", "event_frontier", "event_batch")
        )
        latest_snapshot = await repos.observations.latest_for_session(
            "s1", observation_types=("snapshot", "provider_snapshot")
        )
        turn_count = await repos.turn_attempts.count_for_session("s1")
        active_command = await repos.commands.active_for_session("s1")
        latest_decision = await repos.decisions.latest_for_session("s1")
        no_progress = await repos.decisions.count_for_session_reason(
            "s1", "moonmind_active_no_recent_evidence"
        )

    assert latest_event.observation_id == "o2"  # newest event, not o1
    assert latest_snapshot.observation_id == "o3"
    assert turn_count == 2
    # Delivery-ambiguity outranks a merely claimed command.
    assert active_command.command_id == "c2"
    assert active_command.status == "delivery_unknown"
    assert latest_decision.decision_id == "dec-3"
    assert no_progress == 2
