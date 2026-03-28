from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, ANY

import pytest

from moonmind.workflows.temporal.worker_runtime import (
    MoonMindAgentRun,
    MoonMindManifestIngest,
    MoonMindRun,
    _enforce_codex_config_for_managed_fleet,
    _build_runtime_planner,
    _build_runtime_activities,
    get_activity_route,
    main_async,
    resolve_external_adapter,
)
from moonmind.workflows.temporal.workflows.agent_run import (
    external_adapter_execution_style,
)
from moonmind.workflows.temporal.workers import AGENT_RUNTIME_FLEET, SANDBOX_FLEET, WORKFLOW_FLEET


def test_runtime_planner_preserves_execution_profile_ref():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "instructions": "Use the minimax profile for this Claude run.",
                "runtime": {
                    "mode": "claude",
                    "executionProfileRef": "claude-minimax-oauth",
                },
            }
        },
        parameters={"targetRuntime": "claude"},
        snapshot=snapshot,
    )

    runtime_node = plan["nodes"][0]["inputs"]["runtime"]
    assert runtime_node["mode"] == "claude"
    assert runtime_node["executionProfileRef"] == "claude-minimax-oauth"


def test_runtime_planner_preserves_execution_profile_ref_snake_case():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "instructions": "Anthropic API key profile.",
                "runtime": {
                    "mode": "claude",
                    "execution_profile_ref": "anthropic-work",
                },
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    assert (
        plan["nodes"][0]["inputs"]["runtime"]["executionProfileRef"]
        == "anthropic-work"
    )


def test_runtime_planner_embeds_skill_inputs_for_generated_skill_instructions():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "tool": {
                    "type": "skill",
                    "name": "pr-resolver",
                    "version": "1.0",
                    "inputs": {"pr": "123", "repo": "MoonLadderStudios/MoonMind"},
                },
                "runtime": {"mode": "gemini_cli"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    node_inputs = plan["nodes"][0]["inputs"]
    assert node_inputs["instructions"].startswith(
        "Execute skill 'pr-resolver' with inputs:"
    )
    assert '"pr": "123"' in node_inputs["instructions"]
    assert node_inputs["repo"] == "MoonLadderStudios/MoonMind"


def test_runtime_planner_pr_resolver_injects_branch_selector_into_instruction():
    """When pr-resolver has no inputs.pr but git.startingBranch is set,
    the auto-generated instruction must include the branch as the pr selector
    so the agent knows which PR to target."""
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "tool": {
                    "type": "skill",
                    "name": "pr-resolver",
                    "version": "1.0",
                },
                "git": {"startingBranch": "fix/my-feature-branch"},
                "runtime": {"mode": "gemini_cli"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    node_inputs = plan["nodes"][0]["inputs"]
    assert node_inputs["instructions"].startswith(
        "Execute skill 'pr-resolver' with inputs:"
    )
    assert '"pr": "fix/my-feature-branch"' in node_inputs["instructions"]


def test_runtime_planner_requires_selector_for_pr_resolver_without_instructions():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "pr-resolver task requires task.tool.inputs.pr or "
            "task.git.startingBranch"
        ),
    ):
        planner(
            inputs={
                "task": {
                    "tool": {
                        "type": "skill",
                        "name": "pr-resolver",
                        "version": "1.0",
                    },
                    "runtime": {"mode": "gemini_cli"},
                }
            },
            parameters={},
            snapshot=snapshot,
        )


def _make_snapshot():
    return SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )


