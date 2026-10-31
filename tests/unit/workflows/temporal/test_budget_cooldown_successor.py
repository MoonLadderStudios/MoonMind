"""Real parent/AgentRun recovery with cooldown and worker replacement (#4986)."""

import asyncio
import json
from collections import defaultdict
from contextlib import AsyncExitStack
from datetime import datetime

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.workflows import agent_run
from moonmind.workflows.temporal.workflows import run as parent_module
from tests.integration.services.temporal.workflows import test_agent_run as harness
from tests.unit.workflows.temporal.workflows.test_run_integration import (
    _mock_plan_payload,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.temporal_boundary]
LAUNCHES = []
QUOTA_SEEN = None
COOLDOWN_SECONDS = 3
REAL_PATCHED = workflow.patched


@activity.defn(name="agent_runtime.launch")
async def launch(payload: dict) -> dict:
    LAUNCHES.append(payload)
    return {"container_id": f"process-{len(LAUNCHES)}", "status": "running"}


@activity.defn(name="agent_runtime.status")
async def status(payload: dict) -> dict:
    return {
        "runId": payload.get("runId") or payload.get("run_id") or "managed-run",
        "agentKind": "managed",
        "agentId": "claude_code",
        "status": "failed" if len(LAUNCHES) < 3 else "completed",
    }


@activity.defn(name="agent_runtime.fetch_result")
async def result(payload: dict) -> dict:
    if len(LAUNCHES) == 1:
        QUOTA_SEEN.set()
        return {
            "summary": "Provider cooldown",
            "failureClass": "integration_error",
            "providerErrorCode": "429",
            "retryRecommendation": "retry_after_cooldown",
            "retryDisposition": "wait",
            "providerFailure": {
                "providerErrorClass": "rate_limit",
                "providerErrorCode": "429",
                "retryRecommendation": "retry_after_cooldown",
                "retryAfterSeconds": COOLDOWN_SECONDS,
                "sanitizedSummary": "Provider cooldown",
            },
        }
    if len(LAUNCHES) == 2:
        return {
            "summary": "Execution worker interrupted",
            "failureClass": "system_error",
            "providerErrorCode": "WORKER_SHUTDOWN",
            "retryRecommendation": "retry_step_execution",
            "retryDisposition": "retry",
        }
    return {
        "summary": "Completed",
        "executionState": "completed",
        "objectiveOutcome": "succeeded",
    }


@activity.defn(name="provider_profile.list")
async def profiles(payload: dict) -> dict:
    # One selected account belongs to one runtime. Reusing the harness profile
    # for every runtime would overwrite the parent's authoritative snapshot.
    if payload.get("runtime_id") != "claude_code":
        return {"profiles": [], "profile_statuses": []}
    return await harness.mock_provider_profile_list(payload)


@workflow.defn(name="BudgetCooldownParent")
class BudgetCooldownParent:
    @workflow.run
    async def run(self, options: dict) -> int:
        owner = parent_module.MoonMindRunWorkflow()
        self.owner = owner
        owner._owner_id = "owner"
        owner._repo = "owner/repo"

        async def checkpoint(*_args, **_kwargs):
            return None

        async def manifest(*_args, **kwargs):
            if (
                options.get("pauseBetweenAttempts")
                and kwargs.get("terminal_disposition") == "retryable"
            ):
                owner._paused = True

        async def bind(request):
            return request

        owner._maybe_bind_workflow_scoped_session = bind
        owner._record_canonical_step_checkpoint = checkpoint
        owner._record_step_execution_manifest = manifest
        try:
            await owner._run_execution_stage(
                parameters={
                    "repository": "owner/repo",
                    "model": "selected-model",
                    "publishMode": "none",
                },
                plan_ref="art_plan",
            )
        except ValueError:
            if not options.get("expectExhaustion"):
                raise
        return owner._step_execution_for("step") or 0

    @workflow.query
    def paused_between_attempts(self) -> bool:
        return self.owner._paused

    @workflow.signal
    def resume(self) -> None:
        self.owner._paused = False


