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
    def _review_adoption_blocker(self, evaluation: Any):
        return None

    def _reconcile_selected_review_request(self, evaluation: Any) -> None:
        # Capture the old consumer, including a rolling upgrade where the
        # Activity already emits the selected request fields.
        return None

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await super().run(payload)


@workflow.defn(name="MoonMind.MergeAutomation")
class _BeforeAdoptionBudget(MoonMindMergeAutomationWorkflow):
    def _selected_review_request_cycle_budget_enabled(
        self, observation_key: str
    ) -> bool:
        return False

    def _multiple_review_requests_enabled(self, observation_key: str) -> bool:
        # Published workers predate request receipts.
        return False

    def _review_adoption_blocker(self, evaluation: Any):
        return None

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await super().run(payload)


@workflow.defn(name="MoonMind.MergeAutomation")
class _BeforeMultipleReviewRequests(MoonMindMergeAutomationWorkflow):
    def _multiple_review_requests_enabled(self, observation_key: str) -> bool:
        # Record the consumer that already bounds the selected request, but
        # cannot retain intermediate requests from the same Activity poll.
        return False

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
@pytest.mark.parametrize(
    "max_cycles,old_adopted,outcome,multiple_requests",
    [
        *(
            (max_cycles, old_adopted, outcome, False)
            for max_cycles in (1, 2)
            for old_adopted in (False, True)
            for outcome in ("complete", "refusal")
        ),
        # Requests superseded between polls each consume a cycle.
        (5, False, "complete", True),
    ],
)
async def test_selected_request_survives_worker_upgrade_and_replay(
    monkeypatch, legacy_producer, max_cycles, old_adopted, outcome, multiple_requests
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
    second = {
        **first,
        "requestCommentId": 104 if multiple_requests else 102,
        "requestedAt": (
            "2026-08-24T22:04:00Z" if multiple_requests else "2026-08-24T22:02:00Z"
        ),
    }
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
                    "id": second["requestCommentId"],
                    "body": "@codex review",
                    "created_at": second["requestedAt"],
                },
            ]
            if multiple_requests:
                body.insert(
                    2,
                    {
                        "id": 102,
                        "body": "@codex review",
                        "created_at": "2026-08-24T22:02:00Z",
                    },
                )
            if state["complete"]:
                body.append(
                    {
                        "id": 105 if multiple_requests else 103,
                        "body": (
                            "Codex Review: Didn't find any major issues. 🚀"
                            if outcome == "complete"
                            else "You have reached your Codex usage limits for code reviews."
                        ),
                        "created_at": (
                            "2026-08-24T22:05:00Z"
                            if multiple_requests
                            else "2026-08-24T22:03:00Z"
                        ),
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
    # The gate reads with its owning run's admitted connection: the default,
    # unrecorded here, so the deployment declaration supplies the fixture token.
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "acquire_admitted_repository_use",
        AsyncMock(return_value=(None, None)),
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "load_admitted_repository_access",
        AsyncMock(return_value=("", False)),
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "load_repository_connection_for_launch",
        AsyncMock(return_value=None),
    )
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-token")

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
            "reviewLoop": {"enabled": True, "maxCycles": max_cycles},
        },
        "activeReviewRequest": first,
        "reviewCycles": [{"cycle": 1, **first, "status": "requested"}],
    }
    payload = MergeAutomationStartInput.model_validate(payload).model_dump(
        by_alias=True, mode="json"
    )
    parent_queue = (
        f"review-request-upgrade-{legacy_producer}-{max_cycles}-{old_adopted}"
        f"-{outcome}-{multiple_requests}"
    )
    child_queue = module.settings.temporal.user_workflow_v2_task_queue
    old_worker = _BeforeAdoptionBudget if old_adopted else _BeforeRequestReconciliation
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
        async with (
            Worker(
                env.client, task_queue=INTEGRATIONS_TASK_QUEUE, activities=[evaluate]
            ),
            Worker(
                env.client,
                task_queue=child_queue,
                workflows=[_CleanFixtureResolver],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
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
                workflows=[old_worker],
                workflow_runner=UnsandboxedWorkflowRunner(),
                max_cached_workflows=0,
            ):
                handle = await env.client.start_workflow(
                    old_worker.run,
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
                if max_cycles == 1 and not (old_adopted and not legacy_producer):
                    result = await asyncio.wait_for(handle.result(), timeout=30)
                    history = await handle.fetch_history()
                    assert result["status"] == "blocked"
                    assert any(
                        item["kind"] == "review_cycle_budget_exhausted"
                        for item in result["blockers"]
                    )
                    cycles = result["reviewLoop"]["cycleRecords"]
                    assert len(cycles) == (
                        2 if old_adopted and not legacy_producer else 1
                    )
                    assert all(item.get("completionId") is None for item in cycles)
                    assert result["resolverChildWorkflowIds"] == []
                    assert (
                        result["reviewLoop"]["activeRequest"]["requestCommentId"]
                        == cycles[-1]["requestCommentId"]
                    )
                    await Replayer(
                        workflows=[MoonMindMergeAutomationWorkflow],
                        workflow_runner=UnsandboxedWorkflowRunner(),
                    ).replay_workflow(history)
                    return
                pending_history = await wait_for_timer(2)
                pending = await handle.query(MoonMindMergeAutomationWorkflow.summary)
            assert (
                pending["reviewLoop"]["activeRequest"]["requestCommentId"]
                == second["requestCommentId"]
            )
            cycles = pending["reviewLoop"]["cycleRecords"]
            assert [cycle["status"] for cycle in cycles] == (
                ["superseded", "superseded", "requested"]
                if multiple_requests
                else ["superseded", "requested"]
            )
            assert [cycle["requestCommentId"] for cycle in cycles] == (
                [100, 102, 104] if multiple_requests else [100, 102]
            )
            restored = MergeAutomationStartInput.model_validate(
                {
                    **payload,
                    "activeReviewRequest": pending["reviewLoop"]["activeRequest"],
                    "reviewCycles": cycles,
                }
            )
            assert (
                restored.active_review_request.request_comment_id
                == second["requestCommentId"]
            )
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
    assert result["status"] == ("review_clean" if outcome == "complete" else "blocked")
    assert result["reviewLoop"]["activeRequest"] is None
    first_cycle, *_, second_cycle = result["reviewLoop"]["cycleRecords"]
    assert (
        first_cycle["status"] == "superseded"
        and first_cycle.get("completionId") is None
    )
    assert first_cycle["requestCommentId"] == 100
    assert second_cycle["requestCommentId"] == second["requestCommentId"]
    assert second_cycle["requestedAt"] == second["requestedAt"]
    assert second_cycle["status"] == (
        "completed" if outcome == "complete" else "failed"
    )
    assert second_cycle.get("completionId") == (
        (105 if multiple_requests else 103) if outcome == "complete" else None
    )
    if outcome == "refusal":
        assert result["resolverChildWorkflowIds"] == []
        assert result["blockers"][0]["kind"] == "automated_review_request_failed"
    assert (
        requests[-1]["activeReviewRequest"]["requestCommentId"]
        == second["requestCommentId"]
    )
    assert any(
        payload.get("reviewLoop", {}).get("cycleRecords", [])
        == result["reviewLoop"]["cycleRecords"]
        for _, payload in artifacts
    )
    assert (
        observations[-1]["automatedReviewRequestCommentId"]
        == second["requestCommentId"]
    )
    await Replayer(
        workflows=[MoonMindMergeAutomationWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


@workflow.defn(name="MoonMind.MergeAutomation")
class _BeforeSelectedRequestCycleBudget(MoonMindMergeAutomationWorkflow):
    def _selected_review_request_cycle_budget_enabled(
        self, observation_key: str
    ) -> bool:
        # Capture the deployed consumer that already records the selection
        # patch, but has not yet bounded externally posted requests.
        return False

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await super().run(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario", ["complete", "pending_complete", "pending_superseded"]
)
@pytest.mark.parametrize("multiple_requests", [False, True])
async def test_selected_request_cycle_budget_preserves_recorded_history(
    monkeypatch, tmp_path, scenario, multiple_requests
):
    first = {
        "provider": "codex",
        "headSha": "abcdef1",
        "requestKey": "recorded-request",
        "requestCommentId": 100,
        "requestedAt": "2026-08-24T22:00:00Z",
    }
    payload = {
        "workflowType": "MoonMind.MergeAutomation",
        "parentWorkflowId": "parent",
        "publishContextRef": "artifact://publish-context",
        "pullRequest": {
            "repo": "MoonLadderStudios/MoonMind",
            "number": 350,
            "url": "https://github.com/MoonLadderStudios/MoonMind/pull/350",
            "headSha": "abcdef1",
            "headBranch": "feature",
            "baseBranch": "main",
        },
        "mergeAutomationConfig": {
            "finishMode": "fix_only",
            "timeouts": {"fallbackPollSeconds": 2},
            "reviewLoop": {"enabled": True, "maxCycles": 3 if multiple_requests else 1},
        },
        "activeReviewRequest": first,
        "reviewCycles": [{"cycle": 1, **first, "status": "requested"}],
    }
    payload = MergeAutomationStartInput.model_validate(payload).model_dump(
        by_alias=True, mode="json"
    )
    first = payload["activeReviewRequest"]
    observations = []
    state = {"complete": scenario == "complete", "selected_id": 102}

    @activity.defn(name="merge_automation.evaluate_readiness")
    async def evaluate(_payload):
        observation = {
            "headSha": "abcdef1",
            "ready": state["complete"],
            "pullRequestOpen": True,
            "policyAllowed": True,
            "checksComplete": True,
            "checksPassing": True,
            "automatedReviewComplete": state["complete"],
            "automatedReviewRequestCommentId": state["selected_id"],
            "automatedReviewRequestedAt": (
                "2026-08-24T22:02:00Z"
                if state["selected_id"] == 102
                else (
                    "2026-08-24T22:06:00Z"
                    if multiple_requests
                    else "2026-08-24T22:04:00Z"
                )
            ),
            "automatedReviewCompletionKind": "issue_comment",
            "automatedReviewCompletionId": 103,
            "automatedReviewCompletedAt": "2026-08-24T22:05:00Z",
            "readinessObservationId": activity.info().activity_id,
            "jiraStatusAllowed": True,
        }
        if multiple_requests:
            identifiers = (
                [100, 101, 102] if state["selected_id"] == 102 else [102, 104, 106]
            )
            times = {
                100: first["requestedAt"],
                101: "2026-08-24T22:01:00Z",
                102: "2026-08-24T22:02:00Z",
                104: "2026-08-24T22:04:00Z",
                106: "2026-08-24T22:06:00Z",
            }
            observation["automatedReviewRequests"] = [
                {"requestCommentId": identifier, "requestedAt": times[identifier]}
                for identifier in identifiers
            ]
            if state["selected_id"] == 106:
                observation["automatedReviewCompletionId"] = 107
                observation["automatedReviewCompletedAt"] = "2026-08-24T22:07:00Z"
        if not state["complete"]:
            observation["blockers"] = [
                {
                    "kind": "automated_review_pending",
                    "summary": "Requested automated review has not completed.",
                    "retryable": True,
                    "source": "github",
                }
            ]
        observations.append(observation)
        return observation

    async def capture_artifact(self, *, name, payload):
        return None

    monkeypatch.setattr(
        MoonMindMergeAutomationWorkflow, "_write_json_artifact", capture_artifact
    )
    monkeypatch.setattr(
        MoonMindMergeAutomationWorkflow, "_publish_visibility", lambda self: None
    )
    parent_queue = f"review-request-budget-history-{scenario}-{multiple_requests}"
    previous_worker = (
        _BeforeMultipleReviewRequests
        if multiple_requests
        else _BeforeSelectedRequestCycleBudget
    )
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
        async with (
            Worker(
                env.client, task_queue=INTEGRATIONS_TASK_QUEUE, activities=[evaluate]
            ),
            Worker(
                env.client,
                task_queue=child_queue,
                workflows=[_CleanFixtureResolver],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
        ):
            async with Worker(
                env.client,
                task_queue=parent_queue,
                workflows=[previous_worker],
                workflow_runner=UnsandboxedWorkflowRunner(),
                max_cached_workflows=0,
            ):
                old = await env.client.start_workflow(
                    previous_worker.run,
                    payload,
                    id=f"before-selected-request-budget-{scenario}-{multiple_requests}",
                    task_queue=parent_queue,
                )
                if scenario == "complete":
                    old_result = await asyncio.wait_for(old.result(), timeout=30)
                    old_history = await old.fetch_history()
                else:
                    async with asyncio.timeout(15):
                        while True:
                            old_history = await old.fetch_history()
                            if any(
                                event.HasField("timer_started_event_attributes")
                                for event in old_history.events
                            ):
                                break
                            await asyncio.sleep(0.01)
                    old_result = await old.query(
                        MoonMindMergeAutomationWorkflow.summary
                    )
            (tmp_path / "before-budget-history.json").write_text(old_history.to_json())
            assert old_result["status"] == (
                "review_clean" if scenario == "complete" else "waiting"
            )
            assert old_result["reviewLoop"]["cycles"] == 2
            old_cycle = old_result["reviewLoop"]["cycleRecords"][1]
            assert old_cycle["requestCommentId"] == 102
            assert old_cycle["completionId"] == (
                103 if scenario == "complete" else None
            )
            marker_text = " ".join(
                payload.data.decode("utf-8")
                for event in old_history.events
                if event.HasField("marker_recorded_event_attributes")
                for details in event.marker_recorded_event_attributes.details.values()
                for payload in details.payloads
            )
            assert (
                module.MERGE_AUTOMATION_SELECTED_REVIEW_REQUEST_PATCH_PREFIX
                in marker_text
            )
            assert (
                module.MERGE_AUTOMATION_SELECTED_REVIEW_REQUEST_CYCLE_BUDGET_PATCH_PREFIX
                in marker_text
            ) is multiple_requests
            assert (
                module.MERGE_AUTOMATION_MULTIPLE_REVIEW_REQUESTS_PATCH_PREFIX
                not in marker_text
            )
            await Replayer(
                workflows=[MoonMindMergeAutomationWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ).replay_workflow(old_history)
            state["complete"] = True
            if scenario == "pending_superseded":
                state["selected_id"] = 106 if multiple_requests else 104
            async with Worker(
                env.client,
                task_queue=parent_queue,
                workflows=[MoonMindMergeAutomationWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
                max_cached_workflows=0,
            ):
                if scenario == "complete":
                    current = await env.client.start_workflow(
                        MoonMindMergeAutomationWorkflow.run,
                        payload,
                        id="selected-request-budget",
                        task_queue=parent_queue,
                    )
                else:
                    current = old
                    await env.sleep(timedelta(seconds=3))
                result = await asyncio.wait_for(current.result(), timeout=30)
                history = await current.fetch_history()
            (tmp_path / "current-budget-history.json").write_text(history.to_json())
    assert len(observations) == 2
    assert result["reviewLoop"]["cycles"] == (
        3
        if multiple_requests and scenario != "pending_complete"
        else 1 if scenario == "complete" else 2
    )
    if scenario == "pending_complete" or (multiple_requests and scenario == "complete"):
        assert result["status"] == "review_clean"
        assert result["reviewLoop"]["activeRequest"] is None
        assert result["reviewLoop"]["cycleRecords"][-1]["completionId"] == 103
    else:
        assert result["status"] == "blocked"
        assert [b["kind"] for b in result["blockers"]] == [
            "review_cycle_budget_exhausted"
        ]
        active = result["reviewLoop"]["activeRequest"]
        if scenario == "complete":
            assert active == first
        else:
            assert active["requestCommentId"] == (104 if multiple_requests else 102)
        assert result["reviewLoop"]["cycleRecords"][-1]["status"] == "requested"
        assert result["reviewLoop"]["cycleRecords"][-1].get("completionId") is None
        assert result["resolverChildWorkflowIds"] == []
    assert result["reviewLoop"]["cycleRecords"][0].get("completionId") is None
    await Replayer(
        workflows=[MoonMindMergeAutomationWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


@workflow.defn(name="MoonMind.MergeAutomation")
class _PublishedRefusalSettlement(MoonMindMergeAutomationWorkflow):
    def _review_failure_settlement_enabled(self, observation):
        # Published 227c12a8 workers had only the earlier refusal marker.
        return False

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await super().run(payload)


@workflow.defn(name="MoonMind.MergeAutomation")
class _BeforeRefusalSettlement(_PublishedRefusalSettlement):
    def _review_refusal_settlement_enabled(self, observation_key: str) -> bool:
        return False

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await super().run(payload)


@pytest.mark.asyncio
async def test_refusal_settlement_preserves_completed_history(monkeypatch, tmp_path):
    first = {
        "provider": "codex",
        "headSha": "abcdef1",
        "requestKey": "original-request",
        "requestCommentId": 100,
        "requestedAt": "2026-08-24T22:00:00Z",
    }
    payload = {
        "workflowType": "MoonMind.MergeAutomation",
        "parentWorkflowId": "parent",
        "publishContextRef": "artifact://publish-context",
        "pullRequest": {
            "repo": "MoonLadderStudios/MoonMind",
            "number": 350,
            "url": "https://github.com/MoonLadderStudios/MoonMind/pull/350",
            "headSha": "abcdef1",
            "headBranch": "feature",
            "baseBranch": "main",
        },
        "mergeAutomationConfig": {
            "finishMode": "fix_only",
            "reviewLoop": {"enabled": True},
        },
        "activeReviewRequest": first,
        "reviewCycles": [{"cycle": 1, **first, "status": "requested"}],
    }

    @activity.defn(name="merge_automation.evaluate_readiness")
    async def evaluate(_payload):
        result = {
            "headSha": "abcdef1",
            "pullRequestOpen": True,
            "ready": False,
            "automatedReviewComplete": None,
            "automatedReviewRequestCommentId": 100,
            "automatedReviewRequestedAt": first["requestedAt"],
            "readinessObservationId": activity.info().activity_id,
            "blockers": [
                {
                    "kind": "automated_review_request_failed",
                    "summary": "Provider refused review.",
                    "retryable": False,
                    "source": "codex",
                }
            ],
        }

        if label == "current":
            result["automatedReviewRequestFailure"] = {
                "kind": "issue_comment",
                "id": 101,
                "failedAt": "2026-08-24T22:01:00Z",
                "providerErrorClass": "rate_limit",
            }
            result["blockers"][0]["providerFailure"] = {
                "providerErrorClass": "rate_limit"
            }
        return result

    async def no_artifact(self, *, name, payload):
        return None

    monkeypatch.setattr(
        MoonMindMergeAutomationWorkflow, "_write_json_artifact", no_artifact
    )
    monkeypatch.setattr(
        MoonMindMergeAutomationWorkflow, "_publish_visibility", lambda self: None
    )
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=INTEGRATIONS_TASK_QUEUE, activities=[evaluate]
        ):
            results = []
            for label, worker_type in [
                ("before", _BeforeRefusalSettlement),
                ("published", _PublishedRefusalSettlement),
                ("current", MoonMindMergeAutomationWorkflow),
                ("missing", MoonMindMergeAutomationWorkflow),
            ]:
                queue = f"refusal-terminal-{label}"
                async with Worker(
                    env.client,
                    task_queue=queue,
                    workflows=[worker_type],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                    max_cached_workflows=0,
                ):
                    handle = await env.client.start_workflow(
                        worker_type.run,
                        payload,
                        id=queue,
                        task_queue=queue,
                        execution_timeout=timedelta(minutes=2),
                    )
                    result = await asyncio.wait_for(handle.result(), timeout=30)
                    history = await handle.fetch_history()
                (tmp_path / f"{label}-refusal-history.json").write_text(
                    history.to_json()
                )
                await Replayer(
                    workflows=[MoonMindMergeAutomationWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ).replay_workflow(history)
                results.append(result)
    before, published, current, missing = results
    assert before["status"] == current["status"] == "blocked"
    assert before["reviewLoop"]["activeRequest"]["requestCommentId"] == 100
    assert before["reviewLoop"]["cycleRecords"][-1]["status"] == "requested"
    assert current["reviewLoop"]["activeRequest"] is None
    assert current["reviewLoop"]["cycleRecords"][-1]["status"] == "failed"
    assert current["resolverChildWorkflowIds"] == []
    assert published["status"] == missing["status"] == "blocked"
    assert published["reviewLoop"]["activeRequest"] is None
    assert published["reviewLoop"]["cycleRecords"][-1]["status"] == "failed"
    assert published["reviewLoop"]["cycleRecords"][-1].get("requestFailure") is None
    assert current["reviewLoop"]["cycleRecords"][-1]["requestFailure"]["id"] == 101
    assert missing["reviewLoop"]["activeRequest"]["requestCommentId"] == 100
    assert missing["reviewLoop"]["cycleRecords"][-1]["status"] == "requested"
    assert "receipt" in missing["blockers"][0]["summary"]
