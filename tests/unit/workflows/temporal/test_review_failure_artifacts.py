"""Canonical review failures survive real workflow, artifact, and replay boundaries."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from temporalio import activity, workflow
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.schemas.temporal_models import (
    MergeAutomationStartInput,
    ReadinessBlockerModel,
)
from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.workflows.temporal.activity_catalog import (
    ARTIFACTS_TASK_QUEUE,
    INTEGRATIONS_TASK_QUEUE,
)
from moonmind.workflows.temporal.activity_runtime import TemporalIntegrationActivities
from moonmind.workflows.temporal.workflows.merge_automation import (
    MoonMindMergeAutomationWorkflow,
)
from moonmind.workflows.temporal.workflows.merge_gate import (
    build_continue_as_new_input,
    classify_readiness,
)
from tests.helpers.temporal_artifact_workers import artifact_workers


@workflow.defn(name="MoonMind.MergeAutomation")
class _BeforeFailureMetadata(MoonMindMergeAutomationWorkflow):
    async def _evaluate_readiness_once(self):
        evaluation, evidence, terminal = await super()._evaluate_readiness_once()
        # Old Activity payloads already carried this field; the old consumer
        # discarded it when rebuilding each typed blocker.
        evidence = evidence.model_copy(
            update={
                "blockers": [
                    b.model_copy(update={"provider_failure": None})
                    for b in evidence.blockers
                ]
            }
        )
        return evaluation, evidence, terminal

    @workflow.run
    async def run(self, payload):
        return await super().run(payload)


@workflow.defn(name="MoonMind.MergeAutomation")
class _BeforeFailureSettlement(MoonMindMergeAutomationWorkflow):
    def _review_failure_settlement_enabled(self, observation):
        return False

    @workflow.run
    async def run(self, payload):
        return await super().run(payload)


def _payload():
    first = {
        "provider": "codex",
        "headSha": "abcdef1",
        "requestKey": "retained-request",
        "requestCommentId": 100,
        "requestedAt": "2026-08-24T22:00:00Z",
    }
    return {
        "workflowType": "MoonMind.MergeAutomation",
        "parentWorkflowId": "parent",
        "principal": "system",
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
            "gate": {"github": {"checks": "disabled"}},
            "timeouts": {"fallbackPollSeconds": 2},
            "reviewLoop": {"enabled": True, "maxCycles": 2},
        },
        "activeReviewRequest": first,
        "reviewCycles": [{"cycle": 1, **first, "status": "requested"}],
    }


@pytest.mark.parametrize("metadata", [None, {}, {"rawLog": "not metadata"}])
def test_absent_failure_metadata_preserves_old_serialized_shape(metadata):
    old = {
        "kind": "external_state_unavailable",
        "summary": "Unavailable",
        "retryable": True,
        "source": "github",
    }
    model = ReadinessBlockerModel.model_validate({**old, "providerFailure": metadata})
    assert model.model_dump(by_alias=True) == old
    nested = MergeAutomationStartInput.model_validate(
        {**_payload(), "blockers": [model]}
    )
    assert nested.model_dump(by_alias=True)["blockers"] == [old]


def test_failure_metadata_is_compact_canonical_and_survives_continuation():
    failure = {
        "providerErrorClass": "rate_limit",
        "retryAfterSeconds": 23,
        "resetAt": "2026-08-25T00:00:00Z",
        "quotaScope": "repository",
        "rawLog": "private raw response",
        "reason": "private reason",
        "sanitizedSummary": "private forged summary",
        "providerRequestId": "password=do-not-echo " + "x" * 1000,
    }
    evidence = classify_readiness(
        {
            "headSha": "abcdef1",
            "pullRequestOpen": True,
            "blockers": [
                {
                    "kind": "external_state_unavailable",
                    "summary": "Unavailable",
                    "retryable": True,
                    "source": "github",
                    "providerFailure": failure,
                }
            ],
        },
        tracked_head_sha="abcdef1",
    )
    continuation = build_continue_as_new_input(
        start_input=_payload(),
        blockers=evidence.blockers,
        cycle_count=1,
        resolver_history=[],
        latest_head_sha="abcdef1",
        expire_at=None,
    )
    restored = MergeAutomationStartInput.model_validate(continuation)
    serialized = restored.model_dump(by_alias=True)
    metadata = serialized["blockers"][0]["providerFailure"]
    assert metadata["providerErrorClass"] == "rate_limit"
    assert metadata["providerErrorCode"] == "429"
    assert metadata["retryRecommendation"] == "retry_after_cooldown"
    assert metadata["retryAfterSeconds"] == 23
    assert metadata["resetAt"] == failure["resetAt"]
    assert metadata["quotaScope"] == "repository"
    assert len(metadata["providerRequestId"]) <= 500
    encoded = json.dumps(serialized)
    assert "private" not in encoded and "do-not-echo" not in encoded
    assert "rawLog" not in encoded and "reason" not in metadata


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "quota_status,finish",
    [
        (403, "refusal"),
        (429, "refusal"),
        (403, "expiry"),
        (429, "expiry"),
        (429, "legacy_refusal"),
        (429, "retired_cycle"),
    ],
)
async def test_review_failure_survives_artifact_persistence_and_worker_upgrade(
    tmp_path, monkeypatch, quota_status, finish
):
    state = {"refusal": finish == "legacy_refusal"}
    observations = []
    integration = TemporalIntegrationActivities()

    def respond(request):
        path = request.url.path
        if path.endswith("/pulls/350"):
            body = {"state": "open", "merged": False, "head": {"sha": "abcdef1"}}
        elif path.endswith("/issues/350/comments"):
            if not state["refusal"]:
                return httpx.Response(
                    quota_status,
                    json={"message": "private raw quota response"},
                    headers={
                        "Retry-After": "23",
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": "1790000000",
                    },
                )
            body = [
                {
                    "id": 100,
                    "body": "@codex review",
                    "created_at": "2026-08-24T22:00:00Z",
                },
                {
                    "id": 101,
                    "body": "You have reached your Codex usage limits for code reviews.",
                    "created_at": "2026-08-24T22:01:00Z",
                    "user": {"login": "chatgpt-codex-connector[bot]"},
                },
            ]
        elif path.endswith(("/reviews", "/reactions")):
            body = []
        else:
            raise AssertionError(path)
        return httpx.Response(200, json=body)

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )
    monkeypatch.setattr(
        GitHubService,
        "resolve_github_token",
        AsyncMock(return_value=("synthetic-token", None)),
    )
    monkeypatch.setattr(
        MoonMindMergeAutomationWorkflow, "_publish_visibility", lambda self: None
    )

    @activity.defn(name="merge_automation.evaluate_readiness")
    async def evaluate(payload):
        result = await integration.merge_automation_evaluate_readiness(payload)
        observations.append(result)
        return result

    async with (
        artifact_workers(tmp_path, monkeypatch) as artifacts,
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as env,
    ):
        queue = f"failure-metadata-{quota_status}-{finish}"
        payload = _payload()
        if finish == "retired_cycle":
            payload["reviewCycles"][0]["status"] = "superseded"
        payload["expireAt"] = (
            await env.get_current_time() + timedelta(seconds=30)
        ).isoformat()
        async with (
            Worker(
                env.client,
                task_queue=INTEGRATIONS_TASK_QUEUE,
                activities=[evaluate],
            ),
            Worker(
                env.client,
                task_queue=ARTIFACTS_TASK_QUEUE,
                activities=[
                    artifacts.bind("artifact.create"),
                    artifacts.bind("artifact.write_complete"),
                ],
            ),
        ):

            async def wait_for_timer(count):
                async with asyncio.timeout(15):
                    while True:
                        history = await handle.fetch_history()
                        if (
                            len(observations) >= count
                            and sum(
                                e.HasField("timer_started_event_attributes")
                                for e in history.events
                            )
                            >= count
                        ):
                            return history
                        await asyncio.sleep(0.01)

            old_workflow = (
                _BeforeFailureSettlement
                if finish in {"legacy_refusal", "retired_cycle"}
                else _BeforeFailureMetadata
            )
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[old_workflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
                max_cached_workflows=0,
            ):
                handle = await env.client.start_workflow(
                    old_workflow.run, payload, id=queue, task_queue=queue
                )
                if finish == "legacy_refusal":
                    old_result = await asyncio.wait_for(handle.result(), timeout=30)
                    old_history = await handle.fetch_history()
                else:
                    old_history = await wait_for_timer(1)
                old_pending = await handle.query(
                    MoonMindMergeAutomationWorkflow.summary
                )
            if finish == "retired_cycle":
                retained = old_pending["reviewLoop"]
                assert retained["cycleRecords"][0]["status"] == "superseded"
                await Replayer(
                    data_converter=pydantic_data_converter,
                    workflows=[MoonMindMergeAutomationWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ).replay_workflow(old_history)
                async with Worker(
                    env.client,
                    task_queue=queue,
                    workflows=[MoonMindMergeAutomationWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                    max_cached_workflows=0,
                ):
                    await env.sleep(timedelta(seconds=3))
                    retired_result = await asyncio.wait_for(handle.result(), timeout=30)
                    retired_history = await handle.fetch_history()
                assert retired_result["status"] == "blocked"
                assert retired_result["reviewLoop"] == retained
                saved = json.loads(
                    await artifacts.read(retired_result["artifactRefs"]["summary"])
                )
                assert saved["reviewLoop"] == retained
                await Replayer(
                    data_converter=pydantic_data_converter,
                    workflows=[MoonMindMergeAutomationWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ).replay_workflow(retired_history)
                return
            if finish == "legacy_refusal":
                assert old_result["status"] == "blocked"
                assert old_result["reviewLoop"]["activeRequest"] is not None
                assert (
                    old_result["reviewLoop"]["cycleRecords"][0]["status"] == "requested"
                )
                assert observations[0]["automatedReviewRequestFailure"]["id"] == 101
                old_saved = json.loads(
                    await artifacts.read(old_result["artifactRefs"]["summary"])
                )
                assert old_saved["reviewLoop"] == old_result["reviewLoop"]
                await Replayer(
                    data_converter=pydantic_data_converter,
                    workflows=[MoonMindMergeAutomationWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ).replay_workflow(old_history)
                async with Worker(
                    env.client,
                    task_queue=queue,
                    workflows=[MoonMindMergeAutomationWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                    max_cached_workflows=0,
                ):
                    replayed = await handle.query(
                        MoonMindMergeAutomationWorkflow.summary
                    )
                assert replayed["reviewLoop"] == old_result["reviewLoop"]
                return
            assert "providerFailure" in observations[0]["blockers"][0]
            assert "providerFailure" not in old_pending["blockers"][0]
            old_snapshot = json.loads(
                await artifacts.read(old_pending["artifactRefs"]["gateSnapshots"][-1])
            )
            assert "providerFailure" not in old_snapshot["summary"]["blockers"][0]
            await Replayer(
                data_converter=pydantic_data_converter,
                workflows=[MoonMindMergeAutomationWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ).replay_workflow(old_history)
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[MoonMindMergeAutomationWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
                max_cached_workflows=0,
            ):
                await env.sleep(timedelta(seconds=3))
                await wait_for_timer(2)
                pending = await handle.query(MoonMindMergeAutomationWorkflow.summary)
                metadata = pending["blockers"][0]["providerFailure"]
                assert metadata["retryAfterSeconds"] == 23
                assert metadata["resetAt"] == "2026-09-21T14:13:20+00:00"
                snapshot = json.loads(
                    await artifacts.read(pending["artifactRefs"]["gateSnapshots"][-1])
                )
                assert snapshot["summary"]["blockers"][0]["providerFailure"] == metadata
                state["refusal"] = finish == "refusal"
                result = await asyncio.wait_for(handle.result(), timeout=30)
                history = await handle.fetch_history()
        assert result["status"] == ("blocked" if finish == "refusal" else "expired")
        assert result["resolverChildWorkflowIds"] == []
        saved = json.loads(await artifacts.read(result["artifactRefs"]["summary"]))
        metadata = saved["blockers"][0]["providerFailure"]
        assert metadata == result["blockers"][0]["providerFailure"]
        assert metadata["providerErrorClass"] == "rate_limit"
        assert metadata["retryRecommendation"] == "retry_after_cooldown"
        if finish == "expiry":
            assert metadata["retryAfterSeconds"] == 23
            assert result["reviewLoop"]["activeRequest"] is not None
            assert result["reviewLoop"]["cycleRecords"][-1]["status"] == "requested"
        else:
            assert result["reviewLoop"]["activeRequest"] is None
            cycle = result["reviewLoop"]["cycleRecords"][-1]
            assert cycle["status"] == "failed"
            assert cycle["completionId"] is None
            assert cycle["requestFailure"] == {
                "kind": "issue_comment",
                "id": 101,
                "failedAt": "2026-08-24T22:01:00Z",
                "providerErrorClass": "rate_limit",
            }
            assert saved["reviewLoop"]["cycleRecords"] == [cycle]
            restored = MergeAutomationStartInput.model_validate(
                {**_payload(), "activeReviewRequest": None, "reviewCycles": [cycle]}
            )
            assert restored.model_dump(by_alias=True)["reviewCycles"][0] == cycle
        assert "private raw quota response" not in json.dumps(saved)
        await Replayer(
            data_converter=pydantic_data_converter,
            workflows=[MoonMindMergeAutomationWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ).replay_workflow(history)
