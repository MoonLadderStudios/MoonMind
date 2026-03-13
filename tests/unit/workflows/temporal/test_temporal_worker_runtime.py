import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from moonmind.workflows.temporal.worker_runtime import (
    MoonMindManifestIngest,
    MoonMindRun,
    _build_runtime_activities,
    _build_runtime_planner,
    main_async,
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
    assert kwargs["workflows"] == [MoonMindRun, MoonMindManifestIngest]
    assert kwargs["activities"] == []
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
@patch("moonmind.workflows.temporal.worker_runtime.build_worker_activity_bindings")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalJulesActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalSandboxActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalSkillActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalPlanActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalArtifactActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalArtifactService")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalArtifactRepository")
@patch("moonmind.workflows.temporal.worker_runtime.SkillActivityDispatcher")
async def test_build_runtime_activities_injects_concrete_handlers(
    mock_dispatcher_cls,
    mock_repository_cls,
    mock_service_cls,
    mock_artifact_activities_cls,
    mock_plan_activities_cls,
    mock_skill_activities_cls,
    mock_sandbox_activities_cls,
    mock_jules_activities_cls,
    mock_build_bindings,
):
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

    assert handlers == ["artifact_handler"]
    mock_repository_cls.assert_called_once_with("session")
    mock_service_cls.assert_called_once_with(mock_repository_cls.return_value)
    mock_artifact_activities_cls.assert_called_once_with(mock_service_cls.return_value)
    from unittest.mock import ANY

    mock_plan_activities_cls.assert_called_once_with(
        artifact_service=mock_service_cls.return_value,
        planner=ANY,
    )
    mock_skill_activities_cls.assert_called_once_with(
        dispatcher=mock_dispatcher_cls.return_value,
        artifact_service=mock_service_cls.return_value,
    )
    mock_sandbox_activities_cls.assert_called_once_with(
        artifact_service=mock_service_cls.return_value
    )
    mock_jules_activities_cls.assert_called_once_with(
        artifact_service=mock_service_cls.return_value
    )
    mock_build_bindings.assert_called_once_with(
        fleet="artifacts",
        artifact_activities=mock_artifact_activities_cls.return_value,
        plan_activities=mock_plan_activities_cls.return_value,
        skill_activities=mock_skill_activities_cls.return_value,
        sandbox_activities=mock_sandbox_activities_cls.return_value,
        integration_activities=mock_jules_activities_cls.return_value,
    )
    await resources.aclose()


def test_runtime_planner_never_emits_placeholder_registry_refs():
    planner = _build_runtime_planner()
    snapshot = MagicMock()
    snapshot.digest = "reg:sha256:" + ("a" * 64)
    snapshot.artifact_ref = "art_01HJ4M3Y7RM4C5S2P3Q8G6T7V8"

    payload = planner(
        {
            "task": {
                "instructions": "Summarize the latest CI failure.",
                "runtime": {
                    "mode": "codex",
                    "model": "gpt-5",
                    "effort": "high",
                },
            }
        },
        {"repository": "moonladder/moonmind"},
        snapshot,
    )

    rendered = json.dumps(payload, sort_keys=True)
    assert "sha256:dummy" not in rendered
    assert payload["metadata"]["registry_snapshot"]["digest"] == snapshot.digest
    assert (
        payload["metadata"]["registry_snapshot"]["artifact_ref"]
        == snapshot.artifact_ref
    )


def test_runtime_planner_accepts_task_tool_payload():
    planner = _build_runtime_planner()
    snapshot = MagicMock()
    snapshot.digest = "reg:sha256:" + ("b" * 64)
    snapshot.artifact_ref = "art_01HJ4M3Y7RM4C5S2P3Q8G6T7W9"

    payload = planner(
        {
            "task": {
                "tool": {
                    "type": "skill",
                    "name": "pr-resolver",
                    "version": "1.0",
                },
                "inputs": {"repo": "MoonLadderStudios/MoonMind", "pr": "42"},
            }
        },
        {"repository": "MoonLadderStudios/MoonMind"},
        snapshot,
    )

    node = payload["nodes"][0]
    assert node["tool"]["name"] == "pr-resolver"
    assert node["tool"]["version"] == "1.0"
    assert node["skill"]["name"] == "pr-resolver"
    assert node["inputs"]["pr"] == "42"


