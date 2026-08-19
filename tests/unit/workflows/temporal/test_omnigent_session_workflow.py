"""Regression coverage for MoonLadderStudios/MoonMind#3705."""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from moonmind.config.settings import TemporalSettings
from moonmind.omnigent.reconciler import (
    CompiledSessionIntent,
    DecisionKind,
    DurableSessionState,
    ObservationSet,
    ProviderSessionObservation,
    SubmissionState,
)
from moonmind.schemas.agent_runtime_models import AgentRunResult
from moonmind.schemas.omnigent_session_models import (
    OmnigentSessionContinueAsNewState,
    OmnigentSessionSignal,
    OmnigentSessionTerminalResult,
    OmnigentSessionWorkflowInput,
)
from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog
from moonmind.workflows.temporal.activities import omnigent_session_activities
from moonmind.workflows.temporal.workflow_registry import workflow_fleet_workflow_types
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.omnigent_session import (
    BOUNDED_COMMAND_ACTIVITIES,
    MoonMindOmnigentSessionWorkflow,
    canonical_omnigent_session_id,
    omnigent_session_workflow_id,
)


def _workflow_input(**updates: object) -> OmnigentSessionWorkflowInput:
    payload: dict[str, object] = {
        "sessionId": "oms_123",
        "compiledExecutionIntentRef": "art_intent_123",
        "compiledExecutionIntentDigest": "sha256:" + "a" * 64,
        "workflowId": "workflow-1",
        "stepExecutionId": "step-1",
        "agentRunId": "agent-run-1",
        "initialTurnAttemptId": "turn-1",
        "admittedFeatureGeneration": "omnigent-session-v1",
        "compatibilityVersion": "v1",
    }
    payload.update(updates)
    return OmnigentSessionWorkflowInput.model_validate(payload)


def test_session_identity_uses_canonical_owner_not_attempt_key() -> None:
    first = canonical_omnigent_session_id(
        workflow_id="workflow-1",
        step_execution_id="step-1",
        agent_run_id="agent-run-1",
    )
    second = canonical_omnigent_session_id(
        workflow_id="workflow-1",
        step_execution_id="step-1",
        agent_run_id="agent-run-1",
    )

    assert first == second
    assert first.startswith("oms_")
    assert omnigent_session_workflow_id(first) == f"omnigent-session:{first}"


def test_workflow_input_is_compact_closed_and_reference_only() -> None:
    value = _workflow_input()
    assert value.session_id == "oms_123"
    assert value.resume_state is None

    with pytest.raises(ValidationError):
        _workflow_input(providerToken="raw-secret")
    with pytest.raises(ValidationError):
        _workflow_input(workspacePath="/work/agent_jobs/run/repo")
    with pytest.raises(ValidationError):
        _workflow_input(compiledExecutionIntentRef="/tmp/intent.json")
    with pytest.raises(ValidationError):
        _workflow_input(admittedFeatureGeneration="omnigent-session-v2")


def test_signal_contract_carries_only_safe_ids_and_refs() -> None:
    signal = OmnigentSessionSignal(
        requestId="request-1",
        observationRef="art_observation_1",
        turnAttemptId="turn-2",
        reasonCode="operator_reconcile",
        observedAt=datetime(2026, 8, 18, tzinfo=UTC),
    )
    assert signal.request_id == "request-1"

    with pytest.raises(ValidationError):
        OmnigentSessionSignal(
            requestId="request-2",
            metadata={"token": "not-allowed"},
        )


def test_terminal_result_rejects_paths_and_inline_provider_payloads() -> None:
    with pytest.raises(ValidationError, match="opaque artifact reference"):
        OmnigentSessionTerminalResult(
            status="completed",
            result=AgentRunResult(outputRefs=["/tmp/provider-output.json"]),
        )
    with pytest.raises(ValidationError, match="reference-only"):
        OmnigentSessionTerminalResult(
            status="completed",
            result=AgentRunResult(
                metadata={"publication": {"providerPayload": "inline"}}
            ),
        )


