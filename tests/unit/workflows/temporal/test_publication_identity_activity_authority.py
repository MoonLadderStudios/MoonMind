"""Real retained-shaped Activity input resolves authority from server memos."""

from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.omnigent.git_identity import publication_git_identity_projection

_IDENTITY = {"name": "Recorded operator", "email": "recorded@example.invalid"}


@activity.defn
async def capture_recorded_publication_identity(parameters: dict) -> dict | None:
    return await publication_git_identity_projection(parameters)


@workflow.defn
class RecordedPublicationIdentityChild:
    @workflow.run
    async def run(self, parameters: dict) -> dict:
        identity = await workflow.execute_activity(
            capture_recorded_publication_identity,
            parameters,
            start_to_close_timeout=timedelta(seconds=10),
        )
        return {"identity": identity, "runId": workflow.info().run_id}


@workflow.defn
class RecordedPublicationIdentityParent:
    @workflow.run
    async def run(self, parameters: dict) -> dict:
        return await workflow.execute_child_workflow(
            RecordedPublicationIdentityChild.run,
            parameters,
            id=workflow.info().workflow_id + ":child",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "legacy",
    [
        {"name": "Authored", "email": "authored@example.invalid"},
        "malformed",
        {},
        {"name": "injected\n[user]", "email": "authored@example.invalid"},
    ],
)
@pytest.mark.parametrize("admitted", [False, True])
async def test_activity_reads_actual_parent_memo_and_legacy_parameters_are_inert(
    legacy, admitted
):
    identity = "publication-activity-authority"
    parameters = {
        "publicationGitIdentityAdmission": {"schemaVersion": "v1", "identity": legacy},
        "_publicationGitIdentity": legacy,
    }
    memo = (
        {
            "publicationGitIdentityAdmission": {
                "schemaVersion": "v1",
                "identity": _IDENTITY,
            }
        }
        if admitted
        else {}
    )
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(
            env.client,
            task_queue=identity,
            workflows=[
                RecordedPublicationIdentityParent,
                RecordedPublicationIdentityChild,
            ],
            activities=[capture_recorded_publication_identity],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
    ):
        handle = await env.client.start_workflow(
            RecordedPublicationIdentityParent.run,
            parameters,
            id=identity,
            task_queue=identity,
            memo=memo,
        )
        result = await handle.result()
        parent = await handle.fetch_history()
        child = await env.client.get_workflow_handle(
            identity + ":child", run_id=result["runId"]
        ).fetch_history()
    assert result["identity"] == (_IDENTITY if admitted else None)
    for history in (parent, child):
        await Replayer(
            workflows=[
                RecordedPublicationIdentityParent,
                RecordedPublicationIdentityChild,
            ],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ).replay_workflow(history)
