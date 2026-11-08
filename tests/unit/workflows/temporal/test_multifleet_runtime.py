"""Process ownership and isolated queue contracts for co-located fleets."""

import asyncio
from contextlib import AsyncExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from moonmind.workflows.temporal import worker_runtime as runtime
from moonmind.workflows.temporal.worker_healthcheck import (
    WorkerHealthState,
    _build_response_body,
)


@pytest.fixture
def process(monkeypatch):
    events = []
    topologies = [
        SimpleNamespace(
            fleet="llm",
            service_name="temporal-worker-llm",
            task_queues=("llm",),
            activity_types=(),
            concurrency_limit=3,
            queue_concurrency_limits={},
        ),
        SimpleNamespace(
            fleet="sandbox",
            service_name="temporal-worker-sandbox",
            task_queues=("sandbox",),
            activity_types=(),
            concurrency_limit=7,
            queue_concurrency_limits={},
        ),
    ]
    monkeypatch.setattr(
        runtime,
        "describe_configured_workers",
        lambda **kw: tuple(topologies),
        raising=False,
    )
    monkeypatch.setattr(
        runtime, "enforce_obsolete_configuration_at_startup", lambda *a: None
    )
    server = MagicMock()
    server.wait_closed = AsyncMock()
    health = AsyncMock(return_value=server)
    monkeypatch.setattr(runtime, "start_healthcheck_server", health)
    connect = AsyncMock(return_value=object())
    monkeypatch.setattr(runtime.Client, "connect", connect)

    async def build(topology):
        stack = AsyncExitStack()
        stack.callback(events.append, "close:" + topology.fleet)
        return stack, [topology.fleet]

    monkeypatch.setattr(runtime, "_build_runtime_activities", build)

    def spec(**kwargs):
        fleet = kwargs["topology"].fleet
        return SimpleNamespace(
            workflows=(),
            activities=kwargs["activities"],
            readiness_payload=lambda: {
                "fleet": fleet,
                "taskQueues": [fleet],
                "immutableReleaseIdentity": True,
                "buildId": "sha256:release",
                "registryFingerprint": "registry:" + fleet,
                "workflowTypes": [],
                "activityTypes": [fleet + ".execute"],
            },
        )

    monkeypatch.setattr(runtime, "build_worker_spec", spec)
    workers = []

    def worker(client, **kwargs):
        item = SimpleNamespace(
            is_running=True, run=AsyncMock(), shutdown=AsyncMock(), kwargs=kwargs
        )
        workers.append(item)
        return item

    monkeypatch.setattr(runtime, "Worker", worker)
    return SimpleNamespace(
        events=events,
        topologies=topologies,
        health=health,
        server=server,
        connect=connect,
        workers=workers,
    )


@pytest.mark.asyncio
async def test_multiple_fleets_share_process_keep_queue_limits(process):
    await runtime.main_async()
    assert process.connect.await_count == 1
    assert process.health.await_count == 1
    assert [
        (
            w.kwargs["task_queue"],
            w.kwargs["activities"],
            w.kwargs["max_concurrent_activities"],
        )
        for w in process.workers
    ] == [("llm", ["llm"], 3), ("sandbox", ["sandbox"], 7)]
    state = process.health.call_args.args[0]
    assert state.readiness_metadata["fleets"] == ["llm", "sandbox"]
    assert state.readiness_metadata["buildId"] == "sha256:release"
    assert state.readiness_metadata["taskQueues"] == ["llm", "sandbox"]
    assert state.readiness_metadata["activityTypes"] == [
        "llm.execute",
        "sandbox.execute",
    ]
    assert state.readiness_metadata["registryFingerprints"] == [
        "registry:llm",
        "registry:sandbox",
    ]
    assert set(state.readiness_metadata["fleetSpecs"]) == {"llm", "sandbox"}
    assert state.readiness_metadata["immutableReleaseIdentity"] is True
    assert process.events == ["close:sandbox", "close:llm"]
    process.server.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [RuntimeError("connect failed"), asyncio.CancelledError()]
)
async def test_connection_failure_or_cancellation_closes_health(process, error):
    process.connect.side_effect = error
    with pytest.raises(type(error)):
        await runtime.main_async()
    process.server.close.assert_called_once()
    process.server.wait_closed.assert_awaited_once()


@pytest.mark.asyncio
async def test_later_fleet_setup_failure_closes_earlier_resources(process, monkeypatch):
    original = runtime._build_runtime_activities

    async def build(topology):
        if topology.fleet == "sandbox":
            raise RuntimeError("second fleet failed")
        return await original(topology)

    monkeypatch.setattr(runtime, "_build_runtime_activities", build)
    with pytest.raises(RuntimeError, match="second fleet failed"):
        await runtime.main_async()
    assert process.events == ["close:llm"]
    assert not process.health.call_args.args[0].ready
    process.server.close.assert_called_once()


def test_health_uses_selected_fleets_not_stale_singular_environment(monkeypatch):
    import json

    monkeypatch.setenv("TEMPORAL_WORKER_FLEET", "artifacts")
    state = WorkerHealthState(
        readiness_metadata={"fleet": "multi", "fleets": ["llm", "sandbox"]}
    )
    for readiness in (False, True):
        body = json.loads(_build_response_body(state, readiness=readiness))
        assert body["fleet"] == "multi"
        assert body["fleets"] == ["llm", "sandbox"]
        assert "eventLoopLagSeconds" in body


