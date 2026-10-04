"""Retained idle histories and durable capacity waits use the production owner."""

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from temporalio import activity, workflow
from temporalio.api.enums.v1 import EventType
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal import activity_catalog
from moonmind.workflows.temporal.workflows import run as run_module
from tests.helpers.temporal_visibility import register_deployment_search_attributes

_PATCH_ID = "run-issue-search-capacity-retry-v1"
_TOOL_NAME = "github.load_issue_preset_brief"
_PROBE_ACTIVITY = "capacity_retry_replay.search"
_NODE_ID = "select-issue"
_CAPACITY_EVIDENCE = {"runtimeId": "codex_cli", "profileId": "selected-profile"}


def _capacity_result() -> dict[str, Any]:
    return {
        "status": "COMPLETED",
        "completion_disposition": "idle",
        "outputs": {
            "repository": "example/repo",
            "reasonCode": "local_capacity_unavailable",
            "summary": "Selected provider profile is busy. No issue was claimed.",
            "capacityEvidence": dict(_CAPACITY_EVIDENCE),
        },
    }


def _search_payload() -> dict[str, Any]:
    info = workflow.info()
    return {
        "principal": "operator",
        "invocation_payload": {
            "id": _NODE_ID,
            "tool": {"type": "skill", "name": _TOOL_NAME},
            "inputs": {"repository": "example/repo", "issueSearch": "is:open"},
        },
        "context": {
            "namespace": info.namespace,
            "workflow_id": info.workflow_id,
            "run_id": info.run_id,
            "node_id": _NODE_ID,
            "runtime_selection": {
                "targetRuntime": "codex_cli",
                "profileId": "selected-profile",
            },
        },
        "idempotency_key": f"{info.workflow_id}:select-issue:execute",
    }


def _parent() -> run_module.MoonMindRunWorkflow:
    parent = run_module.MoonMindRunWorkflow()
    parent._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": _NODE_ID,
                "tool": {"type": "skill", "name": _TOOL_NAME},
                "inputs": {"repository": "example/repo", "issueSearch": "is:open"},
            }
        ],
        dependency_map={_NODE_ID: []},
        updated_at=workflow.now(),
    )
    parent._mark_step_running(_NODE_ID, updated_at=workflow.now())
    return parent


def _route() -> activity_catalog.TemporalActivityRoute:
    return activity_catalog.TemporalActivityRoute(
        activity_type=_PROBE_ACTIVITY,
        task_queue=workflow.info().task_queue,
        fleet=activity_catalog.INTEGRATIONS_FLEET,
        capability_class="integration:github",
        timeouts=activity_catalog.TemporalActivityTimeouts(10, 30),
        retries=activity_catalog.TemporalActivityRetries(1, 30),
    )


@workflow.defn(name="IssueSearchCapacityRetryReplay")
class _LegacyCapacitySearch:
    @workflow.run
    async def run(self) -> dict[str, Any]:
        try:
            _parent()
            return await workflow.execute_activity(
                _PROBE_ACTIVITY,
                _search_payload(),
                start_to_close_timeout=timedelta(seconds=10),
            )
        except Exception as exc:
            raise ApplicationError(str(exc), non_retryable=True) from exc


@workflow.defn(name="IssueSearchCapacityRetryReplay")
class _CurrentCapacitySearch:
    def __init__(self) -> None:
        self.parent: run_module.MoonMindRunWorkflow | None = None

    @workflow.run
    async def run(self) -> dict[str, Any]:
        try:
            self.parent = _parent()
            self.parent._issue_search_capacity_retry_enabled = workflow.patched(
                _PATCH_ID
            )
            payload = _search_payload()
            initial = await workflow.execute_activity(
                _PROBE_ACTIVITY,
                payload,
                start_to_close_timeout=timedelta(seconds=10),
            )
            return await self.parent._wait_for_issue_search_capacity(
                execution_result=initial,
                node_id=_NODE_ID,
                tool_name=_TOOL_NAME,
                route=_route(),
                execute_payload=payload,
            )
        except Exception as exc:
            # A fixture programming error should fail the test instead of
            # retrying its Workflow Task forever.
            raise ApplicationError(str(exc), non_retryable=True) from exc

    @workflow.query
    def capacity_state(self) -> str:
        return self.parent._state if self.parent is not None else "starting"

    @workflow.query
    def capacity_retry_enabled(self) -> bool | None:
        return (
            self.parent._issue_search_capacity_retry_enabled
            if self.parent is not None
            else None
        )


