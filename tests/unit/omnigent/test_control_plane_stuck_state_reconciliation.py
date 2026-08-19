"""Production-boundary remediation tests for MoonLadderStudios/MoonMind#3708."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from api_service.db.models import (
    OmnigentCleanupAuthority,
    OmnigentCommand,
    OmnigentTurnAttempt,
)
from moonmind.omnigent.control_plane import OmnigentControlPlaneStore
from moonmind.omnigent.control_plane import metrics, spans
from moonmind.omnigent.control_plane.records import ControlPlaneOutcome
from moonmind.omnigent.control_plane.records import COMMAND_STATE_DELIVERY_UNKNOWN
from moonmind.omnigent.control_plane.stuck_state_reconciliation import (
    StuckStateReconciliationService,
    inspect_stuck_state,
)
from moonmind.omnigent.control_plane.repositories import ControlPlaneRepositories
from moonmind.omnigent.control_plane.stuck_state import StuckStateReason


@pytest_asyncio.fixture()
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/stuck-state.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield maker
    await engine.dispose()


class _Dispatcher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, str]] = []

    async def request_reconcile(self, **payload: str) -> None:
        self.calls.append(dict(payload))
        if self.fail:
            raise RuntimeError("signal delivery result unknown")


class _DiagnosticPublisher:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    async def publish(self, **payload: object) -> str:
        self.payloads.append(dict(payload))
        return "art_stuck_diagnostic_1"


async def _seed_stuck_session(
    session_factory,
    *,
    session_id: str = "sess-1",
    now: datetime,
) -> OmnigentControlPlaneStore:
    store = OmnigentControlPlaneStore(session_factory)
    turn_id = f"turn-{session_id}"
    async with store.transaction() as repos:
        await repos.sessions.create(
            session_id=session_id,
            moonmind_workflow_id=f"wf-{session_id}",
            provider="codex",
            provider_session_ref="provider-session-secret-identity",
            compatibility_ref="compat-v1",
            host_lease_ref="host-lease-secret-identity",
            provider_profile_id="profile-secret-identity",
        )
        await repos.turn_attempts.create(
            turn_attempt_id=turn_id,
            session_id=session_id,
            idempotency_key=f"turn-idempotency-{session_id}",
        )
        await repos.sessions.update_lifecycle(
            session_id,
            expected_revision=1,
            expected_fencing_generation=0,
            active_turn_attempt_id=turn_id,
            observed_state="running",
        )
        await repos.observations.append(
            observation_id=f"snapshot-{session_id}",
            session_id=session_id,
            observation_type="provider_snapshot",
            source="provider_authoritative_snapshot",
            observed_at=now - timedelta(minutes=20),
            deduplication_key=f"snapshot-{session_id}",
            source_digest="sha256:" + "a" * 64,
            bounded_index={
                "providerSession": {"rawStatus": "completed", "present": True},
                "hostLease": {"held": True, "consumerActive": False},
                "profileLease": {"held": True, "consumerActive": False},
            },
        )
    return store


class _FailingDiagnosticPublisher:
    async def publish(self, **_payload: object) -> str:
        raise RuntimeError("artifact backend unavailable")


@pytest.mark.asyncio
async def test_sweep_records_finding_and_dispatches_one_fenced_reconcile(session_factory) -> None:
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    store = await _seed_stuck_session(session_factory, now=now)
    dispatcher = _Dispatcher()
    publisher = _DiagnosticPublisher()
    service = StuckStateReconciliationService(
        session_factory=session_factory,
        dispatcher=dispatcher,
        diagnostic_publisher=publisher,
    )

    first = await service.sweep(now=now)
    second = await service.sweep(now=now + timedelta(minutes=1))

    assert first.findings_recorded == 1
    assert first.reconcile_requests == 1
    assert second.reconcile_requests == 0
    assert len(dispatcher.calls) == 1
    assert dispatcher.calls[0]["session_id"] == "sess-1"
    assert dispatcher.calls[0]["workflow_id"] == "wf-sess-1"
    assert dispatcher.calls[0]["reason_code"] == (
        "moonmind_active_no_recent_evidence"
    )
    assert dispatcher.calls[0]["expected_revision"] == "2"
    assert dispatcher.calls[0]["expected_fencing_generation"] == "0"
    assert publisher.payloads == []

    async with store.transaction() as repos:
        observations = await repos.observations.list_for_session(
            "sess-1", observation_type="stuck_state"
        )
        decisions = await repos.decisions.list_for_session("sess-1")
        commands = await repos.commands.list_for_session("sess-1")
    assert len(observations) == 1
    assert observations[0].bounded_index["reason"] == (
        "moonmind_active_no_recent_evidence"
    )
    assert observations[0].bounded_index["response"] == "reconcile"
    assert len(decisions) == 1
    assert decisions[0].decision_code == "stuck_state_reconcile_requested"
    assert len(commands) == 1
    assert decisions[0].resulting_command_id == commands[0].command_id
    assert commands[0].status == "applied"


@pytest.mark.asyncio
async def test_receiving_activity_validates_canonical_revision_fence_and_command(
    session_factory,
) -> None:
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    store = await _seed_stuck_session(session_factory, now=now)
    service = StuckStateReconciliationService(
        session_factory=session_factory,
        dispatcher=_Dispatcher(),
        diagnostic_publisher=_DiagnosticPublisher(),
    )
    await service.sweep(now=now)
    async with store.transaction() as repos:
        session = await repos.sessions.get("sess-1")
        command = (await repos.commands.list_for_session("sess-1"))[0]
    # Re-open the dispatch window to model the exact receiving Activity
    # boundary before its acknowledged Update is recorded applied.
    async with session_factory() as db:
        row = await db.get(OmnigentCommand, command.command_id)
        assert row is not None
        row.status = "claimed"
        row.delivery_ambiguous = False
        await db.commit()
    assert session is not None

    accepted = await service.validate_reconcile_request(
        session_id="sess-1",
        workflow_id="wf-sess-1",
        request_id=command.command_id,
        reason_code="moonmind_active_no_recent_evidence",
        expected_revision=session.revision,
        expected_fencing_generation=session.fencing_generation,
    )
    assert accepted["accepted"] is True

    with pytest.raises(ValueError, match="revision or fencing"):
        await service.validate_reconcile_request(
            session_id="sess-1",
            workflow_id="wf-sess-1",
            request_id=command.command_id,
            reason_code="moonmind_active_no_recent_evidence",
            expected_revision=session.revision + 1,
            expected_fencing_generation=session.fencing_generation,
        )

    with pytest.raises(ValueError, match="durable command"):
        await service.validate_reconcile_request(
            session_id="sess-1",
            workflow_id="wf-sess-1",
            request_id=command.command_id,
            reason_code="tampered_reason",
            expected_revision=session.revision,
            expected_fencing_generation=session.fencing_generation,
        )

    async with session_factory() as db:
        row = await db.get(OmnigentCommand, command.command_id)
        assert row is not None
        row.status = COMMAND_STATE_DELIVERY_UNKNOWN
        row.delivery_ambiguous = True
        await db.commit()
    with pytest.raises(ValueError, match="delivery ambiguity requires observation"):
        await service.validate_reconcile_request(
            session_id="sess-1",
            workflow_id="wf-sess-1",
            request_id=command.command_id,
            reason_code="moonmind_active_no_recent_evidence",
            expected_revision=session.revision,
            expected_fencing_generation=session.fencing_generation,
        )


@pytest.mark.asyncio
async def test_ambiguous_signal_delivery_is_parked_and_never_resent(session_factory) -> None:
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    store = await _seed_stuck_session(session_factory, now=now)
    dispatcher = _Dispatcher(fail=True)
    service = StuckStateReconciliationService(
        session_factory=session_factory,
        dispatcher=dispatcher,
        diagnostic_publisher=_DiagnosticPublisher(),
    )

    first = await service.sweep(now=now)
    second = await service.sweep(now=now + timedelta(minutes=1))

    assert first.delivery_unknown == 1
    assert second.reconcile_requests == 0
    assert len(dispatcher.calls) == 1
    async with store.transaction() as repos:
        command = (await repos.commands.list_for_session("sess-1"))[0]
    assert command.status == COMMAND_STATE_DELIVERY_UNKNOWN
    assert command.delivery_ambiguous is True


@pytest.mark.asyncio
async def test_persistent_ambiguity_publishes_diagnostics_then_quarantines(session_factory) -> None:
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    store = await _seed_stuck_session(session_factory, now=now)
    async with store.transaction() as repos:
        for ordinal in range(3):
            await repos.decisions.append(
                decision_id=f"prior-{ordinal}",
                session_id="sess-1",
                decision_code="stuck_state_reconcile_requested",
                expected_revision=2,
                fencing_generation=0,
                reason_code="moonmind_active_no_recent_evidence",
            )

    dispatcher = _Dispatcher()
    publisher = _DiagnosticPublisher()
    service = StuckStateReconciliationService(
        session_factory=session_factory,
        dispatcher=dispatcher,
        diagnostic_publisher=publisher,
    )

    result = await service.sweep(now=now)

    assert result.quarantined == 1
    assert result.reconcile_requests == 0
    assert dispatcher.calls == []
    assert len(publisher.payloads) == 1
    diagnostic = publisher.payloads[0]["payload"]
    assert isinstance(diagnostic, dict)
    assert diagnostic["schemaVersion"] == "moonmind.omnigent-stuck-state-diagnostic/v1"
    encoded = repr(diagnostic)
    assert "provider-session-secret-identity" not in encoded
    assert "host-lease-secret-identity" not in encoded
    assert "profile-secret-identity" not in encoded

    async with store.transaction() as repos:
        session = await repos.sessions.get("sess-1")
    assert session is not None
    assert session.historical_read_state == "quarantined"
    assert session.reconciled_state == "quarantined"
    async with store.transaction() as repos:
        decision = await repos.decisions.get(session.last_decision_ref)
    assert decision is not None
    assert decision.decision_code == "quarantine_ambiguous_state"
    assert decision.diagnostics_ref == "art_stuck_diagnostic_1"


@pytest.mark.asyncio
async def test_repository_boundaries_emit_semantic_spans_and_bounded_decision_metric(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real persistence boundaries, not test-only helpers, own #3708 telemetry."""

    emitted: list[tuple[str, dict[str, object]]] = []

    @contextmanager
    def capture(name: str, **attributes: object):
        emitted.append((name, spans.sanitize_span_attributes(attributes)))
        yield

    monkeypatch.setattr(spans, "omnigent_span", capture)
    metrics.reset()
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        await repos.sessions.create(
            session_id="telemetry-session",
            moonmind_workflow_id="wf-telemetry",
            provider="codex",
        )
        await repos.observations.append(
            observation_id="telemetry-observation",
            session_id="telemetry-session",
            observation_type="provider_snapshot",
            source="provider_authoritative_snapshot",
            observed_at=datetime(2026, 8, 19, tzinfo=UTC),
            deduplication_key="telemetry-observation",
        )
        await repos.commands.record(
            command_id="telemetry-command",
            session_id="telemetry-session",
            command_type="submit_turn",
            idempotency_key="telemetry-command",
            payload_digest="sha256:" + "b" * 64,
            expected_session_revision=1,
        )
        await repos.commands.claim_command(
            "telemetry-command",
            owner_class="session_supervisor",
            claim_token="claim-1",
        )
        await repos.commands.record_command_delivery(
            "telemetry-command",
            owner_class="session_supervisor",
            claim_token="claim-1",
            outcome=ControlPlaneOutcome.APPLIED,
        )
        await repos.decisions.append(
            decision_id="telemetry-decision",
            session_id="telemetry-session",
            decision_code="submit_turn",
            expected_revision=1,
            reason_code="provider_ready",
        )

    names = [name for name, _attributes in emitted]
    assert spans.PROVIDER_OBSERVE_SNAPSHOT in names
    assert names.count(spans.COMMAND_EXECUTE) >= 2
    assert spans.SESSION_RECONCILE in names
    assert all(set(attributes) <= spans.SAFE_SPAN_ATTRIBUTES for _, attributes in emitted)
    metric_snapshot = metrics.snapshot()
    assert any(
        key.startswith(metrics.RECONCILIATION_DECISIONS)
        for key in metric_snapshot["counters"]
    )


