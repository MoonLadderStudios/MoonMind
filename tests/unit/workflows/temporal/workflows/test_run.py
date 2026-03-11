import unittest
from typing import Any, Dict

import pytest

pytest.importorskip("temporalio")

from temporalio import activity, client, exceptions
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest
from temporalio.common import (
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
)
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.activity_catalog import (
    INTEGRATIONS_TASK_QUEUE,
    LLM_TASK_QUEUE,
    SANDBOX_TASK_QUEUE,
)
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

PLAN_GENERATE_CALLS: list[Dict[str, Any]] = []
SANDBOX_COMMAND_CALLS: list[Dict[str, Any]] = []
INTEGRATION_START_CALLS: list[Dict[str, Any]] = []
INTEGRATION_STATUS_CALLS: list[Dict[str, Any]] = []


def _trusted_search_attributes() -> TypedSearchAttributes:
    return TypedSearchAttributes(
        [
            SearchAttributePair(
                SearchAttributeKey.for_keyword("mm_owner_id"),
                "trusted-owner",
            ),
            SearchAttributePair(
                SearchAttributeKey.for_keyword("mm_owner_type"),
                "user",
            ),
        ]
    )


async def _register_test_search_attributes(
    env: WorkflowEnvironment,
) -> None:
    await env.client.operator_service.add_search_attributes(
        AddSearchAttributesRequest(
            namespace=env.client.namespace,
            search_attributes={
                "mm_owner_id": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_owner_type": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_state": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_entry": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_updated_at": IndexedValueType.INDEXED_VALUE_TYPE_DATETIME,
                "mm_repo": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_integration": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
            },
        )
    )


async def _wait_for_condition(
    predicate,
    *,
    timeout_seconds: float = 5.0,
    poll_interval_seconds: float = 0.05,
) -> None:
    import asyncio

    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("Timed out waiting for test condition")
        await asyncio.sleep(poll_interval_seconds)


@activity.defn(name="plan.generate")
async def mock_plan_generate(args: Dict[str, Any]) -> Dict[str, Any]:
    PLAN_GENERATE_CALLS.append(args)
    return {"plan_ref": "artifact://plan/123"}


@activity.defn(name="sandbox.run_command")
async def mock_sandbox_command(args: Dict[str, Any]) -> Dict[str, Any]:
    SANDBOX_COMMAND_CALLS.append(args)
    return {"exit_code": 0, "stdout": "executing", "stderr": ""}


@activity.defn(name="integration.jules.start")
async def mock_integration_start(args: Dict[str, Any]) -> Dict[str, Any]:
    INTEGRATION_START_CALLS.append(args)
    return {"correlation_id": "corr-123"}


@activity.defn(name="integration.jules.status")
async def mock_integration_status(args: Dict[str, Any]) -> Dict[str, Any]:
    INTEGRATION_STATUS_CALLS.append(args)
    if len(INTEGRATION_STATUS_CALLS) <= 1:
        return {"normalized_status": "running"}
    return {"normalized_status": "succeeded"}


class TestMoonMindRunWorkflow(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        PLAN_GENERATE_CALLS.clear()
        SANDBOX_COMMAND_CALLS.clear()
        INTEGRATION_START_CALLS.clear()
        INTEGRATION_STATUS_CALLS.clear()

    async def test_moonmind_run_workflow(self) -> None:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            await _register_test_search_attributes(env)
            async with (
                Worker(
                    env.client,
                    task_queue=LLM_TASK_QUEUE,
                    activities=[mock_plan_generate],
                ),
                Worker(
                    env.client,
                    task_queue=SANDBOX_TASK_QUEUE,
                    activities=[mock_sandbox_command],
                ),
                Worker(
                    env.client,
                    task_queue=INTEGRATIONS_TASK_QUEUE,
                    activities=[mock_integration_start, mock_integration_status],
                ),
                Worker(
                    env.client,
                    task_queue="test-task-queue",
                    workflows=[MoonMindRunWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ),
            ):
                request = {
                    "workflowType": "MoonMind.Run",
                    "title": "Test Run",
                    "initialParameters": {
                        "repo": "moonladder/moonmind",
                        "integration": "jules",
                    },
                }

                # Start workflow
                handle = await env.client.start_workflow(
                    MoonMindRunWorkflow.run,
                    request,
                    id="test-workflow-id",
                    task_queue="test-task-queue",
                    memo={"title": "Trusted title"},
                    search_attributes=_trusted_search_attributes(),
                )

                result = await handle.result()
                self.assertEqual(result["status"], "success")
                self.assertEqual(PLAN_GENERATE_CALLS[0]["principal"], "trusted-owner")
                self.assertEqual(SANDBOX_COMMAND_CALLS[0]["principal"], "trusted-owner")
                self.assertEqual(
                    INTEGRATION_START_CALLS[0]["principal"], "trusted-owner"
                )

    async def test_moonmind_run_workflow_ignores_untrusted_owner_payload(self) -> None:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            await _register_test_search_attributes(env)
            async with (
                Worker(
                    env.client,
                    task_queue=LLM_TASK_QUEUE,
                    activities=[mock_plan_generate],
                ),
                Worker(
                    env.client,
                    task_queue=SANDBOX_TASK_QUEUE,
                    activities=[mock_sandbox_command],
                ),
                Worker(
                    env.client,
                    task_queue="test-task-queue",
                    workflows=[MoonMindRunWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ),
            ):
                result = await env.client.execute_workflow(
                    MoonMindRunWorkflow.run,
                    {
                        "workflowType": "MoonMind.Run",
                        "ownerId": "malicious-owner",
                        "ownerType": "system",
                    },
                    id="test-workflow-id-trusted-owner",
                    task_queue="test-task-queue",
                    search_attributes=_trusted_search_attributes(),
                )

        self.assertEqual(result["status"], "success")
        self.assertEqual(PLAN_GENERATE_CALLS[0]["principal"], "trusted-owner")
        self.assertEqual(SANDBOX_COMMAND_CALLS[0]["principal"], "trusted-owner")

    async def test_moonmind_run_workflow_validation_error(self) -> None:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client,
                task_queue="test-task-queue",
                workflows=[MoonMindRunWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                request = {
                    "title": "Test Run",
                    # Missing workflowType
                }

                with self.assertRaises(client.WorkflowFailureError) as exc_info:
                    await env.client.execute_workflow(
                        MoonMindRunWorkflow.run,
                        request,
                        id="test-workflow-id-error",
                        task_queue="test-task-queue",
                    )
                self.assertIsInstance(
                    exc_info.exception.cause, exceptions.ApplicationError
                )
                self.assertEqual(
                    "workflowType is required",
                    exc_info.exception.cause.message,
                )

    async def test_moonmind_run_workflow_requires_trusted_owner_metadata(self) -> None:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client,
                task_queue="test-task-queue",
                workflows=[MoonMindRunWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                with self.assertRaises(client.WorkflowFailureError) as exc_info:
                    await env.client.execute_workflow(
                        MoonMindRunWorkflow.run,
                        {"workflowType": "MoonMind.Run"},
                        id="test-workflow-id-missing-owner",
                        task_queue="test-task-queue",
                    )
                self.assertIsInstance(
                    exc_info.exception.cause, exceptions.ApplicationError
                )
                self.assertEqual(
                    "Trusted owner metadata is required in Temporal search attributes",
                    exc_info.exception.cause.message,
                )
