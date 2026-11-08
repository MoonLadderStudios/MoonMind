"""Keep already-scheduled selected-issue request bytes stable during replay."""

import hashlib
import json
from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from tests.unit.workflows.temporal.workflows.test_run_issue_search_prompt import (
    PROMPT_PATCH,
    build_request,
    selected_issue_outputs,
)


class LegacySearchPrompt(MoonMindRunWorkflow):
    def _patched_or_false_outside_workflow(self, patch_id):
        # Reproduce the old producer without recording the new marker.
        if patch_id == PROMPT_PATCH:
            return False
        return super()._patched_or_false_outside_workflow(patch_id)


@activity.defn
async def record_search_request(request: dict) -> int:
    return len(request["instructionRef"].encode())


async def handoff(parent, outputs):
    parent._record_trusted_issue_context(outputs)
    previous = parent._merge_trusted_issue_context(
        {"summary": "Proceed with selected issue."}
    )
    request = build_request(parent, previous).model_dump(mode="json", by_alias=True)
    # Temporal otherwise does not compare Activity input bytes during replay.
    digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    return await workflow.execute_activity(
        record_search_request,
        request,
        activity_id=f"handoff-{digest}",
        start_to_close_timeout=timedelta(seconds=10),
    )


@workflow.defn(name="SelectedIssueSearchPromptReplay")
class LegacySearchPromptWorkflow:
    @workflow.run
    async def run(self, outputs: dict) -> int:
        return await handoff(LegacySearchPrompt(), outputs)


@workflow.defn(name="SelectedIssueSearchPromptReplay")
class CurrentSearchPromptWorkflow:
    @workflow.run
    async def run(self, outputs: dict) -> int:
        return await handoff(MoonMindRunWorkflow(), outputs)


@pytest.mark.asyncio
async def test_selected_issue_old_and_new_prompt_histories_replay():
    histories = []
    sizes = {}
    async with await WorkflowEnvironment.start_time_skipping() as env:
        for name, implementation in (
            ("legacy", LegacySearchPromptWorkflow),
            ("current", CurrentSearchPromptWorkflow),
        ):
            queue = f"search-prompt-{name}"
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[implementation],
                activities=[record_search_request],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await env.client.start_workflow(
                    implementation.run,
                    selected_issue_outputs(),
                    id=queue,
                    task_queue=queue,
                )
                sizes[name] = await handle.result()
                history = await handle.fetch_history()
                histories.append(history)
                marker_ids = {
                    json.loads(payload.data)["id"]
                    for event in history.events
                    if event.HasField("marker_recorded_event_attributes")
                    and event.marker_recorded_event_attributes.marker_name
                    == "core_patch"
                    for payload in event.marker_recorded_event_attributes.details[
                        "patch-data"
                    ].payloads
                }
                assert (PROMPT_PATCH in marker_ids) is (name == "current")
    assert sizes["current"] < sizes["legacy"] / 2
    replayer = Replayer(
        workflows=[CurrentSearchPromptWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    for history in histories:
        await replayer.replay_workflow(history)
