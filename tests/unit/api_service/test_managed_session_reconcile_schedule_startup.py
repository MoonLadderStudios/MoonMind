from __future__ import annotations

import asyncio
import logging

import pytest

from api_service import main as api_main


@pytest.mark.asyncio
async def test_omnigent_bootstrap_retries_with_capped_backoff_and_maintains_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delays: list[int] = []
    reconciliations: list[bool] = []
    inventory_refreshes: list[bool] = []

    async def fake_sleep(delay_seconds: int) -> None:
        delays.append(delay_seconds)
        if len(delays) == 4:
            raise asyncio.CancelledError

    async def reconcile_once() -> bool:
        reconciliations.append(True)
        return len(reconciliations) > 1

    async def refresh_inventory() -> bool:
        inventory_refreshes.append(True)
        return True

    monkeypatch.setattr(api_main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(api_main, "_reconcile_omnigent_bootstrap_once", reconcile_once)
    monkeypatch.setattr(
        api_main,
        "_sync_omnigent_bootstrap_agent_profile",
        refresh_inventory,
    )

    with pytest.raises(asyncio.CancelledError):
        await api_main._maintain_omnigent_bootstrap_reconciliation(
            initial_ready=False
        )

    assert delays == [5, 10, 120, 120]
    assert len(reconciliations) == 2
    assert len(inventory_refreshes) == 1


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
async def test_api_startup_enables_workspace_cleanup_schedule_by_default(
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

    assert calls == [True]


@pytest.mark.asyncio
async def test_api_startup_persistently_disables_workspace_cleanup_schedule(
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

    monkeypatch.setenv("MOONMIND_MANAGED_RUNTIME_JANITOR_ENABLED", "false")
    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter",
        _Adapter,
    )

    await api_main.ensure_managed_runtime_workspace_cleanup_schedule_started()

    assert calls == [False]


@pytest.mark.asyncio
async def test_api_startup_dry_run_keeps_workspace_cleanup_schedule_enabled(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
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

    monkeypatch.setenv("MOONMIND_MANAGED_RUNTIME_JANITOR_DRY_RUN", "true")
    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter",
        _Adapter,
    )

    with caplog.at_level(logging.INFO):
        await api_main.ensure_managed_runtime_workspace_cleanup_schedule_started()

    assert calls == [True]
    assert "schedule_enabled=True dry_run=True" in caplog.text


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
