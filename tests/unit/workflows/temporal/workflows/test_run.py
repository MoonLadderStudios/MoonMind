import json
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
    ARTIFACTS_TASK_QUEUE,
    INTEGRATIONS_TASK_QUEUE,
    LLM_TASK_QUEUE,
    SANDBOX_TASK_QUEUE,
)
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

PLAN_GENERATE_CALLS: list[Dict[str, Any]] = []
ARTIFACT_READ_CALLS: list[Dict[str, Any]] = []
SKILL_EXECUTE_CALLS: list[Dict[str, Any]] = []
SANDBOX_COMMAND_CALLS: list[Dict[str, Any]] = []
INTEGRATION_START_CALLS: list[Dict[str, Any]] = []
INTEGRATION_STATUS_CALLS: list[Dict[str, Any]] = []
PROPOSAL_GENERATE_CALLS: list[Dict[str, Any]] = []
PROPOSAL_SUBMIT_CALLS: list[Dict[str, Any]] = []


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


@activity.defn(name="artifact.read")
async def mock_artifact_read(args: Dict[str, Any]) -> bytes:
    ARTIFACT_READ_CALLS.append(args)
    artifact_ref = args.get("artifact_ref")
    if artifact_ref == "artifact://registry/123":
        payload = {
            "skills": [
                {
                    "name": "repo.run_tests",
                    "version": "1.0.0",
                    "description": "Run repository test suite",
                    "inputs": {"schema": {"type": "object"}},
                    "outputs": {"schema": {"type": "object"}},
                    "executor": {
                        "activity_type": "mm.skill.execute",
                        "selector": {"mode": "by_capability"},
                    },
                    "requirements": {"capabilities": ["sandbox"]},
                    "policies": {
                        "timeouts": {
                            "start_to_close_seconds": 1800,
                            "schedule_to_close_seconds": 3600,
                        },
                        "retries": {"max_attempts": 3},
                    },
                }
            ]
        }
        return json.dumps(payload).encode("utf-8")
    payload = {
        "plan_version": "1.0",
        "metadata": {
            "title": "Test plan",
            "created_at": "2026-03-12T00:00:00Z",
            "registry_snapshot": {
                "digest": "reg:sha256:" + ("a" * 64),
                "artifact_ref": "artifact://registry/123",
            }
        },
        "policy": {"failure_mode": "FAIL_FAST"},
        "nodes": [
            {
                "id": "step-1",
                "skill": {"name": "repo.run_tests", "version": "1.0.0"},
                "inputs": {"repo_ref": "git:moonmind"},
                "options": {},
            }
        ],
    }
    return json.dumps(payload).encode("utf-8")


@activity.defn(name="mm.skill.execute")
async def mock_skill_execute(args: Dict[str, Any]) -> Dict[str, Any]:
    SKILL_EXECUTE_CALLS.append(args)
    return {"status": "SUCCEEDED", "outputs": {}}


@activity.defn(name="mm.skill.execute")
async def mock_skill_execute_failed(args: Dict[str, Any]) -> Dict[str, Any]:
    SKILL_EXECUTE_CALLS.append(args)
    return {
        "status": "FAILED",
        "outputs": {"error": "forced skill failure"},
        "progress": {"details": "forced skill failure"},
    }


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


@activity.defn(name="proposal.generate")
async def mock_proposal_generate(args: Dict[str, Any]) -> list[Dict[str, Any]]:
    PROPOSAL_GENERATE_CALLS.append(args)
    return [
        {
            "title": "Fix flaky test in module X",
            "summary": "Test xyz is flaky due to race condition",
            "category": "testing",
            "tags": ["flaky", "testing"],
            "taskCreateRequest": {
                "payload": {
                    "repository": "moonladder/moonmind",
                    "task": {"instructions": "Fix the flaky test"},
                }
            },
        }
    ]


@activity.defn(name="proposal.generate")
async def mock_proposal_generate_empty(args: Dict[str, Any]) -> list[Dict[str, Any]]:
    PROPOSAL_GENERATE_CALLS.append(args)
    return []


@activity.defn(name="proposal.submit")
async def mock_proposal_submit(args: Dict[str, Any]) -> Dict[str, Any]:
    PROPOSAL_SUBMIT_CALLS.append(args)
    candidates = args.get("candidates") or []
    return {
        "generated_count": len(candidates),
        "submitted_count": len(candidates),
        "errors": [],
    }


