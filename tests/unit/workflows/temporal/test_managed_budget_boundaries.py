"""Managed AgentRun faults through production Activities, adapter and stores."""

import asyncio
import json
from collections import Counter, defaultdict
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from api_service.db.models import Base
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    ExecutionBudgetContext,
    ManagedRunRecord,
    resolve_execution_budget,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
    _bind_activity_handler,
)
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.workflows import agent_run
from tests.integration.services.temporal.workflows import test_agent_run as harness

pytestmark = [pytest.mark.asyncio, pytest.mark.temporal_boundary]


class ManagedActivities:
    """Inject transport faults around the actual managed side-effect owners.

    Only launching a provider process and account admission are simulated. Run
    identity comes from ManagedAgentAdapter; status, cancellation, result reads
    and artifact writes use production implementations and durable storage.
    """

    def __init__(self, root, service, *, target=None, mode=None, accounting=None):
        self.root, self.service = root, service
        self.restart()
        self.target, self.mode, self.accounting = target, mode, accounting
        self.calls = Counter()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.launches = []
        self.cancellation_seen = False

    def restart(self):
        root, service = self.root, self.service
        self.store = ManagedRunStore(root / "runs")
        self.runtime = TemporalAgentRuntimeActivities(
            artifact_service=service,
            run_store=self.store,
            workspace_root=root,
        )

    @activity.defn(name="agent_runtime.launch")
    async def launch(self, payload: dict) -> dict:
        self.launches.append(payload)
        running = self.target in {"cancel", "cancel_status"} or self.accounting
        record = ManagedRunRecord(
            runId=payload["run_id"],
            workflowId=payload["workflow_id"],
            agentId="claude_code",
            runtimeId="claude_code",
            status="running" if running else "completed",
            startedAt=datetime.now(UTC),
            errorMessage=None if running else "Confirmed managed completion",
        )
        self.store.save(record)
        return record.model_dump(mode="json", by_alias=True)

    def registered(self, name):
        method = getattr(TemporalAgentRuntimeActivities, name.replace(".", "_"))

        @activity.defn(name=name)
        async def handler(payload: dict) -> dict:
            self.calls[name] += 1
            if name == "agent_runtime.status" and self.accounting:
                poll = self.calls[name]
                if self.accounting == "progress_extension" and poll == 2:
                    self.store.update_status(
                        payload["runId"], "running", last_log_offset=1
                    )
                if poll == (5 if self.accounting == "progress_extension" else 3):
                    self.store.update_status(
                        payload["runId"],
                        "completed",
                        exit_code=0,
                        error_message="Confirmed managed completion",
                    )
            target = (
                "cancel_status"
                if name == "agent_runtime.status" and self.cancellation_seen
                else name.removeprefix("agent_runtime.")
            )
            inject = target == self.target and not self.release.is_set()
            if inject:
                self.blocked_info = activity.info()
            if inject and self.mode != "lost_ack":
                self.started.set()
                await self.release.wait()
                raise RuntimeError("Injected Activity interruption before its effect")
            real = _bind_activity_handler(self.runtime, func=method, activity_type=name)
            result = await real(payload)
            if name == "agent_runtime.cancel":
                self.cancellation_seen = True
            if inject and self.mode == "lost_ack":
                # The production effect is already committed, but its Activity
                # acknowledgement will arrive after the admitted deadline.
                self.started.set()
                await self.release.wait()
            return result.model_dump(mode="json", by_alias=True)

        return handler


