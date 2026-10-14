"""Recorded attribution survives an unversioned fleet with old pollers."""

import asyncio
import subprocess
from contextlib import AsyncExitStack
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.config.settings import settings
from moonmind.omnigent.git_identity import publication_git_identity_projection
from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

_SUFFIX = ".publication-v1"
# Collection precedes the unit suite's lifecycle guard. Exercise the real
# adapter against this explicitly owned test server, without lifting the guard
# for other tests or connecting to the deployment.
_START_WORKFLOW = TemporalClientAdapter.start_workflow
_ADMISSION = {"publicationGitIdentityAdmission": {
    "schemaVersion": "v1",
    "identity": {"name": "Admitted", "email": "admitted@example.invalid"},
}}


def _commit(path, env):
    workspace = Path(path)
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    (workspace / "saved.txt").write_text("verified work\n")
    subprocess.run(["git", "-C", path, "add", "saved.txt"], env=env, check=True)
    subprocess.run(["git", "-C", path, "commit", "-qm", "Save work"], env=env, check=True)
    return subprocess.check_output(
        ["git", "-C", path, "log", "-1", "--format=%an <%ae>"], text=True,
    ).strip()


@activity.defn(name="publication_identity_routing_probe")
async def old_publisher(path: str) -> dict:
    # The retained caller does not read admission; this is the published old
    # publisher's settings-only environment path, exercised with real Git.
    env = TemporalAgentRuntimeActivities._workspace_command_env(path)
    return {"author": _commit(path, env), "consumer": "old"}


@activity.defn(name="publication_identity_routing_probe")
async def current_publisher(path: str) -> dict:
    identity = await publication_git_identity_projection()
    env = TemporalAgentRuntimeActivities._workspace_command_env(
        path, publication_git_identity=identity,
    )
    return {"author": _commit(path, env), "consumer": "current"}


@workflow.defn
class IdentityRoutingChild:
    @workflow.run
    async def run(self, request: dict) -> dict:
        route = build_default_activity_catalog().resolve_activity(request["activityType"])
        kwargs = (
            MoonMindAgentRun._execute_kwargs_for_route(route)
            if route.fleet == "agent_runtime"
            else MoonMindRunWorkflow()._execute_kwargs_for_route(route)
        )
        if request.get("noCurrentConsumer"):
            from datetime import timedelta

            kwargs.update(start_to_close_timeout=timedelta(seconds=1),
                          schedule_to_close_timeout=timedelta(seconds=1))
        result = await workflow.execute_activity(
            "publication_identity_routing_probe", request["path"], **kwargs,
        )
        return {**result, "childQueue": workflow.info().task_queue}


@workflow.defn
class IdentityRoutingRoot:
    @workflow.run
    async def run(self, request: dict) -> dict:
        parent = MoonMindRunWorkflow()
        result = await workflow.execute_child_workflow(
            IdentityRoutingChild.run, request,
            id=f"{workflow.info().workflow_id}-child",
            task_queue=parent._workflow_child_task_queue(),
            memo=parent._publication_identity_memo(),
        )
        return {**result, "rootQueue": workflow.info().task_queue}


@pytest.mark.asyncio
@pytest.mark.parametrize("admitted", [False, True])
@pytest.mark.parametrize("merge_lane", [False, True])
@pytest.mark.parametrize("activity_type", ["agent_runtime.fetch_result", "sandbox.checkout_repo"])
async def test_unversioned_old_pollers_cannot_consume_admitted_publication(
    tmp_path, monkeypatch, admitted, merge_lane, activity_type,
):
    monkeypatch.setattr(settings.workflow, "git_user_name", "Changed")
    monkeypatch.setattr(settings.workflow, "git_user_email", "changed@example.invalid")
    base = settings.temporal.merge_automation_workflow_task_queue if merge_lane else settings.temporal.user_workflow_v2_task_queue
    activity_queue = build_default_activity_catalog().resolve_activity(activity_type).task_queue
    workflow_id = f"identity-routing-{uuid4()}"
    async with await WorkflowEnvironment.start_time_skipping() as env, AsyncExitStack() as stack:
        for queue in (base, base + _SUFFIX):
            await stack.enter_async_context(Worker(
                env.client, task_queue=queue,
                workflows=[IdentityRoutingRoot, IdentityRoutingChild],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ))
        await stack.enter_async_context(Worker(
            env.client, task_queue=activity_queue, activities=[old_publisher],
        ))
        await stack.enter_async_context(Worker(
            env.client, task_queue=activity_queue + _SUFFIX, activities=[current_publisher],
        ))
        adapter = TemporalClientAdapter()
        adapter._client = env.client
        await _START_WORKFLOW(adapter,
            workflow_type="IdentityRoutingRoot", workflow_id=workflow_id,
            input_args={"path": str(tmp_path / "repo"), "activityType": activity_type}, task_queue=base,
            memo=_ADMISSION if admitted else {},
        )
        handle = env.client.get_workflow_handle(workflow_id)
        result = await asyncio.wait_for(handle.result(), timeout=30)
        root_history = await handle.fetch_history()
        child_history = await env.client.get_workflow_handle(workflow_id + "-child").fetch_history()

    if admitted:
        assert result["author"] == "Admitted <admitted@example.invalid>"
        assert result["consumer"] == "current"
        assert result["rootQueue"] == result["childQueue"] == base + _SUFFIX
    else:
        assert result["author"] == "Changed <changed@example.invalid>"
        assert result["consumer"] == "old"
        assert result["rootQueue"] == result["childQueue"] == base
    for history in (root_history, child_history):
        await Replayer(
            workflows=[IdentityRoutingRoot, IdentityRoutingChild],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ).replay_workflow(history)


