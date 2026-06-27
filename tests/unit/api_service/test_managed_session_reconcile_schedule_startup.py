from __future__ import annotations

import logging

import pytest

from api_service import main as api_main


@pytest.mark.asyncio
async def test_mm870_api_startup_ensures_managed_session_reconcile_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    class _Adapter:
        async def ensure_managed_session_reconcile_schedule(
            self,
            *,
            enabled: bool,
        ) -> str:
            calls.append(enabled)
            return "mm-operational:managed-session-reconcile"

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter",
        _Adapter,
    )

    await api_main.ensure_managed_session_reconcile_schedule_started()

    assert calls == [True]


@pytest.mark.asyncio
async def test_mm870_api_startup_schedule_failure_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _Adapter:
        async def ensure_managed_session_reconcile_schedule(
            self,
            *,
            enabled: bool,
        ) -> str:
            del enabled
            raise RuntimeError("temporal unavailable")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter",
        _Adapter,
    )

    with caplog.at_level(logging.WARNING):
        await api_main.ensure_managed_session_reconcile_schedule_started()

    assert "Failed to ensure managed session reconcile schedule" in caplog.text


@pytest.mark.asyncio
async def test_mm948_api_startup_ensures_workspace_cleanup_schedule_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    class _Adapter:
        async def ensure_managed_runtime_workspace_cleanup_schedule(
            self,
            *,
            enabled: bool,
        ) -> str:
            calls.append(enabled)
            return "mm-operational:managed-runtime-workspace-cleanup"

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter",
        _Adapter,
    )

    await api_main.ensure_managed_runtime_workspace_cleanup_schedule_started()

    assert calls == [False]


@pytest.mark.asyncio
async def test_mm948_api_startup_cleanup_schedule_failure_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _Adapter:
        async def ensure_managed_runtime_workspace_cleanup_schedule(
            self,
            *,
            enabled: bool,
        ) -> str:
            del enabled
            raise RuntimeError("temporal unavailable")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter",
        _Adapter,
    )

    with caplog.at_level(logging.WARNING):
        await api_main.ensure_managed_runtime_workspace_cleanup_schedule_started()

    assert "Failed to ensure managed runtime workspace cleanup schedule" in caplog.text