def test_runtime_planner_multi_step_generates_multiple_nodes_with_edges():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Objective",
                "steps": [
                    {"id": "s1", "instructions": "Step one instructions"},
                    {"id": "s2", "instructions": "Step two instructions"},
                    {"id": "s3", "instructions": "Step three instructions"},
                ],
                "runtime": {"mode": "jules"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    nodes = plan["nodes"]
    edges = plan["edges"]

    assert len(nodes) == 3
    assert nodes[0]["id"] == "s1"
    assert nodes[0]["inputs"]["instructions"] == "Step one instructions"
    assert nodes[1]["id"] == "s2"
    assert nodes[1]["inputs"]["instructions"] == "Step two instructions"
    assert nodes[2]["id"] == "s3"
    assert nodes[2]["inputs"]["instructions"] == "Step three instructions"

    # All nodes use agent_runtime tool type
    for node in nodes:
        assert node["tool"]["type"] == "agent_runtime"
        assert node["tool"]["name"] == "jules"

    # Sequential edges
    assert len(edges) == 2
    assert edges[0] == {"from": "s1", "to": "s2"}
    assert edges[1] == {"from": "s2", "to": "s3"}


def test_runtime_planner_multi_step_preserves_custom_keys():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Objective",
                "steps": [
                    {
                        "id": "s1",
                        "instructions": "Step one instructions",
                        "custom_field": "custom_value",
                        "another_field": 42,
                        "tool": {"name": "ignored_in_inputs"},
                    },
                    {
                        "id": "s2",
                        "instructions": "Step two instructions",
                    }
                ],
                "runtime": {"mode": "jules"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    nodes = plan["nodes"]
    assert len(nodes) == 2
    
    s1_inputs = nodes[0]["inputs"]
    assert s1_inputs["instructions"] == "Step one instructions"
    assert s1_inputs["custom_field"] == "custom_value"
    assert s1_inputs["another_field"] == 42
    assert "id" not in s1_inputs
    assert "tool" not in s1_inputs
    assert "skill" not in s1_inputs

    s2_inputs = nodes[1]["inputs"]
    assert s2_inputs["instructions"] == "Step two instructions"
    assert "custom_field" not in s2_inputs


def test_runtime_planner_single_step_falls_back_to_single_node():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Do the thing",
                "steps": [
                    {"id": "only", "instructions": "Only step"},
                ],
                "runtime": {"mode": "jules"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    # Single-step array should use the single-node fallback path
    assert len(plan["nodes"]) == 1
    assert plan["edges"] == []
    assert plan["nodes"][0]["inputs"]["instructions"] == "Do the thing"


def test_runtime_planner_no_steps_falls_back_to_single_node():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Do the thing",
                "runtime": {"mode": "jules"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    assert len(plan["nodes"]) == 1
    assert plan["edges"] == []
    assert plan["nodes"][0]["inputs"]["instructions"] == "Do the thing"


def test_runtime_planner_multi_step_step_fallback_instructions():
    """When a step has no instructions, the task-level instructions are used."""
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Task-level fallback",
                "steps": [
                    {"id": "a", "instructions": "Explicit step A"},
                    {"id": "b"},  # no instructions
                ],
                "runtime": {"mode": "jules"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    assert len(plan["nodes"]) == 2
    assert plan["nodes"][0]["inputs"]["instructions"] == "Explicit step A"
    assert plan["nodes"][1]["inputs"]["instructions"] == "Task-level fallback"


def test_runtime_planner_multi_step_auto_generated_ids():
    """When steps lack explicit IDs, sequential IDs are generated."""
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Objective",
                "steps": [
                    {"instructions": "First"},
                    {"instructions": "Second"},
                ],
                "runtime": {"mode": "jules"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    assert plan["nodes"][0]["id"] == "step-1"
    assert plan["nodes"][1]["id"] == "step-2"
    assert plan["edges"] == [{"from": "step-1", "to": "step-2"}]


def test_runtime_planner_publish_pr_appends_gh_suffix_for_cli_runtimes():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Do work",
                "runtime": {"mode": "gemini_cli"},
                "publish": {"mode": "pr"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    text = plan["nodes"][-1]["inputs"]["instructions"]
    assert "commit your work" in text
    assert "Do NOT push or create a pull request" in text
    assert "gh pr create" not in text


def test_runtime_planner_publish_pr_skips_gh_suffix_for_jules():
    """Jules uses API automationMode AUTO_CREATE_PR; do not inject gh CLI text."""
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Do work",
                "runtime": {"mode": "jules"},
                "publish": {"mode": "pr"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    text = plan["nodes"][-1]["inputs"]["instructions"]
    assert text == "Do work"
    assert "gh pr create" not in text


def test_enforce_codex_config_skips_non_managed_fleet() -> None:
    with patch("api_service.scripts.ensure_codex_config.ensure_codex_config") as mock_ensure:
        _enforce_codex_config_for_managed_fleet(WORKFLOW_FLEET)
    mock_ensure.assert_not_called()


@pytest.mark.parametrize("fleet", [SANDBOX_FLEET, AGENT_RUNTIME_FLEET])
def test_enforce_codex_config_runs_for_managed_fleets(fleet: str) -> None:
    with patch("api_service.scripts.ensure_codex_config.ensure_codex_config") as mock_ensure:
        mock_ensure.return_value = SimpleNamespace(path="/tmp/codex-config.toml")
        _enforce_codex_config_for_managed_fleet(fleet)
    mock_ensure.assert_called_once_with()


@pytest.mark.asyncio
@patch("moonmind.workflows.temporal.worker_runtime.start_healthcheck_server")
@patch("moonmind.workflows.temporal.worker_runtime.describe_configured_worker")
@patch("moonmind.workflows.temporal.worker_runtime.Client.connect")
@patch("moonmind.workflows.temporal.worker_runtime.Worker")
async def test_main_async_workflow_fleet(mock_worker_cls, mock_connect, mock_describe, mock_healthcheck):
    # Setup mocks
    mock_healthcheck.return_value = AsyncMock()
    mock_topology = MagicMock()
    mock_topology.fleet = WORKFLOW_FLEET
    mock_topology.task_queues = ["mm.workflow"]
    mock_topology.concurrency_limit = 7
    mock_describe.return_value = mock_topology

    mock_client = MagicMock()
    mock_connect.return_value = mock_client

    mock_worker = MagicMock()
    mock_worker_cls.return_value = mock_worker
    mock_worker.run = AsyncMock()

    # Run
    await main_async()

    # Verify Worker creation uses the mock workflows
    mock_worker_cls.assert_called_once()
    kwargs = mock_worker_cls.call_args.kwargs
    assert kwargs["task_queue"] == "mm.workflow"
    from moonmind.workflows.temporal.workflows.auth_profile_manager import MoonMindAuthProfileManagerWorkflow
    from moonmind.workflows.temporal.workflows.oauth_session import MoonMindOAuthSessionWorkflow as MoonMindOAuthSession
    assert kwargs["workflows"] == [
        MoonMindRun,
        MoonMindManifestIngest,
        MoonMindAuthProfileManagerWorkflow,
        MoonMindAgentRun,
        MoonMindOAuthSession,
    ]
    assert kwargs["activities"] == [
        resolve_external_adapter,
        external_adapter_execution_style,
        get_activity_route,
    ]
    assert kwargs["max_concurrent_workflow_tasks"] == 7
    assert "max_concurrent_activities" not in kwargs

    # Verify worker run is called
    mock_worker.run.assert_awaited_once()


@pytest.mark.asyncio
@patch("moonmind.workflows.temporal.worker_runtime.start_healthcheck_server")
@patch("moonmind.workflows.temporal.worker_runtime._build_runtime_activities")
@patch("moonmind.workflows.temporal.worker_runtime.describe_configured_worker")
@patch("moonmind.workflows.temporal.worker_runtime.Client.connect")
@patch("moonmind.workflows.temporal.worker_runtime.Worker")
async def test_main_async_activity_fleet(
    mock_worker_cls, mock_connect, mock_describe, mock_runtime_activities, mock_healthcheck
):
    # Setup mocks
    mock_healthcheck.return_value = AsyncMock()
    mock_topology = MagicMock()
    mock_topology.fleet = "artifacts"
    mock_topology.task_queues = ["mm.activity.artifacts"]
    mock_topology.concurrency_limit = 3
    mock_describe.return_value = mock_topology

    mock_client = MagicMock()
    mock_connect.return_value = mock_client

    mock_worker = MagicMock()
    mock_worker_cls.return_value = mock_worker
    mock_worker.run = AsyncMock()

    mock_resources = AsyncMock()
    mock_runtime_activities.return_value = (mock_resources, ["test_handler"])

    # Run
    await main_async()

    # Verify Worker creation uses activities
    mock_worker_cls.assert_called_once()
    kwargs = mock_worker_cls.call_args.kwargs
    assert kwargs["task_queue"] == "mm.activity.artifacts"
    assert kwargs["workflows"] == []
    assert kwargs["activities"] == ["test_handler"]
    assert kwargs["max_concurrent_activities"] == 3
    assert "max_concurrent_workflow_tasks" not in kwargs

    # Verify worker run is called
    mock_runtime_activities.assert_awaited_once_with(mock_topology)
    mock_worker.run.assert_awaited_once()
    mock_resources.aclose.assert_awaited_once()


@pytest.mark.asyncio
@patch("moonmind.workflows.temporal.worker_runtime._build_agent_runtime_deps")
@patch("moonmind.workflows.temporal.worker_runtime.build_worker_activity_bindings")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalAgentRuntimeActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalIntegrationActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalSandboxActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalSkillActivities")
@patch("moonmind.workflows.temporal.worker_runtime.SkillActivityDispatcher")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalPlanActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalArtifactActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalArtifactService")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalArtifactRepository")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalProposalActivities")
async def test_build_runtime_activities_injects_concrete_handlers(
    mock_proposal_activities_cls,
    mock_repository_cls,
    mock_service_cls,
    mock_artifact_activities_cls,
    mock_plan_activities_cls,
    mock_dispatcher_cls,
    mock_skill_activities_cls,
    mock_sandbox_activities_cls,
    mock_jules_activities_cls,
    mock_agent_runtime_activities_cls,
    mock_build_bindings,
    mock_build_deps,
):
    run_store = MagicMock()
    run_supervisor = MagicMock()
    run_supervisor.reconcile = AsyncMock(return_value=[])
    run_launcher = MagicMock()
    mock_build_deps.return_value = (run_store, run_supervisor, run_launcher)
    @asynccontextmanager
    async def _fake_session_context():
        yield "session"

    topology = MagicMock()
    topology.fleet = "artifacts"

    mock_binding = MagicMock()
    mock_binding.handler = "artifact_handler"
    mock_build_bindings.return_value = [mock_binding]

    with patch(
        "moonmind.workflows.temporal.worker_runtime.get_async_session_context",
        side_effect=_fake_session_context,
    ):
        resources, handlers = await _build_runtime_activities(topology)

    assert handlers == [
        "artifact_handler",
        resolve_external_adapter,
        external_adapter_execution_style,
        get_activity_route,
    ]
    mock_artifact_activities_cls.assert_called_once_with(ANY)
    mock_plan_activities_cls.assert_called_once_with(
        artifact_service=ANY,
        planner=ANY,
    )
    mock_sandbox_activities_cls.assert_called_once_with(
        artifact_service=ANY
    )
    mock_jules_activities_cls.assert_called_once_with(
        artifact_service=ANY
    )
    mock_agent_runtime_activities_cls.assert_called_once_with(
        artifact_service=ANY,
        run_store=ANY,
        run_supervisor=ANY,
        run_launcher=ANY,
    )
    run_supervisor.reconcile.assert_awaited_once()
    mock_dispatcher_cls.assert_called_once_with()
    mock_skill_activities_cls.assert_called_once_with(
        dispatcher=mock_dispatcher_cls.return_value,
        artifact_service=ANY,
    )
    mock_build_bindings.assert_called_once_with(
        fleet="artifacts",
        artifact_activities=mock_artifact_activities_cls.return_value,
        plan_activities=mock_plan_activities_cls.return_value,
        skill_activities=mock_skill_activities_cls.return_value,
        sandbox_activities=mock_sandbox_activities_cls.return_value,
        integration_activities=mock_jules_activities_cls.return_value,
        agent_runtime_activities=mock_agent_runtime_activities_cls.return_value,
        proposal_activities=mock_proposal_activities_cls.return_value,
        review_activities=ANY,
    )
    mock_proposal_activities_cls.assert_called_once_with(
        artifact_service=ANY,
        proposal_service_factory=ANY,
    )
    proposal_service_factory = mock_proposal_activities_cls.call_args.kwargs[
        "proposal_service_factory"
    ]
    assert callable(proposal_service_factory)
    import typing
    assert isinstance(proposal_service_factory(), typing.AsyncContextManager)
    await resources.aclose()