def test_worker_topology_polls_identity_contract_and_retained_queues():
    fleets = {fleet.fleet: fleet for fleet in build_default_activity_catalog().fleets}
    for fleet in ("workflow", "agent_runtime", "sandbox"):
        queues = fleets[fleet].task_queues
        original = [queue for queue in queues if not queue.endswith(_SUFFIX)]
        assert original
        for queue in original:
            assert queue + _SUFFIX in queues


@pytest.mark.asyncio
@pytest.mark.parametrize("admitted", [False, True])
@pytest.mark.parametrize("operation", ["create", "rebuild"])
async def test_recurring_action_uses_recorded_identity_queue(admitted, operation):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    client = MagicMock()
    client.create_schedule = AsyncMock(return_value=SimpleNamespace(id="saved-schedule"))
    adapter = TemporalClientAdapter(client=client)
    adapter._workflow_topology = SimpleNamespace(task_queues=["custom.workflow"])
    memo = _ADMISSION if admitted else {}
    definition_id = uuid4()
    if operation == "create":
        await adapter.create_schedule(
            definition_id=definition_id, cron_expression="0 0 * * *",
            workflow_type="IdentityRoutingRoot", memo=memo,
        )
        action = client.create_schedule.call_args.args[1].action
    else:
        handle = MagicMock()
        handle.describe = AsyncMock()
        handle.update = AsyncMock()
        client.get_schedule_handle.return_value = handle
        await adapter.update_schedule(
            definition_id=definition_id, workflow_type="IdentityRoutingRoot", memo=memo,
        )
        schedule = SimpleNamespace()
        update = await handle.update.call_args.args[0](
            SimpleNamespace(description=SimpleNamespace(schedule=schedule)),
        )
        action = update.schedule.action
    assert action.task_queue == "custom.workflow" + (_SUFFIX if admitted else "")
    assert action.memo == memo


@pytest.mark.asyncio
async def test_missing_capable_activity_worker_never_falls_back_to_old_poller(tmp_path):
    from temporalio.client import WorkflowFailureError

    base = settings.temporal.user_workflow_v2_task_queue
    activity_queue = build_default_activity_catalog().resolve_activity("agent_runtime.fetch_result").task_queue
    workflow_id = f"identity-no-fallback-{uuid4()}"
    async with await WorkflowEnvironment.start_time_skipping() as env, AsyncExitStack() as stack:
        await stack.enter_async_context(Worker(
            env.client, task_queue=base + _SUFFIX,
            workflows=[IdentityRoutingRoot, IdentityRoutingChild],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ))
        await stack.enter_async_context(Worker(
            env.client, task_queue=activity_queue, activities=[old_publisher],
        ))
        adapter = TemporalClientAdapter(client=env.client)
        await _START_WORKFLOW(adapter,
            workflow_type="IdentityRoutingRoot", workflow_id=workflow_id,
            task_queue=base, memo=_ADMISSION,
            input_args={"path": str(tmp_path / "repo"), "activityType": "agent_runtime.fetch_result",
                        "noCurrentConsumer": True},
        )
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(env.client.get_workflow_handle(workflow_id).result(), timeout=15)
        history = await env.client.get_workflow_handle(workflow_id + "-child").fetch_history()
    scheduled = next(event.activity_task_scheduled_event_attributes for event in history.events
                     if event.HasField("activity_task_scheduled_event_attributes"))
    assert scheduled.task_queue.name == activity_queue + _SUFFIX
    assert any(event.HasField("activity_task_timed_out_event_attributes") for event in history.events)
    assert not (tmp_path / "repo").exists()
