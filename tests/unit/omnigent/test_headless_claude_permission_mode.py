"""Workflow-step Claude sessions launch in an unattended permission mode.

A MoonMind workflow step has no human at the Claude terminal; the operator
authorized the work by launching the workflow. Without an explicit launch mode
Claude Code falls back to the account default (``auto``), whose classifier
refused the batch-pr-resolver helper as "Merge Without Review" and failed
mm:e7413769-f241-4695-af14-0723d9322cbc on every retry.
"""

from types import SimpleNamespace

import pytest

from moonmind.omnigent.codex_execution_decisions import bind_exact_host
from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.adapters.omnigent_agent_adapter import (
    OmnigentResolvedTarget,
    build_omnigent_selection,
    build_omnigent_session_create_payload,
)


def _request(session: dict | None = None) -> AgentExecutionRequest:
    omnigent: dict = {}
    if session is not None:
        omnigent["session"] = session
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="mm:wf",
        idempotencyKey="mm:wf:node-1",
        parameters={"omnigent": omnigent},
    )


def _bind_profile(request: AgentExecutionRequest, harness: str) -> AgentExecutionRequest:
    return bind_exact_host(
        request,
        host_id="host-1",
        workspace_path="/workspaces/run",
        profile_authorization={},
        harness=harness,
        agent_name=f"{harness}-ui",
    )


def _bind_generic(request: AgentExecutionRequest, harness: str) -> AgentExecutionRequest:
    plan = SimpleNamespace(
        planRef="plan-1",
        payload=SimpleNamespace(
            agentSource={"upstreamId": "agent-1"},
            endpointRef="endpoint-1",
            harnessId=harness,
            hostClassRef="host-class-1",
            launchPolicyRef="launch-1",
            capturePolicy={},
            modelConfig=SimpleNamespace(qualifiedId="model-1", effort="high"),
        ),
    )
    binding = SimpleNamespace(
        bindingId="binding-1",
        providerLeases={},
        hostBindingRef="host-binding-1",
        hostLeaseRef="host-lease-1",
    )
    realizer = object.__new__(GenericOmnigentHostRealizer)
    return realizer._bind_exact_host(
        request,
        plan,
        {"omnigentHostId": "host-1", "workspacePath": "/workspaces/run"},
        binding,
    )


def _launch_args(bound: AgentExecutionRequest) -> list[str]:
    payload = build_omnigent_session_create_payload(
        request=bound,
        selection=build_omnigent_selection(bound),
        target=OmnigentResolvedTarget(agent_id="agent-1", source="agent_id"),
    )
    return payload["terminal_launch_args"]


_BINDERS = pytest.mark.parametrize("bind", [_bind_profile, _bind_generic])


@_BINDERS
def test_claude_workflow_session_launches_without_permission_prompts(bind):
    assert _launch_args(bind(_request(), "claude-native")) == [
        "--permission-mode",
        "bypassPermissions",
    ]


@_BINDERS
@pytest.mark.parametrize(
    "explicit",
    [
        ["--permission-mode", "plan"],
        ["--permission-mode=acceptEdits"],
        ["--dangerously-skip-permissions"],
    ],
)
def test_explicit_claude_permission_choice_is_preserved(bind, explicit):
    request = _request({"terminalLaunchArgs": ["--verbose", *explicit]})

    assert _launch_args(bind(request, "claude-native")) == ["--verbose", *explicit]


@_BINDERS
def test_unattended_mode_is_added_beside_other_launch_args(bind):
    request = _request({"terminalLaunchArgs": ["--verbose"]})

    assert _launch_args(bind(request, "claude-native")) == [
        "--verbose",
        "--permission-mode",
        "bypassPermissions",
    ]


@_BINDERS
@pytest.mark.parametrize("harness", ["codex-native", "opencode-native"])
def test_other_harnesses_receive_no_claude_flag(bind, harness):
    assert _launch_args(bind(_request(), harness)) == []
