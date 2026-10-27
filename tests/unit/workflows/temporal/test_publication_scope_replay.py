"""Replay old PR-body requests while new histories retain original scope."""

import hashlib
import json
from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.run import (
    RUN_PUBLICATION_TASK_SCOPE_PATCH,
    MoonMindRunWorkflow,
)


@activity.defn
async def record_publication_body(body: str) -> str:
    return body


class _LegacyBodyParent(MoonMindRunWorkflow):
    def _patched_or_false_outside_workflow(self, marker: str) -> bool:
        if marker == RUN_PUBLICATION_TASK_SCOPE_PATCH:
            return False
        return super()._patched_or_false_outside_workflow(marker)


async def _handoff(parent: MoonMindRunWorkflow) -> str:
    parent._trusted_issue_context = {
        "trustedSource": "moonmind.github.get_issue",
        "issue": {
            "repository": "example/repository",
            "number": 12,
            "title": "Use selected credentials",
            "body": "Use credential B; retain release qualification.",
            "url": "https://github.com/example/repository/issues/12",
        },
    }
    body = parent._resolve_native_pr_body(
        publish_payload={"prBody": "Completed the credential fix."}, task_payload={}
    )
    # Temporal does not compare Activity input bytes. Tie the ID to the actual
    # native body so an accidental replay change cannot pass unnoticed.
    digest = hashlib.sha256(body.encode()).hexdigest()
    return await workflow.execute_activity(
        record_publication_body,
        body,
        activity_id=f"publication-body-{digest}",
        start_to_close_timeout=timedelta(seconds=10),
    )


@workflow.defn(name="PublicationScopeReplay")
class LegacyPublicationScope:
    @workflow.run
    async def run(self) -> str:
        return await _handoff(_LegacyBodyParent())


@workflow.defn(name="PublicationScopeReplay")
class CurrentPublicationScope:
    @workflow.run
    async def run(self) -> str:
        return await _handoff(MoonMindRunWorkflow())


@pytest.mark.asyncio
async def test_original_scope_is_added_only_for_new_histories():
    histories = []
    async with await WorkflowEnvironment.start_time_skipping() as env:
        for label, implementation in (
            ("legacy", LegacyPublicationScope),
            ("current", CurrentPublicationScope),
        ):
            queue = f"publication-task-scope-{label}"
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[implementation],
                activities=[record_publication_body],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await env.client.start_workflow(
                    implementation.run, id=queue, task_queue=queue
                )
                body = await handle.result()
                assert ("Use credential B; retain release qualification." in body) == (
                    label == "current"
                )
                history = await handle.fetch_history()
                markers = {
                    json.loads(payload.data)["id"]
                    for event in history.events
                    if event.HasField("marker_recorded_event_attributes")
                    and event.marker_recorded_event_attributes.marker_name
                    == "core_patch"
                    for payload in event.marker_recorded_event_attributes.details[
                        "patch-data"
                    ].payloads
                }
                assert (RUN_PUBLICATION_TASK_SCOPE_PATCH in markers) == (
                    label == "current"
                )
                histories.append(history)
    replayer = Replayer(
        workflows=[CurrentPublicationScope], workflow_runner=UnsandboxedWorkflowRunner()
    )
    for history in histories:
        await replayer.replay_workflow(history)
