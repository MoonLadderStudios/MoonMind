"""Artifact-backed verifier prompts preserve previously scheduled request bytes."""

import hashlib
import json
from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.run import (
    RUN_ARTIFACT_BACKED_VERIFIER_INSTRUCTION_PATCH,
    RUN_ASSESSMENT_ATTACHMENT_HANDOFF_PATCH,
    MoonMindRunWorkflow,
)
from tests.unit.workflows.skills.test_acceptance_source_binding import (
    source_bound_report,  # noqa: F401
)
from tests.unit.workflows.temporal.workflows.test_run_verifier_handoff import (
    _build_request,
    large_verifier_report,  # noqa: F401
)


class LegacyVerifierHandoff(MoonMindRunWorkflow):
    def _append_trusted_previous_outputs_to_agent_inputs(self, node_inputs, **_kwargs):
        # Independent pre-patch producer, including its existing patch marker.
        include_assessment = workflow.patched(RUN_ASSESSMENT_ATTACHMENT_HANDOFF_PATCH)
        context = self._trusted_previous_outputs_instruction(
            node_inputs.get("previousOutputs"),
            include_assessment=include_assessment,
        )
        merged = dict(node_inputs)
        if context:
            merged["instructions"] = merged["instructions"].rstrip() + "\n\n" + context
        return merged


@activity.defn
async def record_verifier_request(request: dict) -> int:
    return len(json.dumps(request).encode())


async def _handoff(parent, report):
    parent._publish_context["moonSpecGate"] = {
        **report,
        "gateResultRef": "art_complete_report",
    }
    previous = parent._merge_trusted_issue_context(
        {
            "trustedSource": "moonmind.github.get_issue",
            "issueRef": "example/repo#1",
        }
    )
    request = _build_request(parent, previous).model_dump(mode="json", by_alias=True)
    # Temporal does not compare Activity input bytes. Binding the ID to those
    # bytes makes a change to historical instructions fail real replay.
    digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    return await workflow.execute_activity(
        record_verifier_request,
        request,
        activity_id=f"handoff-{digest}",
        start_to_close_timeout=timedelta(seconds=10),
    )


@workflow.defn(name="VerifierInstructionHandoffReplay")
class LegacyVerifierInstructionWorkflow:
    @workflow.run
    async def run(self, report: dict) -> int:
        return await _handoff(LegacyVerifierHandoff(), report)


@workflow.defn(name="VerifierInstructionHandoffReplay")
class CurrentVerifierInstructionWorkflow:
    @workflow.run
    async def run(self, report: dict) -> int:
        return await _handoff(MoonMindRunWorkflow(), report)


@pytest.mark.asyncio
async def test_old_and_new_verifier_handoffs_replay(large_verifier_report):  # noqa: F811
    histories = []
    async with await WorkflowEnvironment.start_time_skipping() as env:
        for name, implementation in (
            ("legacy", LegacyVerifierInstructionWorkflow),
            ("current", CurrentVerifierInstructionWorkflow),
        ):
            queue = f"verifier-handoff-{name}"
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[implementation],
                activities=[record_verifier_request],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await env.client.start_workflow(
                    implementation.run,
                    large_verifier_report,
                    id=queue,
                    task_queue=queue,
                )
                size = await handle.result()
                assert (size < 64 * 1024) is (name == "current")
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
                assert (
                    RUN_ARTIFACT_BACKED_VERIFIER_INSTRUCTION_PATCH in marker_ids
                ) is (name == "current")
    replayer = Replayer(
        workflows=[CurrentVerifierInstructionWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    for history in histories:
        await replayer.replay_workflow(history)
