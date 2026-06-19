"""Regression coverage for MM-848 Temporal unit-test setup."""

from __future__ import annotations

import sys

import pytest


def test_temporal_unit_conftest_installs_temporal_guard() -> None:
    assert "moonmind.workflows.temporal.client" in sys.modules


@pytest.mark.asyncio
async def test_temporal_unit_guard_mocks_lifecycle_calls() -> None:
    from moonmind.workflows.temporal.client import TemporalClientAdapter

    result = await TemporalClientAdapter().start_workflow(
        "workflow", id="mm:guarded-unit-test"
    )

    assert result.workflow_id == "mm:guarded-unit-test"
    assert result.run_id