@asynccontextmanager
async def managed_runtime(tmp_path, monkeypatch, **options):
    def policy(request, **_options):
        return {
            "runtime": request.agent_id,
            "noProgressTimeoutSeconds": (
                20 if options.get("accounting") == "progress_extension" else 1
            ),
            "noProgressGraceSeconds": 1,
            "stuckAction": "request_intervention",
        }

    monkeypatch.setattr(
        agent_run.MoonMindAgentRun,
        "_resiliency_policy_for_request",
        staticmethod(policy),
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/artifacts.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "blobs"),
            )
            state = ManagedActivities(tmp_path, service, **options)
            async with await WorkflowEnvironment.start_time_skipping(
                data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
            ) as env, AsyncExitStack() as stack:
                queue = "managed-budget-" + uuid4().hex
                await stack.enter_async_context(
                    Worker(
                        env.client,
                        task_queue=queue + "-profiles",
                        workflows=[harness.MockProviderProfileManager],
                        workflow_runner=UnsandboxedWorkflowRunner(),
                    )
                )
                manager = await env.client.start_workflow(
                    harness.MockProviderProfileManager.run,
                    {"runtime_id": "claude_code"},
                    id="provider-profile-manager:claude_code",
                    task_queue=queue + "-profiles",
                )
                groups = defaultdict(list)
                for name, handler in {
                    "provider_profile.list": harness.mock_provider_profile_list,
                    "provider_profile.ensure_manager": harness.mock_provider_profile_ensure_manager,
                    "provider_profile.reset_manager": harness.mock_provider_profile_reset_manager,
                    "provider_profile.manager_state": harness.mock_provider_profile_manager_state,
                    "agent_runtime.build_launch_context": harness.mock_agent_runtime_build_launch_context,
                    "agent_runtime.launch": state.launch,
                    **{
                        name: state.registered(name)
                        for name in (
                            "agent_runtime.status",
                            "agent_runtime.fetch_result",
                            "agent_runtime.cancel",
                            "agent_runtime.publish_artifacts",
                        )
                    },
                }.items():
                    groups[
                        agent_run.DEFAULT_ACTIVITY_CATALOG.resolve_activity(
                            name
                        ).task_queue
                    ].append(handler)

                def workers(*, workflows=True, activities=True):
                    result = []
                    if workflows:
                        result.append(
                            Worker(
                                env.client,
                                task_queue=queue,
                                workflows=[agent_run.MoonMindAgentRun],
                                workflow_runner=UnsandboxedWorkflowRunner(),
                                max_cached_workflows=0,
                            )
                        )
                    if activities:
                        result.extend(
                            Worker(env.client, task_queue=q, activities=handlers)
                            for q, handlers in groups.items()
                        )
                    return result

                active = workers()
                for worker in active:
                    await stack.enter_async_context(worker)
                try:
                    yield env, state, service, queue, manager, active, workers, stack
                finally:
                    state.release.set()
    finally:
        await engine.dispose()


