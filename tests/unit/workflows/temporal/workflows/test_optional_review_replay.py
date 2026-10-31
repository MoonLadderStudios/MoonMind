"""Advisory admission changes preserve recorded verifier commands and requests."""

import hashlib
import json
from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.run import (
    RUN_ADVISORY_ARTIFACT_REVIEW_PATCH,
    MoonMindRunWorkflow,
)


class _LegacyParent(MoonMindRunWorkflow):
    def _is_moonspec_verify_step(self, *, tool_name, node_inputs):
        # Independent pre-change detector, with no advisory patch marker.
        if str(tool_name or "").strip().lower() == "moonspec-verify":
            return True
        for key in ("selectedSkill", "skillId", "skill", "targetSkill"):
            value = node_inputs.get(key)
            if isinstance(value, str) and value.strip().lower() == "moonspec-verify":
                return True
            if isinstance(value, dict):
                nested = value.get("id") or value.get("name")
                if (
                    isinstance(nested, str)
                    and nested.strip().lower() == "moonspec-verify"
                ):
                    return True
        return False


@activity.defn
async def record_review_command(payload: dict) -> None:
    pass


async def _review_commands(parent, annotations):
    inputs = {
        "instructions": "Review the supplied immutable artifact; do not publish.",
        "runtime": {"mode": "codex_cli"},
        "selectedSkill": "moonspec-verify",
        "annotations": annotations,
    }
    request = parent._build_agent_execution_request(
        node_inputs=inputs,
        node_id="review",
        tool_name="codex_cli",
        resolved_skillset_ref="art_selected_verifier",
    )
    # Activity IDs bind request bytes: Temporal itself does not compare inputs.
    payload = request.model_dump(mode="json", by_alias=True)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    await workflow.execute_activity(
        record_review_command,
        payload,
        activity_id="request-" + digest,
        start_to_close_timeout=timedelta(seconds=10),
    )
    result = {
        "outputs": {"lastAssistantText": "Advisory findings; evidence unavailable."}
    }
    repair = parent._moonspec_verify_contract_repair_feedback(
        execution_result=result, tool_name="codex_cli", node_inputs=inputs
    )
    if repair:
        await workflow.execute_activity(
            record_review_command,
            {"feedback": repair},
            activity_id="contract-repair",
            start_to_close_timeout=timedelta(seconds=10),
        )
    if parent._is_moonspec_verify_step(tool_name="codex_cli", node_inputs=inputs):
        parent._record_moonspec_verify_gate(node_id="review", outputs=result["outputs"])
    blocked = parent._blocking_moonspec_gate_reason() is not None
    # Command ordering must match both the old gate stop and the new completion.
    await workflow.sleep(1 if blocked else 2)
    return {"repair": bool(repair), "blocked": blocked}


@workflow.defn(name="OptionalReviewReplay")
class _LegacyReviewWorkflow:
    @workflow.run
    async def run(self, annotations: dict) -> dict:
        return await _review_commands(_LegacyParent(), annotations)


@workflow.defn(name="OptionalReviewReplay")
class _CurrentReviewWorkflow:
    @workflow.run
    async def run(self, annotations: dict) -> dict:
        return await _review_commands(MoonMindRunWorkflow(), annotations)


@pytest.mark.asyncio
async def test_advisory_review_replays_old_and_current_commands():
    histories = []
    async with await WorkflowEnvironment.start_time_skipping() as env:
        cases = [
            (_LegacyReviewWorkflow, {"optionalReview": True}, True),
            (_CurrentReviewWorkflow, {"optionalReview": True}, False),
            (_CurrentReviewWorkflow, {}, True),
            (
                _CurrentReviewWorkflow,
                {
                    "optionalReview": True,
                    "issueImplementRole": "moonspec-verification-gate",
                },
                True,
            ),
            (
                _CurrentReviewWorkflow,
                {
                    "optionalReview": True,
                    "jiraOrchestrateRole": "moonspec-verification-gate",
                },
                True,
            ),
        ]
        for index, (implementation, annotations, blocked) in enumerate(cases):
            queue = f"optional-review-replay-{index}"
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[implementation],
                activities=[record_review_command],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await env.client.start_workflow(
                    implementation.run,
                    annotations,
                    id=queue,
                    task_queue=queue,
                )
                assert await handle.result() == {"repair": blocked, "blocked": blocked}
                history = await handle.fetch_history()
                histories.append(history)
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
                assert (RUN_ADVISORY_ARTIFACT_REVIEW_PATCH in markers) is (not blocked)
    replayer = Replayer(
        workflows=[_CurrentReviewWorkflow], workflow_runner=UnsandboxedWorkflowRunner()
    )
    for history in histories:
        await replayer.replay_workflow(history)
