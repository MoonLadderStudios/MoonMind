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
from moonmind.omnigent.harness_platform.planner import compile_execution_plan
from moonmind.omnigent.harness_platform.host_classes import get_launch_policy
from moonmind.omnigent.host_services.runtime_environment import (
    OmnigentRuntimeEnvironmentService,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.security.container_job_capabilities import (
    verify_container_job_session_capability,
)
from moonmind.workflows.temporal.workflows.merge_gate import build_resolver_run_request
from tests.unit.omnigent.test_generic_plane_production_boundary_concurrency import (
    _catalog,
    _compile_kwargs,
    _ready_opencode_image_pair,  # noqa: F401 - test-only image qualification fixture
)
from tests.unit.omnigent.test_plan_and_realizer_dispatch import (
    _compile_claude_plan,
    _compile_codex_plan,
)

_REPLAY = json.loads(
    (
        Path(__file__).parents[2]
        / "fixtures/reliability/resolver-verification-capability.json"
    ).read_text()
)


@pytest.mark.parametrize(
    "harness", ["opencode-native", "codex-native", "claude-native"]
)
@pytest.mark.parametrize("capabilities", _REPLAY["capabilityCases"])
def test_resolver_request_reaches_scoped_container_submission(
    harness, capabilities, monkeypatch
):
    monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED", "1")
    monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_CLAUDE_QUALIFIED", "1")
    factories = {
        "opencode-native": lambda: compile_execution_plan(
            **_compile_kwargs(_catalog(), "1")
        ),
        "codex-native": _compile_codex_plan,
        "claude-native": _compile_claude_plan,
    }
    plan = factories[harness]()
    assert plan.payload.harnessId == harness
    assert plan.payload.executionRealizerRef == "generic-omnigent-host@1"
    selected_profile = plan.payload.credentialBindings[
        "primary-model"
    ].providerProfileRef
    template = {"targetRuntime": "omnigent", "executionProfileRef": selected_profile}
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
    environment = OmnigentRuntimeEnvironmentService(
        moonmind_url="http://api:8000", signing_secret="test-secret"
    ).build(
        request=request,
        plan=plan,
        host_lease_ref="resolver-lease",
        launch_policy=get_launch_policy(plan.payload.launchPolicyRef),
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
        == selected_profile
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
