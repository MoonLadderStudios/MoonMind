"""TDD tests for managed runtime activities — Phase 3 canonical return types.

Validates that agent_runtime_status, agent_runtime_cancel, and
agent_runtime_publish_artifacts return typed Pydantic contracts
(AgentRunStatus, AgentRunResult) instead of dict[str, Any] / None.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from moonmind.schemas.agent_runtime_models import (
    AgentRunResult,
    AgentRunStatus,
    ManagedRunRecord,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalActivityRuntimeError,
    TemporalAgentRuntimeActivities,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore

pytestmark = [pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> ManagedRunStore:
    return ManagedRunStore(tmp_path / "run_store")


def _save_record(
    store: ManagedRunStore,
    *,
    run_id: str,
    status: str,
    runtime_id: str = "codex_cli",
    failure_class: str | None = None,
    error_message: str | None = None,
) -> None:
    store.save(
        ManagedRunRecord(
            runId=run_id,
            agentId=runtime_id,
            runtimeId=runtime_id,
            status=status,
            startedAt=datetime.now(tz=UTC),
            failureClass=failure_class,
            errorMessage=error_message,
        )
    )


# ---------------------------------------------------------------------------
# T1: agent_runtime_status — typed AgentRunStatus return
# ---------------------------------------------------------------------------


async def test_status_running_record_returns_typed_model(tmp_path: Path) -> None:
    """T1.1 — running record yields typed AgentRunStatus."""
    store = _make_store(tmp_path)
    _save_record(store, run_id="run-1", status="running", runtime_id="codex_cli")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_status({"run_id": "run-1", "agent_id": "codex_cli"})

    assert isinstance(result, AgentRunStatus), f"Expected AgentRunStatus, got {type(result)}"
    assert result.status == "running"
    assert result.agent_kind == "managed"


async def test_status_completed_record_returns_typed_model(tmp_path: Path) -> None:
    """T1.2 — completed record yields typed AgentRunStatus with correct status."""
    store = _make_store(tmp_path)
    _save_record(store, run_id="run-2", status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_status({"run_id": "run-2", "agent_id": "codex_cli"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "completed"


async def test_status_failed_record_returns_typed_model_with_metadata(tmp_path: Path) -> None:
    """T1.3 — failed record yields typed AgentRunStatus with runtimeId in metadata."""
    store = _make_store(tmp_path)
    _save_record(
        store,
        run_id="run-3",
        status="failed",
        runtime_id="gemini_cli",
        failure_class="execution_error",
        error_message="Process exited with code 1",
    )

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_status({"run_id": "run-3", "agent_id": "gemini_cli"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "failed"
    assert result.metadata is not None
    assert result.metadata.get("runtimeId") == "gemini_cli"


async def test_status_no_record_returns_optimistic_running(tmp_path: Path) -> None:
    """T1.4 — missing record in store yields stub AgentRunStatus with status='running'."""
    store = _make_store(tmp_path)

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_status({"run_id": "no-such-run", "agent_id": "codex_cli"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "running"
    assert result.agent_kind == "managed"


async def test_status_missing_run_id_raises_error(tmp_path: Path) -> None:
    """T1.5 — missing run_id raises TemporalActivityRuntimeError."""
    store = _make_store(tmp_path)
    activities = TemporalAgentRuntimeActivities(run_store=store)

    with pytest.raises(TemporalActivityRuntimeError):
        await activities.agent_runtime_status({"agent_id": "codex_cli"})


# ---------------------------------------------------------------------------
# T2: agent_runtime_cancel — typed AgentRunStatus return (not None)
# ---------------------------------------------------------------------------


async def test_cancel_with_supervisor_returns_typed_status(tmp_path: Path) -> None:
    """T2.1 — cancel with supervisor returns AgentRunStatus with status='canceled'."""
    mock_supervisor = AsyncMock()
    mock_supervisor.cancel = AsyncMock()

    activities = TemporalAgentRuntimeActivities(
        run_supervisor=mock_supervisor,
    )
    result = await activities.agent_runtime_cancel({"agent_kind": "managed", "run_id": "run-x"})

    assert isinstance(result, AgentRunStatus), f"Expected AgentRunStatus, got {type(result)}"
    assert result.status == "canceled"
    assert result.agent_kind == "managed"


async def test_cancel_supervisor_exception_still_returns_typed_status(tmp_path: Path) -> None:
    """T2.2 — supervisor.cancel raising an exception still yields AgentRunStatus."""
    mock_supervisor = AsyncMock()
    mock_supervisor.cancel = AsyncMock(side_effect=RuntimeError("supervisor failed"))

    activities = TemporalAgentRuntimeActivities(
        run_supervisor=mock_supervisor,
    )
    result = await activities.agent_runtime_cancel({"agent_kind": "managed", "run_id": "run-y"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "canceled"


async def test_cancel_no_supervisor_store_path_returns_typed_status(tmp_path: Path) -> None:
    """T2.3 — no supervisor but store update still returns AgentRunStatus."""
    store = _make_store(tmp_path)
    _save_record(store, run_id="run-cancel-store", status="running")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_cancel({"agent_kind": "managed", "run_id": "run-cancel-store"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "canceled"


async def test_cancel_external_kind_returns_typed_status(tmp_path: Path) -> None:
    """T2.4 — external/unknown kind path still returns AgentRunStatus (best-effort)."""
    activities = TemporalAgentRuntimeActivities()
    result = await activities.agent_runtime_cancel({"agent_kind": "external", "run_id": "ext-run"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "canceled"


# ---------------------------------------------------------------------------
# T3: agent_runtime_publish_artifacts — typed AgentRunResult return
# ---------------------------------------------------------------------------


async def test_publish_artifacts_no_service_returns_result_unchanged() -> None:
    """T3.1 — no artifact service configured → passthrough (returns input model)."""
    original = AgentRunResult(summary="done", failure_class=None)
    activities = TemporalAgentRuntimeActivities()  # no artifact_service

    result = await activities.agent_runtime_publish_artifacts(original)

    assert isinstance(result, AgentRunResult)
    assert result.summary == "done"


async def test_publish_artifacts_none_input_returns_none() -> None:
    """T3.3 — None input returns None."""
    activities = TemporalAgentRuntimeActivities()
    result = await activities.agent_runtime_publish_artifacts(None)
    assert result is None


# ---------------------------------------------------------------------------
# T5: agent_runtime_fetch_result — typed AgentRunResult return
# ---------------------------------------------------------------------------


async def test_fetch_result_completed_returns_typed_model(tmp_path: Path) -> None:
    """T5.1 — completed run returns typed AgentRunResult with failure_class=None."""
    store = _make_store(tmp_path)
    _save_record(store, run_id="fr-1", status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_fetch_result({"run_id": "fr-1"})

    assert isinstance(result, AgentRunResult), f"Expected AgentRunResult, got {type(result)}"
    assert result.failure_class is None


async def test_fetch_result_failed_returns_typed_model(tmp_path: Path) -> None:
    """T5.2 — failed run returns typed AgentRunResult with correct failure_class."""
    store = _make_store(tmp_path)
    _save_record(
        store,
        run_id="fr-2",
        status="failed",
        failure_class="execution_error",
        error_message="Process exited with code 1",
    )

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_fetch_result({"run_id": "fr-2"})

    assert isinstance(result, AgentRunResult)
    assert result.failure_class == "execution_error"


async def test_fetch_result_forwards_pr_resolver_expected_flag(tmp_path: Path) -> None:
    """T5.3 — pr-resolver expectation reaches the managed adapter."""
    from unittest.mock import patch

    store = _make_store(tmp_path)
    _save_record(store, run_id="fr-pr", status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    with patch(
        "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
        autospec=True,
    ) as mock_adapter_cls:
        adapter = mock_adapter_cls.return_value
        adapter.fetch_result = AsyncMock(
            return_value=AgentRunResult(
                summary="blocked",
                failure_class="user_error",
            )
        )

        result = await activities.agent_runtime_fetch_result(
            {"run_id": "fr-pr", "pr_resolver_expected": True}
        )

        adapter.fetch_result.assert_awaited_once_with(
            "fr-pr", pr_resolver_expected=True
        )
        assert result.failure_class == "user_error"


async def test_fetch_result_no_record_returns_empty_typed_model(tmp_path: Path) -> None:
    """T5.4 — no record in store returns empty AgentRunResult (not None, not dict)."""
    store = _make_store(tmp_path)

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_fetch_result({"run_id": "no-such"})

    assert isinstance(result, AgentRunResult)


async def test_fetch_result_missing_run_id_raises_error(tmp_path: Path) -> None:
    """T5.5 — missing run_id raises TemporalActivityRuntimeError."""
    store = _make_store(tmp_path)
    activities = TemporalAgentRuntimeActivities(run_store=store)

    with pytest.raises(TemporalActivityRuntimeError):
        await activities.agent_runtime_fetch_result({"agent_id": "codex_cli"})


# ---------------------------------------------------------------------------
# Boundary & Serialization tests
# ---------------------------------------------------------------------------

from datetime import timedelta
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

@workflow.defn(name="AgentRuntimeStatusBoundaryTest")
class AgentRuntimeStatusBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> AgentRunStatus:
        return await workflow.execute_activity(
            "agent_runtime.status",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )

@workflow.defn(name="AgentRuntimeFetchResultBoundaryTest")
class AgentRuntimeFetchResultBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> AgentRunResult:
        return await workflow.execute_activity(
            "agent_runtime.fetch_result",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )

@workflow.defn(name="AgentRuntimeCancelBoundaryTest")
class AgentRuntimeCancelBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> AgentRunStatus:
        return await workflow.execute_activity(
            "agent_runtime.cancel",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )

@workflow.defn(name="AgentRuntimePublishArtifactsBoundaryTest")
class AgentRuntimePublishArtifactsBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> AgentRunResult | None:
        return await workflow.execute_activity(
            "agent_runtime.publish_artifacts",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )

async def test_agent_runtime_status_temporal_boundary(tmp_path: Path) -> None:
    """Validate Temporal boundary serialization for typed Pydantic return matches contract."""
    from moonmind.workflows.temporal.activity_catalog import TemporalActivityCatalog

    store = _make_store(tmp_path)
    _save_record(store, run_id="boundary-1", status="completed")

    activities_impl = TemporalAgentRuntimeActivities(run_store=store)
    from temporalio import activity

    @activity.defn(name="agent_runtime.status")
    async def _agent_runtime_status_wrapper(request: dict) -> AgentRunStatus:
        return await activities_impl.agent_runtime_status(request)

    handlers = [_agent_runtime_status_wrapper]

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue",
            workflows=[AgentRuntimeStatusBoundaryTest],
            activities=handlers,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimeStatusBoundaryTest.run,
                {"run_id": "boundary-1", "agent_id": "codex_cli"},
                id="boundary-test-status",
                task_queue="boundary-test-queue",
            )

            assert isinstance(result, AgentRunStatus)
            assert result.status == "completed"


@pytest.mark.asyncio
async def test_agent_runtime_fetch_result_temporal_boundary(tmp_path: Path) -> None:
    from unittest.mock import patch

    store = _make_store(tmp_path)
    _save_record(store, run_id="boundary-1", status="completed")

    activities_impl = TemporalAgentRuntimeActivities(run_store=store)
    from temporalio import activity

    @activity.defn(name="agent_runtime.fetch_result")
    async def _agent_runtime_fetch_wrapper(request: dict) -> AgentRunResult:
        res = await activities_impl.agent_runtime_fetch_result(request)
        if hasattr(res, "model_copy"):
            return res.model_copy()
        return res

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-fetch",
            workflows=[AgentRuntimeFetchResultBoundaryTest],
            activities=[_agent_runtime_fetch_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            with patch("moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter", autospec=True) as MockAdapter:
                instance = MockAdapter.return_value
                instance.fetch_result = AsyncMock(return_value=AgentRunResult(summary="ok", failure_class=None))

                result = await env.client.execute_workflow(
                    AgentRuntimeFetchResultBoundaryTest.run,
                    {
                        "run_id": "boundary-1",
                        "agent_id": "claude",
                        "pr_resolver_expected": True,
                    },
                    id="boundary-test-fetch",
                    task_queue="boundary-test-queue-fetch",
                )

                assert isinstance(result, AgentRunResult)
                assert result.summary == "ok"
                instance.fetch_result.assert_awaited_once_with(
                    "boundary-1", pr_resolver_expected=True
                )


@pytest.mark.asyncio
async def test_agent_runtime_cancel_temporal_boundary() -> None:
    from unittest.mock import MagicMock
    mock_supervisor = AsyncMock()
    mock_supervisor.cancel = AsyncMock()
    activities_impl = TemporalAgentRuntimeActivities(
        run_store=MagicMock(),
        run_supervisor=mock_supervisor,
    )
    from temporalio import activity

    @activity.defn(name="agent_runtime.cancel")
    async def _agent_runtime_cancel_wrapper(request: dict) -> AgentRunStatus:
        return await activities_impl.agent_runtime_cancel(request)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-cancel",
            workflows=[AgentRuntimeCancelBoundaryTest],
            activities=[_agent_runtime_cancel_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimeCancelBoundaryTest.run,
                {"run_id": "c-1", "agent_id": "c"},
                id="boundary-test-cancel",
                task_queue="boundary-test-queue-cancel",
            )

            assert isinstance(result, AgentRunStatus)
            assert result.status == "canceled"


@pytest.mark.asyncio
async def test_agent_runtime_publish_temporal_boundary() -> None:
    from unittest.mock import MagicMock
    activities_impl = TemporalAgentRuntimeActivities(run_store=MagicMock())
    from temporalio import activity

    @activity.defn(name="agent_runtime.publish_artifacts")
    async def _agent_runtime_publish_wrapper(request: dict) -> AgentRunResult | None:
        return await activities_impl.agent_runtime_publish_artifacts(None)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-pub",
            workflows=[AgentRuntimePublishArtifactsBoundaryTest],
            activities=[_agent_runtime_publish_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimePublishArtifactsBoundaryTest.run,
                {},
                id="boundary-test-pub",
                task_queue="boundary-test-queue-pub",
            )

            assert result is None
