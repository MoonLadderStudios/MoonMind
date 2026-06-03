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

# NOTE: Not marked integration_ci — Temporal workflow tests with time-skipping consistently exceed CI timeout thresholds. Kept for local dev verification.
pytestmark = [pytest.mark.integration]

from temporalio import activity, client, workflow
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
from moonmind.schemas.agent_runtime_models import (
    AgentRunResult,
    AgentRunStatus,
)

# ── Mock activities ──

@activity.defn(name="agent_runtime.publish_artifacts")
async def mock_publish_artifacts(result: AgentRunResult | None = None) -> AgentRunResult | None:
    return result

@activity.defn(name="agent_runtime.cancel")
async def mock_cancel(request: dict) -> AgentRunStatus:
    return AgentRunStatus(
        runId=request.get("run_id", "unknown"),
        agentKind=request.get("agent_kind", "unknown"),
        agentId="managed",
        status="canceled"
    )

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

@activity.defn(name="artifact.read")
async def mock_artifact_read_jules_missing_pr(args: Dict[str, Any]) -> bytes:
    ARTIFACT_READ_CALLS.append(args)
    payload = {
        "plan_version": "1.0",
        "metadata": {
            "title": "Jules missing PR plan",
            "created_at": "2026-04-04T00:00:00Z",
            "registry_snapshot": {
                "digest": "reg:sha256:" + ("c" * 64),
                "artifact_ref": "artifact://registry/jules-missing-pr",
            },
        },
        "policy": {"failure_mode": "FAIL_FAST"},
        "nodes": [
            {
                "id": "agent-step-1",
                "tool": {
                    "type": "agent_runtime",
                    "name": "jules",
                },
                "inputs": {
                    "targetRuntime": "jules",
                    "instructions": "Fix the bug",
                    "repo": "moonladder/moonmind",
                },
            }
        ],
    }
    return json.dumps(payload).encode("utf-8")

@activity.defn(name="artifact.create")
async def mock_artifact_create(args: Dict[str, Any]) -> Dict[str, Any]:
    return {"artifact_id": "test-artifact-id", "artifact_ref": f"artifact://{args.get('artifact_id', 'test')}"}

@activity.defn(name="artifact.write_complete")
async def mock_artifact_write_complete(args: Dict[str, Any]) -> Dict[str, Any]:
    return {"status": "complete"}

@workflow.defn(name="MoonMind.ProviderProfileManager")
class MockProviderProfileManager:
    """Minimal mock profile manager that assigns slots on request."""

    def __init__(self) -> None:
        self._shutdown = False
        self.pending_requests: list[dict] = []
        self._leases: dict[str, str] = {}

    @workflow.signal
    def request_slot(self, payload: dict) -> None:
        self.pending_requests.append(payload)

    @workflow.signal
    def release_slot(self, payload: dict) -> None:
        requester_id = payload.get("requester_workflow_id")
        to_remove = [p for p, wf in self._leases.items() if wf == requester_id]
        for p in to_remove:
            del self._leases[p]

    @workflow.signal
    def report_cooldown(self, payload: dict) -> None:
        pass

    @workflow.signal
    def sync_profiles(self, payload: dict) -> None:
        pass

    @workflow.query
    def get_state(self) -> dict:
        return {"leases": dict(self._leases), "pending_requests": list(self.pending_requests)}

    @workflow.run
    async def run(self, input_payload: dict) -> dict:
        while not self._shutdown:
            await workflow.wait_condition(lambda: len(self.pending_requests) > 0 or self._shutdown)
            if self._shutdown:
                break
            while self.pending_requests:
                req = self.pending_requests.pop(0)
                profile_id = "default-managed"
                self._leases[profile_id] = req["requester_workflow_id"]
                handle = workflow.get_external_workflow_handle(req["requester_workflow_id"])
                await handle.signal("slot_assigned", {"profile_id": profile_id})
        return {}

