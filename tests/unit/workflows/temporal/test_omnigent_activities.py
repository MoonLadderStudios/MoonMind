import inspect
from unittest.mock import patch

import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.omnigent import execute as omnigent_execute_module
from moonmind.omnigent.execute import LocalOmnigentArtifactGateway
from moonmind.omnigent.store import OmnigentRunStore
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.activities import (
    omnigent_activities as omnigent_activities_module,
)
from moonmind.workflows.temporal.activities.omnigent_activities import (
    omnigent_execute_activity,
)


@pytest.mark.asyncio
@patch("moonmind.omnigent.execute.run_omnigent_execution")
async def test_omnigent_execute_activity_delegates(mock_run):
    expected_result = AgentRunResult(summary="done", output_refs=[])
    mock_run.return_value = expected_result

    req = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="123",
        idempotencyKey="key",
    )

    env = ActivityEnvironment()
    result = await env.run(omnigent_execute_activity, req)

    assert result == expected_result
    mock_run.assert_called_once()
    called_req = mock_run.call_args.args[0]
    assert called_req == req
    assert isinstance(mock_run.call_args.kwargs["artifact_gateway"], LocalOmnigentArtifactGateway)
    assert isinstance(mock_run.call_args.kwargs["run_store"], OmnigentRunStore)


def test_omnigent_execution_path_does_not_use_managed_github_broker() -> None:
    """Omnigent is an external-agent adapter, not a managed runtime launcher."""

    source = "\n".join(
        [
            inspect.getsource(omnigent_activities_module),
            inspect.getsource(omnigent_execute_module),
        ]
    )

    for disallowed in (
        "github_auth_broker",
        "GitHubAuthBroker",
        "build_github_socket_path",
        "render_gh_wrapper_script",
        "render_git_credential_helper_script",
        "GITHUB_TOKEN",
    ):
        assert disallowed not in source
