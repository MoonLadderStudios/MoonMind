"""Real request selection survives a gate worker upgrade and replay."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from temporalio import activity, workflow
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.schemas.temporal_models import MergeAutomationStartInput
from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.workflows.temporal.activity_catalog import INTEGRATIONS_TASK_QUEUE
from moonmind.workflows.temporal.activity_runtime import TemporalIntegrationActivities
from moonmind.workflows.temporal.workflows import merge_automation as module
from moonmind.workflows.temporal.workflows.merge_automation import (
    MoonMindMergeAutomationWorkflow,
)


@workflow.defn(name="MoonMind.MergeAutomation")
class _BeforeRequestReconciliation(MoonMindMergeAutomationWorkflow):
    def _reconcile_selected_review_request(self, evaluation: Any) -> None:
        # Capture the old consumer, including a rolling upgrade where the
        # Activity already emits the selected request fields.
        return None

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await super().run(payload)


@workflow.defn(name="MoonMind.UserWorkflow")
class _CleanFixtureResolver:
    @workflow.run
    async def run(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "success",
            "mergeAutomationDisposition": "review_clean",
            "headSha": "abcdef1",
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_producer", [True, False])
async def test_selected_request_survives_worker_upgrade_and_replay(
    monkeypatch, legacy_producer
):
    repo = "MoonLadderStudios/MoonMind"
    state = {"upgraded": False, "complete": False}
    requests = []
    observations = []
    artifacts = []
    integration = TemporalIntegrationActivities()
    first = {
        "provider": "codex",
        "headSha": "abcdef1",
        "requestKey": "recorded-request",
        "requestCommentId": 100,
        "requestedAt": "2026-08-24T22:00:00Z",
    }
    second = {**first, "requestCommentId": 102, "requestedAt": "2026-08-24T22:02:00Z"}
    provider_user = {"login": "chatgpt-codex-connector[bot]"}

    def respond(request):
        assert request.method == "GET"
        path = request.url.path.removeprefix(f"/repos/{repo}/")
        if path == "pulls/350":
            body = {
                "state": "open",
                "merged": False,
                "head": {"sha": "abcdef1"},
                "base": {"sha": "base123", "ref": "main"},
                "mergeable": True,
            }
        elif path.endswith("/status"):
            body = {"state": "success", "statuses": []}
        elif path.endswith("/check-runs"):
            body = {
                "check_runs": [
                    {"name": "unit", "status": "completed", "conclusion": "success"}
                ]
            }
        elif path == "branches/main":
            body = {"protected": False}
        elif path == "issues/350/comments":
            body = [
                {
                    "id": 100,
                    "body": "@codex review",
                    "created_at": first["requestedAt"],
                },
                {
                    "id": 101,
                    "body": "You have reached your Codex usage limits for code reviews.",
                    "created_at": "2026-08-24T22:01:00Z",
                    "user": provider_user,
                },
                {
                    "id": 102,
                    "body": "@codex review",
                    "created_at": second["requestedAt"],
                },
            ]
            if state["complete"]:
                body.append(
                    {
                        "id": 103,
                        "body": "Codex Review: Didn't find any major issues. 🚀",
                        "created_at": "2026-08-24T22:03:00Z",
                        "user": provider_user,
                    }
                )
        elif path == "pulls/350/reviews" or path.endswith("/reactions"):
            body = []
        else:
            raise AssertionError(path)
        return httpx.Response(200, json=body)

    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        lambda **kwargs: client_type(transport=transport, **kwargs),
    )
    monkeypatch.setattr(
        GitHubService,
        "resolve_github_token",
        AsyncMock(return_value=("synthetic-token", None)),
    )

    @activity.defn(name="merge_automation.evaluate_readiness")
    async def evaluate(payload):
        requests.append(payload)
        result = await integration.merge_automation_evaluate_readiness(payload)
        if legacy_producer and not state["upgraded"]:
            result.pop("automatedReviewRequestCommentId", None)
            result.pop("automatedReviewRequestedAt", None)
        observations.append(dict(result))
        return result

    async def capture_artifact(self, *, name, payload):
        artifacts.append((name, payload))

    monkeypatch.setattr(
        MoonMindMergeAutomationWorkflow, "_write_json_artifact", capture_artifact
    )
    monkeypatch.setattr(
        MoonMindMergeAutomationWorkflow, "_publish_visibility", lambda self: None
    )
    payload = {
        "workflowType": "MoonMind.MergeAutomation",
        "parentWorkflowId": "parent",
        "publishContextRef": "artifact://publish-context",
        "pullRequest": {
            "repo": repo,
            "number": 350,
            "url": f"https://github.com/{repo}/pull/350",
            "headSha": "abcdef1",
            "headBranch": "feature",
            "baseBranch": "main",
        },
        "mergeAutomationConfig": {
            "finishMode": "fix_only",
            "timeouts": {"fallbackPollSeconds": 2},
            "reviewLoop": {"enabled": True},
        },
        "activeReviewRequest": first,
        "reviewCycles": [{"cycle": 1, **first, "status": "requested"}],
    }
    payload = MergeAutomationStartInput.model_validate(payload).model_dump(
        by_alias=True, mode="json"
    )
    parent_queue = f"review-request-upgrade-{legacy_producer}"
    child_queue = module.settings.temporal.user_workflow_v2_task_queue
    async with await WorkflowEnvironment.start_time_skipping() as env:
        await env.client.operator_service.add_search_attributes(
            AddSearchAttributesRequest(
                namespace=env.client.namespace,
                search_attributes={
                    key: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD
                    for key in [
                        "mm_owner_id",
                        "mm_owner_type",
                        "mm_entry",
                        "mm_repo",
                        "mm_state",
                    ]
                },
            )
        )
        async with Worker(
            env.client, task_queue=INTEGRATIONS_TASK_QUEUE, activities=[evaluate]
        ), Worker(
            env.client,
            task_queue=child_queue,
            workflows=[_CleanFixtureResolver],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):

            async def wait_for_timer(count):
                async with asyncio.timeout(15):
                    while True:
                        history = await handle.fetch_history()
                        if (
                            len(observations) >= count
                            and sum(
                                event.HasField("timer_started_event_attributes")
                                for event in history.events
                            )
                            >= count
                        ):
                            return history
                        await asyncio.sleep(0.01)

            async with Worker(
                env.client,
                task_queue=parent_queue,
                workflows=[_BeforeRequestReconciliation],
                workflow_runner=UnsandboxedWorkflowRunner(),
                max_cached_workflows=0,
            ):
                handle = await env.client.start_workflow(
                    _BeforeRequestReconciliation.run,
                    payload,
                    id=parent_queue,
                    task_queue=parent_queue,
                    execution_timeout=timedelta(minutes=5),
                )
                old_history = await wait_for_timer(1)
            await Replayer(
                workflows=[MoonMindMergeAutomationWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ).replay_workflow(old_history)
            state["upgraded"] = True
            async with Worker(
                env.client,
                task_queue=parent_queue,
                workflows=[MoonMindMergeAutomationWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
                max_cached_workflows=0,
            ):
                await env.sleep(timedelta(seconds=3))
                pending_history = await wait_for_timer(2)
                pending = await handle.query(MoonMindMergeAutomationWorkflow.summary)
            assert pending["reviewLoop"]["activeRequest"]["requestCommentId"] == 102
            cycles = pending["reviewLoop"]["cycleRecords"]
            assert [cycle["status"] for cycle in cycles] == ["superseded", "requested"]
            restored = MergeAutomationStartInput.model_validate(
                {
                    **payload,
                    "activeReviewRequest": pending["reviewLoop"]["activeRequest"],
                    "reviewCycles": cycles,
                }
            )
            assert restored.active_review_request.request_comment_id == 102
            assert restored.review_cycles[0].request_comment_id == 100
            await Replayer(
                workflows=[MoonMindMergeAutomationWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ).replay_workflow(pending_history)
            # Restart the worker again with no workflow cache. Completion must
            # settle the B request restored from durable Activity history.
            state["complete"] = True
            async with Worker(
                env.client,
                task_queue=parent_queue,
                workflows=[MoonMindMergeAutomationWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
                max_cached_workflows=0,
            ):
                await env.sleep(timedelta(seconds=3))
                result = await asyncio.wait_for(handle.result(), timeout=30)
                history = await handle.fetch_history()
    assert result["status"] == "review_clean"
    first_cycle, second_cycle = result["reviewLoop"]["cycleRecords"]
    assert (
        first_cycle["status"] == "superseded"
        and first_cycle.get("completionId") is None
    )
    assert first_cycle["requestCommentId"] == 100
    assert second_cycle["requestCommentId"] == 102
    assert second_cycle["requestedAt"] == second["requestedAt"]
    assert second_cycle["completionId"] == 103 and second_cycle["status"] == "completed"
    assert requests[-1]["activeReviewRequest"]["requestCommentId"] == 102
    assert any(
        payload.get("reviewLoop", {}).get("cycleRecords", [])
        == result["reviewLoop"]["cycleRecords"]
        for _, payload in artifacts
    )
    assert observations[-1]["automatedReviewRequestCommentId"] == 102
    await Replayer(
        workflows=[MoonMindMergeAutomationWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)