def test_runtime_planner_uses_parameter_task_fallback_when_inputs_missing():
    planner = _build_runtime_planner()
    snapshot = MagicMock()
    snapshot.digest = "reg:sha256:" + ("c" * 64)
    snapshot.artifact_ref = "art_01HJ4M3Y7RM4C5S2P3Q8G6T7X0"

    payload = planner(
        None,
        {
            "task": {
                "instructions": "Diagnose the failing workflow.",
                "tool": {"type": "skill", "name": "auto", "version": "1.0"},
                "runtime": {"mode": "codex"},
            },
            "targetRuntime": "codex",
            "repository": "MoonLadderStudios/MoonMind",
        },
        snapshot,
    )

    node = payload["nodes"][0]
    assert node["tool"]["name"] == "auto"
    assert node["inputs"]["instructions"] == "Diagnose the failing workflow."


@pytest.mark.asyncio
@patch("moonmind.workflows.temporal.worker_runtime.build_worker_activity_bindings")
@patch("moonmind.workflows.temporal.worker_runtime._build_runtime_planner")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalArtifactService")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalArtifactRepository")
@patch("moonmind.workflows.temporal.worker_runtime.SkillActivityDispatcher")
async def test_build_runtime_activities_fails_fast_when_planner_wiring_missing(
    mock_dispatcher_cls,
    mock_repository_cls,
    mock_service_cls,
    mock_build_runtime_planner,
    mock_build_bindings,
):
    @asynccontextmanager
    async def _fake_session_context():
        yield "session"

    topology = MagicMock()
    topology.fleet = "llm"
    mock_build_runtime_planner.return_value = None
    mock_build_bindings.return_value = []

    with patch(
        "moonmind.workflows.temporal.worker_runtime.get_async_session_context",
        side_effect=_fake_session_context,
    ):
        with pytest.raises(
            RuntimeError, match="Temporal runtime planner wiring is required"
        ):
            await _build_runtime_activities(topology)

    mock_repository_cls.assert_called_once_with("session")
    mock_service_cls.assert_called_once_with(mock_repository_cls.return_value)
    mock_dispatcher_cls.assert_called_once()
    mock_build_bindings.assert_not_called()


@pytest.mark.asyncio
@patch("moonmind.workflows.temporal.worker_runtime.build_worker_activity_bindings")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalJulesActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalSandboxActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalSkillActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalPlanActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalArtifactActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalArtifactService")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalArtifactRepository")
@patch("moonmind.workflows.temporal.worker_runtime.SkillActivityDispatcher")
async def test_auto_skill_handler_rejects_unsupported_runtime_mode(
    mock_dispatcher_cls,
    mock_repository_cls,
    mock_service_cls,
    mock_artifact_activities_cls,
    mock_plan_activities_cls,
    mock_skill_activities_cls,
    mock_sandbox_activities_cls,
    mock_jules_activities_cls,
    mock_build_bindings,
):
    @asynccontextmanager
    async def _fake_session_context():
        yield "session"

    topology = MagicMock()
    topology.fleet = "sandbox"
    mock_build_bindings.return_value = []

    with patch(
        "moonmind.workflows.temporal.worker_runtime.get_async_session_context",
        side_effect=_fake_session_context,
    ):
        resources, _handlers = await _build_runtime_activities(topology)

    register_kwargs = mock_dispatcher_cls.return_value.register_skill.call_args.kwargs
    handler = register_kwargs["handler"]
    result = await handler(
        {
            "instructions": "Inspect failing tests",
            "runtime": {"mode": "unsupported-runtime"},
        },
        {"workflow_id": "wf-1", "node_id": "node-1", "principal": "user-1"},
    )

    assert result.status == "FAILED"
    assert "unsupported" in result.outputs["error"]
    await resources.aclose()
