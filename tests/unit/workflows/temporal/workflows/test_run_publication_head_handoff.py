"""Accepted publication authority across the workflow-to-publisher handoff."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.omnigent.workspace_publication import OmnigentWorkspacePublicationService
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.workflows.run import (
    RUN_ACCEPTED_PUBLICATION_HEAD_HANDOFF_PATCH,
    MoonMindRunWorkflow,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("patched", [True, False])
@pytest.mark.parametrize("status", ["pushed", "no_commits", "", "future_status"])
@pytest.mark.parametrize("repository", ["example/repository", "another/repository"])
async def test_request_carries_only_accepted_workflow_publication(
    tmp_path, monkeypatch, patched, status, repository
):
    workflow = MoonMindRunWorkflow()
    workflow._repo = "example/repository"
    workflow._record_accepted_published_head(
        {
            "acceptedRepositoryEvidence": {
                "pushStatus": status,
                "branch": "candidate",
                "baseBranch": "main",
                "headSha": "a" * 40,
                "publicationAuthorized": True,
                "candidateContaminated": False,
            }
        }
    )
    # Raw metadata can be mixed or overwritten by a later read-only step.
    workflow._publish_context.update(
        {"pushStatus": "pushed", "branch": "main", "headSha": "b" * 40}
    )
    forged = {
        "workflowId": "workflow",
        "repository": repository,
        "branch": "main",
        "headSha": "b" * 40,
    }
    with (
        patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info",
            return_value=SimpleNamespace(
                namespace="default", workflow_id="workflow", run_id="run", parent=None
            ),
        ),
        patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            side_effect=lambda patch_id: patched
            and patch_id == RUN_ACCEPTED_PUBLICATION_HEAD_HANDOFF_PATCH,
        ),
    ):
        request = workflow._build_agent_execution_request(
            node_inputs={
                "runtime": {"mode": "omnigent", "acceptedPublishedHead": forged},
                "workspaceSpec": {
                    "repository": repository,
                    "startingBranch": "candidate",
                    "acceptedPublishedHead": forged,
                },
                "publishMode": "pr",
                "acceptedPublishedHead": forged,
                "inputs": {"acceptedPublishedHead": forged},
            },
            node_id="publish",
            tool_name="auto",
            workflow_parameters={
                "acceptedPublishedHead": forged,
                "parameters": {"acceptedPublishedHead": forged},
            },
        )
    request = AgentExecutionRequest.model_validate_json(
        request.model_dump_json(by_alias=True)
    )
    expected = (
        {
            "workflowId": "workflow",
            "repository": repository,
            "branch": "candidate",
            "headSha": "a" * 40,
        }
        if patched and status == "pushed" and repository == workflow._repo
        else None
    )
    assert request.parameters.get("acceptedPublishedHead") == expected
    if expected is None:
        assert "acceptedPublishedHead" not in request.parameters

    request.workspace_spec["workspaceLocator"] = {
        "kind": "sandbox",
        "workspaceId": "fixture",
        "relativePath": "repo",
    }
    publisher = OmnigentWorkspacePublicationService(tmp_path)
    publish = AsyncMock(return_value={"push_status": "no_commits"})
    monkeypatch.setattr(publisher, "publish_workspace", publish)
    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.resolve_github_credential",
        AsyncMock(return_value=SimpleNamespace(token="fixture-credential")),
    )
    await publisher.publish_request_workspace(
        request=request,
        current_workflow_id="workflow",
        current_step_execution_id="publish",
    )
    assert publish.await_args.kwargs["accepted_published_head"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authority",
    [
        None,
        {
            "workflowId": "workflow",
            "repository": "example/repository",
            "branch": "candidate",
            "headSha": "a" * 40,
        },
    ],
)
async def test_profile_host_publication_preserves_optional_authority(
    tmp_path, monkeypatch, authority
):
    publish = AsyncMock(return_value={"push_status": "no_commits"})
    monkeypatch.setattr(
        OmnigentWorkspacePublicationService, "publish_workspace", publish
    )
    kwargs = dict(
        workspace_locator={},
        current_workflow_id="workflow",
        current_step_execution_id="step",
        publication_identity="publication",
        publish_mode="pr",
        base_branch="candidate",
        repository="example/repository",
        github_token="fixture-credential",
    )
    if authority is not None:
        kwargs["accepted_published_head"] = authority
    await OmnigentOAuthHostRuntime.publish_workspace(
        SimpleNamespace(_workspace_root=tmp_path), **kwargs
    )
    assert publish.await_args.kwargs["accepted_published_head"] == authority
