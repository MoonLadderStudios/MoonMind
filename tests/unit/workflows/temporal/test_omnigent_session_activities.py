"""Unit tests for the bounded MoonMind.OmnigentSession activities (#3705)."""

from __future__ import annotations

import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.omnigent.session_commands import (
    InMemoryOmnigentSessionStore,
    OmnigentSessionCommandExecutor,
    OmnigentSessionCommandOutcome,
)
from moonmind.omnigent.session_reconciler import (
    OmnigentSessionCommand,
    OmnigentSessionCommandKind,
    OmnigentSessionFrontier,
    OmnigentSessionIntent,
)
from moonmind.workflows.temporal.activities import omnigent_session_activities as acts


def _intent() -> OmnigentSessionIntent:
    return OmnigentSessionIntent(
        canonicalSessionId="wf-1:omnigent",
        executionIntentRef="ref",
        executionIntentDigest="digest",
        owningWorkflowId="user-wf-1",
        stepExecutionId="step-1",
        agentRunId="wf-1",
        executionProfileRef="profile:codex",
        initialTurnAttemptId="turn-1",
        admittedFeatureGeneration=1,
    )


class FakeProviderPort:
    async def execute(self, kind, intent, command, frontier):
        if kind is OmnigentSessionCommandKind.ENSURE_HOST:
            return OmnigentSessionCommandOutcome(frontierUpdates={"host_ready": True})
        return OmnigentSessionCommandOutcome()


@pytest.fixture(autouse=True)
def _executor_cleanup():
    yield
    acts.set_omnigent_session_command_executor(None)


@pytest.mark.asyncio
async def test_resolve_intent_admits_when_enabled(monkeypatch):
    monkeypatch.setenv("OMNIGENT_SESSION_SUPERVISOR_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SESSION_SUPERVISOR_CANARY_PERCENT", "100")
    env = ActivityEnvironment()
    payload = {
        "canonicalSessionId": "wf-1:omnigent",
        "executionIntentRef": "ref",
        "executionIntentDigest": "digest",
        "owningWorkflowId": "user-wf-1",
        "stepExecutionId": "step-1",
        "agentRunId": "wf-1",
        "executionProfileRef": "profile:codex",
        "initialTurnAttemptId": "turn-1",
    }
    result = await env.run(acts.omnigent_resolve_intent_activity, payload)
    assert result["admitted"] is True
    assert result["intent"]["canonicalSessionId"] == "wf-1:omnigent"


@pytest.mark.asyncio
async def test_resolve_intent_declines_when_disabled(monkeypatch):
    monkeypatch.delenv("OMNIGENT_SESSION_SUPERVISOR_ENABLED", raising=False)
    env = ActivityEnvironment()
    payload = {
        "canonicalSessionId": "wf-1:omnigent",
        "executionIntentRef": "ref",
        "executionIntentDigest": "digest",
        "owningWorkflowId": "user-wf-1",
        "stepExecutionId": "step-1",
        "agentRunId": "wf-1",
        "executionProfileRef": "profile:codex",
        "initialTurnAttemptId": "turn-1",
    }
    result = await env.run(acts.omnigent_resolve_intent_activity, payload)
    assert result["admitted"] is False
    assert result["intent"] is None


@pytest.mark.asyncio
async def test_command_activity_returns_merged_frontier():
    store = InMemoryOmnigentSessionStore()
    acts.set_omnigent_session_command_executor(
        OmnigentSessionCommandExecutor(store=store, port=FakeProviderPort())
    )
    intent = _intent()
    frontier = OmnigentSessionFrontier(provider_profile_lease_held=True)
    command = OmnigentSessionCommand(
        kind=OmnigentSessionCommandKind.ENSURE_HOST,
        expectedGeneration=1,
        idempotencyKey="ensure_host:1",
    )
    env = ActivityEnvironment()
    result = await env.run(
        acts.omnigent_ensure_host_activity,
        {
            "intent": intent.model_dump(mode="json", by_alias=True),
            "command": command.model_dump(mode="json", by_alias=True),
            "frontier": frontier.model_dump(mode="json", by_alias=True),
        },
    )
    assert result["frontier"]["hostReady"] is True


@pytest.mark.asyncio
async def test_load_reconciliation_inputs_returns_frontier():
    store = InMemoryOmnigentSessionStore()
    acts.set_omnigent_session_command_executor(
        OmnigentSessionCommandExecutor(store=store, port=FakeProviderPort())
    )
    intent = _intent()
    env = ActivityEnvironment()
    result = await env.run(
        acts.omnigent_load_reconciliation_inputs_activity,
        {"intent": intent.model_dump(mode="json", by_alias=True)},
    )
    assert result["frontier"]["fencingGeneration"] == 1
    assert result["frontier"]["turnSubmitted"] is False


@pytest.mark.asyncio
async def test_persist_decision_returns_count():
    intent = _intent()
    env = ActivityEnvironment()
    result = await env.run(
        acts.omnigent_persist_decision_activity,
        {
            "intent": intent.model_dump(mode="json", by_alias=True),
            "status": "awaiting_observation",
            "reasonCodes": ["reading_event_batch"],
            "decisionCount": 7,
            "frontier": OmnigentSessionFrontier().model_dump(mode="json", by_alias=True),
        },
    )
    assert result == {"persisted": True, "decisionCount": 7}
