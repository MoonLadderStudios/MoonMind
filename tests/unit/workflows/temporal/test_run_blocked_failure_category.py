"""Terminal error category for runs that stop before publication.

A Batch PR Resolver run whose sandbox clone dropped mid-transfer recorded an
``integration_error`` step failure, yet the run closed as ``user_error``
because every stop before publication was labelled that way.
"""

from __future__ import annotations

from typing import Any

import pytest
from temporalio import client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow

_PARAMETERS = {
    "requestType": "task",
    "repository": "MoonLadderStudios/MoonMind",
    "publishMode": "none",
    "targetRuntime": "claude_code",
    "workflow": {
        "instructions": "Resolve the batch.",
        "tool": {"type": "skill", "name": "batch-pr-resolver"},
        "skill": {"name": "batch-pr-resolver"},
        "publish": {"mode": "none"},
    },
}


@pytest.fixture
def recorded_categories(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    categories: list[str | None] = []
    monkeypatch.setattr(
        MoonMindUserWorkflow, "_trusted_owner_metadata", lambda self: ("user", "user-1")
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "upsert_search_attributes", lambda _a: None
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _m: None)

    async def fake_planning_stage(self: MoonMindUserWorkflow, **_kwargs: Any) -> str:
        return "artifact://plan/blocked-category"

    async def fake_finalizing_stage(
        self: MoonMindUserWorkflow, **_kwargs: Any
    ) -> None:
        self._finish_summary = {"finishOutcome": {"code": "FAILED"}}

    async def fake_record_terminal_state(
        self: MoonMindUserWorkflow, *, error_category: str | None = None, **_kwargs: Any
    ) -> None:
        categories.append(error_category)

    monkeypatch.setattr(MoonMindUserWorkflow, "_run_planning_stage", fake_planning_stage)
    monkeypatch.setattr(
        MoonMindUserWorkflow, "_run_finalizing_stage", fake_finalizing_stage
    )
    monkeypatch.setattr(
        MoonMindUserWorkflow, "_record_terminal_state", fake_record_terminal_state
    )
    return categories


async def _run(workflow_id: str) -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-blocked-failure-category",
            workflows=[MoonMindUserWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            with pytest.raises(client.WorkflowFailureError):
                await env.client.execute_workflow(
                    MoonMindUserWorkflow.run,
                    {
                        "workflowType": "MoonMind.UserWorkflow",
                        "initialParameters": _PARAMETERS,
                    },
                    id=workflow_id,
                    task_queue="test-blocked-failure-category",
                )


@pytest.mark.asyncio
async def test_failed_step_stop_keeps_the_step_failure_category(
    recorded_categories: list[str | None], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failed_step(self: MoonMindUserWorkflow, **_kwargs: Any) -> None:
        message = (
            "Provider request failed with provider error OMNIGENT_HOST_LAUNCH_FAILED: "
            "sandbox workspace clone failed: fatal: early EOF"
        )
        self._record_result_failure_diagnostic(
            stage="executing",
            category="integration_error",
            source="child_workflow",
            step_id="node-1",
            step_title="omnigent",
            message=message,
        )
        self._plan_blocked_message = message

    monkeypatch.setattr(MoonMindUserWorkflow, "_run_execution_stage", failed_step)

    await _run("test-blocked-category-step-failure")

    assert recorded_categories == ["integration_error"]


@pytest.mark.asyncio
async def test_gate_stop_without_step_failure_stays_user_error(
    recorded_categories: list[str | None], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def gate_stop(self: MoonMindUserWorkflow, **_kwargs: Any) -> None:
        self._plan_blocked_message = "Structured gate stopped before downstream handoff."

    monkeypatch.setattr(MoonMindUserWorkflow, "_run_execution_stage", gate_stop)

    await _run("test-blocked-category-gate-stop")

    assert recorded_categories == ["user_error"]


def _blocked_workflow(*, capacity_exhausted: bool = False) -> MoonMindUserWorkflow:
    wf = MoonMindUserWorkflow()
    wf._state = "executing"
    wf._record_result_failure_diagnostic(
        stage="executing",
        category="integration_error",
        source="child_workflow",
        step_id="node-1",
        step_title="omnigent",
        message="sandbox workspace clone failed: fatal: early EOF",
    )
    wf._plan_blocked_message = "blocked"
    if capacity_exhausted:
        wf._issue_search_capacity_retry_enabled = True
        wf._publish_context["capacityWait"] = {"exhausted": True}
    return wf


def test_exhausted_capacity_wait_outranks_an_earlier_step_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _p: True)

    wf = _blocked_workflow(capacity_exhausted=True)

    assert wf._blocked_publish_failure_category() == "execution_error"


def test_history_without_the_patch_keeps_the_legacy_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id
        != run_workflow_module.RUN_BLOCKED_STEP_FAILURE_CATEGORY_PATCH,
    )

    assert _blocked_workflow()._blocked_publish_failure_category() == "user_error"