@pytest.mark.timeout(90)
@pytest.mark.parametrize(
    "target", ["status", "fetch_result", "cancel", "cancel_status", "publish_artifacts"]
)
@pytest.mark.parametrize("mode", ["timeout", "lost_ack", "process_loss"])
async def test_managed_faults_preserve_the_admitted_boundary(
    tmp_path, monkeypatch, target, mode
):
    async with managed_runtime(
        tmp_path, monkeypatch, target=target, mode=mode
    ) as runtime:
        env, state, service, queue, _manager, active, workers, stack = runtime
        now = await env.get_current_time()
        budget = ExecutionBudgetContext(
            budget=resolve_execution_budget(
                timeout_policy={
                    "timeout_seconds": 40,
                    "max_timeout_seconds": 40,
                    "execution_budget_mode": "flat",
                }
            ),
            logicalStepId=queue + ":step",
            attemptId=queue + ":previous",
            admittedAt=now - timedelta(seconds=3),
        ).successor(now=now, attempt_id=queue + ":attempt")
        handle = await env.client.start_workflow(
            agent_run.MoonMindAgentRun.run,
            AgentExecutionRequest(
                agentKind="managed",
                agentId="claude_code",
                executionProfileRef="default-managed",
                correlationId=queue,
                idempotencyKey=queue + ":execute",
                executionBudget=budget,
                parameters={
                    "publishMode": "none",
                    "model": "selected-model",
                    "repository": "owner/repo",
                },
            ),
            id=queue,
            task_queue=queue,
        )
        # handle.result enables time skipping while the workflow reaches the
        # selected boundary; an active Activity keeps it there until env.sleep.
        result_task = asyncio.create_task(handle.result())
        await asyncio.wait_for(state.started.wait(), timeout=20)
        if mode == "process_loss":
            # Replace the real workflow worker before advancing across expiry.
            # A fresh worker replays the admitted budget from server history.
            await asyncio.wait_for(active[0].shutdown(), timeout=10)
            await stack.enter_async_context(workers(activities=False)[0])
        boundary_deadline = (
            state.blocked_info.scheduled_time
            + state.blocked_info.schedule_to_close_timeout
        )
        await env.sleep(
            max(0, (boundary_deadline - await env.get_current_time()).total_seconds())
            + 2
        )
        state.release.set()
        if mode == "process_loss":
            # Stop the Activity workers while the reply is still unavailable.
            for worker in active[1:]:
                await asyncio.wait_for(worker.shutdown(), timeout=10)
            state.restart()
            for worker in workers(workflows=False):
                await stack.enter_async_context(worker)
        result = await asyncio.wait_for(result_task, timeout=20)
        history = await handle.fetch_history()
        completed_at = next(
            event.event_time.ToDatetime(tzinfo=UTC)
            for event in history.events
            if event.HasField("workflow_execution_completed_event_attributes")
        )
        assert len(state.launches) == 1
        launched = state.launches[0]["request"]
        assert launched["executionBudget"]["attemptId"] == budget.attempt_id
        assert launched["executionBudget"]["logicalStepId"] == budget.logical_step_id
        assert launched["parameters"]["model"] == "selected-model"
        assert launched["parameters"]["repository"] == "owner/repo"
        assert launched["parameters"]["publishMode"] == "none"
        assert (
            state.calls[
                "agent_runtime." + ("status" if target == "cancel_status" else target)
            ]
            >= 1
        )
        assert state.calls["agent_runtime.publish_artifacts"] == 1
        assert (
            result.metadata.get("reason") == "timed_out"
            or target == "publish_artifacts"
        )
        if target == "publish_artifacts":
            assert result.summary == "Confirmed managed completion"
            assert result.failure_class is None
            assert result.metadata["terminalSaveIncomplete"] is True
        saved_budget = ExecutionBudgetContext.model_validate(
            result.metadata["executionBudget"]
        )
        assert saved_budget.logical_step_id == budget.logical_step_id
        assert saved_budget.attempt_id == budget.attempt_id
        assert saved_budget.recovery_count == budget.recovery_count
        assert saved_budget.consumed_seconds == 3
        if target != "publish_artifacts":
            assert saved_budget.finishing_deadline is not None
        assert saved_budget.remaining_seconds(completed_at) == 0

        completions = [
            event
            for event in history.events
            if event.HasField("workflow_execution_completed_event_attributes")
        ]
        assert len(completions) == 1  # one recorded terminal recovery decision
        workflow_clock = now
        commands = Counter()
        execution_deadline = saved_budget.admitted_at + timedelta(
            seconds=saved_budget.budget.deadline_seconds(
                saved_budget.last_progress_seconds
            )
            - saved_budget.consumed_seconds
            + saved_budget.excluded_seconds,
        )
        for event in history.events:
            if event.HasField("workflow_task_started_event_attributes"):
                workflow_clock = event.event_time.ToDatetime(tzinfo=UTC)
            if not event.HasField("activity_task_scheduled_event_attributes"):
                continue
            attrs = event.activity_task_scheduled_event_attributes
            if not attrs.activity_type.name.startswith("agent_runtime."):
                continue
            commands[attrs.activity_type.name] += 1
            if attrs.activity_type.name == "agent_runtime.publish_artifacts":
                terminal = json.loads(attrs.input.payloads[0].data)
                assert terminal["summary"] == result.summary
                assert terminal["metadata"][
                    "executionBudget"
                ] == saved_budget.model_dump(mode="json", by_alias=True)
                assert attrs.retry_policy.maximum_attempts == 1
            deadline = (
                saved_budget.finishing_deadline
                if attrs.activity_type.name == "agent_runtime.publish_artifacts"
                and saved_budget.finishing_deadline is not None
                else execution_deadline
            )
            assert (
                workflow_clock + attrs.schedule_to_close_timeout.ToTimedelta()
                <= deadline + timedelta(milliseconds=1)
            )
            assert (
                attrs.start_to_close_timeout.ToTimedelta()
                <= attrs.schedule_to_close_timeout.ToTimedelta()
            )
        assert commands["agent_runtime.launch"] == 1
        assert commands["agent_runtime.fetch_result"] <= 1
        assert commands["agent_runtime.cancel"] <= 1
        assert commands["agent_runtime.publish_artifacts"] == 1

        persisted = await service.list_for_execution(
            namespace="default",
            workflow_id=queue,
            run_id=(await handle.describe()).run_id,
            access=ArtifactAccessContext.for_execution(queue),
            link_type="output.agent_result",
        )
        assert len(persisted) == (
            1 if target != "publish_artifacts" or mode == "lost_ack" else 0
        )
        if persisted:
            _, raw = await service.read(
                artifact_id=persisted[0].artifact_id,
                access=ArtifactAccessContext.for_execution(queue),
            )
            assert json.loads(raw)["summary"] == result.summary
        if target == "cancel" and mode == "lost_ack":
            assert state.store.load(state.launches[0]["run_id"]).status == "canceled"


