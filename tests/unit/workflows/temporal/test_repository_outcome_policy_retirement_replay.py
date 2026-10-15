"""Retired no-commit policy payloads replay without authorizing new requests."""

import hashlib
import json
from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.run import (
    RUN_REPOSITORY_BOUND_NO_COMMIT_OUTCOME_PATCH,
    RUN_RETIRED_REPOSITORY_OUTCOME_POLICY_PATCH,
    RUN_TRUSTED_NO_COMMIT_REPOSITORY_OUTCOME_PATCH,
    MoonMindRunWorkflow,
)


@activity.defn
async def record_repository_outcome_parameters(parameters: dict) -> dict:
    return parameters


async def _handoff(parameters: dict) -> dict:
    # Replay does not compare Activity input bytes. Bind the Activity ID to the
    # payload so omitting/changing a historical policy fails real replay.
    digest = hashlib.sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest()
    return await workflow.execute_activity(
        record_repository_outcome_parameters,
        parameters,
        activity_id=f"repository-outcome-{digest}",
        start_to_close_timeout=timedelta(seconds=10),
    )


def _legacy_policy(version: str) -> dict:
    policy = {
        "schemaVersion": f"repository-outcome-policy/{version}",
        "allowNoCommit": True,
        "authority": "trusted_assessment",
        "assessmentVerdict": "FULLY_IMPLEMENTED",
        "assessmentArtifactRef": "art_assessment_1",
    }
    if version == "v2":
        policy.update(assessedRepository="example/repository", assessedBranch="main")
    return policy


@workflow.defn(name="RepositoryOutcomePolicyRetirementReplay")
class LegacyRepositoryOutcomePolicy:
    """Independent producer of the two previously scheduled request shapes."""

    @workflow.run
    async def run(self, version: str) -> dict:
        if version == "v2":
            assert workflow.patched(RUN_REPOSITORY_BOUND_NO_COMMIT_OUTCOME_PATCH)
        assert workflow.patched(RUN_TRUSTED_NO_COMMIT_REPOSITORY_OUTCOME_PATCH)
        return await _handoff({"repositoryOutcomePolicy": _legacy_policy(version)})


@workflow.defn(name="RepositoryOutcomePolicyRetirementReplay")
class CurrentRepositoryOutcomePolicy:
    @workflow.run
    async def run(self, version: str) -> dict:
        parent = MoonMindRunWorkflow()
        parent._assessment_context = {
            "assessmentArtifactRef": "art_assessment_1",
            "assessmentVerdict": "FULLY_IMPLEMENTED",
            "assessedRepository": "example/repository",
            "assessedBranch": "main",
        }
        request = parent._build_agent_execution_request(
            node_inputs={
                "targetRuntime": "omnigent",
                "repository": "example/repository",
                "startingBranch": "main",
                "repositoryOperation": "write",
                "publishMode": "pr",
            },
            node_id="implement",
            tool_name="omnigent",
        )
        return await _handoff(
            {
                key: value
                for key, value in request.parameters.items()
                if key == "repositoryOutcomePolicy"
            }
        )


def _patch_ids(history) -> set[str]:
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
@pytest.mark.parametrize("version", ["v1", "v2"])
async def test_repository_outcome_policy_retirement_histories_replay(version):
    histories = []
    async with await WorkflowEnvironment.start_time_skipping() as env:
        for kind, implementation, expected in (
            (
                "legacy",
                LegacyRepositoryOutcomePolicy,
                {
                    "repositoryOutcomePolicy": _legacy_policy(version),
                },
            ),
            ("current", CurrentRepositoryOutcomePolicy, {}),
        ):
            queue = f"repository-outcome-retirement-{kind}-{version}"
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[implementation],
                activities=[record_repository_outcome_parameters],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await env.client.start_workflow(
                    implementation.run,
                    version,
                    id=queue,
                    task_queue=queue,
                )
                assert await handle.result() == expected
                history = await handle.fetch_history()
                histories.append(history)
                markers = _patch_ids(history)
                assert (RUN_RETIRED_REPOSITORY_OUTCOME_POLICY_PATCH in markers) == (
                    kind == "current"
                )
                if kind == "current":
                    assert RUN_TRUSTED_NO_COMMIT_REPOSITORY_OUTCOME_PATCH not in markers
                    assert RUN_REPOSITORY_BOUND_NO_COMMIT_OUTCOME_PATCH not in markers
    replayer = Replayer(
        workflows=[CurrentRepositoryOutcomePolicy],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    for history in histories:
        await replayer.replay_workflow(history)
