from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from temporalio import activity, workflow
from temporalio.api.enums.v1 import EventType, IndexedValueType
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
        if cycle > 1 and scenario != "repeated_wait":
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
        if scenario in {"new_timed", "repeated_wait"}:
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


@workflow.defn(name="MoonMind.UserWorkflow")
class _RecordedCIFailureResolver:
    @workflow.run
    async def run(self, _payload: dict[str, Any]) -> dict[str, Any]:
        # A resolver's claim still needs confirmation from the tracked PR.
        return {
            "status": "success",
            "mergeAutomationDisposition": "merged",
            "headSha": "abcdef1",
        }


@workflow.defn(name="MoonMind.MergeAutomation")
class _PreActionableCIFailureGate(MoonMindMergeAutomationWorkflow):
    def _actionable_ci_failures_enabled(self, evaluation: Any = None) -> bool:
        # Record the pre-fix worker's decision for failed-and-queued CI.
        return False

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await super().run(payload)


@workflow.defn(name="MoonMind.MergeAutomation")
class _PreMissingCIWaitGate(MoonMindMergeAutomationWorkflow):
    def _missing_ci_wait_enabled(self, evaluation: Any = None) -> bool:
        # A pre-feature consumer keeps polling even when a newer readiness
        # producer already reports the absence of head-bound CI explicitly.
        return False

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await super().run(payload)


