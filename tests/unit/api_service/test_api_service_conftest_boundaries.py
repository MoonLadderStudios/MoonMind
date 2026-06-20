"""Regression coverage for MM-848 api_service unit-test setup."""

from __future__ import annotations

import sys

import pytest


def test_api_service_unit_conftest_preloads_api_db_models_and_temporal_guard() -> None:
    assert "api_service.db.models" in sys.modules
    assert "moonmind.workflows.temporal.client" in sys.modules


@pytest.mark.asyncio
async def test_api_service_unit_temporal_guard_mocks_lifecycle_calls() -> None:
    from moonmind.workflows.temporal.client import TemporalClientAdapter

    result = await TemporalClientAdapter().start_workflow(
        workflow_type="workflow", workflow_id="mm:guarded-api-service-unit-test"
    )

    assert result.workflow_id == "mm:guarded-api-service-unit-test"
    assert result.run_id