@pytest.mark.asyncio
async def test_shared_transport_closes_once_after_all_fleets_drain(
    process, monkeypatch
):
    from moonmind.workflows.adapters import omnigent_client
    from moonmind.workflows.temporal import worker_lifecycle

    for topology in process.topologies:
        topology.activity_types = ("omnigent.status",)
    initialize = AsyncMock()
    close = AsyncMock(side_effect=lambda: process.events.append("transport:closed"))
    monkeypatch.setattr(omnigent_client, "init_shared_pool_client", initialize)
    monkeypatch.setattr(omnigent_client, "aclose_shared_pool_client", close)

    async def serve(workers, *, ready, stopping):
        await ready()
        stopping()
        assert close.await_count == 0
        process.events.append("all:drained")

    monkeypatch.setattr(worker_lifecycle, "serve_workers", serve)
    await runtime.main_async()
    initialize.assert_awaited_once()
    close.assert_awaited_once()
    assert process.events == [
        "all:drained",
        "close:sandbox",
        "close:llm",
        "transport:closed",
    ]


@pytest.mark.asyncio
async def test_cleanup_failure_still_closes_health(process, monkeypatch):
    async def build(topology):
        stack = AsyncExitStack()

        def fail():
            raise RuntimeError("cleanup failed")

        stack.callback(fail)
        return stack, []

    monkeypatch.setattr(runtime, "_build_runtime_activities", build)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await runtime.main_async()
    process.server.close.assert_called_once()


@pytest.mark.asyncio
async def test_event_loop_lag_records_and_stops_on_cancellation():
    from moonmind.workflows.temporal.worker_healthcheck import _measure_event_loop_lag

    state = WorkerHealthState()
    task = asyncio.create_task(_measure_event_loop_lag(state, interval=0.001))
    try:
        await asyncio.sleep(0.01)
        assert state.event_loop_lag_seconds >= 0
        assert state.event_loop_lag_max_seconds > 0
        assert state.event_loop_lag_max_seconds >= state.event_loop_lag_seconds
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_runtime_logging_uses_actual_selection_not_legacy_marker(monkeypatch):
    import logging

    monkeypatch.setenv("TEMPORAL_WORKER_FLEET", "artifacts")
    fields = {
        "service": "temporal-worker-multi",
        "component": "temporal-worker",
        "worker_fleet": "workflow,llm",
        "worker_id": "test",
    }
    log_filter = runtime.OpenTelemetryLoggingFilter(default_fields=fields)
    record = logging.LogRecord("worker", logging.INFO, __file__, 1, "test", (), None)
    log_filter.filter(record)
    assert record.worker_fleet == "workflow,llm"
    assert record.service == "temporal-worker-multi"


@pytest.mark.asyncio
async def test_metrics_runtime_and_client_are_process_scoped(process, monkeypatch):
    from moonmind.observability import telemetry

    monkeypatch.setenv("MOONMIND_ENABLE_OPENTELEMETRY", "1")
    monkeypatch.setattr(runtime, "_configure_worker_logging", lambda **kwargs: None)
    initialize = MagicMock()
    monkeypatch.setattr(telemetry, "initialize_telemetry", initialize)
    metrics_runtime = MagicMock()
    monkeypatch.setattr(runtime, "Runtime", metrics_runtime)
    await runtime.main_async()
    initialize.assert_called_once()
    metrics_runtime.assert_called_once()
    assert process.connect.call_args.kwargs["runtime"] is metrics_runtime.return_value


@pytest.mark.asyncio
async def test_cli_override_is_forwarded_without_rewriting_environment(
    process, monkeypatch
):
    monkeypatch.setenv("TEMPORAL_WORKER_FLEET", "artifacts")
    resolve = MagicMock(return_value=tuple(process.topologies))
    monkeypatch.setattr(runtime, "describe_configured_workers", resolve)
    await runtime.main_async(fleet="llm")
    resolve.assert_called_once_with(fleet="llm")
    import os

    assert os.environ["TEMPORAL_WORKER_FLEET"] == "artifacts"


@pytest.mark.asyncio
async def test_event_loop_lag_warning_is_numeric_and_diagnostic(monkeypatch):
    from moonmind.workflows.temporal import worker_healthcheck as health

    state = WorkerHealthState()
    times = iter([0.0, 1.25, 2.0])
    warning = MagicMock()
    with monkeypatch.context() as patch:
        patch.setattr(
            health.asyncio,
            "get_running_loop",
            lambda: SimpleNamespace(time=lambda: next(times)),
        )
        patch.setattr(
            health.asyncio,
            "sleep",
            AsyncMock(side_effect=[None, asyncio.CancelledError()]),
        )
        patch.setattr(health.logger, "warning", warning)
        with pytest.raises(asyncio.CancelledError):
            await health._measure_event_loop_lag(state)
    warning.assert_called_once_with(
        "Temporal worker event loop lag: %.6f seconds", 0.25
    )
    assert state.event_loop_lag_seconds == 0.25
    assert state.startup_error is None


@pytest.mark.asyncio
async def test_combined_process_retains_release_and_agent_backend_probes(
    process, monkeypatch
):
    from moonmind.workflows.skills.deployment_release import readiness_matches

    process.topologies[1].fleet = "agent_runtime"
    process.topologies[1].task_queues = ("agent_runtime",)
    monkeypatch.setattr(runtime, "_initialize_process_transports", AsyncMock())
    await runtime.main_async()
    metadata = process.health.call_args.args[0].readiness_metadata
    assert readiness_matches({**metadata, "ready": True}, "sha256:release")
    assert (
        metadata["containerBackend"]
        == metadata["fleetSpecs"]["agent_runtime"]["containerBackend"]
    )
    assert "agent_runtime" in metadata["taskQueues"]
    assert "agent_runtime.execute" in metadata["activityTypes"]