class TestMoonMindRunWorkflow(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        PLAN_GENERATE_CALLS.clear()
        ARTIFACT_READ_CALLS.clear()
        SKILL_EXECUTE_CALLS.clear()
        SANDBOX_COMMAND_CALLS.clear()
        INTEGRATION_START_CALLS.clear()
        INTEGRATION_STATUS_CALLS.clear()
        PROPOSAL_GENERATE_CALLS.clear()
        PROPOSAL_SUBMIT_CALLS.clear()

    async def test_moonmind_run_workflow(self) -> None:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            await _register_test_search_attributes(env)
            async with (
                Worker(
                    env.client,
                    task_queue=LLM_TASK_QUEUE,
                    activities=[
                        mock_plan_generate,
                    ],
                ),
                Worker(
                    env.client,
                    task_queue=ARTIFACTS_TASK_QUEUE,
                    activities=[mock_artifact_read],
                ),
                Worker(
                    env.client,
                    task_queue=SANDBOX_TASK_QUEUE,
                    activities=[mock_sandbox_command, mock_skill_execute],
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
                self.assertEqual(ARTIFACT_READ_CALLS[0]["principal"], "trusted-owner")
                self.assertEqual(SKILL_EXECUTE_CALLS[0]["principal"], "trusted-owner")
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
                    activities=[
                        mock_plan_generate,
                    ],
                ),
                Worker(
                    env.client,
                    task_queue=ARTIFACTS_TASK_QUEUE,
                    activities=[mock_artifact_read],
                ),
                Worker(
                    env.client,
                    task_queue=SANDBOX_TASK_QUEUE,
                    activities=[mock_sandbox_command, mock_skill_execute],
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
        self.assertEqual(ARTIFACT_READ_CALLS[0]["principal"], "trusted-owner")
        self.assertEqual(SKILL_EXECUTE_CALLS[0]["principal"], "trusted-owner")

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

    async def test_moonmind_run_workflow_fail_fast_surfaces_failed_skill_status(
        self,
    ) -> None:
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
                    task_queue=ARTIFACTS_TASK_QUEUE,
                    activities=[mock_artifact_read],
                ),
                Worker(
                    env.client,
                    task_queue=SANDBOX_TASK_QUEUE,
                    activities=[mock_sandbox_command, mock_skill_execute_failed],
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
                        id="test-workflow-id-failed-skill-status",
                        task_queue="test-task-queue",
                        search_attributes=_trusted_search_attributes(),
                    )
                self.assertIsInstance(
                    exc_info.exception.cause, exceptions.ApplicationError
                )
                self.assertIn(
                    "plan node execution returned status FAILED",
                    exc_info.exception.cause.message,
                )

    async def test_proposals_stage_enabled(self) -> None:
        """When proposeTasks is true, proposal activities are invoked."""
        async with await WorkflowEnvironment.start_time_skipping() as env:
            await _register_test_search_attributes(env)
            async with (
                Worker(
                    env.client,
                    task_queue=LLM_TASK_QUEUE,
                    activities=[
                        mock_plan_generate,
                        mock_proposal_generate,
                        mock_proposal_submit,
                    ],
                ),
                Worker(
                    env.client,
                    task_queue=ARTIFACTS_TASK_QUEUE,
                    activities=[mock_artifact_read],
                ),
                Worker(
                    env.client,
                    task_queue=SANDBOX_TASK_QUEUE,
                    activities=[mock_sandbox_command, mock_skill_execute],
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
                        "initialParameters": {
                            "repo": "moonladder/moonmind",
                            "proposeTasks": True,
                            "proposalDefaultRuntime": "gemini_cli",
                        },
                    },
                    id="test-workflow-proposals-enabled",
                    task_queue="test-task-queue",
                    search_attributes=_trusted_search_attributes(),
                )

            self.assertEqual(result["status"], "success")
            self.assertEqual(len(PROPOSAL_GENERATE_CALLS), 1)
            self.assertEqual(len(PROPOSAL_SUBMIT_CALLS), 1)
            self.assertEqual(result.get("proposals_generated"), 1)
            self.assertEqual(result.get("proposals_submitted"), 1)

    async def test_proposals_stage_disabled(self) -> None:
        """When proposeTasks is absent, proposal activities are not called."""
        async with await WorkflowEnvironment.start_time_skipping() as env:
            await _register_test_search_attributes(env)
            async with (
                Worker(
                    env.client,
                    task_queue=LLM_TASK_QUEUE,
                    activities=[
                        mock_plan_generate,
                        mock_proposal_generate_empty,
                        mock_proposal_submit,
                    ],
                ),
                Worker(
                    env.client,
                    task_queue=ARTIFACTS_TASK_QUEUE,
                    activities=[mock_artifact_read],
                ),
                Worker(
                    env.client,
                    task_queue=SANDBOX_TASK_QUEUE,
                    activities=[mock_sandbox_command, mock_skill_execute],
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
                        "initialParameters": {
                            "repo": "moonladder/moonmind",
                        },
                    },
                    id="test-workflow-proposals-disabled",
                    task_queue="test-task-queue",
                    search_attributes=_trusted_search_attributes(),
                )

            self.assertEqual(result["status"], "success")
            self.assertEqual(len(PROPOSAL_GENERATE_CALLS), 0)
            self.assertEqual(len(PROPOSAL_SUBMIT_CALLS), 0)
            self.assertNotIn("proposals_generated", result)
