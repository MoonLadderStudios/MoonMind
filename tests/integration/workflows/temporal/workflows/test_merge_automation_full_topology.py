"""MM-1209 regression for the real MergeAutomation/UserWorkflow/AgentRun chain."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from temporalio import activity
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.activity_catalog import (
    AGENT_RUNTIME_TASK_QUEUE,
    ARTIFACTS_TASK_QUEUE,
    INTEGRATIONS_TASK_QUEUE,
    LLM_TASK_QUEUE,
)
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.merge_automation import (
    MoonMindMergeAutomationWorkflow,
)
from moonmind.workflows.temporal.workflows.merge_gate import (
    deterministic_resolver_idempotency_key,
)
from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow
from tests.integration.services.temporal.workflows.test_agent_run import (
    MockProviderProfileManager,
    _COMMON_AGENT_RUN_ACTIVITIES,
)
from tests.unit.workflows.temporal.workflows.test_run_integration import (
    _mock_resilience_policy_envelope,
)


pytestmark = [pytest.mark.integration]


@activity.defn(name="plan.generate")
async def _plan_generate(_payload: dict[str, Any]) -> dict[str, str]:
    return {"plan_ref": "artifact://mm1209/plan"}


@activity.defn(name="artifact.read")
async def _artifact_read(_payload: dict[str, Any]) -> bytes:
    artifact_ref = str(
        _payload.get("artifact_ref") or _payload.get("artifactRef") or ""
    )
    if artifact_ref == "artifact://mm1209/registry":
        return json.dumps({"skills": []}).encode("utf-8")
    return json.dumps(
        {
            "plan_version": "1.0",
            "metadata": {
                "title": "Resolve PR",
                "created_at": "2026-07-12T00:00:00Z",
                "registry_snapshot": {
                    "digest": "reg:sha256:" + ("a" * 64),
                    "artifact_ref": "artifact://mm1209/registry",
                },
            },
            "policy": {"failure_mode": "FAIL_FAST"},
            "nodes": [
                {
                    "id": "resolver",
                    "tool": {"type": "agent_runtime", "name": "claude_code"},
                    "inputs": {
                        "targetRuntime": "claude_code",
                        "instructions": "Run the resolved pr-resolver Skill.",
                        "selectedSkill": "pr-resolver",
                        "skill": {
                            "name": "pr-resolver",
                            "sideEffect": {
                                "terminalContractId": "pr_resolver_terminal.v1",
                                "outcomeArtifact": "var/pr_resolver/result.json",
                                "terminalSchemaVersion": "2",
                            },
                        },
                    },
                }
            ],
        }
    ).encode("utf-8")


@activity.defn(name="artifact.create")
async def _artifact_create(_payload: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    return {"artifact_id": "art-mm1209"}, {"upload_url": "memory://mm1209"}


@activity.defn(name="artifact.write_complete")
async def _artifact_write(_payload: Any) -> dict[str, str]:
    return {"artifact_id": "art-mm1209"}


@activity.defn(name="resilience.compile_policy")
async def _compile_policy(_payload: Any) -> dict[str, Any]:
    return _mock_resilience_policy_envelope(_payload)


@activity.defn(name="execution.record_terminal_state")
async def _record_terminal_state(_payload: Any) -> dict[str, bool]:
    return {"recorded": True}


@activity.defn(name="merge_automation.evaluate_readiness")
async def _readiness(_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "headSha": "abcdef1",
        "ready": True,
        "pullRequestOpen": True,
        "policyAllowed": True,
        "checksComplete": True,
        "checksPassing": True,
        "automatedReviewComplete": True,
        "jiraStatusAllowed": True,
    }


_terminal_evidence_calls = 0


@activity.defn(name="agent_runtime.evaluate_terminal_evidence")
async def _terminal_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    global _terminal_evidence_calls
    _terminal_evidence_calls += 1
    contract = payload["terminalContract"]
    if _terminal_evidence_calls == 1:
        return {
            "summary": "durable continuation requested",
            "failureClass": "execution_error",
            "providerErrorCode": "PR_RESOLVER_REENTER_GATE",
            "metadata": {
                "terminalContractOutcome": "continuation_requested",
                "terminalContractEvidencePath": "var/pr_resolver/result.json",
                "terminalContractExecutionRef": contract["executionRef"],
                "mergeAutomationDisposition": "reenter_gate",
                "gatedContinuation": {
                    "schemaVersion": "gated-continuation/v1",
                    "gateType": "merge_automation",
                    "action": "reenter_gate",
                    "reason": "codex_review_grace_wait",
                    "retryAfterSeconds": 2,
                    "executionRef": contract["executionRef"],
                    "headSha": "abcdef1",
                },
            },
        }
    return {
        "summary": "merged",
        "metadata": {
            "terminalContractOutcome": "terminal_success",
            "terminalContractSatisfied": True,
            "mergeAutomationDisposition": "merged",
            "push_status": "pushed",
            "push_branch": "feature",
            "pull_request_url": (
                "https://github.com/MoonLadderStudios/MoonMind/pull/1209"
            ),
        },
    }


@activity.defn(name="agent_skill.resolve")
async def _resolve_skill(*_args: Any) -> dict[str, Any]:
    return {
        "manifestRef": "art-skill-mm1209",
        "skills": [{"name": "pr-resolver"}],
    }


async def _register_search_attributes(env: WorkflowEnvironment) -> None:
    await env.client.operator_service.add_search_attributes(
        AddSearchAttributesRequest(
            namespace=env.client.namespace,
            search_attributes={
                "mm_owner_id": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_owner_type": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_entry": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_repo": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_state": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_updated_at": IndexedValueType.INDEXED_VALUE_TYPE_DATETIME,
                "mm_started_at": IndexedValueType.INDEXED_VALUE_TYPE_DATETIME,
                "mm_scheduled_for": IndexedValueType.INDEXED_VALUE_TYPE_DATETIME,
                "mm_has_dependencies": IndexedValueType.INDEXED_VALUE_TYPE_BOOL,
                "mm_dependency_count": IndexedValueType.INDEXED_VALUE_TYPE_INT,
                "mm_current_step_order": IndexedValueType.INDEXED_VALUE_TYPE_INT,
                "mm_integration": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_target_runtime": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST,
                "mm_target_skill": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST,
                "mm_title": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST,
            },
        )
    )


@pytest.mark.asyncio
async def test_real_three_workflow_topology_waits_then_merges() -> None:
    global _terminal_evidence_calls
    _terminal_evidence_calls = 0
    parent_id = "mm1209-full-topology"
    child_queue = "mm.workflow.user.v2"
    resolver_base = deterministic_resolver_idempotency_key(
        parent_workflow_id="user-parent",
        repo="MoonLadderStudios/MoonMind",
        pr_number=1209,
        head_sha="abcdef1",
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        await _register_search_attributes(env)
        common = [*_COMMON_AGENT_RUN_ACTIVITIES, _terminal_evidence, _resolve_skill]
        async with (
            Worker(env.client, task_queue=LLM_TASK_QUEUE, activities=[_plan_generate]),
            Worker(
                env.client,
                task_queue=ARTIFACTS_TASK_QUEUE,
                activities=[
                    _artifact_read,
                    _artifact_create,
                    _artifact_write,
                    _compile_policy,
                    _record_terminal_state,
                    *common,
                ],
            ),
            Worker(env.client, task_queue=AGENT_RUNTIME_TASK_QUEUE, activities=common),
            Worker(
                env.client,
                task_queue=INTEGRATIONS_TASK_QUEUE,
                activities=[_readiness],
            ),
            Worker(
                env.client,
                task_queue=child_queue,
                workflows=[MoonMindUserWorkflow, MoonMindAgentRun, MockProviderProfileManager],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client,
                task_queue="mm1209-parent",
                workflows=[MoonMindMergeAutomationWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
        ):
            await env.client.start_workflow(
                MockProviderProfileManager.run,
                {"runtime_id": "claude_code"},
                id="provider-profile-manager:claude_code",
                task_queue=child_queue,
            )
            handle = await env.client.start_workflow(
                MoonMindMergeAutomationWorkflow.run,
                {
                    "workflowType": "MoonMind.MergeAutomation",
                    "parentWorkflowId": "user-parent",
                    "publishContextRef": "artifact://publish-context",
                    "pullRequest": {
                        "repo": "MoonLadderStudios/MoonMind",
                        "number": 1209,
                        "url": "https://github.com/MoonLadderStudios/MoonMind/pull/1209",
                        "headSha": "abcdef1",
                        "headBranch": "feature",
                        "baseBranch": "main",
                    },
                    "mergeAutomationConfig": {
                        "timeouts": {"fallbackPollSeconds": 2},
                    },
                    "resolverTemplate": {"targetRuntime": "claude_code"},
                },
                id=parent_id,
                task_queue="mm1209-parent",
            )

            async def complete_agent(cycle: int) -> None:
                agent_id = f"{resolver_base}:{cycle}:agent:resolver"
                agent = env.client.get_workflow_handle(agent_id)
                for _ in range(100):
                    try:
                        await agent.signal(
                            MoonMindAgentRun.completion_signal,
                            {"summary": f"resolver cycle {cycle} completed"},
                        )
                        return
                    except Exception:
                        await asyncio.sleep(0.05)
                try:
                    parent_result = await asyncio.wait_for(handle.result(), timeout=2)
                except asyncio.TimeoutError:
                    parent_result = "Timeout (still running)"
                resolver_id = f"{resolver_base}:{cycle}"
                try:
                    resolver_result = await env.client.get_workflow_handle(
                        resolver_id
                    ).result()
                except Exception as exc:
                    messages = []
                    current: BaseException | None = exc
                    while current is not None:
                        messages.append(f"{type(current).__name__}: {current}")
                        current = getattr(current, "cause", None) or current.__cause__
                    resolver_result = " <- ".join(messages)
                try:
                    first_resolver = await env.client.get_workflow_handle(
                        f"{resolver_base}:1"
                    ).result()
                except Exception as exc:
                    first_messages = []
                    current = exc
                    while current is not None:
                        first_messages.append(f"{type(current).__name__}: {current}")
                        current = getattr(current, "cause", None) or current.__cause__
                    first_resolver = " <- ".join(first_messages)
                raise AssertionError(
                    f"AgentRun child did not start: {agent_id}; "
                    f"resolver={resolver_result}; first={first_resolver}; "
                    f"parent={parent_result}"
                )

            await complete_agent(1)
            await complete_agent(2)
            result = await asyncio.wait_for(handle.result(), timeout=30)
            try:
                second_result = await env.client.get_workflow_handle(
                    f"{resolver_base}:2"
                ).result()
            except Exception as exc:
                second_messages = []
                current: BaseException | None = exc
                while current is not None:
                    second_messages.append(f"{type(current).__name__}: {current}")
                    current = getattr(current, "cause", None) or current.__cause__
                second_result = " <- ".join(second_messages)

    assert result["status"] == "merged", (
        result.get("summary"),
        result.get("blockers"),
        second_result,
    )
    assert result["cycles"] == 2
    assert result["continuationCounters"]["continuation_wait_completed"] == 1
    assert _terminal_evidence_calls == 2
