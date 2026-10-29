"""MoonMind.Run Step retries follow the attempt's budget and recovery owner.

MoonLadderStudios/MoonMind#4986. A Step retry charges what the AgentRun
attempt recorded as execution, not its cooldown, capacity or pause waits, asks
the same recovery owner, and never re-attempts what that owner stopped. Runs on
a real registered Temporal workflow, and every recorded history replays on a
fresh worker, which is what a worker restart does.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.activity_catalog import WORKFLOW_TASK_QUEUE
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.run import (
    RUN_AGENT_RUNTIME_RETRY_CLASSIFICATION_PATCH,
    RUN_CONDITIONAL_REGISTRY_READ_PATCH,
    RUN_EXPLICIT_STEP_RETRY_RECOMMENDATION_PATCH,
    RUN_INTERRUPTED_STEP_DEADLINE_PATCH,
    RUN_STEP_BUDGET_OWNER_PATCH,
    MoonMindRunWorkflow,
)
from tests.unit.workflows.temporal.workflows.test_agent_run_step_budget_owner import (
    _RATE_LIMITED,
    _managed_request,
    run_agent_run_on_temporal,
)
from tests.unit.workflows.temporal.workflows.test_run_integration import (
    _immediate_wait_condition,
    _mock_plan_payload,
)

_STEP = "agent-turn"
_STEP_CEILING = 7200
_LEGACY_PATCHES = frozenset(
    {
        RUN_CONDITIONAL_REGISTRY_READ_PATCH,
        RUN_AGENT_RUNTIME_RETRY_CLASSIFICATION_PATCH,
        RUN_EXPLICIT_STEP_RETRY_RECOMMENDATION_PATCH,
        RUN_INTERRUPTED_STEP_DEADLINE_PATCH,
        "run-typed-agent-outcomes-v1",
    }
)
_CURRENT_PATCHES = _LEGACY_PATCHES | {RUN_STEP_BUDGET_OWNER_PATCH}
_REAL_PATCHED = workflow.patched
_HOST_LOST = {
    "summary": "Managed host was replaced while the attempt was running.",
    "failureClass": "system_error",
    "providerErrorCode": "MANAGED_HOST_LOST",
    "retryRecommendation": "retry_step_execution",
}

# Scripted first attempt: how long it occupied the parent, and its result.
_FIRST_ATTEMPT: dict[str, Any] = {}
_CHILD_REQUESTS: list[AgentExecutionRequest] = []
_MANIFEST_BUDGETS: list[dict[str, Any]] = []


@workflow.defn(name="MoonMind.AgentRun")
class _RecordedAgentRun:
    """Replays a real AgentRun attempt's duration and recorded result."""

    @workflow.run
    async def run(self, request: AgentExecutionRequest) -> AgentRunResult:
        _CHILD_REQUESTS.append(request)
        if request.step_execution.execution_ordinal == 1:
            await workflow.sleep(timedelta(seconds=_FIRST_ATTEMPT["wallClockSeconds"]))
            return AgentRunResult.model_validate(_FIRST_ATTEMPT["result"])
        return AgentRunResult(summary="Completed on the Step retry.")


async def _plan(activity_type: str, _payload: Any, **_kwargs: Any) -> bytes:
    assert activity_type == "artifact.read"
    return _mock_plan_payload(
        [
            {
                "id": _STEP,
                "tool": {"type": "agent_runtime", "name": "omnigent"},
                "inputs": {
                    "instructions": "Implement the change.",
                    "runtime": {"mode": "omnigent", "model": "claude-opus-5-5"},
                    "timeoutPolicy": {
                        "timeout_seconds": 3600,
                        "max_timeout_seconds": _STEP_CEILING,
                    },
                },
            }
        ]
    )


