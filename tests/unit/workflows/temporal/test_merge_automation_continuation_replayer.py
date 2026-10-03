from __future__ import annotations

import asyncio
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
    def _actionable_ci_failures_enabled(self) -> bool:
        # Record a pre-fix worker's wait without introducing the new marker.
        return False

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await super().run(payload)


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
    ("actionable_ci_failure_patch", "explicit_failure"),
    [(False, True), (True, True), (True, False)],
    ids=["legacy-failed-and-queued", "failed-and-queued", "queued-only"],
)
async def test_incomplete_ci_histories_replay_deterministically(
    monkeypatch: pytest.MonkeyPatch,
    actionable_ci_failure_patch: bool,
    explicit_failure: bool,
) -> None:
    should_wait = not (actionable_ci_failure_patch and explicit_failure)
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
        if readiness_calls == 1:
            return {
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
                "headSha": "abcdef1",
                "ready": False,
                "pullRequestOpen": True,
                "policyAllowed": True,
                "checksComplete": True,
                "checksPassing": False,
                "blockers": [{"kind": "checks_failed", "summary": "Tests failed"}],
            }
        return {
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
    original_patched = module.workflow.patched
    if not actionable_ci_failure_patch:
        # Omit the new marker to record the command sequence of an old worker.
        monkeypatch.setattr(
            module.workflow,
            "patched",
            lambda name: (
                False
                if name == module.MERGE_AUTOMATION_ACTIONABLE_CI_FAILURE_PATCH
                else original_patched(name)
            ),
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
                id=f"mm-ci-failure-{actionable_ci_failure_patch}-{explicit_failure}",
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
    monkeypatch.setattr(module.workflow, "patched", original_patched)
    await Replayer(
        workflows=[MoonMindMergeAutomationWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


@pytest.mark.asyncio
async def test_open_legacy_ci_wait_reconciles_after_worker_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ordinary_or_malformed_signals = [
        {"source": "github", "event": "check_run"},
        {"action": "reconcile_known_ci_failure"},
        {
            "schemaVersion": "merge-automation-reconcile/v1",
            "action": "unknown",
        },
        {
            "schemaVersion": "merge-automation-reconcile/v0",
            "action": "reconcile_known_ci_failure",
        },
    ]
    readiness_requests: list[dict[str, Any]] = []
    failed_observations = 2 + len(ordinary_or_malformed_signals)

    @activity.defn(name="merge_automation.evaluate_readiness")
    async def evaluate_readiness(payload: dict[str, Any]) -> dict[str, Any]:
        readiness_requests.append(payload)
        if len(readiness_requests) <= failed_observations:
            return {
                "headSha": "abcdef1",
                "ready": False,
                "pullRequestOpen": True,
                "policyAllowed": True,
                "checksComplete": False,
                "checksPassing": False,
                "blockers": [
                    {
                        "kind": "checks_failed",
                        "summary": "Required test job failed",
                        "source": "github",
                    },
                    {
                        "kind": "checks_running",
                        "summary": "Downstream test job is queued",
                        "source": "github",
                    },
                ],
            }
        return {
            "headSha": "abcdef1",
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
    payload = _payload("legacy_ci_wait")
    # Poll only on signals during the upgrade; no real-time timer race should
    # release the old wait while its worker is stopped and recreated.
    payload["mergeAutomationConfig"]["timeouts"]["fallbackPollSeconds"] = 3600
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
                workflows=[_RecordedCIFailureResolver],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
        ):
            async def wait_for_gate_timer(expected_count: int):
                async with asyncio.timeout(10):
                    while True:
                        history = await handle.fetch_history()
                        assert not any(
                            event.HasField(
                                "start_child_workflow_execution_initiated_event_attributes"
                            )
                            for event in history.events
                        ), "An ordinary signal must not opt an old gate into CI remediation"
                        timer_count = sum(
                            event.HasField("timer_started_event_attributes")
                            for event in history.events
                        )
                        if timer_count >= expected_count:
                            return history
                        await asyncio.sleep(0.01)

            async with Worker(
                env.client,
                task_queue=parent_queue,
                workflows=[_PreActionableCIFailureGate],
                workflow_runner=UnsandboxedWorkflowRunner(),
                # A restarted test worker must get the task before the
                # assertion deadline, rather than waiting on the old sticky
                # queue's default ten-second ScheduleToStart timeout.
                sticky_queue_schedule_to_start_timeout=timedelta(seconds=1),
            ):
                handle = await env.client.start_workflow(
                    _PreActionableCIFailureGate.run,
                    payload,
                    id="mm-open-legacy-ci-wait",
                    task_queue=parent_queue,
                    execution_timeout=timedelta(hours=2),
                )
                old_history = await wait_for_gate_timer(1)
                assert (
                    module.MERGE_AUTOMATION_ACTIONABLE_CI_FAILURE_PATCH
                    not in old_history.to_json()
                )

            # A fresh worker replays the open history. The SDK remembers the
            # absent patch as False; only explicit reconciliation may override
            # that historical decision on a future signal-driven evaluation.
            async with Worker(
                env.client,
                task_queue=parent_queue,
                workflows=[MoonMindMergeAutomationWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
                sticky_queue_schedule_to_start_timeout=timedelta(seconds=1),
            ):
                for timer_count, signal_payload in enumerate(
                    ordinary_or_malformed_signals, start=2
                ):
                    await handle.signal(
                        MoonMindMergeAutomationWorkflow.external_event, signal_payload
                    )
                    await wait_for_gate_timer(timer_count)

            # Both requests are durably recorded before a worker can dispatch
            # the resolver, so duplicate delivery cannot race terminal close.
            for _ in range(2):
                await handle.signal(
                    MoonMindMergeAutomationWorkflow.external_event,
                    {
                        "schemaVersion": "merge-automation-reconcile/v1",
                        "action": "reconcile_known_ci_failure",
                    },
                )
            async with Worker(
                env.client,
                task_queue=parent_queue,
                workflows=[MoonMindMergeAutomationWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
                sticky_queue_schedule_to_start_timeout=timedelta(seconds=1),
            ):
                result = await asyncio.wait_for(handle.result(), timeout=30)
                history = await handle.fetch_history()

    assert result["status"] == "merged"
    assert result["cycles"] == 1
    assert result["latestHeadSha"] == "abcdef1"
    assert len(result["resolverChildWorkflowIds"]) == 1
    assert len(readiness_requests) == failed_observations + 1
    assert all(
        request["pullRequest"] == payload["pullRequest"]
        and request["mergeAutomationConfig"] == readiness_requests[0]["mergeAutomationConfig"]
        for request in readiness_requests
    ), "Reconciliation must preserve the admitted PR and continuation budgets"
    assert sum(
        event.HasField("timer_started_event_attributes") for event in history.events
    ) == 1 + len(ordinary_or_malformed_signals)
    assert sum(
        event.HasField("start_child_workflow_execution_initiated_event_attributes")
        for event in history.events
    ) == 1
    await Replayer(
        workflows=[MoonMindMergeAutomationWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)
