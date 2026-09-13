"""Resolver creation must deliver a scoped, usable verification capability."""

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from api_service.api.routers.mcp_tools import (
    ToolCallRequest,
    _enforce_container_capability_scope,
)
from moonmind.container_job_cli import python_test_submission
from moonmind.omnigent.harness_platform.execution_plan import (
    create_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.host_classes import get_launch_policy
from moonmind.omnigent.host_services.runtime_environment import (
    OmnigentRuntimeEnvironmentService,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.security.container_job_capabilities import (
    verify_container_job_session_capability,
)
from moonmind.workflows.temporal.workflows.merge_gate import build_resolver_run_request
from tests.unit.omnigent.test_generic_platform_production_services import _plan

_REPLAY = json.loads(
    (
        Path(__file__).parents[2]
        / "fixtures/reliability/resolver-verification-capability.json"
    ).read_text()
)


@pytest.mark.parametrize(
    "harness", ["opencode-native", "codex-native", "claude-code-native"]
)
@pytest.mark.parametrize("capabilities", _REPLAY["capabilityCases"])
def test_resolver_request_reaches_scoped_container_submission(harness, capabilities):
    template = {"targetRuntime": "omnigent", "executionProfileRef": "selected-profile"}
    if capabilities is not None:
        template["requiredCapabilities"] = capabilities
    child = build_resolver_run_request(
        parent_workflow_id="merge-owner",
        pull_request={
            "repo": "example/repo",
            "number": 1,
            "url": "https://github.com/example/repo/pull/1",
            "headSha": "a" * 40,
            "headBranch": "candidate",
            "baseBranch": "main",
        },
        jira_issue_key=None,
        merge_method="squash",
        resolver_template=template,
    )
    request = AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "resolver-child",
            "idempotencyKey": "resolver-step",
            "parameters": child["initial_parameters"],
            "workspaceSpec": {
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": "resolver-workspace",
                    "relativePath": "repo",
                }
            },
            "stepExecution": {
                "workflowId": "resolver-child",
                "runId": "child-run",
                "logicalStepId": "node-1",
                "executionOrdinal": 1,
                "stepExecutionId": "resolver-child:child-run:node-1:execution:1",
                "runtimeContextPolicy": "fresh_agent_run",
            },
        }
    )
    plan = _plan("test/model")
    plan = create_execution_plan_envelope(
        {**plan.payload.model_dump(mode="json", by_alias=True), "harnessId": harness}
    )
    environment = OmnigentRuntimeEnvironmentService(
        moonmind_url="http://api:8000", signing_secret="test-secret"
    ).build(
        request=request,
        plan=plan,
        host_lease_ref="resolver-lease",
        launch_policy=get_launch_policy("omnigent-on-demand@1"),
        workspace_attachment={"accessMode": "read-write"},
    )
    submission = python_test_submission(["tests/unit/test_example.py"], env=environment)
    capability = verify_container_job_session_capability(
        environment["MOONMIND_CONTAINER_JOBS_BEARER_TOKEN"], secret="test-secret"
    )
    assert capability.runtime_id == harness
    assert capability.workflow_id == "resolver-child"
    assert capability.workspace_id == "resolver-workspace"
    assert capability.session_id == "resolver-lease"
    assert (
        child["initial_parameters"]["task"]["runtime"]["executionProfileRef"]
        == "selected-profile"
    )
    assert "MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN" not in environment
    _enforce_container_capability_scope(
        ToolCallRequest(tool="container.submit", arguments=submission), capability
    )
    submission["spec"]["workspaceRef"]["workspaceId"] = "another-workspace"
    with pytest.raises(HTTPException) as denied:
        _enforce_container_capability_scope(
            ToolCallRequest(tool="container.submit", arguments=submission), capability
        )
    assert denied.value.status_code == 403
