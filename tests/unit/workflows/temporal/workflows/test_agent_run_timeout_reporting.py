"""Regression tests for managed agent-run timeout reporting and the
workflow-side turn deadline.

Context: workflow mm:4b897068 failed because a codex ``send_turn`` activity
ran ~2h45m and timed out, and the AgentRun reduced that to a bare
``failure_class="execution_error"`` with an empty summary. These tests lock in:

- Fix A: timeout results carry an actionable, non-token summary
  (``_timed_out_result``).
- Fix B: the codex blocking turn is bounded by a deterministic workflow-side
  deadline that fails fast with a descriptive error
  (``_send_turn_within_budget``), without touching the managed agent runtime.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from temporalio.exceptions import ApplicationError

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.workflows.agent_run import (
    _MIN_MANAGED_TURN_DEADLINE_SECONDS,
    MoonMindAgentRun,
    workflow as agent_run_workflow,
)

pytestmark = [pytest.mark.asyncio]

_CATEGORY_TOKENS = {
    "user_error",
    "integration_error",
    "execution_error",
    "system_error",
}


def _request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        correlationId="corr-1",
        idempotencyKey="idem-1",
    )


# --- Fix A: timeout results carry an actionable, non-token summary ---


async def test_timed_out_result_emits_actionable_summary_and_metadata() -> None:
    workflow = MoonMindAgentRun()
    result = workflow._timed_out_result(
        request=_request(),
        timeout_seconds=3600,
        elapsed_seconds=9932,
        detail="made no observable progress and exceeded its execution budget",
    )

    assert result.failure_class == "execution_error"
    # The operator summary must never be empty nor a bare category token.
    assert result.summary
    assert result.summary not in _CATEGORY_TOKENS
    assert "execution budget 3600s" in result.summary
    assert "9932s" in result.summary
    assert "intervention" in result.summary.lower()
    assert result.metadata["reason"] == "timed_out"
    assert result.metadata["timeoutSeconds"] == 3600
    assert result.metadata["elapsedSeconds"] == 9932
    assert result.metadata["agentId"] == "codex_cli"


async def test_timed_out_result_without_elapsed_omits_elapsed_metadata() -> None:
    workflow = MoonMindAgentRun()
    result = workflow._timed_out_result(
        request=_request(),
        timeout_seconds=3600,
        detail="exceeded its execution budget before dispatching a turn",
    )

    assert result.summary
    assert result.summary not in _CATEGORY_TOKENS
    assert "elapsedSeconds" not in result.metadata


# --- Fix B: workflow-side deadline on the codex blocking turn ---


def _patch_workflow_primitives(
    monkeypatch: pytest.MonkeyPatch,
    *,
    patched: bool,
    now: datetime,
    wait_condition,
) -> None:
    monkeypatch.setattr(
        agent_run_workflow, "patched", lambda _id: patched, raising=True
    )
    monkeypatch.setattr(agent_run_workflow, "now", lambda: now, raising=True)
    monkeypatch.setattr(
        agent_run_workflow, "wait_condition", wait_condition, raising=True
    )


async def test_send_turn_within_budget_passthrough_when_patch_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindAgentRun()
    sentinel = object()

    async def _fake_activity(*_args, **_kwargs):
        return sentinel

    # Patch disabled -> no race, just await the activity directly.
    monkeypatch.setattr(
        agent_run_workflow, "patched", lambda _id: False, raising=True
    )
    monkeypatch.setattr(
        workflow, "_execute_routed_activity", _fake_activity, raising=True
    )

    result = await workflow._send_turn_within_budget(
        {"instructions": "hello"},
        timeout_seconds=3600,
        overall_start=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )
    assert result is sentinel


async def test_send_turn_within_budget_returns_completed_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindAgentRun()
    sentinel = {"status": "completed"}

    async def _fake_activity(*_args, **_kwargs):
        return sentinel

    async def _wait_condition(_predicate, *, timeout):
        # The turn finishes within budget: let the scheduled coroutine complete.
        await asyncio.sleep(0)
        return True

    start = datetime(2026, 6, 15, tzinfo=timezone.utc)
    _patch_workflow_primitives(
        monkeypatch, patched=True, now=start, wait_condition=_wait_condition
    )
    monkeypatch.setattr(
        workflow, "_execute_routed_activity", _fake_activity, raising=True
    )

    result = await workflow._send_turn_within_budget(
        {"instructions": "hello"},
        timeout_seconds=3600,
        overall_start=start,
    )
    assert result == sentinel


async def test_send_turn_within_budget_aborts_on_deadline_with_descriptive_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindAgentRun()
    cancelled = asyncio.Event()

    async def _never_completes(*_args, **_kwargs):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def _wait_condition(_predicate, *, timeout):
        # Let the scheduled turn task start (reach its await) before the
        # deterministic workflow timer "fires".
        await asyncio.sleep(0)
        raise asyncio.TimeoutError()

    start = datetime(2026, 6, 15, tzinfo=timezone.utc)
    _patch_workflow_primitives(
        monkeypatch, patched=True, now=start, wait_condition=_wait_condition
    )
    monkeypatch.setattr(
        workflow, "_execute_routed_activity", _never_completes, raising=True
    )

    with pytest.raises(ApplicationError) as excinfo:
        await workflow._send_turn_within_budget(
            {"instructions": "hello"},
            timeout_seconds=3600,
            overall_start=start,
        )

    err = excinfo.value
    assert err.type == "ManagedSessionTurnDeadlineExceeded"
    assert err.non_retryable is True
    assert "budget" in str(err)
    assert "without completing" in str(err)
    # The in-flight turn activity task must be cancelled.
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    assert cancelled.is_set()


async def test_send_turn_within_budget_floors_deadline_for_exhausted_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindAgentRun()
    captured_timeout: list[timedelta] = []

    async def _fake_activity(*_args, **_kwargs):
        return {"status": "completed"}

    async def _wait_condition(_predicate, *, timeout):
        captured_timeout.append(timeout)
        await asyncio.sleep(0)
        return True

    start = datetime(2026, 6, 15, tzinfo=timezone.utc)
    # "now" is far past the budget so remaining is negative -> floor applies.
    now = start + timedelta(seconds=10_000)
    _patch_workflow_primitives(
        monkeypatch, patched=True, now=now, wait_condition=_wait_condition
    )
    monkeypatch.setattr(
        workflow, "_execute_routed_activity", _fake_activity, raising=True
    )

    await workflow._send_turn_within_budget(
        {"instructions": "hello"},
        timeout_seconds=3600,
        overall_start=start,
    )
    assert captured_timeout == [
        timedelta(seconds=_MIN_MANAGED_TURN_DEADLINE_SECONDS)
    ]