@workflow.defn(name="MoonMind.UserWorkflow")
class _ReadinessFixtureResolver:
    @workflow.run
    async def run(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return await workflow.execute_activity(
            "qualification.merge_fixture",
            {},
            task_queue=workflow.info().task_queue,
            start_to_close_timeout=timedelta(seconds=10),
        )


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
            "reviewLoop": {"enabled": scenario == "repeated_wait", "maxConsecutiveNoProgressCycles": 2},
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
        ("repeated_wait", "blocked"),
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
    # Record the historical continuation sequence, then replay it with the
    # current workflow. Modern merge confirmation has its own HTTP journey.
    original_patched = module.workflow.patched
    monkeypatch.setattr(
        module.workflow,
        "patched",
        lambda name: (
            False
            if name == module.MERGE_AUTOMATION_RESOLVER_MERGE_CONFIRMATION_PATCH
            else original_patched(name)
        ),
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

    monkeypatch.setattr(module.workflow, "patched", original_patched)
    replayer = Replayer(
        workflows=[MoonMindMergeAutomationWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    await replayer.replay_workflow(history)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("versioned_producer", "explicit_failure"),
    [(False, True), (True, True), (True, False)],
    ids=["old-producer-failed-and-queued", "v1-producer-failed-and-queued", "v1-producer-queued-only"],
)
async def test_incomplete_ci_histories_replay_deterministically(
    monkeypatch: pytest.MonkeyPatch,
    versioned_producer: bool,
    explicit_failure: bool,
) -> None:
    should_wait = not (versioned_producer and explicit_failure)
    readiness_calls = 0
    initial_blockers = [
        {
            "kind": "checks_running",
            "summary": "Downstream test job is queued",
            "retryable": True,
            "source": "github",
        }
    ]
    if explicit_failure:
        initial_blockers.append(
            {
                "kind": "checks_failed",
                "summary": "Required test job failed",
                "retryable": True,
                "source": "github",
            }
        )

    @activity.defn(name="merge_automation.evaluate_readiness")
    async def evaluate_readiness(_payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal readiness_calls
        readiness_calls += 1
        version_evidence = (
            {
                "actionableCiFailuresVersion": "v1",
                "readinessObservationId": activity.info().activity_id,
            }
            if versioned_producer
            else {}
        )
        if readiness_calls == 1:
            return {
                **version_evidence,
                "headSha": "abcdef1",
                "ready": False,
                "pullRequestOpen": True,
                "policyAllowed": True,
                "checksComplete": False,
                "checksPassing": False,
                "blockers": initial_blockers,
            }
        if should_wait and readiness_calls == 2:
            return {
                **version_evidence,
                "headSha": "abcdef1",
                "ready": False,
                "pullRequestOpen": True,
                "policyAllowed": True,
                "checksComplete": True,
                "checksPassing": False,
                "blockers": [{"kind": "checks_failed", "summary": "Tests failed"}],
            }
        return {
            **version_evidence,
            "headSha": "fedcba2",
            "ready": False,
            "pullRequestOpen": False,
            "pullRequestMerged": True,
            "policyAllowed": True,
            "checksComplete": True,
            "checksPassing": True,
        }

    async def skip_artifact(
        self: MoonMindMergeAutomationWorkflow,
        *,
        name: str,
        payload: dict[str, Any],
    ) -> None:
        return None

    monkeypatch.setattr(
        MoonMindMergeAutomationWorkflow, "_write_json_artifact", skip_artifact
    )
    monkeypatch.setattr(
        MoonMindMergeAutomationWorkflow, "_publish_visibility", lambda self: None
    )
    child_queue = module.settings.temporal.user_workflow_v2_task_queue
    parent_queue = "mm-ci-failure-replay"
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
                activities=[evaluate_readiness],
            ),
            Worker(
                env.client,
                task_queue=child_queue,
                workflows=[_RecordedCIFailureResolver],
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
                _payload("ci_failure"),
                id=f"mm-ci-failure-{versioned_producer}-{explicit_failure}",
                task_queue=parent_queue,
                execution_timeout=timedelta(minutes=2),
            )
            result = await handle.result()
            history = await handle.fetch_history()

        observations = [
            (await env.client.data_converter.decode(
                event.activity_task_completed_event_attributes.result.payloads
            ))[0]
            for event in history.events
            if event.HasField("activity_task_completed_event_attributes")
        ]

    assert result["status"] == "merged"
    assert result["cycles"] == 1
    assert result["latestHeadSha"] == "fedcba2"
    assert readiness_calls == (3 if should_wait else 2)
    # Provider evidence remains unchanged in durable history even when its
    # actionable failure opens resolver scheduling ahead of downstream checks.
    assert observations[0]["blockers"] == initial_blockers
    assert observations[0]["checksComplete"] is False
    assert observations[0]["checksPassing"] is False
    assert observations[0].get("actionableCiFailuresVersion") == (
        "v1" if versioned_producer else None
    )
    assert bool(observations[0].get("readinessObservationId")) is versioned_producer
    assert observations[-1]["pullRequestMerged"] is True
    child_started = next(
        event.event_id
        for event in history.events
        if event.HasField("start_child_workflow_execution_initiated_event_attributes")
    )
    pre_child_timers = [
        event
        for event in history.events
        if event.HasField("timer_started_event_attributes")
        and event.event_id < child_started
    ]
    assert bool(pre_child_timers) is should_wait

    # The current implementation must replay both histories without forcing
    # the new branch into an old history or changing its recorded wait timer.
    await Replayer(
        workflows=[MoonMindMergeAutomationWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


@pytest.mark.asyncio
@pytest.mark.parametrize("historical_producer", ["old", "new"])
async def test_missing_ci_wait_is_bounded_after_fresh_producer_and_consumer_upgrade(
    monkeypatch: pytest.MonkeyPatch,
    historical_producer: str,
) -> None:
    from unittest.mock import AsyncMock

    import httpx

    from moonmind.workflows.adapters.github_service import GitHubService
    from moonmind.workflows.temporal.activity_runtime import (
        TemporalIntegrationActivities,
    )

    producer_upgraded = historical_producer == "new"
    readiness_requests: list[dict[str, Any]] = []
    producer_results: list[dict[str, Any]] = []
    readiness_activity_ids: list[str] = []
    http_requests: list[str] = []
    integration_activities = TemporalIntegrationActivities()

    def github_response(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.github.com"
        assert request.method == "GET"
        path = request.url.path
        http_requests.append(path)
        if path.endswith("/pulls/1209"):
            body = {
                "number": 1209,
                "state": "open",
                "merged": False,
                "head": {"sha": "abcdef1", "ref": "feature"},
                "base": {"sha": "base123", "ref": "main"},
            }
        elif path.endswith("/status"):
            body = {"state": "pending", "statuses": []}
        elif path.endswith("/check-runs"):
            body = {"check_runs": []}
        elif path.endswith("/branches/main"):
            body = {"protected": False}
        elif path.endswith("/reviews"):
            body = [
                {
                    "id": 123,
                    "state": "APPROVED",
                    "user": {"login": "reviewer"},
                    "submitted_at": "2026-10-03T00:00:00Z",
                    "commit_id": "abcdef1",
                }
            ]
        elif path.endswith("/reactions"):
            body = []
        else:
            raise AssertionError(f"Unexpected GitHub fixture request: {path}")
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(github_response)
    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        lambda **kwargs: client_type(transport=transport, **kwargs),
    )
    monkeypatch.setattr(
        GitHubService,
        "resolve_github_token",
        AsyncMock(return_value=("fixture-only", None)),
    )
    # The gate reads with its owning run's admitted connection. This retained
    # history predates a recorded connection, so model the deployment default
    # that owns the fixture token instead of consulting repository state.
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
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-only")

    @activity.defn(name="merge_automation.evaluate_readiness")
    async def evaluate_readiness(payload: dict[str, Any]) -> dict[str, Any]:
        readiness_requests.append(payload)
        readiness_activity_ids.append(activity.info().activity_id)
        evidence = await integration_activities.merge_automation_evaluate_readiness(
            payload
        )
        if not producer_upgraded:
            # Keep the already-supported observation identity and CI-failure
            # capability intact; only this new producer field is absent.
            evidence.pop("checksReported", None)
        producer_results.append(dict(evidence))
        return evidence

    async def skip_artifact(
        self: MoonMindMergeAutomationWorkflow,
        *,
        name: str,
        payload: dict[str, Any],
    ) -> None:
        return None

    monkeypatch.setattr(
        MoonMindMergeAutomationWorkflow, "_write_json_artifact", skip_artifact
    )
    monkeypatch.setattr(
        MoonMindMergeAutomationWorkflow, "_publish_visibility", lambda self: None
    )
    payload = _payload("missing_ci")
    # Omit reviewLoop and expiry: the supported defaults must bound this wait
    # even when no review loop or overall expiration has been opted into.
    payload["mergeAutomationConfig"] = {"timeouts": {"fallbackPollSeconds": 60}}
    parent_queue = "mm-missing-ci-upgrade"
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=INTEGRATIONS_TASK_QUEUE,
            activities=[evaluate_readiness],
        ):

            async def wait_for_poll_timer(expected_count: int):
                try:
                    async with asyncio.timeout(10):
                        while True:
                            history = await handle.fetch_history()
                            assert not any(
                                event.HasField(
                                    "start_child_workflow_execution_initiated_event_attributes"
                                )
                                or event.HasField(
                                    "workflow_execution_completed_event_attributes"
                                )
                                for event in history.events
                            ), "Unversioned observations must keep waiting without a resolver"
                            timer_count = sum(
                                event.HasField("timer_started_event_attributes")
                                for event in history.events
                            )
                            if timer_count >= expected_count:
                                assert timer_count == expected_count
                                assert len(producer_results) == expected_count
                                return history
                            await asyncio.sleep(0.01)
                except TimeoutError as exc:
                    history = await handle.fetch_history()
                    recent_events = [
                        (event.event_id, EventType.Name(event.event_type))
                        for event in history.events[-20:]
                    ]
                    raise AssertionError(
                        f"Expected {expected_count} durable poll timers; "
                        f"readiness calls={len(producer_results)}; "
                        f"recent events={recent_events}"
                    ) from exc

            async with Worker(
                env.client,
                task_queue=parent_queue,
                workflows=[_PreMissingCIWaitGate],
                workflow_runner=UnsandboxedWorkflowRunner(),
                max_cached_workflows=0,
            ):
                handle = await env.client.start_workflow(
                    _PreMissingCIWaitGate.run,
                    payload,
                    id=f"mm-missing-ci-upgrade-{historical_producer}",
                    task_queue=parent_queue,
                    execution_timeout=timedelta(minutes=30),
                )
                old_history = await wait_for_poll_timer(1)

            # A newly installed consumer must preserve the timer already
            # recorded by an old consumer, including with a new producer.
            await Replayer(
                workflows=[MoonMindMergeAutomationWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ).replay_workflow(old_history)

            async with Worker(
                env.client,
                task_queue=parent_queue,
                workflows=[MoonMindMergeAutomationWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
                max_cached_workflows=0,
            ):
                if historical_producer == "old":
                    # Three fresh polls exceed the default budget if absent
                    # checksReported were incorrectly treated as explicit False.
                    for expected_count in range(2, 5):
                        await env.sleep(timedelta(seconds=60))
                        neutral_history = await wait_for_poll_timer(expected_count)
                    await Replayer(
                        workflows=[MoonMindMergeAutomationWorkflow],
                        workflow_runner=UnsandboxedWorkflowRunner(),
                    ).replay_workflow(neutral_history)
                legacy_observation_count = len(producer_results)
                producer_upgraded = True
                # Ordinary durable polling supplies new Activity IDs; no
                # signal, expiry, or mutation of old history triggers recovery.
                result = await asyncio.wait_for(handle.result(), timeout=30)
                history = await handle.fetch_history()

        observations = [
            (
                await env.client.data_converter.decode(
                    event.activity_task_completed_event_attributes.result.payloads
                )
            )[0]
            for event in history.events
            if event.HasField("activity_task_completed_event_attributes")
        ]

    assert result["status"] == "blocked"
    assert result["cycles"] == 0
    assert result["resolverChildWorkflowIds"] == []
    assert result["latestHeadSha"] == "abcdef1"
    assert [blocker["kind"] for blocker in result["blockers"]] == [
        "review_loop_no_progress"
    ]
    assert "CI" in result["summary"]
    assert len(observations) == legacy_observation_count + 3
    assert len(producer_results) == len(observations)
    assert len(set(readiness_activity_ids)) == len(observations)
    for observation, activity_id in zip(observations, readiness_activity_ids):
        assert observation["readinessObservationId"] == activity_id
        assert observation["headSha"] == "abcdef1"
        assert observation["checksComplete"] is False
        assert observation["checksPassing"] is False
        assert {blocker["kind"] for blocker in observation["blockers"]} == {
            "checks_running"
        }
    if historical_producer == "old":
        assert legacy_observation_count == 4
        assert all("checksReported" not in item for item in observations[:4])
    else:
        assert legacy_observation_count == 1
        assert observations[0]["checksReported"] is False
    assert all(
        item["checksReported"] is False
        for item in observations[legacy_observation_count:]
    )
    assert all(
        request["pullRequest"] == payload["pullRequest"]
        and request["mergeAutomationConfig"]
        == readiness_requests[0]["mergeAutomationConfig"]
        for request in readiness_requests
    ), "Worker upgrades must preserve the admitted PR and continuation budgets"
    effective_config = readiness_requests[0]["mergeAutomationConfig"]
    assert effective_config["reviewLoop"]["enabled"] is False
    assert effective_config["reviewLoop"]["maxConsecutiveNoProgressCycles"] == 2
    assert effective_config["timeouts"].get("expireAfterSeconds") is None
    assert (
        sum(
            event.HasField("timer_started_event_attributes") for event in history.events
        )
        == legacy_observation_count + 2
    )
    assert not any(
        event.HasField("start_child_workflow_execution_initiated_event_attributes")
        or event.HasField("workflow_execution_signaled_event_attributes")
        for event in history.events
    )
    assert sum(path.endswith("/check-runs") for path in http_requests) == len(
        observations
    )
    assert sum(path.endswith("/status") for path in http_requests) == len(observations)
    await Replayer(
        workflows=[MoonMindMergeAutomationWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


@pytest.mark.asyncio
@pytest.mark.parametrize("review_state", ["complete", "pending", "unavailable"])
@pytest.mark.parametrize("historical_producer", ["old", "new"])
async def test_open_legacy_ci_wait_recovers_on_fresh_readiness_after_worker_upgrade(
    monkeypatch: pytest.MonkeyPatch,
    review_state: str,
    historical_producer: str,
) -> None:
    from unittest.mock import AsyncMock

    import httpx

    from moonmind.workflows.adapters.github_service import GitHubService
    from moonmind.workflows.temporal.activity_runtime import (
        TemporalIntegrationActivities,
    )

    state = {"merged": False, "upgraded": False}
    readiness_requests: list[dict[str, Any]] = []
    readiness_activity_ids: list[str] = []
    producer_results: list[dict[str, Any]] = []
    http_requests: list[str] = []
    fresh_readiness = asyncio.Event()
    integration_activities = TemporalIntegrationActivities()

    def github_response(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.github.com"
        assert request.method == "GET"
        path = request.url.path
        http_requests.append(path)
        if path.endswith("/pulls/1209"):
            body = {
                "number": 1209,
                "state": "closed" if state["merged"] else "open",
                "merged": state["merged"],
                "head": {"sha": "abcdef1", "ref": "feature"},
                "base": {"sha": "base123", "ref": "main"},
            }
        elif path.endswith("/status"):
            body = {"state": "success", "statuses": []}
        elif path.endswith("/check-runs"):
            body = {
                "check_runs": [
                    {
                        "id": 1,
                        "name": "required-tests",
                        "status": "completed",
                        "conclusion": "failure",
                    },
                    {
                        "id": 2,
                        "name": "downstream-tests",
                        "status": "queued",
                        "conclusion": None,
                    },
                ]
            }
        elif path.endswith("/branches/main"):
            body = {"protected": False}
        elif path.endswith("/reviews"):
            if len(readiness_requests) <= 2 and review_state == "unavailable":
                return httpx.Response(503, json={"message": "Review service unavailable"})
            if len(readiness_requests) <= 2 and review_state == "pending":
                body = []
            else:
                body = [
                    {
                        "id": 123,
                        "state": "APPROVED",
                        "user": {"login": "reviewer"},
                        "submitted_at": "2026-10-03T00:00:00Z",
                        "commit_id": "abcdef1",
                    }
                ]
        elif path.endswith("/reactions"):
            body = []
        else:
            raise AssertionError(f"Unexpected GitHub fixture request: {path}")
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(github_response)
    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        lambda **kwargs: client_type(transport=transport, **kwargs),
    )
    monkeypatch.setattr(
        GitHubService,
        "resolve_github_token",
        AsyncMock(return_value=("fixture-only", None)),
    )
    # The gate reads with its owning run's admitted connection: the default,
    # unrecorded here, so the deployment declaration supplies the fixture token.
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
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-only")

    @activity.defn(name="merge_automation.evaluate_readiness")
    async def evaluate_readiness(payload: dict[str, Any]) -> dict[str, Any]:
        readiness_requests.append(payload)
        readiness_activity_ids.append(activity.info().activity_id)
        # An old consumer may receive either producer during a rolling update.
        # Keep actual producer output intact except for the fields an old
        # producer did not emit before deployment.
        evidence = await integration_activities.merge_automation_evaluate_readiness(payload)
        if not state["upgraded"] and historical_producer == "old":
            evidence.pop("actionableCiFailuresVersion", None)
            evidence.pop("readinessObservationId", None)
        producer_results.append(dict(evidence))
        if state["upgraded"]:
            fresh_readiness.set()
        return evidence

    @activity.defn(name="qualification.merge_fixture")
    async def merge_fixture(_payload: dict[str, Any]) -> dict[str, Any]:
        assert producer_results[-1]["automatedReviewComplete"] is True
        assert producer_results[-1]["checksComplete"] is False
        assert producer_results[-1]["checksPassing"] is False
        state["merged"] = True
        return {
            "status": "success",
            "mergeAutomationDisposition": "merged",
            "headSha": "abcdef1",
        }

    async def skip_artifact(
        self: MoonMindMergeAutomationWorkflow,
        *,
        name: str,
        payload: dict[str, Any],
    ) -> None:
        return None

    monkeypatch.setattr(
        MoonMindMergeAutomationWorkflow, "_write_json_artifact", skip_artifact
    )
    monkeypatch.setattr(
        MoonMindMergeAutomationWorkflow, "_publish_visibility", lambda self: None
    )
    payload = _payload("legacy_ci_wait")
    child_queue = module.settings.temporal.user_workflow_v2_task_queue
    parent_queue = "mm-ci-failure-upgrade"
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
                activities=[evaluate_readiness],
            ),
            Worker(
                env.client,
                task_queue=child_queue,
                workflows=[_ReadinessFixtureResolver],
                activities=[merge_fixture],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
        ):
            async def wait_for_old_timer():
                try:
                    async with asyncio.timeout(10):
                        while True:
                            history = await handle.fetch_history()
                            assert not any(
                                event.HasField(
                                    "start_child_workflow_execution_initiated_event_attributes"
                                )
                                for event in history.events
                            )
                            if any(
                                event.HasField("timer_started_event_attributes")
                                for event in history.events
                            ):
                                return history
                            await asyncio.sleep(0.01)
                except TimeoutError as exc:
                    history = await handle.fetch_history()
                    recent_events = [
                        (event.event_id, EventType.Name(event.event_type))
                        for event in history.events[-20:]
                    ]
                    raise AssertionError(
                        f"Expected the old gate's timer; readiness calls={len(readiness_requests)}; "
                        f"recent events={recent_events}"
                    ) from exc

            async with Worker(
                env.client,
                task_queue=parent_queue,
                workflows=[_PreActionableCIFailureGate],
                workflow_runner=UnsandboxedWorkflowRunner(),
                # Every activation replays through the normal queue, avoiding
                # orphaned sticky dispatch when the test replaces its worker.
                max_cached_workflows=0,
            ):
                handle = await env.client.start_workflow(
                    _PreActionableCIFailureGate.run,
                    payload,
                    id=f"mm-open-legacy-ci-wait-{historical_producer}-{review_state}",
                    task_queue=parent_queue,
                    execution_timeout=timedelta(minutes=2),
                )
                old_history = await wait_for_old_timer()

            # A new producer's capability must not rewrite the old consumer's
            # already-recorded timer. Catch that replay boundary before a
            # fresh observation has a chance to admit the resolver.
            await Replayer(
                workflows=[MoonMindMergeAutomationWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ).replay_workflow(old_history)
            if historical_producer == "old":
                assert "actionableCiFailuresVersion" not in producer_results[0]
                assert "readinessObservationId" not in producer_results[0]
            else:
                assert producer_results[0]["actionableCiFailuresVersion"] == "v1"
                assert producer_results[0]["readinessObservationId"] == readiness_activity_ids[0]

            state["upgraded"] = True
            async with Worker(
                env.client,
                task_queue=parent_queue,
                workflows=[MoonMindMergeAutomationWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
                max_cached_workflows=0,
            ):
                # The existing durable poll is sufficient: no operator signal
                # or mutation of workflow history is needed after an upgrade.
                await env.sleep(timedelta(seconds=2))
                await asyncio.wait_for(fresh_readiness.wait(), timeout=10)
                assert producer_results[1].get("actionableCiFailuresVersion") == "v1"
                assert producer_results[1].get("readinessObservationId") == readiness_activity_ids[1]
                result = await asyncio.wait_for(handle.result(), timeout=30)
                history = await handle.fetch_history()

        observations = [
            (await env.client.data_converter.decode(
                event.activity_task_completed_event_attributes.result.payloads
            ))[0]
            for event in history.events
            if event.HasField("activity_task_completed_event_attributes")
            and event.activity_task_completed_event_attributes.result.payloads
        ]

    assert result["status"] == "merged"
    assert result["cycles"] == 1
    assert result["latestHeadSha"] == "abcdef1"
    assert len(result["resolverChildWorkflowIds"]) == 1
    expected_timers = 1 if review_state == "complete" else 2
    assert len(readiness_requests) == expected_timers + 2
    assert all(
        request["pullRequest"] == payload["pullRequest"]
        and request["mergeAutomationConfig"] == readiness_requests[0]["mergeAutomationConfig"]
        for request in readiness_requests
    ), "Automatic recovery must preserve the admitted PR and continuation budgets"
    if historical_producer == "old":
        assert "actionableCiFailuresVersion" not in observations[0]
        assert "readinessObservationId" not in observations[0]
    else:
        assert observations[0]["actionableCiFailuresVersion"] == "v1"
        assert observations[0]["readinessObservationId"] == readiness_activity_ids[0]
    assert observations[0]["checksComplete"] is False
    assert observations[0]["checksPassing"] is False
    assert {blocker["kind"] for blocker in observations[0]["blockers"]} >= {
        "checks_failed", "checks_running"
    }
    assert producer_results[1]["actionableCiFailuresVersion"] == "v1"
    assert readiness_activity_ids[1] != readiness_activity_ids[0]
    assert all(
        evidence["readinessObservationId"] == activity_id
        for evidence, activity_id in zip(producer_results[1:], readiness_activity_ids[1:])
    )
    if review_state != "complete":
        assert producer_results[1]["automatedReviewComplete"] is (
            False if review_state == "pending" else None
        )
        assert any(
            blocker["kind"] == (
                "automated_review_pending" if review_state == "pending" else "external_state_unavailable"
            )
            for blocker in producer_results[1]["blockers"]
        )
    assert producer_results[-1]["pullRequestMerged"] is True
    assert sum(
        event.HasField("timer_started_event_attributes") for event in history.events
    ) == expected_timers
    assert sum(
        event.HasField("start_child_workflow_execution_initiated_event_attributes")
        for event in history.events
    ) == 1
    assert not any(
        event.HasField("workflow_execution_signaled_event_attributes")
        for event in history.events
    )
    assert any(path.endswith("/reviews") for path in http_requests)
    assert sum(path.endswith("/pulls/1209") for path in http_requests) == len(readiness_requests)
    await Replayer(
        workflows=[MoonMindMergeAutomationWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)
