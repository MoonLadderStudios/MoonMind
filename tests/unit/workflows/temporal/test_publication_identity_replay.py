"""Mixed API/worker rollout retains the recorded publication request payload."""

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowHistory
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

_PATCH = "run-publication-git-identity-payload-v1"
_KEY = "_publicationGitIdentity"
_HISTORY = (
    Path(__file__).parents[3]
    / "fixtures/temporal/publication_identity_mixed_worker.json"
)
_IDENTITY = {"name": "Admitted operator", "email": "admitted@example.invalid"}


@activity.defn
async def capture_publication_parameters(parameters: dict) -> dict:
    return parameters


async def _handoff(parameters: dict) -> dict:
    # The SDK does not compare Activity argument bytes during replay. Binding
    # its ID to the payload makes argument drift observable by the real Replayer.
    digest = hashlib.sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest()
    return await workflow.execute_activity(
        capture_publication_parameters,
        parameters,
        activity_id=f"publication-identity-{digest}",
        start_to_close_timeout=timedelta(seconds=10),
    )


def _request(parent: MoonMindRunWorkflow, parameters: dict):
    return parent._build_agent_execution_request(
        node_inputs={
            "instructions": "Publish checkpoint",
            "runtime": {"mode": "omnigent"},
        },
        node_id="publish",
        tool_name="omnigent",
        workflow_parameters=parameters,
    )


@workflow.defn(name="PublicationIdentityHandoffReplay")
class LegacyPublicationIdentityHandoff:
    """Old worker omits attribution even when updated API input includes it."""

    @workflow.run
    async def run(self, parameters: dict) -> dict:
        parent = MoonMindRunWorkflow()
        check = parent._workflow_patch_enabled
        parent._workflow_patch_enabled = lambda patch: (
            False if patch == _PATCH else check(patch)
        )
        request = _request(parent, parameters)
        request.parameters.pop(_KEY, None)
        return await _handoff(request.parameters)


@workflow.defn(name="PublicationIdentityHandoffReplay")
class CurrentPublicationIdentityHandoff:
    @workflow.run
    async def run(self, parameters: dict) -> dict:
        return await _handoff(_request(MoonMindRunWorkflow(), parameters).parameters)


async def _record(workflow_class, identity: str):
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(
            env.client,
            task_queue=identity,
            workflows=[workflow_class],
            activities=[capture_publication_parameters],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
    ):
        handle = await env.client.start_workflow(
            workflow_class.run,
            {_KEY: _IDENTITY},
            id=identity,
            task_queue=identity,
            memo={
                "publicationGitIdentityAdmission": {
                    "schemaVersion": "v1",
                    "identity": _IDENTITY,
                }
            },
        )
        result = await handle.result()
        history = await handle.fetch_history()
    return result, history


async def write_retained_history():
    result, history = await _record(
        LegacyPublicationIdentityHandoff, "publication-identity-old-worker"
    )
    assert _KEY not in result
    payload = json.loads(history.to_json())
    for event in payload["events"]:
        for attributes in event.values():
            if isinstance(attributes, dict) and "identity" in attributes:
                attributes["identity"] = "publication-replay-worker"
    _HISTORY.write_text(json.dumps(payload, indent=2) + "\n")


def _patch_ids(history):
    return {
        json.loads(payload.data)["id"]
        for event in history.events
        if event.HasField("marker_recorded_event_attributes")
        and event.marker_recorded_event_attributes.marker_name == "core_patch"
        for payload in event.marker_recorded_event_attributes.details[
            "patch-data"
        ].payloads
    }


@pytest.mark.asyncio
async def test_retained_api_identity_with_old_worker_payload_replays():
    history = WorkflowHistory.from_json(
        "publication-identity-old-worker", _HISTORY.read_text()
    )
    assert _PATCH not in _patch_ids(history)
    started = history.events[0].workflow_execution_started_event_attributes
    assert json.loads(started.input.payloads[0].data)[_KEY] == _IDENTITY
    scheduled = next(
        event.activity_task_scheduled_event_attributes
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    )
    assert _KEY not in json.loads(scheduled.input.payloads[0].data)
    await Replayer(
        workflows=[CurrentPublicationIdentityHandoff],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


@pytest.mark.asyncio
async def test_new_publication_identity_payload_records_patch_and_replays():
    result, history = await _record(
        CurrentPublicationIdentityHandoff, "publication-identity-current-worker"
    )
    assert result["publicationGitIdentityAdmission"] == {
        "schemaVersion": "v1",
        "identity": _IDENTITY,
    }
    assert _PATCH in _patch_ids(history)
    await Replayer(
        workflows=[CurrentPublicationIdentityHandoff],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)
