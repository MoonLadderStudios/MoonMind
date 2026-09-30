"""Real Temporal journeys for Publish Saved Work (MoonLadderStudios/MoonMind#4018).

The saved-work branch of ``MoonMind.PublicationRecoveryV1`` runs on a
time-skipping Temporal server. Workers poll the production task queues with
the production bindings of the ``publication_recovery`` Activities only, so no
agent or model Activity could complete. The destination is a local bare Git
remote and the pull-request provider a recording fixture. The workflow worker
keeps no cache, so every workflow task replays the recorded history.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import pytest
from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.client import Client, WorkflowFailureError, WorkflowHandle
from temporalio.exceptions import CancelledError as TemporalCancelledError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.activity_catalog import (
    AGENT_RUNTIME_FLEET,
    ARTIFACTS_FLEET,
    INTEGRATIONS_FLEET,
    INTEGRATIONS_TASK_QUEUE,
    WORKFLOW_TASK_QUEUE,
    TemporalActivityCatalog,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalIntegrationActivities,
    build_activity_bindings,
)
from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.publication_recovery import (
    SavedWorkPublicationContract,
    saved_work_publication_workflow_id,
)
from moonmind.workflows.temporal.workflows.publication_recovery import (
    MoonMindPublicationRecoveryWorkflow,
)
from tests.support.saved_work_capture import git
from tests.unit.publish.test_saved_work_publication_journey import (  # noqa: F401
    Journey,
    _enforced_access,
    _write_destination,
    journey,
)

pytestmark = [pytest.mark.asyncio]

Hook = Callable[[int], Awaitable[None]]
_SCHEDULED = EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED


def _with_hooks(
    name: str, handler: Any, *, before: Hook | None, after: Hook | None
) -> Any:
    """Wrap a production handler; hooks see the Nth execution of the Activity."""
    executions = 0

    @activity.defn(name=name)
    async def wrapped(payload: Any = None) -> Any:
        nonlocal executions
        executions += 1
        if before is not None:
            await before(executions)
        result = await handler(payload)
        if after is not None:
            await after(executions)
        return result

    return wrapped


def _queues(
    state: Journey,
    after: dict[str, Hook] | None = None,
    *,
    before: dict[str, Hook] | None = None,
) -> dict[str, list]:
    """Production ``publication_recovery`` bindings grouped by task queue."""

    catalog = build_default_activity_catalog()
    bindings = build_activity_bindings(
        TemporalActivityCatalog(
            activities=tuple(
                item
                for item in catalog.activities
                if item.activity_type.startswith("publication_recovery.")
            ),
            fleets=catalog.fleets,
        ),
        artifact_activities=TemporalArtifactActivities(state.service),
        integration_activities=TemporalIntegrationActivities(),
        agent_runtime_activities=state.runtime,
        fleets=[AGENT_RUNTIME_FLEET, INTEGRATIONS_FLEET, ARTIFACTS_FLEET],
    )
    queues: dict[str, list] = {}
    for binding in bindings:
        handler = binding.handler
        name = binding.activity_type
        if name in (after or {}) or name in (before or {}):
            handler = _with_hooks(
                name,
                handler,
                before=(before or {}).get(name),
                after=(after or {}).get(name),
            )
        queues.setdefault(binding.task_queue, []).append(handler)
    return queues


@asynccontextmanager
async def _workers(
    client: Client, queues: dict[str, list], *, without: set[str] = frozenset()
):
    """One worker generation: the workflow worker plus the reachable Activity queues."""

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(
            Worker(
                client,
                task_queue=WORKFLOW_TASK_QUEUE,
                workflows=[MoonMindPublicationRecoveryWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
                max_cached_workflows=0,
            )
        )
        for queue, handlers in queues.items():
            if queue not in without:
                await stack.enter_async_context(
                    Worker(client, task_queue=queue, activities=handlers)
                )
        yield


async def _scheduled(handle: WorkflowHandle) -> list[str]:
    history = await handle.fetch_history()
    return [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.event_type == _SCHEDULED
    ]


async def _wait_until_scheduled(handle: WorkflowHandle, name: str) -> None:
    for _ in range(600):
        if name in await _scheduled(handle):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"{name} was never scheduled")


def _contract(state: Journey) -> tuple[dict[str, Any], str]:
    contract = state.contract(
        objective="pr", baseBranch="main", strategy="additive_import"
    )
    workflow_id = saved_work_publication_workflow_id(
        SavedWorkPublicationContract.model_validate(contract)
    )
    return contract, workflow_id


async def _persisted(state: Journey, handle: WorkflowHandle) -> dict[str, Any]:
    principal = f"workflow:{handle.id}"
    run_id = (await handle.describe()).run_id
    (artifact,) = [
        artifact
        for artifact in await state.service.list_for_execution(
            namespace=state.service._default_namespace,
            workflow_id=handle.id,
            run_id=run_id,
            principal=principal,
            link_type="result",
        )
        if (artifact.metadata_json or {}).get("name")
        == "saved-work-publication-result.json"
    ]
    _meta, payload = await state.service.read(
        artifact_id=artifact.artifact_id, principal=principal
    )
    return json.loads(payload)


async def test_worker_restart_between_push_and_pull_request_recovers_only_the_pr(
    tmp_path, monkeypatch
):
    async with journey(
        tmp_path,
        monkeypatch,
        destination_files={"README.md": "x\n"},
        emulate_temporal=False,
    ) as state:
        before = state.saved_bytes()
        contract, workflow_id = _contract(state)

        async def lose_first_acknowledgment(execution: int) -> None:
            if execution == 1:
                raise RuntimeError("worker lost after the push landed")

        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
        ) as env:
            # The first worker generation cannot reach the pull-request queue.
            async with _workers(
                env.client,
                _queues(
                    state,
                    {"publication_recovery.saved_work_push": lose_first_acknowledgment},
                ),
                without={INTEGRATIONS_TASK_QUEUE},
            ):
                handle = await env.client.start_workflow(
                    MoonMindPublicationRecoveryWorkflow.run,
                    contract,
                    id=workflow_id,
                    task_queue=WORKFLOW_TASK_QUEUE,
                )
                await _wait_until_scheduled(
                    handle, "publication_recovery.saved_work_pull_request"
                )
            head = git(state.remote, "rev-parse", "refs/heads/saved/work")
            assert len(state.pushes()) == 1 and state.provider.creates == []

            # A fresh generation replays the history and completes only the PR.
            async with _workers(env.client, _queues(state)):
                result = await handle.result()
            scheduled = await _scheduled(handle)

        assert result["outcome"] == "published"
        assert result["push"]["status"] == "reconciled"
        assert result["candidate"]["headSha"] == head
        assert result["pullRequest"]["status"] == "created"
        assert git(state.remote, "rev-parse", "refs/heads/saved/work") == head
        assert len(state.pushes()) == 1
        assert len(state.provider.creates) == 1
        # The workflow starts each push attempt; the second one reconciles.
        assert scheduled == [
            "publication_recovery.saved_work_prepare",
            "publication_recovery.saved_work_push",
            "publication_recovery.saved_work_push",
            "publication_recovery.saved_work_pull_request",
            "publication_recovery.persist_result",
            "publication_recovery.cleanup",
        ]
        assert state.saved_objects_unchanged(before)
        assert await state.use_claims() == []


async def test_cancellation_during_push_records_the_landed_push_and_stops(
    tmp_path, monkeypatch
):
    async with journey(
        tmp_path,
        monkeypatch,
        destination_files={"README.md": "x\n"},
        emulate_temporal=False,
    ) as state:
        contract, workflow_id = _contract(state)
        landed, release = asyncio.Event(), asyncio.Event()

        async def hold_acknowledgment(execution: int) -> None:
            landed.set()
            await release.wait()

        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
        ) as env:
            async with _workers(
                env.client,
                _queues(
                    state, {"publication_recovery.saved_work_push": hold_acknowledgment}
                ),
            ):
                handle = await env.client.start_workflow(
                    MoonMindPublicationRecoveryWorkflow.run,
                    contract,
                    id=workflow_id,
                    task_queue=WORKFLOW_TASK_QUEUE,
                )
                await asyncio.wait_for(landed.wait(), timeout=60)
                await handle.cancel()
                # Let the workflow observe the cancellation before the push reports.
                await asyncio.sleep(1)
                release.set()
                with pytest.raises(WorkflowFailureError) as failure:
                    await handle.result()
            persisted = await _persisted(state, handle)
            scheduled = await _scheduled(handle)

        assert isinstance(failure.value.cause, TemporalCancelledError)
        assert persisted["outcome"] == "cancelled"
        assert persisted["push"]["status"] == "pushed"
        assert persisted["push"]["remoteHeadSha"] == git(
            state.remote, "rev-parse", "refs/heads/saved/work"
        )
        assert "publication_recovery.saved_work_pull_request" not in scheduled
        assert scheduled[-2:] == [
            "publication_recovery.persist_result",
            "publication_recovery.cleanup",
        ]
        assert state.provider.creates == []
        assert await state.use_claims() == []


async def test_resubmission_after_a_terminated_run_completes_only_the_pr(
    tmp_path, monkeypatch
):
    async with journey(
        tmp_path,
        monkeypatch,
        destination_files={"README.md": "x\n"},
        emulate_temporal=False,
    ) as state:
        before = state.saved_bytes()
        contract, workflow_id = _contract(state)
        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
        ) as env:
            async with _workers(
                env.client, _queues(state), without={INTEGRATIONS_TASK_QUEUE}
            ):
                first = await env.client.start_workflow(
                    MoonMindPublicationRecoveryWorkflow.run,
                    contract,
                    id=workflow_id,
                    task_queue=WORKFLOW_TASK_QUEUE,
                )
                await _wait_until_scheduled(
                    first, "publication_recovery.saved_work_pull_request"
                )
                # The run ends after its push without any terminal record.
                await first.terminate(reason="operator stopped a stuck publication")
            head = git(state.remote, "rev-parse", "refs/heads/saved/work")
            _write_destination(tmp_path, state.remote, {"later.txt": "moved\n"})

            async with _workers(env.client, _queues(state)):
                second = await env.client.start_workflow(
                    MoonMindPublicationRecoveryWorkflow.run,
                    contract,
                    id=workflow_id,
                    task_queue=WORKFLOW_TASK_QUEUE,
                )
                result = await second.result()

        assert result["outcome"] == "published"
        assert result["candidate"]["headSha"] == head
        assert result["push"]["status"] == "reconciled"
        assert result["pullRequest"]["status"] == "created"
        assert git(state.remote, "rev-parse", "refs/heads/saved/work") == head
        assert len(state.pushes()) == 1
        assert len(state.provider.creates) == 1
        assert state.saved_objects_unchanged(before)
        assert await state.use_claims() == []


async def test_cancellation_after_a_failed_push_attempt_starts_no_new_attempt(
    tmp_path, monkeypatch
):
    async with journey(
        tmp_path,
        monkeypatch,
        destination_files={"README.md": "x\n"},
        emulate_temporal=False,
    ) as state:
        contract, workflow_id = _contract(state)
        failed = asyncio.Event()

        async def fail_before_pushing(execution: int) -> None:
            # Only the first attempt fails; any later attempt would push.
            if execution == 1:
                failed.set()
                raise RuntimeError("transient failure before any push")

        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
        ) as env:
            async with _workers(
                env.client,
                _queues(
                    state,
                    before={"publication_recovery.saved_work_push": fail_before_pushing},
                ),
            ):
                handle = await env.client.start_workflow(
                    MoonMindPublicationRecoveryWorkflow.run,
                    contract,
                    id=workflow_id,
                    task_queue=WORKFLOW_TASK_QUEUE,
                )
                await asyncio.wait_for(failed.wait(), timeout=60)
                await handle.cancel()
                with pytest.raises(WorkflowFailureError) as failure:
                    await handle.result()
            persisted = await _persisted(state, handle)
            scheduled = await _scheduled(handle)

        assert isinstance(failure.value.cause, TemporalCancelledError)
        assert state.pushes() == []
        assert scheduled.count("publication_recovery.saved_work_push") == 1
        assert "publication_recovery.saved_work_pull_request" not in scheduled
        assert persisted["outcome"] == "cancelled"
        # The failed attempt is unconfirmed and keeps its original error.
        assert persisted["push"] == {"status": "unconfirmed", "reasonCode": "RuntimeError"}
        assert scheduled[-2:] == [
            "publication_recovery.persist_result",
            "publication_recovery.cleanup",
        ]
        assert await state.use_claims() == []
