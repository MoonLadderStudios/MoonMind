from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from temporalio import activity, workflow
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.activity_catalog import INTEGRATIONS_TASK_QUEUE
from moonmind.workflows.temporal.workflows import merge_automation as module
from moonmind.workflows.temporal.workflows.merge_automation import (
    MoonMindMergeAutomationWorkflow,
)


@activity.defn(name="merge_automation.evaluate_readiness")
async def _ready(_payload: dict[str, Any]) -> dict[str, Any]:
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


@workflow.defn(name="MoonMind.UserWorkflow")
class _RecordedResolverChild:
    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        initial = payload.get("initial_parameters", {})
        scenario = str(
            initial.get("task", {}).get("runtime", {}).get("model") or ""
        )
        info = workflow.info()
        cycle = int(info.workflow_id.rsplit(":", 1)[-1])
        if cycle > 1:
            return {
                "status": "success",
                "mergeAutomationDisposition": "merged",
                "headSha": "abcdef1",
            }
        if scenario == "old_failure":
            return {
                "status": "failed",
                "mergeAutomationDisposition": "failed",
                "providerErrorCode": "PR_RESOLVER_REENTER_GATE",
            }
        continuation = {
            "schemaVersion": "gated-continuation/v1",
            "gateType": "merge_automation",
            "action": "reenter_gate",
            "reason": "codex_review_grace_wait",
            "executionRef": "step:resolver:1",
            "headSha": "abcdef1",
            "ownerWorkflowId": info.parent.workflow_id,
            "ownerRunId": info.parent.run_id,
            "ownerWorkflowType": "MoonMind.MergeAutomation",
            "childWorkflowId": info.workflow_id,
            "childRunId": info.run_id,
        }
        if scenario == "new_timed":
            continuation["retryAfterSeconds"] = 2
        if scenario == "rejected":
            top_level_child_run_id = "forged-run"
        else:
            top_level_child_run_id = info.run_id
        return {
            "status": "success",
            "completionDisposition": "gated_continuation",
            "mergeAutomationDisposition": "reenter_gate",
            "headSha": "abcdef1",
            "executionRef": "step:resolver:1",
            "childRunId": top_level_child_run_id,
            "gatedContinuation": continuation,
        }


def _payload(scenario: str) -> dict[str, Any]:
    return {
        "workflowType": "MoonMind.MergeAutomation",
        "parentWorkflowId": "user-workflow-parent",
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
        "resolverTemplate": {"model": scenario},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected_status"),
    [
        ("old_failure", "failed"),
        ("new_timed", "merged"),
        ("legacy_untimed", "merged"),
        ("rejected", "failed"),
    ],
)
async def test_continuation_histories_replay_deterministically(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    expected_status: str,
) -> None:
    async def skip_artifact(
        self: MoonMindMergeAutomationWorkflow,
        *,
        name: str,
        payload: dict[str, Any],
    ) -> None:
        return None

    monkeypatch.setattr(
        MoonMindMergeAutomationWorkflow,
        "_write_json_artifact",
        skip_artifact,
    )
    monkeypatch.setattr(
        MoonMindMergeAutomationWorkflow,
        "_publish_visibility",
        lambda self: None,
    )
    child_queue = module.settings.temporal.user_workflow_v2_task_queue
    parent_queue = "mm1209-merge-replay"
    async with await WorkflowEnvironment.start_time_skipping() as env:
        await env.client.operator_service.add_search_attributes(
            AddSearchAttributesRequest(
                namespace=env.client.namespace,
                search_attributes={
                    "mm_owner_id": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                    "mm_owner_type": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                    "mm_entry": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                    "mm_repo": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                    "mm_state": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                },
            )
        )
        async with (
            Worker(
                env.client,
                task_queue=INTEGRATIONS_TASK_QUEUE,
                activities=[_ready],
            ),
            Worker(
                env.client,
                task_queue=child_queue,
                workflows=[_RecordedResolverChild],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client,
                task_queue=parent_queue,
                workflows=[MoonMindMergeAutomationWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
        ):
            handle = await env.client.start_workflow(
                MoonMindMergeAutomationWorkflow.run,
                _payload(scenario),
                id=f"mm1209-{scenario}",
                task_queue=parent_queue,
                execution_timeout=timedelta(minutes=2),
            )
            result = await handle.result()
            history = await handle.fetch_history()

    assert result["status"] == expected_status
    if scenario == "new_timed":
        assert result["continuationCounters"]["continuation_wait_completed"] == 1
    if scenario == "legacy_untimed":
        assert result["continuationCounters"]["legacy_continuation_fallback_used"] == 1
    if scenario == "rejected":
        assert result["continuationCounters"]["continuation_rejected_ownership"] == 1

    replayer = Replayer(
        workflows=[MoonMindMergeAutomationWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(history)
