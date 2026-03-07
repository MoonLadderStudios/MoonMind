from unittest.mock import MagicMock, patch

import pytest

from moonmind.workflows.temporal.worker_runtime import (
    MoonMindManifestIngest,
    MoonMindRun,
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
    mock_describe.return_value = mock_topology

    mock_client = MagicMock()
    mock_connect.return_value = mock_client

    mock_worker = MagicMock()
    mock_worker_cls.return_value = mock_worker
    mock_worker.run = MagicMock(
        return_value=None
    )  # Must be async if awaited, but wait, run() is awaited
    import asyncio

    future = asyncio.Future()
    future.set_result(None)
    mock_worker.run.return_value = future

    # Run
    await main_async()

    # Verify Worker creation uses the mock workflows
    mock_worker_cls.assert_called_once()
    kwargs = mock_worker_cls.call_args.kwargs
    assert kwargs["task_queue"] == "mm.workflow"
    assert kwargs["workflows"] == [MoonMindRun, MoonMindManifestIngest]
    assert kwargs["activities"] == []

    # Verify worker run is called
    mock_worker.run.assert_called_once()


@pytest.mark.asyncio
@patch("moonmind.workflows.temporal.worker_runtime.build_worker_activity_bindings")
@patch("moonmind.workflows.temporal.worker_runtime.describe_configured_worker")
@patch("moonmind.workflows.temporal.worker_runtime.Client.connect")
@patch("moonmind.workflows.temporal.worker_runtime.Worker")
async def test_main_async_activity_fleet(
    mock_worker_cls, mock_connect, mock_describe, mock_bindings
):
    # Setup mocks
    mock_topology = MagicMock()
    mock_topology.fleet = "artifacts"
    mock_topology.task_queues = ["mm.activity.artifacts"]
    mock_describe.return_value = mock_topology

    mock_client = MagicMock()
    mock_connect.return_value = mock_client

    mock_worker = MagicMock()
    mock_worker_cls.return_value = mock_worker
    import asyncio

    future = asyncio.Future()
    future.set_result(None)
    mock_worker.run.return_value = future

    mock_bind = MagicMock()
    mock_bind.handler = "test_handler"
    mock_bindings.return_value = [mock_bind]

    # Run
    await main_async()

    # Verify Worker creation uses activities
    mock_worker_cls.assert_called_once()
    kwargs = mock_worker_cls.call_args.kwargs
    assert kwargs["task_queue"] == "mm.activity.artifacts"
    assert kwargs["workflows"] == []
    assert kwargs["activities"] == ["test_handler"]

    # Verify worker run is called
    mock_worker.run.assert_called_once()
