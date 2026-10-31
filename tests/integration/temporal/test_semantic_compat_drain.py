"""Retained semantic adapters drain through real Temporal history observations."""

import asyncio
import json
from contextlib import AsyncExitStack
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio import workflow
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.skills.approval_policy import step_gate_payload
from moonmind.workflows.temporal.activity_catalog import get_workflow_child_task_queue
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.workflows.run import (
    RUN_ACCOUNT_INTENT_NORMALIZATION_PATCH,
    RUN_EMPTY_AGENT_SKILLSET_SNAPSHOT_PATCH,
    RUN_STEP_PROFILE_SELECTION_INHERITANCE_PATCH,
)
from tests.helpers.temporal_visibility import register_deployment_search_attributes
from tests.integration.temporal.test_linked_recovery_retained_history import (
    FakeActivities,
    _activity_workers,
    _start,
    _workflow_worker,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.integration_ci,
    pytest.mark.temporal_boundary,
]

FAMILIES = {"gate_aliases", "profile_selection", "native_pr_branches"}


def _receipt(name, client, result, *, handle=None):
    path = Path("artifacts/remediate-4990/observations")
    path.mkdir(parents=True, exist_ok=True)
    payload = {
        "boundary": "TemporalClientAdapter.observe_semantic_compat_drain",
        "topology": "isolated Temporal local server; real Visibility and run-pinned history reads",
        "namespace": client.namespace,
        "observations": {
            family: {**asdict(row), "mayRemove": row.may_remove}
            for family, row in result.items()
        },
    }
    if handle:
        payload["workflowId"] = handle.id
        payload["runId"] = handle.first_execution_run_id
        payload["workload"] = "MoonMind.UserWorkflow"
    (path / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n")


async def _observe(client):
    result = await TemporalClientAdapter(client=client).observe_semantic_compat_drain()
    assert set(result) == FAMILIES
    return result


async def test_complete_empty_observation_permits_removal_eligibility():
    async with await WorkflowEnvironment.start_local() as env:
        result = await _observe(env.client)
        assert all(row.may_remove for row in result.values())
        assert all(row.supported_resets == 0 for row in result.values())
        _receipt("empty", env.client, result)


async def test_real_queued_start_retains_every_adapter_without_starting_work():
    async with await WorkflowEnvironment.start_local(
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
    ) as env:
        await register_deployment_search_attributes(env)
        handle = await _start(env, f"unpolled-{uuid4()}", {}, admitted=True)
        # Starting a workflow does not wait for its Visibility index entry.
        # Observe the known consumer before testing the drain classification.
        async with asyncio.timeout(10):
            while not [
                row
                async for row in env.client.list_workflows(
                    query=f'WorkflowId="{handle.id}" AND ExecutionStatus="Running"'
                )
            ]:
                await asyncio.sleep(0.05)
        result = await _observe(env.client)
        assert all(row.queued_starts == 1 for row in result.values())
        assert all(not row.may_remove for row in result.values())
        _receipt("queued", env.client, result, handle=handle)
        history = await handle.fetch_history()
        assert not any(
            e.HasField("workflow_task_completed_event_attributes") for e in history.events
        )
        await handle.terminate("isolated drain test complete")


async def test_production_workflow_saved_alias_and_reset_consumers_retain_support():
    fake = FakeActivities(hold_finish=True)
    # A saved result is admitted to the real parent and remains in its start
    # history across worker loss and supported reset. No synthetic history or
    # scripted Visibility listing supplies the observer's consumer identities.
    parameters = {"previousOutputs": {"verification": {"verdict": "BLOCKED"}}}
    async with (
        await WorkflowEnvironment.start_local(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
        ) as env,
        AsyncExitStack() as stack,
    ):
        await register_deployment_search_attributes(env)
        await _activity_workers(stack, env.client, fake)
        async with _workflow_worker(env.client, f"semantic-drain-{uuid4()}") as worker:
            handle = await _start(env, worker.task_queue, parameters, admitted=True)
            await asyncio.wait_for(fake.finish_started.wait(), 30)
            active = await _observe(env.client)
            assert all(not row.may_remove for row in active.values())
            assert active["gate_aliases"].saved_inputs == 1
            assert active["gate_aliases"].open_histories == 1
            _receipt("active-saved", env.client, active, handle=handle)
            fake.release_finish.set()
            assert (await asyncio.wait_for(handle.result(), 30))["status"] == "success"
        closed = await _observe(env.client)
        assert all(row.supported_resets == 1 for row in closed.values())
        assert all(not row.may_remove for row in closed.values())
        _receipt("reset", env.client, closed, handle=handle)
        assert fake.steps == ["prepare", "implement", "finish"]
        # Observation never closes a workflow, deletes a history or reruns work.
        assert (await handle.fetch_history()).events


async def test_canonical_completed_work_drains_gate_aliases_independently():
    fake = FakeActivities()
    parameters = {"previousOutputs": {"moonSpecVerify": {"verdict": "BLOCKED"}}}
    async with (
        await WorkflowEnvironment.start_local(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
        ) as env,
        AsyncExitStack() as stack,
    ):
        await register_deployment_search_attributes(env)
        await _activity_workers(stack, env.client, fake)
        async with _workflow_worker(env.client, f"canonical-drain-{uuid4()}") as worker:
            handle = await _start(env, worker.task_queue, parameters, admitted=True)
            assert (await asyncio.wait_for(handle.result(), 30))["status"] == "success"
        result = await _observe(env.client)
        assert result["gate_aliases"].may_remove
        assert not result["profile_selection"].may_remove
        assert not result["native_pr_branches"].may_remove
        _receipt("canonical", env.client, result, handle=handle)


@pytest.mark.parametrize("saved_outputs", [
    {"verification": {"verdict": "BLOCKED"}},
    {"moonSpecVerify": {}, "verdict": "BLOCKED"},
    {"moonSpecVerify": {"summary": "Saved diagnostics"}, "gateVerdict": "BLOCKED"},
    {"moonSpecVerify": {"verdict": ""}, "verification_verdict": "BLOCKED"},
], ids=["legacy-container", "empty-canonical", "diagnostics-only", "blank-verdict"])
async def test_paused_saved_schedule_retains_alias_decoder_until_repaired(
    saved_outputs, request,
):
    async with await WorkflowEnvironment.start_local(
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
    ) as env:
        adapter = TemporalClientAdapter(client=env.client)
        definition = uuid4()
        arguments = dict(
            definition_id=definition, cron_expression="0 0 1 1 *", enabled=False,
            workflow_type="MoonMind.UserWorkflow",
        )
        schedule_id = await adapter.create_schedule(
            **arguments, workflow_input={
                "previousOutputs": saved_outputs,
            },
        )
        description = await env.client.get_schedule_handle(schedule_id).describe()
        assert description.schedule.state.paused
        assert step_gate_payload(saved_outputs)["verdict"] == "BLOCKED"
        result = await _observe(env.client)
        assert result["gate_aliases"].saved_inputs == 1
        assert not result["gate_aliases"].may_remove
        assert result["profile_selection"].may_remove
        assert result["native_pr_branches"].may_remove
        _receipt(f"saved-schedule-{request.node.callspec.id}", env.client, result)
        await adapter.update_schedule(
            **arguments, workflow_input={
                "previousOutputs": {"moonSpecVerify": {"verdict": "BLOCKED"}},
            },
        )
        repaired = await _observe(env.client)
        assert all(row.may_remove for row in repaired.values())
        _receipt(f"repaired-schedule-{request.node.callspec.id}", env.client, repaired)


@workflow.defn(name="MoonMind.AgentRun")
class _ExternalAgent:
    """External agent only; the real parent compiles and records each request."""

    @workflow.run
    async def run(self, request: dict) -> dict:
        return {"summary": "External agent completed", "output_refs": []}


@pytest.mark.parametrize("late_patch", [False, True], ids=["current", "late-marker"])
async def test_real_parent_profile_commands_keep_their_pre_marker_consumer(
    monkeypatch, late_patch,
):
    fake = FakeActivities(hold_finish=True)
    fake.plan["nodes"] = [
        {
            "id": name, "tool": {"type": "agent_runtime", "name": "codex_cli"},
            "inputs": {
                "instructions": "Use admitted account intent.",
                "runtime": {"mode": "codex_cli"},
            },
        } if name != "finish" else fake.plan["nodes"][2]
        for name in ("prepare", "finish", "implement")
    ]
    fake.plan["edges"] = [
        {"from": "prepare", "to": "finish"}, {"from": "finish", "to": "implement"},
    ]
    original_patched = workflow.patched
    old_profile = late_patch

    def patched(name):
        # Skill materialization is an external service, outside this profile
        # command boundary. The empty-skill snapshot has no profile semantics.
        if name == RUN_EMPTY_AGENT_SKILLSET_SNAPSHOT_PATCH:
            return False
        if old_profile and name in {
            RUN_ACCOUNT_INTENT_NORMALIZATION_PATCH,
            RUN_STEP_PROFILE_SELECTION_INHERITANCE_PATCH,
        }:
            return False
        return original_patched(name)

    monkeypatch.setattr(workflow, "patched", patched)
    async with (
        await WorkflowEnvironment.start_local(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
        ) as env,
        AsyncExitStack() as stack,
    ):
        await register_deployment_search_attributes(env)
        await _activity_workers(stack, env.client, fake)
        queue = f"profile-drain-{uuid4()}"
        async with (
            Worker(
                env.client, task_queue=get_workflow_child_task_queue(queue),
                workflows=[_ExternalAgent], workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            _workflow_worker(env.client, queue),
        ):
            handle = await _start(env, queue, {}, admitted=True)
            await asyncio.wait_for(fake.finish_started.wait(), 30)
            old_profile = False
            fake.release_finish.set()
            assert (await asyncio.wait_for(handle.result(), 30))["status"] == "success"
        history = await handle.fetch_history()
        patches = {
            json.loads(
                event.marker_recorded_event_attributes.details["patch-data"].payloads[0].data
            )["id"]
            for event in history.events
            if event.HasField("marker_recorded_event_attributes")
            and event.marker_recorded_event_attributes.marker_name == "core_patch"
        }
        assert RUN_ACCOUNT_INTENT_NORMALIZATION_PATCH in patches
        result = await _observe(env.client)
        assert result["profile_selection"].may_remove is (not late_patch)
        assert result["profile_selection"].supported_resets == int(late_patch)
        _receipt(
            "profile-late" if late_patch else "profile-current",
            env.client, result, handle=handle,
        )


async def test_actual_temporal_namespace_failure_is_unknown_not_drained():
    async with await WorkflowEnvironment.start_local() as env:
        inaccessible = await Client.connect(
            env.client.service_client.config.target_host,
            namespace=f"unavailable-{uuid4()}",
        )
        result = await _observe(inaccessible)
        assert all(not row.may_remove for row in result.values())
        assert all(row.open_histories is None for row in result.values())
        assert all(row.supported_resets is None for row in result.values())
        assert all(row.errors for row in result.values())
        _receipt("unknown", inaccessible, result)
