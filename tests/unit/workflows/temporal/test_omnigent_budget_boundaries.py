"""Provider-free integration of the registered session workflow for #4986."""

import asyncio
from collections import Counter, defaultdict
from contextlib import AsyncExitStack
from datetime import timedelta

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.omnigent.reconciler import (
    CompiledSessionIntent,
    DurableSessionState,
    EvidenceObservation,
    ObservationSet,
    ProviderSessionObservation,
    ProviderTurnObservation,
    SubmissionState,
    TerminalOutcome,
)
from moonmind.schemas.agent_runtime_models import (
    AgentRunResult,
    ExecutionBudgetContext,
    resolve_execution_budget,
)
from moonmind.schemas.omnigent_session_models import OmnigentSessionWorkflowInput
from moonmind.workflows.temporal.workflows import omnigent_session as module

pytestmark = [pytest.mark.asyncio, pytest.mark.temporal_boundary]


class SessionActivities:
    """Idempotent external state; workflow/reducer/dispatch remain production code."""

    def __init__(self, target, mode, now):
        self.target = target
        self.mode = mode
        self.now = now
        self.blocked = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = Counter()
        self.effects = Counter()
        self.observed_terminal = target in {
            "omnigent.harvest_evidence",
            "omnigent.publish_workspace",
        }
        self.terminal = self.observed_terminal
        self.saved = False
        self.harvested = False
        self.decisions = {}
        self.decision_writes = Counter()
        self.budgets = []

    def apply(self, name):
        if name in {"omnigent.observe_snapshot", "omnigent.read_event_batch"}:
            self.observed_terminal = True
        elif name == "omnigent.record_terminal":
            if not self.terminal:
                self.effects[name] += 1
            self.terminal = True
        elif name == "omnigent.harvest_evidence":
            if not self.harvested:
                self.effects[name] += 1
            self.harvested = True
        elif name == "omnigent.publish_workspace":
            if not self.saved:
                self.effects[name] += 1
            self.saved = True

    async def call(self, name, payload):
        self.calls[name] += 1
        if payload.get("executionBudget"):
            self.budgets.append(payload["executionBudget"])
        if name == self.target and self.calls[name] == 1:
            if self.mode == "lost_ack":
                self.apply(name)
            self.blocked.set()
            await self.release.wait()
            raise RuntimeError("Injected lost acknowledgement/process loss")
        self.apply(name)
        if name == "omnigent.load_reconciliation_inputs":
            intent = CompiledSessionIntent(
                sessionId="session",
                provider="omnigent",
                turnPromptDigest="sha256:prompt",
                requiresProfileLease=False,
                requiresHost=False,
                requiresCleanup=False,
                reconcileIntervalSeconds=1,
            )
            durable = DurableSessionState(
                sessionId="session",
                revision=1,
                ownerToken="owner",
                fencingGeneration=1,
                providerSessionAttached=True,
                providerSessionId="provider-session",
                attemptId="turn",
                submission=SubmissionState.ACCEPTED,
                terminalOutcome=TerminalOutcome.SUCCESS if self.terminal else None,
                terminalEvidenceRef="art_result" if self.saved else None,
                evidenceHarvested=self.harvested,
            )
            observations = ObservationSet(
                evidence=EvidenceObservation(
                    observedAt=self.now, terminalEvidenceAvailable=True
                ),
                providerSession=ProviderSessionObservation(
                    observedAt=self.now,
                    providerSessionId="provider-session",
                    rawStatus="completed" if self.observed_terminal else "running",
                    snapshotDigest="snapshot",
                ),
                providerTurn=(
                    ProviderTurnObservation(
                        observedAt=self.now,
                        attemptId="turn",
                        turnComplete=True,
                        rawStatus="completed",
                        outcome="success",
                    )
                    if self.observed_terminal
                    else None
                ),
            )
            return {
                "intent": intent.model_dump(mode="json", by_alias=True),
                "durable": durable.model_dump(mode="json", by_alias=True),
                "observations": observations.model_dump(mode="json", by_alias=True),
                **(
                    {
                        "terminalResult": {
                            "status": "completed",
                            "result": {"summary": "saved"},
                        }
                    }
                    if self.saved
                    else {}
                ),
            }
        if name == "omnigent.persist_decision":
            self.decision_writes[payload["decisionId"]] += 1
            self.decisions[payload["decisionId"]] = payload["decision"]
        if name in {"omnigent.read_event_batch", "omnigent.observe_snapshot"}:
            return {
                "observationCount": 1,
                "readStatus": "available",
                "snapshotFrontier": "terminal",
            }
        if name == "omnigent.publish_workspace":
            return {"terminalResultRef": "art_result"}
        return {}

    def registered(self, name):
        @activity.defn(name=name)
        async def handler(payload: dict) -> dict:
            return await self.call(name, payload)

        return handler