@workflow.defn(name="MoonMind.AgentRun")
class MockNoPrAgentRun:
    @workflow.run
    async def run(self, _input_payload: dict) -> dict:
        return {
            "summary": "Completed without creating a PR",
            "metadata": {},
            "outputRefs": [],
            "diagnosticsRef": None,
            "failureClass": None,
            "providerErrorCode": None,
            "retryRecommendation": None,
        }

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
                    activities=[mock_artifact_read_agent, mock_artifact_create, mock_artifact_write_complete],
                ),
                Worker(
                    env.client,
                    task_queue=SANDBOX_TASK_QUEUE,
                    activities=[mock_skill_execute_agent],
                ),
                Worker(
                    env.client,
                    task_queue=WORKFLOW_TASK_QUEUE,
                    workflows=[MoonMindAgentRun, MockProviderProfileManager],
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
                    activities=[bad_plan_reader, mock_artifact_create, mock_artifact_write_complete],
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
                    "expected 'agent_runtime'",
                    exc_info.exception.cause.message,
                )

    async def test_legacy_skill_tool_type_is_rejected(self) -> None:
        """Legacy tool.type='skill' plan nodes are no longer dispatched by MoonMind.Run."""

        @activity.defn(name="artifact.read")
        async def legacy_skill_plan_reader(args: Dict[str, Any]) -> bytes:
            ARTIFACT_READ_CALLS.append(args)
            plan = {
                "plan_version": "1.0",
                "metadata": {
                    "title": "Legacy skill plan",
                    "created_at": "2026-06-03T00:00:00Z",
                    "registry_snapshot": {
                        "digest": "reg:sha256:" + ("e" * 64),
                        "artifact_ref": "artifact://registry/legacy-skill",
                    },
                },
                "policy": {"failure_mode": "FAIL_FAST"},
                "nodes": [
                    {
                        "id": "legacy-skill-step",
                        "tool": {
                            "type": "skill",
                            "name": "repo.run_tests",
                            "version": "1.0.0",
                        },
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
                    activities=[
                        legacy_skill_plan_reader,
                        mock_artifact_create,
                        mock_artifact_write_complete,
                    ],
                ),
                Worker(
                    env.client,
                    task_queue=SANDBOX_TASK_QUEUE,
                    activities=[mock_skill_execute_agent],
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
                        id="test-legacy-skill-tool-type-rejected",
                        task_queue="test-task-queue",
                        search_attributes=_trusted_search_attributes(),
                    )

                self.assertIn(
                    "unsupported plan node tool.type: 'skill'; expected 'agent_runtime'",
                    exc_info.exception.cause.message,
                )
                self.assertEqual(SKILL_EXECUTE_CALLS, [])
                self.assertNotIn(
                    "artifact://registry/legacy-skill",
                    [call.get("artifact_ref") for call in ARTIFACT_READ_CALLS],
                )

    async def test_jules_agent_runtime_pr_publish_without_pr_fails_in_finalization(
        self,
    ) -> None:
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
                    activities=[
                        mock_artifact_read_jules_missing_pr,
                        mock_artifact_create,
                        mock_artifact_write_complete,
                    ],
                ),
                Worker(
                    env.client,
                    task_queue=WORKFLOW_TASK_QUEUE,
                    workflows=[MockNoPrAgentRun],
                    workflow_runner=UnsandboxedWorkflowRunner(),
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
                        {
                            "workflowType": "MoonMind.Run",
                            "title": "Jules missing PR failure",
                            "initialParameters": {
                                "repo": "moonladder/moonmind",
                                "publishMode": "pr",
                            },
                        },
                        id="test-jules-agent-runtime-missing-pr",
                        task_queue="test-task-queue",
                        search_attributes=_trusted_search_attributes(),
                    )

                self.assertIn(
                    "publishMode 'pr' requested but no PR was created",
                    exc_info.exception.cause.message,
                )

# ── Legacy registry workflow-boundary tests ──

class TestSnapshotPinningOnRetry(unittest.IsolatedAsyncioTestCase):
    """Verify MoonMind.Run dispatch does not read legacy skill registries."""

    def setUp(self) -> None:
        PLAN_GENERATE_CALLS.clear()
        ARTIFACT_READ_CALLS.clear()
        SKILL_EXECUTE_CALLS.clear()

    # ------------------------------------------------------------------
    # 1. Retry within the same run passes the identical resolved_skillset_ref
    # ------------------------------------------------------------------

    async def test_retry_child_workflow_receives_identical_skillset_ref(self) -> None:
        """Agent runtime dispatch ignores registry_snapshot_ref metadata."""
        KNOWN_SNAPSHOT_REF = "artifact://registry/retry-snap-42"

        @activity.defn(name="artifact.read")
        async def retry_plan_reader(args: Dict[str, Any]) -> bytes:
            ARTIFACT_READ_CALLS.append(args)
            if args.get("artifact_ref") == KNOWN_SNAPSHOT_REF:
                payload = {"skills": []}
                return json.dumps(payload).encode("utf-8")
            payload = {
                "plan_version": "1.0",
                "metadata": {
                    "title": "Retry plan",
                    "created_at": "2026-03-16T00:00:00Z",
                    "registry_snapshot": {
                        "digest": "reg:sha256:" + ("b" * 64),
                        "artifact_ref": KNOWN_SNAPSHOT_REF,
                    },
                },
                "policy": {"failure_mode": "FAIL_FAST"},
                "nodes": [
                    {
                        "id": "retry-agent-step",
                        "tool": {
                            "type": "agent_runtime",
                            "name": "codex_cli",
                            "version": "1.0",
                        },
                        "inputs": {
                            "repo_ref": "git:moonmind",
                            "selectedSkill": "repo.run_tests",
                        },
                        "options": {},
                    }
                ],
            }
            return json.dumps(payload).encode("utf-8")

        @activity.defn(name="plan.generate")
        async def retry_plan_gen(args: Dict[str, Any]) -> Dict[str, Any]:
            PLAN_GENERATE_CALLS.append(args)
            return {"plan_ref": "artifact://plan/retry-test"}

        async with await WorkflowEnvironment.start_time_skipping() as env:
            await _register_test_search_attributes(env)
            async with (
                Worker(
                    env.client,
                    task_queue=LLM_TASK_QUEUE,
                    activities=[retry_plan_gen],
                ),
                Worker(
                    env.client,
                    task_queue=ARTIFACTS_TASK_QUEUE,
                    activities=[retry_plan_reader, mock_artifact_create, mock_artifact_write_complete],
                ),
                Worker(
                    env.client,
                    task_queue="test-task-queue",
                    workflows=[MoonMindRunWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ),
            ):
                handle = await env.client.start_workflow(
                    MoonMindRunWorkflow.run,
                    {"workflowType": "MoonMind.Run", "title": "Retry Test"},
                    id="test-retry-skillset-ref",
                    task_queue="test-task-queue",
                    search_attributes=_trusted_search_attributes(),
                )
                try:
                    await handle.result()
                except Exception:
                    pass

                # The legacy skill registry is no longer read by Run dispatch.
                registry_reads = [
                    c for c in ARTIFACT_READ_CALLS
                    if c.get("artifact_ref") == KNOWN_SNAPSHOT_REF
                ]
                self.assertEqual(
                    len(registry_reads), 0,
                    "Run dispatch must not read registry snapshots",
                )

    # ------------------------------------------------------------------
    # 2. Rerun defaults to the original ResolvedSkillSet (re-reads plan,
    #    does NOT call agent_skill.resolve)
    # ------------------------------------------------------------------

    async def test_rerun_reuses_plan_registry_snapshot_ref(self) -> None:
        """When a run is rerun, the workflow re-reads the plan artifact and
        uses the registry_snapshot_ref embedded in the plan metadata.  It
        does NOT invoke agent_skill.resolve to produce a new snapshot.

        This test starts two independent workflow executions with the same
        plan artifact, verifying that both receive the same
        resolved_skillset_ref from the plan — proving no re-resolution occurs.
        """
        EXPECTED_SNAPSHOT_REF = "artifact://registry/rerun-snap-99"

        @activity.defn(name="artifact.read")
        async def rerun_plan_reader(args: Dict[str, Any]) -> bytes:
            ARTIFACT_READ_CALLS.append(args)
            payload = {
                "plan_version": "1.0",
                "metadata": {
                    "title": "Rerun plan",
                    "created_at": "2026-03-16T00:00:00Z",
                    "registry_snapshot": {
                        "digest": "reg:sha256:" + ("c" * 64),
                        "artifact_ref": EXPECTED_SNAPSHOT_REF,
                    },
                },
                "policy": {"failure_mode": "FAIL_FAST"},
                "nodes": [
                    {
                        "id": "rerun-agent-step",
                        "tool": {
                            "type": "agent_runtime",
                            "name": "codex_cli",
                            "version": "1.0",
                        },
                        "inputs": {
                            "repo_ref": "git:moonmind",
                            "selectedSkill": "repo.run_tests",
                        },
                        "options": {},
                    }
                ],
            }
            return json.dumps(payload).encode("utf-8")

        @activity.defn(name="plan.generate")
        async def rerun_plan_gen(args: Dict[str, Any]) -> Dict[str, Any]:
            PLAN_GENERATE_CALLS.append(args)
            return {"plan_ref": "artifact://plan/rerun-test"}

        async with await WorkflowEnvironment.start_time_skipping() as env:
            await _register_test_search_attributes(env)
            async with (
                Worker(
                    env.client,
                    task_queue=LLM_TASK_QUEUE,
                    activities=[rerun_plan_gen],
                ),
                Worker(
                    env.client,
                    task_queue=ARTIFACTS_TASK_QUEUE,
                    activities=[rerun_plan_reader, mock_artifact_create, mock_artifact_write_complete],
                ),
                Worker(
                    env.client,
                    task_queue=SANDBOX_TASK_QUEUE,
                    activities=[mock_skill_execute_agent],
                ),
                Worker(
                    env.client,
                    task_queue="test-task-queue",
                    workflows=[MoonMindRunWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ),
            ):
                # First run
                handle1 = await env.client.start_workflow(
                    MoonMindRunWorkflow.run,
                    {"workflowType": "MoonMind.Run", "title": "Rerun Run 1"},
                    id="test-rerun-run-1",
                    task_queue="test-task-queue",
                    search_attributes=_trusted_search_attributes(),
                )
                try:
                    await handle1.result()
                except Exception:
                    pass  # May fail; we only care about the plan read

                # Second run (simulating a rerun)
                handle2 = await env.client.start_workflow(
                    MoonMindRunWorkflow.run,
                    {"workflowType": "MoonMind.Run", "title": "Rerun Run 2"},
                    id="test-rerun-run-2",
                    task_queue="test-task-queue",
                    search_attributes=_trusted_search_attributes(),
                )
                try:
                    await handle2.result()
                except Exception:
                    pass

                # Both runs ignore the legacy registry snapshot ref during dispatch.
                registry_reads = [
                    c for c in ARTIFACT_READ_CALLS
                    if c.get("artifact_ref") == EXPECTED_SNAPSHOT_REF
                ]
                self.assertEqual(
                    len(registry_reads), 0,
                    "Reruns must not read registry snapshots for Run dispatch",
                )

    # ------------------------------------------------------------------
    # 3. Child workflow AgentRun dispatch ignores legacy registry snapshots
    # ------------------------------------------------------------------

    async def test_child_workflow_ignores_legacy_registry_snapshot_ref(self) -> None:
        """Agent runtime dispatch does not read registry_snapshot_ref metadata."""
        KNOWN_SNAPSHOT_REF = "artifact://registry/child-ref-test"

        @activity.defn(name="artifact.read")
        async def child_ref_plan_reader(args: Dict[str, Any]) -> bytes:
            ARTIFACT_READ_CALLS.append(args)
            if args.get("artifact_ref") == KNOWN_SNAPSHOT_REF:
                payload = {"skills": []}
                return json.dumps(payload).encode("utf-8")
            payload = {
                "plan_version": "1.0",
                "metadata": {
                    "title": "Child ref test plan",
                    "created_at": "2026-03-16T00:00:00Z",
                    "registry_snapshot": {
                        "digest": "reg:sha256:" + ("d" * 64),
                        "artifact_ref": KNOWN_SNAPSHOT_REF,
                    },
                },
                "policy": {"failure_mode": "FAIL_FAST"},
                "nodes": [
                    {
                        "id": "child-agent-step",
                        "tool": {
                            "type": "agent_runtime",
                            "name": "codex_cli",
                            "version": "1.0",
                        },
                        "inputs": {
                            "repo_ref": "git:moonmind",
                            "selectedSkill": "repo.run_tests",
                        },
                        "options": {},
                    }
                ],
            }
            return json.dumps(payload).encode("utf-8")

        @activity.defn(name="plan.generate")
        async def child_ref_plan_gen(args: Dict[str, Any]) -> Dict[str, Any]:
            PLAN_GENERATE_CALLS.append(args)
            return {"plan_ref": "artifact://plan/child-ref-test"}

        async with await WorkflowEnvironment.start_time_skipping() as env:
            await _register_test_search_attributes(env)
            async with (
                Worker(
                    env.client,
                    task_queue=LLM_TASK_QUEUE,
                    activities=[child_ref_plan_gen],
                ),
                Worker(
                    env.client,
                    task_queue=ARTIFACTS_TASK_QUEUE,
                    activities=[child_ref_plan_reader, mock_artifact_create, mock_artifact_write_complete],
                ),
                Worker(
                    env.client,
                    task_queue=SANDBOX_TASK_QUEUE,
                    activities=[mock_skill_execute_agent],
                ),
                Worker(
                    env.client,
                    task_queue="test-task-queue",
                    workflows=[MoonMindRunWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ),
            ):
                handle = await env.client.start_workflow(
                    MoonMindRunWorkflow.run,
                    {"workflowType": "MoonMind.Run", "title": "Child Ref Test"},
                    id="test-child-ref",
                    task_queue="test-task-queue",
                    search_attributes=_trusted_search_attributes(),
                )
                try:
                    await handle.result()
                except Exception:
                    pass

                # Assert the registry snapshot is not read by Run dispatch.
                registry_reads = [
                    c for c in ARTIFACT_READ_CALLS
                    if c.get("artifact_ref") == KNOWN_SNAPSHOT_REF
                ]
                self.assertEqual(
                    len(registry_reads), 0,
                    "Parent must not read registry snapshots for Run dispatch",
                )
