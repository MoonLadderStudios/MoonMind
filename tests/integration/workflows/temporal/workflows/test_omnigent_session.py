"""Integration tests for the MoonMind.OmnigentSession workflow (#3705).

These spawn a real Temporal test server via
``WorkflowEnvironment.start_time_skipping()`` and register the real workflow +
bounded activities against a hermetic in-memory command executor. They prove the
supervisor runs from immutable intent, converges, hands a compact terminal
result back to a parent workflow (the product-path handoff shape used by
``MoonMind.AgentRun``), and replays deterministically.
"""

from __future__ import annotations

import pytest

pytest.importorskip("temporalio")

# NOTE: Not marked integration_ci — Temporal workflow tests with time-skipping consistently exceed CI timeout thresholds. Kept for local dev verification.
pytestmark = [pytest.mark.integration]

from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.omnigent.session_commands import (
    InMemoryOmnigentSessionStore,
    OmnigentSessionCommandExecutor,
    OmnigentSessionCommandOutcome,
)
from moonmind.omnigent.session_reconciler import (
    OmnigentSessionCommandKind,
    OmnigentSessionIntent,
    OmnigentSessionReconcilePolicy,
    OmnigentSessionWorkflowInput,
)
from moonmind.workflows.temporal.activities.omnigent_session_activities import (
    OMNIGENT_SESSION_ACTIVITY_HANDLERS,
    set_omnigent_session_command_executor,
)
from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog
from moonmind.workflows.temporal.workflows.omnigent_session import (
    MoonMindOmnigentSessionWorkflow,
)

_TASK_QUEUE = build_default_activity_catalog().resolve_activity(
    "omnigent.persist_decision"
).task_queue


class _HappyPathPort:
    """Fake provider port that drives a complete happy-path lifecycle."""

    async def execute(self, kind, intent, command, frontier):
        updates: dict = {}
        if kind is OmnigentSessionCommandKind.ENSURE_PROVIDER_PROFILE_LEASE:
            updates = {"provider_profile_lease_held": True}
        elif kind is OmnigentSessionCommandKind.ENSURE_HOST:
            updates = {"host_ready": True}
        elif kind is OmnigentSessionCommandKind.ENSURE_PROVIDER_SESSION:
            updates = {
                "provider_session_established": True,
                "provider_session_id": "sess-1",
            }
        elif kind is OmnigentSessionCommandKind.SUBMIT_TURN:
            updates = {
                "turn_submitted": True,
                "turn_attempts": frontier.turn_attempts + 1,
            }
        elif kind in (
            OmnigentSessionCommandKind.READ_EVENT_BATCH,
            OmnigentSessionCommandKind.OBSERVE_SNAPSHOT,
        ):
            updates = {
                "terminal_observed": True,
                "terminal_outcome": "completed",
                "last_observed_provider_status": "completed",
            }
        elif kind is OmnigentSessionCommandKind.HARVEST_EVIDENCE:
            updates = {
                "evidence_harvested": True,
                "terminal_result_ref": "artifact:terminal",
                "diagnostics_ref": "artifact:diag",
            }
        elif kind is OmnigentSessionCommandKind.PUBLISH_WORKSPACE:
            updates = {"workspace_published": True}
        elif kind is OmnigentSessionCommandKind.STOP_PROVIDER_SESSION:
            updates = {"provider_session_stopped": True}
        elif kind is OmnigentSessionCommandKind.STOP_HOST:
            updates = {"host_stopped": True}
        elif kind is OmnigentSessionCommandKind.RELEASE_LEASES:
            updates = {"leases_released": True}
        return OmnigentSessionCommandOutcome(frontierUpdates=updates)


@workflow.defn(name="OmnigentSessionParentHarness")
class _ParentHarness:
    """Mimics the AgentRun -> OmnigentSession child handoff and compact result."""

    @workflow.run
    async def run(self, workflow_input: OmnigentSessionWorkflowInput) -> dict:
        return await workflow.execute_child_workflow(
            "MoonMind.OmnigentSession",
            workflow_input,
            id=f"{workflow_input.intent.canonical_session_id}:session",
            task_queue=_TASK_QUEUE,
        )


def _intent() -> OmnigentSessionIntent:
    return OmnigentSessionIntent(
        canonicalSessionId="wf-1:omnigent",
        executionIntentRef="artifact:intent",
        executionIntentDigest="digest",
        owningWorkflowId="user-wf-1",
        stepExecutionId="step-1",
        agentRunId="wf-1",
        executionProfileRef="profile:codex-oauth",
        initialTurnAttemptId="wf-1:omnigent:turn:1",
        admittedFeatureGeneration=1,
        policy=OmnigentSessionReconcilePolicy(snapshotIntervalSeconds=5),
    )


@pytest.fixture(autouse=True)
def _executor():
    set_omnigent_session_command_executor(
        OmnigentSessionCommandExecutor(
            store=InMemoryOmnigentSessionStore(), port=_HappyPathPort()
        )
    )
    yield
    set_omnigent_session_command_executor(None)


@pytest.mark.asyncio
async def test_session_workflow_converges_and_replays():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=_TASK_QUEUE,
            workflows=[MoonMindOmnigentSessionWorkflow],
            activities=list(OMNIGENT_SESSION_ACTIVITY_HANDLERS),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                MoonMindOmnigentSessionWorkflow.run,
                OmnigentSessionWorkflowInput(intent=_intent()),
                id="wf-1:omnigent:session",
                task_queue=_TASK_QUEUE,
            )
            result = await handle.result()
            assert result["status"] == "completed"
            assert result["terminalResultRef"] == "artifact:terminal"

            status = await handle.query("get_status")
            assert status["status"] == "completed"

            history = await handle.fetch_history()

    # New histories replay deterministically under the session workflow.
    replayer = Replayer(
        workflows=[MoonMindOmnigentSessionWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(history)


@pytest.mark.asyncio
async def test_parent_receives_compact_terminal_result():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=_TASK_QUEUE,
            workflows=[MoonMindOmnigentSessionWorkflow, _ParentHarness],
            activities=list(OMNIGENT_SESSION_ACTIVITY_HANDLERS),
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                _ParentHarness.run,
                OmnigentSessionWorkflowInput(intent=_intent()),
                id="user-wf-1:agentrun:harness",
                task_queue=_TASK_QUEUE,
            )
            assert result["status"] == "completed"
            assert result["canonicalSessionId"] == "wf-1:omnigent"
            assert result["decisionCount"] >= 1