@workflow.defn(name="StepBudgetOwnerFixture")
class _StepBudgetOwnerFixture:
    @workflow.run
    async def run(self) -> list[dict[str, Any]]:
        run = MoonMindRunWorkflow()
        run._owner_id = "owner-1"
        run._repo = "org/repo"

        async def bind_session(request):
            return request

        async def no_checkpoint(*_args, **_kwargs):
            return None

        async def record_manifest(_logical_step_id, **kwargs):
            if kwargs.get("budget"):
                _MANIFEST_BUDGETS.append(dict(kwargs["budget"]))
            return None

        run._maybe_bind_workflow_scoped_session = bind_session
        run._record_canonical_step_checkpoint = no_checkpoint
        run._record_step_execution_manifest = record_manifest
        try:
            await run._run_execution_stage(
                parameters={"publishMode": "pr", "repository": "org/repo"},
                plan_ref="plan-ref",
            )
        except ValueError:
            pass  # the Step failed; its decisions are the evidence
        return list(run._step_recovery_decisions.get(_STEP, []))


def _patch_environment(monkeypatch: pytest.MonkeyPatch, patches: frozenset[str]):
    monkeypatch.setattr(run_workflow_module, "execute_typed_activity", _plan)
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _m: None)
    monkeypatch.setattr(
        run_workflow_module.workflow, "upsert_search_attributes", lambda _a: None
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "wait_condition", _immediate_wait_condition
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id in patches and _REAL_PATCHED(patch_id),
    )