@pytest.mark.timeout(90)
@pytest.mark.parametrize(
    "cooldown_seconds,expected_attempts,pause_seconds",
    [(3, 2, 0), (120, 1, 0), (3, 2, 100)],
    ids=["successor", "cooldown-deadline", "paused-successor"],
)
async def test_cooldown_parent_retry_and_worker_replacement_share_budget_and_intent(
    monkeypatch,
    cooldown_seconds,
    expected_attempts,
    pause_seconds,
):
    global QUOTA_SEEN, COOLDOWN_SECONDS
    QUOTA_SEEN = asyncio.Event()
    COOLDOWN_SECONDS = cooldown_seconds
    LAUNCHES.clear()
    enabled = {
        parent_module.RUN_CONDITIONAL_REGISTRY_READ_PATCH,
        parent_module.RUN_AGENT_RUNTIME_RETRY_CLASSIFICATION_PATCH,
        parent_module.RUN_EXPLICIT_STEP_RETRY_RECOMMENDATION_PATCH,
        parent_module.RUN_LOGICAL_STEP_EXECUTION_BUDGET_PATCH,
    }
    monkeypatch.setattr(
        workflow,
        "patched",
        lambda key: (
            REAL_PATCHED(key) if not key.startswith("run-") or key in enabled else False
        ),
    )
    monkeypatch.setattr(workflow, "upsert_search_attributes", lambda _attributes: None)
    monkeypatch.setattr(workflow, "upsert_memo", lambda _memo: None)

    async def plan(name, _payload, **_kwargs):
        assert name == "artifact.read"
        return _mock_plan_payload(
            [
                {
                    "id": "step",
                    "tool": {"type": "agent_runtime", "name": "claude_code"},
                    "inputs": {
                        "instructions": "Complete the selected work",
                        "agentKind": "managed",
                        "executionProfileRef": "default-managed",
                        "model": "selected-model",
                        "workspaceSpec": {
                            "repository": "owner/repo",
                            "startingBranch": "main",
                        },
                        "timeoutPolicy": {
                            "timeout_seconds": 40,
                            "max_timeout_seconds": 40,
                            "execution_budget_mode": "flat",
                        },
                    },
                }
            ]
        )

    monkeypatch.setattr(parent_module, "execute_typed_activity", plan)
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
    ) as env:
        root_queue = "budget-cooldown-parent"
        child_queue = parent_module.WORKFLOW_TASK_QUEUE

        def root_worker():
            return Worker(
                env.client,
                task_queue=root_queue,
                workflows=[BudgetCooldownParent],
                workflow_runner=UnsandboxedWorkflowRunner(),
                max_cached_workflows=0,
            )

        def child_worker():
            return Worker(
                env.client,
                task_queue=child_queue,
                workflows=[
                    agent_run.MoonMindAgentRun,
                    harness.MockProviderProfileManager,
                ],
                workflow_runner=UnsandboxedWorkflowRunner(),
                max_cached_workflows=0,
            )

        groups = defaultdict(list)
        for name, handler in {
            "provider_profile.list": profiles,
            "provider_profile.ensure_manager": harness.mock_provider_profile_ensure_manager,
            "provider_profile.reset_manager": harness.mock_provider_profile_reset_manager,
            "provider_profile.manager_state": harness.mock_provider_profile_manager_state,
            "agent_runtime.build_launch_context": harness.mock_agent_runtime_build_launch_context,
            "agent_runtime.launch": launch,
            "agent_runtime.status": status,
            "agent_runtime.fetch_result": result,
            "agent_runtime.publish_artifacts": harness.mock_publish_artifacts,
            "agent_runtime.cancel": harness.mock_cancel,
        }.items():
            groups[
                agent_run.DEFAULT_ACTIVITY_CATALOG.resolve_activity(name).task_queue
            ].append(handler)
        async with AsyncExitStack() as stack:
            for queue, handlers in groups.items():
                await stack.enter_async_context(
                    Worker(env.client, task_queue=queue, activities=handlers)
                )
            root = await stack.enter_async_context(root_worker())
            child = await stack.enter_async_context(child_worker())
            manager = await env.client.start_workflow(
                harness.MockProviderProfileManager.run,
                {"runtime_id": "claude_code"},
                id="provider-profile-manager:claude_code",
                task_queue=child_queue,
            )
            handle = await env.client.start_workflow(
                BudgetCooldownParent.run,
                {
                    "expectExhaustion": expected_attempts == 1,
                    "pauseBetweenAttempts": bool(pause_seconds),
                },
                id="budget-cooldown-parent",
                task_queue=root_queue,
            )
            await asyncio.wait_for(QUOTA_SEEN.wait(), timeout=20)
            for _ in range(100):
                if (await manager.query(harness.MockProviderProfileManager.get_state))[
                    "cooldown_reports"
                ]:
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("The production workflow did not select provider cooldown")
            await root.shutdown()
            await child.shutdown()
            await stack.enter_async_context(root_worker())
            await stack.enter_async_context(child_worker())
            if pause_seconds:
                for _ in range(40):
                    if await handle.query(BudgetCooldownParent.paused_between_attempts):
                        break
                    await env.sleep(1)
                else:
                    pytest.fail("The parent did not pause at the successor boundary")
                assert len(LAUNCHES) == 2
                await env.sleep(pause_seconds)
                assert len(LAUNCHES) == 2
                await handle.signal(BudgetCooldownParent.resume)
            try:
                assert (
                    await asyncio.wait_for(handle.result(), timeout=30)
                    == expected_attempts
                )
            except TimeoutError:
                history = await handle.fetch_history()
                children = [
                    event.child_workflow_execution_started_event_attributes.workflow_execution.workflow_id
                    for event in history.events
                    if event.HasField(
                        "child_workflow_execution_started_event_attributes"
                    )
                ]
                traces = {}
                for child_id in children:
                    child_history = await env.client.get_workflow_handle(
                        child_id
                    ).fetch_history()
                    traces[child_id] = [
                        event.event_type for event in child_history.events[-12:]
                    ]
                print(
                    json.dumps(
                        {
                            "launches": len(LAUNCHES),
                            "children": traces,
                            "rootEvents": [
                                event.event_type for event in history.events[-12:]
                            ],
                        }
                    ),
                    flush=True,
                )
                raise
        if expected_attempts == 1:
            from moonmind.schemas.agent_runtime_models import (
                AgentRunResult,
                ExecutionBudgetContext,
            )

            assert len(LAUNCHES) == 1
            first_context = LAUNCHES[0]["request"]["executionBudget"]
            raw = await env.client.get_workflow_handle(
                first_context["attemptId"]
            ).result()
            terminal = (
                raw
                if isinstance(raw, AgentRunResult)
                else AgentRunResult.model_validate(raw)
            )
            assert terminal.metadata["reason"] == "timed_out"
            final_budget = ExecutionBudgetContext.model_validate(
                terminal.metadata["executionBudget"]
            )
            assert final_budget.elapsed_seconds(await env.get_current_time()) <= 42
            return
        assert len(LAUNCHES) == 3
        requests = [entry["request"] for entry in LAUNCHES]
        contexts = [entry["executionBudget"] for entry in requests]
        assert contexts[0]["attemptId"] == contexts[1]["attemptId"]
        assert contexts[2]["attemptId"] != contexts[0]["attemptId"]
        assert contexts[2]["logicalStepId"] == contexts[0]["logicalStepId"] == "step"
        assert contexts[2]["budget"] == contexts[0]["budget"]
        assert contexts[2]["consumedSeconds"] >= 3
        assert contexts[2]["recoveryCount"] == 1
        if pause_seconds:
            wall_seconds = (
                datetime.fromisoformat(contexts[2]["admittedAt"])
                - datetime.fromisoformat(contexts[0]["admittedAt"])
            ).total_seconds()
            assert wall_seconds - contexts[2]["consumedSeconds"] >= pause_seconds
        assert all(
            entry["agentId"] == "claude_code"
            and entry["executionProfileRef"] == "default-managed"
            for entry in requests
        )
        assert all(
            entry["parameters"]["publishMode"] == "none"
            and entry["parameters"]["repository"] == "owner/repo"
            for entry in requests
        )
        assert all(
            entry["parameters"]["model"] == "selected-model" for entry in requests
        )