@pytest.mark.timeout(90)
@pytest.mark.parametrize(
    "accounting", ["capacity_wait", "progress_extension", "expired_successor"]
)
async def test_registered_managed_reconciliation_preserves_time_accounting(
    tmp_path,
    monkeypatch,
    accounting,
):
    async with managed_runtime(tmp_path, monkeypatch, accounting=accounting) as runtime:
        env, state, _service, queue, manager, *_ = runtime
        now = await env.get_current_time()
        policy = {
            "timeout_seconds": 30 if accounting == "progress_extension" else 60,
            "max_timeout_seconds": 90 if accounting == "progress_extension" else 60,
            "progress_stall_seconds": 30,
        }
        if accounting == "capacity_wait":
            await manager.signal(
                "report_cooldown",
                {
                    "profile_id": "default-managed",
                    "cooldown_seconds": 100,
                },
            )
        successor = None
        if accounting == "expired_successor":
            successor = ExecutionBudgetContext(
                budget=resolve_execution_budget(timeout_policy={"timeout_seconds": 10}),
                logicalStepId=queue + ":step",
                attemptId=queue + ":previous",
                admittedAt=now - timedelta(seconds=11),
            ).successor(now=now - timedelta(seconds=2), attempt_id=queue + ":attempt")
        handle = await env.client.start_workflow(
            agent_run.MoonMindAgentRun.run,
            AgentExecutionRequest(
                agentKind="managed",
                agentId="claude_code",
                executionProfileRef="default-managed",
                correlationId=queue,
                idempotencyKey=queue + ":execute",
                timeoutPolicy=policy,
                executionBudget=successor,
                parameters={"publishMode": "none"},
            ),
            id=queue,
            task_queue=queue,
        )
        result = await asyncio.wait_for(handle.result(), timeout=20)
        history = await handle.fetch_history()
        # The server may skip unrelated workflows' timers after this workflow
        # completes. Account against this execution's retained completion event.
        completed_at = next(
            event.event_time.ToDatetime(tzinfo=UTC)
            for event in history.events
            if event.HasField("workflow_execution_completed_event_attributes")
        )
        context = ExecutionBudgetContext.model_validate(
            result.metadata["executionBudget"]
        )
        if accounting == "expired_successor":
            assert state.launches == []
            assert state.calls["agent_runtime.status"] == 0
            assert state.calls["agent_runtime.fetch_result"] == 0
            assert result.metadata["reason"] == "timed_out"
            assert context.consumed_seconds == 9
            assert context.recovery_count == 1
        else:
            assert len(state.launches) == 1
            assert state.calls["agent_runtime.cancel"] == 0
            assert state.calls["agent_runtime.fetch_result"] == 1
            assert result.summary == "Confirmed managed completion"
            assert result.failure_class is None
            if accounting == "capacity_wait":
                # Queueing starts after the manager's cooldown was recorded,
                # so the excluded interval is slightly shorter than 100s.
                assigned = False
                for event in history.events:
                    if event.HasField("workflow_execution_signaled_event_attributes"):
                        assigned |= (
                            event.workflow_execution_signaled_event_attributes.signal_name
                            == "slot_assigned"
                        )
                    if assigned and event.HasField(
                        "workflow_task_started_event_attributes"
                    ):
                        admitted = event.event_time.ToDatetime(tzinfo=UTC)
                        break
                assert assigned
                assert context.excluded_seconds == pytest.approx(
                    (admitted - context.admitted_at).total_seconds(),
                    abs=0.001,
                )
                assert context.excluded_seconds > policy["timeout_seconds"]
                assert context.elapsed_seconds(completed_at) < 30
            else:
                assert context.elapsed_seconds(completed_at) > policy["timeout_seconds"]
                assert context.last_progress_seconds >= 20