class _CapacityProbe:
    def __init__(self, *, recover: bool, defer_first: bool = False) -> None:
        self.recover = recover
        self.defer_first = defer_first
        self.calls: list[dict[str, Any]] = []
        self.started = asyncio.Event()
        self.deferred_token: bytes | None = None

    @activity.defn(name=_PROBE_ACTIVITY)
    async def search(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(
            {"payload": payload, "startedAt": activity.info().started_time}
        )
        if self.defer_first and len(self.calls) == 1:
            self.deferred_token = activity.info().task_token
            self.started.set()
            activity.raise_complete_async()
        if self.recover and len(self.calls) == 3:
            return {
                "status": "COMPLETED",
                "outputs": {
                    "repository": "example/repo",
                    "issue": {"number": 3970},
                    "summary": "Selected example/repo#3970.",
                },
            }
        return _capacity_result()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "recover", [True, False], ids=["capacity-recovers", "budget-exhausted"]
)
async def test_production_capacity_wait_survives_restart_and_replays_legacy_history(
    recover: bool,
) -> None:
    assert hasattr(
        run_module.MoonMindRunWorkflow, "_wait_for_issue_search_capacity"
    ), "Production Run workflow must own bounded pre-selection capacity recovery"
    assert run_module.RUN_ISSUE_SEARCH_CAPACITY_RETRY_PATCH == _PATCH_ID
    histories = []
    async with await asyncio.wait_for(
        WorkflowEnvironment.start_time_skipping(), timeout=30
    ) as env:
        await asyncio.wait_for(register_deployment_search_attributes(env), timeout=30)
        legacy_probe = _CapacityProbe(recover=recover, defer_first=True)
        async with Worker(
            env.client,
            task_queue="capacity-replay-legacy",
            workflows=[_LegacyCapacitySearch],
            activities=[legacy_probe.search],
            workflow_runner=UnsandboxedWorkflowRunner(),
            max_cached_workflows=0,
        ):
            legacy = await env.client.start_workflow(
                _LegacyCapacitySearch.run,
                id=f"capacity-replay-legacy-{recover}",
                task_queue="capacity-replay-legacy",
            )
            await asyncio.wait_for(legacy_probe.started.wait(), timeout=5)
            active_legacy_history = await asyncio.wait_for(
                legacy.fetch_history(), timeout=10
            )
            assert not any(
                event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED
                for event in active_legacy_history.events
            )
            histories.append(active_legacy_history)

        # Complete the original Activity after the legacy worker stops. The
        # upgraded worker must preserve the already active owner's old branch.
        async with Worker(
            env.client,
            task_queue="capacity-replay-legacy",
            workflows=[_CurrentCapacitySearch],
            activities=[legacy_probe.search],
            workflow_runner=UnsandboxedWorkflowRunner(),
            max_cached_workflows=0,
        ):
            assert (
                await asyncio.wait_for(
                    legacy.query(_CurrentCapacitySearch.capacity_retry_enabled),
                    timeout=10,
                )
            ) is False
            assert legacy_probe.deferred_token is not None
            await asyncio.wait_for(
                env.client.get_async_activity_handle(
                    task_token=legacy_probe.deferred_token
                ).complete(_capacity_result()),
                timeout=10,
            )
            assert (
                await asyncio.wait_for(legacy.result(), timeout=30)
            ) == _capacity_result()
            histories.append(await asyncio.wait_for(legacy.fetch_history(), timeout=10))
        assert len(legacy_probe.calls) == 1

        probe = _CapacityProbe(recover=recover)
        worker_args = {
            "task_queue": "capacity-replay-current",
            "workflows": [_CurrentCapacitySearch],
            "activities": [probe.search],
            "workflow_runner": UnsandboxedWorkflowRunner(),
            "max_cached_workflows": 0,
        }
        async with Worker(env.client, **worker_args):
            current = await env.client.start_workflow(
                _CurrentCapacitySearch.run,
                id=f"capacity-replay-current-{recover}",
                task_queue="capacity-replay-current",
            )
            async with asyncio.timeout(10):
                while True:
                    state = await current.query(_CurrentCapacitySearch.capacity_state)
                    if state == "awaiting_slot":
                        break
                    await asyncio.sleep(0.05)
            waiting_history = await asyncio.wait_for(
                current.fetch_history(), timeout=10
            )
            assert await asyncio.wait_for(
                current.query(_CurrentCapacitySearch.capacity_retry_enabled), timeout=10
            )
            assert any(
                event.event_type == EventType.EVENT_TYPE_TIMER_STARTED
                for event in waiting_history.events
            )
            assert len(probe.calls) == 1

        # Restart the worker while the parent is durably waiting, preserving
        # its exact owner and the pending timer rather than starting a run.
        async with Worker(env.client, **worker_args):
            result = await asyncio.wait_for(current.result(), timeout=30)
            histories.append(
                await asyncio.wait_for(current.fetch_history(), timeout=10)
            )

    initial_payload = probe.calls[0]["payload"]
    for index, call in enumerate(probe.calls[1:], start=1):
        payload = call["payload"]
        assert payload["context"] == initial_payload["context"]
        assert payload["invocation_payload"] == initial_payload["invocation_payload"]
        assert payload["idempotency_key"] == (
            f"{initial_payload['idempotency_key']}_capacity_recheck_{index}"
        )
    elapsed = (
        probe.calls[-1]["startedAt"] - probe.calls[0]["startedAt"]
    ).total_seconds()
    if recover:
        assert result["status"] == "COMPLETED"
        assert result["outputs"]["issue"]["number"] == 3970
        assert len(probe.calls) == 3
        assert elapsed == pytest.approx(90, abs=1)
    else:
        assert result["status"] == "FAILED"
        assert result["outputs"]["reasonCode"] == "local_capacity_unavailable"
        assert result["outputs"]["error"] == "RESOURCE_EXHAUSTED"
        assert result["outputs"]["capacityEvidence"] == _CAPACITY_EVIDENCE
        assert "issue" not in result["outputs"]
        assert elapsed == pytest.approx(1800, abs=1)
    for legacy_history in histories[:-1]:
        assert not any(
            event.event_type == EventType.EVENT_TYPE_TIMER_STARTED
            for event in legacy_history.events
        )
    replayer = Replayer(
        workflows=[_CurrentCapacitySearch],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    for history in histories:
        await asyncio.wait_for(replayer.replay_workflow(history), timeout=30)
