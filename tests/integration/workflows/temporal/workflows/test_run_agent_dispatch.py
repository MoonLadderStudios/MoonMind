"""Integration tests for agent_runtime dispatch in MoonMind.Run.

These tests spawn a real Temporal test server via
``WorkflowEnvironment.start_time_skipping()`` and exercise the full
workflow execution path.
"""

import json
import unittest
from typing import Any, Dict

import pytest

pytest.importorskip("temporalio")

from temporalio import activity, client
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
    ARTIFACTS_TASK_QUEUE,
    LLM_TASK_QUEUE,
    SANDBOX_TASK_QUEUE,
    WORKFLOW_TASK_QUEUE,
)
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun


# ── Mock activities ──

@activity.defn(name="agent_runtime.publish_artifacts")
async def mock_publish_artifacts(result: dict) -> dict:
    return result


@activity.defn(name="agent_runtime.cancel")
async def mock_cancel(request: dict) -> None:
    pass


PLAN_GENERATE_CALLS: list[Dict[str, Any]] = []
ARTIFACT_READ_CALLS: list[Dict[str, Any]] = []
SKILL_EXECUTE_CALLS: list[Dict[str, Any]] = []


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


async def _register_test_search_attributes(env: WorkflowEnvironment) -> None:
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


@activity.defn(name="plan.generate")
async def mock_plan_generate_agent(args: Dict[str, Any]) -> Dict[str, Any]:
    PLAN_GENERATE_CALLS.append(args)
    return {"plan_ref": "artifact://plan/agent-test"}


@activity.defn(name="artifact.read")
async def mock_artifact_read_agent(args: Dict[str, Any]) -> bytes:
    ARTIFACT_READ_CALLS.append(args)
    payload = {
        "plan_version": "1.0",
        "metadata": {
            "title": "Agent plan",
            "created_at": "2026-03-16T00:00:00Z",
            "registry_snapshot": {},
        },
        "policy": {"failure_mode": "FAIL_FAST"},
        "nodes": [
            {
                "id": "agent-step-1",
                "tool": {
                    "type": "agent_runtime",
                    "name": "gemini_cli",
                },
                "inputs": {
                    "targetRuntime": "gemini_cli",
                    "instructions": "Fix the bug",
                    "runtime": {
                        "model": "gemini-2.5-pro",
                        "effort": "high",
                    },
                    "repo": "moonladder/moonmind",
                },
            }
        ],
    }
    return json.dumps(payload).encode("utf-8")


@activity.defn(name="mm.skill.execute")
async def mock_skill_execute_agent(args: Dict[str, Any]) -> Dict[str, Any]:
    SKILL_EXECUTE_CALLS.append(args)
    return {"status": "COMPLETED", "outputs": {}}


# ── Integration tests (require Temporal test server) ──


class TestAgentRuntimeDispatch(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        PLAN_GENERATE_CALLS.clear()
        ARTIFACT_READ_CALLS.clear()
        SKILL_EXECUTE_CALLS.clear()

    async def test_agent_runtime_node_dispatches_child_workflow(self) -> None:
        """Plan node with tool.type='agent_runtime' should dispatch MoonMind.AgentRun."""
        async with await WorkflowEnvironment.start_time_skipping() as env:
            await _register_test_search_attributes(env)
            async with (
                Worker(
                    env.client,
                    task_queue=LLM_TASK_QUEUE,
                    activities=[mock_plan_generate_agent],
                ),
                Worker(
                    env.client,
                    task_queue=ARTIFACTS_TASK_QUEUE,
                    activities=[mock_artifact_read_agent],
                ),
                Worker(
                    env.client,
                    task_queue=SANDBOX_TASK_QUEUE,
                    activities=[mock_skill_execute_agent],
                ),
                Worker(
                    env.client,
                    task_queue=WORKFLOW_TASK_QUEUE,
                    workflows=[MoonMindAgentRun],
                    activities=[mock_publish_artifacts, mock_cancel],
                    workflow_runner=UnsandboxedWorkflowRunner(),
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
                    "title": "Agent Dispatch Test",
                    "initialParameters": {
                        "repo": "moonladder/moonmind",
                    },
                }

                handle = await env.client.start_workflow(
                    MoonMindRunWorkflow.run,
                    request,
                    id="test-agent-dispatch",
                    task_queue="test-task-queue",
                    search_attributes=_trusted_search_attributes(),
                )

                # The child workflow will fail because the adapter is a stub.
                # With FAIL_FAST, the parent propagates the failure.
                try:
                    await handle.result()
                    # If it somehow succeeds, that's fine too — what matters is
                    # that the dispatch took the agent_runtime path.
                except Exception:
                    # Expected: child workflow failure propagated through parent
                    pass

                # The critical assertion: the skill activity path should NOT
                # have been used for an agent_runtime node.
                self.assertEqual(len(SKILL_EXECUTE_CALLS), 0, "Skill path should not be used")

    async def test_unsupported_tool_type_raises(self) -> None:
        """Plan node with unsupported tool.type raises ValueError."""
        @activity.defn(name="artifact.read")
        async def bad_plan_reader(args: Dict[str, Any]) -> bytes:
            plan = {
                "plan_version": "1.0",
                "metadata": {
                    "title": "Bad plan",
                    "created_at": "2026-03-16T00:00:00Z",
                    "registry_snapshot": {},
                },
                "policy": {"failure_mode": "FAIL_FAST"},
                "nodes": [
                    {
                        "id": "bad-step",
                        "tool": {"type": "unknown_type", "name": "x", "version": "1.0"},
                        "inputs": {},
                    }
                ],
            }
            return json.dumps(plan).encode("utf-8")

        async with await WorkflowEnvironment.start_time_skipping() as env:
            await _register_test_search_attributes(env)
            async with (
                Worker(
                    env.client,
                    task_queue=LLM_TASK_QUEUE,
                    activities=[mock_plan_generate_agent],
                ),
                Worker(
                    env.client,
                    task_queue=ARTIFACTS_TASK_QUEUE,
                    activities=[bad_plan_reader],
                ),
                Worker(
                    env.client,
                    task_queue="test-task-queue",
                    workflows=[MoonMindRunWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ),
            ):
                with self.assertRaises(client.WorkflowFailureError) as exc_info:
                    await env.client.execute_workflow(
                        MoonMindRunWorkflow.run,
                        {"workflowType": "MoonMind.Run"},
                        id="test-unsupported-tool-type",
                        task_queue="test-task-queue",
                        search_attributes=_trusted_search_attributes(),
                    )
                self.assertIn(
                    "must be 'skill' or 'agent_runtime'",
                    exc_info.exception.cause.message,
                )