@pytest.mark.parametrize(
    "target",
    [
        "omnigent.read_event_batch",
        "omnigent.observe_snapshot",
        "omnigent.harvest_evidence",
        "omnigent.publish_workspace",
    ],
)
@pytest.mark.parametrize("mode", ["timeout", "lost_ack", "process_loss"])
async def test_registered_session_crosses_deadline_without_new_allowance(
    target, mode, monkeypatch
):
    monkeypatch.setattr(module, "SNAPSHOT_INTERVAL_SECONDS", 1)
    monkeypatch.setattr(
        module.workflow, "upsert_search_attributes", lambda _attributes: None
    )
    async with await WorkflowEnvironment.start_time_skipping() as env:
        now = await env.get_current_time()
        budget = ExecutionBudgetContext(
            budget=resolve_execution_budget(
                timeout_policy={
                    "timeout_seconds": 3,
                    "max_timeout_seconds": 3,
                    "execution_budget_mode": "flat",
                }
            ),
            logicalStepId="logical-step",
            attemptId="attempt",
            admittedAt=now,
        )
        state = SessionActivities(target, mode, now)
        groups = defaultdict(list)
        for name in (
            "omnigent.load_reconciliation_inputs",
            "omnigent.persist_decision",
            "omnigent.heartbeat_host_lease",
            "omnigent.read_event_batch",
            "omnigent.observe_snapshot",
            "omnigent.record_terminal",
            "omnigent.harvest_evidence",
            "omnigent.publish_workspace",
        ):
            groups[
                module.DEFAULT_ACTIVITY_CATALOG.resolve_activity(name).task_queue
            ].append(state.registered(name))
        async with AsyncExitStack() as stack:
            for queue, activities in groups.items():
                await stack.enter_async_context(
                    Worker(env.client, task_queue=queue, activities=activities)
                )
            queue = f"budget-{mode}-{target.rsplit('.', 1)[-1]}"

            def worker():
                return Worker(
                    env.client,
                    task_queue=queue,
                    workflows=[module.MoonMindOmnigentSessionWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                    max_cached_workflows=0,
                )

            owner = await stack.enter_async_context(worker())
            handle = await env.client.start_workflow(
                module.MoonMindOmnigentSessionWorkflow.run,
                OmnigentSessionWorkflowInput(
                    sessionId="session",
                    compiledExecutionIntentRef="art_intent",
                    compiledExecutionIntentDigest="sha256:" + "a" * 64,
                    workflowId="workflow",
                    stepExecutionId="step",
                    agentRunId="agent",
                    initialTurnAttemptId="turn",
                    executionBudget=budget,
                ),
                id=queue,
                task_queue=queue,
            )
            await asyncio.wait_for(state.blocked.wait(), timeout=10)
            if mode == "process_loss":
                await owner.shutdown()
                await stack.enter_async_context(worker())
            await env.sleep(5)
            state.release.set()
            result = await asyncio.wait_for(handle.result(), timeout=30)
            history = await handle.fetch_history()
        assert isinstance(result, AgentRunResult)
        assert result.summary == "saved", {
            "calls": dict(state.calls),
            "decisions": state.decisions,
            "metadata": result.metadata,
        }
        assert state.effects["omnigent.harvest_evidence"] == 1
        assert state.effects["omnigent.publish_workspace"] == 1
        # A lost acknowledgement follows a confirmed save, so observation must
        # recover it without another call. The other injected failures occur
        # before that effect and may safely retry the save.
        if target == "omnigent.publish_workspace" and mode == "lost_ack":
            assert state.calls["omnigent.publish_workspace"] == 1
        assert state.effects["omnigent.record_terminal"] <= 1
        assert state.decision_writes
        assert all(count == 1 for count in state.decision_writes.values())
        contexts = [
            ExecutionBudgetContext.model_validate(value) for value in state.budgets
        ]
        assert {value.attempt_id for value in contexts} == {"attempt"}
        assert all(
            value.budget == budget.budget and value.admitted_at == now
            for value in contexts
        )
        finishing_deadlines = {
            value.finishing_deadline
            for value in contexts
            if value.finishing_deadline is not None
        }
        assert len(finishing_deadlines) == 1
        # Inspect real Temporal command attributes, including retries. Every
        # execution command ends by the admitted deadline; later commands use
        # the same explicitly bounded finishing deadline.
        workflow_clock = now
        for event in history.events:
            if event.HasField("workflow_task_started_event_attributes"):
                # workflow.now() is the activation's deterministic timestamp;
                # command storage also includes network/worker processing time.
                workflow_clock = event.event_time.ToDatetime(tzinfo=now.tzinfo)
            if not event.HasField("activity_task_scheduled_event_attributes"):
                continue
            attrs = event.activity_task_scheduled_event_attributes
            allowance = attrs.schedule_to_close_timeout.ToTimedelta()
            deadline = (
                next(iter(finishing_deadlines))
                if workflow_clock >= now + timedelta(seconds=3)
                else now + timedelta(seconds=3)
            )
            assert workflow_clock + allowance <= deadline + timedelta(milliseconds=1)
            assert attrs.activity_type.name not in {
                "omnigent.submit_turn",
                "omnigent.ensure_host",
            }
