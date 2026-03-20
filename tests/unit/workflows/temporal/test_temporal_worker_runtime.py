from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch, ANY

import pytest

from moonmind.workflows.temporal.worker_runtime import (
    MoonMindAgentRun,
    MoonMindManifestIngest,
    MoonMindRun,
    _build_runtime_activities,
    main_async,
    resolve_external_adapter,
)
from moonmind.workflows.temporal.workflows.agent_run import (
    external_adapter_execution_style,
)
from moonmind.workflows.temporal.workers import WORKFLOW_FLEET


@pytest.mark.asyncio
@patch("moonmind.workflows.temporal.worker_runtime.describe_configured_worker")
@patch("moonmind.workflows.temporal.worker_runtime.Client.connect")
@patch("moonmind.workflows.temporal.worker_runtime.Worker")
async def test_main_async_workflow_fleet(mock_worker_cls, mock_connect, mock_describe):
    # Setup mocks
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
    assert kwargs["workflows"] == [
        MoonMindRun,
        MoonMindManifestIngest,
        MoonMindAuthProfileManagerWorkflow,
        MoonMindAgentRun,
    ]
    assert kwargs["activities"] == [
        resolve_external_adapter,
        external_adapter_execution_style,
    ]
    assert kwargs["max_concurrent_workflow_tasks"] == 7
    assert "max_concurrent_activities" not in kwargs

    # Verify worker run is called
    mock_worker.run.assert_awaited_once()


@pytest.mark.asyncio
@patch("moonmind.workflows.temporal.worker_runtime._build_runtime_activities")
@patch("moonmind.workflows.temporal.worker_runtime.describe_configured_worker")
@patch("moonmind.workflows.temporal.worker_runtime.Client.connect")
@patch("moonmind.workflows.temporal.worker_runtime.Worker")
async def test_main_async_activity_fleet(
    mock_worker_cls, mock_connect, mock_describe, mock_runtime_activities
):
    # Setup mocks
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
    mock_build_deps.return_value = (MagicMock(), MagicMock(), MagicMock())
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
    ]
    mock_repository_cls.assert_called_once_with("session")
    mock_service_cls.assert_called_once_with(mock_repository_cls.return_value)
    mock_artifact_activities_cls.assert_called_once_with(mock_service_cls.return_value)
    mock_plan_activities_cls.assert_called_once_with(
        artifact_service=mock_service_cls.return_value,
        planner=ANY,
    )
    mock_sandbox_activities_cls.assert_called_once_with(
        artifact_service=mock_service_cls.return_value
    )
    mock_jules_activities_cls.assert_called_once_with(
        artifact_service=mock_service_cls.return_value
    )
    mock_agent_runtime_activities_cls.assert_called_once_with(
        artifact_service=mock_service_cls.return_value,
        run_store=ANY,
        run_supervisor=ANY,
        run_launcher=ANY,
    )
    mock_dispatcher_cls.assert_called_once_with()
    mock_skill_activities_cls.assert_called_once_with(
        dispatcher=mock_dispatcher_cls.return_value,
        artifact_service=mock_service_cls.return_value,
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
        artifact_service=mock_service_cls.return_value,
    )
    await resources.aclose()
