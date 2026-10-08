"""Replay the accepted-head request written before authored-base handoff."""

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowHistory
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.run import (
    RUN_ACCEPTED_PUBLICATION_BASE_HANDOFF_PATCH,
    RUN_ACCEPTED_PUBLICATION_HEAD_HANDOFF_PATCH,
    MoonMindRunWorkflow,
)

_HISTORY = (
    Path(__file__).parents[3]
    / "fixtures/temporal/accepted_publication_head_without_base.json"
)


def _patch_ids(history: WorkflowHistory) -> set[str]:
    return {
        json.loads(payload.data)["id"]
        for event in history.events
        if event.HasField("marker_recorded_event_attributes")
        and event.marker_recorded_event_attributes.marker_name == "core_patch"
        for payload in event.marker_recorded_event_attributes.details[
            "patch-data"
        ].payloads
    }


@activity.defn
async def record_accepted_publication_head(head: dict) -> dict:
    return head


async def _handoff(head: dict) -> dict:
    # Temporal does not compare Activity payload bytes during replay. Binding
    # the fixture's Activity ID to its payload makes a changed request shape
    # observable to the real Replayer without mocking workflow.patched.
    digest = hashlib.sha256(json.dumps(head, sort_keys=True).encode()).hexdigest()
    return await workflow.execute_activity(
        record_accepted_publication_head,
        head,
        activity_id=f"accepted-head-{digest}",
        start_to_close_timeout=timedelta(seconds=10),
    )


@workflow.defn(name="AcceptedPublicationBaseHandoffReplay")
class LegacyAcceptedPublicationHeadHandoff:
    """Pre-change producer retained for regenerating the checked-in history."""

    @workflow.run
    async def run(self) -> dict:
        assert workflow.patched(RUN_ACCEPTED_PUBLICATION_HEAD_HANDOFF_PATCH)
        return await _handoff(
            {
                "workflowId": workflow.info().workflow_id,
                "repository": "example/repository",
                "branch": "candidate",
                "headSha": "a" * 40,
            }
        )


@workflow.defn(name="AcceptedPublicationBaseHandoffReplay")
class CurrentAcceptedPublicationHeadHandoff:
    @workflow.run
    async def run(self) -> dict:
        parent = MoonMindRunWorkflow()
        parent._repo = "example/repository"
        parent._record_accepted_published_head(
            {
                "acceptedRepositoryEvidence": {
                    "pushStatus": "pushed",
                    "branch": "candidate",
                    "baseBranch": "main",
                    "headSha": "a" * 40,
                }
            }
        )
        request = parent._build_agent_execution_request(
            node_inputs={
                "runtime": {"mode": "omnigent"},
                "workspaceSpec": {
                    "repository": "example/repository",
                    "startingBranch": "candidate",
                },
                "publishMode": "pr",
            },
            node_id="publish",
            tool_name="auto",
            workflow_parameters={},
        )
        return await _handoff(request.parameters["acceptedPublishedHead"])


@pytest.mark.asyncio
async def test_retained_accepted_head_without_base_replays():
    history = WorkflowHistory.from_json(
        "accepted-head-before-base", _HISTORY.read_text()
    )
    assert RUN_ACCEPTED_PUBLICATION_HEAD_HANDOFF_PATCH in _patch_ids(history)
    assert RUN_ACCEPTED_PUBLICATION_BASE_HANDOFF_PATCH not in _patch_ids(history)
    scheduled = next(
        event.activity_task_scheduled_event_attributes
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    )
    assert "baseBranch" not in json.loads(scheduled.input.payloads[0].data)
    await Replayer(
        workflows=[CurrentAcceptedPublicationHeadHandoff],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


@pytest.mark.asyncio
async def test_new_accepted_head_with_base_replays():
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(
            env.client,
            task_queue="accepted-head-with-base",
            workflows=[CurrentAcceptedPublicationHeadHandoff],
            activities=[record_accepted_publication_head],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
    ):
        handle = await env.client.start_workflow(
            CurrentAcceptedPublicationHeadHandoff.run,
            id="accepted-head-with-base",
            task_queue="accepted-head-with-base",
        )
        assert (await handle.result())["baseBranch"] == "main"
        history = await handle.fetch_history()
    assert RUN_ACCEPTED_PUBLICATION_BASE_HANDOFF_PATCH in _patch_ids(history)
    await Replayer(
        workflows=[CurrentAcceptedPublicationHeadHandoff],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)
