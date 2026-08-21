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
    ProviderStatusClass,
    SubmissionState,
    TerminalOutcome,
    classify_provider_status,
)
from moonmind.omnigent.turn_contracts import PRE_CUTOVER_SIGNAL_TURN_SOURCE
from moonmind.schemas.agent_runtime_models import AgentRunResult
from moonmind.schemas.omnigent_session_models import (
    OMNIGENT_SESSION_FEATURE_GENERATION,
    OmnigentPersistFailureRequest,
    OmnigentFailureAuthorityRequest,
    OmnigentSessionAdmissionDecision,
    OmnigentSessionAdmissionRequest,
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
    MAX_PENDING_SIGNAL_INTENTS,
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


def test_admission_contract_is_frozen_compact_and_fail_closed() -> None:
    request = OmnigentSessionAdmissionRequest(
        workflowId="workflow-1",
        stepExecutionId="step-1",
        agentRunId="agent-run-1",
        executionProfileRef="omnigent-codex",
    )
    admitted = OmnigentSessionAdmissionDecision(
        admitted=True,
        reasonCode="enabled",
        admissionMode="enabled",
        admittedFeatureGeneration=OMNIGENT_SESSION_FEATURE_GENERATION,
    )

    assert request.model_dump(mode="json", by_alias=True) == {
        "workflowId": "workflow-1",
        "stepExecutionId": "step-1",
        "agentRunId": "agent-run-1",
        "executionProfileRef": "omnigent-codex",
    }
    assert admitted.admitted_feature_generation == "omnigent-session-v1"
    with pytest.raises(ValidationError):
        OmnigentSessionAdmissionDecision(
            admitted=True,
            reasonCode="enabled",
            admissionMode="enabled",
            admittedFeatureGeneration="omnigent-session-v2",
        )


def test_failure_contract_carries_only_typed_bounded_evidence() -> None:
    authority = OmnigentFailureAuthorityRequest(
        sessionId="oms_123",
        compiledExecutionIntentRef="art_intent_123",
        compiledExecutionIntentDigest="sha256:" + "a" * 64,
        workflowId="workflow-1",
        stepExecutionId="step-1",
        agentRunId="agent-run-1",
    )
    request = OmnigentPersistFailureRequest(
        sessionId="oms_123",
        compiledExecutionIntentRef="art_intent_123",
        compiledExecutionIntentDigest="sha256:" + "a" * 64,
        expectedRevision=5,
        fencingGeneration=2,
        decisionId="decision-5",
        commandId="command-5",
        status="cleanup_incomplete",
        failedActivity="omnigent.stop_host",
        reasonCode="bounded_activity_exhausted",
    )

    assert authority.workflow_id == "workflow-1"
    assert request.status == "cleanup_incomplete"
    assert request.failed_activity == "omnigent.stop_host"
    with pytest.raises(ValidationError):
        OmnigentPersistFailureRequest(
            **request.model_dump(mode="python", by_alias=True),
            error="provider token and unbounded exception prose",
        )


def test_signal_contract_carries_only_safe_ids_and_refs() -> None:
    signal = OmnigentSessionSignal(
        requestId="request-1",
        observationRef="art_observation_1",
        turnAttemptId="turn-2",
        turnSource="workflow_chat",
        reasonCode="operator_reconcile",
        observedAt=datetime(2026, 8, 18, tzinfo=UTC),
    )
    assert signal.request_id == "request-1"
    assert signal.turn_source == "workflow_chat"

    # The turn source is a closed vocabulary (#3707): an invented source kind
    # cannot reach the supervisor even through a well-formed signal.
    with pytest.raises(ValidationError):
        OmnigentSessionSignal(
            requestId="request-3",
            turnAttemptId="turn-3",
            turnSource="continuation",
        )

    # A continuation must still name its turn identity and instruction.
    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._initialize(_workflow_input())
    with pytest.raises(ValueError):
        supervisor.submit_authorized_turn(
            OmnigentSessionSignal(requestId="request-4")
        )

    # An *omitted* source is a pre-#3707 in-flight history, not a live producer
    # skipping the field: raising on replay would wedge an already-admitted run,
    # so it resolves to the one deterministic pre-cutover source instead. The
    # replay/cutover contract is covered in test_omnigent_supervisor_replay.py.
    supervisor.submit_authorized_turn(
        OmnigentSessionSignal(
            requestId="request-4",
            turnAttemptId="turn-4",
            instructionRef="art_instruction_4",
        )
    )
    queued = supervisor._pending_signal_intents[-1]
    assert queued["kind"] == "submit_authorized_continuation"
    assert queued["payload"]["turnSource"] == (
        PRE_CUTOVER_SIGNAL_TURN_SOURCE.value
    )

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
            # The real repository is idempotent on (session, dedup key); a fake
            # that always appends cannot catch an identity collision.
            identity = (kwargs["session_id"], kwargs["deduplication_key"])
            for existing in writes:
                if (
                    existing["session_id"],
                    existing["deduplication_key"],
                ) == identity:
                    return SimpleNamespace(**existing)
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
    # The confirming read repeats the identical provider snapshot, so it must
    # still persist as its own row instead of deduplicating against the pending
    # observation and losing the completion evidence.
    assert len(writes) == 2
    assert writes[0]["source_digest"] == writes[1]["source_digest"]
    assert writes[0]["deduplication_key"] != writes[1]["deduplication_key"]
    assert writes[0]["observation_id"] != writes[1]["observation_id"]

    # A retry of that same confirming read is still deduplicated.
    await omnigent_session_activities.omnigent_observe_snapshot_activity(request)
    assert len(writes) == 2

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
            turnSource="repository_continuation",
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
        "omnigent.evaluate_session_admission",
        "omnigent.resolve_intent",
        "omnigent.load_reconciliation_inputs",
        "omnigent.load_failure_authority",
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
        "omnigent.persist_failure",
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
        if activity_name == "omnigent.heartbeat_host_lease":
            return {"hostLeaseHeartbeat": "renewed"}
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

    assert calls[:5] == [
        "omnigent.load_reconciliation_inputs",
        "omnigent.heartbeat_host_lease",
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
            {"hostLeaseHeartbeat": "renewed"},
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
    assert "OMNIGENT_SESSION_ADMISSION_PATCH_ID" in source
    assert '"omnigent.evaluate_session_admission"' in source
    assert '"MoonMind.OmnigentSession"' in source
    assert "omnigent_session_workflow_id" in source
    assert "ChildWorkflowCancellationType.ABANDON" in source
    assert '"cancel_or_interrupt_requested"' in source
    assert "OMNIGENT_PROFILE_BOUND_EXECUTION_PATCH_ID" in source
    assert '"integration.omnigent.profile_bound_execute"' in source


# ---------------------------------------------------------------------------
# Codex review follow-ups on MoonLadderStudios/MoonMind#3742
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("terminal_state", "expected"),
    [
        ("completed", TerminalOutcome.SUCCESS),
        ("success", TerminalOutcome.SUCCESS),
        ("failed", TerminalOutcome.FAILURE),
        # A timeout is a system failure, not a user cancellation. Classifying it
        # as cancelled makes a later `failed` provider snapshot look like a
        # contradictory terminal and quarantines an already timed-out session.
        ("timed_out", TerminalOutcome.FAILURE),
        ("timeout", TerminalOutcome.FAILURE),
        ("delivery_unknown", TerminalOutcome.FAILURE),
        ("canceled", TerminalOutcome.CANCELLED),
        ("cancelled", TerminalOutcome.CANCELLED),
    ],
)
def test_durable_terminal_outcome_matches_reducer_classification(
    terminal_state: str, expected: TerminalOutcome
) -> None:
    assert (
        omnigent_session_activities._durable_terminal_outcome(
            terminal_state, TerminalOutcome, classify_provider_status
        )
        is expected
    )
    if terminal_state in {"timed_out", "timeout", "failed"}:
        assert (
            classify_provider_status(terminal_state)
            is ProviderStatusClass.TERMINAL_FAILURE
        )


def test_durable_terminal_outcome_is_none_without_terminal_state() -> None:
    for empty in (None, "", "   "):
        assert (
            omnigent_session_activities._durable_terminal_outcome(
                empty, TerminalOutcome, classify_provider_status
            )
            is None
        )


def _signal(request_id: str, **updates: object) -> OmnigentSessionSignal:
    payload: dict[str, object] = {"requestId": request_id}
    payload.update(updates)
    return OmnigentSessionSignal.model_validate(payload)


def test_full_signal_backlog_never_throws_from_a_signal_handler() -> None:
    """Raising here would fail the workflow task and replay the same signal."""

    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._initialize(_workflow_input())
    for index in range(MAX_PENDING_SIGNAL_INTENTS):
        supervisor._queue_signal_intent(
            "approval_or_intervention_changed", _signal(f"req-{index}")
        )
    assert len(supervisor._pending_signal_intents) == MAX_PENDING_SIGNAL_INTENTS

    # An overflowing non-recovery intent is counted, not raised.
    supervisor._queue_signal_intent(
        "approval_or_intervention_changed", _signal("req-overflow")
    )
    assert supervisor._dropped_signal_intents == 1
    assert len(supervisor._pending_signal_intents) == MAX_PENDING_SIGNAL_INTENTS

    # Cancellation and cleanup are the intents needed to recover a wedged
    # session, so they are still admitted past the bound.
    supervisor.cancel_or_interrupt_requested(_signal("cancel-1"))
    supervisor.cleanup_requested(_signal("cleanup-1"))
    queued_kinds = [
        item["kind"] for item in supervisor._pending_signal_intents
    ]
    assert "cancel_or_interrupt_requested" in queued_kinds
    assert "cleanup_requested" in queued_kinds
    assert supervisor._cancel_requested is True
    assert supervisor._cleanup_requested is True

    # A second cancel does not grow the backlog without bound either.
    supervisor.cancel_or_interrupt_requested(_signal("cancel-2"))
    assert queued_kinds.count("cancel_or_interrupt_requested") == 1
    assert supervisor.get_state()["droppedIntentCount"] == 2


def test_repeated_signal_request_id_is_deduplicated_not_requeued() -> None:
    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._initialize(_workflow_input())
    supervisor.cancel_or_interrupt_requested(_signal("cancel-1"))
    supervisor.cancel_or_interrupt_requested(_signal("cancel-1"))
    assert len(supervisor._pending_signal_intents) == 1
    assert supervisor._dropped_signal_intents == 0
    # A distinct request id is still real new intent.
    supervisor.cleanup_requested(_signal("cleanup-1"))
    assert len(supervisor._pending_signal_intents) == 2


@pytest.mark.asyncio
async def test_poll_cycle_renews_host_lease_before_observing() -> None:
    supervisor = MoonMindOmnigentSessionWorkflow()
    supervisor._initialize(_workflow_input())
    durable = DurableSessionState(
        sessionId="oms_123",
        revision=1,
        ownerToken="owner",
        fencingGeneration=1,
    )
    calls: list[str] = []

    async def execute(activity_name: str, _payload: object) -> object:
        calls.append(activity_name)
        if activity_name == "omnigent.heartbeat_host_lease":
            return {"hostLeaseHeartbeat": "renewed"}
        return {"observationCount": 0}

    supervisor._execute_activity = execute  # type: ignore[method-assign]
    assert await supervisor._observe_after_wait(durable) is True
    assert calls == [
        "omnigent.heartbeat_host_lease",
        "omnigent.read_event_batch",
        "omnigent.observe_snapshot",
    ]
    assert supervisor.get_state()["hostLeaseHeartbeat"] == "renewed"


@pytest.mark.asyncio
async def test_heartbeat_activity_renews_only_a_renewable_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lease owned by cleanup is reported, not renewed out from under it."""

    import moonmind.omnigent.control_plane as control_plane_module
    import moonmind.omnigent.oauth_hosts as oauth_hosts_module

    session = SimpleNamespace(
        session_id="oms_123",
        host_lease_ref="host-lease-1",
        cleanup_state="pending",
    )
    lease_state = {"status": "assigned", "cleanupClaimed": False}
    heartbeats: list[str] = []

    class FakeSessions:
        async def get(self, _session_id: str) -> object:
            return session

    class FakeStore:
        @asynccontextmanager
        async def transaction(self):
            yield SimpleNamespace(sessions=FakeSessions())

    class FakeHosts:
        def __init__(self, _session_factory: object) -> None:
            pass

        async def get_host_lease(self, lease_id: str) -> object:
            if lease_state["status"] == "missing":
                return None
            return SimpleNamespace(
                lease_id=lease_id, status=lease_state["status"]
            )

        async def heartbeat_host_lease(self, lease_id: str) -> object:
            if lease_state["cleanupClaimed"]:
                raise oauth_hosts_module.OmnigentOAuthHostError(
                    "host lease cleanup is owned by the janitor",
                    code=oauth_hosts_module.HOST_CLEANUP_CLAIMED_ERROR_CODE,
                )
            heartbeats.append(lease_id)
            return SimpleNamespace(lease_id=lease_id, status="assigned")

    monkeypatch.setattr(
        control_plane_module,
        "OmnigentControlPlaneStore",
        lambda _session_maker: FakeStore(),
    )
    monkeypatch.setattr(
        oauth_hosts_module, "OmnigentOAuthHostRepository", FakeHosts
    )
    request = {
        "sessionId": "oms_123",
        "compiledExecutionIntentRef": "art_intent_123",
        "compiledExecutionIntentDigest": "sha256:" + "a" * 64,
        "expectedRevision": 7,
        "fencingGeneration": 1,
    }
    heartbeat = omnigent_session_activities.omnigent_heartbeat_host_lease_activity

    assert (await heartbeat(request))["hostLeaseHeartbeat"] == "renewed"
    assert heartbeats == ["host-lease-1"]

    # `draining` is owned by whoever won the cleanup fence.
    lease_state["status"] = "draining"
    assert (await heartbeat(request))["hostLeaseHeartbeat"] == "not_renewable"

    # Read as renewable, then drained before the heartbeat CAS landed.
    lease_state["status"] = "assigned"
    lease_state["cleanupClaimed"] = True
    assert (await heartbeat(request))["hostLeaseHeartbeat"] == "cleanup_claimed"

    lease_state["cleanupClaimed"] = False
    lease_state["status"] = "missing"
    assert (await heartbeat(request))["hostLeaseHeartbeat"] == "missing"

    lease_state["status"] = "assigned"
    session.host_lease_ref = None
    assert (await heartbeat(request))["hostLeaseHeartbeat"] == "not_attached"
    assert heartbeats == ["host-lease-1"]


@pytest.mark.asyncio
async def test_stop_host_does_not_run_cleanup_it_did_not_win(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two cleanup owners must not delete the same host concurrently."""

    import moonmind.omnigent.control_plane as control_plane_module
    import moonmind.omnigent.oauth_hosts as oauth_hosts_module

    session = SimpleNamespace(
        session_id="oms_123",
        host_lease_ref="host-lease-1",
        cleanup_state="host_stopped",
        metadata={},
        revision=7,
        fencing_generation=1,
    )
    claims: list[dict[str, object]] = []

    class FakeSessions:
        async def get(self, _session_id: str) -> object:
            return session

    class FakeStore:
        @asynccontextmanager
        async def transaction(self):
            yield SimpleNamespace(sessions=FakeSessions())

    class FakeHosts:
        def __init__(self, _session_factory: object) -> None:
            pass

        async def get_host_lease(self, lease_id: str) -> object:
            return SimpleNamespace(
                lease_id=lease_id,
                status="draining",
                last_heartbeat_at=datetime.now(UTC),
                binding_ref="binding-1",
            )

        async def claim_host_lease_cleanup(self, lease_id: str, **kwargs: object):
            claims.append({"leaseId": lease_id, **kwargs})
            return None

        async def validate_binding(self, _binding_ref: str) -> object:
            raise AssertionError("cleanup ran without winning the fence")

    monkeypatch.setattr(
        control_plane_module,
        "OmnigentControlPlaneStore",
        lambda _session_maker: FakeStore(),
    )
    monkeypatch.setattr(
        oauth_hosts_module, "OmnigentOAuthHostRepository", FakeHosts
    )

    async def fake_claim(_request: object) -> tuple[object, bool]:
        return SimpleNamespace(status="claimed"), True

    async def fake_settle(_request: object, **_kwargs: object) -> dict[str, object]:
        return {"commandId": "cmd-1", "outcome": "settled"}

    monkeypatch.setattr(
        omnigent_session_activities, "_claim_command", fake_claim
    )
    monkeypatch.setattr(
        omnigent_session_activities, "_settle_command", fake_settle
    )

    result = await omnigent_session_activities.omnigent_stop_host_activity(
        {
            "sessionId": "oms_123",
            "compiledExecutionIntentRef": "art_intent_123",
            "compiledExecutionIntentDigest": "sha256:" + "a" * 64,
            "expectedRevision": 7,
            "fencingGeneration": 1,
            "commandId": "cmd-1",
        }
    )

    assert result["outcome"] == "settled"
    # The fence was attempted with the observed status *and* heartbeat, so a
    # lease heartbeated since the read cannot hand authority to a second owner.
    assert len(claims) == 3
    assert claims[0]["expected_status"] == "draining"
    assert "expected_last_heartbeat_at" in claims[0]


@pytest.mark.asyncio
async def test_profile_lease_request_carries_owning_workflow_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An activity-owned grant is rejected without `metadata.workflowId`."""

    from moonmind.workflows.temporal.workflows.provider_profile_manager import (
        MoonMindProviderProfileManagerWorkflow,
    )

    import moonmind.omnigent.control_plane as control_plane_module
    import moonmind.provider_profiles.lease_client as lease_client_module

    session = SimpleNamespace(
        session_id="oms_123",
        revision=7,
        provider_profile_generation=3,
        step_execution_id="step-1",
    )
    captured: dict[str, object] = {}

    class FakeSessions:
        async def get(self, _session_id: str) -> object:
            return session

        async def bind_runtime_authority(self, _session_id: str, **_kwargs: object):
            return session

    class FakeStore:
        @asynccontextmanager
        async def transaction(self):
            yield SimpleNamespace(sessions=FakeSessions())

    class FakeLeaseClient:
        def __init__(self, _adapter: object) -> None:
            pass

        async def acquire_execution_lease(self, **kwargs: object) -> object:
            captured.update(kwargs)
            return SimpleNamespace(
                lease_id="profile-lease-1", owner_id=kwargs["owner_id"]
            )

    class FakeDbSession:
        async def get(self, _model: object, _profile_id: str) -> object:
            return SimpleNamespace(
                enabled=True,
                auth_state="connected",
                runtime_id="codex_cli",
                credential_generation=4,
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

    async def fake_intent(_request: object) -> object:
        return SimpleNamespace(
            execution_profile_ref="omnigent-codex", idempotency_key="idem-1"
        )

    monkeypatch.setattr(
        control_plane_module,
        "OmnigentControlPlaneStore",
        lambda _session_maker: FakeStore(),
    )
    monkeypatch.setattr(
        lease_client_module, "ProviderProfileLeaseClient", FakeLeaseClient
    )
    monkeypatch.setattr(
        omnigent_session_activities, "_load_intent_request", fake_intent
    )

    async def fake_claim(_request: object) -> tuple[object, bool]:
        return SimpleNamespace(status="claimed"), True

    async def fake_settle(_request: object, **_kwargs: object) -> dict[str, object]:
        return {"commandId": "cmd-1"}

    monkeypatch.setattr(
        omnigent_session_activities, "_claim_command", fake_claim
    )
    monkeypatch.setattr(
        omnigent_session_activities, "_settle_command", fake_settle
    )
    monkeypatch.setattr(
        "api_service.db.base.async_session_maker",
        lambda: FakeDbSession(),
        raising=False,
    )

    await omnigent_session_activities.omnigent_ensure_provider_profile_lease_activity(
        {
            "sessionId": "oms_123",
            "compiledExecutionIntentRef": "art_intent_123",
            "compiledExecutionIntentDigest": "sha256:" + "a" * 64,
            "expectedRevision": 7,
            "fencingGeneration": 1,
            "commandId": "cmd-1",
        }
    )

    metadata = dict(captured["metadata"])  # type: ignore[arg-type]
    assert metadata["workflowId"] == omnigent_session_workflow_id("oms_123")
    assert metadata["stepExecutionId"] == "step-1"
    assert metadata["ownerIsWorkflow"] is False
    # The manager's allowlist is what makes a session-only key unusable here.
    safe = MoonMindProviderProfileManagerWorkflow._safe_lease_metadata(
        {"metadata": metadata}
    )
    assert safe["workflowId"] == omnigent_session_workflow_id("oms_123")
    assert "canonicalSessionId" not in safe
