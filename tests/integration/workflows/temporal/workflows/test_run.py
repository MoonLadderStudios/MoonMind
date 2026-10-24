"""UserWorkflow journeys through real Temporal dispatch and terminal projection."""

from __future__ import annotations

import json
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import pytest
from temporalio import activity, client, exceptions, workflow
from temporalio.common import (
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
)
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.config.settings import settings
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.activity_catalog import (
    AGENT_RUNTIME_TASK_QUEUE,
    ARTIFACTS_TASK_QUEUE,
    LLM_TASK_QUEUE,
)
from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow
from tests.integration.workflows.temporal.workflows.test_merge_automation_full_topology import (
    _register_search_attributes,
)
from tests.unit.workflows.temporal.workflows.test_run_integration import (
    _mock_resilience_policy_envelope,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


_CHILD_CASES: dict[str, tuple[dict[str, Any] | None, list[Any]]] = {}


@workflow.defn(name="MoonMind.AgentRun")
class AgentChild:
    @workflow.run
    async def run(self, payload: AgentExecutionRequest) -> dict[str, Any]:
        parent = workflow.info().parent
        assert parent is not None
        result, calls = _CHILD_CASES[parent.workflow_id]
        calls.append(payload)
        return result if result is not None else {"summary": "Tests passed."}


def _plan(tool_type: str = "agent_runtime") -> dict[str, Any]:
    return {
        "plan_version": "1.0",
        "metadata": {
            "title": "Test plan",
            "created_at": "2026-03-12T00:00:00Z",
            "registry_snapshot": {
                "digest": "reg:sha256:" + "a" * 64,
                "artifact_ref": "artifact://registry/legacy",
            },
        },
        "policy": {"failure_mode": "FAIL_FAST"},
        "nodes": [
            {
                "id": "step-1",
                "tool": {"type": tool_type, "name": "claude_code"},
                "inputs": {"instructions": "Run repository tests."},
            }
        ],
    }


@asynccontextmanager
async def _running_user_workflow(
    *,
    plan=None,
    child_result=None,
    request=None,
    workflow_id="test-user-workflow",
    trusted_owner=True,
    context=None,
):
    """Stub external effects, retaining the real registered parent and queues."""
    calls: dict[str, list[Any]] = {
        key: []
        for key in ("plan", "reads", "children", "terminal", "summaries", "skills")
    }
    selected_plan = plan if plan is not None else _plan()
    _CHILD_CASES[workflow_id] = (child_result, calls["children"])

    @activity.defn(name="plan.generate")
    async def generate(payload: dict[str, Any]) -> dict[str, str]:
        calls["plan"].append(payload)
        return {"plan_ref": "artifact://plan/test"}

    @activity.defn(name="artifact.read")
    async def read(payload: dict[str, Any]) -> bytes:
        calls["reads"].append(payload)
        # Unexpected registry reads must fail instead of returning a plan and
        # passing an assertion before dispatch ever reaches its child.
        assert payload["artifact_ref"] == "artifact://plan/test"
        return json.dumps(selected_plan).encode()

    @activity.defn(name="artifact.create")
    async def create(_payload: Any) -> tuple[dict[str, str], dict[str, str]]:
        return {"artifact_id": "art-test-summary"}, {"upload_url": "memory://test"}

    @activity.defn(name="artifact.write_complete")
    async def write(payload: dict[str, Any]) -> dict[str, str]:
        calls["summaries"].append(payload)
        return {"artifact_id": "art-test-summary"}

    @activity.defn(name="resilience.compile_policy")
    async def compile_policy(payload: Any) -> dict[str, Any]:
        return _mock_resilience_policy_envelope(payload)

    @activity.defn(name="execution.record_terminal_state")
    async def terminal(payload: dict[str, Any]) -> dict[str, bool]:
        calls["terminal"].append(payload)
        return {"recorded": True}

    @activity.defn(name="provider_profile.list")
    async def profiles(_payload: Any) -> dict[str, Any]:
        return {"profiles": []}

    @activity.defn(name="agent_skill.resolve")
    async def skills(*payload: Any) -> dict[str, Any]:
        calls["skills"].append(payload)
        return {"manifestRef": "artifact://resolved/test", "skills": []}

    owner = TypedSearchAttributes(
        [
            SearchAttributePair(
                SearchAttributeKey.for_keyword("mm_owner_id"), "trusted-owner"
            ),
            SearchAttributePair(
                SearchAttributeKey.for_keyword("mm_owner_type"), "user"
            ),
        ]
    )
    async with await WorkflowEnvironment.start_time_skipping() as env:
        await _register_search_attributes(env)
        if context is not None:
            context.update(environment=env, client=env.client)
        async with AsyncExitStack() as stack:
            routes = [
                (LLM_TASK_QUEUE, [generate]),
                (
                    ARTIFACTS_TASK_QUEUE,
                    [read, create, write, compile_policy, terminal, profiles],
                ),
                (AGENT_RUNTIME_TASK_QUEUE, [skills]),
            ]
            for queue, activities in routes:
                await stack.enter_async_context(
                    Worker(env.client, task_queue=queue, activities=activities)
                )
            parent_queue = "test-parent"
            child_queue = settings.temporal.user_workflow_v2_task_queue
            for queue in dict.fromkeys([parent_queue, child_queue]):
                await stack.enter_async_context(
                    Worker(
                        env.client,
                        task_queue=queue,
                        workflows=[MoonMindUserWorkflow, AgentChild],
                        workflow_runner=UnsandboxedWorkflowRunner(),
                    )
                )
            handle = await env.client.start_workflow(
                MoonMindUserWorkflow.run,
                (
                    request
                    if request is not None
                    else {"workflowType": "MoonMind.UserWorkflow"}
                ),
                id=workflow_id,
                task_queue=parent_queue,
                search_attributes=owner if trusted_owner else TypedSearchAttributes([]),
            )
            try:
                yield handle, calls
            finally:
                _CHILD_CASES.pop(workflow_id, None)


@pytest.mark.asyncio
async def test_user_workflow_completes_and_projects_queryable_step_ledger() -> None:
    async with _running_user_workflow() as (handle, calls):
        assert (await handle.result())["status"] == "success"
        progress = await handle.query("get_progress")
        ledger = await handle.query("get_step_ledger")
        assert progress["total"] == progress["completed"] == 1
        assert ledger["workflowId"] == handle.id
        assert ledger["runScope"] == "latest"
        assert [(s["logicalStepId"], s["status"]) for s in ledger["steps"]] == [
            ("step-1", "completed")
        ]
        assert len(calls["children"]) == 1
        assert calls["terminal"][-1]["state"] == "completed"
        assert calls["plan"][0]["principal"] == f"workflow:{handle.id}"
        assert calls["reads"][0]["principal"] == f"workflow:{handle.id}"
        await Replayer(
            workflows=[MoonMindUserWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ).replay_workflow(await handle.fetch_history())


@pytest.mark.asyncio
async def test_user_workflow_ignores_untrusted_payload_owner() -> None:
    async with _running_user_workflow(
        request={
            "workflowType": "MoonMind.UserWorkflow",
            "ownerId": "malicious-owner",
            "ownerType": "system",
        }
    ) as (handle, calls):
        assert (await handle.result())["status"] == "success"
        assert calls["plan"][0]["principal"] == f"workflow:{handle.id}"
        assert calls["reads"][0]["principal"] == f"workflow:{handle.id}"
        assert (await handle.describe()).search_attributes["mm_owner_id"] == [
            "trusted-owner"
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "admission,trusted_owner,message",
    [
        ({"title": "Missing type"}, True, "workflowType is required"),
        (
            {"workflowType": "MoonMind.UserWorkflow"},
            False,
            "Trusted owner metadata is required",
        ),
    ],
)
async def test_user_workflow_rejects_invalid_admission(
    admission, trusted_owner, message
) -> None:
    async with _running_user_workflow(
        request=admission, trusted_owner=trusted_owner
    ) as (handle, calls):
        with pytest.raises(client.WorkflowFailureError) as error:
            await handle.result()
        assert isinstance(error.value.cause, exceptions.ApplicationError)
        assert message in error.value.cause.message
        assert calls["children"] == []


@pytest.mark.asyncio
async def test_user_workflow_fail_fast_preserves_child_failure() -> None:
    async with _running_user_workflow(
        child_result={
            "failureClass": "user_error",
            "summary": "Forced agent failure",
        }
    ) as (handle, calls):
        with pytest.raises(client.WorkflowFailureError) as error:
            await handle.result()
        assert "Forced agent failure" in error.value.cause.message
        assert (await handle.describe()).search_attributes["mm_state"] == ["failed"]
        assert calls["terminal"][-1]["state"] == "failed"
        assert len(calls["children"]) == 1


@pytest.mark.asyncio
async def test_pr_publication_without_changes_stays_failed() -> None:
    async with _running_user_workflow(
        request={
            "workflowType": "MoonMind.UserWorkflow",
            "initialParameters": {
                "repo": "MoonLadderStudios/MoonMind",
                "publishMode": "pr",
            },
        },
        child_result={
            "summary": "No changes",
            "metadata": {"push_status": "no_commits", "push_branch": "feature/no-op"},
        },
    ) as (handle, calls):
        with pytest.raises(client.WorkflowFailureError) as error:
            await handle.result()
        assert "no publishable diff" in error.value.cause.message
        assert "feature/no-op" in error.value.cause.message
        assert calls["terminal"][-1]["state"] == "failed"