def test_reconciliation_input_ignores_bounded_executor_diagnostics() -> None:
    mapping, frontier = omnigent_session_activities._observation_payload(
        [
            SimpleNamespace(
                observed_at=datetime(2026, 8, 18, tzinfo=UTC),
                bounded_index={
                    "providerSession": {
                        "observedAt": "2026-08-18T00:00:00Z",
                        "rawStatus": "idle",
                    },
                    "snapshotCandidate": {
                        "attemptId": "turn-1",
                        "signature": [2, "item-2"],
                    },
                },
            )
        ]
    )

    assert set(mapping) == {"providerSession"}
    assert frontier["snapshotFrontier"] is None
    ObservationSet.model_validate(mapping)


@pytest.mark.asyncio
async def test_snapshot_requires_stable_marked_turn_before_idle_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale idle projection cannot synthesize terminal on its first read."""

    import moonmind.omnigent.bridge_store as bridge_store_module
    import moonmind.omnigent.control_plane as control_plane_module

    marker = "MoonMind-Omnigent-Run:\n  idempotencyKey: turn-1"
    snapshot = {
        "status": "idle",
        "items": [
            {
                "id": "user-1",
                "type": "message",
                "data": {
                    "role": "user",
                    "content": [{"text": marker}],
                },
            },
            {
                "id": "assistant-1",
                "type": "message",
                "data": {
                    "role": "assistant",
                    "content": [{"text": "Finished."}],
                },
            },
        ],
    }
    session = SimpleNamespace(
        session_id="oms_123",
        active_turn_attempt_id="turn-1",
        provider_session_ref="provider-session-1",
        provider_event_cursor=None,
        snapshot_frontier=None,
        cleanup_state="pending",
        terminal_state=None,
        terminal_evidence_ref=None,
        revision=7,
        host_lease_ref="host-lease-1",
        provider_profile_id="profile-1",
        metadata={"providerLeaseRef": "profile-lease-1"},
    )
    turn = SimpleNamespace(
        turn_attempt_id="turn-1",
        idempotency_key="turn-idempotency-1",
    )
    prior_observations: list[object] = []
    writes: list[dict[str, object]] = []
    client_state = {"available": True}

    class FakeSessions:
        async def get(self, _session_id: str) -> object:
            return session

        async def advance_observation_frontier(
            self, _session_id: str, **_kwargs: object
        ) -> object:
            return session

    class FakeTurns:
        async def get(self, _turn_id: str) -> object:
            return turn

    class FakeObservations:
        async def list_for_session(
            self, _session_id: str, *, limit: int, latest: bool
        ) -> list[object]:
            assert limit == 500
            assert latest is True
            return list(prior_observations)

        async def append(self, **kwargs: object) -> object:
            writes.append(dict(kwargs))
            return SimpleNamespace(**kwargs)

    repos = SimpleNamespace(
        sessions=FakeSessions(),
        turn_attempts=FakeTurns(),
        observations=FakeObservations(),
    )

    class FakeStore:
        @asynccontextmanager
        async def transaction(self):
            yield repos

    class FakeBridgeStore:
        async def get_existing(self, _idempotency_key: str) -> object:
            return SimpleNamespace(
                first_message_marker=marker,
                metadata_={"first_message_pre_dispatch_item_ids": []},
            )

    class FakeClient:
        async def get_session(self, _session_id: str) -> dict[str, object]:
            if not client_state["available"]:
                raise RuntimeError("provider unavailable")
            return snapshot

    class FakeHttpClient:
        async def aclose(self) -> None:
            return None

    async def fake_client_context() -> tuple[FakeHttpClient, FakeClient]:
        return FakeHttpClient(), FakeClient()

    monkeypatch.setattr(
        control_plane_module,
        "OmnigentControlPlaneStore",
        lambda _session_maker: FakeStore(),
    )
    monkeypatch.setattr(
        bridge_store_module,
        "OmnigentBridgeSessionStore",
        lambda _session_maker: FakeBridgeStore(),
    )
    monkeypatch.setattr(
        omnigent_session_activities,
        "_omnigent_client_context",
        fake_client_context,
    )
    request = {
        "sessionId": "oms_123",
        "compiledExecutionIntentRef": "art_intent_123",
        "compiledExecutionIntentDigest": "sha256:" + "a" * 64,
        "expectedRevision": 7,
        "fencingGeneration": 1,
    }

    await omnigent_session_activities.omnigent_observe_snapshot_activity(request)
    first_index = dict(writes[-1]["bounded_index"])
    assert first_index["providerSession"]["rawStatus"] == "idle"
    assert "providerTurn" not in first_index
    prior_observations.append(
        SimpleNamespace(
            observation_type="provider_snapshot",
            observed_at=datetime.now(UTC) - timedelta(seconds=61),
            bounded_index=first_index,
        )
    )

    await omnigent_session_activities.omnigent_observe_snapshot_activity(request)
    second_index = dict(writes[-1]["bounded_index"])
    assert second_index["providerTurn"]["turnComplete"] is True

    session.cleanup_state = "host_stopped"
    client_state["available"] = False
    unavailable = (
        await omnigent_session_activities.omnigent_observe_snapshot_activity(
            request
        )
    )
    resource_index = dict(writes[-1]["bounded_index"])
    assert unavailable["readStatus"] == "unavailable"
    assert resource_index["host"]["runnerReady"] is False
    assert resource_index["profileLease"]["consumerActive"] is False


def test_every_reconciler_command_routes_to_bounded_activity_phases() -> None:
    assert BOUNDED_COMMAND_ACTIVITIES[DecisionKind.ENSURE_PROFILE_LEASE] == (
        "omnigent.ensure_provider_profile_lease",
    )
    assert BOUNDED_COMMAND_ACTIVITIES[DecisionKind.HARVEST_EVIDENCE] == (
        "omnigent.harvest_evidence",
        "omnigent.publish_workspace",
    )
    assert BOUNDED_COMMAND_ACTIVITIES[DecisionKind.BEGIN_CLEANUP] == (
        "omnigent.stop_provider_session",
        "omnigent.stop_host",
    )
    assert BOUNDED_COMMAND_ACTIVITIES[DecisionKind.RELEASE_LEASES] == (
        "omnigent.release_leases",
    )


def test_workflow_exposes_typed_wakes_controls_and_compact_query() -> None:
    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._initialize(_workflow_input())

    signal = OmnigentSessionSignal(requestId="wake-1", reasonCode="callback")
    supervisor.provider_observation_available(signal)
    supervisor.provider_callback_or_host_exit_recorded(signal)
    supervisor.approval_or_intervention_changed(signal)
    supervisor.operator_reconcile_requested(signal)
    supervisor.submit_authorized_turn(
        OmnigentSessionSignal(
            requestId="turn-2-request",
            turnAttemptId="turn-2",
            instructionRef="art_instruction_2",
        )
    )
    supervisor.cancel_or_interrupt_requested(
        OmnigentSessionSignal(requestId="cancel-1", reasonCode="operator_cancel")
    )
    supervisor.cleanup_requested(
        OmnigentSessionSignal(requestId="cleanup-1", reasonCode="operator_cleanup")
    )

    state = supervisor.get_state()
    assert state["sessionId"] == "oms_123"
    assert state["wakeSequence"] == 7
    assert state["cancelRequested"] is True
    assert state["cleanupRequested"] is True
    assert state["pendingIntentCount"] == 3
    assert "compiledExecutionIntentRef" not in state


def test_production_registry_and_catalog_include_supervisor_boundary() -> None:
    types = workflow_fleet_workflow_types(TemporalSettings())
    assert "MoonMind.OmnigentSession" in types

    catalog = build_default_activity_catalog()
    required = {
        "omnigent.resolve_intent",
        "omnigent.load_reconciliation_inputs",
        "omnigent.ensure_provider_profile_lease",
        "omnigent.ensure_host",
        "omnigent.ensure_provider_session",
        "omnigent.submit_turn",
        "omnigent.read_event_batch",
        "omnigent.observe_snapshot",
        "omnigent.harvest_evidence",
        "omnigent.publish_workspace",
        "omnigent.stop_provider_session",
        "omnigent.stop_host",
        "omnigent.release_leases",
        "omnigent.persist_decision",
        "omnigent.persist_signal_intents",
        "omnigent.record_terminal",
    }
    for activity_name in required:
        route = catalog.resolve_activity(activity_name)
        assert route.timeouts.start_to_close_seconds <= 300
        assert route.timeouts.schedule_to_close_seconds <= 600
        assert route.timeouts.heartbeat_timeout_seconds is None

    assert catalog.resolve_activity(
        "omnigent.read_event_batch"
    ).timeouts.start_to_close_seconds <= 30


def test_continue_as_new_carries_only_bounded_summary_state() -> None:
    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._initialize(_workflow_input())
    supervisor._decision_count = 101
    supervisor._observation_count = 203
    supervisor._turn_attempt_count = 2
    supervisor._last_revision = 17
    supervisor._last_event_cursor = "cursor-9"
    supervisor._last_snapshot_frontier = "snapshot-8"
    supervisor._terminal_result_ref = "art_result_1"

    carried = supervisor._build_continue_as_new_input()
    dumped = carried.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert dumped["sessionId"] == "oms_123"
    assert dumped["resumeState"] == {
        "continueAsNewCount": 1,
        "decisionCount": 101,
        "observationCount": 203,
        "turnAttemptCount": 2,
        "lastSessionRevision": 17,
        "lastEventCursor": "cursor-9",
        "lastSnapshotFrontier": "snapshot-8",
        "terminalResultRef": "art_result_1",
    }
    assert "providerToken" not in str(dumped)
    assert "workspacePath" not in str(dumped)


def test_continue_as_new_thresholds_reset_per_history_segment() -> None:
    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._initialize(
        _workflow_input(
            resumeState=OmnigentSessionContinueAsNewState(
                continueAsNewCount=2,
                decisionCount=200,
                observationCount=900,
                turnAttemptCount=25,
            )
        )
    )
    supervisor._segment_started_at = datetime(2026, 8, 18, tzinfo=UTC)
    with (
        patch(
            "moonmind.workflows.temporal.workflows.omnigent_session.workflow.info",
            return_value=SimpleNamespace(
                is_continue_as_new_suggested=False,
                get_current_history_length=lambda: 1,
            ),
        ),
        patch(
            "moonmind.workflows.temporal.workflows.omnigent_session.workflow.now",
            return_value=datetime(2026, 8, 18, tzinfo=UTC),
        ),
    ):
        assert supervisor._should_continue_as_new() is False


@pytest.mark.asyncio
async def test_signal_intent_is_not_lost_when_persistence_retries() -> None:
    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._initialize(_workflow_input())
    supervisor.cancel_or_interrupt_requested(
        OmnigentSessionSignal(requestId="cancel-retry")
    )
    durable = DurableSessionState(
        sessionId="oms_123",
        revision=1,
        ownerToken="owner",
        fencingGeneration=1,
    )
    supervisor._execute_activity = AsyncMock(side_effect=RuntimeError("retry"))

    with pytest.raises(RuntimeError, match="retry"):
        await supervisor._persist_pending_signal_intents(durable)
    assert len(supervisor._pending_signal_intents) == 1

    supervisor._execute_activity = AsyncMock(return_value={"appliedIntentCount": 1})
    assert await supervisor._persist_pending_signal_intents(durable) is True
    assert supervisor._pending_signal_intents == []


@pytest.mark.asyncio
async def test_timeout_reconciles_authoritative_snapshot_before_terminal_intent() -> None:
    """A missed terminal edge wins over an already elapsed workflow deadline."""

    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    intent = CompiledSessionIntent(
        sessionId="oms_123",
        provider="omnigent",
        requiresProfileLease=False,
        requiresHost=False,
        maxTurnAttempts=1,
        reconcileIntervalSeconds=30,
        turnPromptDigest="sha256:prompt",
    )
    durable = DurableSessionState(
        sessionId="oms_123",
        revision=5,
        ownerToken="owner",
        fencingGeneration=1,
        providerSessionAttached=True,
        providerSessionId="provider-session-1",
        attemptId="turn-1",
        submission=SubmissionState.ACCEPTED,
    )
    load_count = 0
    calls: list[str] = []

    class StopAfterTerminalDecision(RuntimeError):
        pass

    async def execute(activity_name: str, _payload: object) -> object:
        nonlocal load_count
        calls.append(activity_name)
        if activity_name == "omnigent.load_reconciliation_inputs":
            load_count += 1
            observations = (
                ObservationSet()
                if load_count == 1
                else ObservationSet(
                    providerSession=ProviderSessionObservation(
                        observedAt=now,
                        providerSessionId="provider-session-1",
                        rawStatus="completed",
                        snapshotDigest="snapshot-terminal",
                    )
                )
            )
            return {
                "intent": intent.model_dump(mode="json", by_alias=True),
                "durable": durable.model_dump(mode="json", by_alias=True),
                "observations": observations.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                ),
                "phase": "turn_in_flight",
                "timeoutAt": (now - timedelta(seconds=1)).isoformat(),
            }
        if activity_name == "omnigent.read_event_batch":
            return {"observationCount": 0}
        if activity_name == "omnigent.observe_snapshot":
            return {
                "observationCount": 1,
                "snapshotFrontier": "snapshot-terminal",
            }
        if activity_name == "omnigent.persist_decision":
            return {"decisionId": "decision-terminal"}
        if activity_name == "omnigent.record_terminal":
            raise StopAfterTerminalDecision
        raise AssertionError(activity_name)

    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._execute_activity = execute  # type: ignore[method-assign]
    supervisor._update_visibility = lambda: None  # type: ignore[method-assign]
    with patch(
        "moonmind.workflows.temporal.workflows.omnigent_session.workflow.now",
        return_value=now,
    ):
        with pytest.raises(StopAfterTerminalDecision):
            await supervisor.run(_workflow_input())

    assert calls[:4] == [
        "omnigent.load_reconciliation_inputs",
        "omnigent.read_event_batch",
        "omnigent.observe_snapshot",
        "omnigent.load_reconciliation_inputs",
    ]
    assert "omnigent.persist_signal_intents" not in calls
    assert calls[-1] == "omnigent.record_terminal"


@pytest.mark.asyncio
async def test_unavailable_snapshot_does_not_satisfy_timeout_reconciliation() -> None:
    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._initialize(_workflow_input())
    durable = DurableSessionState(
        sessionId="oms_123",
        revision=1,
        ownerToken="owner",
        fencingGeneration=1,
    )
    supervisor._execute_activity = AsyncMock(
        side_effect=(
            {"observationCount": 0, "readStatus": "unavailable"},
            {
                "observationCount": 0,
                "readStatus": "unavailable",
                "snapshotFrontier": None,
            },
        )
    )

    assert await supervisor._observe_after_wait(durable) is False
    assert supervisor._timeout_snapshot_observed is False


def test_agent_run_patch_preserves_legacy_replay_and_selects_new_supervisor() -> None:
    source = inspect.getsource(MoonMindAgentRun.run)

    assert "OMNIGENT_SESSION_SUPERVISOR_PATCH_ID" in source
    assert '"MoonMind.OmnigentSession"' in source
    assert "omnigent_session_workflow_id" in source
    assert "ChildWorkflowCancellationType.ABANDON" in source
    assert '"cancel_or_interrupt_requested"' in source
    assert "OMNIGENT_PROFILE_BOUND_EXECUTION_PATCH_ID" in source
    assert '"integration.omnigent.profile_bound_execute"' in source