@pytest.mark.asyncio
async def test_sweep_records_resource_and_compatibility_metrics(
    session_factory,
) -> None:
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    await _seed_stuck_session(session_factory, now=now)
    metrics.reset()
    service = StuckStateReconciliationService(
        session_factory=session_factory,
        dispatcher=_Dispatcher(),
        diagnostic_publisher=_DiagnosticPublisher(),
    )

    await service.sweep(now=now)

    snapshot = metrics.snapshot()
    assert any(
        key.startswith(metrics.ORPHANED_LEASES) for key in snapshot["counters"]
    )
    assert any(
        key.startswith(metrics.DEPLOYED_BUILD_COMPATIBILITY)
        for key in snapshot["counters"]
    )


@pytest.mark.asyncio
async def test_service_level_detector_matrix_covers_every_durable_condition(
    session_factory,
) -> None:
    """Every #3708 condition crosses actual repositories and persisted rows."""

    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    old = now - timedelta(minutes=40)
    fresh_metadata = {
        "protected_live_evidence_at": now.isoformat(),
        "conformance_runner_available": True,
    }
    store = OmnigentControlPlaneStore(session_factory)

    async def create_session(
        ordinal: int, *, attached: bool = False, compatibility: bool = True,
        metadata=None,
    ):
        async with store.transaction() as repos:
            return await repos.sessions.create(
                session_id=f"matrix-{ordinal}",
                moonmind_workflow_id=f"wf-matrix-{ordinal}",
                provider="codex",
                provider_session_ref=f"provider-{ordinal}" if attached else None,
                compatibility_ref="compat-v1" if compatibility else None,
                metadata=metadata or {},
            )

    async def snapshot(ordinal: int, index: dict[str, object], *, at=now):
        async with store.transaction() as repos:
            await repos.observations.append(
                observation_id=f"matrix-snapshot-{ordinal}",
                session_id=f"matrix-{ordinal}",
                observation_type="provider_snapshot",
                source="provider_authoritative_snapshot",
                observed_at=at,
                deduplication_key=f"matrix-snapshot-{ordinal}",
                bounded_index=index,
            )

    # 1: active with no recent evidence.
    await create_session(1)
    async with store.transaction() as repos:
        await repos.turn_attempts.create(
            turn_attempt_id="matrix-turn-1",
            session_id="matrix-1",
            idempotency_key="matrix-turn-1",
        )
        await repos.sessions.update_lifecycle(
            "matrix-1",
            expected_revision=1,
            expected_fencing_generation=0,
            active_turn_attempt_id="matrix-turn-1",
        )

    # 2: provider terminal while MoonMind remains nonterminal.
    await create_session(2, attached=True, metadata=fresh_metadata)
    await snapshot(2, {"providerSession": {"rawStatus": "completed"}})

    # 3: MoonMind terminal while the provider remains active.
    await create_session(3, attached=True, metadata=fresh_metadata)
    async with store.transaction() as repos:
        await repos.sessions.mark_terminal(
            "matrix-3", "success", expected_revision=1,
            expected_fencing_generation=0,
        )
    await snapshot(3, {"providerSession": {"rawStatus": "running"}})

    # 4: active turn has only liveness beyond policy.
    await create_session(4)
    async with store.transaction() as repos:
        await repos.turn_attempts.create(
            turn_attempt_id="matrix-turn-4", session_id="matrix-4",
            idempotency_key="matrix-turn-4",
        )
        await repos.sessions.update_lifecycle(
            "matrix-4", expected_revision=1, expected_fencing_generation=0,
            active_turn_attempt_id="matrix-turn-4",
        )
        await repos.observations.append(
            observation_id="matrix-liveness-4", session_id="matrix-4",
            observation_type="liveness", source="provider_liveness",
            observed_at=now, deduplication_key="matrix-liveness-4",
        )
    await snapshot(4, {"providerSession": {"rawStatus": "running"}}, at=old)

    # 5: repeated reconciliation without revision/frontier progress.
    session5 = await create_session(5)
    async with store.transaction() as repos:
        for ordinal in range(3):
            await repos.decisions.append(
                decision_id=f"matrix-decision-5-{ordinal}", session_id="matrix-5",
                decision_code="await_observation",
                expected_revision=session5.revision,
                observation_frontier_digest="same-frontier",
            )

    # 6/7: orphan host and profile leases are independent findings.
    await create_session(6)
    await snapshot(6, {"hostLease": {"held": True, "consumerActive": False}})
    await create_session(7)
    await snapshot(7, {"profileLease": {"held": True, "consumerActive": False}})

    # 8: cleanup claim exceeded its deadline.
    await create_session(8)
    async with store.transaction() as repos:
        await repos.cleanup.claim_cleanup(
            "matrix-8", owner_class="janitor", claim_token="matrix-cleanup-8"
        )

    # 9: admitted session never established compatibility/actual-build state.
    await create_session(
        9, attached=True, compatibility=False, metadata=fresh_metadata
    )
    await snapshot(9, {"providerSession": {"rawStatus": "running"}})

    # 10: claimed command exceeded the delivery deadline.
    await create_session(10)
    async with store.transaction() as repos:
        await repos.commands.record(
            command_id="matrix-command-10", session_id="matrix-10",
            command_type="submit_turn", idempotency_key="matrix-command-10",
            payload_digest="sha256:" + "c" * 64,
        )
        await repos.commands.claim_command(
            "matrix-command-10", owner_class="session_supervisor",
            claim_token="matrix-command-claim-10",
        )

    # 11: protected evidence expired and its runner is unavailable.
    await create_session(
        11,
        attached=True,
        metadata={
            "protected_live_evidence_at": (
                now - timedelta(days=2)
            ).isoformat(),
            "conformance_runner_available": False,
        },
    )
    await snapshot(11, {"providerSession": {"rawStatus": "running"}})

    # Set deadline timestamps through the actual ORM rows because these are
    # database-owned lifecycle timestamps, not repository input fields.
    async with session_factory() as db:
        (await db.get(OmnigentTurnAttempt, "matrix-turn-1")).created_at = old
        (await db.get(OmnigentCleanupAuthority, "matrix-8")).updated_at = old
        (await db.get(OmnigentCommand, "matrix-command-10")).updated_at = old
        await db.commit()

    expected = {
        1: StuckStateReason.MOONMIND_ACTIVE_NO_RECENT_EVIDENCE,
        2: StuckStateReason.PROVIDER_TERMINAL_MOONMIND_NONTERMINAL,
        3: StuckStateReason.MOONMIND_TERMINAL_PROVIDER_ACTIVE,
        4: StuckStateReason.ACTIVE_TURN_LIVENESS_ONLY,
        5: StuckStateReason.REPEATED_RECONCILIATION_NO_PROGRESS,
        6: StuckStateReason.HOST_LEASE_WITHOUT_SESSION_AUTHORITY,
        7: StuckStateReason.PROFILE_LEASE_WITHOUT_CONSUMER,
        8: StuckStateReason.CLEANUP_INCOMPLETE_PAST_DEADLINE,
        9: StuckStateReason.COMPATIBILITY_UNKNOWN_AFTER_ADMISSION,
        10: StuckStateReason.COMMAND_STUCK_CLAIMED_OR_DELIVERY_UNKNOWN,
        11: StuckStateReason.LIVE_CONFORMANCE_EVIDENCE_STALE,
    }
    async with session_factory() as db:
        repos = ControlPlaneRepositories.bind(db)
        for ordinal, reason in expected.items():
            inspection = await inspect_stuck_state(
                repos, session_id=f"matrix-{ordinal}", now=now
            )
            assert inspection is not None
            assert reason in {finding.reason for finding in inspection.findings}


@pytest.mark.asyncio
async def test_one_diagnostic_failure_does_not_starve_later_candidates(
    session_factory,
) -> None:
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    store = await _seed_stuck_session(session_factory, session_id="sess-1", now=now)
    await _seed_stuck_session(session_factory, session_id="sess-2", now=now)
    async with store.transaction() as repos:
        for ordinal in range(3):
            await repos.decisions.append(
                decision_id=f"prior-failure-{ordinal}",
                session_id="sess-1",
                decision_code="stuck_state_reconcile_requested",
                expected_revision=2,
                fencing_generation=0,
                reason_code="moonmind_active_no_recent_evidence",
            )
    dispatcher = _Dispatcher()
    service = StuckStateReconciliationService(
        session_factory=session_factory,
        dispatcher=dispatcher,
        diagnostic_publisher=_FailingDiagnosticPublisher(),
    )

    result = await service.sweep(now=now)

    assert result.scanned == 2
    assert result.failures == 1
    assert result.reconcile_requests == 1
    assert [call["session_id"] for call in dispatcher.calls] == ["sess-2"]
