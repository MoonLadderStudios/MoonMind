"""Unit tests for the fenced, idempotent Omnigent session command layer (#3705)."""

from __future__ import annotations

import pytest

from moonmind.omnigent.session_commands import (
    InMemoryOmnigentSessionStore,
    NullOmnigentSessionProviderPort,
    OmnigentSessionCommandExecutor,
    OmnigentSessionCommandOutcome,
    OmnigentSessionCommandUnavailableError,
    OmnigentSessionFencedError,
)
from moonmind.omnigent.session_reconciler import (
    OmnigentSessionCommand,
    OmnigentSessionCommandCondition,
    OmnigentSessionCommandKind,
    OmnigentSessionFrontier,
    OmnigentSessionIntent,
)


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


def _command(kind, *, generation=1, key=None) -> OmnigentSessionCommand:
    return OmnigentSessionCommand(
        kind=kind,
        expectedGeneration=generation,
        idempotencyKey=key or f"{kind.value}:{generation}",
    )


class CountingPort:
    """Fake provider port that counts executions per idempotency key."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, kind, intent, command, frontier):
        self.calls.append(command.idempotency_key)
        if kind is OmnigentSessionCommandKind.ENSURE_PROVIDER_PROFILE_LEASE:
            return OmnigentSessionCommandOutcome(
                frontierUpdates={"provider_profile_lease_held": True}
            )
        return OmnigentSessionCommandOutcome()


@pytest.mark.asyncio
async def test_command_applies_frontier_update_and_persists():
    store = InMemoryOmnigentSessionStore()
    executor = OmnigentSessionCommandExecutor(store=store, port=CountingPort())
    intent = _intent()
    outcome = await executor.execute(
        intent, _command(OmnigentSessionCommandKind.ENSURE_PROVIDER_PROFILE_LEASE)
    )
    assert outcome.condition is OmnigentSessionCommandCondition.OK
    record = await store.load(intent.canonical_session_id)
    assert record is not None
    assert record.frontier.provider_profile_lease_held is True


@pytest.mark.asyncio
async def test_idempotent_retry_does_not_repeat_side_effect():
    store = InMemoryOmnigentSessionStore()
    port = CountingPort()
    executor = OmnigentSessionCommandExecutor(store=store, port=port)
    intent = _intent()
    command = _command(OmnigentSessionCommandKind.ENSURE_PROVIDER_PROFILE_LEASE)

    first = await executor.execute(intent, command)
    second = await executor.execute(intent, command)

    assert port.calls == [command.idempotency_key]  # executed exactly once
    assert first.frontier_updates == second.frontier_updates


@pytest.mark.asyncio
async def test_stale_generation_command_is_fenced():
    store = InMemoryOmnigentSessionStore()
    executor = OmnigentSessionCommandExecutor(store=store, port=CountingPort())
    intent = _intent()

    # Advance the durable generation to 2 by executing a gen-2 command.
    await executor.execute(
        intent,
        _command(OmnigentSessionCommandKind.OBSERVE_SNAPSHOT, generation=2, key="obs:2"),
    )

    # A delayed gen-1 command must be rejected, not applied.
    with pytest.raises(OmnigentSessionFencedError):
        await executor.execute(
            intent,
            _command(
                OmnigentSessionCommandKind.SUBMIT_TURN, generation=1, key="submit:1"
            ),
        )


@pytest.mark.asyncio
async def test_bump_generation_advances_record():
    class BumpingPort:
        async def execute(self, kind, intent, command, frontier):
            return OmnigentSessionCommandOutcome(
                frontierUpdates={"turn_submitted": True}, bumpGeneration=True
            )

    store = InMemoryOmnigentSessionStore()
    executor = OmnigentSessionCommandExecutor(store=store, port=BumpingPort())
    intent = _intent()
    outcome = await executor.execute(
        intent, _command(OmnigentSessionCommandKind.SUBMIT_TURN)
    )
    assert outcome.bump_generation is True
    record = await store.load(intent.canonical_session_id)
    assert record.generation == 2
    assert record.frontier.fencing_generation == 2


@pytest.mark.asyncio
async def test_null_port_fails_closed():
    store = InMemoryOmnigentSessionStore()
    executor = OmnigentSessionCommandExecutor(
        store=store, port=NullOmnigentSessionProviderPort()
    )
    with pytest.raises(OmnigentSessionCommandUnavailableError):
        await executor.execute(
            _intent(), _command(OmnigentSessionCommandKind.ENSURE_HOST)
        )


def test_outcome_merged_frontier_uses_field_names():
    frontier = OmnigentSessionFrontier()
    outcome = OmnigentSessionCommandOutcome(
        frontierUpdates={"host_ready": True}, bumpGeneration=True
    )
    merged = outcome.merged_frontier(frontier)
    assert merged.host_ready is True
    assert merged.fencing_generation == frontier.fencing_generation + 1
    # Original is untouched (pure).
    assert frontier.host_ready is False