async def _run_step(monkeypatch, patches: frozenset[str], name: str):
    _patch_environment(monkeypatch, patches)
    _CHILD_REQUESTS.clear()
    _MANIFEST_BUDGETS.clear()
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
    ) as env:
        async with (
            Worker(
                env.client,
                task_queue=WORKFLOW_TASK_QUEUE,
                workflows=[_RecordedAgentRun],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client,
                task_queue=f"{name}-queue",
                workflows=[_StepBudgetOwnerFixture],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
        ):
            handle = await env.client.start_workflow(
                _StepBudgetOwnerFixture.run, id=name, task_queue=f"{name}-queue"
            )
            decisions = await handle.result()
            history = await handle.fetch_history()
    return decisions, history, list(_CHILD_REQUESTS), list(_MANIFEST_BUDGETS)


async def _replay(monkeypatch, *histories) -> None:
    _patch_environment(monkeypatch, _CURRENT_PATCHES)
    replayer = Replayer(
        workflows=[_StepBudgetOwnerFixture],
        workflow_runner=UnsandboxedWorkflowRunner(),
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
    )
    for history in histories:
        await replayer.replay_workflow(history)


async def _real_cooldown_then_host_loss(monkeypatch) -> tuple[AgentRunResult, float]:
    """Run a real AgentRun attempt: provider cooldown, then host loss."""

    launches: list[datetime] = []
    first_segment, second_segment = 50, 30

    @activity.defn(name="agent_runtime.launch")
    async def launch(_request: dict) -> dict:
        launches.append(activity.info().current_attempt_scheduled_time)
        return {"container_id": "c", "status": "running", "agent_id": "claude_code"}

    @activity.defn(name="agent_runtime.status")
    async def status(request: dict) -> dict:
        at = activity.info().current_attempt_scheduled_time
        segment = (at - launches[-1]).total_seconds()
        finished = segment >= (first_segment if len(launches) == 1 else second_segment)
        return {
            "runId": request.get("runId") or "managed-run",
            "agentKind": "managed",
            "agentId": "claude_code",
            "status": "failed" if finished else "running",
            "metadata": {"lastLogOffset": str(segment)},
        }

    @activity.defn(name="agent_runtime.fetch_result")
    async def fetch_result(_request: dict) -> dict:
        return dict(_RATE_LIMITED) if len(launches) == 1 else dict(_HOST_LOST)

    published: list[datetime] = []

    @activity.defn(name="agent_runtime.publish_artifacts")
    async def publish(result: AgentRunResult) -> AgentRunResult:
        published.append(activity.info().current_attempt_scheduled_time)
        return result

    result, _history = await run_agent_run_on_temporal(
        monkeypatch,
        workflow_id="test-step-budget-cooldown-host-loss",
        request=_managed_request(600),
        activities=[launch, status, fetch_result, publish],
    )
    assert isinstance(result, AgentRunResult), result
    assert len(launches) == 2
    wall_clock = (published[-1] - launches[0]).total_seconds()
    return result, wall_clock


@pytest.mark.asyncio
async def test_cooldown_then_step_retry_after_restart_keeps_one_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4: provider cooldown, then a Step retry, then a worker restart."""

    attempt, wall_clock = await _real_cooldown_then_host_loss(monkeypatch)
    consumed = attempt.metadata["consumedExecutionSeconds"]
    assert [
        (item["action"], item["reason"])
        for item in attempt.metadata["recoveryDecisions"]
    ] == [("cooldown", "provider_cooldown"), ("terminal_stop", "no_recovery_proposed")]
    # The cooldown wait made the attempt's wall-clock much longer than the
    # execution it consumed.
    assert wall_clock > consumed + 60

    _FIRST_ATTEMPT.update(
        wallClockSeconds=wall_clock,
        result=attempt.model_dump(by_alias=True, mode="json"),
    )
    decisions, history, requests, budgets = await _run_step(
        monkeypatch, _CURRENT_PATCHES, "step-budget-owner-current"
    )

    assert [r.step_execution.execution_ordinal for r in requests] == [1, 2]
    # One Step retry decision, made by the recovery owner, charging the
    # execution the attempt recorded rather than its cooldown wait.
    assert [(d["action"], d["reason"]) for d in decisions] == [
        ("successor", "step_retry")
    ]
    assert decisions[0]["consumedExecutionSeconds"] == consumed
    assert budgets == [
        {
            "stepExecutionBudget": {
                "maxSeconds": _STEP_CEILING,
                "consumedExecutionSeconds": consumed,
                "recoveryDecision": decisions[0],
            }
        }
    ]
    successor_ceiling = requests[1].timeout_policy["max_timeout_seconds"]
    assert successor_ceiling == _STEP_CEILING - consumed
    # Recovery preserves the user-selected intent.
    first, retry = requests
    # Each Omnigent attempt gets its own sandbox; what it was asked to do,
    # with which runtime and model, against which repository and publication
    # mode, is unchanged.
    for field in ("agent_id", "agent_kind", "execution_profile_ref"):
        assert getattr(retry, field) == getattr(first, field)

    def intent(request: AgentExecutionRequest) -> dict[str, Any]:
        # Only the per-attempt ledger identity may differ.
        return {k: v for k, v in request.parameters.items() if k != "metadata"}

    assert intent(retry) == intent(first)
    assert intent(first)["model"] == "claude-opus-5-5"
    assert "org/repo" in repr(intent(first)) and "'pr'" in repr(intent(first))

    # A worker restart replays the same decisions and budgets.
    await _replay(monkeypatch, history)


@pytest.mark.asyncio
async def test_step_retry_honors_the_attempt_owner_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parent never re-attempts what the attempt's owner refused."""

    _FIRST_ATTEMPT.update(
        wallClockSeconds=600,
        result={
            **_HOST_LOST,
            "metadata": {
                "consumedExecutionSeconds": 600,
                "recoveryDecisions": [
                    {
                        "action": "terminal_stop",
                        "reason": "execution_budget_exhausted",
                        "consumedExecutionSeconds": 600,
                    }
                ],
            },
        },
    )

    decisions, history, requests, _budgets = await _run_step(
        monkeypatch, _CURRENT_PATCHES, "step-budget-owner-binding-stop"
    )

    assert len(requests) == 1
    assert [(d["action"], d["reason"]) for d in decisions] == [
        ("terminal_stop", "execution_budget_exhausted")
    ]
    await _replay(monkeypatch, history)


@pytest.mark.asyncio
async def test_histories_before_the_owner_keep_their_wall_clock_charge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC5: a history recorded before the patch replays its recorded charge."""

    _FIRST_ATTEMPT.update(
        wallClockSeconds=1800,
        result={**_HOST_LOST, "metadata": {"consumedExecutionSeconds": 120}},
    )

    legacy_decisions, legacy_history, legacy, _ = await _run_step(
        monkeypatch, _LEGACY_PATCHES, "step-budget-owner-legacy"
    )
    current_decisions, current_history, current, _ = await _run_step(
        monkeypatch, _CURRENT_PATCHES, "step-budget-owner-recorded"
    )

    assert legacy_decisions == []
    assert 5390 <= legacy[1].timeout_policy["max_timeout_seconds"] <= 5400
    assert current[1].timeout_policy["max_timeout_seconds"] == _STEP_CEILING - 120
    assert [(d["action"], d["reason"]) for d in current_decisions] == [
        ("successor", "step_retry")
    ]
    await _replay(monkeypatch, legacy_history, current_history)
