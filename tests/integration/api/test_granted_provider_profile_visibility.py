"""Grant-time identity survives child failure and worker replay in real Visibility."""

from __future__ import annotations

import asyncio
import shutil
from typing import Any
from uuid import uuid4

import pytest
from temporalio import workflow
from temporalio.client import WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.api.routers.executions import (
    _detect_optional_temporal_search_attributes,
    _provider_profile_facet_response,
)
from moonmind.workflows.executions.provider_profile_projection import (
    provider_profile_summary_from_memo,
)
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.workflows.agent_run import (
    AGENT_RUN_GRANTED_PROFILE_PROGRESS_PATCH_ID,
    MoonMindAgentRun,
)
from moonmind.workflows.temporal.workflows.run import (
    RUN_GRANTED_PROFILE_PROGRESS_PATCH,
    RUN_LAUNCH_PROVIDER_PROFILE_PROJECTION_PATCH,
    RUN_PAUSED_AGENT_PROGRESS_PATCH,
    MoonMindUserWorkflow,
)
from tests.helpers.temporal_visibility import register_deployment_search_attributes
from tests.integration.api.test_execution_list_provider_profile_visibility import (
    _listed_ids,
    _production_query,
    _start,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
_QUEUE = "provider-profile-grant-lifecycle"


@workflow.defn(name="MoonMind.AgentRun", sandboxed=False)
class _ControlledGrantedAgentRun:
    """Real production progress emitter, with controllable provider boundaries."""

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.emitter = MoonMindAgentRun()

    @workflow.signal
    def control(self, update: str) -> None:
        if update == "Pause":
            self.emitter.pause()
        else:
            self.emitter.resume()

    @workflow.query
    def paused(self) -> bool:
        return self.emitter._paused

    @workflow.signal
    def command(self, value: str) -> None:
        self.commands.append(value)

    @workflow.run
    async def run(self, _request: Any) -> dict[str, Any]:
        emitter = self.emitter
        emitter._progress_step_execution_id = "step-1"
        emitter._progress_generation = workflow.info().workflow_id
        emitter._profile_snapshots = {
            "work": {"account_label": "Frozen work"},
            "other": {"account_label": "Other account"},
        }
        parent = workflow.info().parent
        profile = "work"
        hold_grant = bool(_request.get("holdGrant"))
        if hold_grant:
            await emitter._signal_parent_child_state_changed(
                parent, "awaiting_slot", "Waiting for capacity"
            )
            await workflow.wait_condition(lambda: bool(self.commands))
            command = self.commands.pop(0)
            assert command == "grant"
        while True:
            emitter._assigned_profile_id = profile
            if workflow.patched(AGENT_RUN_GRANTED_PROFILE_PROGRESS_PATCH_ID):
                await emitter._signal_parent_granted_profile(parent, "codex_cli")
            else:
                await emitter._signal_parent_child_state_changed(
                    parent, "launching", "Slot acquired for codex_cli"
                )
            if emitter._paused:
                await workflow.wait_condition(lambda: not emitter._paused)
            if profile == "work" and not hold_grant:
                await emitter._signal_parent_child_state_changed(
                    parent, "awaiting_callback", "Waiting for provider callback"
                )
            await workflow.wait_condition(lambda: bool(self.commands))
            command = self.commands.pop(0)
            if command == "retry":
                await emitter._signal_parent_child_state_changed(
                    parent, "awaiting_slot", "Retrying with another profile"
                )
                profile = "other"
                continue
            if command == "error":
                raise ApplicationError(
                    "Provider failed after grant", non_retryable=True
                )
            return {
                "summary": "done",
                "metadata": {
                    "providerProfileId": profile,
                    "providerProfileLabel": "Changed after grant",
                },
            }


@pytest.fixture
def controlled_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    async def planning(self: MoonMindUserWorkflow, **kwargs: Any) -> str:
        return "artifact://plan/grant-lifecycle"

    async def execution(self: MoonMindUserWorkflow, **kwargs: Any) -> None:
        child_id = f"{workflow.info().workflow_id}:agent"
        self._active_agent_child_workflow_id = child_id
        result = await workflow.execute_child_workflow(
            "MoonMind.AgentRun",
            {"holdGrant": kwargs.get("parameters", {}).get("pauseGrant", False)},
            id=child_id,
            task_queue=_QUEUE,
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
        self._map_agent_run_result(result)

    async def no_op(self: MoonMindUserWorkflow, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(MoonMindUserWorkflow, "_run_planning_stage", planning)
    monkeypatch.setattr(MoonMindUserWorkflow, "_run_execution_stage", execution)
    monkeypatch.setattr(MoonMindUserWorkflow, "_run_finalizing_stage", no_op)
    monkeypatch.setattr(MoonMindUserWorkflow, "_record_terminal_state", no_op)


@pytest.mark.parametrize(
    "generation,outcome",
    [
        ("current", "success"),
        ("current", "cancel"),
        ("current", "error"),
        ("prior_projection", "success"),
        ("mixed_workers", "success"),
        ("historical", "success"),
    ],
)
async def test_granted_identity_visible_before_and_after_child_termination(
    controlled_stages: None,
    monkeypatch: pytest.MonkeyPatch,
    generation: str,
    outcome: str,
) -> None:
    original_patched = workflow.patched
    new_patches = {
        AGENT_RUN_GRANTED_PROFILE_PROGRESS_PATCH_ID,
        RUN_GRANTED_PROFILE_PROGRESS_PATCH,
    }
    if generation == "mixed_workers":
        new_patches.remove(AGENT_RUN_GRANTED_PROFILE_PROGRESS_PATCH_ID)
    if generation == "historical":
        new_patches.add(RUN_LAUNCH_PROVIDER_PROFILE_PROJECTION_PATCH)
    if generation != "current":
        monkeypatch.setattr(
            workflow,
            "patched",
            lambda name: False if name in new_patches else original_patched(name),
        )
    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=shutil.which("temporal")
    ) as env:
        await register_deployment_search_attributes(env)
        client = env.client
        usable = await _detect_optional_temporal_search_attributes(client)
        owner = str(uuid4())
        monkeypatch.setattr(
            MoonMindUserWorkflow,
            "_trusted_owner_metadata",
            lambda self: ("user", owner),
        )
        workflow_id = f"mm:grant-{uuid4().hex[:8]}"
        parameters = {"targetRuntime": "codex_cli"}
        await _start(
            TemporalClientAdapter(client),
            workflow_id=workflow_id,
            owner_id=owner,
            parameters=None if generation == "historical" else parameters,
            task_queue=_QUEUE,
            input_args={
                "workflowType": "MoonMind.UserWorkflow",
                "initialParameters": parameters,
            },
        )
        parent = client.get_workflow_handle(workflow_id)
        child = client.get_workflow_handle(f"{workflow_id}:agent")

        async def visible(params: list[tuple[str, str]]) -> list[str]:
            count_query, list_query = _production_query(
                params, owner_id=owner, usable_search_attributes=usable
            )
            ids = await _listed_ids(client, list_query)
            assert (await client.count_workflows(query=count_query)).count == len(ids)
            return ids

        async def wait_for_profile(profile: str) -> None:
            async with asyncio.timeout(30):
                while await visible([("providerProfileIdIn", profile)]) != [
                    workflow_id
                ]:
                    await asyncio.sleep(0.1)

        def worker() -> Worker:
            return Worker(
                client,
                task_queue=_QUEUE,
                workflows=[MoonMindUserWorkflow, _ControlledGrantedAgentRun],
                workflow_runner=UnsandboxedWorkflowRunner(),
            )

        async with worker():
            if generation == "current":
                await wait_for_profile("work")
                assert await visible([("providerProfileStateIn", "pending")]) == []
            else:
                # Ensure the child has recorded its bare progress before restart.
                async with asyncio.timeout(30):
                    while True:
                        try:
                            history = await child.fetch_history()
                            if any(
                                event.HasField(
                                    "external_workflow_execution_signaled_event_attributes"
                                )
                                for event in history.events
                            ):
                                break
                        except RPCError as exc:
                            assert exc.status == RPCStatusCode.NOT_FOUND
                        await asyncio.sleep(0.1)
                assert await visible(
                    [
                        (
                            "providerProfileStateIn",
                            "not_recorded" if generation == "historical" else "pending",
                        )
                    ]
                ) == [workflow_id]
            running = await parent.describe()
            assert running.status.name == "RUNNING"

        # A new worker must rebuild frozen profile and accepted progress state.
        async with worker():
            if generation == "current":
                await child.signal("command", "retry")
                await wait_for_profile("other")
                assert await visible([("providerProfileIdIn", "work")]) == [workflow_id]
            if outcome == "cancel":
                await child.cancel()
            else:
                await child.signal("command", outcome)
            try:
                await parent.result()
            except WorkflowFailureError:
                if outcome == "success":
                    raise
            described = await parent.describe()
            assert described.status.name != "RUNNING"
            parent_history = await parent.fetch_history()
            child_history = await child.fetch_history()
            child_status = (await child.describe()).status.name
            assert (
                child_status
                == {"success": "COMPLETED", "cancel": "CANCELED", "error": "FAILED"}[
                    outcome
                ]
            )

        memo = await described.memo()
        summary = provider_profile_summary_from_memo(memo)
        if generation == "historical":
            assert summary["selectionState"] == "not_recorded"
            assert await visible([("providerProfileStateIn", "not_recorded")]) == [
                workflow_id
            ]
        else:
            expected = ["work", "other"] if generation == "current" else ["work"]
            assert [entry["id"] for entry in summary["profiles"]] == expected
            assert summary["profileCount"] == len(expected)
            if generation == "current":
                assert summary["profiles"][0]["label"] == "Frozen work"
                assert summary["profiles"][1]["label"] == "Other account"
            await wait_for_profile(expected[-1])
            count_query, _ = _production_query(
                [], owner_id=owner, usable_search_attributes=usable
            )
            facets = await _provider_profile_facet_response(
                client=client,
                base_query=count_query,
                search_value=None,
                page_size=50,
                next_page_token=None,
            )
            assert {item.value: item.count for item in facets.items} == {
                profile: 1 for profile in expected
            }

        # Restore current code and replay actual prior/new histories, including
        # the old result-only projection feature and genuine no-projection data.
        monkeypatch.setattr(workflow, "patched", original_patched)
        replayer = Replayer(
            workflows=[MoonMindUserWorkflow, _ControlledGrantedAgentRun],
            workflow_runner=UnsandboxedWorkflowRunner(),
        )
        await replayer.replay_workflow(parent_history)
        await replayer.replay_workflow(child_history)


@pytest.mark.parametrize("retained", [False, True])
async def test_paused_grant_visibility_resume_and_replay(
    controlled_stages: None,
    monkeypatch: pytest.MonkeyPatch,
    retained: bool,
) -> None:
    original_patched = workflow.patched
    if retained:
        monkeypatch.setattr(
            workflow,
            "patched",
            lambda name: (
                False
                if name == RUN_PAUSED_AGENT_PROGRESS_PATCH
                else original_patched(name)
            ),
        )

    # The SDK test runtime has no workflow-to-workflow update transport. Use
    # a durable signal for that boundary; exercise the real parent controls,
    # child pause flag/wait, progress emitter, reducer, and Visibility writes.
    async def forward(self: MoonMindUserWorkflow, update: str) -> bool:
        if update == "Pause":
            # Cover a paused workflow with existing operator attention too.
            self._attention_required = True
        await workflow.get_external_workflow_handle(
            self._active_agent_child_workflow_id
        ).signal("control", update)
        return True

    monkeypatch.setattr(
        MoonMindUserWorkflow, "_forward_lifecycle_update_to_active_child", forward
    )
    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=shutil.which("temporal")
    ) as env:
        await register_deployment_search_attributes(env)
        client = env.client
        usable = await _detect_optional_temporal_search_attributes(client)
        owner = str(uuid4())
        monkeypatch.setattr(
            MoonMindUserWorkflow,
            "_trusted_owner_metadata",
            lambda self: ("user", owner),
        )
        workflow_id = f"mm:paused-grant-{uuid4().hex[:8]}"
        parameters = {"targetRuntime": "codex_cli", "pauseGrant": True}
        await _start(
            TemporalClientAdapter(client),
            workflow_id=workflow_id,
            owner_id=owner,
            parameters=parameters,
            task_queue=_QUEUE,
            input_args={
                "workflowType": "MoonMind.UserWorkflow",
                "initialParameters": parameters,
            },
        )
        parent = client.get_workflow_handle(workflow_id)
        child = client.get_workflow_handle(f"{workflow_id}:agent")

        def worker() -> Worker:
            return Worker(
                client,
                task_queue=_QUEUE,
                workflows=[MoonMindUserWorkflow, _ControlledGrantedAgentRun],
                workflow_runner=UnsandboxedWorkflowRunner(),
            )

        async with worker():
            async with asyncio.timeout(30):
                while (await parent.query("get_status"))[
                    "waiting_reason"
                ] != "provider_capacity":
                    await asyncio.sleep(0.1)
            await parent.execute_update("Pause")
            async with asyncio.timeout(30):
                while not await child.query("paused"):
                    await asyncio.sleep(0.1)
            before_grant = await parent.query("get_status")
            await child.signal("command", "grant")
            _count, query = _production_query(
                [("providerProfileIdIn", "work")],
                owner_id=owner,
                usable_search_attributes=usable,
            )
            async with asyncio.timeout(30):
                while await _listed_ids(client, query) != [workflow_id]:
                    await asyncio.sleep(0.1)
            assert await child.query("paused") is True
            after_grant = await parent.query("get_status")
            assert after_grant["paused"] is True
            if retained:
                assert after_grant["state"] == "executing"
            else:
                assert after_grant == before_grant

        # Replay the buffered accepted projection on a new worker, then drain
        # it only after Resume reaches the same child. No new child progress is
        # emitted on resume in this retained child behavior.
        async with worker():
            await parent.execute_update("Resume")
            assert (await parent.query("get_status"))["state"] == "executing"
            assert (await parent.query("get_status"))["waiting_reason"] is None
            assert (await (await parent.describe()).memo())["attention_required"] is False
            await child.signal("command", "success")
            await parent.result()
            history = await parent.fetch_history()
            child_history = await child.fetch_history()
        monkeypatch.setattr(workflow, "patched", original_patched)
        replayer = Replayer(
            workflows=[MoonMindUserWorkflow, _ControlledGrantedAgentRun],
            workflow_runner=UnsandboxedWorkflowRunner(),
        )
        await replayer.replay_workflow(history)
        await replayer.replay_workflow(child_history)
